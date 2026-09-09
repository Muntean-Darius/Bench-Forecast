"""FastAPI backend for Bench Forecast allocation recommendations.

Three core endpoints:
1. POST /api/v1/forecast/generate - Run forecast workflow and pause for human review
2. POST /api/v1/forecast/execute - Resume workflow with human approval/rejection
3. POST /api/v1/forecast/feedback - Submit human review feedback

The workflow pauses after forecast planning, allowing a human manager to review
recommendations before execution updates the database.
"""

import logging
from typing import Any, Dict
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from src.core.config import Config
from src.core.tracing import setup_phoenix_tracing, get_tracer
from src.schemas.models import AllocationRecommendation
from src.agents.graph import build_forecast_graph


logger = logging.getLogger(__name__)


# Initialize FastAPI app
app = FastAPI(
    title="Bench Forecast API",
    version="1.0.0",
    description="Agentic workforce allocation recommendation engine",
)

# Initialize tracing
setup_phoenix_tracing()

# Build and compile the LangGraph workflow at startup
FORECAST_GRAPH = build_forecast_graph()

# Store active workflows by thread_id for pause/resume capability
ACTIVE_WORKFLOWS: Dict[str, Dict[str, Any]] = {}


class GenerateForecastRequest(BaseModel):
    """Request to generate a forecast."""
    department_id: str = Field(default="ALL", description="Department to forecast for")
    thread_id: str = Field(
        default_factory=lambda: __import__("uuid").uuid4().hex,
        description="Unique thread ID for this workflow invocation (for pause/resume)",
    )


class ExecuteForecastRequest(BaseModel):
    """Request to execute an approved forecast."""
    recommendation_id: str = Field(..., description="Recommendation ID to execute")
    thread_id: str = Field(..., description="Thread ID of the paused workflow")
    approved: bool = Field(..., description="Whether human approved the recommendation")
    approver_name: str = Field(default="unknown", description="Name of human reviewer")


class FeedbackRequest(BaseModel):
    """Submit feedback on a recommendation."""
    recommendation_id: str = Field(..., description="Recommendation ID")
    feedback: str = Field(..., description="Human feedback/review notes")
    approved: bool = Field(default=False, description="Whether feedback is approval")


@app.on_event("startup")
async def startup():
    """Initialize on app startup."""
    logger.info("=" * 60)
    logger.info("🚀 Bench Forecast API Starting")
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


@app.post(
    "/api/v1/forecast/generate",
    response_model=Dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_forecast(payload: GenerateForecastRequest):
    """Trigger agentic bench forecast generation and pause for human review.
    
    This endpoint:
    1. Runs the workflow through forecast planning
    2. Pauses before execution (HITL breakpoint)
    3. Returns recommendations for human review
    4. Stores workflow state for resume via /execute
    
    Args:
        payload: GenerateForecastRequest with department_id and thread_id
        
    Returns:
        HTTP 202 (Accepted) with recommendations and thread_id for later execution
    """
    logger.info(f"📊 GENERATE FORECAST: thread_id={payload.thread_id}")
    
    try:
        # Stream the workflow until it pauses (before execution node)
        input_state = {
            "employees": [],
            "demands": [],
            "skill_matches": [],
            "recommendations": None,
            "human_approved": False,
            "human_feedback": None,
            "execution_status": None,
        }
        
        # Use streaming to run until pause
        final_state = None
        for output in FORECAST_GRAPH.stream(
            input_state,
            config={"configurable": {"thread_id": payload.thread_id}},
        ):
            logger.debug(f"  Step: {output}")
            final_state = output
        
        if not final_state:
            raise ValueError("Workflow produced no output")
        
        # Extract the actual state dictionary from the streaming output
        # LangGraph stream returns a dict of {node_name: state_update}
        state_update = final_state[list(final_state.keys())[-1]] if final_state else {}
        
        # Merge with existing workflow state
        workflow_state = {**input_state, **state_update}
        
        # Store workflow state for later execution
        ACTIVE_WORKFLOWS[payload.thread_id] = workflow_state
        
        recommendations = workflow_state.get("recommendations", {})
        
        logger.info(
            f"✓ Forecast generated: {len(recommendations.get('reallocations', []))} reallocations, "
            f"paused for human review (thread_id={payload.thread_id})"
        )
        
        return {
            "status": "paused_for_review",
            "thread_id": payload.thread_id,
            "recommendation_id": recommendations.get("recommendation_id", "unknown"),
            "recommendations": recommendations,
            "next_step": "POST /api/v1/forecast/execute with approval decision",
        }
        
    except Exception as e:
        logger.error(f"✗ Forecast generation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast generation failed: {str(e)}",
        )


@app.post(
    "/api/v1/forecast/execute",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def execute_forecast(payload: ExecuteForecastRequest) -> Dict[str, Any]:
    """Execute approved allocation actions (resume paused workflow).
    
    This endpoint:
    1. Resumes the workflow from the HITL pause point
    2. Sets human_approved flag based on approval decision
    3. Continues to execution node
    4. Returns execution status and audit trail
    
    This is where human approval flows into deterministic database updates.
    
    Args:
        payload: ExecuteForecastRequest with thread_id and approval decision
        
    Returns:
        Execution status and results
    """
    logger.info(
        f"⚡ EXECUTE FORECAST: thread_id={payload.thread_id}, "
        f"approved={payload.approved}, approver={payload.approver_name}"
    )
    
    try:
        # Retrieve paused workflow state
        if payload.thread_id not in ACTIVE_WORKFLOWS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No active workflow with thread_id={payload.thread_id}. "
                f"Call /generate first.",
            )
        
        workflow_state = ACTIVE_WORKFLOWS[payload.thread_id]
        
        # Set human approval flag and feedback
        workflow_state["human_approved"] = payload.approved
        workflow_state["human_feedback"] = payload.approver_name
        
        tracer = get_tracer()
        tracer.log_allocation_decision(
            recommendation_id=payload.recommendation_id,
            approved=payload.approved,
            approver=payload.approver_name,
        )
        
        # Resume workflow (will execute only if approved)
        for output in FORECAST_GRAPH.stream(
            workflow_state,
            config={"configurable": {"thread_id": payload.thread_id}},
        ):
            logger.debug(f"  Resume step: {output}")
            workflow_state = {**workflow_state, **output[list(output.keys())[-1]]}
        
        execution_status = workflow_state.get("execution_status", "unknown")
        
        logger.info(f"✓ Execution complete: {execution_status}")
        
        # Clean up workflow state
        del ACTIVE_WORKFLOWS[payload.thread_id]
        
        return {
            "status": "completed",
            "approved": payload.approved,
            "execution_status": execution_status,
            "recommendations": workflow_state.get("recommendations", {}),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ Execution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution failed: {str(e)}",
        )


@app.post(
    "/api/v1/forecast/feedback",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
async def forecast_feedback(payload: FeedbackRequest) -> Dict[str, Any]:
    """Submit human-in-the-loop review feedback and decision.
    
    This is an alternative endpoint for storing feedback before execution.
    Can be used by the Streamlit UI to capture human reasoning.
    
    Args:
        payload: FeedbackRequest with recommendation_id and feedback text
        
    Returns:
        Feedback confirmation
    """
    logger.info(
        f"💬 FEEDBACK: recommendation_id={payload.recommendation_id}, "
        f"approved={payload.approved}"
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


@app.get("/api/v1/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "llm_provider": Config.LLM_PROVIDER,
        "phoenix_enabled": Config.ENABLE_PHOENIX,
    }


@app.get("/", status_code=status.HTTP_200_OK)
async def root():
    """Root endpoint with API documentation."""
    return {
        "title": "Bench Forecast API",
        "version": "1.0.0",
        "endpoints": {
            "generate": "POST /api/v1/forecast/generate - Start forecast workflow",
            "execute": "POST /api/v1/forecast/execute - Execute approved allocations",
            "feedback": "POST /api/v1/forecast/feedback - Submit human feedback",
            "health": "GET /api/v1/health - Health check",
            "docs": "GET /docs - Interactive API documentation (Swagger UI)",
        },
        "workflow": "data_extractor → skill_matcher → forecast_planner → [PAUSE] → execution_engine",
    }

