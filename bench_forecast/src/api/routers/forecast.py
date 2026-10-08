import uuid
import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.api.deps import get_current_user
from src.database.database import get_db
from src.database.models import ForecastRun
from src.services.forecast_runner import run_forecast_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/forecast",
    tags=["Forecast Execution"],
)

class RunForecastRequest(BaseModel):
    horizon_days: int = Field(default=90, ge=30, le=365, description="Look-ahead window in days")

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
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    Triggers the forecast pipeline asynchronously.
    Returns the forecast_run_id and initial paused state metadata.
    """
    user_uuid = uuid.UUID(current_user["id"])
    logger.info(f"User {user_uuid} triggering forecast run with horizon {payload.horizon_days} days.")
    
    try:
        # run_forecast_pipeline blocks until human_review_node interrupt fires,
        # at which point it returns the paused state.
        result_state = await run_forecast_pipeline(
            horizon_days=payload.horizon_days,
            user_id=user_uuid
        )
        
        forecast_run_id = result_state.get("forecast_run_id")
        if not forecast_run_id:
            raise ValueError("Pipeline did not return a forecast_run_id in state.")
            
        return RunForecastResponse(
            forecast_run_id=forecast_run_id,
            status="paused_for_review",
            state_metadata={
                "revision_count": result_state.get("revision_count", 0),
                "decisions": result_state.get("decisions", []),
                "min_win_probability": result_state.get("min_win_probability", 0.75)
            }
        )
    except Exception as e:
        logger.error(f"Failed to trigger forecast run: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Forecast pipeline execution failed: {str(e)}"
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
