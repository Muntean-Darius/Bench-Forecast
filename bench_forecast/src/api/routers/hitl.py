import uuid
import logging
from typing import List, Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.database import get_db
from src.api.deps import get_current_user
from src.schemas.hitl import (
    AllocationPendingResponse,
    ApproveRequest,
    RejectRequest,
    HiringRecommendationResponse,
    DismissHiringRequest,
)
from src.services.hitl_service import HITLService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["HITL Review"],
)

# HITL is handled by the allocation state machine (approve / reject / assign /
# release below), not by a graph interrupt. See src/agents/graph.py for the
# rationale.

# Standard /api/v1/allocations endpoints
allocations_router = APIRouter(
    prefix="/allocations",
    tags=["HITL Allocations"],
)

@allocations_router.get("/pending", response_model=List[AllocationPendingResponse])
async def get_pending_allocations(db: AsyncSession = Depends(get_db)):
    """Fetch all allocations awaiting manager review with demand details and AI justifications."""
    hitl_service = HITLService(db)
    try:
        return await hitl_service.get_pending_allocations()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@allocations_router.post("/{allocation_id}/approve", response_model=AllocationPendingResponse)
async def approve_allocation(allocation_id: uuid.UUID, payload: ApproveRequest, db: AsyncSession = Depends(get_db)):
    hitl_service = HITLService(db)
    try:
        return await hitl_service.approve_allocation(
            allocation_id=allocation_id,
            reviewer_id=payload.reviewer_id,
            time_to_decision_seconds=getattr(payload, 'time_to_decision_seconds', None)
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@allocations_router.post("/{allocation_id}/reject", response_model=AllocationPendingResponse)
async def reject_allocation(allocation_id: uuid.UUID, payload: RejectRequest, db: AsyncSession = Depends(get_db)):
    hitl_service = HITLService(db)
    try:
        return await hitl_service.reject_allocation(
            allocation_id=allocation_id,
            reviewer_id=payload.reviewer_id,
            feedback=payload.feedback,
            time_to_decision_seconds=getattr(payload, 'time_to_decision_seconds', None)
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

class DirectAssignRequest(BaseModel):
    employee_id: uuid.UUID
    demand_id: Optional[uuid.UUID] = None
    project_name: Optional[str] = None
    role_on_project: Optional[str] = None

class DirectReleaseRequest(BaseModel):
    employee_id: uuid.UUID

@allocations_router.post("/assign", status_code=status.HTTP_200_OK)
async def direct_assign_employee(payload: DirectAssignRequest, db: AsyncSession = Depends(get_db)):
    from src.database.models import Employee, ProjectDemand, Project, Allocation, BenchStatusEnum, AllocationStatusEnum, DemandStatusEnum
    from datetime import date
    from decimal import Decimal

    employee = await db.get(Employee, payload.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    project_name = payload.project_name
    role = payload.role_on_project

    if payload.demand_id:
        demand = await db.get(ProjectDemand, payload.demand_id)
        if demand:
            if not role:
                role = demand.role
            if not project_name and demand.project_id:
                proj = await db.get(Project, demand.project_id)
                if proj:
                    project_name = proj.name
            if demand.headcount_needed and demand.headcount_needed > 0:
                demand.headcount_needed -= 1
            if demand.headcount_needed <= 0:
                demand.status = DemandStatusEnum.filled

    if not project_name:
        project_name = "Assigned Project"

    employee.bench_status = BenchStatusEnum.on_project
    employee.current_project = project_name
    employee.bench_start_date = None

    allocation = Allocation(
        employee_id=payload.employee_id,
        demand_id=payload.demand_id,
        role_on_project=role or employee.job_title or "Engineer",
        allocation_pct=Decimal("100.00"),
        status=AllocationStatusEnum.active,
        ai_justification="Direct manager assignment from Bench & Pipeline interface.",
        start_date=date.today(),
    )
    db.add(allocation)
    await db.commit()

    return {
        "status": "assigned",
        "employee_id": str(employee.id),
        "employee_name": f"{employee.first_name} {employee.last_name}".strip(),
        "current_project": employee.current_project,
        "bench_status": employee.bench_status.value
    }

@allocations_router.post("/release", status_code=status.HTTP_200_OK)
async def direct_release_employee(payload: DirectReleaseRequest, db: AsyncSession = Depends(get_db)):
    from src.database.models import Employee, Allocation, BenchStatusEnum, AllocationStatusEnum
    from datetime import date
    from sqlalchemy import select

    employee = await db.get(Employee, payload.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    employee.bench_status = BenchStatusEnum.bench
    employee.current_project = None
    employee.bench_start_date = date.today()

    alloc_stmt = select(Allocation).where(
        Allocation.employee_id == payload.employee_id,
        Allocation.status.in_([AllocationStatusEnum.active, AllocationStatusEnum.approved_by_manager])
    )
    alloc_res = await db.execute(alloc_stmt)
    for a in alloc_res.scalars().all():
        a.status = AllocationStatusEnum.completed
        a.end_date = date.today()

    await db.commit()

    return {
        "status": "released",
        "employee_id": str(employee.id),
        "bench_status": employee.bench_status.value
    }

# Include both /allocations and /api/allocations for complete backward compatibility
router.include_router(allocations_router)

legacy_allocations_router = APIRouter(
    prefix="/api/allocations",
    tags=["Legacy HITL Allocations"],
)
legacy_allocations_router.add_api_route("/pending", get_pending_allocations, methods=["GET"], response_model=List[AllocationPendingResponse])
legacy_allocations_router.add_api_route("/{allocation_id}/approve", approve_allocation, methods=["POST"], response_model=AllocationPendingResponse)
legacy_allocations_router.add_api_route("/{allocation_id}/reject", reject_allocation, methods=["POST"], response_model=AllocationPendingResponse)
legacy_allocations_router.add_api_route("/assign", direct_assign_employee, methods=["POST"])
legacy_allocations_router.add_api_route("/release", direct_release_employee, methods=["POST"])
router.include_router(legacy_allocations_router)


# ---------------------------------------------------------------------------
# External-hire recommendations
# ---------------------------------------------------------------------------

hiring_router = APIRouter(
    prefix="/hiring",
    tags=["HITL Hiring"],
)


@hiring_router.get("/pending", response_model=List[HiringRecommendationResponse])
async def get_pending_hiring(db: AsyncSession = Depends(get_db)):
    """Fetch all open external-hire recommendations."""
    hitl_service = HITLService(db)
    try:
        return await hitl_service.get_pending_hiring()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@hiring_router.post("/{recommendation_id}/dismiss", response_model=HiringRecommendationResponse)
async def dismiss_hiring(
    recommendation_id: uuid.UUID,
    payload: Optional[DismissHiringRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Dismiss an external-hire recommendation."""
    hitl_service = HITLService(db)
    try:
        return await hitl_service.dismiss_hiring(recommendation_id)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


router.include_router(hiring_router)

legacy_hiring_router = APIRouter(
    prefix="/api/hiring",
    tags=["Legacy HITL Hiring"],
)
legacy_hiring_router.add_api_route("/pending", get_pending_hiring, methods=["GET"], response_model=List[HiringRecommendationResponse])
legacy_hiring_router.add_api_route("/{recommendation_id}/dismiss", dismiss_hiring, methods=["POST"], response_model=HiringRecommendationResponse)
router.include_router(legacy_hiring_router)
