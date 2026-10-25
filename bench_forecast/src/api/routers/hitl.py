import uuid
import logging
from datetime import date
from decimal import Decimal
from typing import List, Any, Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.database import get_db
from src.api.deps import (
    assert_can_manage_employee,
    get_current_user,
    is_admin,
    manager_employee_id,
    require_roles,
)
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    ProjectDemand,
    TrainingAssignment,
    TrainingStatusEnum,
)
from src.schemas.hitl import (
    AllocationPendingResponse,
    ApproveRequest,
    RejectRequest,
    HiringRecommendationResponse,
    DismissHiringRequest,
)
from src.schemas.workforce import AllocationUpdate
from src.services.hitl_service import HITLService
from src.services.notifications import NotificationService, schedule_notification
from src.services.workforce_service import (
    AllocationService,
    CapacityExceededError,
)

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
async def approve_allocation(
    allocation_id: uuid.UUID,
    payload: ApproveRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    hitl_service = HITLService(db)
    try:
        allocation = await hitl_service.approve_allocation(
            allocation_id=allocation_id,
            reviewer_id=payload.reviewer_id,
            time_to_decision_seconds=getattr(payload, 'time_to_decision_seconds', None)
        )
    except CapacityExceededError as ce:
        raise HTTPException(status_code=409, detail=ce.as_detail())
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    notifications = []
    try:
        notifier = NotificationService(db)
        notifications.append(
            await notifier.notify_employee_allocated(
                allocation, approved=True
            )
        )
        if allocation.ai_upskilling_path and allocation.demand_id:
            training = (
                await db.execute(
                    select(TrainingAssignment)
                    .options(selectinload(TrainingAssignment.employee))
                    .where(
                        TrainingAssignment.employee_id == allocation.employee_id,
                        TrainingAssignment.demand_id == allocation.demand_id,
                        TrainingAssignment.status.in_([
                            TrainingStatusEnum.assigned,
                            TrainingStatusEnum.in_progress,
                        ]),
                    )
                    .order_by(TrainingAssignment.assigned_at.desc())
                )
            ).scalars().first()
            if training is not None:
                notifications.append(
                    await notifier.notify_training_assigned(training)
                )
        await db.commit()
    except Exception:
        logger.exception("Could not queue HITL approval notifications")
        await db.rollback()

    for notification in notifications:
        if notification.status == "queued":
            schedule_notification(notification.id, background_tasks)
    return allocation

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
    allocated_hours_per_day: Optional[Decimal] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    ai_justification: Optional[str] = None

class DirectReleaseRequest(BaseModel):
    employee_id: uuid.UUID


async def _load_allocation(db: AsyncSession, allocation_id: uuid.UUID) -> Allocation:
    result = await db.execute(
        select(Allocation)
        .options(
            selectinload(Allocation.project_demand).selectinload(
                ProjectDemand.project
            ),
            selectinload(Allocation.employee),
        )
        .where(Allocation.id == allocation_id)
    )
    allocation = result.scalars().first()
    if allocation is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    return allocation


def _serialize_allocation(allocation: Allocation) -> dict:
    demand = allocation.__dict__.get("project_demand")
    project = demand.__dict__.get("project") if demand else None
    return {
        "id": str(allocation.id),
        "employee_id": str(allocation.employee_id),
        "demand_id": str(allocation.demand_id) if allocation.demand_id else None,
        "project_id": str(project.id) if project else None,
        "project_name": project.name if project else None,
        "role_on_project": allocation.role_on_project,
        "allocated_hours_per_day": float(allocation.allocated_hours_per_day or 0),
        "allocation_pct": float(allocation.allocation_pct or 0),
        "status": (
            allocation.status.value
            if hasattr(allocation.status, "value")
            else str(allocation.status)
        ),
        "start_date": allocation.start_date,
        "end_date": allocation.end_date,
        "ai_justification": allocation.ai_justification,
        "ai_match_score": (
            float(allocation.ai_match_score)
            if allocation.ai_match_score is not None
            else None
        ),
    }


@allocations_router.get("", status_code=status.HTTP_200_OK)
async def list_allocations_endpoint(
    employee_id: Optional[uuid.UUID] = Query(default=None),
    project_id: Optional[uuid.UUID] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    if not is_admin(user):
        own = manager_employee_id(user)
        if employee_id is None:
            if user.get("role") == "manager":
                # Managers see their reports' allocations.
                from src.database.models import Employee

                report_ids = (
                    await db.execute(
                        select(Employee.id).where(
                            Employee.manager_id == uuid.UUID(own)
                        )
                    )
                ).scalars().all()
                if not report_ids:
                    return []
                from src.services.workforce_service import list_allocations

                rows = []
                for report_id in report_ids:
                    rows.extend(
                        await list_allocations(
                            db,
                            employee_id=report_id,
                            project_id=project_id,
                            statuses=None,
                        )
                    )
                return [_serialize_allocation(row) for row in rows]
            if own:
                employee_id = uuid.UUID(own)
            else:
                return []
        else:
            await assert_can_manage_employee(db, user, employee_id)

    from src.services.workforce_service import list_allocations

    rows = await list_allocations(
        db, employee_id=employee_id, project_id=project_id
    )
    return [_serialize_allocation(row) for row in rows]


@allocations_router.post("/assign", status_code=status.HTTP_200_OK)
async def direct_assign_employee(
    payload: DirectAssignRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    """Manual assignment. Managers may only assign their direct reports."""
    await assert_can_manage_employee(db, user, payload.employee_id)
    service = AllocationService(db)
    try:
        allocation = await service.create_allocation(
            employee_id=payload.employee_id,
            demand_id=payload.demand_id,
            allocated_hours_per_day=payload.allocated_hours_per_day,
            role_on_project=payload.role_on_project,
            start_date=payload.start_date,
            end_date=payload.end_date,
            justification=(
                payload.ai_justification
                or "Direct manager assignment from Bench & Pipeline interface."
            ),
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
    allocation = await _load_allocation(db, allocation.id)
    return _serialize_allocation(allocation)


@allocations_router.patch("/{allocation_id}", status_code=status.HTTP_200_OK)
async def update_allocation_endpoint(
    allocation_id: uuid.UUID,
    payload: AllocationUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    allocation = await db.get(Allocation, allocation_id)
    if allocation is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    await assert_can_manage_employee(db, user, allocation.employee_id)

    service = AllocationService(db)
    try:
        updated = await service.update_allocation(
            allocation,
            allocated_hours_per_day=payload.allocated_hours_per_day,
            start_date=payload.start_date,
            end_date=payload.end_date,
            status=(
                AllocationStatusEnum(payload.status) if payload.status else None
            ),
            manager_notes=payload.manager_notes,
        )
    except CapacityExceededError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await db.commit()
    updated = await _load_allocation(db, updated.id)
    return _serialize_allocation(updated)


@allocations_router.post("/{allocation_id}/release", status_code=status.HTTP_200_OK)
async def release_allocation_endpoint(
    allocation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    allocation = await db.get(Allocation, allocation_id)
    if allocation is None:
        raise HTTPException(status_code=404, detail="Allocation not found")
    await assert_can_manage_employee(db, user, allocation.employee_id)
    service = AllocationService(db)
    released = await service.release_allocation(allocation)
    await db.commit()
    released = await _load_allocation(db, released.id)
    return _serialize_allocation(released)


@allocations_router.post("/release", status_code=status.HTTP_200_OK)
async def direct_release_employee(
    payload: DirectReleaseRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    await assert_can_manage_employee(db, user, payload.employee_id)
    service = AllocationService(db)
    alloc_stmt = select(Allocation).where(
        Allocation.employee_id == payload.employee_id,
        Allocation.status.in_([
            AllocationStatusEnum.active,
            AllocationStatusEnum.approved_by_manager,
        ]),
    )
    rows = (await db.execute(alloc_stmt)).scalars().all()
    for allocation in rows:
        await service.release_allocation(allocation)
    await db.commit()
    return {
        "status": "released",
        "employee_id": str(payload.employee_id),
        "released_count": len(rows),
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
