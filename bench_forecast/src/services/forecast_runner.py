"""Service layer wrapper for executing the LangGraph forecast pipeline."""

import logging
import uuid
from typing import Any, Dict

from sqlalchemy import select

from src.database.database import AsyncSessionLocal
from src.database.models import ForecastRun
from src.agents.graph import build_forecast_graph

logger = logging.getLogger(__name__)

async def run_forecast_pipeline(horizon_days: int, user_id: uuid.UUID) -> Dict[str, Any]:
    """
    Executes the forecast pipeline up to the human_review_node interrupt.
    
    1. Creates a new forecast_runs row in PostgreSQL.
    2. Initializes the LangGraph state with forecast_run_id.
    3. Invokes the graph until the human_review_node interrupt fires.
    
    Args:
        horizon_days (int): Look-ahead window in days.
        user_id (uuid.UUID): ID of the user triggering the run.
        
    Returns:
        Dict[str, Any]: The final state of the graph execution when it pauses.
    """
    forecast_run_id = uuid.uuid4()
    
    # 1. Create a new forecast_runs row in PostgreSQL
    logger.info(f"Creating ForecastRun {forecast_run_id} triggered by user {user_id}")
    try:
        async with AsyncSessionLocal() as db:
            new_run = ForecastRun(
                id=forecast_run_id,
                forecast_horizon_days=horizon_days,
                triggered_by=user_id,
                status="in_progress"
            )
            db.add(new_run)
            await db.commit()
    except Exception as e:
        logger.exception("Failed to create forecast_runs record")
        raise
        
    # 2. Initialize the LangGraph state
    initial_state = {
        # Cast UUIDs to str — LangGraph's MemorySaver checkpoint serialiser uses
        # json.dumps without a custom encoder, so raw uuid.UUID objects will raise
        # TypeError. All DB-boundary nodes cast back to uuid.UUID when needed.
        "forecast_run_id": str(forecast_run_id),
        "horizon_days": horizon_days,
        "user_id": str(user_id),
        "min_win_probability": 0.75,
        "revision_count": 0,
        "human_approved": False,
        "bench_employees": [],
        "open_demands": [],
        "skill_matches": [],
        "decisions": [],
    }
    
    # 3. Invoke the graph until it pauses
    try:
        graph = build_forecast_graph()
        
        # Configuration required for checkpointing (MemorySaver)
        config = {"configurable": {"thread_id": str(forecast_run_id)}}
        
        logger.info(f"Invoking forecast pipeline for run {forecast_run_id}")
        
        # We invoke the graph asynchronously
        # Because we have interrupt_before=["human_review"], it will execute
        # the extraction, matching, and planning nodes, and then return the state.
        result_state = await graph.ainvoke(initial_state, config=config)
        
        logger.info(f"Forecast pipeline paused for run {forecast_run_id}. Waiting for HITL.")
        return result_state
        
    except Exception as e:
        logger.exception(f"Forecast pipeline execution failed for run {forecast_run_id}")
        
        # Update status to failed
        try:
            async with AsyncSessionLocal() as db:
                run_stmt = select(ForecastRun).where(ForecastRun.id == forecast_run_id)
                run_res = await db.execute(run_stmt)
                run_rec = run_res.scalars().first()
                if run_rec:
                    run_rec.status = "failed"
                    await db.commit()
        except Exception:
            pass
            
        raise
