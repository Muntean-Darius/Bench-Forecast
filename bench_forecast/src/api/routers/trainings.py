"""Training / upskilling assignment APIs."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    assert_can_manage_employee,
    get_current_user,
    is_admin,
    manager_employee_id,
    require_roles,
)
from src.database.database import get_db
from src.database.models import TrainingStatusEnum
from src.schemas.training import TrainingCreate, TrainingUpdate
from src.services.notifications import NotificationService, schedule_notification
from src.services.training_service import TrainingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/trainings", tags=["Trainings"])


def _serialize(training) -> dict:
    return {
        "id": training.id,
        "employee_id": training.employee_id,
        "employee_name": (
            f"{training.employee.first_name} {training.employee.last_name}".strip()
            if training.employee
            else None
        ),
        "project_id": training.project_id,
        "project_name": training.project.name if training.project else None,
        "demand_id": training.demand_id,
        "title": training.title,
        "targeted_skills": training.targeted_skills or [],
        "ai_upskilling_path": training.ai_upskilling_path,
        "status": (
            training.status.value
            if hasattr(training.status, "value")
            else str(training.status)
        ),
        "assigned_at": training.assigned_at,
        "target_completion_date": training.target_completion_date,
        "completed_at": training.completed_at,
        "notes": training.notes,
    }


@router.get("", status_code=status.HTTP_200_OK)
async def list_trainings(
    employee_id: Optional[uuid.UUID] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = TrainingService(db)
    status_enum = None
    if status_filter:
        try:
            status_enum = TrainingStatusEnum(status_filter)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid training status")

    if is_admin(user):
        rows = await service.list(
            employee_id=employee_id, status=status_enum
        )
    elif user.get("role") == "manager":
        manager_id = manager_employee_id(user)
        if not manager_id:
            raise HTTPException(status_code=403, detail="Manager is not linked to an employee")
        if employee_id is not None:
            await assert_can_manage_employee(db, user, employee_id)
            rows = await service.list(
                employee_id=employee_id, status=status_enum
            )
        else:
            rows = await service.list(
                manager_id=uuid.UUID(manager_id), status=status_enum
            )
    else:
        own = manager_employee_id(user)
        if not own:
            raise HTTPException(status_code=403, detail="Employee account has no record")
        if employee_id is not None and str(employee_id) != own:
            raise HTTPException(status_code=403, detail="You can only view your own trainings")
        rows = await service.list(
            employee_id=uuid.UUID(own), status=status_enum
        )
    return [_serialize(row) for row in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_training(
    payload: TrainingCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    await assert_can_manage_employee(db, user, payload.employee_id)
    service = TrainingService(db)
    training = await service.create(
        employee_id=payload.employee_id,
        title=payload.title,
        targeted_skills=payload.targeted_skills,
        ai_upskilling_path=payload.ai_upskilling_path,
        project_id=payload.project_id,
        demand_id=payload.demand_id,
        target_completion_date=payload.target_completion_date,
        notes=payload.notes,
        assigned_by=uuid.UUID(user["id"]),
    )
    notification = None
    try:
        notification = await NotificationService(db).notify_training_assigned(
            training
        )
    except Exception:
        logger.exception("Could not queue training_assigned notification")
    await db.commit()
    if notification is not None and notification.status == "queued":
        schedule_notification(notification.id, background_tasks)
    training = await service.get(training.id)
    return _serialize(training)


@router.patch("/{training_id}", status_code=status.HTTP_200_OK)
async def update_training(
    training_id: uuid.UUID,
    payload: TrainingUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    service = TrainingService(db)
    training = await service.get(training_id)
    if training is None:
        raise HTTPException(status_code=404, detail="Training not found")
    await assert_can_manage_employee(db, user, training.employee_id)
    try:
        updated = await service.update(
            training,
            title=payload.title,
            targeted_skills=payload.targeted_skills,
            ai_upskilling_path=payload.ai_upskilling_path,
            status=(
                TrainingStatusEnum(payload.status) if payload.status else None
            ),
            target_completion_date=payload.target_completion_date,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    updated = await service.get(training_id)
    return _serialize(updated)


@router.post("/{training_id}/complete", status_code=status.HTTP_200_OK)
async def complete_training(
    training_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    service = TrainingService(db)
    training = await service.get(training_id)
    if training is None:
        raise HTTPException(status_code=404, detail="Training not found")
    await assert_can_manage_employee(db, user, training.employee_id)
    updated = await service.complete(training)
    await db.commit()
    updated = await service.get(training_id)
    return _serialize(updated)
