"""Cumulative, non-destructive skill tracking.

Rules:
  * Skills are a set union. Merging never deletes an existing skill.
  * Project-earned skills are ``source='project'`` and ``is_locked=True``.
  * A CV re-upload can add skills and update CV-origin evidence, but it can
    never remove or downgrade a locked/project skill.
  * Every change is recorded in ``employee_skill_evidence``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    Employee,
    EmployeeSkill,
    EmployeeSkillEvidence,
    ProficiencyLevelEnum,
    ProjectDemand,
    Skill,
    SkillOriginEnum,
    TrainingAssignment,
)

logger = logging.getLogger(__name__)

_SOURCE_PRIORITY = {
    SkillOriginEnum.manual: 1,
    SkillOriginEnum.cv: 2,
    SkillOriginEnum.training: 3,
    SkillOriginEnum.project: 4,
}


def _as_origin(value) -> SkillOriginEnum:
    if isinstance(value, SkillOriginEnum):
        return value
    name = str(value or "cv").lower()
    try:
        return SkillOriginEnum(name)
    except ValueError:
        return SkillOriginEnum.cv


def normalize_skills(skills: Iterable) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in skills or []:
        if raw is None:
            continue
        name = str(raw).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def skills_from_demand(demand: ProjectDemand) -> list[str]:
    raw = demand.required_skills or []
    if isinstance(raw, dict):
        raw = raw.get("skills", list(raw.keys()))
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return normalize_skills(raw)


async def _skill_cache(session: AsyncSession) -> dict[str, Skill]:
    rows = (await session.execute(select(Skill))).scalars().all()
    return {skill.name.lower(): skill for skill in rows}


async def _get_or_create_skill(
    session: AsyncSession, cache: dict[str, Skill], name: str
) -> Skill:
    key = name.lower()
    skill = cache.get(key)
    if skill is not None:
        return skill
    skill = Skill(name=name, is_verified=False)
    session.add(skill)
    await session.flush()
    cache[key] = skill
    return skill


async def get_employee_skill_links(
    session: AsyncSession, employee_id: uuid.UUID
) -> list[EmployeeSkill]:
    result = await session.execute(
        select(EmployeeSkill)
        .options(selectinload(EmployeeSkill.skill))
        .where(EmployeeSkill.employee_id == employee_id)
    )
    return list(result.scalars().all())


async def get_skill_names(
    session: AsyncSession, employee_id: uuid.UUID
) -> list[str]:
    links = await get_employee_skill_links(session, employee_id)
    return sorted({link.skill.name for link in links if link.skill}, key=str.lower)


async def sync_employee_skill_text(
    session: AsyncSession, employee_id: uuid.UUID
) -> None:
    """Keep the legacy comma-separated column in sync with the union."""
    employee = await session.get(Employee, employee_id)
    if employee is None:
        return
    names = await get_skill_names(session, employee_id)
    employee.skills = ", ".join(names) if names else None


async def merge_skills(
    session: AsyncSession,
    employee_id: uuid.UUID,
    skills: Iterable,
    *,
    source: SkillOriginEnum | str,
    source_ref: Optional[uuid.UUID] = None,
    note: Optional[str] = None,
    proficiency: ProficiencyLevelEnum = ProficiencyLevelEnum.intermediate,
) -> dict:
    """Union *skills* into an employee's skill set without deleting anything."""
    origin = _as_origin(source)
    names = normalize_skills(skills)
    if not names:
        await sync_employee_skill_text(session, employee_id)
        return {"added": [], "upgraded": [], "total": 0, "skills": []}

    cache = await _skill_cache(session)
    links = {
        link.skill_id: link
        for link in await get_employee_skill_links(session, employee_id)
    }
    evidence_rows = (
        await session.execute(
            select(EmployeeSkillEvidence).where(
                EmployeeSkillEvidence.employee_id == employee_id
            )
        )
    ).scalars().all()
    evidence_seen = {
        (e.skill_id, _as_origin(e.source), e.source_ref) for e in evidence_rows
    }

    added: list[str] = []
    upgraded: list[str] = []

    for name in names:
        skill = await _get_or_create_skill(session, cache, name)
        link = links.get(skill.id)
        should_add_evidence = False
        existing_priority = (
            _SOURCE_PRIORITY.get(_as_origin(link.source), 0) if link else 0
        )

        if link is None:
            link = EmployeeSkill(
                employee_id=employee_id,
                skill_id=skill.id,
                proficiency_level=proficiency,
                source=origin,
                source_ref=source_ref,
                is_locked=(origin == SkillOriginEnum.project),
                acquired_at=datetime.now(tz=timezone.utc),
            )
            session.add(link)
            links[skill.id] = link
            added.append(skill.name)
            should_add_evidence = True
        else:
            # Never downgrade or unlock a project-earned skill.
            if (
                origin == SkillOriginEnum.project
                or _SOURCE_PRIORITY.get(origin, 0) > existing_priority
            ):
                if _as_origin(link.source) != origin or link.source_ref != source_ref:
                    link.source = origin
                    link.source_ref = source_ref
                    if origin == SkillOriginEnum.project:
                        link.is_locked = True
                    upgraded.append(skill.name)
                    should_add_evidence = True
            elif origin == SkillOriginEnum.project:
                link.is_locked = True

        if should_add_evidence:
            key = (skill.id, origin, source_ref)
            if key not in evidence_seen:
                session.add(
                    EmployeeSkillEvidence(
                        employee_id=employee_id,
                        skill_id=skill.id,
                        source=origin,
                        source_ref=source_ref,
                        note=note,
                    )
                )
                evidence_seen.add(key)

    await session.flush()
    await sync_employee_skill_text(session, employee_id)
    all_names = await get_skill_names(session, employee_id)
    logger.info(
        "merge_skills: employee=%s source=%s added=%d upgraded=%d total=%d",
        employee_id,
        origin.value,
        len(added),
        len(upgraded),
        len(all_names),
    )
    return {
        "added": added,
        "upgraded": upgraded,
        "total": len(all_names),
        "skills": all_names,
    }


async def apply_project_skills(
    session: AsyncSession,
    employee_id: uuid.UUID,
    demand: ProjectDemand,
) -> dict:
    return await merge_skills(
        session,
        employee_id,
        skills_from_demand(demand),
        source=SkillOriginEnum.project,
        source_ref=demand.id,
        note=f"Earned on {demand.role}.",
    )


async def apply_training_skills(
    session: AsyncSession,
    employee_id: uuid.UUID,
    training: TrainingAssignment,
) -> dict:
    return await merge_skills(
        session,
        employee_id,
        training.targeted_skills or [],
        source=SkillOriginEnum.training,
        source_ref=training.id,
        note=f"Completed {training.title}.",
    )


async def skills_by_source(
    session: AsyncSession, employee_id: uuid.UUID
) -> dict:
    links = await get_employee_skill_links(session, employee_id)
    grouped: dict[str, list[dict]] = {
        "cv": [],
        "project": [],
        "training": [],
        "manual": [],
    }
    for link in links:
        if not link.skill:
            continue
        origin = _as_origin(link.source).value
        grouped.setdefault(origin, []).append(
            {
                "skill_id": str(link.skill_id),
                "name": link.skill.name,
                "category": link.skill.category,
                "proficiency_level": (
                    link.proficiency_level.value
                    if hasattr(link.proficiency_level, "value")
                    else str(link.proficiency_level)
                ),
                "source": origin,
                "is_locked": bool(link.is_locked),
                "acquired_at": (
                    link.acquired_at.isoformat() if link.acquired_at else None
                ),
            }
        )
    return {"employee_id": str(employee_id), **grouped}
