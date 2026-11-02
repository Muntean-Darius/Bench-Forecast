import asyncio
import json
import uuid
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.api.deps import get_current_user
from src.database.database import get_db
from src.database.models import ForecastRun
from src.services.forecast_runner import create_forecast_run, execute_forecast_run

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/forecast",
    tags=["Forecast Execution"],
)

class RunForecastRequest(BaseModel):
    horizon_days: int = Field(default=90, ge=30, le=365, description="Look-ahead window in days")
    project_id: Optional[uuid.UUID] = Field(default=None, description="Optional target project ID to run forecast specifically for")
    min_win_probability: float = Field(default=0.75, ge=0.0, le=1.0, description="Exclude pipeline demands below this win probability")

class RunForecastResponse(BaseModel):
    forecast_run_id: str
    status: str
    state_metadata: Dict[str, Any]

class ForecastRunStatusResponse(BaseModel):
    id: str
    status: str
    run_date: str
    forecast_horizon_days: int
    triggered_by: str
    current_stage: Optional[str] = None

@router.post("/run", response_model=RunForecastResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_forecast_run(
    payload: RunForecastRequest,
    background_tasks: BackgroundTasks,
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Queue a forecast run and return immediately.

    The planner runs in a background task; the client polls
    ``GET /forecast/runs/{id}`` until the status is no longer ``queued`` /
    ``running``. All state is persisted in PostgreSQL, so the run survives a
    restart.
    """
    user_uuid = uuid.UUID(current_user["id"])
    logger.info(
        f"User {user_uuid} queuing forecast run (horizon={payload.horizon_days}d, "
        f"project={payload.project_id}, min_win={payload.min_win_probability})"
    )

    try:
        run_id = await create_forecast_run(payload.horizon_days, user_uuid)
        background_tasks.add_task(
            execute_forecast_run,
            run_id,
            payload.horizon_days,
            user_uuid,
            payload.project_id,
            payload.min_win_probability,
        )
        return RunForecastResponse(
            forecast_run_id=str(run_id),
            status="queued",
            state_metadata={
                "revision_count": 0,
                "decisions": [],
                "min_win_probability": payload.min_win_probability,
            },
        )
    except Exception as e:
        logger.error(f"Failed to queue forecast run: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not queue forecast run: {str(e)}",
        )

@router.get("/runs/{run_id}", response_model=ForecastRunStatusResponse, status_code=status.HTTP_200_OK)
async def get_forecast_run_status(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Polls the current status of a forecast run from the database.
    """
    try:
        stmt = select(ForecastRun).where(ForecastRun.id == run_id)
        result = await db.execute(stmt)
        run_record = result.scalars().first()
        
        if not run_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Forecast run {run_id} not found."
            )
            
        return ForecastRunStatusResponse(
            id=str(run_record.id),
            status=run_record.status,
            run_date=run_record.run_date.isoformat(),
            forecast_horizon_days=run_record.forecast_horizon_days,
            triggered_by=str(run_record.triggered_by) if run_record.triggered_by else "",
            current_stage=run_record.current_stage,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch run status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not fetch forecast run status."
        )


@router.get("/runs/{run_id}/stream")
async def stream_forecast_run(
    run_id: uuid.UUID,
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """SSE stream that reports real LangGraph node progress.

    Previous versions polled the status through this request's long-lived
    ``AsyncSession`` without refreshing the ORM identity map: the first read
    cached ``ForecastRun`` and every later ``select()`` returned the same stale
    object, so the terminal ``paused_for_review`` state was never observed and
    the UI waited forever. ``populate_existing`` forces a fresh row on every
    poll.
    """
    from src.auth.auth import verify_token

    # Auth: accept token via query param (EventSource can't set headers)
    if not token:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    payload = verify_token(token)
    if not payload:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Invalid token"}, status_code=401)

    STEPS = [
        ("data_extractor",    "Data Extraction: Scanning Bench Supply & Pipeline Demands",     25),
        ("skill_matcher",     "Semantic Matching: Calculating ChromaDB Embeddings & Fit Scores", 50),
        ("allocation_decider","Margin Optimization: Balancing Billable Utilization & Margin Caps", 75),
        ("forecast_planner",  "Plan Formulation: Persisting Allocation Proposals & Hiring Alerts", 100),
    ]
    STEP_KEYS = [step[0] for step in STEPS]
    TERMINAL = {"paused_for_review", "completed"}

    async def _read_run():
        """Read the run with a fresh ORM snapshot (bypasses the identity map)."""
        stmt = (
            select(ForecastRun)
            .where(ForecastRun.id == run_id)
            .execution_options(populate_existing=True)
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    async def event_generator():
        started_at = asyncio.get_event_loop().time()
        max_wait = 600  # 10 minutes max
        last_heartbeat = started_at
        emitted: Dict[str, str] = {}

        while True:
            if asyncio.get_event_loop().time() - started_at > max_wait:
                yield f"event: error\ndata: {json.dumps({'status': 'timeout', 'error': 'Forecast run timed out'})}\n\n"
                return

            try:
                run_record = await _read_run()
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                return

            if not run_record:
                yield f"event: error\ndata: {json.dumps({'status': 'not_found', 'error': f'Run {run_id} not found'})}\n\n"
                return

            db_status = run_record.status

            if db_status == "failed":
                yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': 'Forecast run failed — check backend logs'})}\n\n"
                return

            if db_status in TERMINAL:
                # Finish any steps that never received an explicit event.
                for step_key, step_label, progress in STEPS:
                    if emitted.get(step_key) != "completed":
                        yield f"event: step\ndata: {json.dumps({'step': step_key, 'label': step_label, 'progress': progress, 'status': 'completed'})}\n\n"
                        emitted[step_key] = "completed"
                yield f"event: complete\ndata: {json.dumps({'status': db_status, 'forecast_run_id': str(run_id), 'message': 'Forecast run completed successfully'})}\n\n"
                return

            # Prefer the node persisted by the graph; fall back to the first step
            # while the run is queued or before the first node writes its stage.
            stage = getattr(run_record, "current_stage", None) if db_status == "running" else None
            step_index = STEP_KEYS.index(stage) if stage in STEP_KEYS else 0

            # Every step before the active one is finished.
            for step_key, step_label, progress in STEPS[:step_index]:
                if emitted.get(step_key) != "completed":
                    yield f"event: step\ndata: {json.dumps({'step': step_key, 'label': step_label, 'progress': progress, 'status': 'completed'})}\n\n"
                    emitted[step_key] = "completed"

            step_key, step_label, progress = STEPS[step_index]
            if emitted.get(step_key) != "active":
                yield f"event: step\ndata: {json.dumps({'step': step_key, 'label': step_label, 'progress': progress, 'status': 'active'})}\n\n"
                emitted[step_key] = "active"

            # Keep proxies from closing an idle stream while a long LLM call runs.
            now = asyncio.get_event_loop().time()
            if now - last_heartbeat >= 15:
                yield ": keep-alive\n\n"
                last_heartbeat = now

            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
