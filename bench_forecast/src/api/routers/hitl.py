import uuid
import logging
from typing import List, Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from langgraph.types import Command

from src.database.database import get_db
from src.api.deps import get_current_user
from src.schemas.hitl import AllocationPendingResponse, ApproveRequest, RejectRequest
from src.services.hitl_service import HITLService
from src.agents.graph import build_forecast_graph

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["HITL Review"],
)

class ReviewForecastRequest(BaseModel):
    human_approved: bool = Field(..., description="True to approve execution, False to reject")
    rejection_feedback: Optional[str] = Field(None, description="Guidance for revision if rejected")

@router.post("/forecast/{run_id}/review", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def review_forecast(
    run_id: str,
    payload: ReviewForecastRequest,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Accepts manager input (approve/reject).
    Reloads the graph checkpoint using thread_id (run_id), injects decision via Command(resume=...),
    and triggers either execution or revision planning.
    """
    logger.info(f"Manager {current_user['username']} reviewing forecast {run_id}. Approved: {payload.human_approved}")
    
    # We must use thread_id = run_id to retrieve the checkpointed state
    config = {"configurable": {"thread_id": run_id}}
    graph = build_forecast_graph()
    
    # Inject the decision via LangGraph's Command(resume=...)
    resume_payload: Dict[str, Any] = {
        "human_approved": payload.human_approved,
        "human_feedback": current_user["username"],
    }
    if not payload.human_approved and payload.rejection_feedback:
        resume_payload["rejection_feedback"] = payload.rejection_feedback
        
    try:
        # Pass Command(resume=...) directly to ainvoke
        logger.info(f"Resuming graph for run {run_id} via Command(resume=...)")
        result_state = await graph.ainvoke(Command(resume=resume_payload), config=config)
        
        execution_status = result_state.get("execution_status")
        decisions = result_state.get("decisions", [])
        
        if payload.human_approved:
            return {
                "status": "completed",
                "approved": True,
                "execution_status": execution_status,
                "decisions_processed": len(decisions)
            }
        elif payload.rejection_feedback and result_state.get("revision_count", 0) > 0:
            return {
                "status": "revised_for_review",
                "revision_count": result_state.get("revision_count"),
                "revision_feedback_applied": payload.rejection_feedback,
                "decisions": decisions
            }
        else:
            return {
                "status": "rejected_terminated",
                "approved": False,
                "execution_status": "skipped_no_approval",
                "decisions": decisions
            }
            
    except Exception as e:
        logger.error(f"Failed to resume graph for run {run_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute review decision: {str(e)}"
        )

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
