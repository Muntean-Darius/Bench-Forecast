import pytest
import pytest_asyncio
import uuid
from typing import Dict, Any
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langgraph.types import Command
from src.services.forecast_runner import run_forecast_pipeline
from src.agents.graph import build_forecast_graph
from src.database.database import AsyncSessionLocal
from sqlalchemy import delete
from src.database.models import Employee, ProjectDemand, BenchStatusEnum, DemandStatusEnum, ForecastRun, Allocation, Project, User

# ---------------------------------------------------------------------------
# End-to-End Pipeline Tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def setup_test_db():
    """
    Sets up a clean test database state using the actual async session.
    Note: Assumes tests are configured to point to a test database instance.
    """
    emp_id = uuid.uuid4()
    demand_id = uuid.uuid4()
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    
    async with AsyncSessionLocal() as db:
        # Create a test user
        test_user = User(
            id=user_id,
            email=f"e2e_{user_id}@example.com",
            role="admin"
        )
        
        # Create a test employee
        test_employee = Employee(
            id=emp_id,
            employee_number=f"E2E-{emp_id}",
            email=f"e2emp_{emp_id}@example.com",
            job_title="E2E Mock Tester",
            first_name="E2E",
            last_name="TestUser",
            bench_status=BenchStatusEnum.bench,
            is_active=True,
            profile_text="E2E Mock Profile"
        )
        
        # Create a test project
        test_project = Project(
            id=project_id,
            name="E2E Mock Project",
            status="Pipeline",
            probability=90,
            target_margin=20.0,
            total_budget=50000.0
        )

        # Create a test demand
        test_demand = ProjectDemand(
            id=demand_id,
            project_id=project_id,
            role="E2E Tester",
            description="Mock Demand for E2E",
            status=DemandStatusEnum.open,
            win_probability=0.9,
            headcount_needed=1
        )
        
        db.add(test_user)
        db.add(test_employee)
        db.add(test_project)
        db.add(test_demand)
        await db.commit()
        
    yield {"emp_id": emp_id, "demand_id": demand_id, "user_id": user_id, "project_id": project_id}
    
    # Teardown
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Allocation).where(Allocation.employee_id == emp_id))
        await db.execute(delete(ProjectDemand).where(ProjectDemand.id == demand_id))
        await db.execute(delete(Project).where(Project.id == project_id))
        await db.execute(delete(Employee).where(Employee.id == emp_id))
        await db.execute(delete(ForecastRun).where(ForecastRun.triggered_by == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest.mark.asyncio
async def test_full_pipeline_flow(setup_test_db):
    """
    Validates the full pipeline flow:
    1. Triggers run_forecast_pipeline.
    2. Verifies the graph interrupts at human_review_node.
    3. Injects a mock HITL approval via Command(resume=...).
    4. Asserts final database writes (headcount decrements, status updates).
    """
    
    user_id = setup_test_db["user_id"]
    horizon_days = 90
    
    # 1. Trigger the forecast pipeline
    # The pipeline should run data_extractor -> skill_matcher -> allocation_decider -> forecast_planner 
    # and then hit the interrupt_before=["human_review"] pause.
    paused_state = await run_forecast_pipeline(
        horizon_days=horizon_days,
        user_id=user_id
    )
    
    forecast_run_id = paused_state.get("forecast_run_id")
    assert forecast_run_id is not None, "Pipeline did not generate a forecast_run_id"
    
    # Ensure recommendations were populated before the pause
    assert "recommendations" in paused_state
    
    # In a real environment, the LLM will generate 'reallocations'. 
    # For a deterministic test, we can manually inject our test employee/demand into the state.
    # We do this by reconstructing the graph and resuming with a modified state + approval.
    
    graph = build_forecast_graph()
    config = {"configurable": {"thread_id": forecast_run_id}}
    
    # Mocking the LLM's recommendation for our specific test setup
    mock_reallocations = {
        "human_approved": True,
        "recommendations": {
            "reallocations": [
                {
                    "employee_id": str(setup_test_db["emp_id"]),
                    "role_id": str(setup_test_db["demand_id"]),
                    "role": "E2E Tester",
                    "match_score": 0.99
                }
            ]
        }
    }
    
    # 2. & 3. Inject mock HITL approval via Command(resume=...)
    # We pass the approval and the injected reallocations so the execution engine processes them.
    final_state = await graph.ainvoke(
        Command(
            resume=mock_reallocations,
            update=mock_reallocations
        ), 
        config=config
    )
    
    assert final_state["execution_status"] == "executed_1_of_1", "Execution engine did not process the reallocation."
    
    # 4. Assert final relational database writes
    async with AsyncSessionLocal() as db:
        # Check Employee status update
        emp = await db.get(Employee, setup_test_db["emp_id"])
        assert emp.bench_status == BenchStatusEnum.on_project, "Employee bench status was not updated."
        
        # Check Demand headcount decrement and status update
        demand = await db.get(ProjectDemand, setup_test_db["demand_id"])
        assert demand.headcount_needed == 0, "Demand headcount was not decremented."
        assert demand.status == DemandStatusEnum.filled, "Demand status was not set to filled."
        
        # Check ForecastRun status
        run = await db.get(ForecastRun, uuid.UUID(forecast_run_id))
        assert run.status == "completed", "ForecastRun status was not marked as completed."

