"""FastAPI backend for Bench Forecast allocation recommendations.

Endpoints:
1. POST /api/v1/forecast/generate  — Run forecast workflow; pause for human review
2. POST /api/v1/forecast/execute   — Resume: approve (→ DB) or reject with feedback (→ revise)
3. POST /api/v1/forecast/feedback  — Submit audit notes
4. GET  /api/v1/health             — Health check

New features:
- horizon_days: configurable forecast window (default 90d)
- min_win_probability: pipeline filter threshold (default 0.75)
- HITL rejection feedback: manager's feedback triggers LLM revision instead of terminating
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, status, Header
from pydantic import BaseModel, Field
from langgraph.types import Command

from src.core.config import Config
from src.core.tracing import setup_phoenix_tracing, get_tracer
from src.agents.graph import build_forecast_graph
from src.database.vector_store import VectorStoreManager


logger = logging.getLogger(__name__)

app = FastAPI(
    title="Bench Forecast API",
    version="2.0.0",
    description="Agentic workforce allocation engine with RAG matching and HITL feedback loop",
)

setup_phoenix_tracing()

# Compiled workflow — interrupt_before=["human_review"] for true HITL pause/resume
FORECAST_GRAPH = build_forecast_graph()

# recommendation_id → thread_id mapping so UI can resume by rec_id
RECOMMENDATION_THREAD_MAP: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class GenerateForecastRequest(BaseModel):
    """Request to generate a forecast."""
    department_id: str = Field(default="ALL", description="Department to forecast for")
    thread_id: str = Field(
        default_factory=lambda: __import__("uuid").uuid4().hex,
        description="Unique thread ID for this workflow invocation",
    )
    horizon_days: int = Field(
        default=90, ge=30, le=365,
        description="Forecast horizon in days — employees finishing projects within this window",
    )
    min_win_probability: float = Field(
        default=0.75, ge=0.0, le=1.0,
        description="Minimum pipeline win probability to include a demand in matching",
    )


class ExecuteForecastRequest(BaseModel):
    """Request to execute or reject an approved forecast."""
    recommendation_id: str = Field(..., description="Recommendation ID to act on")
    thread_id: str = Field(default="", description="Thread ID of the paused workflow")
    approved: bool = Field(..., description="True = approve and execute; False = reject")
    approver_name: str = Field(default="unknown", description="Name of human reviewer")
    rejection_feedback: Optional[str] = Field(
        default=None,
        description=(
            "Manager's reason for rejection and guidance for revision. "
            "Required when approved=False if you want a revised plan instead of termination."
        ),
    )


class FeedbackRequest(BaseModel):
    """Submit audit notes on a recommendation."""
    recommendation_id: str = Field(..., description="Recommendation ID")
    feedback: str = Field(..., description="Human feedback/review notes")
    approved: bool = Field(default=False, description="Whether feedback is approval")


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
# POST /api/v1/forecast/generate
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/forecast/generate",
    response_model=Dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_forecast(payload: GenerateForecastRequest):
    """Run the forecast workflow and pause for human review.

    Streams data_extractor → skill_matcher (RAG) → forecast_planner,
    then pauses natively at interrupt_before=["human_review"].

    Returns HTTP 202 with recommendations and thread_id for later /execute call.
    """
    logger.info(
        f"📊 GENERATE FORECAST: thread_id={payload.thread_id} "
        f"horizon={payload.horizon_days}d win_prob>={payload.min_win_probability:.0%}"
    )

    try:
        input_state = {
            "employees": [],
            "demands": [],
            "skill_matches": [],
            "recommendations": None,
            "human_approved": False,
            "human_feedback": None,
            "rejection_feedback": None,
            "revision_count": 0,
            "execution_status": None,
            # Pass config params into state for data_extractor_node to read
            "horizon_days": payload.horizon_days,
            "min_win_probability": payload.min_win_probability,
        }

        cfg = {"configurable": {"thread_id": payload.thread_id}}
        final_state = None
        for chunk in FORECAST_GRAPH.stream(input_state, config=cfg, stream_mode="values"):
            logger.debug(f"  Stream chunk: {list(chunk.keys())}")
            final_state = chunk

        if not final_state:
            raise ValueError("Workflow produced no output")

        recommendations = final_state.get("recommendations") or {}
        rec_id = recommendations.get("recommendation_id", "unknown")

        # Store mapping for UI to resolve thread_id by rec_id
        RECOMMENDATION_THREAD_MAP[rec_id] = payload.thread_id

        logger.info(
            f"✓ Forecast paused for review: "
            f"{len(recommendations.get('reallocations', []))} reallocations "
            f"(thread_id={payload.thread_id}, rec_id={rec_id})"
        )

        return {
            "status": "paused_for_review",
            "thread_id": payload.thread_id,
            "recommendation_id": rec_id,
            "horizon_days": payload.horizon_days,
            "min_win_probability": payload.min_win_probability,
            "recommendations": recommendations,
            "next_step": "POST /api/v1/forecast/execute — set approved=true to commit or approved=false with rejection_feedback to revise",
        }

    except Exception as e:
        logger.error(f"✗ Forecast generation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast generation failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# POST /api/v1/forecast/execute
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/forecast/execute",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def execute_forecast(payload: ExecuteForecastRequest) -> Dict[str, Any]:
    """Resume the paused workflow with an approval or rejection decision.

    Approval (approved=True):
        → Runs execution_engine_node → writes to SQLite → returns "completed"

    Rejection with feedback (approved=False + rejection_feedback):
        → Runs revision_planner_node (LLM generates revised plan)
        → Pauses again at human_review interrupt
        → Returns "revised_for_review" with new recommendations

    Rejection without feedback (approved=False, no rejection_feedback):
        → Routes to END with no DB writes
        → Returns "rejected_terminated"
    """
    logger.info(
        f"⚡ EXECUTE FORECAST: rec_id={payload.recommendation_id} "
        f"approved={payload.approved} approver={payload.approver_name}"
    )

    try:
        # Resolve thread_id from rec_id if not provided
        thread_id = payload.thread_id
        if not thread_id and payload.recommendation_id in RECOMMENDATION_THREAD_MAP:
            thread_id = RECOMMENDATION_THREAD_MAP[payload.recommendation_id]
            logger.info(f"  Resolved thread_id={thread_id} from rec_id")

        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No active workflow for recommendation_id={payload.recommendation_id}. "
                    "Call /generate first."
                ),
            )

        tracer = get_tracer()
        tracer.log_allocation_decision(
            recommendation_id=payload.recommendation_id,
            approved=payload.approved,
            approver=payload.approver_name,
        )

        cfg = {"configurable": {"thread_id": thread_id}}

        # Build resume payload — explicitly update state in checkpointer
        resume_payload: Dict[str, Any] = {
            "human_approved": payload.approved,
            "human_feedback": payload.approver_name,
        }
        if not payload.approved and payload.rejection_feedback:
            resume_payload["rejection_feedback"] = payload.rejection_feedback

        FORECAST_GRAPH.update_state(cfg, resume_payload)

        final_state = None
        for chunk in FORECAST_GRAPH.stream(
            None,
            config=cfg,
            stream_mode="values",
        ):
            logger.debug(f"  Resume chunk: {list(chunk.keys())}")
            final_state = chunk

        if not final_state:
            raise ValueError("Resume produced no output")

        execution_status = final_state.get("execution_status")
        new_recommendations = final_state.get("recommendations", {})
        new_rec_id = new_recommendations.get("recommendation_id", "unknown")

        # Determine response based on what happened
        if payload.approved:
            # Workflow completed — clean up
            RECOMMENDATION_THREAD_MAP.pop(payload.recommendation_id, None)
            logger.info(f"✓ Execution complete: {execution_status}")
            return {
                "status": "completed",
                "approved": True,
                "execution_status": execution_status,
                "recommendations": new_recommendations,
            }

        elif payload.rejection_feedback and final_state.get("revision_count", 0) > 0:
            # Revision generated — graph paused again; update thread mapping
            RECOMMENDATION_THREAD_MAP.pop(payload.recommendation_id, None)
            RECOMMENDATION_THREAD_MAP[new_rec_id] = thread_id
            logger.info(
                f"✓ Revision complete: new rec_id={new_rec_id} "
                f"(revision {final_state.get('revision_count')}/2)"
            )
            return {
                "status": "revised_for_review",
                "thread_id": thread_id,
                "recommendation_id": new_rec_id,
                "revision_count": final_state.get("revision_count", 1),
                "revision_feedback_applied": payload.rejection_feedback,
                "recommendations": new_recommendations,
                "next_step": "POST /api/v1/forecast/execute with new recommendation_id",
            }

        else:
            # Rejected with no feedback — terminated
            RECOMMENDATION_THREAD_MAP.pop(payload.recommendation_id, None)
            logger.info("✓ Workflow terminated (rejected, no feedback)")
            return {
                "status": "rejected_terminated",
                "approved": False,
                "execution_status": "skipped_no_approval",
                "recommendations": new_recommendations,
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Execution failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# POST /api/v1/forecast/feedback
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/forecast/feedback",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def forecast_feedback(payload: FeedbackRequest) -> Dict[str, Any]:
    """Submit audit notes on a recommendation (separate from HITL decision)."""
    logger.info(
        f"💬 FEEDBACK: recommendation_id={payload.recommendation_id} approved={payload.approved}"
    )
    try:
        tracer = get_tracer()
        tracer.log_allocation_decision(
            recommendation_id=payload.recommendation_id,
            approved=payload.approved,
            approver="feedback_api",
        )
        return {
            "status": "feedback_recorded",
            "recommendation_id": payload.recommendation_id,
            "approved": payload.approved,
            "feedback_length": len(payload.feedback),
        }
    except Exception as e:
        logger.error(f"✗ Feedback submission failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Feedback submission failed: {str(e)}",
        )


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

# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    user: dict

@app.post("/api/v1/auth/login", status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest):
    from src.auth.auth import authenticate_user, create_access_token
    user = authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user["id"], "username": user["username"], "role": user["role"]})
    return {"token": token, "user": user}

@app.get("/api/v1/auth/me", status_code=status.HTTP_200_OK)
async def get_current_user(authorization: str = Header(None)):
    from src.auth.auth import verify_token, get_user_by_id
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ", 1)[1]
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ---------------------------------------------------------------------------
# Employee Portal endpoints  
# ---------------------------------------------------------------------------

@app.get("/api/v1/employee/portal/{employee_id}", status_code=status.HTTP_200_OK)
async def get_employee_portal(employee_id: str):
    """Get employee's current project and next assignment info."""
    from src.agents.nodes import _get_db
    try:
        db = _get_db()
        # Get employee info
        employees = db.get_bench_forecast(horizon_days=365)
        employee = next((e for e in employees if e.id == employee_id), None)
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Get allocation history to find next assignment
        allocations = []
        if hasattr(db, 'get_allocation_history'):
            allocations = db.get_allocation_history(employee_id=employee_id)
        
        # Get all demands to find potential next projects
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
    from src.auth.auth import verify_token, change_password
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    tok = verify_token(authorization.split(" ", 1)[1])
    if not tok:
        raise HTTPException(status_code=401, detail="Invalid token")
    if not change_password(tok["sub"], payload.old_password, payload.new_password):
        raise HTTPException(status_code=400, detail="Wrong current password")
    return {"status": "password_changed"}


@app.post("/api/v1/employee/upload-cv/{employee_id}", status_code=status.HTTP_200_OK)
async def upload_cv(employee_id: str, authorization: str = Header(None)):
    """Update CV/profile_text for an employee."""
    from src.auth.auth import verify_token
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    tok = verify_token(authorization.split(" ", 1)[1])
    if not tok:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Read raw body as profile text
    return {"status": "ready", "note": "CV upload endpoint placeholder — use PUT with profile_text body"}


class UpdateProfileRequest(BaseModel):
    profile_text: str

@app.put("/api/v1/employee/profile/{employee_id}", status_code=status.HTTP_200_OK)
async def update_employee_profile(employee_id: str, payload: UpdateProfileRequest, authorization: str = Header(None)):
    """Update employee profile_text (CV content)."""
    from src.auth.auth import verify_token, update_cv_uri
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    tok = verify_token(authorization.split(" ", 1)[1])
    if not tok:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Update profile_text in employee DB
    from src.agents.nodes import _get_db
    db = _get_db()
    import sqlite3
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE employees SET profile_text=?, updated_at=? WHERE id=?",
                     (payload.profile_text, __import__('datetime').datetime.utcnow().isoformat(), employee_id))
        conn.commit()
    update_cv_uri(tok["sub"], f"profile:{employee_id}")
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
        "active_workflows": len(RECOMMENDATION_THREAD_MAP),
    }


@app.get("/", status_code=status.HTTP_200_OK)
async def root():
    """Root endpoint with API overview."""
    return {
        "title": "Bench Forecast API",
        "version": "2.0.0",
        "endpoints": {
            "generate": "POST /api/v1/forecast/generate — Start forecast (horizon_days, min_win_probability)",
            "execute":  "POST /api/v1/forecast/execute  — Approve (→ DB) or Reject+feedback (→ revise)",
            "feedback": "POST /api/v1/forecast/feedback — Submit audit notes",
            "health":   "GET  /api/v1/health            — Health check",
            "docs":     "GET  /docs                     — Swagger UI",
        },
        "workflow": (
            "data_extractor[horizon] → skill_matcher[RAG] → forecast_planner → "
            "[INTERRUPT] → human_review → {execute | revise+loop | end}"
        ),
    }
