"""End-to-end test of the canonical HITL execution loop.

The LangGraph workflow is now a linear *planner* (no interrupt): it produces
allocation proposals which are persisted, and the HITL state machine
(``HITLService``) executes them. This test exercises that loop against the real
database — approve a pending proposal and assert every downstream effect
(allocation, employee, demand, forecast run) is applied atomically.
"""

import os
import sys
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.database.database import AsyncSessionLocal
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchStatusEnum,
    DemandStatusEnum,
    Employee,
    ForecastRun,
    HiringRecommendation,
    HiringRecommendationStatusEnum,
    Project,
    ProjectDemand,
    TrainingAssignment,
    User,
)
from src.agents.nodes import data_extractor_node
from src.services.hitl_service import HITLService

RUN_TAG = uuid.uuid4().hex[:8]


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test():
    """Return pooled asyncpg connections to a fresh event loop per test.

    pytest-asyncio creates a new event loop per test, but the module-level async
    engine caches connections bound to the previous loop — disposing them after
    each test avoids 'attached to a different loop' errors.
    """
    yield
    from src.database.database import _async_engine
    if _async_engine is not None:
        await _async_engine.dispose()


async def _purge_e2e(db) -> None:
    """Remove every E2E artefact (this run's and any leftovers)."""
    await db.execute(text(
        "DELETE FROM hitl_review_events WHERE reviewer_id IN "
        "(SELECT id FROM users WHERE username LIKE 'e2e_%')"
    ))
    await db.execute(text(
        "DELETE FROM employees WHERE employee_number LIKE 'E2E-%' OR last_name = 'TestUser'"
    ))
    await db.execute(text(
        "DELETE FROM project_demands WHERE role LIKE 'E2E %' OR project_id IN "
        "(SELECT id FROM projects WHERE name LIKE 'E2E Mock Project%')"
    ))
    await db.execute(text("DELETE FROM projects WHERE name LIKE 'E2E Mock Project%'"))
    await db.execute(text(
        "DELETE FROM forecast_runs WHERE id IN "
        "(SELECT forecast_run_id FROM allocations WHERE forecast_run_id IS NOT NULL "
        " AND employee_id IN (SELECT id FROM employees WHERE employee_number LIKE 'E2E-%'))"
    ))
    await db.execute(text(
        "UPDATE forecast_runs SET triggered_by = NULL WHERE triggered_by IN "
        "(SELECT id FROM users WHERE username LIKE 'e2e_%')"
    ))
    await db.execute(text("DELETE FROM users WHERE username LIKE 'e2e_%'"))
    await db.commit()


@pytest_asyncio.fixture
async def setup_test_db():
    emp_id = uuid.uuid4()
    demand_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    run_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        await _purge_e2e(db)

        db.add(User(
            id=user_id,
            username=f"e2e_{RUN_TAG}",
            password_hash="not-a-real-hash",
            full_name="E2E Test User",
            email=f"e2e_{user_id}@example.com",
            role="manager",
        ))
        db.add(Employee(
            id=emp_id,
            employee_number=f"E2E-{RUN_TAG}",
            email=f"e2emp_{user_id}@example.com",
            job_title="E2E Mock Tester",
            first_name="E2E",
            last_name="TestUser",
            bench_status=BenchStatusEnum.bench,
            bench_start_date=date.today(),
            is_active=True,
            profile_text="E2E Mock Profile",
            skills="Testing",
        ))
        db.add(Project(
            id=project_id,
            name=f"E2E Mock Project {RUN_TAG}",
            status="Pipeline",
            probability=90,
            target_margin=20.0,
            total_budget=50000.0,
        ))
        db.add(ProjectDemand(
            id=demand_id,
            project_id=project_id,
            role=f"E2E Tester {RUN_TAG}",
            description="Mock Demand for E2E",
            required_skills=["Testing"],
            status=DemandStatusEnum.open,
            win_probability=0.9,
            headcount_needed=1,
            target_bill_rate=Decimal("100.00"),
        ))
        # Flush the dependencies first so the user row exists before the run's
        # triggered_by FK is written (the ORM does not order raw FK columns).
        await db.flush()
        db.add(ForecastRun(
            id=run_id,
            forecast_horizon_days=90,
            triggered_by=user_id,
            status="paused_for_review",
        ))
        await db.commit()

    yield {
        "emp_id": emp_id, "demand_id": demand_id,
        "user_id": user_id, "project_id": project_id, "run_id": run_id,
    }

    async with AsyncSessionLocal() as db:
        await _purge_e2e(db)


async def _create_pending_allocation(ids) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        alloc = Allocation(
            id=uuid.uuid4(),
            employee_id=ids["emp_id"],
            demand_id=ids["demand_id"],
            role_on_project="E2E Tester",
            allocation_pct=Decimal("100.00"),
            status=AllocationStatusEnum.pending_manager_approval,
            forecast_run_id=ids["run_id"],
            created_by=ids["user_id"],
            start_date=date.today(),
        )
        db.add(alloc)
        await db.commit()
        return alloc.id


@pytest.mark.asyncio
async def test_approval_executes_allocation_and_completes_run(setup_test_db):
    """Approving a proposal must execute it and close the forecast run."""
    ids = setup_test_db
    alloc_id = await _create_pending_allocation(ids)

    async with AsyncSessionLocal() as db:
        svc = HITLService(db)
        await svc.approve_allocation(alloc_id, reviewer_id=ids["user_id"])

    async with AsyncSessionLocal() as db:
        alloc = await db.get(Allocation, alloc_id)
        assert alloc.status == AllocationStatusEnum.approved_by_manager
        assert alloc.approved_by == ids["user_id"]

        emp = await db.get(Employee, ids["emp_id"])
        assert emp.bench_status == BenchStatusEnum.on_project
        assert emp.current_project is not None

        demand = await db.get(ProjectDemand, ids["demand_id"])
        assert demand.headcount_needed == 0
        assert demand.status == DemandStatusEnum.filled

        run = await db.get(ForecastRun, ids["run_id"])
        assert run.status == "completed", "Run should auto-complete once it has no open proposals"


@pytest.mark.asyncio
async def test_approval_of_training_allocation_preserves_upskilling(setup_test_db):
    """A training allocation is approved like any other and keeps its upskilling path."""
    ids = setup_test_db
    async with AsyncSessionLocal() as db:
        alloc = Allocation(
            id=uuid.uuid4(),
            employee_id=ids["emp_id"],
            demand_id=ids["demand_id"],
            role_on_project="E2E Tester",
            allocation_pct=Decimal("100.00"),
            status=AllocationStatusEnum.pending_manager_approval,
            ai_upskilling_path="Complete a Selenium course to close the gap.",
            forecast_run_id=ids["run_id"],
            created_by=ids["user_id"],
            start_date=date.today(),
        )
        db.add(alloc)
        await db.commit()
        alloc_id = alloc.id

    async with AsyncSessionLocal() as db:
        await HITLService(db).approve_allocation(
            alloc_id, reviewer_id=ids["user_id"]
        )

    async with AsyncSessionLocal() as db:
        alloc = await db.get(Allocation, alloc_id)
        assert alloc.status == AllocationStatusEnum.approved_by_manager
        assert alloc.ai_upskilling_path == "Complete a Selenium course to close the gap."

        emp = await db.get(Employee, ids["emp_id"])
        assert emp.bench_status == BenchStatusEnum.on_project

        demand = await db.get(ProjectDemand, ids["demand_id"])
        assert demand.headcount_needed == 0
        assert demand.status == DemandStatusEnum.filled

        training = (
            await db.execute(
                select(TrainingAssignment).where(
                    TrainingAssignment.employee_id == ids["emp_id"],
                    TrainingAssignment.demand_id == ids["demand_id"],
                )
            )
        ).scalars().first()
        assert training is not None, "Approved train proposal must create a training assignment"
        assert training.ai_upskilling_path == alloc.ai_upskilling_path
        assert training.targeted_skills


@pytest.mark.asyncio
async def test_run_stays_paused_until_open_hire_is_dismissed(setup_test_db):
    """A run is not complete while a hiring recommendation is still open."""
    ids = setup_test_db
    alloc_id = await _create_pending_allocation(ids)

    async with AsyncSessionLocal() as db:
        hire = HiringRecommendation(
            id=uuid.uuid4(),
            project_id=ids["project_id"],
            demand_id=ids["demand_id"],
            role="E2E Hire",
            required_skills=["Testing"],
            headcount=1,
            justification="No internal candidate.",
            status=HiringRecommendationStatusEnum.open,
            forecast_run_id=ids["run_id"],
            created_by=ids["user_id"],
        )
        db.add(hire)
        await db.commit()
        hire_id = hire.id

    async with AsyncSessionLocal() as db:
        await HITLService(db).approve_allocation(
            alloc_id, reviewer_id=ids["user_id"]
        )

    async with AsyncSessionLocal() as db:
        run = await db.get(ForecastRun, ids["run_id"])
        assert run.status == "paused_for_review"

    async with AsyncSessionLocal() as db:
        await HITLService(db).dismiss_hiring(hire_id)

    async with AsyncSessionLocal() as db:
        run = await db.get(ForecastRun, ids["run_id"])
        assert run.status == "completed"


@pytest.mark.asyncio
async def test_approved_allocation_is_excluded_from_next_forecast(setup_test_db):
    """An approved allocation must leave the bench and close its demand."""
    ids = setup_test_db
    alloc_id = await _create_pending_allocation(ids)

    async with AsyncSessionLocal() as db:
        await HITLService(db).approve_allocation(
            alloc_id, reviewer_id=ids["user_id"]
        )

    updates = await data_extractor_node({
        "horizon_days": 90,
        "min_win_probability": 0.0,
        "target_project_id": str(ids["project_id"]),
    })

    bench_ids = {str(e["id"]) for e in updates["bench_employees"]}
    demand_ids = {str(d["id"]) for d in updates["open_demands"]}
    assert str(ids["emp_id"]) not in bench_ids, "approved employee is still on the bench"
    assert str(ids["demand_id"]) not in demand_ids, "filled demand is still open"


@pytest.mark.asyncio
async def test_rejection_marks_allocation_rejected(setup_test_db):
    """Rejecting a proposal records the decision and removes it from the queue."""
    ids = setup_test_db
    alloc_id = await _create_pending_allocation(ids)

    async with AsyncSessionLocal() as db:
        svc = HITLService(db)
        await svc.reject_allocation(alloc_id, reviewer_id=ids["user_id"], feedback="Not a fit")

    async with AsyncSessionLocal() as db:
        alloc = await db.get(Allocation, alloc_id)
        assert alloc.status == AllocationStatusEnum.rejected_by_manager
        assert alloc.manager_notes == "Not a fit"
