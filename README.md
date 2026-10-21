# Bench Forecast 2.0

An agentic workforce allocation engine with RAG matching and a Human-In-The-Loop (HITL) feedback loop.

## Quick Start Guide

### 1. Database (PostgreSQL via Docker)
You need a running PostgreSQL database. A Docker container named `bench-pg` should already be running from earlier. If you ever need to start it fresh, run:
```bash
docker run -d --name bench-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
```

### 2. Backend (FastAPI + LangGraph)
Open a terminal in the `bench_forecast/` directory, activate the virtual environment, and start the API server:
```bash
cd bench_forecast
source venv/bin/activate

# (Optional) If you need to reset the database and re-seed mock data:
# python seed.py

# Start the FastAPI server
python server.py
```
*The backend will run at http://localhost:8000. You can view the API docs at http://localhost:8000/docs.*

### 3. Frontend (React + Vite)
Open a **new terminal** in the `frontend/` directory, install dependencies (if you haven't), and start the dev server:
```bash
cd frontend
npm install
npm run dev
```
*The frontend will run at http://localhost:3000.*

---

## Testing the Main Flow
Once both servers are running, open **http://localhost:3000** in your browser.

1. **Generate Forecast:** Click **"Run Forecast"**. This triggers the LangGraph agent to extract data from PostgreSQL, use ChromaDB (RAG) to semantically match CVs against open demands, and generate a draft allocation plan.
2. **Human-in-the-Loop Review:** The workflow will pause and present the proposed allocations on your screen.
3. **Execute or Reject:**
   - **Approve:** If it looks good, click **"Approve & Execute"**. The agent will resume and write the allocations to the PostgreSQL database.
   - **Reject with Feedback:** If a match is bad, write feedback in the notes section (e.g., "Diana is too junior for this architect role, find someone else") and reject it. The LLM will revise the plan based on your feedback and present a new draft for you to review!

---

## AI Observability Platform

The backend is wired to [`ai-observability-sdk`](https://pypi.org/project/ai-observability-sdk/).
Each forecast run becomes one **execution** in the platform dashboard with the
LangGraph node tree, LLM/retriever spans, token counts, cost, failure
classification, retries and loop detection. HITL approvals/rejections reuse the
same `workflow_id` (the forecast-run id), so they join the original trace.

### 1. Install + configure

```bash
cd bench_forecast
source venv/bin/activate
pip install -r requirements.txt          # includes ai-observability-sdk
pip install "ai-observability-sdk[mock]" # only needed for the aiobs-mock smoke test
```

Add to `bench_forecast/.env` (see `.env.example`):

```dotenv
AI_OBSERVABILITY_API_KEY=<ingest-key>
AI_OBSERVABILITY_ENDPOINT=http://localhost:8000   # platform base URL
# AI_OBSERVABILITY_CAPTURE_PROMPTS=true           # opt-in payload capture
# AI_OBSERVABILITY_ENABLED=false                  # temporary kill switch
```

> **Port clash:** Bench Forecast also defaults to `API_PORT=8000`. If the local
> platform stack runs on 8000, start the backend on another port
> (`API_PORT=8001`) or point `AI_OBSERVABILITY_ENDPOINT` at the platform's real
> URL. The SDK appends `/v1/traces` itself.

Tracing is disabled automatically when neither a key nor an endpoint is set, so
local development without the platform is unaffected. The SDK is optional at
runtime: if the package is missing, the app logs and continues.

### 2. Smoke-test connectivity

```bash
aiobs-mock --endpoint http://localhost:8000 --api-key <your-key>
```

Expected: `30/30 scenarios passed`, then ~30 executions appear in the
dashboard. If this fails, fix the endpoint/key before running a forecast.

### 3. Where the integration lives

| Concern | File |
|---|---|
| `init()` / `workflow()` / `span()` / flush + shutdown wrapper | `bench_forecast/src/core/observability.py` |
| Early init, shutdown hook, `/health` export stats | `bench_forecast/src/api/main.py` |
| Forecast run workflow (`workflow_id` = forecast-run id) | `bench_forecast/src/services/forecast_runner.py` |
| HITL approve/reject workflows | `bench_forecast/src/services/hitl_service.py` |
| Context propagation into the parallel LLM thread pools | `bench_forecast/src/agents/nodes.py` |

LangChain instrumentation is owned by the SDK. The old import-time
`LangChainInstrumentor().instrument()` call was removed to avoid double
instrumentation; legacy Arize Phoenix is only used when the platform SDK is not
active. Because the app uses `ChatOllama`/`ChatGroq` (which call the raw clients
internally), the SDK's raw Ollama/Groq interceptors are disabled by default —
otherwise every LLM call would export two spans and double token/cost totals.
Set `AI_OBSERVABILITY_RAW_CLIENT_TRACING=true` only if you add direct client
calls.

### 4. Verify

- `GET /api/v1/health` now includes an `observability` block with
  `enabled`, `exported_batches`, `failed_batches` and `last_error`.
- Run a forecast from the UI. In **Engineering → Executions** you should see
  `bench-forecast` with status, duration, tokens, cost and the span tree
  (`planner_graph` → LangGraph nodes → LLM/RETRIEVER spans).
- `failed_batches` climbing means a wrong endpoint, revoked key or network
  issue — check `last_error` (`401` = bad key, `409` = conflicting project
  header).
- Prompts/responses are **not** captured unless
  `AI_OBSERVABILITY_CAPTURE_PROMPTS=true` or a per-workflow override is used.

Servers flush on FastAPI shutdown (`ai_observability.shutdown()`); scripts such
as `scripts/eval_forecast.py` call `flush()` explicitly.

