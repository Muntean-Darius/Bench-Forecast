"""Manager CV approval queue, diff view and CV status endpoints."""

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
from src.core.observability import workflow
from src.database.database import get_db
from src.database.models import CvApprovalStatusEnum, CvReviewRequest, Employee
from src.schemas.cv import CvApproveRequest, CvRejectRequest
from src.services.cv_review_service import CvReviewService
from src.services.notifications import NotificationService, schedule_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["CV Review"])


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _employee_name(employee) -> str:
    if employee is None:
        return ""
    return f"{employee.first_name} {employee.last_name}".strip()


def _serialize_review(review: CvReviewRequest) -> dict:
    return {
        "review_id": review.id,
        "document_id": review.document_id,
        "employee_id": review.employee_id,
        "employee_name": _employee_name(review.employee),
        "manager_id": review.manager_id,
        "manager_name": (
            _employee_name(review.manager) if review.manager else None
        ),
        "status": _status_value(review.status),
        "proposed_skills": review.proposed_skills or [],
        "requested_at": review.requested_at,
        "reviewed_at": review.reviewed_at,
        "manager_comment": review.manager_comment,
        "document": (
            {
                "file_name": review.document.file_name,
                "version": review.document.version,
                "download_url": (
                    f"/api/v1/documents/employee/{review.employee_id}/download"
                ),
            }
            if review.document
            else None
        ),
    }


def _can_access_review(user: dict, review: CvReviewRequest) -> bool:
    if is_admin(user):
        return True
    own = manager_employee_id(user)
    if not own:
        return False
    if user.get("role") == "manager":
        return str(review.manager_id or "") == own or str(review.employee_id) == own
    return str(review.employee_id) == own


def _can_decide_review(user: dict, review: CvReviewRequest) -> bool:
    if is_admin(user):
        return True
    if user.get("role") != "manager":
        return False
    own = manager_employee_id(user)
    if not own:
        return False
    if str(review.manager_id or "") == own:
        return True
    return bool(review.employee and str(review.employee.manager_id or "") == own)


@router.get("/cv-reviews", status_code=status.HTTP_200_OK)
async def list_cv_reviews(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = CvReviewService(db)
    status_enum = None
    if status_filter:
        try:
            status_enum = CvApprovalStatusEnum(status_filter)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid review status")

    if is_admin(user):
        reviews = await service.list_reviews(status=status_enum)
    elif user.get("role") == "manager":
        manager_id = manager_employee_id(user)
        if not manager_id:
            raise HTTPException(status_code=403, detail="Manager is not linked to an employee")
        reviews = await service.list_reviews(
            manager_id=uuid.UUID(manager_id), status=status_enum
        )
    else:
        employee_id = manager_employee_id(user)
        if not employee_id:
            raise HTTPException(status_code=403, detail="Employee account has no record")
        reviews = await service.list_reviews(
            employee_id=uuid.UUID(employee_id), status=status_enum
        )
    return [_serialize_review(review) for review in reviews]


@router.get("/cv-reviews/pending", status_code=status.HTTP_200_OK)
async def pending_cv_reviews(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return await list_cv_reviews(
        status_filter=CvApprovalStatusEnum.pending_approval.value,
        db=db,
        user=user,
    )


@router.get("/cv-reviews/{review_id}", status_code=status.HTTP_200_OK)
async def cv_review_detail(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    service = CvReviewService(db)
    review = await service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="CV review not found")
    if not _can_access_review(user, review):
        raise HTTPException(status_code=403, detail="Not allowed to view this CV review")
    diff = await service.build_diff(review)
    diff["document"]["download_url"] = (
        f"/api/v1/documents/employee/{review.employee_id}/download"
    )
    return diff


@router.post("/cv-reviews/{review_id}/approve", status_code=status.HTTP_200_OK)
@workflow(name="cv-review-approval", version="v2", context={"entrypoint": "cv-approval"})
async def approve_cv_review(
    review_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    payload: Optional[CvApproveRequest] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    service = CvReviewService(db)
    review = await service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="CV review not found")
    if not _can_decide_review(user, review):
        raise HTTPException(status_code=403, detail="Not allowed to approve this CV")
    try:
        approved = await service.approve(
            review,
            reviewer_id=uuid.UUID(user["id"]),
            comment=payload.comment if payload else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.exception("CV approval failed")
        raise HTTPException(
            status_code=502,
            detail=f"CV approval failed while updating the vector store: {exc}",
        )

    notification = None
    try:
        employee = approved.__dict__.get("employee") or await db.get(
            Employee, approved.employee_id
        )
        notification = await NotificationService(db).notify_cv_decision(
            employee,
            approved,
            approved=True,
            comment=payload.comment if payload else None,
        )
        await db.commit()
    except Exception:
        logger.exception("Could not queue cv_approved notification")
        await db.rollback()
    if notification is not None and notification.status == "queued":
        schedule_notification(notification.id, background_tasks)
    return _serialize_review(approved)


@router.post("/cv-reviews/{review_id}/reject", status_code=status.HTTP_200_OK)
async def reject_cv_review(
    review_id: uuid.UUID,
    payload: CvRejectRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin", "manager")),
):
    service = CvReviewService(db)
    review = await service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="CV review not found")
    if not _can_decide_review(user, review):
        raise HTTPException(status_code=403, detail="Not allowed to reject this CV")
    try:
        rejected = await service.reject(
            review,
            reviewer_id=uuid.UUID(user["id"]),
            comment=payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    notification = None
    try:
        employee = rejected.__dict__.get("employee") or await db.get(
            Employee, rejected.employee_id
        )
        notification = await NotificationService(db).notify_cv_decision(
            employee,
            rejected,
            approved=False,
            comment=payload.comment,
        )
        await db.commit()
    except Exception:
        logger.exception("Could not queue cv_rejected notification")
        await db.rollback()
    if notification is not None and notification.status == "queued":
        schedule_notification(notification.id, background_tasks)
    return _serialize_review(rejected)


@router.get("/employees/{employee_id}/cv-status", status_code=status.HTTP_200_OK)
async def employee_cv_status(
    employee_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    await assert_can_manage_employee(db, user, employee_id)
    service = CvReviewService(db)
    return await service.cv_status(employee_id)
