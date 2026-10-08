import pytest
import uuid
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from unittest.mock import patch, MagicMock, AsyncMock
from src.agents.nodes import (
    data_extractor_node,
    allocation_decider_node,
    execution_engine_node
)
from src.database.models import Employee, ProjectDemand, BenchStatusEnum, DemandStatusEnum, ForecastRun

# ---------------------------------------------------------------------------
# Fixtures for Mocking Dependencies
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_async_session():
    """Mock for AsyncSessionLocal context manager."""
    with patch("src.agents.nodes.AsyncSessionLocal") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session
        yield mock_session


@pytest.fixture
def mock_vector_store():
    """Mock for VectorStoreManager (ChromaDB) to isolate skill matching/deciding logic."""
    with patch("src.agents.nodes.VectorStoreManager") as mock_vsm_class:
        mock_instance = MagicMock()
        mock_vsm_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_llm_factory():
    """Mock LLM Factory to avoid actual API calls."""
    with patch("src.agents.nodes.LLMFactory") as mock_factory:
        mock_llm = MagicMock()
        mock_factory.get_chat_model.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def mock_rag_pipeline():
    """Mock RAG Pipeline for allocation_decider_node."""
    with patch("src.agents.nodes.RAGPipeline") as mock_rag_class:
        mock_instance = MagicMock()
        mock_rag_class.return_value = mock_instance
        yield mock_instance


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_data_extractor_node(mock_async_session):
    """Test extracting employees and open demands from PostgreSQL."""
    
    # 1. Setup mock data
    mock_emp = Employee(
        id=uuid.uuid4(),
        first_name="Jane",
        last_name="Doe",
        bench_status=BenchStatusEnum.bench,
        is_active=True,
        profile_text="Senior Python Developer"
    )
    mock_emp.employee_costs = [] # Mock empty costs
    
    mock_demand = ProjectDemand(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role="Backend Engineer",
        description="FastAPI expert",
        status=DemandStatusEnum.open,
        win_probability=0.8,
        target_bill_rate=120.0
    )
    
    # Mocking database execute results
    # First call for employees, second for demands
    emp_result = MagicMock()
    emp_result.scalars.return_value.all.return_value = [mock_emp]
    
    demand_result = MagicMock()
    demand_result.scalars.return_value.all.return_value = [mock_demand]
    
    mock_async_session.execute.side_effect = [emp_result, demand_result]
    
    # 2. Execute Node
    initial_state = {"horizon_days": 90, "min_win_probability": 0.75}
    updates = await data_extractor_node(initial_state)
    
    # 3. Assertions
    assert "bench_employees" in updates
    assert len(updates["bench_employees"]) == 1
    assert updates["bench_employees"][0]["name"] == "Jane Doe"
    
    assert "open_demands" in updates
    assert len(updates["open_demands"]) == 1
    assert updates["open_demands"][0]["role"] == "Backend Engineer"


def test_allocation_decider_node(mock_rag_pipeline, mock_llm_factory):
    """Test financial-based allocation decider (Sync node)."""
    
    # 1. Setup mock state and dependencies
    mock_demand_id = str(uuid.uuid4())
    state = {
        "open_demands": [{
            "id": mock_demand_id,
            "project_id": str(uuid.uuid4()),
            "role": "Data Scientist",
            "description": "ML Models",
            "target_bill_rate": 150.0,
            "target_margin": 30.0
        }]
    }
    
    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"id": "c1", "name": "John Doe", "cost": 50.0}
    ]
    
    mock_llm_response = MagicMock()
    mock_llm_response.content = '{"action_type": "allocate", "employee_id": "c1", "projected_margin": 66.6, "justification": "Candidate is an excellent fit for the role due to strong skills."}'
    mock_llm_factory.get_chat_model.return_value.invoke.return_value = mock_llm_response
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "allocate", 
        "employee_id": "c1", 
        "projected_margin": 66.6, 
        "justification": "Candidate is an excellent fit for the role due to strong skills."
    }

    # 2. Execute Node
    updates = allocation_decider_node(state)
    
    # 3. Assertions
    assert "decisions" in updates
    assert len(updates["decisions"]) == 1
    decision = updates["decisions"][0]
    
    assert decision["action_type"] == "allocate"
    assert decision["employee_id"] == "c1"
    assert decision["projected_margin"] == 66.6
    assert mock_rag_pipeline.retrieve_candidates.called


@pytest.mark.asyncio
async def test_execution_engine_node_approved(mock_async_session):
    """Test execution engine handles approved plans and updates the database."""
    
    # 1. Setup mock state
    emp_id = uuid.uuid4()
    demand_id = uuid.uuid4()
    forecast_id = uuid.uuid4()
    
    state = {
        "human_approved": True,
        "forecast_run_id": str(forecast_id),
        "recommendations": {
            "reallocations": [
                {
                    "employee_id": str(emp_id),
                    "role_id": str(demand_id),
                    "role": "DevOps",
                    "match_score": 0.95
                }
            ]
        }
    }
    
    # Mocking database query for demand and employee
    mock_demand = MagicMock(id=demand_id, headcount_needed=1, status=DemandStatusEnum.open)
    mock_emp = MagicMock(id=emp_id, bench_status=BenchStatusEnum.bench)
    mock_run = MagicMock(id=forecast_id)
    
    demand_result = MagicMock()
    demand_result.scalars.return_value.first.return_value = mock_demand
    
    emp_result = MagicMock()
    emp_result.scalars.return_value.first.return_value = mock_emp
    
    run_result = MagicMock()
    run_result.scalars.return_value.first.return_value = mock_run
    
    mock_async_session.execute.side_effect = [demand_result, emp_result, run_result]
    
    # 2. Execute Node
    updates = await execution_engine_node(state)
    
    # 3. Assertions
    assert updates["execution_status"] == "executed_1_of_1"
    
    # Ensure database records were mutated correctly
    assert mock_demand.headcount_needed == 0
    assert mock_demand.status == DemandStatusEnum.filled
    assert mock_emp.bench_status == BenchStatusEnum.on_project
    assert mock_run.status == "completed"
    
    # Ensure add and commit were called
    assert mock_async_session.add.called
    assert mock_async_session.commit.called


@pytest.mark.asyncio
async def test_execution_engine_node_rejected(mock_async_session):
    """Test execution engine skips operations when HITL approval is absent."""
    state = {"human_approved": False, "recommendations": {}}
    updates = await execution_engine_node(state)
    assert updates["execution_status"] == "skipped_no_approval"
    assert not mock_async_session.commit.called

