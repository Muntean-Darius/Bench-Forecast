"""Asynchronous email notifications backed by a transactional outbox.

Design:
  * Business endpoints write an ``email_notifications`` row in the same
    transaction as the allocation / CV / training change (the outbox).
  * Delivery is triggered *after* commit via FastAPI BackgroundTasks (routers)
    or ``asyncio.create_task`` (services that commit internally).
  * ``deliver_notification`` uses its own database session, catches every
    delivery error, and records ``status='failed'`` + ``error`` without ever
    raising into the business transaction.
  * Recipient resolution checks ``users.email`` first, then
    ``employees.email``. A missing/invalid address becomes ``status='skipped'``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
import uuid
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import Config
from src.database.database import AsyncSessionLocal
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    EmailNotification,
    Employee,
    Project,
    ProjectDemand,
    TrainingAssignment,
    User,
)
from src.services.workforce_service import summarize_allocations

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ROLLOFF_DAYS = 14


def _valid_email(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = str(value).strip()
    return candidate if _EMAIL_RE.match(candidate) else None


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _fmt_date(value) -> str:
    return value.isoformat() if value else "—"


def _employee_name(employee: Optional[Employee]) -> str:
    if employee is None:
        return "Employee"
    return f"{employee.first_name} {employee.last_name}".strip() or "Employee"


# =============================================================================
# Sender abstraction
# =============================================================================

class EmailSender(ABC):
    provider: str = "base"

    @abstractmethod
    async def send(self, *, to: str, subject: str, body: str) -> None:
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    provider = "console"

    async def send(self, *, to: str, subject: str, body: str) -> None:
        logger.info(
            "\n=== EMAIL (console) ===\nTo: %s\nSubject: %s\n%s\n=== END EMAIL ===",
            to,
            subject,
            body,
        )


class SMTPEmailSender(EmailSender):
    provider = "smtp"

    def __init__(self) -> None:
        if not Config.SMTP_HOST:
            raise ValueError("SMTP_HOST is required when EMAIL_PROVIDER=smtp")

    def _send_sync(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = Config.EMAIL_FROM
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        context = ssl.create_default_context()
        if Config.SMTP_USE_SSL:
            client = smtplib.SMTP_SSL(
                Config.SMTP_HOST,
                Config.SMTP_PORT,
                timeout=Config.SMTP_TIMEOUT,
                context=context,
            )
        else:
            client = smtplib.SMTP(
                Config.SMTP_HOST,
                Config.SMTP_PORT,
                timeout=Config.SMTP_TIMEOUT,
            )

        with client as smtp:
            if Config.SMTP_USE_TLS and not Config.SMTP_USE_SSL:
                smtp.starttls(context=context)
            if Config.SMTP_USER:
                smtp.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            smtp.send_message(message)

    async def send(self, *, to: str, subject: str, body: str) -> None:
        # smtplib is synchronous; keep it off the FastAPI event loop.
        await asyncio.to_thread(
            self._send_sync, to=to, subject=subject, body=body
        )


class SendGridEmailSender(EmailSender):
    provider = "sendgrid"

    def __init__(self) -> None:
        if not Config.SENDGRID_API_KEY:
            raise ValueError(
                "SENDGRID_API_KEY is required when EMAIL_PROVIDER=sendgrid"
            )

    async def send(self, *, to: str, subject: str, body: str) -> None:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": Config.EMAIL_FROM},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        headers = {
            "Authorization": f"Bearer {Config.SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            import httpx  # noqa: PLC0415

            async with httpx.AsyncClient(timeout=Config.SMTP_TIMEOUT) as client:
                response = await client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                return
        except ModuleNotFoundError:
            # Fallback for environments without httpx: do not block the loop.
            import requests  # noqa: PLC0415

            def _post() -> None:
                response = requests.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers=headers,
                    timeout=Config.SMTP_TIMEOUT,
                )
                response.raise_for_status()

            await asyncio.to_thread(_post)


def get_email_sender() -> EmailSender:
    provider = (Config.EMAIL_PROVIDER or "console").strip().lower()
    if provider == "smtp":
        return SMTPEmailSender()
    if provider == "sendgrid":
        return SendGridEmailSender()
    return ConsoleEmailSender()


# =============================================================================
# Outbox service
# =============================================================================

class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve_employee_email(
        self, employee: Optional[Employee]
    ) -> Optional[str]:
        if employee is None:
            return None
        user = (
            await self.session.execute(
                select(User).where(User.employee_id == employee.id)
            )
        ).scalars().first()
        return _valid_email(getattr(user, "email", None)) or _valid_email(
            getattr(employee, "email", None)
        )

    async def queue(
        self,
        *,
        event_type: str,
        recipient_email: Optional[str],
        subject: str,
        body_text: str,
        recipient_employee_id: Optional[uuid.UUID] = None,
        payload: Optional[dict[str, Any]] = None,
        skip_reason: Optional[str] = None,
    ) -> EmailNotification:
        valid_email = _valid_email(recipient_email)
        status = "skipped" if valid_email is None else "queued"
        error = None
        if valid_email is None:
            error = skip_reason or "No valid recipient email found"

        notification = EmailNotification(
            recipient_email=valid_email or "",
            recipient_employee_id=recipient_employee_id,
            event_type=event_type,
            subject=subject,
            body_text=body_text,
            payload=payload or {},
            status=status,
            error=error,
            attempts=0,
            created_at=_utcnow(),
        )
        self.session.add(notification)
        await self.session.flush()
        if status == "skipped":
            logger.warning(
                "Notification skipped (%s): %s", event_type, error
            )
        return notification

    # ------------------------------------------------------------------
    # Business event templates
    # ------------------------------------------------------------------

    async def notify_employee_allocated(
        self,
        allocation: Allocation,
        *,
        employee: Optional[Employee] = None,
        demand: Optional[ProjectDemand] = None,
        project: Optional[Project] = None,
        approved: bool = False,
    ) -> EmailNotification:
        emp = (
            employee
            or allocation.__dict__.get("employee")
            or await self.session.get(Employee, allocation.employee_id)
        )
        demand = (
            demand
            or allocation.__dict__.get("project_demand")
            or (
                await self.session.get(ProjectDemand, allocation.demand_id)
                if allocation.demand_id
                else None
            )
        )
        project = project or (
            demand.__dict__.get("project") if demand is not None else None
        )
        if project is None and demand is not None and demand.project_id:
            project = await self.session.get(Project, demand.project_id)

        project_name = (
            project.name
            if project is not None
            else (allocation.role_on_project or "Assigned project")
        )
        role = allocation.role_on_project or (
            demand.role if demand is not None else "Engineer"
        )
        hours = float(
            allocation.allocated_hours_per_day
            or (demand.hours_per_day if demand is not None else 8)
            or 8
        )
        recipient = await self.resolve_employee_email(emp)
        subject = (
            f"Allocation approved: {project_name}"
            if approved
            else f"New project allocation: {project_name}"
        )
        body = "\n".join([
            f"Hello {_employee_name(emp)},",
            "",
            (
                "Your allocation has been approved."
                if approved
                else "You have been allocated to a new project."
            ),
            "",
            f"Project: {project_name}",
            f"Role: {role}",
            f"Daily hours: {hours:g}h",
            f"Start date: {_fmt_date(allocation.start_date)}",
            f"End date: {_fmt_date(allocation.end_date)}",
            "",
            "If these details look wrong, contact your people manager.",
            "",
            "— Bench Forecast",
        ])
        return await self.queue(
            event_type="employee_allocated",
            recipient_email=recipient,
            recipient_employee_id=emp.id if emp else None,
            subject=subject,
            body_text=body,
            payload={
                "allocation_id": str(allocation.id),
                "employee_id": str(allocation.employee_id),
                "project_id": str(project.id) if project else None,
                "project_name": project_name,
                "role": role,
                "hours_per_day": hours,
                "start_date": _fmt_date(allocation.start_date),
                "end_date": _fmt_date(allocation.end_date),
                "approved": approved,
            },
        )

    async def notify_training_assigned(
        self,
        training: TrainingAssignment,
        *,
        employee: Optional[Employee] = None,
    ) -> EmailNotification:
        emp = (
            employee
            or training.__dict__.get("employee")
            or await self.session.get(Employee, training.employee_id)
        )
        recipient = await self.resolve_employee_email(emp)
        skills = ", ".join(training.targeted_skills or []) or "the target skills"
        body = "\n".join([
            f"Hello {_employee_name(emp)},",
            "",
            "A new training / upskilling path has been assigned to you.",
            "",
            f"Path: {training.title}",
            f"Targeted skills: {skills}",
            f"Target completion: {_fmt_date(training.target_completion_date)}",
            "",
            training.ai_upskilling_path or "Open your employee portal for details.",
            "",
            "— Bench Forecast",
        ])
        return await self.queue(
            event_type="training_assigned",
            recipient_email=recipient,
            recipient_employee_id=emp.id if emp else None,
            subject=f"Training assigned: {training.title}",
            body_text=body,
            payload={
                "training_id": str(training.id),
                "employee_id": str(training.employee_id),
                "title": training.title,
                "targeted_skills": training.targeted_skills or [],
                "target_completion_date": _fmt_date(
                    training.target_completion_date
                ),
            },
        )

    async def notify_cv_approval_needed(
        self,
        employee: Employee,
        document,
        review,
    ) -> EmailNotification:
        manager = (
            employee.__dict__.get("manager")
            or (
                await self.session.get(Employee, employee.manager_id)
                if employee.manager_id
                else None
            )
        )
        recipient = await self.resolve_employee_email(manager)
        body = "\n".join([
            f"Hello {_employee_name(manager)},",
            "",
            (
                f"{_employee_name(employee)} uploaded a new CV that needs "
                "your approval."
            ),
            "",
            f"Document: {document.file_name} (v{document.version})",
            f"Proposed skills: {', '.join(review.proposed_skills or []) or 'none extracted'}",
            "",
            "Open the CV Approvals queue to review the diff and approve or reject.",
            "",
            "— Bench Forecast",
        ])
        return await self.queue(
            event_type="cv_approval_needed",
            recipient_email=recipient,
            recipient_employee_id=manager.id if manager else None,
            subject=(
                f"CV approval needed: {_employee_name(employee)}"
            ),
            body_text=body,
            payload={
                "review_id": str(review.id),
                "document_id": str(document.id),
                "employee_id": str(employee.id),
                "employee_name": _employee_name(employee),
                "manager_id": str(manager.id) if manager else None,
                "version": document.version,
            },
            skip_reason=(
                "Employee has no manager assigned"
                if manager is None
                else "Manager has no valid email address"
            ),
        )

    async def notify_cv_decision(
        self,
        employee: Employee,
        review,
        *,
        approved: bool,
        comment: Optional[str] = None,
    ) -> EmailNotification:
        recipient = await self.resolve_employee_email(employee)
        decision = "approved" if approved else "rejected"
        subject = f"Your CV was {decision}"
        body = "\n".join([
            f"Hello {_employee_name(employee)},",
            "",
            f"Your uploaded CV (review {review.id}) was {decision} by your manager.",
            "",
            f"Manager comment: {comment or 'No comment provided.'}",
            "",
            (
                "Your active talent profile and embeddings have been updated."
                if approved
                else "Your active profile has not changed. You can update the CV and resubmit."
            ),
            "",
            "— Bench Forecast",
        ])
        return await self.queue(
            event_type="cv_approved" if approved else "cv_rejected",
            recipient_email=recipient,
            recipient_employee_id=employee.id,
            subject=subject,
            body_text=body,
            payload={
                "review_id": str(review.id),
                "document_id": str(review.document_id),
                "employee_id": str(employee.id),
                "approved": approved,
                "comment": comment,
            },
        )

    async def notify_capacity_warning(
        self,
        manager: Employee,
        employee: Employee,
        reasons: list[str],
        dedupe_key: str,
    ) -> EmailNotification:
        recipient = await self.resolve_employee_email(manager)
        body = "\n".join([
            f"Hello {_employee_name(manager)},",
            "",
            f"Capacity warning for {_employee_name(employee)}:",
            "",
            *[f"- {reason}" for reason in reasons],
            "",
            "Open the Team workload view to review remaining hours and roll-off dates.",
            "",
            "— Bench Forecast",
        ])
        return await self.queue(
            event_type="capacity_warning",
            recipient_email=recipient,
            recipient_employee_id=manager.id,
            subject=f"Capacity warning: {_employee_name(employee)}",
            body_text=body,
            payload={
                "dedupe_key": dedupe_key,
                "employee_id": str(employee.id),
                "employee_name": _employee_name(employee),
                "manager_id": str(manager.id),
                "reasons": reasons,
                "date": dedupe_key.rsplit(":", 1)[-1],
            },
            skip_reason="Manager has no valid email address",
        )


# =============================================================================
# Capacity / roll-off scan
# =============================================================================

class CapacityScanService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.notifications = NotificationService(session)

    async def scan(
        self,
        *,
        today: Optional[date] = None,
        employee_ids: Optional[list[uuid.UUID]] = None,
    ) -> dict:
        today = today or date.today()
        horizon = today + timedelta(days=_ROLLOFF_DAYS)
        # Use a 48-hour lookback rather than UTC midnight: the server may be in
        # a non-UTC timezone, so local date.today() can be ahead of UTC.
        lookback_start = _utcnow() - timedelta(days=2)

        stmt = (
            select(Employee)
            .options(
                selectinload(Employee.allocations),
                selectinload(Employee.manager),
            )
            .where(Employee.is_active.is_(True))
        )
        if employee_ids is not None:
            stmt = stmt.where(Employee.id.in_(employee_ids))
        employees = (await self.session.execute(stmt)).scalars().all()

        existing = (
            await self.session.execute(
                select(EmailNotification).where(
                    EmailNotification.event_type == "capacity_warning",
                    EmailNotification.created_at >= lookback_start,
                )
            )
        ).scalars().all()
        seen = {
            (row.payload or {}).get("dedupe_key")
            for row in existing
            if row.payload
        }

        queued: list[EmailNotification] = []
        deduped = 0
        no_manager = 0

        for employee in employees:
            if not employee.manager_id:
                continue
            summary = summarize_allocations(
                employee, employee.allocations, on_date=today
            )
            committed = summary["committed_hours_per_day"]
            reserved = summary["reserved_hours_per_day"]
            remaining = summary["remaining_available_hours"]

            reasons: list[str] = []
            if (
                remaining <= 0
                and (committed + reserved) > 0
            ):
                reasons.append(
                    f"Fully allocated: {float(committed + reserved):g}h/day "
                    f"against a {float(summary['daily_capacity_hours']):g}h/day capacity."
                )

            rolloffs = [
                allocation
                for allocation in employee.allocations
                if allocation.status
                in (
                    AllocationStatusEnum.active,
                    AllocationStatusEnum.approved_by_manager,
                )
                and allocation.end_date is not None
                and today <= allocation.end_date <= horizon
            ]
            if rolloffs:
                for allocation in rolloffs:
                    reasons.append(
                        f"Roll-off on {allocation.end_date.isoformat()} from "
                        f"{allocation.role_on_project}."
                    )

            if not reasons:
                continue

            key = f"{employee.id}:capacity_warning:{today.isoformat()}"
            if key in seen:
                deduped += 1
                continue

            manager = employee.manager or (
                await self.session.get(Employee, employee.manager_id)
            )
            if manager is None:
                no_manager += 1
                continue

            notification = await self.notifications.notify_capacity_warning(
                manager, employee, reasons, key
            )
            queued.append(notification)
            seen.add(key)

        await self.session.flush()
        return {
            "date": today.isoformat(),
            "warnings": len(queued),
            "deduped": deduped,
            "no_manager": no_manager,
            "notifications": queued,
        }


# =============================================================================
# Delivery worker (own session, never raises)
# =============================================================================

async def deliver_notification(notification_id: uuid.UUID | str) -> None:
    try:
        notification_uuid = uuid.UUID(str(notification_id))
    except (ValueError, TypeError):
        logger.error("Invalid notification id: %r", notification_id)
        return

    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(EmailNotification, notification_uuid)
            if row is None or row.status != "queued":
                return
            recipient = row.recipient_email
            subject = row.subject
            body = row.body_text
            provider = (Config.EMAIL_PROVIDER or "console").lower()
            if not Config.EMAILS_ENABLED:
                row.status = "skipped"
                row.error = "Email delivery disabled (EMAILS_ENABLED=false)"
                await db.commit()
                return
            row.attempts = int(row.attempts or 0) + 1
            row.provider = provider
            await db.commit()

        try:
            sender = get_email_sender()
            await sender.send(to=recipient, subject=subject, body=body)
        except Exception as exc:  # delivery must never escape
            logger.exception(
                "Notification delivery failed: id=%s provider=%s",
                notification_uuid,
                provider,
            )
            async with AsyncSessionLocal() as db:
                row = await db.get(EmailNotification, notification_uuid)
                if row is not None:
                    row.status = "failed"
                    row.error = str(exc)[:4000]
                    row.provider = provider
                    await db.commit()
            return

        async with AsyncSessionLocal() as db:
            row = await db.get(EmailNotification, notification_uuid)
            if row is not None:
                row.status = "sent"
                row.error = None
                row.sent_at = _utcnow()
                row.provider = provider
                await db.commit()
        logger.info("Notification delivered: id=%s to=%s", notification_uuid, recipient)
    except Exception:
        # Last-resort guard: never let a background delivery failure bubble up.
        logger.exception("Unhandled notification delivery error: id=%s", notification_id)


def schedule_notification(
    notification_id: uuid.UUID | str,
    background_tasks=None,
) -> None:
    """Schedule delivery after the business transaction has committed."""
    if background_tasks is not None:
        background_tasks.add_task(deliver_notification, notification_id)
    else:
        asyncio.create_task(deliver_notification(notification_id))
