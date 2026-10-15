import pytest
import uuid
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from unittest.mock import patch, MagicMock, AsyncMock
from src.agents.nodes import (
    data_extractor_node,
    allocation_decider_node,
    execution_engine_node,
    forecast_planner_node,
    _build_match_justification,
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
    
    # Mocking database query for demand, project, employee and forecast run
    mock_demand = MagicMock(id=demand_id, headcount_needed=1, status=DemandStatusEnum.open)
    mock_proj = MagicMock()
    mock_proj.name = "Mock Project"
    mock_emp = MagicMock(id=emp_id, bench_status=BenchStatusEnum.bench)
    mock_run = MagicMock(id=forecast_id)
    
    demand_result = MagicMock()
    demand_result.scalars.return_value.first.return_value = mock_demand
    
    proj_result = MagicMock()
    proj_result.scalars.return_value.first.return_value = mock_proj
    
    emp_result = MagicMock()
    emp_result.scalars.return_value.first.return_value = mock_emp
    
    run_result = MagicMock()
    run_result.scalars.return_value.first.return_value = mock_run
    
    # Query order in execution_engine_node: demand → employee → project → run
    mock_async_session.execute.side_effect = [demand_result, emp_result, proj_result, run_result]
    
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


@pytest.mark.asyncio
async def test_data_extractor_cost_fallback(mock_async_session):
    """Test data_extractor_node provides non-zero cost fallback for employees with missing cost rows."""
    from src.database.models import ExperienceLevelEnum
    mock_emp = Employee(
        id=uuid.uuid4(),
        first_name="Alice",
        last_name="Smith",
        bench_status=BenchStatusEnum.bench,
        experience_level=ExperienceLevelEnum.senior,
        is_active=True,
    )
    mock_emp.employee_costs = []
    mock_emp.hourly_cost_rate = None

    emp_res = MagicMock()
    emp_res.scalars.return_value.all.return_value = [mock_emp]
    dem_res = MagicMock()
    dem_res.scalars.return_value.all.return_value = []
    mock_async_session.execute.side_effect = [emp_res, dem_res]

    updates = await data_extractor_node({"horizon_days": 90, "min_win_probability": 0.75})
    emp = updates["bench_employees"][0]
    assert emp["seniority"] == "Senior"
    assert emp["hourly_cost"] > 0
    assert emp["hourly_cost"] == 95.0


def test_allocation_decider_prevents_duplicate_allocations(mock_rag_pipeline, mock_llm_factory):
    """Test that two open demands do not allocate the exact same bench candidate."""
    emp_id = str(uuid.uuid4())
    state = {
        "bench_employees": [
            {"id": emp_id, "name": "Solo Senior", "seniority": "Senior", "hourly_cost": 95.0}
        ],
        "open_demands": [
            {
                "id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()),
                "role": "Role 1",
                "target_bill_rate": 150.0,
                "target_margin": 25.0
            },
            {
                "id": str(uuid.uuid4()),
                "project_id": str(uuid.uuid4()),
                "role": "Role 2",
                "target_bill_rate": 150.0,
                "target_margin": 25.0
            }
        ]
    }

    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"employee_id": emp_id, "full_name": "Solo Senior", "hourly_cost_rate": 95.0, "projected_margin_pct": 36.67}
    ]

    mock_llm_response = MagicMock()
    mock_llm_response.content = f'{{"action_type": "allocate", "employee_id": "{emp_id}", "projected_margin": 36.67, "justification": "Best available fit for this demand."}}'
    mock_llm_factory.get_chat_model.return_value.invoke.return_value = mock_llm_response
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "allocate",
        "employee_id": emp_id,
        "projected_margin": 36.67,
        "justification": "Best available fit for this demand."
    }

    updates = allocation_decider_node(state)
    decisions = updates["decisions"]
    assert len(decisions) == 2

    # Demand 1 gets the employee
    assert decisions[0]["action_type"] == "allocate"
    assert decisions[0]["employee_id"] == emp_id
    assert decisions[0]["hourly_cost_rate"] == 95.0

    # Demand 2 CANNOT get the same employee; must fallback to hire since no more candidates
    assert decisions[1]["action_type"] == "hire"
    assert decisions[1]["employee_id"] is None


def test_allocation_decider_prevents_hire_when_full_match_exists(mock_rag_pipeline, mock_llm_factory):
    """An LLM 'hire' is overridden when a bench candidate fully matches."""
    emp_id = str(uuid.uuid4())
    state = {
        "bench_employees": [
            {"id": emp_id, "name": "Ada Lovelace", "seniority": "Senior",
             "hourly_cost": 95.0, "skills": ["Python", "SQL"], "job_title": "Data Engineer"}
        ],
        "open_demands": [{
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "role": "Data Engineer",
            "description": "Build pipelines",
            "required_skills": ["Python", "SQL"],
            "target_bill_rate": 150.0,
            "target_margin": 25.0,
        }],
    }
    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"employee_id": emp_id, "full_name": "Ada Lovelace", "hourly_cost_rate": 95.0}
    ]
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "hire",
        "employee_id": None,
        "projected_margin": 0.0,
        "justification": "No suitable internal candidate found for this role.",
    }

    updates = allocation_decider_node(state)
    decision = updates["decisions"][0]

    # Full skill coverage → allocate internally rather than hire.
    assert decision["action_type"] == "allocate"
    assert decision["employee_id"] == emp_id


def test_allocation_decider_respects_hire_without_full_match(mock_rag_pipeline, mock_llm_factory):
    """When nobody fully matches, an LLM 'hire' decision stands."""
    emp_id = str(uuid.uuid4())
    state = {
        "bench_employees": [
            {"id": emp_id, "name": "Weak Match", "seniority": "Junior",
             "hourly_cost": 55.0, "skills": ["Excel", "Communication"], "job_title": "Analyst"}
        ],
        "open_demands": [{
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "role": "Backend Python Developer",
            "description": "Build FastAPI services",
            "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            "target_bill_rate": 120.0,
            "target_margin": 28.0,
        }],
    }
    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"employee_id": emp_id, "full_name": "Weak Match", "hourly_cost_rate": 55.0}
    ]
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "hire",
        "employee_id": None,
        "projected_margin": 0.0,
        "justification": "No internal candidate covers the required stack.",
    }

    updates = allocation_decider_node(state)
    decision = updates["decisions"][0]

    assert decision["action_type"] == "hire"
    assert decision["employee_id"] is None


def test_allocation_decider_forces_hire_when_no_meaningful_match(mock_rag_pipeline, mock_llm_factory):
    """If the best candidate covers <50% of required skills, hire instead."""
    emp_id = str(uuid.uuid4())
    state = {
        "bench_employees": [
            {"id": emp_id, "name": "Weak", "seniority": "Junior",
             "hourly_cost": 55.0, "skills": ["Excel"], "job_title": "Analyst"}
        ],
        "open_demands": [{
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "role": "Site Reliability Engineer",
            "description": "Kubernetes and Go platform reliability",
            "required_skills": ["Kubernetes", "Prometheus", "Grafana", "Go"],
            "target_bill_rate": 125.0,
            "target_margin": 31.0,
        }],
    }
    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"employee_id": emp_id, "full_name": "Weak", "hourly_cost_rate": 55.0}
    ]
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "allocate",
        "employee_id": emp_id,
        "projected_margin": 50.0,
        "justification": "Cheapest candidate available.",
    }

    updates = allocation_decider_node(state)
    decision = updates["decisions"][0]

    assert decision["action_type"] == "hire"
    assert decision["employee_id"] is None


def test_match_justification_drops_contradictory_hire_text():
    """Hire-language must not be appended to an allocation/training justification."""
    j = _build_match_justification(
        "Alice", "Java Developer", ["Java", "Spring"], [],
        "No internal candidate meets the criteria; external hire recommended.",
        action="allocate",
    )
    assert "external hire" not in j.lower()
    assert "no internal candidate" not in j.lower()
    assert "Java" in j

    # Consistent, specific LLM text is kept.
    j2 = _build_match_justification(
        "Alice", "Java Developer", ["Java"], [], "Strong Spring experience.",
        action="allocate",
    )
    assert "Strong Spring experience." in j2


def test_allocation_decider_uses_llm_chosen_candidate(mock_rag_pipeline, mock_llm_factory):
    """The employee the LLM proposes is the one selected (model drives choice)."""
    good_id = str(uuid.uuid4())
    bad_id = str(uuid.uuid4())
    state = {
        "bench_employees": [
            {"id": good_id, "name": "Good Match", "seniority": "Senior",
             "hourly_cost": 95.0, "skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
             "job_title": "Backend Python Engineer"},
            {"id": bad_id, "name": "Weak Match", "seniority": "Junior",
             "hourly_cost": 40.0, "skills": ["Excel", "Communication"],
             "job_title": "Business Analyst"},
        ],
        "open_demands": [{
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "role": "Backend Python Developer",
            "description": "Build FastAPI services on PostgreSQL",
            "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            "target_bill_rate": 120.0,
            "target_margin": 28.0,
        }],
    }

    mock_rag_pipeline.retrieve_candidates.return_value = [
        {"employee_id": good_id, "full_name": "Good Match", "hourly_cost_rate": 95.0},
        {"employee_id": bad_id, "full_name": "Weak Match", "hourly_cost_rate": 40.0},
    ]
    # The LLM (unreliably) picks the weaker candidate.
    mock_llm_factory.parse_json_response.return_value = {
        "action_type": "allocate",
        "employee_id": bad_id,
        "projected_margin": 60.0,
        "justification": "Selected the cheapest candidate for the role.",
    }

    updates = allocation_decider_node(state)
    decision = updates["decisions"][0]

    # The LLM picked the weak candidate; that choice stands. Because it lacks the
    # required skills, the action is refined from 'allocate' to 'train'.
    assert decision["employee_id"] == bad_id
    assert decision["action_type"] == "train"


def test_forecast_planner_node_fallback_on_unparseable_json(mock_llm_factory):
    """Test forecast_planner_node synthesizes a plan from decisions when LLM JSON is malformed."""
    emp_id = str(uuid.uuid4())
    state = {
        "skill_matches": [{"employee_id": emp_id, "demand_id": "d1", "match_score": 0.85}],
        "bench_employees": [{"id": emp_id, "name": "Jane Doe"}],
        "open_demands": [{"id": "d1", "role": "Data Engineer"}],
        "decisions": [
            {
                "action_type": "allocate",
                "employee_id": emp_id,
                "role_title": "Data Engineer",
                "target_project_id": "p1",
                "match_score": 0.85,
                "projected_margin": 32.0,
                "justification": "Optimal assignment",
            },
            {
                "action_type": "hire",
                "employee_id": None,
                "role_title": "Frontend Lead",
                "target_project_id": "p2",
                "match_score": 0.0,
                "projected_margin": 0.0,
                "justification": "No viable candidates",
            }
        ]
    }

    # Simulate LLM returning malformed/unparseable content
    mock_llm_response = MagicMock()
    mock_llm_response.content = '{"bench_employees": 15, "open_positions": 5, "broken...'
    mock_llm_factory.get_chat_model.return_value.invoke.return_value = mock_llm_response
    mock_llm_factory.parse_json_response.side_effect = ValueError("Could not extract valid JSON")

    updates = forecast_planner_node(state)
    assert "recommendations" in updates
    plan = updates["recommendations"]
    assert len(plan["reallocations"]) == 1
    assert plan["reallocations"][0]["employee_id"] == emp_id
    assert len(plan["hirings"]) == 1
    assert plan["hirings"][0]["role"] == "Frontend Lead"
    assert plan["confidence_score"] > 0



