"""Capacity, allocation and workforce scoping services.

The rules implemented here are the single source of truth for every write path
(manual assignment, HITL approval, forecast execution, allocation updates):

* effective capacity = MIN(employee.daily_capacity_hours, 8.0)
* committed hours = active + approved allocations
* reserved hours = proposed_by_ai + pending_manager_approval allocations
* remaining = effective capacity - committed - reserved
* NULL start/end dates mean "ongoing" for overlap checks
* an allocation never counts against itself when it is being updated
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchStatusEnum,
    Employee,
    EmployeeCost,
    Project,
    ProjectDemand,
    DemandStatusEnum,
)

logger = logging.getLogger(__name__)

HARD_CAP_HOURS = Decimal("8.00")
_COMMITTED = {
    AllocationStatusEnum.active,
    AllocationStatusEnum.approved_by_manager,
}
_RESERVED = {
    AllocationStatusEnum.proposed_by_ai,
    AllocationStatusEnum.pending_manager_approval,
}


class CapacityExceededError(ValueError):
    """Raised when an allocation would exceed the employee's daily capacity."""

    def __init__(
        self,
        *,
        employee_id: uuid.UUID,
        requested_hours: Decimal,
        remaining_hours: Decimal,
        capacity_hours: Decimal,
        committed_hours: Decimal,
        reserved_hours: Decimal,
    ) -> None:
        self.employee_id = employee_id
        self.requested_hours = requested_hours
        self.remaining_hours = remaining_hours
        self.capacity_hours = capacity_hours
        self.committed_hours = committed_hours
        self.reserved_hours = reserved_hours
        super().__init__(
            f"Cannot allocate {requested_hours}h/day to employee {employee_id}: "
            f"only {remaining_hours}h/day remains of a {capacity_hours}h/day capacity "
            f"(committed={committed_hours}h, reserved={reserved_hours}h)."
        )

    def as_detail(self) -> dict:
        return {
            "code": "capacity_exceeded",
            "message": str(self),
            "employee_id": str(self.employee_id),
            "requested_hours_per_day": float(self.requested_hours),
            "remaining_available_hours": float(self.remaining_hours),
            "daily_capacity_hours": float(self.capacity_hours),
            "committed_hours_per_day": float(self.committed_hours),
            "reserved_hours_per_day": float(self.reserved_hours),
        }


def _decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def effective_capacity(employee: Employee) -> Decimal:
    """Contracted capacity, always bounded by the absolute 8h/day cap."""
    return min(
        _decimal(getattr(employee, "daily_capacity_hours", None), "8"),
        HARD_CAP_HOURS,
    )


def allocation_overlaps(allocation: Allocation, on_date: Optional[date] = None) -> bool:
    """Return True when an allocation is active on *on_date*.

    NULL start or end dates are treated as open-ended/ongoing.
    """
    day = on_date or date.today()
    start = getattr(allocation, "start_date", None)
    end = getattr(allocation, "end_date", None)
    if start is not None and start > day:
        return False
    if end is not None and end < day:
        return False
    return True


def _status_value(status) -> str:
    return status.value if hasattr(status, "value") else str(status)


def summarize_allocations(
    employee: Employee,
    allocations: Iterable[Allocation],
    *,
    exclude_allocation_id: Optional[uuid.UUID] = None,
    on_date: Optional[date] = None,
) -> dict:
    """Compute committed/reserved/remaining hours for one employee."""
    capacity = effective_capacity(employee)
    committed = Decimal("0")
    reserved = Decimal("0")
    included: list[Allocation] = []

    for allocation in allocations:
        if exclude_allocation_id is not None and allocation.id == exclude_allocation_id:
            continue
        if not allocation_overlaps(allocation, on_date):
            continue
        status = _status_value(allocation.status)
        hours = _decimal(allocation.allocated_hours_per_day)
        if status in {s.value for s in _COMMITTED}:
            committed += hours
            included.append(allocation)
        elif status in {s.value for s in _RESERVED}:
            reserved += hours
            included.append(allocation)

    remaining = max(Decimal("0"), capacity - committed - reserved)
    utilization_pct = (
        (committed / capacity * Decimal("100")).quantize(Decimal("0.01"))
        if capacity > 0
        else Decimal("0.00")
    )
    return {
        "daily_capacity_hours": capacity,
        "committed_hours_per_day": committed,
        "reserved_hours_per_day": reserved,
        "remaining_available_hours": remaining,
        "utilization_pct": utilization_pct,
        "allocations": included,
    }


async def _load_employee(
    session: AsyncSession, employee_id: uuid.UUID
) -> Optional[Employee]:
    result = await session.execute(
        select(Employee)
        .options(selectinload(Employee.allocations))
        .where(Employee.id == employee_id)
    )
    return result.scalars().first()


async def _load_allocations(
    session: AsyncSession, employee_id: uuid.UUID
) -> list[Allocation]:
    result = await session.execute(
        select(Allocation).where(Allocation.employee_id == employee_id)
    )
    return list(result.scalars().all())


async def latest_hourly_cost(
    session: AsyncSession, employee: Employee
) -> float:
    result = await session.execute(
        select(EmployeeCost)
        .where(EmployeeCost.employee_id == employee.id)
        .order_by(EmployeeCost.effective_date.desc())
    )
    cost = result.scalars().first()
    if cost and cost.internal_hourly_cost:
        return float(cost.internal_hourly_cost)
    if getattr(employee, "hourly_cost_rate", None):
        return float(employee.hourly_cost_rate)
    level = (
        employee.experience_level.value
        if hasattr(employee.experience_level, "value")
        else str(employee.experience_level or "mid")
    )
    defaults = {
        "junior": 40.0,
        "mid": 65.0,
        "senior": 95.0,
        "lead": 125.0,
        "principal": 160.0,
    }
    return defaults.get(level.lower(), 65.0)


def recalculate_allocation(
    allocation: Allocation,
    employee: Employee,
    requested_hours: Optional[Decimal] = None,
) -> Decimal:
    capacity = effective_capacity(employee)
    hours = _decimal(
        requested_hours
        if requested_hours is not None
        else allocation.allocated_hours_per_day,
        "8",
    )
    if hours <= 0:
        raise ValueError("allocated_hours_per_day must be greater than 0")
    if hours > HARD_CAP_HOURS:
        raise ValueError("allocated_hours_per_day must not exceed 8.0")
    allocation.allocated_hours_per_day = hours
    allocation.allocation_pct = (
        (hours / capacity * Decimal("100")).quantize(Decimal("0.01"))
        if capacity > 0
        else Decimal("0.00")
    )
    return allocation.allocation_pct


class CapacityService:
    """Async capacity checks around an AsyncSession."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_employee(self, employee_id: uuid.UUID) -> Employee:
        employee = await _load_employee(self.session, employee_id)
        if not employee:
            raise ValueError(f"Employee {employee_id} not found")
        return employee

    async def get_capacity(
        self,
        employee_id: uuid.UUID,
        *,
        exclude_allocation_id: Optional[uuid.UUID] = None,
        on_date: Optional[date] = None,
        employee: Optional[Employee] = None,
    ) -> dict:
        emp = employee or await self.get_employee(employee_id)
        # Always re-read allocations: the identity map can hold a stale
        # relationship collection after a previous allocation was flushed.
        allocations = await _load_allocations(self.session, employee_id)
        summary = summarize_allocations(
            emp,
            allocations,
            exclude_allocation_id=exclude_allocation_id,
            on_date=on_date,
        )
        summary["employee"] = emp
        return summary

    async def assert_capacity(
        self,
        employee_id: uuid.UUID,
        requested_hours,
        *,
        exclude_allocation_id: Optional[uuid.UUID] = None,
        on_date: Optional[date] = None,
        employee: Optional[Employee] = None,
    ) -> dict:
        hours = _decimal(requested_hours)
        if hours <= 0:
            raise ValueError("requested hours must be greater than 0")
        if hours > HARD_CAP_HOURS:
            raise ValueError("requested hours must not exceed 8.0")

        summary = await self.get_capacity(
            employee_id,
            exclude_allocation_id=exclude_allocation_id,
            on_date=on_date,
            employee=employee,
        )
        if hours > summary["remaining_available_hours"]:
            raise CapacityExceededError(
                employee_id=employee_id,
                requested_hours=hours,
                remaining_hours=summary["remaining_available_hours"],
                capacity_hours=summary["daily_capacity_hours"],
                committed_hours=summary["committed_hours_per_day"],
                reserved_hours=summary["reserved_hours_per_day"],
            )
        return summary

    async def sync_employee_status(self, employee_id: uuid.UUID) -> None:
        employee = await self.get_employee(employee_id)
        summary = await self.get_capacity(employee_id, employee=employee)
        if summary["committed_hours_per_day"] > 0:
            if employee.bench_status != BenchStatusEnum.internal:
                employee.bench_status = BenchStatusEnum.on_project
            employee.bench_start_date = None
        elif employee.bench_status != BenchStatusEnum.internal:
            employee.bench_status = BenchStatusEnum.bench
            employee.current_project = None
            if employee.bench_start_date is None:
                employee.bench_start_date = date.today()


class AllocationService:
    """Create/update/release allocations with capacity + project-skill effects."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.capacity = CapacityService(session)

    async def create_allocation(
        self,
        *,
        employee_id: uuid.UUID,
        demand_id: Optional[uuid.UUID] = None,
        allocated_hours_per_day=None,
        role_on_project: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        status: AllocationStatusEnum = AllocationStatusEnum.active,
        justification: Optional[str] = None,
        created_by: Optional[uuid.UUID] = None,
    ) -> Allocation:
        employee = await self.capacity.get_employee(employee_id)
        demand: Optional[ProjectDemand] = None
        if demand_id:
            demand = await self.session.get(ProjectDemand, demand_id)
            if not demand:
                raise ValueError(f"Demand {demand_id} not found")
            if (
                status in _COMMITTED
                and (demand.headcount_needed or 0) <= 0
            ):
                raise ValueError(
                    f"Demand {demand_id} is already fully filled"
                )

        hours = allocated_hours_per_day
        if hours is None and demand is not None:
            hours = demand.hours_per_day
        if hours is None:
            hours = Decimal("8.00")

        await self.capacity.assert_capacity(
            employee_id,
            hours,
            exclude_allocation_id=None,
            on_date=start_date or date.today(),
            employee=employee,
        )

        role = role_on_project or (demand.role if demand else employee.job_title or "Engineer")
        allocation = Allocation(
            employee_id=employee_id,
            demand_id=demand_id,
            role_on_project=role,
            allocated_hours_per_day=_decimal(hours),
            allocation_pct=Decimal("0"),
            status=status,
            start_date=start_date or date.today(),
            end_date=end_date,
            ai_justification=justification or "Manual manager assignment.",
            created_by=created_by,
        )
        recalculate_allocation(allocation, employee)
        self.session.add(allocation)
        await self.session.flush()

        if demand is not None and status in _COMMITTED:
            demand.headcount_needed = max(0, (demand.headcount_needed or 0) - 1)
            demand.status = self._demand_status(demand)

        employee.bench_status = BenchStatusEnum.on_project
        employee.bench_start_date = None
        if demand and demand.project_id:
            project = await self.session.get(Project, demand.project_id)
            employee.current_project = project.name if project else role
        elif role:
            employee.current_project = role

        if demand is not None and status in _COMMITTED:
            from src.services.skills_service import apply_project_skills

            await apply_project_skills(
                self.session, employee_id, demand
            )

        await self.session.flush()
        await self.session.refresh(allocation)
        return allocation

    async def update_allocation(
        self,
        allocation: Allocation,
        *,
        allocated_hours_per_day=None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        status: Optional[AllocationStatusEnum] = None,
        manager_notes: Optional[str] = None,
    ) -> Allocation:
        employee = await self.capacity.get_employee(allocation.employee_id)

        if allocated_hours_per_day is not None:
            await self.capacity.assert_capacity(
                allocation.employee_id,
                allocated_hours_per_day,
                exclude_allocation_id=allocation.id,
                on_date=start_date or allocation.start_date or date.today(),
                employee=employee,
            )
            recalculate_allocation(
                allocation, employee, _decimal(allocated_hours_per_day)
            )
        else:
            recalculate_allocation(allocation, employee)

        previous_status = _status_value(allocation.status)
        if start_date is not None:
            allocation.start_date = start_date
        if end_date is not None:
            allocation.end_date = end_date
        if status is not None:
            allocation.status = status
        if manager_notes is not None:
            allocation.manager_notes = manager_notes

        demand = (
            await self.session.get(ProjectDemand, allocation.demand_id)
            if allocation.demand_id
            else None
        )
        if demand is not None:
            previous_committed = previous_status in {
                s.value for s in _COMMITTED
            }
            new_committed = _status_value(allocation.status) in {
                s.value for s in _COMMITTED
            }
            if not previous_committed and new_committed:
                demand.headcount_needed = max(
                    0, int(demand.headcount_needed or 0) - 1
                )
                from src.services.skills_service import apply_project_skills

                await apply_project_skills(self.session, allocation.employee_id, demand)
            elif previous_committed and not new_committed:
                required = max(int(demand.headcount_required or 1), 1)
                demand.headcount_needed = min(
                    required, int(demand.headcount_needed or 0) + 1
                )
            if previous_committed != new_committed:
                demand.status = self._demand_status(demand)

        await self.session.flush()
        await self.capacity.sync_employee_status(allocation.employee_id)
        await self.session.flush()
        await self.session.refresh(allocation)
        return allocation

    async def release_allocation(self, allocation: Allocation) -> Allocation:
        was_committed = _status_value(allocation.status) in {
            s.value for s in _COMMITTED
        }
        allocation.status = AllocationStatusEnum.completed
        allocation.end_date = date.today()
        if allocation.demand_id:
            demand = await self.session.get(ProjectDemand, allocation.demand_id)
            if demand is not None:
                if was_committed:
                    required = max(
                        int(demand.headcount_required or 1), 1
                    )
                    demand.headcount_needed = min(
                        required,
                        int(demand.headcount_needed or 0) + 1,
                    )
                demand.status = self._demand_status(demand)
        await self.session.flush()
        await self.capacity.sync_employee_status(allocation.employee_id)
        await self.session.flush()
        await self.session.refresh(allocation)
        return allocation

    @staticmethod
    def _demand_status(demand: ProjectDemand) -> DemandStatusEnum:
        if demand.status == DemandStatusEnum.requires_training:
            return DemandStatusEnum.requires_training
        required = max(int(demand.headcount_required or 1), 1)
        needed = max(int(demand.headcount_needed or 0), 0)
        if needed <= 0:
            return DemandStatusEnum.filled
        if needed < required:
            return DemandStatusEnum.partially_filled
        return DemandStatusEnum.open


async def list_allocations(
    session: AsyncSession,
    *,
    employee_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    statuses: Optional[list[AllocationStatusEnum]] = None,
) -> list[Allocation]:
    stmt = (
        select(Allocation)
        .options(
            selectinload(Allocation.employee),
            selectinload(Allocation.project_demand).selectinload(ProjectDemand.project),
        )
        .order_by(Allocation.created_at.desc())
    )
    if employee_id:
        stmt = stmt.where(Allocation.employee_id == employee_id)
    if project_id:
        stmt = stmt.join(ProjectDemand, Allocation.demand_id == ProjectDemand.id).where(
            ProjectDemand.project_id == project_id
        )
    if statuses:
        stmt = stmt.where(Allocation.status.in_(statuses))
    result = await session.execute(stmt)
    return list(result.scalars().all())
