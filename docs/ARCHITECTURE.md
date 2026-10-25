# Bench Forecast: Architecture & Manual Testing Guide

> **The repository root [`README.md`](../README.md) is the canonical project guide**
> for setup, roles, schema, migrations, seeding, APIs, capacity rules, CV review,
> trainings, notifications, frontend routes and verification. This document
> focuses on the forecast/RAG internals and manual testing flow.

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
    Houses the independent processing nodes for the planner graph:
    *   `data_extractor_node`: Fetches available talent (within the forecast horizon) and open demands (win probability ≥ threshold, needed within the horizon).
    *   `skill_matcher_node` / `allocation_decider_node`: RAG context + deterministic skill/margin scoring; the LLM provides narrative only, and the best-match candidate per demand is selected deterministically.
    *   `forecast_planner_node`: Generates a consolidated plan (reallocations / trainings / hirings).
    *   *Retained HITL utilities (not wired into the graph):* `human_review_node`, `revision_planner_node`, `route_after_review`, `execution_engine_node`.
*   **`src/agents/graph.py`**
    Compiles the **linear planner** graph: `data_extractor → skill_matcher → allocation_decider → forecast_planner → END`. There is **no interrupt and no checkpointer** — HITL is handled by the PostgreSQL allocation state machine (see below).
*   **`src/services/forecast_runner.py`**
    Two-phase orchestration. `create_forecast_run` inserts a `ForecastRun` (status `queued`) and returns its id immediately; `execute_forecast_run` runs the planner, persists proposals + hire recommendations, and flips the status to `paused_for_review` (success) or `failed` (error). Runs execute as a FastAPI background task, so the API responds without blocking on the LLM.

### Data Flow

1. **Queue:** `POST /forecast/run` calls `create_forecast_run` (status `queued`), schedules `execute_forecast_run` as a background task, and returns the run id immediately.
2. **Extraction:** `data_extractor_node` populates `bench_employees` (available within the horizon) and `open_demands` (win probability ≥ threshold, needed within the horizon).
3. **AI Planning:** `skill_matcher_node` → `allocation_decider_node` → `forecast_planner_node` produce per-demand decisions (allocate / train / hire).
4. **Persist proposals:** `execute_forecast_run` writes `Allocation` rows as `pending_manager_approval` and `HiringRecommendation` rows, then sets the run to `paused_for_review`.
5. **Review:** The manager approves / rejects each proposal in the UI (`POST /allocations/{id}/approve|reject`).
6. **Execution:** `HITLService` applies the decision transactionally — allocation status, employee `bench_status` / `current_project`, demand `headcount_needed` / `filled` — and, once the run has no open proposals left, marks the `ForecastRun` `completed`. Rejections re-plan the role (or raise an external-hire recommendation).

---

## 2. Step-by-Step Manual Testing Guide

Follow these sequential steps to manually trigger a forecast, inspect the interrupted state, provide Human-In-The-Loop (HITL) approval, and verify the data changes in PostgreSQL.

### Step 2.1: Trigger the Planner

Quickest path — the bundled smoke script:

```bash
cd bench_forecast
./venv/bin/python tests/run_test.py
```

Or inline in a script / REPL:

```python
from sqlalchemy import select
from src.database.database import AsyncSessionLocal
from src.database.models import User
from src.services.forecast_runner import run_forecast_pipeline

async with AsyncSessionLocal() as db:
    user = await db.scalar(select(User).limit(1))

# Runs the planner to completion and persists pending proposals.
state = await run_forecast_pipeline(horizon_days=90, user_id=user.id)
run_id = state["forecast_run_id"]
print("Run:", run_id, "decisions:", len(state.get("decisions") or []))
```

Via the API the same operation is asynchronous: `POST /api/v1/forecast/run`
returns `{ "forecast_run_id": ..., "status": "queued" }`, and the client polls
`GET /api/v1/forecast/runs/{id}` until the status leaves `queued` / `running`.

### Step 2.2: Approve a Proposal (HITL)

Approvals are applied by the allocation state machine — there is no graph to resume:

```python
import uuid
from sqlalchemy import select
from src.database.database import AsyncSessionLocal
from src.database.models import Allocation, AllocationStatusEnum
from src.services.hitl_service import HITLService

async with AsyncSessionLocal() as db:
    alloc = await db.scalar(
        select(Allocation).where(
            Allocation.forecast_run_id == uuid.UUID(run_id),
            Allocation.status == AllocationStatusEnum.pending_manager_approval,
        ).limit(1)
    )
    await HITLService(db).approve_allocation(alloc.id, reviewer_id=user.id)
    print("approved", alloc.id)
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

