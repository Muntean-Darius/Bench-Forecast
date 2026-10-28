"""Forecast run orchestration.

The forecast is a two-phase, persisted operation:

1. ``create_forecast_run`` — inserts a ``forecast_runs`` row with status
   ``queued`` and returns its id immediately (so the API can respond without
   blocking on the LLM pipeline).
2. ``execute_forecast_run`` — runs the planner graph, persists the resulting
   allocation proposals + hiring recommendations, and flips the run status to
   ``paused_for_review`` (success) or ``failed`` (error).

All run state lives in PostgreSQL, so runs survive process restarts and there
is no in-memory checkpoint to lose.
"""

import logging
import time
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import select

from src.core.config import Config
from src.core.observability import span, workflow

from src.database.database import AsyncSessionLocal
from src.database.models import (
    Employee,
    ForecastRun,
    Allocation,
    AllocationStatusEnum,
    ProjectDemand,
    HiringRecommendation,
    HiringRecommendationStatusEnum,
)
from src.services.workforce_service import effective_capacity
from src.agents.graph import build_forecast_graph

logger = logging.getLogger(__name__)


async def create_forecast_run(horizon_days: int, user_id: uuid.UUID) -> uuid.UUID:
    """Insert a ForecastRun row with status='queued' and return its id."""
    forecast_run_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        db.add(ForecastRun(
            id=forecast_run_id,
            forecast_horizon_days=horizon_days,
            triggered_by=user_id,
            status="queued",
        ))
        await db.commit()
    logger.info(f"ForecastRun {forecast_run_id} queued by user {user_id} (horizon={horizon_days}d)")
    return forecast_run_id


async def _set_run_status(forecast_run_id: uuid.UUID, status: str) -> None:
    async with AsyncSessionLocal() as db:
        run = (await db.execute(
            select(ForecastRun).where(ForecastRun.id == forecast_run_id)
        )).scalars().first()
        if run:
            run.status = status
            await db.commit()


async def execute_forecast_run(
    forecast_run_id: uuid.UUID,
    horizon_days: int,
    user_id: uuid.UUID,
    project_id: Optional[uuid.UUID] = None,
    min_win_probability: float = 0.75,
) -> Dict[str, Any]:
    """Execute one forecast run inside an observability **workflow**.

    ``workflow_id`` is the persisted forecast-run id, so retries and the later
    HITL approve/reject actions join the same trace. The span tree underneath
    (LangGraph nodes, LLM/TOOL/RETRIEVER spans) is captured automatically by the
    SDK's instrumentors.
    """
    model_name = (
        Config.GROQ_MODEL
        if Config.LLM_PROVIDER == "groq"
        else Config.LLM_MODEL
        if Config.LLM_PROVIDER == "openai_compatible"
        else Config.OLLAMA_MODEL
    )
    context = {
        "horizon_days": horizon_days,
        "min_win_probability": min_win_probability,
        "project_id": str(project_id) if project_id else None,
        "llm_provider": Config.LLM_PROVIDER,
        "llm_model": model_name,
    }

    with workflow(
        name="bench-forecast",
        workflow_id=str(forecast_run_id),
        version="v2",
        context=context,
        user_id=str(user_id),
    ):
        return await _execute_forecast_run(
            forecast_run_id=forecast_run_id,
            horizon_days=horizon_days,
            user_id=user_id,
            project_id=project_id,
            min_win_probability=min_win_probability,
        )


async def _execute_forecast_run(
    forecast_run_id: uuid.UUID,
    horizon_days: int,
    user_id: uuid.UUID,
    project_id: Optional[uuid.UUID] = None,
    min_win_probability: float = 0.75,
) -> Dict[str, Any]:
    """Run the planner graph, persist proposals, and update the run status."""
    started_at = time.time()
    await _set_run_status(forecast_run_id, "running")

    initial_state = {
        # UUIDs kept as str for JSON-serialisation; nodes cast back to UUID at
        # the DB boundary.
        "forecast_run_id": str(forecast_run_id),
        "horizon_days": horizon_days,
        "user_id": str(user_id),
        "target_project_id": str(project_id) if project_id else None,
        "min_win_probability": min_win_probability,
        "revision_count": 0,
        "human_approved": False,
        "bench_employees": [],
        "open_demands": [],
        "skill_matches": [],
        "decisions": [],
    }

    try:
        graph = build_forecast_graph()
        logger.info(f"Invoking forecast planner for run {forecast_run_id}")
        with span(
            "planner_graph",
            context={
                "forecast_run_id": str(forecast_run_id),
                "horizon_days": horizon_days,
                "project_id": str(project_id) if project_id else None,
            },
        ):
            result_state = await graph.ainvoke(initial_state)

        open_demands = result_state.get("open_demands", [])
        decisions = result_state.get("decisions", [])
        bench_employees = result_state.get("bench_employees", [])

        async with AsyncSessionLocal() as db:
            # Replace stale pending proposals for the target scope.
            old_allocs_stmt = select(Allocation).where(
                Allocation.status.in_([
                    AllocationStatusEnum.pending_manager_approval,
                    AllocationStatusEnum.proposed_by_ai,
                ])
            )
            if project_id:
                old_allocs_stmt = (
                    old_allocs_stmt
                    .join(ProjectDemand, Allocation.demand_id == ProjectDemand.id)
                    .where(ProjectDemand.project_id == project_id)
                )
            old_allocs = (await db.execute(old_allocs_stmt)).scalars().all()
            for old_a in old_allocs:
                await db.delete(old_a)
            if old_allocs:
                await db.flush()

            bench_ids_set = {str(b["id"]) for b in bench_employees if b.get("id")}

            for demand in open_demands:
                demand_id_str = demand.get("id")
                if not demand_id_str:
                    continue
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
                    # Defense in depth: the decider grounds every id, but if an
                    # ungrounded one ever reaches here, degrade to an external
                    # hire instead of silently dropping the demand's proposal.
                    logger.error(
                        f"Employee {emp_id_str} in decision for demand "
                        f"{demand_id_str} is not in bench_employees; "
                        f"converting to an external-hire recommendation"
                    )
                    dec["action_type"] = "hire"
                    dec["employee_id"] = None
                    dec["justification"] = (
                        f"No valid internal candidate could be resolved for "
                        f"'{demand.get('role', 'role')}'. External hire recommended."
                    )
                    continue

                margin_val = float(dec.get("projected_margin", 0.0))
                match_score_val = float(dec.get("match_score") or 0.0)
                if match_score_val <= 0:
                    match_score_val = float(dec.get("similarity_score") or 0.0)

                hours = Decimal(str(
                    demand.get("hours_per_day")
                    or dec.get("hours_per_day")
                    or 8
                ))
                employee = await db.get(Employee, uuid.UUID(emp_id_str))
                capacity = effective_capacity(employee) if employee else Decimal("8")
                pct = (
                    (hours / capacity * Decimal("100")).quantize(Decimal("0.01"))
                    if capacity > 0
                    else Decimal("100.00")
                )

                db.add(Allocation(
                    id=uuid.uuid4(),
                    employee_id=uuid.UUID(emp_id_str),
                    demand_id=uuid.UUID(demand_id_str),
                    role_on_project=demand.get("role", "Engineer"),
                    allocated_hours_per_day=hours,
                    allocation_pct=pct,
                    status=AllocationStatusEnum.pending_manager_approval,
                    ai_justification=(
                        dec.get("justification")
                        or f"Proposed allocation for {demand.get('role', 'Role')}."
                    ),
                    ai_match_score=Decimal(str(round(match_score_val, 4))),
                    ai_projected_margin=Decimal(str(round(margin_val, 2))),
                    ai_upskilling_path=dec.get("upskilling_path"),
                    forecast_run_id=forecast_run_id,
                    created_by=user_id,
                    start_date=date.today(),
                ))

            # External-hire recommendations for demands the agent could not staff.
            old_hire_stmt = select(HiringRecommendation).where(
                HiringRecommendation.status == HiringRecommendationStatusEnum.open
            )
            if project_id:
                old_hire_stmt = old_hire_stmt.where(
                    HiringRecommendation.project_id == project_id
                )
            for old_hire in (await db.execute(old_hire_stmt)).scalars().all():
                await db.delete(old_hire)

            for demand in open_demands:
                demand_id_str = demand.get("id")
                if not demand_id_str:
                    continue
                dec = next(
                    (d for d in decisions if str(d.get("role_id")) == demand_id_str),
                    None,
                )
                if not dec or dec.get("action_type") != "hire":
                    continue
                demand_project_id = demand.get("project_id")
                db.add(HiringRecommendation(
                    id=uuid.uuid4(),
                    project_id=uuid.UUID(demand_project_id) if demand_project_id else None,
                    demand_id=uuid.UUID(demand_id_str),
                    role=demand.get("role") or dec.get("role_title") or "Engineer",
                    required_skills=demand.get("required_skills") or [],
                    headcount=1,
                    justification=dec.get("justification") or (
                        f"External hire recommended for {demand.get('role', 'role')}: "
                        f"no bench employee matches the required skills."
                    ),
                    status=HiringRecommendationStatusEnum.open,
                    forecast_run_id=forecast_run_id,
                    created_by=user_id,
                ))

            # ---- Run observability / evaluation metrics --------------------
            mix = {"allocate": 0, "train": 0, "hire": 0}
            for d in decisions:
                a = d.get("action_type")
                if a in mix:
                    mix[a] += 1
            if Config.LLM_PROVIDER == "groq":
                model_name = Config.GROQ_MODEL
            elif Config.LLM_PROVIDER == "openai_compatible":
                model_name = Config.LLM_MODEL
            else:
                model_name = Config.OLLAMA_MODEL

            run = (await db.execute(
                select(ForecastRun).where(ForecastRun.id == forecast_run_id)
            )).scalars().first()
            if run:
                run.status = "paused_for_review"
                run.decision_metrics = {
                    "decisions": len(decisions),
                    "allocate": mix["allocate"],
                    "train": mix["train"],
                    "hire": mix["hire"],
                    "demands": len(open_demands),
                    "bench": len(bench_employees),
                    "skill_matches": len(result_state.get("skill_matches", [])),
                    # Full plan (deterministic summary + advisory alternatives)
                    # retained for audit/HITL context.
                    "plan": result_state.get("recommendations", {}),
                }
                run.latency_seconds = Decimal(str(round(time.time() - started_at, 2)))
                run.llm_provider = Config.LLM_PROVIDER
                run.llm_model = model_name
            await db.commit()
            logger.info(f"Forecast run {forecast_run_id} paused for review; proposals persisted.")

        return result_state

    except Exception:
        logger.exception(f"Forecast run {forecast_run_id} failed")
        try:
            await _set_run_status(forecast_run_id, "failed")
        except Exception:
            logger.exception(f"Could not mark run {forecast_run_id} as failed")
        raise


async def run_forecast_pipeline(
    horizon_days: int,
    user_id: uuid.UUID,
    project_id: Optional[uuid.UUID] = None,
    min_win_probability: float = 0.75,
) -> Dict[str, Any]:
    """Convenience wrapper: create + execute in one call (used by tests)."""
    run_id = await create_forecast_run(horizon_days, user_id)
    return await execute_forecast_run(
        run_id, horizon_days, user_id, project_id, min_win_probability
    )
