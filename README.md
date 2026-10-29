# Bench Forecast 2.0

An agentic workforce allocation platform with:

- RAG-based CV ↔ demand matching
- fractional daily-hour allocations across concurrent projects
- a hard 8h/day capacity cap
- cumulative, non-destructive skill tracking
- staged CV review with manager approval and ChromaDB re-embedding
- project/role staffing visibility for managers and admins
- training and upskilling pathways
- asynchronous, outbox-backed email notifications
- Human-In-The-Loop (HITL) approval and feedback

The backend is FastAPI + PostgreSQL + ChromaDB. The frontend is React + TypeScript + Vite.

---

## 1. Current Capabilities

### Workforce and capacity

- Employees have `daily_capacity_hours`:
  - full-time and contractors: `8.0`
  - part-time: `4.0`
  - absolute hard cap: `8.0h/day`
- Demands have `hours_per_day`, seniority, required skills and headcount.
- Allocations have `allocated_hours_per_day`.
- `allocation_pct = allocated_hours_per_day / daily_capacity_hours * 100`.
- Multiple concurrent project assignments are supported, e.g. `4h + 4h`.
- Capacity validation:
  - `committed = active + approved_by_manager`
  - `reserved = proposed_by_ai + pending_manager_approval`
  - `remaining = effective_capacity - committed - reserved`
  - `NULL` start/end dates are treated as ongoing
  - updating an allocation excludes its own previous hours from the check
- Over-capacity requests return HTTP `409` with structured detail:

```json
{
  "detail": {
    "code": "capacity_exceeded",
    "message": "...",
    "remaining_available_hours": 0.0,
    "daily_capacity_hours": 8.0,
    "committed_hours_per_day": 8.0,
    "reserved_hours_per_day": 0.0,
    "requested_hours_per_day": 1.0
  }
}
```

### Projects and staffing

- Managers and admins can view every company project.
- Project detail includes:
  - client, lifecycle status, win probability, budget, target margin
  - daily demanded vs allocated hours
  - filled vs required seats
  - role roster with statuses:
    - `open`
    - `partially_filled`
    - `filled`
    - `requires_training`
  - assigned talent, allocated hours, remaining hours and AI match score
- Manual assignment:
  - managers can only assign their direct reports (`manager_id == user.employee_id`)
  - admins can assign anyone
- Candidate drawer returns only people with enough remaining daily hours.

### Cumulative skills

- Skills carry provenance:
  - `source`: `cv | project | training | manual`
  - `source_ref`: document / demand / training UUID
  - `is_locked`: true for project-earned skills
- Merges are set unions and never delete existing skills.
- Project-earned skills are locked and cannot be pruned or downgraded by a later CV upload.
- Every provenance change is recorded in `employee_skill_evidence`.

### CV approval workflow

- Employee upload → staged as `pending_approval`.
- Staging does **not** modify:
  - `employees.profile_text`
  - active skills
  - ChromaDB
- Manager review:
  - new skills
  - retained skills
  - locked project-earned skills
  - summary before/after
  - original PDF link
- Approval:
  - applies the skill union
  - preserves locked project skills
  - rebuilds the active profile text
  - re-embeds the employee in ChromaDB
  - marks previous CVs `is_current=false`
- Rejection:
  - stores manager feedback
  - leaves the active profile and ChromaDB untouched
- Managers uploading their own CV are auto-approved.
- Admin accounts do not upload personal CVs.

### Trainings

- Training assignments include title, targeted skills, AI upskilling path, status and target completion date.
- Completing training adds targeted skills with `source='training'`.
- HITL train proposals create an active training assignment on approval.

### Email notifications

- Transactional outbox in `email_notifications`.
- Delivery runs after commit through FastAPI BackgroundTasks.
- Sender providers:
  - `console` (default, development-safe)
  - `smtp` (`smtplib` wrapped in `asyncio.to_thread`)
  - `sendgrid` (HTTP API)
- Delivery failures never roll back business transactions.
- Recipient resolution: `users.email` → `employees.email`; invalid/missing emails become `skipped`.
- Triggers:
  - `employee_allocated`
  - `training_assigned`
  - `cv_approval_needed`
  - `cv_approved`
  - `cv_rejected`
  - `capacity_warning`
- Capacity scan deduplicates per `(employee_id, event_type, date)`.

---

## 2. Architecture at a Glance

```text
React + Vite frontend
        │
        ▼
FastAPI API (/api/v1)
        │
        ├── PostgreSQL (authoritative domain data)
        │     employees, skills, projects, demands, allocations,
        │     documents, CV reviews, trainings, notifications, finance
        │
        ├── ChromaDB (employee_profiles collection)
        │     CV / profile chunks + embeddings
        │
        ├── Ollama (nomic-embed-text)
        │     embeddings for seeding and CV approval
        │
        ├── MinIO / local storage
        │     raw CV files
        │
        └── optional ai-observability-sdk
              forecast run / LLM span telemetry
```

Key invariants:

- No embedding columns in PostgreSQL; ChromaDB is the vector store.
- Business transactions and notification delivery are decoupled.
- Manager project visibility is global; assignment authority is scoped.
- Project-earned skills are locked.
- The 8h/day cap is enforced at API, service and database levels.

---

## 3. Repository Layout

```text
Bench-Forecast/
├── Makefile
├── README.md
├── .env.example
├── requirements.txt
├── docker-compose.yml
├── bench_forecast/
│   ├── alembic/
│   │   └── versions/
│   ├── scripts/
│   │   ├── seed_simulation.py
│   │   ├── demo_catalog.py
│   │   ├── backfill_usability.py
│   │   ├── check_db.py
│   │   ├── migrate.py
│   │   └── eval_forecast.py
│   ├── src/
│   │   ├── agents/            # LangGraph nodes and state
│   │   ├── api/               # FastAPI app + routers
│   │   ├── auth/              # JWT + demo account bootstrap
│   │   ├── core/              # config, LLM, observability
│   │   ├── database/          # models, sessions, Chroma/RAG
│   │   ├── ingestion/         # PDF extraction, embeddings, Chroma writes
│   │   ├── schemas/           # Pydantic request/response models
│   │   └── services/          # workforce, skills, CV, training, notifications, HITL, finance
│   ├── tests/
│   ├── chroma_data/           # embedded Chroma persistence (default)
│   └── storage/cv/            # local CV fallback
├── frontend/
│   ├── src/
│   │   ├── api/               # typed API client
│   │   ├── components/        # projects, CV, trainings, shared UI
│   │   ├── contexts/          # AuthContext
│   │   ├── hooks/
│   │   ├── pages/
│   │   └── types/
│   └── package.json
├── storage/cvs/
└── docs/
```

---

## 4. Roles and Demo Accounts

Three roles are supported:

| Role | Scope |
|---|---|
| `admin` | Global dashboard, all projects, all people, all CV approvals, trainings, bench, AI queue, settings |
| `manager` | Direct reports, team workload, all project visibility, direct-report assignment, CV approvals, trainings |
| `employee` | Personal portal: workload, trainings, profile/CV |

After `make seed`:

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin` |
| People Manager | `manager` | `manager` |
| Employee | generated `first_last`, e.g. `aaron_nguyen` | `employee` |

All generated employee accounts use password `employee`.  
All generated team-lead manager accounts use password `manager`.

These are development credentials. Change them before any real deployment.

---

## 5. Prerequisites

- Docker + Docker Compose
- Python 3.12
- Node.js + npm
- Ollama with `nomic-embed-text` for embeddings:

```bash
ollama pull nomic-embed-text
ollama serve
```

Optional:

- MinIO / S3 for object storage (local filesystem fallback is built in)
- Chroma server mode via Docker (embedded persistence is the default)
- AI observability platform SDK

---

## 6. Quick Start

All commands run from the repository root.

```bash
cp .env.example .env
make setup                 # backend deps into bench_forecast/venv + npm install
make migrate               # apply Alembic migrations
make backfill              # non-destructive backfill (optional on a fresh seed)
make seed                  # start Docker Postgres, migrate, reseed demo data
make db-check              # connection + row counts + consistency checks
```

Start the two processes in separate terminals:

```bash
make api                   # FastAPI: http://localhost:8000
make web                   # Vite:    http://localhost:3000
```

Open:

- UI: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/api/v1/health`

### Make targets

| Target | Purpose |
|---|---|
| `make setup` | Install backend + frontend dependencies |
| `make db-up` | Start the Docker PostgreSQL |
| `make migrate` | Apply Alembic migrations |
| `make backfill` | Non-destructive skills/manager/hours backfill |
| `make seed` | Reset and reseed demo data |
| `make db-check` | Connection, counts, Alembic head and capacity checks |
| `make api` | Run FastAPI on `:8000` |
| `make web` | Run Vite on `:3000` |
| `make test` | Run backend tests |

---

## 7. Environment Configuration

One shared `.env` at the repository root. See `.env.example` for the full list.

Key groups:

- **LLM**
  - `LLM_PROVIDER=ollama | groq | openai_compatible`
  - `OLLAMA_*`, `GROQ_*`, `LLM_*`
- **Embeddings**
  - `EMBEDDING_MODEL=nomic-embed-text`
  - `OLLAMA_BASE_URL`
- **PostgreSQL**
  - `POSTGRES_HOST`, `POSTGRES_PORT=5434`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- **Chroma**
  - `CHROMA_PERSIST_DIR`, optional `CHROMA_HOST` / `CHROMA_PORT`
- **MinIO**
  - `MINIO_*` (local filesystem fallback if unavailable)
- **Auth**
  - `JWT_SECRET_KEY` (at least 32 bytes in production)
- **Email**
  - `EMAILS_ENABLED`, `EMAIL_PROVIDER=console|smtp|sendgrid`
  - `EMAIL_FROM`, `SMTP_*`, `SENDGRID_API_KEY`
- **Observability**
  - `AI_OBSERVABILITY_*`, `LANGCHAIN_*`, legacy `PHOENIX_*`

Development defaults are safe:

- email provider is `console`
- Chroma uses embedded persistence
- AI observability is disabled when no key/endpoint is configured

---

## 8. Database

### Docker is the source of truth

PostgreSQL runs in Docker on host port **5434**:

```bash
docker compose up -d postgres
docker compose ps postgres
make db-check
```

Port `5432` is deliberately avoided because local PostgreSQL installations commonly own it and silently serve stale data.

### Alembic

Current migration chain:

```text
806bfe6b3200  baseline schema
9f3c1a7b5d20  usability & capability expansion   ← current head
```

Apply migrations:

```bash
make migrate
# or
cd bench_forecast && ./venv/bin/python -m scripts.migrate
```

`scripts/migrate.py` tolerates databases created by `init_db_sync()` (SQLAlchemy `create_all`) by stamping the baseline when needed. For schema changes, Alembic remains required because `create_all` does not alter existing columns.

### Main tables

```text
Core HR:
  departments, employees, skills, employee_skills,
  employee_skill_evidence

Documents / CV:
  documents, cv_review_requests

Projects / staffing:
  projects, project_demands, project_roles (legacy), allocations

Training:
  training_assignments

Auth:
  users

Notifications:
  email_notifications

Forecasting / HITL:
  forecast_runs, hitl_review_events, hiring_recommendations

Finance / KPIs:
  employee_costs, bench_history, bench_costs, kpi_snapshots
```

### Capacity invariants

- `employees.daily_capacity_hours > 0 AND <= 8`
- `project_demands.hours_per_day > 0 AND <= 8`
- `allocations.allocated_hours_per_day > 0 AND <= 8`
- Employee cumulative committed hours cannot exceed capacity.
- `allocation_pct` is derived from hours and capacity.

### Backfill

`make backfill` is non-destructive and safe to re-run. It:

- normalizes legacy `employees.skills` into `skills` + `employee_skills`
- fills daily capacity
- converts legacy allocation percentages to hours
- grants project-earned locked skills from historical allocations
- backfills project clients/lifecycle dates
- assigns a deterministic manager hierarchy if missing

---

## 9. Demo Data and Seeding

```bash
make seed
# faster DB-only seed, no embeddings:
cd bench_forecast && SEED_EMBEDDINGS=false ./venv/bin/python -m scripts.seed_simulation
```

The seeder is deterministic (`random.seed(42)`, Faker seed 42) and destructive for domain tables.

Current seeded state:

```text
employees                 100
projects                  11
project_demands           46
allocations               213
employee_skills           1177
employee_skill_evidence   636
training_assignments      18
cv_review_requests        3
email_notifications       0
forecast_runs             8
hitl_review_events        40
users                     101  (1 admin, 12 managers, 88 employees)
```

Demand status mix:

```text
open                 14
filled               29
partially_filled      2
requires_training     1
```

Seeded data includes:

- full-time (8h), part-time (4h) and contractor (8h) employees
- a 12-team-lead manager hierarchy (99/100 employees have a manager)
- project history, active staffing and pipeline projects
- fractional 4h/2h roles and multi-project allocations
- 469 locked project-earned skills
- 18 training assignments (14 assigned, 4 completed)
- 2 pending CV reviews and 1 rejected example
- forecasts, HITL events, bench costs, bench history and KPI snapshots

Embeddings are refreshed in the shared Chroma collection (`employee_profiles`) under `bench_forecast/chroma_data`.

> **Warning:** `make seed` deletes domain rows.  
> Avoid `docker compose down -v`; it deletes the persistent Postgres/Chroma/MinIO volumes.

---

## 10. Core Workflows

### 10.1 Forecast and HITL

1. Open **AI Action Queue**.
2. Click **Run Forecast**.
3. The LangGraph pipeline:
   - extracts active employees with remaining capacity
   - fetches `open` and `partially_filled` demands
   - retrieves candidates via ChromaDB
   - evaluates margin, skills and remaining hours
   - produces pending allocation proposals
4. Review proposals.
5. Approve or reject:
   - approval revalidates capacity, grants project skills, may create training assignments
   - rejection accepts feedback and can generate a replacement proposal or hiring recommendation

### 10.2 Project and roster management

- **Projects** lists every company project.
- **Project Detail** (`/projects/:id`) shows:
  - project summary
  - daily-hours staffing bar
  - seats filled vs required
  - full role roster
  - assigned employee cards
  - Assign Talent drawer
  - AI recommendations panel

### 10.3 Fractional allocation

- Managers view all projects but assign only direct reports.
- Admins assign anyone.
- The candidate drawer filters by remaining hours.
- `4h + 4h` is valid for an 8h employee.
- `4h + 5h`, or an 8h demand for a 4h employee, returns `409 capacity_exceeded`.

### 10.4 CV review

Employee flow:

1. Upload CV PDF.
2. Document is staged as `pending_approval`.
3. Manager is notified.
4. Manager opens **CV Approvals**:
   - sees new/retained/project-earned skills
   - sees summary before/after
   - opens the original PDF
5. Approve or reject.
6. Employee portal shows:
   - `Live`
   - `Pending Manager Approval`
   - `Changes Requested` with manager comments

### 10.5 Trainings

- Create from the **Trainings** page.
- Employee sees assignments in **Trainings & Upskilling**.
- Completing a training adds targeted skills with `source='training'`.

### 10.6 Email outbox

Manual capacity scan:

```bash
curl -X POST http://localhost:8000/api/v1/notifications/capacity-scan \
  -H "Authorization: Bearer <admin-token>"
```

Outbox audit:

```http
GET /api/v1/notifications
```

Retry:

```http
POST /api/v1/notifications/{notification_id}/retry
```

The capacity scan is currently admin-triggered; `CAPACITY_SCAN_INTERVAL_HOURS` is reserved for a future scheduler.

---

## 11. API Surface

### Auth

```http
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

### Employees and supply

```http
GET /api/v1/employees
GET /api/v1/demands
GET /api/v1/employees/{id}/capacity
GET /api/v1/employees/{id}/skills
PATCH /api/v1/employees/{id}/schedule
```

### Projects and demands

```http
GET   /api/v1/projects
GET   /api/v1/projects/{id}
GET   /api/v1/projects/{id}/roster
GET   /api/v1/projects/{id}/recommendations?demand_id=...
POST  /api/v1/projects/{id}/demands
PATCH /api/v1/projects/{id}/demands/{demand_id}
POST  /api/v1/projects/{id}/demands/{demand_id}/assign
```

### Team

```http
GET /api/v1/team
GET /api/v1/team/workload
GET /api/v1/team/candidates?demand_id=...
```

### Allocations / HITL

```http
GET  /api/v1/allocations
POST /api/v1/allocations/assign
PATCH /api/v1/allocations/{id}
POST /api/v1/allocations/{id}/release
GET  /api/v1/allocations/pending
POST /api/v1/allocations/{id}/approve
POST /api/v1/allocations/{id}/reject
```

### CV reviews

```http
GET  /api/v1/cv-reviews
GET  /api/v1/cv-reviews/pending
GET  /api/v1/cv-reviews/{id}
POST /api/v1/cv-reviews/{id}/approve
POST /api/v1/cv-reviews/{id}/reject
GET  /api/v1/employees/{id}/cv-status
```

### Documents

```http
POST /api/v1/documents/upload
GET  /api/v1/documents/employee/{employee_id}
GET  /api/v1/documents/employee/{employee_id}/download
```

### Trainings

```http
GET   /api/v1/trainings
POST  /api/v1/trainings
PATCH /api/v1/trainings/{id}
POST  /api/v1/trainings/{id}/complete
```

### Notifications

```http
POST /api/v1/notifications/capacity-scan
GET  /api/v1/notifications
POST /api/v1/notifications/{id}/retry
```

### Forecast / finance / analytics

```http
POST /api/v1/forecast/run
GET  /api/v1/forecast/runs/{run_id}
GET  /api/v1/analytics/bench-trend
GET  /api/v1/analytics/bench-burn-trend
GET  /api/v1/analytics/kpi-trend
GET  /api/v1/analytics/project-margins
GET  /api/v1/finance/...
```

---

## 12. Frontend

### Role-aware navigation

**Admin**
- Dashboard
- Projects
- Team Workload
- CV Approvals
- Trainings
- Bench & Pipeline
- AI Action Queue
- Settings

**Manager**
- My Team
- Projects
- CV Approvals
- Trainings
- Bench
- AI Action Queue
- Settings

**Employee**
- Projects & Workload
- Trainings & Upskilling
- Profile & CV

The CV Approvals nav item shows a pending-review badge that refreshes on approval/rejection and every 30 seconds.

### Routes

```text
/                 Admin dashboard; manager redirects to /team
/projects         Project directory
/projects/:id     Project detail + roster
/team             Team workload / My Team
/approvals        CV approval queue
/trainings        Training management
/bench            Bench & Pipeline
/queue            AI Action Queue
/queue/:id        HITL review
/settings         Forecast settings
```

Employee portal is a single role-specific screen with three tabs.

### Key frontend components

```text
components/projects/ProjectSummaryCard.tsx
components/projects/RoleRosterTable.tsx
components/projects/AssignTalentDrawer.tsx
components/projects/AIMatchRecommendationsPanel.tsx
components/cv/CVDiffView.tsx
components/cv/CVStatusCard.tsx
components/team (via Team page)
components/trainings/TrainingCard.tsx
components/shared/CapacityBar.tsx
components/shared/Badges.tsx
```

---

## 13. Testing & Verification

### Backend

```bash
make test
# or
cd bench_forecast && ./venv/bin/python -m pytest -q
```

Current result:

```text
52 passed
```

Coverage includes:

- fractional multi-project allocation / 8h cap
- `exclude_allocation_id` on updates
- part-time capacity rejection
- project-skill lock/union behavior
- staged CV approval and rejection
- CV diff/project-skill retention
- training lifecycle
- HITL approval/rejection and train assignment creation
- notification outbox delivery success/failure/skip
- capacity scan daily deduplication
- manager/admin candidate scoping
- forecast node behavior

### Frontend

```bash
cd frontend
npm run build
```

Current result: TypeScript + Vite production build passes.  
Only the non-blocking Vite large-chunk advisory is emitted.

### Database consistency

```bash
make db-check
```

The script prints:

- PostgreSQL host/port/database
- Alembic head
- row counts
- capacity violations
- manager coverage
- locked project-skill count

Current result:

```text
alembic_head  : 9f3c1a7b5d20
allocations_over_8h                        0
allocations_non_positive_hours             0
employees_over_8h_capacity                 0
demands_over_8h                            0
employees_over_capacity_committed          0
employees_over_capacity_including_reserved 0
employees_with_manager                     99
project_earned_locked_skills               469

PASS: schema + capacity consistency checks are clean
```

### Verified end-to-end scenarios

- 8h employee assigned `4h + 4h` → portal shows 100% utilization
- additional 1h assignment → `409 capacity_exceeded`
- 4h part-time employee assigned an 8h demand → `409`
- project-earned skills remain locked after CV approval
- employee CV stays `pending_approval` until manager action
- approval re-embeds ChromaDB and preserves locked skills
- rejection leaves active profile untouched and surfaces manager feedback
- training assignment appears in portal and outbox
- training completion grants `source='training'` skill
- roster shows all four statuses
- manager candidate drawer is direct-report scoped
- admin candidate drawer is company-wide
- all notification event types are written
- second capacity scan is fully deduplicated

---

## 14. AI Observability

The backend can export forecast runs and LLM spans to
[`ai-observability-sdk`](https://pypi.org/project/ai-observability-sdk/).
Each forecast run becomes one execution; HITL decisions reuse the forecast-run
`workflow_id`.

Configure in `.env`:

```dotenv
AI_OBSERVABILITY_API_KEY=<ingest-key>
AI_OBSERVABILITY_ENDPOINT=http://localhost:8000
# AI_OBSERVABILITY_CAPTURE_PROMPTS=true
# AI_OBSERVABILITY_ENABLED=false
```

Notes:

- Use `ai-observability-sdk>=0.3.1` (it deduplicates `ChatOllama`/`ChatGroq`
  spans against the raw clients). The wrapper remains compatible with 0.2.x.
- Tracing is disabled automatically when neither key nor endpoint is set.
- The SDK is optional at runtime.
- The startup endpoint preflight is advisory: an unreachable collector logs a
  warning and tracing still initialises; check
  `GET /api/v1/health → observability` for export health. Set
  `AI_OBSERVABILITY_PREFLIGHT_STRICT=true` for fail-fast behaviour.
- If the local observability platform also uses port 8000, run the backend on
  `API_PORT=8001` or change the observability endpoint.
- The SDK appends `/v1/traces` itself.
- Legacy Arize Phoenix is only used when the platform SDK is not active.
- Forecast runs, HITL reviews, CV uploads and CV approvals each run inside one
  `workflow()`, so their LLM/RETRIEVER spans render as one execution.

Relevant files:

| Concern | File |
|---|---|
| SDK init / workflow / span / flush | `bench_forecast/src/core/observability.py` |
| App startup/shutdown and health stats | `bench_forecast/src/api/main.py` |
| Forecast workflow | `bench_forecast/src/services/forecast_runner.py` |
| HITL workflows | `bench_forecast/src/services/hitl_service.py` |
| Parallel LLM context propagation | `bench_forecast/src/agents/nodes.py` |

`GET /api/v1/health` includes an `observability` block with enabled status,
exported/failed batches and last error.

Optional smoke test:

```bash
./bench_forecast/venv/bin/python -m pip install "ai-observability-sdk[mock]"
aiobs-mock --endpoint http://localhost:8000 --api-key <your-key>
```

---

## 15. Useful Scripts

| Script | Purpose |
|---|---|
| `bench_forecast/scripts/seed_simulation.py` | Deterministic full demo seed |
| `bench_forecast/scripts/demo_catalog.py` | Project/demand reference catalogue |
| `bench_forecast/scripts/backfill_usability.py` | Non-destructive idempotent data backfill |
| `bench_forecast/scripts/check_db.py` | Health + capacity consistency report |
| `bench_forecast/scripts/migrate.py` | Alembic wrapper with baseline stamping |
| `bench_forecast/scripts/eval_forecast.py` | Forecast evaluation helper |

---

## 16. Troubleshooting

### `make db-check` shows port 5432

The Docker database is exposed on **5434**. Fix `POSTGRES_PORT` in `.env` and restart the API. A local PostgreSQL instance is likely answering on 5432.

### ChromaDB is empty

Run `make seed` with Ollama available. The seeder embeds 100 employee profiles. If you only need database data:

```bash
cd bench_forecast
SEED_EMBEDDINGS=false ./venv/bin/python -m scripts.seed_simulation
```

### CV approval fails at the embedding stage

Ensure Ollama is running and `nomic-embed-text` is pulled:

```bash
ollama serve
ollama pull nomic-embed-text
```

A failed embedding leaves the CV review pending; it does not partially apply the profile.

### Email notifications are not appearing

The default provider is `console`, so deliverable emails are printed to the backend log. Check:

- `EMAILS_ENABLED=true`
- `email_notifications.status`
- `email_notifications.error`
- `GET /api/v1/notifications`

### `docker compose down -v`

Do not use `-v` unless you intend to delete persistent PostgreSQL, Chroma, MinIO and Phoenix data. Use `docker compose down`.

### Alembic says tables already exist

Use `make migrate` rather than raw `alembic upgrade`. The wrapper detects an
`init_db_sync()`-created schema without an `alembic_version` row and stamps the
baseline before continuing.

### Legacy data

The schema keeps a few backward-compatibility fields:

- `employees` legacy columns such as `skills`, `current_project`, `hourly_cost_rate`
- `project_roles`
- `projects.probability` / lifecycle strings

The current code uses `employee_skills`, `project_demands`, `allocations`, and
the capacity/training/CV tables as the authoritative model.

---

## 17. Known Non-Blocking Warnings

- Python `datetime.utcnow()` deprecation warnings in older code paths
- FastAPI `on_event` deprecation warnings
- Vite chunk-size advisory (`> 500 kB`)

None of these currently fail tests or the production build.
