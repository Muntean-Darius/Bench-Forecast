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
            triggered_by=str(run_record.triggered_by) if run_record.triggered_by else ""
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
    """SSE stream that polls DB status and emits step events as each phase completes."""
    from src.auth.auth import verify_token

    # Auth: accept token via query param (EventSource can't set headers)
    if token:
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

    async def event_generator():
        # Emit each step as the run progresses (poll DB status)
        started_at = asyncio.get_event_loop().time()
        max_wait = 600  # 10 minutes max

        # Emit first step immediately
        for i, (step_key, step_label, progress) in enumerate(STEPS):
            step_sent = False
            while True:
                if asyncio.get_event_loop().time() - started_at > max_wait:
                    yield f"event: error\ndata: {json.dumps({'status': 'timeout', 'error': 'Forecast run timed out'})}\n\n"
                    return

                try:
                    stmt = select(ForecastRun).where(ForecastRun.id == run_id)
                    result = await db.execute(stmt)
                    run_record = result.scalars().first()
                    if not run_record:
                        yield f"event: error\ndata: {json.dumps({'status': 'not_found', 'error': f'Run {run_id} not found'})}\n\n"
                        return

                    db_status = run_record.status

                    if db_status == "failed":
                        yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': 'Forecast run failed — check backend logs'})}\n\n"
                        return

                    if not step_sent:
                        yield f"event: step\ndata: {json.dumps({'step': step_key, 'label': step_label, 'progress': progress, 'status': 'running'})}\n\n"
                        step_sent = True

                    # Move to next step when run progresses, or when terminal
                    if db_status in ("paused_for_review", "completed"):
                        # Emit all remaining steps as completed
                        for j in range(i, len(STEPS)):
                            sk, sl, sp = STEPS[j]
                            yield f"event: step\ndata: {json.dumps({'step': sk, 'label': sl, 'progress': sp, 'status': 'completed'})}\n\n"
                        yield f"event: complete\ndata: {json.dumps({'status': db_status, 'forecast_run_id': str(run_id), 'message': 'Forecast run completed successfully'})}\n\n"
                        return

                    # For running status, emit step and advance after a delay
                    if db_status == "running":
                        await asyncio.sleep(3)
                        break  # advance to next step
                    else:
                        # queued — wait
                        await asyncio.sleep(2)
                        continue
                except Exception as e:
                    yield f"event: error\ndata: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                    return

        # After all steps emitted, keep polling for terminal state
        while True:
            if asyncio.get_event_loop().time() - started_at > max_wait:
                yield f"event: error\ndata: {json.dumps({'status': 'timeout', 'error': 'Forecast run timed out'})}\n\n"
                return
            await asyncio.sleep(2)
            try:
                stmt = select(ForecastRun).where(ForecastRun.id == run_id)
                result = await db.execute(stmt)
                run_record = result.scalars().first()
                if run_record and run_record.status in ("paused_for_review", "completed"):
                    yield f"event: complete\ndata: {json.dumps({'status': run_record.status, 'forecast_run_id': str(run_id), 'message': 'Forecast run completed successfully'})}\n\n"
                    return
                if run_record and run_record.status == "failed":
                    yield f"event: error\ndata: {json.dumps({'status': 'failed', 'error': 'Forecast run failed'})}\n\n"
                    return
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )