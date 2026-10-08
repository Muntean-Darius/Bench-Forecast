# Bench Forecast: Architecture & Manual Testing Guide

This document provides a comprehensive overview of the **Bench Forecast** backend system. It details the directory structure, the responsibilities of core files, the data flow of the LangGraph state machine, and provides a step-by-step guide to manually testing the pipeline locally.

---

## 1. System Overview & File Roles

Bench Forecast leverages **LangGraph**, **FastAPI**, **SQLAlchemy 2.0 (Async)**, **PostgreSQL**, and **ChromaDB** to orchestrate AI-driven allocation decisions for bench employees.

### Directory Structure & File Roles

Below is a map of the core files powering the backend pipeline:

```text
bench_forecast/src/
├── agents/
│   ├── graph.py        # LangGraph Workflow Construction
│   ├── nodes.py        # Discrete LangGraph Processing Nodes
│   └── state.py        # TypedDict definition of Graph State
├── database/
│   ├── database.py     # SQLAlchemy 2.0 Async/Sync DB Session Factories
│   ├── models.py       # SQLAlchemy ORM Definitions (PostgreSQL tables)
│   └── vector_store.py # ChromaDB interactions (Semantic Search)
└── services/
    └── forecast_runner.py # Service Layer triggering the Pipeline
```

### File Responsibilities

*   **`src/database/models.py`**
    Defines the PostgreSQL relational schema using SQLAlchemy ORM. Contains models for `Employee`, `ProjectDemand`, `ForecastRun`, and `Allocation`. It is the absolute source of truth for the system's operational state.
*   **`src/database/database.py`**
    Manages database connectivity, supplying `AsyncSessionLocal` (via asyncpg) for fast, non-blocking asynchronous queries within the application layer, and `SyncSessionLocal` for background/sync jobs.
*   **`src/agents/state.py`**
    Declares the `State` class (a `TypedDict`), defining the exact payload structure that flows between nodes during the pipeline run. It includes properties like `bench_employees`, `open_demands`, `recommendations`, and `human_approved`.
*   **`src/agents/nodes.py`**
    Houses the independent business-logic blocks (nodes) for the graph:
    *   `data_extractor_node`: Fetches available talent and open demands from Postgres.
    *   `skill_matcher_node` / `allocation_decider_node`: Retrieves RAG context via `vector_store.py` and uses an LLM to evaluate match fit and financial viability.
    *   `forecast_planner_node`: Generates a consolidated plan.
    *   `human_review_node`: Gatekeeper node evaluating the injected HITL decision.
    *   `execution_engine_node`: Deterministically writes the approved reallocations back to Postgres.
*   **`src/agents/graph.py`**
    Instantiates the `StateGraph`, registers all nodes from `nodes.py`, defining the directed edges (flow) and conditional routing. Critically, it configures `interrupt_before=["human_review"]` to pause the AI loop for human validation.
*   **`src/services/forecast_runner.py`**
    The service interface. It initializes a `ForecastRun` in the DB, sets up the initial LangGraph state with `forecast_run_id`, and triggers `graph.ainvoke(...)` to kick off the pipeline until it hits the interrupt pause.

### Data Flow

1. **Trigger:** A request hits the service layer (`forecast_runner.py`), creating a `ForecastRun` and initializing the graph with `run_forecast_pipeline`.
2. **Extraction:** The state enters `data_extractor_node`, which queries `AsyncSessionLocal` to populate `bench_employees` and `open_demands` in the state.
3. **AI Planning:** The state transitions through the skill matcher and allocation decider, appending context from ChromaDB (`VectorStoreManager`) and invoking the LLM, finally resulting in a structured plan built by `forecast_planner_node`.
4. **Pause (HITL):** The graph pauses exactly before `human_review_node`, saving the state to a `MemorySaver` checkpoint keyed by the `forecast_run_id`.
5. **Resume:** A manager submits their review via a `Command(resume={"human_approved": True})`. The state updates and unpauses, allowing `human_review_node` and `execution_engine_node` to execute.
6. **Execution:** The execution node writes `Allocation` records, decrements demand `headcount_needed`, updates the employee to `on_project` in PostgreSQL, and marks the `ForecastRun` as `completed`.

---

## 2. Step-by-Step Manual Testing Guide

Follow these sequential steps to manually trigger a forecast, inspect the interrupted state, provide Human-In-The-Loop (HITL) approval, and verify the data changes in PostgreSQL.

### Step 2.1: Open a Python Shell & Trigger the Pipeline

Run a local Python REPL from the project root:

```bash
cd bench_forecast
python -m asyncio
```

Execute the pipeline in the REPL:

```python
import uuid
from src.services.forecast_runner import run_forecast_pipeline
from src.agents.graph import build_forecast_graph
from langgraph.types import Command

# 1. Trigger the forecast pipeline
user_id = uuid.uuid4()
horizon_days = 90

# This will run the graph and PAUSE before human_review_node
paused_state = await run_forecast_pipeline(horizon_days, user_id)

print(f"Pipeline paused. Run ID: {paused_state['forecast_run_id']}")
print(f"Generated Reallocations: {paused_state['recommendations'].get('reallocations')}")

# Save the run ID for the resume step
run_id = paused_state['forecast_run_id']
```

### Step 2.2: Resume the Pipeline with HITL Approval

In the same Python session, inject the human approval using LangGraph's `Command(resume=...)`:

```python
# 2. Re-instantiate the graph configuration
graph = build_forecast_graph()
config = {"configurable": {"thread_id": run_id}}

# 3. Inject approval
approval_payload = {"human_approved": True, "human_feedback": "Looks good, approved."}

# 4. Resume the graph execution
final_state = await graph.ainvoke(Command(resume=approval_payload), config=config)

print(f"Execution Status: {final_state.get('execution_status')}")
```

### Step 2.3: Verify Results in PostgreSQL (psql)

Once the python execution succeeds, open a separate terminal to verify the transactional changes in your local database.

```bash
# Connect to your local PostgreSQL instance
psql -U postgres -d bench_forecast
```

Run the following queries to validate that the pipeline successfully committed the allocations:

```sql
-- 1. Verify the Forecast Run is completed
SELECT id, status, forecast_horizon_days 
FROM forecast_runs 
ORDER BY created_at DESC LIMIT 1;

-- 2. Check that the Allocations were created
SELECT employee_id, demand_id, role_on_project, ai_match_score 
FROM allocations 
ORDER BY created_at DESC LIMIT 5;

-- 3. Ensure the Employee bench status was updated to 'on_project'
SELECT id, first_name, last_name, bench_status 
FROM employees 
WHERE id IN (
    SELECT employee_id FROM allocations ORDER BY created_at DESC LIMIT 5
);

-- 4. Confirm the Demand headcount was decremented and status filled
SELECT id, role, status, headcount_needed 
FROM project_demands 
WHERE id IN (
    SELECT demand_id FROM allocations ORDER BY created_at DESC LIMIT 5
);
```

If the data reflects the plan generated during Step 2.1, your end-to-end integration is completely functional!

