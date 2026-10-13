import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    Employee,
    HitlReviewEvent,
    HitlDecisionEnum,
)

class HITLService:
    """Service to handle Human-in-the-Loop review actions for allocations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def submit_proposal(self, allocation_id: uuid.UUID) -> Allocation:
        """Transitions an AI proposal to pending_manager_approval."""
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")
            
        if allocation.status != AllocationStatusEnum.proposed_by_ai:
            raise ValueError(f"Allocation must be 'proposed_by_ai', got '{allocation.status}'")

        allocation.status = AllocationStatusEnum.pending_manager_approval
        await self.session.commit()
        await self.session.refresh(allocation)
        return allocation

    async def approve_allocation(self, allocation_id: uuid.UUID, reviewer_id: uuid.UUID, time_to_decision_seconds: int = None) -> Allocation:
        """Approves an allocation, updating status and logging the event."""
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")

        if allocation.status not in [
            AllocationStatusEnum.pending_manager_approval,
            AllocationStatusEnum.proposed_by_ai
        ]:
            raise ValueError(f"Cannot approve allocation in status '{allocation.status}'")

        allocation.status = AllocationStatusEnum.approved_by_manager
        allocation.approved_by = reviewer_id
        allocation.approved_at = datetime.utcnow()

        event = HitlReviewEvent(
            forecast_run_id=allocation.forecast_run_id,
            allocation_id=allocation.id,
            reviewer_id=reviewer_id,
            decision=HitlDecisionEnum.approved,
            time_to_decision_seconds=time_to_decision_seconds,
            reviewed_at=datetime.utcnow()
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(allocation)
        return allocation

    async def reject_allocation(self, allocation_id: uuid.UUID, reviewer_id: uuid.UUID, feedback: str, time_to_decision_seconds: int = None) -> Allocation:
        """Rejects an allocation with manager feedback."""
        allocation = await self.session.get(Allocation, allocation_id)
        if not allocation:
            raise ValueError(f"Allocation {allocation_id} not found")

        allocation.status = AllocationStatusEnum.rejected_by_manager
        allocation.manager_notes = feedback

        event = HitlReviewEvent(
            forecast_run_id=allocation.forecast_run_id,
            allocation_id=allocation.id,
            reviewer_id=reviewer_id,
            decision=HitlDecisionEnum.rejected,
            rejection_feedback=feedback,
            time_to_decision_seconds=time_to_decision_seconds,
            reviewed_at=datetime.utcnow()
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(allocation)
        return allocation

    async def get_pending_allocations(self) -> List[Allocation]:
        """Fetch all allocations awaiting manager review."""
        stmt = (
            select(Allocation)
            .options(
                joinedload(Allocation.project_demand),
                joinedload(Allocation.employee).selectinload(Employee.employee_costs),
            )
            .where(
                Allocation.status.in_([
                    AllocationStatusEnum.pending_manager_approval,
                    AllocationStatusEnum.proposed_by_ai,
                ])
            )
            .order_by(Allocation.created_at.desc())
        )
        result = await self.session.execute(stmt)
        allocations = list(result.scalars().all())

        # Resolve each employee's most recent hourly cost rate so the HITL UI can
        # render cost rate / margin without empty fields. Fallback chain:
        # employee_costs (latest) → legacy hourly_cost_rate → seniority default.
        sen_defaults = {
            "junior": 40.0,
            "mid": 65.0,
            "senior": 95.0,
            "lead": 125.0,
            "principal": 160.0,
        }
        for allocation in allocations:
            employee = allocation.employee
            if employee is None:
                continue

            cost_rate = None
            employee_costs = getattr(employee, "employee_costs", None) or []
            if employee_costs:
                latest = sorted(
                    employee_costs,
                    key=lambda c: getattr(c, "effective_date", None) or date.min,
                    reverse=True,
                )[0]
                if latest.internal_hourly_cost:
                    cost_rate = latest.internal_hourly_cost

            if cost_rate is None and employee.hourly_cost_rate:
                cost_rate = employee.hourly_cost_rate

            if cost_rate is None:
                sen = str(
                    employee.experience_level.value
                    if hasattr(employee.experience_level, "value")
                    else (employee.experience_level or "mid")
                ).lower()
                cost_rate = Decimal(str(sen_defaults.get(sen, 65.0)))

            cost_dec = Decimal(str(cost_rate))
            # Attach the resolved value for serialisation without marking the
            # mapped column dirty (this is a read-only GET endpoint; get_db
            # commits on success, so a plain assignment would persist a derived
            # value into the legacy column).
            set_committed_value(employee, "hourly_cost_rate", cost_dec)
            employee.cost_rate = cost_dec

        return allocations
