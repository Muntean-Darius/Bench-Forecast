"""Idempotent data backfill for the usability/capability expansion.

Run **after** ``alembic upgrade head`` and before the API is started:

    cd bench_forecast
    ./venv/bin/python -m scripts.backfill_usability

It is deliberately non-destructive:

* normalises legacy ``employees.skills`` into ``skills`` + ``employee_skills``
* fills daily capacity / hours-per-day / allocation hours from existing data
* assigns a deterministic department manager hierarchy when ``manager_id``
  is still empty
* backfills project clients and lifecycle dates

It never deletes rows and never overwrites project-earned / locked skills.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parents[0]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from demo_catalog import PROJECT_BLUEPRINTS  # noqa: E402
from src.database.database import SyncSessionLocal  # noqa: E402
from src.database.models import (  # noqa: E402
    Allocation,
    AllocationStatusEnum,
    Employee,
    EmployeeSkill,
    EmployeeSkillEvidence,
    Project,
    ProjectDemand,
    Skill,
    SkillOriginEnum,
)

RANK = {
    "principal": 5,
    "lead": 4,
    "senior": 3,
    "mid": 2,
    "junior": 1,
}
CAPACITY = Decimal("8.00")
PART_TIME_CAPACITY = Decimal("4.00")


def _hours(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def backfill_capacities(session) -> int:
    changed = 0
    for emp in session.execute(select(Employee)).scalars().all():
        employment = (
            emp.employment_type.value
            if hasattr(emp.employment_type, "value")
            else str(emp.employment_type or "full_time")
        )
        target = PART_TIME_CAPACITY if employment == "part_time" else CAPACITY
        current = _hours(emp.daily_capacity_hours)
        if current <= 0 or current > CAPACITY:
            emp.daily_capacity_hours = target
            changed += 1
    session.flush()
    return changed


def backfill_skills(session) -> tuple[int, int]:
    """Convert comma-separated legacy skills into normalised rows."""
    existing = {
        name.lower(): skill
        for skill in session.execute(select(Skill)).scalars().all()
        for name in [skill.name]
    }
    pairs = {
        (row.employee_id, row.skill_id)
        for row in session.execute(select(EmployeeSkill)).scalars().all()
    }

    created_skills = 0
    created_links = 0

    for emp in session.execute(select(Employee)).scalars().all():
        raw = emp.skills or ""
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            key = token.lower()
            skill = existing.get(key)
            if skill is None:
                skill = Skill(name=token, is_verified=False)
                session.add(skill)
                session.flush()
                existing[key] = skill
                created_skills += 1
            if (emp.id, skill.id) in pairs:
                continue
            session.add(
                EmployeeSkill(
                    employee_id=emp.id,
                    skill_id=skill.id,
                    source=SkillOriginEnum.cv,
                    is_locked=False,
                )
            )
            pairs.add((emp.id, skill.id))
            created_links += 1

    session.flush()
    return created_skills, created_links


def backfill_project_skills(session) -> int:
    """Attach demand skills earned by active/completed allocations.

    Project-earned skills are locked so a later CV re-upload can never prune
    them. This is the cumulative-skill guarantee for pre-existing data.
    """
    cache = {
        skill.name.lower(): skill
        for skill in session.execute(select(Skill)).scalars().all()
    }
    links = {
        (link.employee_id, link.skill_id): link
        for link in session.execute(select(EmployeeSkill)).scalars().all()
    }
    evidence_seen = {
        (e.employee_id, e.skill_id, e.source, e.source_ref)
        for e in session.execute(select(EmployeeSkillEvidence)).scalars().all()
    }

    created = 0
    for allocation in session.execute(select(Allocation)).scalars().all():
        if allocation.status not in {
            AllocationStatusEnum.active,
            AllocationStatusEnum.approved_by_manager,
            AllocationStatusEnum.completed,
        }:
            continue
        demand = session.get(ProjectDemand, allocation.demand_id)
        if not demand:
            continue

        raw_skills = demand.required_skills or []
        if isinstance(raw_skills, dict):
            raw_skills = raw_skills.get("skills", list(raw_skills.keys()))
        if not isinstance(raw_skills, list):
            raw_skills = []

        for raw in raw_skills:
            name = str(raw).strip()
            if not name:
                continue
            key = name.lower()
            skill = cache.get(key)
            if skill is None:
                skill = Skill(name=name, is_verified=False)
                session.add(skill)
                session.flush()
                cache[key] = skill

            link = links.get((allocation.employee_id, skill.id))
            if link is None:
                link = EmployeeSkill(
                    employee_id=allocation.employee_id,
                    skill_id=skill.id,
                    source=SkillOriginEnum.project,
                    source_ref=demand.id,
                    is_locked=True,
                )
                session.add(link)
                links[(allocation.employee_id, skill.id)] = link
                created += 1
            else:
                link.source = SkillOriginEnum.project
                link.source_ref = demand.id
                link.is_locked = True

            ev_key = (
                allocation.employee_id,
                skill.id,
                SkillOriginEnum.project,
                demand.id,
            )
            if ev_key not in evidence_seen:
                session.add(EmployeeSkillEvidence(
                    employee_id=allocation.employee_id,
                    skill_id=skill.id,
                    source=SkillOriginEnum.project,
                    source_ref=demand.id,
                    note=f"Earned on {demand.role}.",
                ))
                evidence_seen.add(ev_key)

    session.flush()
    return created


def backfill_allocations(session) -> int:
    employees = {
        emp.id: emp
        for emp in session.execute(select(Employee)).scalars().all()
    }
    changed = 0
    for allocation in session.execute(select(Allocation)).scalars().all():
        emp = employees.get(allocation.employee_id)
        capacity = _hours(emp.daily_capacity_hours) if emp else CAPACITY
        if capacity <= 0:
            capacity = CAPACITY

        hours = _hours(allocation.allocated_hours_per_day)
        if hours <= 0:
            pct = _hours(allocation.allocation_pct)
            hours = min(capacity, max(Decimal("0.5"), pct / 100 * capacity))
            allocation.allocated_hours_per_day = hours
            changed += 1

        expected_pct = (hours / capacity * Decimal("100")).quantize(
            Decimal("0.01")
        )
        if _hours(allocation.allocation_pct) != expected_pct:
            allocation.allocation_pct = expected_pct
            changed += 1
    session.flush()
    return changed


def backfill_demands(session) -> int:
    changed = 0
    for demand in session.execute(select(ProjectDemand)).scalars().all():
        if _hours(demand.hours_per_day) <= 0:
            demand.hours_per_day = CAPACITY
            changed += 1

        allocated = session.execute(
            select(func.count(Allocation.id)).where(
                Allocation.demand_id == demand.id
            )
        ).scalar() or 0
        # Never shrink a value that is already known. This makes the backfill
        # safe to re-run after the richer seeder has populated the real total.
        required = max(
            int(demand.headcount_required or 0),
            int(demand.headcount_needed or 0),
            int(allocated),
            1,
        )
        if int(demand.headcount_required or 0) != required:
            demand.headcount_required = required
            changed += 1
    session.flush()
    return changed


def backfill_projects(session) -> int:
    clients = {bp["name"]: bp.get("client") for bp in PROJECT_BLUEPRINTS}
    changed = 0

    for project in session.execute(select(Project)).scalars().all():
        if not project.client_name and project.name in clients:
            project.client_name = clients[project.name]
            changed += 1

        if project.start_date is None or project.end_date is None:
            bounds = session.execute(
                select(
                    func.min(Allocation.start_date),
                    func.max(Allocation.end_date),
                )
                .join(ProjectDemand, ProjectDemand.id == Allocation.demand_id)
                .where(ProjectDemand.project_id == project.id)
            ).one()
            if bounds[0] and project.start_date is None:
                project.start_date = bounds[0]
                changed += 1
            if bounds[1] and project.end_date is None:
                project.end_date = bounds[1]
                changed += 1
    session.flush()
    return changed


def _rank(emp: Employee) -> int:
    value = (
        emp.experience_level.value
        if hasattr(emp.experience_level, "value")
        else str(emp.experience_level or "mid")
    )
    return RANK.get(value.lower(), 1)


def backfill_managers(session) -> int:
    """Assign a deterministic department → head-of-department hierarchy.

    Only fills NULL manager_id values, so it can run repeatedly without
    overwriting a manually curated org chart.
    """
    employees = [
        emp
        for emp in session.execute(select(Employee)).scalars().all()
        if emp.is_active
    ]
    if not employees:
        return 0

    by_department: dict = {}
    for emp in employees:
        by_department.setdefault(emp.department_id, []).append(emp)

    department_leads: list[Employee] = []
    for members in by_department.values():
        lead = max(
            members,
            key=lambda e: (_rank(e), -(e.hire_date.toordinal() if e.hire_date else 0)),
        )
        department_leads.append(lead)

    if not department_leads:
        return 0

    global_head = max(
        department_leads,
        key=lambda e: (_rank(e), -(e.hire_date.toordinal() if e.hire_date else 0)),
    )

    changed = 0
    for lead in department_leads:
        if lead.id != global_head.id and lead.manager_id is None:
            lead.manager_id = global_head.id
            changed += 1

    for emp in employees:
        if emp.id == global_head.id or emp.manager_id is not None:
            continue
        members = by_department.get(emp.department_id) or []
        lead = max(
            members,
            key=lambda e: (_rank(e), -(e.hire_date.toordinal() if e.hire_date else 0)),
        )
        if lead.id == emp.id:
            lead = global_head
        if lead.id != emp.id:
            emp.manager_id = lead.id
            changed += 1

    session.flush()
    return changed


def _print_summary(session) -> None:
    counts = {
        "employees": session.execute(
            select(func.count(Employee.id))
        ).scalar(),
        "employees_with_manager": session.execute(
            select(func.count(Employee.id)).where(Employee.manager_id.isnot(None))
        ).scalar(),
        "skills": session.execute(select(func.count(Skill.id))).scalar(),
        "employee_skills": session.execute(
            select(func.count()).select_from(EmployeeSkill)
        ).scalar(),
        "project_skills": session.execute(
            select(func.count())
            .select_from(EmployeeSkill)
            .where(EmployeeSkill.source == SkillOriginEnum.project)
        ).scalar(),
        "allocations": session.execute(
            select(func.count(Allocation.id))
        ).scalar(),
        "allocations_over_8h": session.execute(
            select(func.count(Allocation.id)).where(
                Allocation.allocated_hours_per_day > 8
            )
        ).scalar(),
    }
    print("Backfill summary")
    for key, value in counts.items():
        print(f"  {key:<24} {value}")


def main() -> None:
    print("Running non-destructive usability backfill…")
    with SyncSessionLocal() as session:
        capacities = backfill_capacities(session)
        skills, links = backfill_skills(session)
        project_links = backfill_project_skills(session)
        allocations = backfill_allocations(session)
        demands = backfill_demands(session)
        projects = backfill_projects(session)
        managers = backfill_managers(session)
        session.commit()

        # Sanity guards: these must never fail after a successful backfill.
        over_cap = session.execute(
            text(
                "SELECT count(*) FROM allocations "
                "WHERE allocated_hours_per_day > 8"
            )
        ).scalar()
        if over_cap:
            raise SystemExit(
                f"Backfill produced {over_cap} allocations above the 8h cap"
            )

        _print_summary(session)

    print(
        "Backfill complete: "
        f"capacities={capacities}, skills_created={skills}, "
        f"skill_links={links}, project_skill_links={project_links}, "
        f"allocations={allocations}, demands={demands}, "
        f"projects={projects}, managers={managers}"
    )


if __name__ == "__main__":
    main()
