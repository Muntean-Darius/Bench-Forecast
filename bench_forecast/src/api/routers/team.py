"""People-manager team workload, candidate drawer and employee capacity APIs."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
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


# __ Employee Creation ___________________________________________________________

class _EmployeeCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=5)
    job_title: str = Field(..., min_length=1, max_length=200)
    experience_level: str = "mid"
    employment_type: str = "full_time"
    bench_status: str = "bench"
    hourly_cost_rate: float = 65.00
    skills: list[str] = Field(default_factory=list)
    location: str = "Bucharest"


@router.post("/employees", status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: _EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_roles("manager", "admin")),
):
    """Create a new employee with cost record and user account atomically."""
    from src.database.models import EmployeeCost, ExperienceLevelEnum, BenchStatusEnum, User
    from src.auth.auth import hash_password

    # Uniqueness checks
    existing_emp = await db.execute(select(Employee).where(Employee.email == payload.email))
    if existing_emp.scalars().first():
        raise HTTPException(status_code=409, detail=f"Email '{payload.email}' is already registered as an employee.")

    from src.database.models import User as UserModel
    existing_user = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    if existing_user.scalars().first():
        raise HTTPException(status_code=409, detail=f"Email '{payload.email}' already has a user account.")

    emp_number = f"EMP-{uuid.uuid4().hex[:6].upper()}"

    bench_start = date.today() if payload.bench_status == "bench" else None

    skills_str = ", ".join(payload.skills)
    profile_text = (
        f"{payload.first_name} {payload.last_name} — {payload.job_title} "
        f"({payload.experience_level.capitalize()}) — Skills: {skills_str or 'Not specified'}"
    )

    try:
        exp_level = ExperienceLevelEnum(payload.experience_level)
    except ValueError:
        exp_level = ExperienceLevelEnum.mid

    try:
        bench_st = BenchStatusEnum(payload.bench_status)
    except ValueError:
        bench_st = BenchStatusEnum.bench

    try:
        from src.database.models import EmploymentTypeEnum as ET
        emp_type = ET(payload.employment_type)
    except ValueError:
        from src.database.models import EmploymentTypeEnum as ET
        emp_type = ET.full_time

    emp = Employee(
        employee_number=emp_number,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        job_title=payload.job_title,
        experience_level=exp_level,
        employment_type=emp_type,
        bench_status=bench_st,
        hire_date=date.today(),
        bench_start_date=bench_start,
        location=payload.location,
        profile_text=profile_text,
        is_active=True,
        # Legacy shims
        full_name=f"{payload.first_name} {payload.last_name}",
        primary_role=payload.job_title,
        seniority=payload.experience_level,
        hourly_cost_rate=Decimal(str(payload.hourly_cost_rate)),
        skills=skills_str,
    )
    db.add(emp)
    await db.flush()

    hourly = Decimal(str(payload.hourly_cost_rate))
    daily = hourly * 8
    annual = daily * 220

    cost = EmployeeCost(
        employee_id=emp.id,
        effective_date=date.today(),
        internal_hourly_cost=hourly,
        internal_daily_cost=daily,
        annual_salary=annual,
        currency="EUR",
        overhead_multiplier=Decimal("1.35"),
    )
    db.add(cost)
    await db.flush()

    first_lower = payload.first_name.lower()
    last_lower = payload.last_name.lower()
    base_username = f"{first_lower}.{last_lower}"
    existing_unames = {u[0] for u in (await db.execute(select(UserModel.username))).all()}
    username = base_username
    if username in existing_unames:
        username = f"{base_username}.{emp.id.hex[:4]}"

    user = UserModel(
        username=username,
        password_hash=hash_password(username),
        email=payload.email,
        full_name=f"{payload.first_name} {payload.last_name}",
        role="employee",
        employee_id=emp.id,
        is_active=True,
    )
    db.add(user)
    await db.commit()

    logger.info(f"Employee created: {emp.id} '{emp.first_name} {emp.last_name}' username='{username}'")

    return {
        "id": str(emp.id),
        "employee_number": emp.employee_number,
        "first_name": emp.first_name,
        "last_name": emp.last_name,
        "email": emp.email,
        "job_title": emp.job_title,
        "experience_level": payload.experience_level,
        "employment_type": payload.employment_type,
        "bench_status": payload.bench_status,
        "location": emp.location,
        "username": username,
        "message": f"Employee created. Login: username='{username}', password='{username}'",
    }


# __ Admin Manager Management ___________________________________________________

class _ManagerCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    email: str = Field(..., min_length=5)
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    department_id: Optional[uuid.UUID] = None
    employee_id: Optional[uuid.UUID] = None


@router.get("/admin/managers", status_code=status.HTTP_200_OK)
async def list_managers(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_roles("admin")),
):
    """List all manager accounts with direct-report counts."""
    from src.database.models import User as UserModel

    result = await db.execute(select(UserModel).where(UserModel.role == "manager").where(UserModel.is_active.is_(True)))
    managers = result.scalars().all()

    # Count direct reports per manager employee_id
    emp_ids = [m.employee_id for m in managers if m.employee_id]
    dr_counts = {}
    if emp_ids:
        dr_result = await db.execute(select(Employee).where(Employee.manager_id.in_(emp_ids)))
        dr_emps = dr_result.scalars().all()
        for e in dr_emps:
            dr_counts[e.manager_id] = dr_counts.get(e.manager_id, 0) + 1

    return [
        {
            "id": str(m.id),
            "username": m.username,
            "email": m.email,
            "full_name": m.full_name,
            "role": m.role,
            "employee_id": str(m.employee_id) if m.employee_id else None,
            "department_id": str(m.department_id) if m.department_id else None,
            "is_active": m.is_active,
            "direct_reports_count": dr_counts.get(m.employee_id, 0) if m.employee_id else 0,
        }
        for m in managers
    ]


@router.post("/admin/managers", status_code=status.HTTP_201_CREATED)
async def create_manager(
    payload: _ManagerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_roles("admin")),
):
    """Create a new manager user account (admin only)."""
    from src.database.models import User as UserModel
    from src.auth.auth import hash_password

    # Uniqueness checks
    existing_uname = await db.execute(select(UserModel).where(UserModel.username == payload.username))
    if existing_uname.scalars().first():
        raise HTTPException(status_code=409, detail=f"Username '{payload.username}' is already taken.")

    existing_email = await db.execute(select(UserModel).where(UserModel.email == payload.email))
    if existing_email.scalars().first():
        raise HTTPException(status_code=409, detail=f"Email '{payload.email}' is already registered.")

    new_manager = UserModel(
        username=payload.username,
        password_hash=hash_password(payload.password),
        email=payload.email,
        full_name=payload.full_name,
        role="manager",
        department_id=payload.department_id,
        employee_id=payload.employee_id,
        is_active=True,
    )
    db.add(new_manager)
    await db.commit()
    await db.refresh(new_manager)

    logger.info(f"Manager created: {new_manager.id} username='{new_manager.username}' by admin {current_user.get('id')}")

    return {
        "id": str(new_manager.id),
        "username": new_manager.username,
        "email": new_manager.email,
        "full_name": new_manager.full_name,
        "role": "manager",
        "is_active": True,
        "message": f"Manager account created. Login: username='{payload.username}', password='{payload.password}'",
    }
