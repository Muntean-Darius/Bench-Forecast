"""Training assignment lifecycle + skill grants."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database.models import (
    Employee,
    ProjectDemand,
    TrainingAssignment,
    TrainingStatusEnum,
)
from src.services.skills_service import apply_training_skills, normalize_skills

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class TrainingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        employee_id: uuid.UUID,
        title: str,
        targeted_skills: list[str],
        ai_upskilling_path: Optional[str] = None,
        project_id: Optional[uuid.UUID] = None,
        demand_id: Optional[uuid.UUID] = None,
        target_completion_date=None,
        notes: Optional[str] = None,
        assigned_by: Optional[uuid.UUID] = None,
        status: TrainingStatusEnum = TrainingStatusEnum.assigned,
    ) -> TrainingAssignment:
        employee = await self.session.get(Employee, employee_id)
        if employee is None:
            raise ValueError(f"Employee {employee_id} not found")

        if project_id is None and demand_id is not None:
            demand = await self.session.get(ProjectDemand, demand_id)
            if demand is not None:
                project_id = demand.project_id

        training = TrainingAssignment(
            employee_id=employee_id,
            project_id=project_id,
            demand_id=demand_id,
            title=title,
            targeted_skills=normalize_skills(targeted_skills),
            ai_upskilling_path=ai_upskilling_path,
            status=status,
            assigned_by=assigned_by,
            assigned_at=_utcnow(),
            target_completion_date=target_completion_date,
            notes=notes,
        )
        self.session.add(training)
        await self.session.flush()
        await self.session.refresh(training)
        logger.info(
            "Training assigned: employee=%s title=%s skills=%s",
            employee_id,
            title,
            training.targeted_skills,
        )
        return training

    async def list(
        self,
        *,
        employee_id: Optional[uuid.UUID] = None,
        manager_id: Optional[uuid.UUID] = None,
        status: Optional[TrainingStatusEnum] = None,
    ) -> list[TrainingAssignment]:
        stmt = (
            select(TrainingAssignment)
            .options(
                joinedload(TrainingAssignment.employee),
                joinedload(TrainingAssignment.project),
                joinedload(TrainingAssignment.demand),
            )
            .order_by(TrainingAssignment.assigned_at.desc())
        )
        if employee_id is not None:
            stmt = stmt.where(TrainingAssignment.employee_id == employee_id)
        if status is not None:
            stmt = stmt.where(TrainingAssignment.status == status)
        if manager_id is not None:
            stmt = stmt.join(
                Employee, Employee.id == TrainingAssignment.employee_id
            ).where(Employee.manager_id == manager_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        training: TrainingAssignment,
        *,
        title: Optional[str] = None,
        targeted_skills: Optional[list[str]] = None,
        ai_upskilling_path: Optional[str] = None,
        status: Optional[TrainingStatusEnum] = None,
        target_completion_date=None,
        notes: Optional[str] = None,
    ) -> TrainingAssignment:
        if title is not None:
            training.title = title
        if targeted_skills is not None:
            training.targeted_skills = normalize_skills(targeted_skills)
        if ai_upskilling_path is not None:
            training.ai_upskilling_path = ai_upskilling_path
        if target_completion_date is not None:
            training.target_completion_date = target_completion_date
        if notes is not None:
            training.notes = notes
        if status is not None:
            training.status = status
            if status == TrainingStatusEnum.completed and training.completed_at is None:
                training.completed_at = _utcnow()
                await apply_training_skills(
                    self.session, training.employee_id, training
                )
        await self.session.flush()
        await self.session.refresh(training)
        return training

    async def complete(self, training: TrainingAssignment) -> TrainingAssignment:
        training.status = TrainingStatusEnum.completed
        training.completed_at = _utcnow()
        await apply_training_skills(self.session, training.employee_id, training)
        await self.session.flush()
        await self.session.refresh(training)
        return training

    async def get(self, training_id: uuid.UUID) -> Optional[TrainingAssignment]:
        result = await self.session.execute(
            select(TrainingAssignment)
            .options(
                joinedload(TrainingAssignment.employee),
                joinedload(TrainingAssignment.project),
                joinedload(TrainingAssignment.demand),
            )
            .where(TrainingAssignment.id == training_id)
        )
        return result.scalars().first()
