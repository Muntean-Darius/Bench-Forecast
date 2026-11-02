"""FastAPI backend for Bench Forecast allocation recommendations."""

import logging
from typing import Any, Dict
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import Config

# ---------------------------------------------------------------------------
# Observability — MUST run before routers (and therefore LangChain clients)
# are imported. The SDK installs the OpenInference instrumentors here; a
# late init would miss the first clients and detach spans from the workflow.
# ---------------------------------------------------------------------------
from src.core.observability import (
    init_observability,
    observability_status,
    shutdown_observability,
)
from src.core.tracing import setup_phoenix_tracing

logger = logging.getLogger(__name__)

_OBSERVABILITY_ACTIVE = init_observability()

# Legacy Arize Phoenix is only used when the platform SDK is not active;
# running both would duplicate every LLM/chain span.
if not _OBSERVABILITY_ACTIVE:
    setup_phoenix_tracing()
else:
    logger.info("AI observability SDK active — legacy Phoenix tracer skipped")

from src.database.vector_store import VectorStoreManager
from src.api.routers.documents import router as documents_router
from src.api.routers.finance import router as finance_router
from src.api.routers.hitl import router as hitl_router
from src.api.routers.forecast import router as forecast_router
from src.api.routers.analytics import router as analytics_router
from src.api.routers.projects import router as projects_router
from src.api.routers.team import router as team_router
from src.api.routers.cv_reviews import router as cv_reviews_router
from src.api.routers.trainings import router as trainings_router
from src.api.routers.notifications import router as notifications_router


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Bench Forecast API",
    version="2.0.0",
    description="Agentic workforce allocation engine with RAG matching and HITL feedback loop",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(documents_router)
app.include_router(finance_router)
app.include_router(hitl_router)
app.include_router(forecast_router)
app.include_router(analytics_router)
app.include_router(projects_router)
app.include_router(team_router)
app.include_router(cv_reviews_router)
app.include_router(trainings_router)
app.include_router(notifications_router)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    """Initialize on app startup: validate config and auto-seed ChromaDB."""
    logger.info("=" * 60)
    logger.info("🚀 Bench Forecast API v2.0 Starting")
    logger.info("=" * 60)
    logger.info(f"LLM Provider: {Config.LLM_PROVIDER}")
    if Config.LLM_PROVIDER == "groq":
        logger.info(f"Groq Model: {Config.GROQ_MODEL}")
    elif Config.LLM_PROVIDER == "deepseek":
        logger.info(
            f"DeepSeek Model: {Config.DEEPSEEK_MODEL} @ {Config.DEEPSEEK_BASE_URL}"
        )
    elif Config.LLM_PROVIDER == "openai_compatible":
        logger.info(f"OpenAI-compatible Model: {Config.LLM_MODEL} @ {Config.LLM_BASE_URL}")
    else:
        logger.info(f"Ollama Model: {Config.OLLAMA_MODEL} @ {Config.OLLAMA_BASE_URL}")
    logger.info(
        f"Database: postgresql://{Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/"
        f"{Config.POSTGRES_DB}"
    )
    logger.info(
        "AI observability: "
        f"{'Enabled' if _OBSERVABILITY_ACTIVE else 'Disabled'}"
    )
    logger.info(
        f"Legacy Phoenix Tracing: "
        f"{'Enabled' if (Config.ENABLE_PHOENIX and not _OBSERVABILITY_ACTIVE) else 'Disabled'}"
    )
    logger.info("=" * 60)
    Config.validate()

    # Ensure any newly added tables exist (idempotent — only creates missing).
    try:
        from src.database.database import init_db_sync
        init_db_sync()
        logger.info("✓ Database schema verified/initialised")
    except Exception as e:
        logger.warning(f"DB schema init failed (non-fatal): {e}")

    from src.auth.auth import init_auth_db
    init_auth_db()

    # ChromaDB is populated by the seeder (`make seed`). Report status only.
    try:
        vector_mgr = VectorStoreManager()
        count = vector_mgr.collection.count()
        if count == 0:
            logger.warning(
                "ChromaDB is empty — run `make seed` to load employee profiles"
            )
        else:
            logger.info(f"✓ ChromaDB ready ({count} employee profiles)")
    except Exception as e:
        logger.warning(f"ChromaDB check failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown():
    """Flush pending telemetry and uninstrument frameworks on app stop."""
    shutdown_observability()


# ---------------------------------------------------------------------------
# Auth endpoints (Moved from inline to keep functionality if needed, ideally in an auth router but sticking to existing structure)
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from fastapi import HTTPException, Header

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/v1/auth/login", status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest):
    from src.auth.auth import authenticate_user, create_access_token
    user = authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["id"], "username": user["username"], "role": user["role"]})
    return {"token": token, "user": user}

@app.get("/api/v1/auth/me", status_code=status.HTTP_200_OK)
async def get_current_user_info(authorization: str = Header(None)):
    from src.auth.auth import verify_token, get_user_by_id
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.replace("Bearer ", "").strip()
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ---------------------------------------------------------------------------
# Employee Portal endpoints (Legacy kept for compatibility)
# ---------------------------------------------------------------------------

@app.get("/api/v1/employee/portal/{employee_id}", status_code=status.HTTP_200_OK)
async def get_employee_portal(employee_id: str):
    """Get employee's current project and next assignment info."""
    from src.database.database import get_sync_db
    from src.database.models import (
        Employee,
        EmployeeCost,
        Allocation,
        ProjectDemand,
        DemandStatusEnum,
        AllocationStatusEnum,
        CvApprovalStatusEnum,
        CvReviewRequest,
        Document,
        DocumentTypeEnum,
        TrainingAssignment,
        TrainingStatusEnum,
    )
    from datetime import date
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload
    import uuid

    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid employee UUID")

    try:
        with get_sync_db() as session:
            emp = session.execute(
                select(Employee).where(Employee.id == emp_uuid)
            ).scalar_one_or_none()
            if not emp:
                raise HTTPException(status_code=404, detail="Employee not found")

            cost = session.execute(
                select(EmployeeCost).where(EmployeeCost.employee_id == emp_uuid)
            ).scalars().first()

            # Only return completed (Done) and approved/active (Assigned) allocations.
            # Exclude proposals pending manager approval or rejected.
            alloc_rows = session.execute(
                select(Allocation)
                .options(
                    joinedload(Allocation.project_demand).joinedload(ProjectDemand.project)
                )
                .where(
                    Allocation.employee_id == emp_uuid,
                    Allocation.status.in_([
                        AllocationStatusEnum.approved_by_manager,
                        AllocationStatusEnum.active,
                        AllocationStatusEnum.completed,
                    ])
                )
                .order_by(Allocation.start_date.desc().nulls_last())
            ).scalars().all()

            allocations = []
            for a in alloc_rows:
                # Allocation has no `project_name` column; resolve the display
                # name from the linked demand → project, then fall back to the
                # role label so the portal never raises an AttributeError.
                if a.project_demand and a.project_demand.project:
                    proj_name = a.project_demand.project.name
                elif a.role_on_project:
                    proj_name = a.role_on_project
                else:
                    proj_name = "Assigned Project"

                raw_st = a.status.value if hasattr(a.status, 'value') else str(a.status)
                status_label = "Done" if raw_st == AllocationStatusEnum.completed.value else "Assigned"

                allocations.append({
                    "id": str(a.id),
                    "project_name": proj_name,
                    "project_id": str(a.project_id) if a.project_id else None,
                    "role": a.role_on_project,
                    "role_on_project": a.role_on_project,
                    "allocation_pct": float(a.allocation_pct),
                    "allocated_hours_per_day": float(
                        a.allocated_hours_per_day or 0
                    ),
                    "start_date": str(a.start_date) if a.start_date else None,
                    "end_date": str(a.end_date) if a.end_date else None,
                    "status": status_label,
                    "raw_status": raw_st,
                    "ai_justification": a.ai_justification,
                })

            demand_rows = session.execute(
                select(ProjectDemand).where(
                    ProjectDemand.status.in_([
                        DemandStatusEnum.open,
                        DemandStatusEnum.partially_filled,
                    ])
                )
            ).scalars().all()

            open_demands = []
            for d in demand_rows:
                skills = []
                if isinstance(d.required_skills, list):
                    skills = d.required_skills
                elif isinstance(d.required_skills, dict):
                    skills = list(d.required_skills.keys())
                elif isinstance(d.required_skills, str):
                    skills = [s.strip() for s in d.required_skills.split(",") if s.strip()]

                open_demands.append({
                    "id": str(d.id),
                    "role": d.role,
                    "required_skills": skills,
                    "project_id": str(d.project_id),
                    "hours_per_day": float(d.hours_per_day or 0),
                    "seniority_level": (
                        d.required_experience_level.value
                        if d.required_experience_level is not None
                        and hasattr(d.required_experience_level, "value")
                        else None
                    ),
                    "start_date": str(d.needed_by_date) if d.needed_by_date else str(date.today()),
                    "win_probability": float(d.win_probability) if d.win_probability else 0.8,
                    "description": d.description or f"Demand for {d.role}",
                })

            skills = []
            if emp.skills:
                skills = [s.strip() for s in emp.skills.split(",") if s.strip()]
            elif emp.job_title:
                skills = [emp.job_title]

            full_name = f"{emp.first_name} {emp.last_name}".strip()
            cost_rate = float(cost.internal_hourly_cost) if cost and cost.internal_hourly_cost else (
                float(emp.hourly_cost_rate) if emp.hourly_cost_rate else 0.0
            )
            if cost_rate <= 0:
                sen_val = str(emp.experience_level.value).lower() if emp.experience_level else "mid"
                sen_defaults = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                cost_rate = sen_defaults.get(sen_val, 65.0)

            from src.services.workforce_service import summarize_allocations

            capacity = summarize_allocations(emp, emp.allocations)

            training_rows = session.execute(
                select(TrainingAssignment)
                .where(
                    TrainingAssignment.employee_id == emp_uuid,
                    TrainingAssignment.status.in_([
                        TrainingStatusEnum.assigned,
                        TrainingStatusEnum.in_progress,
                        TrainingStatusEnum.completed,
                    ]),
                )
                .order_by(TrainingAssignment.assigned_at.desc())
            ).scalars().all()
            trainings = [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "targeted_skills": t.targeted_skills or [],
                    "ai_upskilling_path": t.ai_upskilling_path,
                    "status": (
                        t.status.value if hasattr(t.status, "value") else str(t.status)
                    ),
                    "assigned_at": str(t.assigned_at) if t.assigned_at else None,
                    "target_completion_date": (
                        str(t.target_completion_date)
                        if t.target_completion_date
                        else None
                    ),
                    "completed_at": str(t.completed_at) if t.completed_at else None,
                    "notes": t.notes,
                }
                for t in training_rows
            ]

            pending_review = session.execute(
                select(CvReviewRequest)
                .where(
                    CvReviewRequest.employee_id == emp_uuid,
                    CvReviewRequest.status == CvApprovalStatusEnum.pending_approval,
                )
                .order_by(CvReviewRequest.requested_at.desc())
            ).scalars().first()
            latest_rejected = session.execute(
                select(CvReviewRequest)
                .where(
                    CvReviewRequest.employee_id == emp_uuid,
                    CvReviewRequest.status == CvApprovalStatusEnum.rejected,
                )
                .order_by(CvReviewRequest.reviewed_at.desc())
            ).scalars().first()
            current_doc = session.execute(
                select(Document)
                .where(
                    Document.employee_id == emp_uuid,
                    Document.document_type == DocumentTypeEnum.cv,
                    Document.approval_status == CvApprovalStatusEnum.approved,
                    Document.is_current.is_(True),
                )
                .order_by(Document.uploaded_at.desc())
            ).scalars().first()

            if pending_review is not None:
                cv_status = {
                    "state": "pending_approval",
                    "label": "Pending Manager Approval",
                    "review_id": str(pending_review.id),
                    "document_id": str(pending_review.document_id),
                    "version": None,
                    "manager_id": str(emp.manager_id) if emp.manager_id else None,
                    "manager_name": (
                        f"{emp.manager.first_name} {emp.manager.last_name}".strip()
                        if emp.manager else None
                    ),
                    "manager_comment": None,
                    "proposed_skills": pending_review.proposed_skills or [],
                    "submitted_at": str(pending_review.requested_at),
                }
            elif latest_rejected is not None and (
                current_doc is None
                or latest_rejected.reviewed_at is None
                or latest_rejected.reviewed_at > current_doc.uploaded_at
            ):
                cv_status = {
                    "state": "changes_requested",
                    "label": "Changes Requested",
                    "review_id": str(latest_rejected.id),
                    "document_id": str(latest_rejected.document_id),
                    "version": None,
                    "manager_id": str(emp.manager_id) if emp.manager_id else None,
                    "manager_name": (
                        f"{emp.manager.first_name} {emp.manager.last_name}".strip()
                        if emp.manager else None
                    ),
                    "manager_comment": latest_rejected.manager_comment,
                    "proposed_skills": latest_rejected.proposed_skills or [],
                    "submitted_at": str(latest_rejected.requested_at),
                }
            else:
                cv_status = {
                    "state": "live",
                    "label": "Live / Active",
                    "review_id": None,
                    "document_id": str(current_doc.id) if current_doc else None,
                    "version": current_doc.version if current_doc else None,
                    "manager_id": str(emp.manager_id) if emp.manager_id else None,
                    "manager_name": (
                        f"{emp.manager.first_name} {emp.manager.last_name}".strip()
                        if emp.manager else None
                    ),
                    "manager_comment": None,
                    "proposed_skills": [],
                    "submitted_at": str(current_doc.uploaded_at) if current_doc else None,
                }

            return {
                "employee": {
                    "id": str(emp.id),
                    "name": full_name,
                    "skills": skills,
                    "current_project": emp.current_project,
                    "available_from": str(emp.bench_start_date) if emp.bench_start_date else str(date.today()),
                    "experience_years": float(emp.experience_years) if emp.experience_years else 3.0,
                    "cost_rate": cost_rate,
                    "profile_text": emp.profile_text or "",
                    "employment_type": (
                        emp.employment_type.value
                        if hasattr(emp.employment_type, "value")
                        else str(emp.employment_type or "full_time")
                    ),
                    "daily_capacity_hours": float(capacity["daily_capacity_hours"]),
                    "committed_hours_per_day": float(capacity["committed_hours_per_day"]),
                    "remaining_available_hours": float(capacity["remaining_available_hours"]),
                    "utilization_pct": float(capacity["utilization_pct"]),
                    "manager_id": str(emp.manager_id) if emp.manager_id else None,
                    "manager_name": (
                        f"{emp.manager.first_name} {emp.manager.last_name}".strip()
                        if emp.manager else None
                    ),
                },
                "capacity": {
                    "daily_capacity_hours": float(capacity["daily_capacity_hours"]),
                    "committed_hours_per_day": float(capacity["committed_hours_per_day"]),
                    "reserved_hours_per_day": float(capacity["reserved_hours_per_day"]),
                    "remaining_available_hours": float(capacity["remaining_available_hours"]),
                    "utilization_pct": float(capacity["utilization_pct"]),
                },
                "allocations": allocations,
                "open_demands": open_demands,
                "trainings": trainings,
                "cv_status": cv_status,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Employee portal error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@app.post("/api/v1/employee/change-password", status_code=status.HTTP_200_OK)
async def employee_change_password(payload: ChangePasswordRequest, authorization: str = Header(None)):
    from src.api.deps import get_current_user
    from src.auth.auth import change_password
    user = await get_current_user(authorization)
    if not change_password(user["id"], payload.old_password, payload.new_password):
        raise HTTPException(status_code=400, detail="Wrong current password")
    return {"status": "password_changed"}


class UpdateProfileRequest(BaseModel):
    profile_text: str

@app.put("/api/v1/employee/profile/{employee_id}", status_code=status.HTTP_200_OK)
async def update_employee_profile(employee_id: str, payload: UpdateProfileRequest, authorization: str = Header(None)):
    """Update employee profile_text (CV content)."""
    from src.api.deps import get_current_user
    from src.auth.auth import update_cv_uri
    from src.database.database import get_sync_db
    from src.database.models import Employee
    from sqlalchemy import select
    from datetime import datetime
    import uuid

    user = await get_current_user(authorization)
    if str(user.get("role") or "").lower() != "admin":
        raise HTTPException(
            status_code=403,
            detail=(
                "Direct profile edits are disabled. Employees and managers must "
                "upload a CV PDF; employee updates are staged for manager approval."
            ),
        )
    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid employee UUID")

    with get_sync_db() as session:
        emp = session.execute(select(Employee).where(Employee.id == emp_uuid)).scalar_one_or_none()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found")
        emp.profile_text = payload.profile_text
        emp.updated_at = datetime.utcnow()
        session.commit()

    update_cv_uri(user["id"], f"profile:{employee_id}")
    return {"status": "profile_updated"}

# ---------------------------------------------------------------------------
# GET /api/v1/health  &  GET /
# ---------------------------------------------------------------------------

@app.get("/health", status_code=status.HTTP_200_OK)
@app.get("/api/health", status_code=status.HTTP_200_OK)
@app.get("/api/v1/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint (available at /health, /api/health and /api/v1/health)."""
    return {
        "status": "healthy",
        "version": "2.0.0",
        "llm_provider": Config.LLM_PROVIDER,
        "phoenix_enabled": Config.ENABLE_PHOENIX and not _OBSERVABILITY_ACTIVE,
        "observability": observability_status(),
    }

@app.get("/", status_code=status.HTTP_200_OK)
async def root():
    """Root endpoint with API overview."""
    return {
        "title": "Bench Forecast API",
        "version": "2.0.0",
        "endpoints": {
            "forecast": "/api/v1/forecast/run",
            "review":   "/api/v1/forecast/{run_id}/review",
            "health":   "GET  /api/v1/health",
            "docs":     "GET  /docs",
        }
    }

# ---------------------------------------------------------------------------
# OpenAI-compatible model listing (compatibility shim)
# ---------------------------------------------------------------------------

@app.get("/v1/models", status_code=status.HTTP_200_OK)
@app.get("/models", status_code=status.HTTP_200_OK)
async def list_models():
    """OpenAI-compatible ``/v1/models`` endpoint.

    Some LLM clients/tools probe ``/v1/models`` on whatever base URL they are
    configured with. When the Bench Forecast API is (mis)pointed at as an
    OpenAI-compatible endpoint, returning the active model here avoids the
    spurious ``404 Not Found`` in the server logs.
    """
    if Config.LLM_PROVIDER == "groq":
        model_id = Config.GROQ_MODEL
    elif Config.LLM_PROVIDER == "deepseek":
        model_id = Config.DEEPSEEK_MODEL
    elif Config.LLM_PROVIDER == "openai_compatible":
        model_id = Config.LLM_MODEL
    else:
        model_id = Config.OLLAMA_MODEL
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": Config.LLM_PROVIDER,
            }
        ],
    }

# ---------------------------------------------------------------------------
# GET /api/v1/employees & GET /api/v1/demands
# ---------------------------------------------------------------------------

@app.get("/api/v1/employees", status_code=status.HTTP_200_OK)
async def get_employees(horizon_days: int = 90):
    from src.database.database import get_sync_db
    from src.database.models import Employee, EmployeeCost, BenchStatusEnum
    from datetime import date, timedelta
    from sqlalchemy import select, or_

    try:
        cutoff = date.today() + timedelta(days=horizon_days)
        with get_sync_db() as session:
            stmt = (
                select(Employee, EmployeeCost)
                .outerjoin(EmployeeCost, Employee.id == EmployeeCost.employee_id)
                .where(Employee.is_active == True)
                .order_by(Employee.bench_start_date.asc().nulls_last())
            )
            rows = session.execute(stmt).all()

            result = []
            for emp, cost in rows:
                full_name = f"{emp.first_name} {emp.last_name}".strip()
                skills = []
                if emp.skills:
                    skills = [s.strip() for s in emp.skills.split(",") if s.strip()]
                elif emp.job_title:
                    skills = [emp.job_title]
                # NOTE: seniority is surfaced separately via `experience_level`,
                # so it is intentionally NOT appended to the skills list.

                cost_rate = float(cost.internal_hourly_cost) if cost and cost.internal_hourly_cost else (
                    float(emp.hourly_cost_rate) if emp.hourly_cost_rate else 0.0
                )
                if cost_rate <= 0:
                    sen_val = str(emp.experience_level.value).lower() if emp.experience_level else "mid"
                    sen_defaults = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                    cost_rate = sen_defaults.get(sen_val, 65.0)

                exp_years = float(emp.experience_years) if emp.experience_years else (
                    8.0 if emp.experience_level.value == 'principal' else
                    6.0 if emp.experience_level.value == 'lead' else
                    4.0 if emp.experience_level.value == 'senior' else
                    2.0 if emp.experience_level.value == 'mid' else 1.0
                )

                from src.services.workforce_service import summarize_allocations

                capacity = summarize_allocations(emp, emp.allocations)
                manager_name = (
                    f"{emp.manager.first_name} {emp.manager.last_name}".strip()
                    if emp.manager
                    else None
                )
                result.append({
                    "id": str(emp.id),
                    "name": full_name,
                    "full_name": full_name,
                    "skills": skills,
                    "current_project": emp.current_project,
                    "available_from": str(emp.bench_start_date) if emp.bench_start_date else str(date.today()),
                    "experience_years": exp_years,
                    "cost_rate": cost_rate,
                    "profile_text": emp.profile_text or "",
                    "job_title": emp.job_title or "",
                    "experience_level": emp.experience_level.value if emp.experience_level else "",
                    "bench_status": emp.bench_status.value if emp.bench_status else "bench",
                    "manager_id": str(emp.manager_id) if emp.manager_id else None,
                    "manager_name": manager_name,
                    "employment_type": (
                        emp.employment_type.value
                        if hasattr(emp.employment_type, "value")
                        else str(emp.employment_type)
                    ),
                    "daily_capacity_hours": float(capacity["daily_capacity_hours"]),
                    "committed_hours_per_day": float(capacity["committed_hours_per_day"]),
                    "remaining_available_hours": float(capacity["remaining_available_hours"]),
                    "utilization_pct": float(capacity["utilization_pct"]),
                })
            return result
    except Exception as e:
        logger.error(f"Failed to fetch employees: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/demands", status_code=status.HTTP_200_OK)
async def get_demands(min_win_probability: float = 0.75, horizon_days: int = 365):
    from src.database.database import get_sync_db
    from src.database.models import ProjectDemand, Project, DemandStatusEnum
    from datetime import date, timedelta
    from sqlalchemy import select

    try:
        cutoff = date.today() + timedelta(days=horizon_days)
        with get_sync_db() as session:
            stmt = (
                select(ProjectDemand)
                .join(Project, ProjectDemand.project_id == Project.id)
                .where(
                    ProjectDemand.status.in_([
                        DemandStatusEnum.open,
                        DemandStatusEnum.partially_filled,
                    ])
                )
            )
            results = session.execute(stmt).scalars().all()

            demands = []
            for d in results:
                win_prob = float(d.win_probability) if d.win_probability is not None else (
                    float(d.project.probability) / 100.0 if d.project and d.project.probability else 0.8
                )
                if win_prob < min_win_probability:
                    continue
                # Only surface roles needed within the forecast horizon.
                if d.needed_by_date is not None and d.needed_by_date > cutoff:
                    continue

                skills = []
                if isinstance(d.required_skills, list):
                    skills = d.required_skills
                elif isinstance(d.required_skills, dict):
                    skills = list(d.required_skills.keys())
                elif isinstance(d.required_skills, str):
                    skills = [s.strip() for s in d.required_skills.split(",") if s.strip()]

                demands.append({
                    "id": str(d.id),
                    "role": d.role,
                    "required_skills": skills,
                    "project_id": str(d.project_id),
                    "project_name": d.project.name if d.project else f"Project {str(d.project_id)[:8]}",
                    "start_date": str(d.needed_by_date) if d.needed_by_date else str(date.today()),
                    "headcount": d.headcount_needed,
                    "headcount_required": d.headcount_required,
                    "hours_per_day": float(d.hours_per_day or 0),
                    "status": (
                        d.status.value if hasattr(d.status, "value") else str(d.status)
                    ),
                    "win_probability": win_prob,
                    "description": d.description or f"Demand for {d.role}",
                    "target_bill_rate": float(d.target_bill_rate) if d.target_bill_rate else 100.0,
                    "target_margin": float(d.target_margin) if d.target_margin else 30.0,
                })
            return demands
    except Exception as e:
        logger.error(f"Failed to fetch demands: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects", status_code=status.HTTP_200_OK)
async def get_projects_legacy():
    from src.database.database import get_sync_db
    from src.database.models import Project, ProjectDemand, DemandStatusEnum
    from sqlalchemy import select, func

    try:
        with get_sync_db() as session:
            stmt = select(Project).order_by(Project.name.asc())
            projects_db = session.execute(stmt).scalars().all()
            
            # Count open demands per project
            count_rows = session.execute(
                select(ProjectDemand.project_id, func.count(ProjectDemand.id))
                .where(ProjectDemand.status == DemandStatusEnum.open)
                .group_by(ProjectDemand.project_id)
            ).all()
            dem_counts = {r[0]: r[1] for r in count_rows}

            return [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "status": p.status,
                    "probability": p.probability,
                    "target_margin": float(p.target_margin) if p.target_margin else 25.0,
                    "total_budget": float(p.total_budget) if p.total_budget else 0.0,
                    "open_demands_count": dem_counts.get(p.id, 0),
                }
                for p in projects_db
            ]
    except Exception as e:
        logger.error(f"Failed to fetch projects: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
