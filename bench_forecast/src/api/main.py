"""FastAPI backend for Bench Forecast allocation recommendations."""

import logging
import os
from pathlib import Path
from typing import Any, Dict
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

# OpenTelemetry / Phoenix Tracing Environment Variables
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "http://localhost:6006/v1/traces"

from src.core.config import Config
from src.core.tracing import setup_phoenix_tracing
from src.database.vector_store import VectorStoreManager
from src.api.routers.documents import router as documents_router
from src.api.routers.finance import router as finance_router
from src.api.routers.hitl import router as hitl_router
from src.api.routers.forecast import router as forecast_router
from src.api.routers.analytics import router as analytics_router


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

setup_phoenix_tracing()


# ---------------------------------------------------------------------------
# Startup
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
    elif Config.LLM_PROVIDER == "openai_compatible":
        logger.info(f"OpenAI-compatible Model: {Config.LLM_MODEL} @ {Config.LLM_BASE_URL}")
    else:
        logger.info(f"Ollama Model: {Config.OLLAMA_MODEL} @ {Config.OLLAMA_BASE_URL}")
    logger.info(f"Database: {Config.SQLITE_DB_PATH}")
    logger.info(f"Phoenix Tracing: {'Enabled' if Config.ENABLE_PHOENIX else 'Disabled'}")
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

    # Auto-seed ChromaDB from mock_data.json if empty
    try:
        mock_data_path = Path(__file__).resolve().parents[2] / "data" / "mock_data.json"
        if mock_data_path.exists():
            vector_mgr = VectorStoreManager()
            if vector_mgr.collection.count() == 0:
                count = vector_mgr.load_and_index_mock_data(str(mock_data_path))
                logger.info(f"✓ ChromaDB auto-seeded: {count} employee profiles indexed")
            else:
                logger.info(f"✓ ChromaDB already seeded ({vector_mgr.collection.count()} profiles)")
        else:
            logger.warning(f"mock_data.json not found at {mock_data_path}")
    except Exception as e:
        logger.warning(f"ChromaDB seeding failed (non-fatal): {e}")


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
    from src.database.models import Employee, EmployeeCost, Allocation, ProjectDemand, DemandStatusEnum, AllocationStatusEnum
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
                    "start_date": str(a.start_date) if a.start_date else None,
                    "end_date": str(a.end_date) if a.end_date else None,
                    "status": status_label,
                    "raw_status": raw_st,
                    "ai_justification": a.ai_justification,
                })

            demand_rows = session.execute(
                select(ProjectDemand).where(ProjectDemand.status == DemandStatusEnum.open)
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
                },
                "allocations": allocations,
                "open_demands": open_demands,
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
        "phoenix_enabled": Config.ENABLE_PHOENIX,
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
                    "bench_status": emp.bench_status.value if emp.bench_status else "bench"
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
                .where(ProjectDemand.status == DemandStatusEnum.open)
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
                    "win_probability": win_prob,
                    "description": d.description or f"Demand for {d.role}",
                    "target_bill_rate": float(d.target_bill_rate) if d.target_bill_rate else 100.0,
                    "target_margin": float(d.target_margin) if d.target_margin else 30.0,
                })
            return demands
    except Exception as e:
        logger.error(f"Failed to fetch demands: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/projects", status_code=status.HTTP_200_OK)
@app.get("/api/projects", status_code=status.HTTP_200_OK)
async def get_projects():
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
