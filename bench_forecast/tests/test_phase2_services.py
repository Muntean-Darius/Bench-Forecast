"""Phase 2 regression tests — capacity, scoping and CV routing."""

import os
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchStatusEnum,
    CvApprovalStatusEnum,
    Employee,
    EmploymentTypeEnum,
    ProjectDemand,
    SkillOriginEnum,
)
from src.services.skills_service import normalize_skills
from src.services.workforce_service import (
    effective_capacity,
    summarize_allocations,
)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_async_engine():
    yield
    from src.database.database import _async_engine

    if _async_engine is not None:
        await _async_engine.dispose()


def _employee(capacity="8.00") -> Employee:
    return Employee(
        id=uuid.uuid4(),
        first_name="Capacity",
        last_name="Tester",
        job_title="Engineer",
        employment_type=EmploymentTypeEnum.full_time,
        daily_capacity_hours=Decimal(capacity),
        is_active=True,
    )


def _allocation(
    employee: Employee,
    hours: str,
    status: AllocationStatusEnum,
    *,
    start=None,
    end=None,
) -> Allocation:
    return Allocation(
        id=uuid.uuid4(),
        employee_id=employee.id,
        role_on_project="Engineer",
        allocated_hours_per_day=Decimal(hours),
        allocation_pct=Decimal("50.00"),
        status=status,
        start_date=start,
        end_date=end,
    )


def test_effective_capacity_respects_hard_cap():
    assert effective_capacity(_employee("8.00")) == Decimal("8.00")
    assert effective_capacity(_employee("9.00")) == Decimal("8.00")
    assert effective_capacity(_employee("4.00")) == Decimal("4.00")


def test_remaining_excludes_committed_and_reserved():
    emp = _employee("8.00")
    allocations = [
        _allocation(emp, "4.00", AllocationStatusEnum.active),
        _allocation(emp, "2.00", AllocationStatusEnum.pending_manager_approval),
    ]
    summary = summarize_allocations(emp, allocations)
    assert summary["committed_hours_per_day"] == Decimal("4.00")
    assert summary["reserved_hours_per_day"] == Decimal("2.00")
    assert summary["remaining_available_hours"] == Decimal("2.00")


def test_null_dates_are_treated_as_ongoing():
    emp = _employee("8.00")
    allocation = _allocation(emp, "4.00", AllocationStatusEnum.active)
    summary = summarize_allocations(emp, [allocation], on_date=date.today())
    assert summary["remaining_available_hours"] == Decimal("4.00")


def test_future_and_past_allocations_are_ignored():
    emp = _employee("8.00")
    future = _allocation(
        emp,
        "4.00",
        AllocationStatusEnum.active,
        start=date.today() + timedelta(days=30),
    )
    past = _allocation(
        emp,
        "4.00",
        AllocationStatusEnum.active,
        start=date.today() - timedelta(days=60),
        end=date.today() - timedelta(days=1),
    )
    summary = summarize_allocations(emp, [future, past])
    assert summary["remaining_available_hours"] == Decimal("8.00")


def test_exclude_allocation_id_prevents_self_collision():
    emp = _employee("8.00")
    existing = _allocation(emp, "6.00", AllocationStatusEnum.active)
    other = _allocation(emp, "2.00", AllocationStatusEnum.active)

    # Updating the 6h allocation to 8h should not collide with its own old 6h.
    summary = summarize_allocations(
        emp,
        [existing, other],
        exclude_allocation_id=existing.id,
    )
    assert summary["remaining_available_hours"] == Decimal("6.00")

    plain = summarize_allocations(emp, [existing, other])
    assert plain["remaining_available_hours"] == Decimal("0.00")


def test_fractional_multi_project_allocation_is_valid():
    emp = _employee("8.00")
    project_a = _allocation(emp, "4.00", AllocationStatusEnum.active)
    project_b = _allocation(emp, "4.00", AllocationStatusEnum.approved_by_manager)
    summary = summarize_allocations(emp, [project_a, project_b])
    assert summary["remaining_available_hours"] == Decimal("0.00")
    assert summary["committed_hours_per_day"] == Decimal("8.00")


def test_part_time_capacity_cannot_take_eight_hours():
    emp = _employee("4.00")
    summary = summarize_allocations(emp, [])
    assert summary["daily_capacity_hours"] == Decimal("4.00")
    assert summary["remaining_available_hours"] == Decimal("4.00")


def test_skill_normalization_is_case_insensitive_union():
    assert normalize_skills(["Python", "python", " SQL ", None, ""]) == [
        "Python",
        "SQL",
    ]


@pytest.mark.asyncio
async def test_allocation_service_enforces_hard_cap_and_supports_multi_project():
    from sqlalchemy import delete

    from src.database.database import AsyncSessionLocal
    from src.database.models import Allocation as AllocationModel
    from src.database.models import (
        BenchStatusEnum,
        DemandStatusEnum,
        EmployeeSkill,
        EmployeeSkillEvidence,
        Project,
        ProjectDemand,
    )
    from src.services.workforce_service import (
        AllocationService,
        CapacityExceededError,
    )

    tag = uuid.uuid4().hex[:8]
    project = Project(
        name=f"Phase2 Capacity {tag}",
        status="Active",
        probability=100,
        target_margin=Decimal("25.00"),
        total_budget=Decimal("100000.00"),
    )
    employee = Employee(
        employee_number=f"P2-{tag}",
        first_name="Capacity",
        last_name=f"Test{tag}",
        email=f"capacity.{tag}@example.com",
        job_title="Engineer",
        employment_type=EmploymentTypeEnum.full_time,
        daily_capacity_hours=Decimal("8.00"),
        bench_status=BenchStatusEnum.bench,
        is_active=True,
    )
    try:
        async with AsyncSessionLocal() as db:
            db.add(project)
            db.add(employee)
            await db.flush()
            demand_a = ProjectDemand(
                project_id=project.id,
                role="Engineer A",
                required_skills=["Python"],
                hours_per_day=Decimal("4.00"),
                headcount_needed=1,
                headcount_required=1,
                status=DemandStatusEnum.open,
            )
            demand_b = ProjectDemand(
                project_id=project.id,
                role="Engineer B",
                required_skills=["SQL"],
                hours_per_day=Decimal("4.00"),
                headcount_needed=1,
                headcount_required=1,
                status=DemandStatusEnum.open,
            )
            demand_c = ProjectDemand(
                project_id=project.id,
                role="Engineer C",
                required_skills=["Docker"],
                hours_per_day=Decimal("1.00"),
                headcount_needed=1,
                headcount_required=1,
                status=DemandStatusEnum.open,
            )
            db.add(demand_a)
            db.add(demand_b)
            db.add(demand_c)
            await db.commit()

            service = AllocationService(db)
            first = await service.create_allocation(
                employee_id=employee.id,
                demand_id=demand_a.id,
                allocated_hours_per_day=Decimal("4.00"),
            )
            second = await service.create_allocation(
                employee_id=employee.id,
                demand_id=demand_b.id,
                allocated_hours_per_day=Decimal("4.00"),
            )
            assert first.allocation_pct == Decimal("50.00")
            assert second.allocation_pct == Decimal("50.00")

            with pytest.raises(CapacityExceededError):
                await service.create_allocation(
                    employee_id=employee.id,
                    demand_id=demand_c.id,
                    allocated_hours_per_day=Decimal("1.00"),
                )

            # Updating the first allocation must exclude its own old 4h.
            await service.update_allocation(
                first, allocated_hours_per_day=Decimal("3.00")
            )
            await db.commit()
            assert first.allocation_pct == Decimal("37.50")
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(AllocationModel).where(
                    AllocationModel.employee_id == employee.id
                )
            )
            await db.execute(
                delete(EmployeeSkillEvidence).where(
                    EmployeeSkillEvidence.employee_id == employee.id
                )
            )
            await db.execute(
                delete(EmployeeSkill).where(
                    EmployeeSkill.employee_id == employee.id
                )
            )
            await db.execute(
                delete(ProjectDemand).where(
                    ProjectDemand.project_id == project.id
                )
            )
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(Employee).where(Employee.id == employee.id))
            await db.commit()


@pytest.mark.asyncio
async def test_cv_approval_is_union_and_preserves_project_skills(monkeypatch):
    from sqlalchemy import delete

    from src.database.database import AsyncSessionLocal
    from src.database.models import (
        CvReviewRequest,
        Document,
        DocumentTypeEnum,
        EmployeeSkill,
        EmployeeSkillEvidence,
        IngestionStatusEnum,
        Skill,
        SkillOriginEnum,
    )
    from src.services.cv_review_service import CvReviewService

    tag = uuid.uuid4().hex[:8]
    project_skill = f"ProjectOnly{tag}"
    new_cv_skill = f"NewFromCV{tag}"

    async def fake_reembed(session, employee, document, profile_text):
        return "fake-chroma-id", 1

    monkeypatch.setattr(
        "src.services.cv_review_service.reembed_employee_profile",
        fake_reembed,
    )

    employee = Employee(
        employee_number=f"CV-{tag}",
        first_name="CV",
        last_name=f"Tester{tag}",
        email=f"cv.{tag}@example.com",
        job_title="Engineer",
        employment_type=EmploymentTypeEnum.full_time,
        daily_capacity_hours=Decimal("8.00"),
        bench_status=BenchStatusEnum.bench,
        is_active=True,
        skills=project_skill,
        profile_text="Original profile",
    )
    project_skill_row = Skill(name=project_skill, is_verified=False)

    try:
        async with AsyncSessionLocal() as db:
            db.add(employee)
            db.add(project_skill_row)
            await db.flush()
            db.add(
                EmployeeSkill(
                    employee_id=employee.id,
                    skill_id=project_skill_row.id,
                    source=SkillOriginEnum.project,
                    is_locked=True,
                )
            )
            old_doc = Document(
                employee_id=employee.id,
                document_type=DocumentTypeEnum.cv,
                file_name="old.pdf",
                file_path="storage/cv/old.pdf",
                version=1,
                is_current=True,
                ingestion_status=IngestionStatusEnum.ingested,
                approval_status="approved",
                text_content="old",
            )
            new_doc = Document(
                employee_id=employee.id,
                document_type=DocumentTypeEnum.cv,
                file_name="new.pdf",
                file_path="storage/cv/new.pdf",
                version=2,
                is_current=False,
                ingestion_status=IngestionStatusEnum.pending,
                approval_status="pending_approval",
                text_content=f"CV with {new_cv_skill}",
            )
            db.add(old_doc)
            db.add(new_doc)
            await db.commit()

            service = CvReviewService(db)
            review = await service.stage_cv(
                employee=employee,
                document=new_doc,
                extracted_skills=[new_cv_skill],
                summary="Fresh summary",
            )
            await db.commit()

            review = await service.get_review(review.id)
            approved = await service.approve(review, reviewer_id=None, comment="ok")
            assert approved.status == CvApprovalStatusEnum.approved

        async with AsyncSessionLocal() as db:
            emp = await db.get(Employee, employee.id)
            assert "Fresh summary" in (emp.profile_text or "")
            assert project_skill in (emp.profile_text or "")

            links = (
                await db.execute(
                    select(EmployeeSkill).where(
                        EmployeeSkill.employee_id == employee.id
                    )
                )
            ).scalars().all()
            names = {
                (await db.get(Skill, link.skill_id)).name: link for link in links
            }
            assert project_skill in names and names[project_skill].is_locked
            assert new_cv_skill in names

            docs = (
                await db.execute(
                    select(Document).where(Document.employee_id == employee.id)
                )
            ).scalars().all()
            by_version = {doc.version: doc for doc in docs}
            assert by_version[1].is_current is False
            assert by_version[2].is_current is True
            assert by_version[2].approval_status == CvApprovalStatusEnum.approved
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                delete(CvReviewRequest).where(
                    CvReviewRequest.employee_id == employee.id
                )
            )
            await db.execute(
                delete(Document).where(Document.employee_id == employee.id)
            )
            await db.execute(
                delete(EmployeeSkillEvidence).where(
                    EmployeeSkillEvidence.employee_id == employee.id
                )
            )
            await db.execute(
                delete(EmployeeSkill).where(
                    EmployeeSkill.employee_id == employee.id
                )
            )
            await db.execute(delete(Employee).where(Employee.id == employee.id))
            await db.execute(delete(Skill).where(Skill.id == project_skill_row.id))
            await db.commit()


@pytest.mark.asyncio
async def test_phase2_api_role_scoping():
    """Manager candidate drawer only returns direct reports; admin sees more."""
    from fastapi.testclient import TestClient

    import src.api.main as main

    client = TestClient(main.app)
    with client:
        def login(username, password):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": password},
            )
            assert response.status_code == 200
            return {"Authorization": f"Bearer {response.json()['token']}"}

        admin_headers = login("admin", "admin")
        manager_headers = login("manager", "manager")
        manager_me = client.get(
            "/api/v1/auth/me", headers=manager_headers
        ).json()
        manager_employee_id = manager_me["employee_id"]
        assert manager_employee_id

        projects = client.get(
            "/api/v1/projects", headers=manager_headers
        ).json()
        assert projects, "managers must be able to view all projects"

        detail = client.get(
            f"/api/v1/projects/{projects[0]['id']}", headers=manager_headers
        ).json()
        assert "roster" in detail
        demand_id = detail["roster"][0]["demand_id"]

        manager_candidates = client.get(
            f"/api/v1/team/candidates?demand_id={demand_id}",
            headers=manager_headers,
        ).json()
        assert all(
            str(candidate["manager_id"]) == manager_employee_id
            for candidate in manager_candidates
        )

        admin_candidates = client.get(
            f"/api/v1/team/candidates?demand_id={demand_id}",
            headers=admin_headers,
        ).json()
        assert len(admin_candidates) >= len(manager_candidates)

        # A manager must not be able to assign someone outside their team.
        outside = next(
            candidate for candidate in admin_candidates
            if str(candidate["manager_id"]) != manager_employee_id
        )
        forbidden = client.post(
            "/api/v1/allocations/assign",
            headers=manager_headers,
            json={
                "employee_id": outside["employee_id"],
                "demand_id": demand_id,
                "allocated_hours_per_day": 2,
            },
        )
        assert forbidden.status_code == 403
