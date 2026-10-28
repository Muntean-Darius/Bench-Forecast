"""People-manager team workload, candidate drawer and employee capacity APIs."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.deps import (
    assert_can_manage_employee,
    get_current_user,
    is_admin,
    manager_employee_id,
    require_roles,
)
from src.database.database import get_db
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    Employee,
    EmploymentTypeEnum,
    ProjectDemand,
)
from src.schemas.workforce import ScheduleUpdate
from src.services.skills_service import normalize_skills, skills_by_source, skills_from_demand
from src.services.workforce_service import (
    CapacityService,
    effective_capacity,
    summarize_allocations,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Team & Capacity"])

_CURRENT = {
    AllocationStatusEnum.active,
    AllocationStatusEnum.approved_by_manager,
    AllocationStatusEnum.pending_manager_approval,
    AllocationStatusEnum.proposed_by_ai,
}


def _name(emp: Employee) -> str:
    return f"{emp.first_name} {emp.last_name}".strip()


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _serialize_allocation(allocation: Allocation) -> dict:
    demand = allocation.project_demand
    project = demand.project if demand else None
    return {
        "allocation_id": allocation.id,
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "role_on_project": allocation.role_on_project,
        "allocated_hours_per_day": float(allocation.allocated_hours_per_day or 0),
        "allocation_pct": float(allocation.allocation_pct or 0),
        "status": _status_value(allocation.status),
        "start_date": allocation.start_date,
        "end_date": allocation.end_date,
    }


def _serialize_workload(employee: Employee, pending_cv: int = 0) -> dict:
    allocations = list(employee.allocations or [])
    summary = summarize_allocations(employee, allocations)
    next_rolloff = None
    for allocation in allocations:
        if allocation.end_date and allocation.status in _CURRENT:
            if next_rolloff is None or allocation.end_date < next_rolloff:
                next_rolloff = allocation.end_date
    return {
        "employee_id": employee.id,
        "employee_name": _name(employee),
        "job_title": employee.job_title,
        "manager_id": employee.manager_id,
        "manager_name": _name(employee.manager) if employee.manager else None,
        "employment_type": (
            employee.employment_type.value
            if hasattr(employee.employment_type, "value")
            else str(employee.employment_type)
        ),
        "daily_capacity_hours": float(summary["daily_capacity_hours"]),
        "committed_hours_per_day": float(summary["committed_hours_per_day"]),
        "reserved_hours_per_day": float(summary["reserved_hours_per_day"]),
        "remaining_available_hours": float(summary["remaining_available_hours"]),
        "utilization_pct": float(summary["utilization_pct"]),
        "bench_status": _status_value(employee.bench_status),
        "allocations": [_serialize_allocation(a) for a in allocations],
        "next_rolloff_date": next_rolloff,
        "pending_cv_reviews": pending_cv,
    }


async def _load_employee(session: AsyncSession, employee_id: uuid.UUID) -> Employee:
    result = await session.execute(
        select(Employee)
        .options(
            selectinload(Employee.allocations)
            .selectinload(Allocation.project_demand)
            .selectinload(ProjectDemand.project),
            selectinload(Employee.manager),
        )
        .where(Employee.id == employee_id)
    )
    employee = result.scalars().first()
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.get("/team", status_code=status.HTTP_200_OK)
async def team_workload(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    """Managers see direct reports; admins see the whole company."""
    stmt = (
        select(Employee)
        .options(
            selectinload(Employee.allocations)
            .selectinload(Allocation.project_demand)
            .selectinload(ProjectDemand.project),
            selectinload(Employee.manager),
        )
        .where(Employee.is_active.is_(True))
    )
    if not is_admin(user):
        manager_id = manager_employee_id(user)
        if not manager_id:
            raise HTTPException(
                status_code=403,
                detail="Manager account is not linked to an employee record.",
            )
        stmt = stmt.where(Employee.manager_id == uuid.UUID(manager_id))
    stmt = stmt.order_by(Employee.last_name.asc(), Employee.first_name.asc())
    employees = list((await db.execute(stmt)).scalars().all())
    return [_serialize_workload(employee) for employee in employees]


@router.get("/team/workload", status_code=status.HTTP_200_OK)
async def team_workload_alias(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    return await team_workload(db=db, user=user)


@router.get("/team/candidates", status_code=status.HTTP_200_OK)
async def team_candidates(
    demand_id: uuid.UUID = Query(...),
    min_hours: Optional[Decimal] = Query(default=None),
    q: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    """Candidate drawer.

    Managers may only see/assign their own direct reports. Admins see everyone.
    """
    demand = await db.get(ProjectDemand, demand_id)
    if demand is None:
        raise HTTPException(status_code=404, detail="Demand not found")

    needed_hours = Decimal(str(min_hours or demand.hours_per_day or 8))
    required = skills_from_demand(demand)

    stmt = (
        select(Employee)
        .options(
            selectinload(Employee.allocations)
            .selectinload(Allocation.project_demand)
            .selectinload(ProjectDemand.project),
            selectinload(Employee.manager),
        )
        .where(Employee.is_active.is_(True))
    )
    if not is_admin(user):
        manager_id = manager_employee_id(user)
        if not manager_id:
            raise HTTPException(
                status_code=403,
                detail="Manager account is not linked to an employee record.",
            )
        stmt = stmt.where(Employee.manager_id == uuid.UUID(manager_id))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (Employee.first_name.ilike(like))
            | (Employee.last_name.ilike(like))
            | (Employee.job_title.ilike(like))
        )
    employees = list((await db.execute(stmt)).scalars().all())

    result = []
    for employee in employees:
        summary = summarize_allocations(employee, employee.allocations)
        if summary["remaining_available_hours"] < needed_hours:
            continue
        employee_skills = normalize_skills(
            [s.strip() for s in (employee.skills or "").split(",") if s.strip()]
        )
        matched, missing = [], []
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
        result.append(
            {
                "employee_id": employee.id,
                "employee_name": _name(employee),
                "job_title": employee.job_title,
                "manager_id": employee.manager_id,
                "remaining_available_hours": float(
                    summary["remaining_available_hours"]
                ),
                "daily_capacity_hours": float(effective_capacity(employee)),
                "match_score": round(
                    min(1.0, 0.85 * coverage + 0.15 * margin), 4
                ),
                "matched_skills": matched,
                "missing_skills": missing,
                "current_allocations": [
                    _serialize_allocation(a) for a in summary["allocations"]
                ],
            }
        )

    result.sort(key=lambda item: item["match_score"], reverse=True)
    return result


@router.get("/employees/{employee_id}/capacity", status_code=status.HTTP_200_OK)
async def employee_capacity(
    employee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    await assert_can_manage_employee(db, user, employee_id)
    employee = await _load_employee(db, employee_id)
    service = CapacityService(db)
    summary = await service.get_capacity(employee_id, employee=employee)
    return {
        "employee_id": employee.id,
        "employee_name": _name(employee),
        "employment_type": (
            employee.employment_type.value
            if hasattr(employee.employment_type, "value")
            else str(employee.employment_type)
        ),
        "daily_capacity_hours": float(summary["daily_capacity_hours"]),
        "committed_hours_per_day": float(summary["committed_hours_per_day"]),
        "reserved_hours_per_day": float(summary["reserved_hours_per_day"]),
        "remaining_available_hours": float(summary["remaining_available_hours"]),
        "utilization_pct": float(summary["utilization_pct"]),
        "allocations": [
            _serialize_allocation(a) for a in summary["allocations"]
        ],
    }


@router.get("/employees/{employee_id}/skills", status_code=status.HTTP_200_OK)
async def employee_skills_by_source(
    employee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    await assert_can_manage_employee(db, user, employee_id)
    return await skills_by_source(db, employee_id)


@router.patch("/employees/{employee_id}/schedule", status_code=status.HTTP_200_OK)
async def update_schedule(
    employee_id: uuid.UUID,
    payload: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    await assert_can_manage_employee(db, user, employee_id)
    employee = await _load_employee(db, employee_id)

    if payload.employment_type is not None:
        employee.employment_type = EmploymentTypeEnum(payload.employment_type)
    if payload.daily_capacity_hours is not None:
        employee.daily_capacity_hours = payload.daily_capacity_hours
    elif payload.employment_type == "part_time":
        employee.daily_capacity_hours = Decimal("4.00")

    if (
        employee.daily_capacity_hours is None
        or employee.daily_capacity_hours <= 0
        or employee.daily_capacity_hours > 8
    ):
        raise HTTPException(
            status_code=422,
            detail="daily_capacity_hours must be between 0 and 8.0",
        )

    await db.flush()
    summary = await CapacityService(db).get_capacity(
        employee_id, employee=employee
    )
    if (
        summary["committed_hours_per_day"]
        + summary["reserved_hours_per_day"]
        > summary["daily_capacity_hours"]
    ):
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "capacity_exceeded",
                "message": (
                    "The new schedule is below the employee's existing "
                    "committed + reserved hours."
                ),
                "committed_hours_per_day": float(
                    summary["committed_hours_per_day"]
                ),
                "reserved_hours_per_day": float(
                    summary["reserved_hours_per_day"]
                ),
                "daily_capacity_hours": float(
                    summary["daily_capacity_hours"]
                ),
            },
        )

    # Recalculate existing allocation percentages against the new capacity.
    from src.services.workforce_service import recalculate_allocation

    for allocation in employee.allocations:
        recalculate_allocation(allocation, employee)

    await db.commit()
    await db.refresh(employee)
    return {
        "employee_id": employee.id,
        "employment_type": (
            employee.employment_type.value
            if hasattr(employee.employment_type, "value")
            else str(employee.employment_type)
        ),
        "daily_capacity_hours": float(employee.daily_capacity_hours),
    }
