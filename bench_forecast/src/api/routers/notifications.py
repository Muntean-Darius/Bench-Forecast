"""Email outbox audit + capacity/roll-off scan APIs."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import require_roles
from src.database.database import get_db
from src.database.models import EmailNotification
from src.services.notifications import (
    CapacityScanService,
    deliver_notification,
    schedule_notification,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


def _serialize(row: EmailNotification) -> dict:
    return {
        "id": str(row.id),
        "recipient_email": row.recipient_email,
        "recipient_employee_id": (
            str(row.recipient_employee_id)
            if row.recipient_employee_id
            else None
        ),
        "event_type": row.event_type,
        "subject": row.subject,
        "status": row.status,
        "provider": row.provider,
        "error": row.error,
        "attempts": row.attempts,
        "created_at": row.created_at,
        "sent_at": row.sent_at,
        "payload": row.payload or {},
    }


@router.post("/capacity-scan", status_code=status.HTTP_200_OK)
async def capacity_scan(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    """Scan for fully-utilised employees and 14-day roll-offs.

    Each notification is deduplicated per ``(employee_id, event_type, date)``
    by the ``dedupe_key`` stored in the outbox payload.
    """
    service = CapacityScanService(db)
    result = await service.scan()
    notifications = result.pop("notifications")
    await db.commit()

    queued = 0
    skipped = 0
    for notification in notifications:
        if notification.status == "queued":
            schedule_notification(notification.id, background_tasks)
            queued += 1
        else:
            skipped += 1

    return {
        **result,
        "queued": queued,
        "skipped": skipped,
    }


@router.get("", status_code=status.HTTP_200_OK)
async def list_notifications(
    notification_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    stmt = select(EmailNotification).order_by(
        EmailNotification.created_at.desc()
    )
    if notification_status:
        stmt = stmt.where(EmailNotification.status == notification_status)
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(row) for row in rows]


@router.post("/{notification_id}/retry", status_code=status.HTTP_200_OK)
async def retry_notification(
    notification_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    row = await db.get(EmailNotification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.status not in ("failed", "queued", "skipped"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry a '{row.status}' notification",
        )
    row.status = "queued"
    row.error = None
    await db.commit()
    schedule_notification(row.id, background_tasks)
    return {"id": str(row.id), "status": "queued"}
