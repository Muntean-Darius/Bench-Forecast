import uuid
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
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
