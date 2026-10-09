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
    else:
        logger.info(f"Ollama Model: {Config.OLLAMA_MODEL} @ {Config.OLLAMA_BASE_URL}")
    logger.info(f"Database: {Config.SQLITE_DB_PATH}")
    logger.info(f"Phoenix Tracing: {'Enabled' if Config.ENABLE_PHOENIX else 'Disabled'}")
    logger.info("=" * 60)
    Config.validate()

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
    from src.api.deps import get_current_user
    return await get_current_user(authorization)

# ---------------------------------------------------------------------------
# Employee Portal endpoints (Legacy kept for compatibility)
# ---------------------------------------------------------------------------

@app.get("/api/v1/employee/portal/{employee_id}", status_code=status.HTTP_200_OK)
async def get_employee_portal(employee_id: str):
    """Get employee's current project and next assignment info."""
    from src.agents.nodes import _get_db
    try:
        db = _get_db()
        employees = db.get_bench_forecast(horizon_days=365)
        employee = next((e for e in employees if e.id == employee_id), None)
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        allocations = []
        if hasattr(db, 'get_allocation_history'):
            allocations = db.get_allocation_history(employee_id=employee_id)
        
        demands = db.get_open_demands(min_win_probability=0.5)
        
        return {
            "employee": {
                "id": employee.id,
                "name": employee.name,
                "skills": employee.skills,
                "current_project": employee.current_project,
                "available_from": str(employee.available_from),
                "experience_years": employee.experience_years,
                "cost_rate": employee.cost_rate,
                "profile_text": getattr(employee, 'profile_text', '') or '',
            },
            "allocations": allocations,
            "open_demands": [
                {
                    "id": d.id,
                    "role": d.role,
                    "required_skills": d.required_skills,
                    "project_id": d.project_id,
                    "start_date": str(d.start_date),
                    "win_probability": d.win_probability,
                    "description": d.description,
                }
                for d in demands
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Employee portal error: {e}")
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
    user = await get_current_user(authorization)
    from src.agents.nodes import _get_db
    db = _get_db()
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE employees SET profile_text=?, updated_at=? WHERE id=?",
                     (payload.profile_text, __import__('datetime').datetime.utcnow().isoformat(), employee_id))
        conn.commit()
    update_cv_uri(user["id"], f"profile:{employee_id}")
    return {"status": "profile_updated"}

# ---------------------------------------------------------------------------
# GET /api/v1/health  &  GET /
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint."""
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
# GET /api/v1/employees & GET /api/v1/demands
# ---------------------------------------------------------------------------

@app.get("/api/v1/employees", status_code=status.HTTP_200_OK)
async def get_employees(horizon_days: int = 90):
    from src.agents.nodes import _get_db
    try:
        db = _get_db()
        return db.get_bench_forecast(horizon_days=horizon_days)
    except Exception as e:
        logger.error(f"Failed to fetch employees: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/demands", status_code=status.HTTP_200_OK)
async def get_demands(min_win_probability: float = 0.75):
    from src.agents.nodes import _get_db
    try:
        db = _get_db()
        return db.get_open_demands(min_win_probability=min_win_probability)
    except Exception as e:
        logger.error(f"Failed to fetch demands: {e}")
        raise HTTPException(status_code=500, detail=str(e))
