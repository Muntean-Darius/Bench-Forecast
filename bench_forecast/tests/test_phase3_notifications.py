"""Phase 3 notification outbox, sender failure and capacity-scan tests."""

import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database.database import AsyncSessionLocal
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchStatusEnum,
    EmailNotification,
    Employee,
    EmploymentTypeEnum,
)
from src.services import notifications as notification_module
from src.services.notifications import (
    CapacityScanService,
    NotificationService,
    deliver_notification,
)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_async_engine():
    yield
    from src.database.database import _async_engine

    if _async_engine is not None:
        await _async_engine.dispose()


class _OkSender:
    provider = "test-ok"

    def __init__(self):
        self.sent = []

    async def send(self, *, to, subject, body):
        self.sent.append((to, subject, body))


class _FailingSender:
    provider = "test-fail"

    async def send(self, *, to, subject, body):
        raise RuntimeError("simulated SMTP outage")


def _employee(manager_id=None, email=None, capacity="8.00"):
    tag = uuid.uuid4().hex[:8]
    return Employee(
        employee_number=f"NOTIF-{tag}",
        first_name="Notify",
        last_name=f"Test{tag}",
        email=email or f"notify.{tag}@example.com",
        job_title="Engineer",
        employment_type=EmploymentTypeEnum.full_time,
        daily_capacity_hours=Decimal(capacity),
        bench_status=BenchStatusEnum.bench,
        manager_id=manager_id,
        is_active=True,
    )


async def _cleanup(employee_ids, notification_ids):
    async with AsyncSessionLocal() as db:
        if notification_ids:
            await db.execute(
                delete(EmailNotification).where(
                    EmailNotification.id.in_(notification_ids)
                )
            )
        for employee_id in employee_ids:
            await db.execute(
                delete(Allocation).where(Allocation.employee_id == employee_id)
            )
        await db.commit()
    async with AsyncSessionLocal() as db:
        for employee_id in employee_ids:
            await db.execute(delete(Employee).where(Employee.id == employee_id))
        await db.commit()


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded_without_raising(monkeypatch):
    employee = _employee()
    notification_id = None
    try:
        async with AsyncSessionLocal() as db:
            db.add(employee)
            await db.commit()
            service = NotificationService(db)
            row = await service.queue(
                event_type="employee_allocated",
                recipient_email=employee.email,
                recipient_employee_id=employee.id,
                subject="Test",
                body_text="Body",
            )
            await db.commit()
            notification_id = row.id

        monkeypatch.setattr(
            notification_module, "get_email_sender", lambda: _FailingSender()
        )
        await deliver_notification(notification_id)

        async with AsyncSessionLocal() as db:
            row = await db.get(EmailNotification, notification_id)
            assert row.status == "failed"
            assert row.attempts == 1
            assert "simulated SMTP outage" in (row.error or "")
    finally:
        await _cleanup([employee.id], [notification_id] if notification_id else [])


@pytest.mark.asyncio
async def test_delivery_success_marks_sent(monkeypatch):
    employee = _employee()
    notification_id = None
    sender = _OkSender()
    try:
        async with AsyncSessionLocal() as db:
            db.add(employee)
            await db.commit()
            row = await NotificationService(db).queue(
                event_type="training_assigned",
                recipient_email=employee.email,
                recipient_employee_id=employee.id,
                subject="Training",
                body_text="Body",
            )
            await db.commit()
            notification_id = row.id

        monkeypatch.setattr(
            notification_module, "get_email_sender", lambda: sender
        )
        await deliver_notification(notification_id)

        async with AsyncSessionLocal() as db:
            row = await db.get(EmailNotification, notification_id)
            assert row.status == "sent"
            assert row.sent_at is not None
            assert sender.sent and sender.sent[0][0] == employee.email
    finally:
        await _cleanup([employee.id], [notification_id] if notification_id else [])


@pytest.mark.asyncio
async def test_missing_email_is_skipped_not_crashing():
    employee = _employee(email="not-an-email")
    notification_id = None
    try:
        async with AsyncSessionLocal() as db:
            db.add(employee)
            await db.commit()
            row = await NotificationService(db).queue(
                event_type="cv_approved",
                recipient_email=employee.email,
                recipient_employee_id=employee.id,
                subject="CV",
                body_text="Body",
            )
            await db.commit()
            notification_id = row.id
            assert row.status == "skipped"
            assert row.error
    finally:
        await _cleanup([employee.id], [notification_id] if notification_id else [])


@pytest.mark.asyncio
async def test_employee_cv_upload_queues_manager_approval_notification(tmp_path):
    from fastapi.testclient import TestClient
    from fpdf import FPDF

    import src.api.main as main
    from src.auth.auth import hash_password
    from src.database.models import (
        CvReviewRequest,
        Document,
        User,
    )

    manager = None
    employee = None
    employee_user = None
    try:
        async with AsyncSessionLocal() as db:
            manager = _employee()
            db.add(manager)
            await db.flush()
            employee = _employee(manager_id=manager.id)
            db.add(employee)
            await db.flush()
            employee_user = User(
                username=f"upload_{uuid.uuid4().hex[:10]}",
                password_hash=hash_password("employee"),
                role="employee",
                full_name="Upload Tester",
                employee_id=employee.id,
            )
            db.add(employee_user)
            await db.commit()
            manager_id = manager.id
            employee_id = employee.id
            username = employee_user.username

        from src.database.database import _async_engine

        if _async_engine is not None:
            await _async_engine.dispose()

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(
            0,
            5,
            (
                "Upload Tester CV\n\n"
                "Professional Summary\n"
                "Experienced software engineer delivering reliable backend "
                "services, data pipelines and cloud-native applications.\n\n"
                "Core Skills\n"
                "Python, Kafka, Docker, SQL, REST APIs, testing and delivery.\n\n"
                "Experience\n"
                "Built and operated production data platforms for multiple "
                "teams, including event-driven Kafka pipelines and automated "
                "deployment workflows.\n"
            ),
        )
        pdf_path = tmp_path / "upload.pdf"
        pdf.output(str(pdf_path))

        client = TestClient(main.app)
        with client:
            login = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "employee"},
            )
            assert login.status_code == 200
            headers = {
                "Authorization": f"Bearer {login.json()['token']}"
            }
            with open(pdf_path, "rb") as handle:
                response = client.post(
                    "/api/v1/documents/upload",
                    headers=headers,
                    files={"file": ("upload.pdf", handle, "application/pdf")},
                    data={
                        "employee_id": str(employee_id),
                        "document_type": "cv",
                    },
                )
            assert response.status_code == 201, response.text
            assert response.json()["approval_status"] == "pending_approval"

        if _async_engine is not None:
            await _async_engine.dispose()

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(EmailNotification).where(
                        EmailNotification.event_type == "cv_approval_needed",
                        EmailNotification.recipient_employee_id == manager_id,
                    ).order_by(EmailNotification.created_at.desc())
                )
            ).scalars().first()
            assert row is not None
            assert row.recipient_email == manager.email
            assert row.status in ("queued", "sent")
            assert row.payload["employee_id"] == str(employee_id)
    finally:
        # Use the synchronous session for teardown: the TestClient has already
        # closed its own event loop, so the async engine must not be reused here.
        from src.database.database import SyncSessionLocal

        with SyncSessionLocal() as db:
            if employee is not None:
                db.execute(
                    delete(CvReviewRequest).where(
                        CvReviewRequest.employee_id == employee.id
                    )
                )
                db.execute(
                    delete(Document).where(Document.employee_id == employee.id)
                )
                db.execute(
                    delete(EmailNotification).where(
                        EmailNotification.payload["employee_id"].astext
                        == str(employee.id)
                    )
                )
                db.execute(
                    delete(User).where(User.employee_id == employee.id)
                )
                db.execute(
                    delete(Employee).where(Employee.id == employee.id)
                )
            if manager is not None:
                db.execute(delete(Employee).where(Employee.id == manager.id))
            db.commit()


@pytest.mark.asyncio
async def test_allocation_notification_template_uses_project_and_hours():
    from src.database.models import Project, ProjectDemand

    employee = _employee()
    project = None
    demand = None
    notification_id = None
    try:
        async with AsyncSessionLocal() as db:
            project = Project(
                name=f"Notify Project {uuid.uuid4().hex[:8]}",
                status="Active",
                probability=100,
                target_margin=Decimal("25.00"),
                total_budget=Decimal("10000.00"),
            )
            db.add(project)
            db.add(employee)
            await db.flush()
            demand = ProjectDemand(
                project_id=project.id,
                role="Notification Engineer",
                required_skills=[],
                hours_per_day=Decimal("4.00"),
                headcount_needed=1,
                headcount_required=1,
            )
            db.add(demand)
            await db.flush()
            allocation = Allocation(
                employee_id=employee.id,
                demand_id=demand.id,
                role_on_project=demand.role,
                allocated_hours_per_day=Decimal("4.00"),
                allocation_pct=Decimal("50.00"),
                status=AllocationStatusEnum.active,
                start_date=date.today(),
            )
            db.add(allocation)
            await db.flush()

            row = await NotificationService(db).notify_employee_allocated(
                allocation
            )
            await db.commit()
            notification_id = row.id
            assert row.status == "queued"
            assert row.payload["project_name"] == project.name
            assert row.payload["hours_per_day"] == 4.0
    finally:
        async with AsyncSessionLocal() as db:
            if notification_id:
                await db.execute(
                    delete(EmailNotification).where(
                        EmailNotification.id == notification_id
                    )
                )
            await db.execute(
                delete(Allocation).where(Allocation.employee_id == employee.id)
            )
            if demand is not None:
                await db.execute(
                    delete(ProjectDemand).where(ProjectDemand.id == demand.id)
                )
            if project is not None:
                await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(Employee).where(Employee.id == employee.id))
            await db.commit()


@pytest.mark.asyncio
async def test_training_endpoint_queues_and_delivers_notification():
    from fastapi.testclient import TestClient

    import src.api.main as main
    from src.database.models import TrainingAssignment, User

    employee = _employee()
    try:
        async with AsyncSessionLocal() as db:
            db.add(employee)
            await db.commit()
            employee_id = str(employee.id)

        from src.database.database import _async_engine

        if _async_engine is not None:
            await _async_engine.dispose()

        client = TestClient(main.app)
        with client:
            login = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "admin"},
            )
            assert login.status_code == 200
            headers = {
                "Authorization": f"Bearer {login.json()['token']}"
            }
            response = client.post(
                "/api/v1/trainings",
                headers=headers,
                json={
                    "employee_id": employee_id,
                    "title": f"Notification Path {employee.employee_number}",
                    "targeted_skills": ["Kafka"],
                    "ai_upskilling_path": "Kafka fundamentals",
                },
            )
            assert response.status_code == 201, response.text

        if _async_engine is not None:
            await _async_engine.dispose()

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(EmailNotification).where(
                        EmailNotification.event_type == "training_assigned",
                        EmailNotification.recipient_employee_id == employee.id,
                    )
                )
            ).scalars().all()
            assert rows, "training_assigned notification was not queued"
            assert rows[0].status in ("sent", "queued")
            assert rows[0].subject.startswith("Training assigned")
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(TrainingAssignment).where(
                    TrainingAssignment.employee_id == employee.id
                )
            )
            await db.execute(
                delete(EmailNotification).where(
                    EmailNotification.recipient_employee_id == employee.id
                )
            )
            await db.execute(
                delete(User).where(User.employee_id == employee.id)
            )
            await db.execute(
                delete(Employee).where(Employee.id == employee.id)
            )
            await db.commit()


@pytest.mark.asyncio
async def test_capacity_scan_dedupes_per_employee_event_day():
    today = date.today()
    manager = _employee()
    report = None
    notification_ids = []
    try:
        async with AsyncSessionLocal() as db:
            db.add(manager)
            await db.flush()
            report = _employee(manager_id=manager.id)
            db.add(report)
            await db.flush()
            db.add(
                Allocation(
                    employee_id=report.id,
                    role_on_project="Engineer",
                    allocated_hours_per_day=Decimal("8.00"),
                    allocation_pct=Decimal("100.00"),
                    status=AllocationStatusEnum.active,
                    start_date=today,
                    end_date=today + timedelta(days=10),
                )
            )
            await db.commit()

            first = await CapacityScanService(db).scan(
                today=today, employee_ids=[report.id]
            )
            await db.commit()
            assert first["warnings"] == 1
            assert first["deduped"] == 0
            notification_ids = [row.id for row in first["notifications"]]

            second = await CapacityScanService(db).scan(
                today=today, employee_ids=[report.id]
            )
            await db.commit()
            assert second["warnings"] == 0
            assert second["deduped"] == 1

        async with AsyncSessionLocal() as db:
            row = await db.get(EmailNotification, notification_ids[0])
            assert row.event_type == "capacity_warning"
            assert row.recipient_employee_id == manager.id
            assert row.payload["employee_id"] == str(report.id)
            assert row.payload["dedupe_key"].startswith(str(report.id))
    finally:
        employee_ids = [manager.id]
        if report is not None:
            employee_ids.append(report.id)
        await _cleanup(employee_ids, notification_ids)
