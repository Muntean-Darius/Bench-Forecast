"""Project list/detail, role roster, demands and candidate recommendations."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.deps import (
    assert_can_manage_employee,
    get_current_user,
    require_roles,
)
from src.database.database import get_db
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    DemandStatusEnum,
    Employee,
    ExperienceLevelEnum,
    Project,
    ProjectDemand,
)
from src.schemas.workforce import DemandCreate, DemandUpdate
from src.services.notifications import NotificationService, schedule_notification
from src.services.skills_service import normalize_skills, skills_from_demand
from src.services.workforce_service import (
    AllocationService,
    CapacityExceededError,
    effective_capacity,
    summarize_allocations,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])

_CURRENT_STATUSES = {
    AllocationStatusEnum.active,
    AllocationStatusEnum.approved_by_manager,
    AllocationStatusEnum.pending_manager_approval,
    AllocationStatusEnum.proposed_by_ai,
}
_ROSTER_STATUSES = _CURRENT_STATUSES | {AllocationStatusEnum.completed}


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _demand_status(demand: ProjectDemand) -> str:
    if demand.status == DemandStatusEnum.requires_training:
        return "requires_training"
    required = max(int(demand.headcount_required or 1), 1)
    need = max(int(demand.headcount_needed or 0), 0)
    if need <= 0:
        return "filled"
    if need < required:
        return "partially_filled"
    return "open"


def _demand_headcounts(demand: ProjectDemand) -> tuple[int, int]:
    required = max(int(demand.headcount_required or 1), 1)
    need = max(int(demand.headcount_needed or 0), 0)
    return required, max(required - need, 0)


def _employee_name(emp: Employee) -> str:
    return f"{emp.first_name} {emp.last_name}".strip()


def _as_date(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date: {value}")


def _serialize_project(project: Project) -> dict:
    demands = project.demands or []
    remainder = 0
    for demand in demands:
        remainder += max(int(demand.headcount_needed or 0), 0)
    total_roles = sum(max(int(d.headcount_required or 1), 1) for d in demands)
    open_roles = sum(
        1 for demand in demands if _demand_status(demand) == "open"
    )
    partially = sum(
        1 for demand in demands if _demand_status(demand) == "partially_filled"
    )
    filled = sum(
        1 for demand in demands if _demand_status(demand) == "filled"
    )
    return {
        "id": project.id,
        "name": project.name,
        "client_name": project.client_name,
        "status": project.status,
        "probability": project.probability,
        "total_budget": float(project.total_budget or 0),
        "target_margin": float(project.target_margin or 0),
        "start_date": project.start_date,
        "end_date": project.end_date,
        "open_roles": open_roles,
        "filled_roles": filled,
        "partially_filled_roles": partially,
        "total_roles": total_roles,
        "open_seats": remainder,
        # Backwards-compatible field used by the existing action queue UI.
        "open_demands_count": sum(
            1
            for demand in demands
            if _demand_status(demand)
            in ("open", "partially_filled", "requires_training")
        ),
    }


def _serialize_allocation(
    allocation: Allocation,
    employee: Employee,
    all_allocations: list[Allocation],
) -> dict:
    summary = summarize_allocations(employee, all_allocations)
    return {
        "allocation_id": allocation.id,
        "employee_id": allocation.employee_id,
        "employee_name": _employee_name(employee),
        "job_title": employee.job_title,
        "allocated_hours_per_day": float(allocation.allocated_hours_per_day or 0),
        "allocation_pct": float(allocation.allocation_pct or 0),
        "remaining_available_hours": float(
            summary["remaining_available_hours"]
        ),
        "status": _status_value(allocation.status),
        "ai_match_score": (
            float(allocation.ai_match_score)
            if allocation.ai_match_score is not None
            else None
        ),
        "ai_justification": allocation.ai_justification,
        "start_date": allocation.start_date,
        "end_date": allocation.end_date,
    }


def _serialize_demand(demand: ProjectDemand, allocations_by_employee: dict) -> dict:
    required, filled = _demand_headcounts(demand)
    assignments = []
    for allocation in demand.allocations or []:
        if allocation.status not in _ROSTER_STATUSES:
            continue
        employee = allocation.employee
        if employee is None:
            continue
        assignments.append(
            _serialize_allocation(
                allocation,
                employee,
                allocations_by_employee.get(employee.id, []),
            )
        )
    return {
        "demand_id": demand.id,
        "role": demand.role,
        "description": demand.description,
        "seniority_level": (
            demand.required_experience_level.value
            if demand.required_experience_level is not None
            and hasattr(demand.required_experience_level, "value")
            else None
        ),
        "hours_per_day": float(demand.hours_per_day or 0),
        "headcount_required": required,
        "headcount_filled": len(assignments),
        "headcount_needed": max(int(demand.headcount_needed or 0), 0),
        "status": _demand_status(demand),
        "required_skills": skills_from_demand(demand),
        "target_bill_rate": (
            float(demand.target_bill_rate)
            if demand.target_bill_rate is not None
            else None
        ),
        "target_margin": (
            float(demand.target_margin)
            if demand.target_margin is not None
            else None
        ),
        "needed_by_date": demand.needed_by_date,
        "assignments": assignments,
    }


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    result = await session.execute(
        select(Project)
        .options(
            selectinload(Project.demands)
            .selectinload(ProjectDemand.allocations)
            .selectinload(Allocation.employee)
        )
        .where(Project.id == project_id)
    )
    project = result.scalars().first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _allocations_by_employee(
    session: AsyncSession, employee_ids: set[uuid.UUID]
) -> dict:
    if not employee_ids:
        return {}
    result = await session.execute(
        select(Allocation).where(
            Allocation.employee_id.in_(employee_ids),
            Allocation.status.in_(list(_ROSTER_STATUSES)),
        )
    )
    grouped: dict = {}
    for allocation in result.scalars().all():
        grouped.setdefault(allocation.employee_id, []).append(allocation)
    return grouped




# __ Project Creation ___________________________________________________________

class _InitialDemandCreate(BaseModel):
    role: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    required_skills: list[str] = Field(default_factory=list)
    headcount_needed: int = Field(default=1, ge=1, le=50)
    hours_per_day: Decimal = Field(default=Decimal("8.00"))
    target_bill_rate: Optional[float] = Field(default=None, ge=0)
    needed_by_date: Optional[date] = None


class _ProjectCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    client_name: Optional[str] = Field(default=None, max_length=255)
    status: str = Field(default="Pipeline")
    probability: int = Field(default=75, ge=0, le=100)
    target_margin: float = Field(default=35.0, ge=0.0, le=100.0)
    total_budget: float = Field(default=0.0, ge=0.0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    initial_demands: list[_InitialDemandCreate] = Field(default_factory=list)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: _ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_roles("manager", "admin")),
):
    """Create a new project with optional initial role demands."""
    proj = Project(
        name=payload.name,
        client_name=payload.client_name,
        status=payload.status,
        probability=payload.probability,
        target_margin=Decimal(str(payload.target_margin)),
        total_budget=Decimal(str(payload.total_budget)),
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    db.add(proj)
    await db.flush()  # get proj.id

    demand_ids = []
    for d in payload.initial_demands:
        demand = ProjectDemand(
            project_id=proj.id,
            role=d.role,
            description=d.description,
            required_skills=d.required_skills or [],
            headcount_needed=d.headcount_needed,
            headcount_required=d.headcount_needed,
            hours_per_day=d.hours_per_day,
            target_bill_rate=Decimal(str(d.target_bill_rate)) if d.target_bill_rate is not None else None,
            target_margin=Decimal(str(payload.target_margin)),
            win_probability=Decimal(str(payload.probability / 100.0)),
            status=DemandStatusEnum.open,
            needed_by_date=d.needed_by_date,
        )
        db.add(demand)
        await db.flush()
        demand_ids.append(str(demand.id))

    await db.commit()
    logger.info(f"Project created: {proj.id} '{proj.name}' by user {current_user.get('id')}")

    return {
        "id": str(proj.id),
        "name": proj.name,
        "client_name": proj.client_name,
        "status": proj.status,
        "probability": proj.probability,
        "target_margin": float(proj.target_margin),
        "total_budget": float(proj.total_budget),
        "start_date": proj.start_date,
        "end_date": proj.end_date,
        "demand_ids": demand_ids,
    }

@router.get("", status_code=status.HTTP_200_OK)
@router.get("/", include_in_schema=False)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Managers and admins can browse every company project."""
    result = await db.execute(
        select(Project)
        .options(
            selectinload(Project.demands).selectinload(ProjectDemand.allocations)
        )
        .order_by(Project.name.asc())
    )
    return [_serialize_project(project) for project in result.scalars().all()]


@router.get("/{project_id}", status_code=status.HTTP_200_OK)
async def project_detail(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    project = await _load_project(db, project_id)
    employee_ids = {
        allocation.employee_id
        for demand in project.demands
        for allocation in demand.allocations
        if allocation.status in _ROSTER_STATUSES
    }
    grouped = await _allocations_by_employee(db, employee_ids)

    roster = [
        _serialize_demand(demand, grouped) for demand in project.demands
    ]
    utilization_statuses = set(_CURRENT_STATUSES)
    if str(project.status).lower() in ("closed", "completed"):
        utilization_statuses.add(AllocationStatusEnum.completed)
    demanded = sum(
        float(demand.hours_per_day or 0) * max(int(demand.headcount_required or 1), 1)
        for demand in project.demands
    )
    allocated = sum(
        float(allocation.allocated_hours_per_day or 0)
        for demand in project.demands
        for allocation in demand.allocations
        if allocation.status in utilization_statuses
    )
    required_seats = sum(
        max(int(d.headcount_required or 1), 1) for d in project.demands
    )
    filled_seats = sum(
        len(
            [
                a
                for a in demand.allocations
                if a.status in utilization_statuses
            ]
        )
        for demand in project.demands
    )
    return {
        "project": _serialize_project(project),
        "utilization": {
            "demanded_hours_per_day": round(demanded, 2),
            "allocated_hours_per_day": round(allocated, 2),
            "filled_seats": filled_seats,
            "required_seats": required_seats,
            "utilization_pct": (
                round(allocated / demanded * 100, 2) if demanded > 0 else 0.0
            ),
        },
        "roster": roster,
    }


@router.get("/{project_id}/roster", status_code=status.HTTP_200_OK)
async def project_roster(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    detail = await project_detail(project_id, db, user)
    return detail["roster"]


@router.get("/{project_id}/recommendations", status_code=status.HTTP_200_OK)
async def project_recommendations(
    project_id: uuid.UUID,
    demand_id: uuid.UUID = Query(...),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    demand = await db.get(ProjectDemand, demand_id)
    if demand is None or demand.project_id != project_id:
        raise HTTPException(status_code=404, detail="Demand not found on project")

    required = skills_from_demand(demand)
    demand_hours = Decimal(str(demand.hours_per_day or 8))

    result = await db.execute(
        select(Employee)
        .options(
            selectinload(Employee.allocations)
            .selectinload(Allocation.project_demand)
            .selectinload(ProjectDemand.project)
        )
        .where(Employee.is_active.is_(True))
    )
    candidates = []
    for employee in result.scalars().all():
        summary = summarize_allocations(employee, employee.allocations)
        if summary["remaining_available_hours"] < demand_hours:
            continue

        employee_skills = normalize_skills(
            [s.strip() for s in (employee.skills or "").split(",") if s.strip()]
        )
        matched = []
        missing = []
        for skill in required:
            key = skill.lower()
            if any(key in candidate.lower() or candidate.lower() in key for candidate in employee_skills):
                matched.append(skill)
            else:
                missing.append(skill)
        coverage = len(matched) / len(required) if required else 0.5

        cost = float(employee.hourly_cost_rate or 0) or 65.0
        bill = float(demand.target_bill_rate or 0) or 100.0
        margin = max(0.0, (bill - cost) / bill) if bill else 0.0
        score = round(min(1.0, 0.85 * coverage + 0.15 * margin), 4)

        candidates.append(
            {
                "employee_id": employee.id,
                "employee_name": _employee_name(employee),
                "job_title": employee.job_title,
                "manager_id": employee.manager_id,
                "remaining_available_hours": float(
                    summary["remaining_available_hours"]
                ),
                "daily_capacity_hours": float(effective_capacity(employee)),
                "match_score": score,
                "matched_skills": matched,
                "missing_skills": missing,
                "current_allocations": [
                    {
                        "allocation_id": allocation.id,
                        "project_id": allocation.project_id,
                        "project_name": (
                            allocation.project_demand.project.name
                            if allocation.project_demand
                            and allocation.project_demand.project
                            else None
                        ),
                        "role_on_project": allocation.role_on_project,
                        "allocated_hours_per_day": float(
                            allocation.allocated_hours_per_day or 0
                        ),
                        "allocation_pct": float(allocation.allocation_pct or 0),
                        "status": _status_value(allocation.status),
                        "start_date": allocation.start_date,
                        "end_date": allocation.end_date,
                    }
                    for allocation in summary["allocations"]
                ],
            }
        )

    candidates.sort(key=lambda item: item["match_score"], reverse=True)
    return candidates[:limit]


@router.post(
    "/{project_id}/demands",
    status_code=status.HTTP_201_CREATED,
)
async def create_demand(
    project_id: uuid.UUID,
    payload: DemandCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    demand = ProjectDemand(
        project_id=project_id,
        role=payload.role,
        description=payload.description,
        required_skills=payload.required_skills,
        hours_per_day=payload.hours_per_day,
        required_experience_level=(
            ExperienceLevelEnum(payload.required_experience_level)
            if payload.required_experience_level
            else None
        ),
        min_proficiency=payload.min_proficiency,
        headcount_needed=payload.headcount_required,
        headcount_required=payload.headcount_required,
        target_bill_rate=payload.target_bill_rate,
        target_margin=payload.target_margin,
        win_probability=payload.win_probability,
        needed_by_date=payload.needed_by_date,
        status=DemandStatusEnum.open,
    )
    db.add(demand)
    await db.commit()
    await db.refresh(demand)
    return {"demand_id": demand.id, "status": _demand_status(demand)}


@router.patch(
    "/{project_id}/demands/{demand_id}",
    status_code=status.HTTP_200_OK,
)
async def update_demand(
    project_id: uuid.UUID,
    demand_id: uuid.UUID,
    payload: DemandUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    demand = await db.get(ProjectDemand, demand_id)
    if demand is None or demand.project_id != project_id:
        raise HTTPException(status_code=404, detail="Demand not found on project")

    data = payload.model_dump(exclude_unset=True)
    cancelled = data.pop("cancelled", False)
    if "required_experience_level" in data and data["required_experience_level"]:
        data["required_experience_level"] = ExperienceLevelEnum(
            data["required_experience_level"]
        )
    if payload.headcount_required is not None:
        delta = payload.headcount_required - int(demand.headcount_required or 1)
        demand.headcount_needed = max(
            0, int(demand.headcount_needed or 0) + delta
        )
        demand.headcount_required = payload.headcount_required
    for key, value in data.items():
        if key == "status":
            if value == "filled":
                demand.headcount_needed = 0
                value = DemandStatusEnum.filled
            elif value == "partially_filled":
                demand.headcount_needed = max(
                    int(demand.headcount_needed or 0), 1
                )
                value = DemandStatusEnum.partially_filled
            elif value == "requires_training":
                value = DemandStatusEnum.requires_training
            else:
                value = DemandStatusEnum.open
        setattr(demand, key, value)
    if cancelled:
        demand.status = DemandStatusEnum.cancelled
    await db.commit()
    await db.refresh(demand)
    return {"demand_id": demand.id, "status": _demand_status(demand)}


@router.post(
    "/{project_id}/demands/{demand_id}/assign",
    status_code=status.HTTP_200_OK,
)
async def assign_to_demand(
    project_id: uuid.UUID,
    demand_id: uuid.UUID,
    payload: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    """Manager-scoped assignment: managers may only assign direct reports."""
    employee_id_raw = payload.get("employee_id")
    if not employee_id_raw:
        raise HTTPException(status_code=422, detail="employee_id is required")
    try:
        employee_id = uuid.UUID(str(employee_id_raw))
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid employee_id")

    demand = await db.get(ProjectDemand, demand_id)
    if demand is None or demand.project_id != project_id:
        raise HTTPException(status_code=404, detail="Demand not found on project")

    await assert_can_manage_employee(db, user, employee_id)
    service = AllocationService(db)
    try:
        allocation = await service.create_allocation(
            employee_id=employee_id,
            demand_id=demand_id,
            allocated_hours_per_day=payload.get("allocated_hours_per_day"),
            role_on_project=payload.get("role_on_project"),
            start_date=_as_date(payload.get("start_date")),
            end_date=_as_date(payload.get("end_date")),
            justification=payload.get("ai_justification"),
            created_by=uuid.UUID(user["id"]) if user.get("id") else None,
        )
    except CapacityExceededError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    notification = None
    try:
        notification = await NotificationService(db).notify_employee_allocated(
            allocation
        )
    except Exception:
        logger.exception("Could not queue employee_allocated notification")

    await db.commit()
    if notification is not None and notification.status == "queued":
        schedule_notification(notification.id, background_tasks)
    return {
        "status": "assigned",
        "allocation_id": str(allocation.id),
        "employee_id": str(allocation.employee_id),
        "allocated_hours_per_day": float(allocation.allocated_hours_per_day),
        "allocation_pct": float(allocation.allocation_pct),
    }
