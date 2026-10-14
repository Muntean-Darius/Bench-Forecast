import uuid
from datetime import datetime
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    HitlReviewEvent,
    HitlDecisionEnum,
    Employee,
    BenchStatusEnum,
    ProjectDemand,
    DemandStatusEnum,
    Project,
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

        if allocation.status == AllocationStatusEnum.approved_by_manager:
            return allocation

        if allocation.status not in [
            AllocationStatusEnum.pending_manager_approval,
            AllocationStatusEnum.proposed_by_ai
        ]:
            raise ValueError(f"Cannot approve allocation in status '{allocation.status}'")

        allocation.status = AllocationStatusEnum.approved_by_manager
        allocation.approved_by = reviewer_id
        allocation.approved_at = datetime.utcnow()

        # Update the associated Employee's bench status and current project
        employee = await self.session.get(Employee, allocation.employee_id)
        if employee:
            employee.bench_status = BenchStatusEnum.on_project
            employee.bench_start_date = None

            project_name = None
            if allocation.demand_id:
                demand = await self.session.get(ProjectDemand, allocation.demand_id)
                if demand:
                    if demand.project_id:
                        proj = await self.session.get(Project, demand.project_id)
                        if proj:
                            project_name = proj.name
                    if not project_name and demand.role:
                        project_name = f"Project ({demand.role})"
                    
                    if demand.headcount_needed and demand.headcount_needed > 0:
                        demand.headcount_needed -= 1
                    if demand.headcount_needed <= 0:
                        demand.status = DemandStatusEnum.filled

            if not project_name:
                project_name = allocation.role_on_project or "Assigned Project"

            employee.current_project = project_name

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

        # Re-fetch with joinedload for Pydantic serialization
        stmt = (
            select(Allocation)
            .options(
                joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                joinedload(Allocation.employee),
            )
            .where(Allocation.id == allocation_id)
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

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

        stmt = (
            select(Allocation)
            .options(
                joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                joinedload(Allocation.employee),
            )
            .where(Allocation.id == allocation_id)
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def get_pending_allocations(self) -> List[Allocation]:
        """Fetch all allocations awaiting manager review."""
        stmt = (
            select(Allocation)
            .options(
                joinedload(Allocation.project_demand).joinedload(ProjectDemand.project),
                joinedload(Allocation.employee),
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
        return list(result.scalars().all())
