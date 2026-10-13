import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict

from sqlalchemy import select, update

from src.database.database import AsyncSessionLocal
from src.database.models import ForecastRun, Allocation, AllocationStatusEnum
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

        # Persist proposed allocations and update run status
        try:
            open_demands = result_state.get("open_demands", [])
            decisions = result_state.get("decisions", [])
            skill_matches = result_state.get("skill_matches", [])
            bench_employees = result_state.get("bench_employees", [])

            async with AsyncSessionLocal() as db:
                run_stmt = select(ForecastRun).where(ForecastRun.id == forecast_run_id)
                run_res = await db.execute(run_stmt)
                run_rec = run_res.scalars().first()
                if run_rec:
                    run_rec.status = "paused_for_review"

                # A new forecast run supersedes any proposals still awaiting
                # review from previous runs. Cancel them first so the manager
                # queue never shows the same employee twice (e.g. as a direct
                # allocation from an old run AND a training allocation from
                # this run). Cancelled rows are retained for audit history.
                superseded = await db.execute(
                    update(Allocation)
                    .where(
                        Allocation.status.in_([
                            AllocationStatusEnum.pending_manager_approval,
                            AllocationStatusEnum.proposed_by_ai,
                        ])
                    )
                    .values(status=AllocationStatusEnum.cancelled)
                )
                logger.info(
                    f"Superseded {superseded.rowcount if superseded.rowcount is not None else 'existing'} "
                    f"stale pending allocation(s) for new forecast run {forecast_run_id}."
                )

                bench_ids_set = {str(b["id"]) for b in bench_employees if b.get("id")}

                for demand in open_demands:
                    demand_id_str = demand.get("id")
                    if not demand_id_str:
                        continue
                    demand_uuid = uuid.UUID(demand_id_str)

                    # Look for decision matching this demand where action_type is allocate or train
                    dec = next(
                        (
                            d for d in decisions
                            if str(d.get("role_id")) == demand_id_str
                            and d.get("action_type") in ("allocate", "train")
                            and d.get("employee_id")
                        ),
                        None,
                    )

                    if not dec or not dec.get("employee_id"):
                        continue

                    emp_id_str = str(dec["employee_id"])
                    if emp_id_str not in bench_ids_set:
                        logger.warning(
                            f"Employee {emp_id_str} in decision for demand {demand_id_str} is not in bench_employees"
                        )
                        continue

                    emp_uuid = uuid.UUID(emp_id_str)
                    margin_val = float(dec.get("projected_margin", 0.0))
                    match_score_val = float(dec.get("match_score") or 0.0)
                    if match_score_val <= 0:
                        # Fall back to the similarity score attached by the
                        # allocation_decider_node when the LLM omitted match_score.
                        match_score_val = float(dec.get("similarity_score") or 0.0)
                    justification_val = (
                        dec.get("justification")
                        or f"Proposed allocation for {demand.get('role', 'Role')}."
                    )
                    upskill_val = dec.get("upskilling_path")

                    alloc = Allocation(
                        id=uuid.uuid4(),
                        employee_id=emp_uuid,
                        demand_id=demand_uuid,
                        role_on_project=demand.get("role", "Engineer"),
                        allocation_pct=Decimal("100.00"),
                        status=AllocationStatusEnum.pending_manager_approval,
                        ai_justification=justification_val,
                        ai_match_score=Decimal(str(round(match_score_val, 4))),
                        ai_projected_margin=Decimal(str(round(margin_val, 2))),
                        ai_upskilling_path=upskill_val,
                        forecast_run_id=forecast_run_id,
                        created_by=user_id,
                        start_date=date.today(),
                    )
                    db.add(alloc)

                await db.commit()
                logger.info(f"Successfully persisted pending allocations for forecast run {forecast_run_id}.")
        except Exception as persist_err:
            logger.exception(f"Failed to persist pending allocations to database: {persist_err}")
            raise

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
