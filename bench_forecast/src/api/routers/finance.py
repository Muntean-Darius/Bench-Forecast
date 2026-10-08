"""FastAPI router — Phase 3 Financial Dashboard.

Mount prefix: /api/v1/finance

Endpoints
---------

Employee cost management:
  POST   /finance/employee-costs
      Create a new EmployeeCost record.
      The server auto-computes internal_daily_cost and internal_hourly_cost
      from annual_salary + overhead_multiplier if not explicitly provided.

  GET    /finance/employee-costs/{employee_id}
      Return the currently active cost record for an employee.

Project financials:
  POST   /finance/project-financials
      Create or add a new ProjectFinancial configuration for a project.

  GET    /finance/projects/{project_id}/budget
      Return current budget consumed vs. total for a project.

Bench cost calculation:
  POST   /finance/bench-costs/calculate
      Trigger the bench burn-rate engine for one employee over a period.
      Returns the created/updated BenchCost record.

  GET    /finance/bench-costs/employee/{employee_id}
      Paginated bench cost history for a single employee.

Dashboard aggregates:
  GET    /finance/bench-burn/monthly?year=&month=
      Total bench burn across the org for a calendar month.

  GET    /finance/bench-costs/summary?period_start=&period_end=
      Org-wide bench cost summary with per-employee breakdown.

All write operations are wrapped in a single database transaction. Reads use a
read-only dependency to signal intent (same session, no autocommit difference
but documents the pattern clearly).

Error handling:
  404 — Employee / project not found or no cost record exists.
  409 — BenchCost row already exists for the same period (overwrite=False).
  422 — Pydantic validation failure (FastAPI default).
  500 — Unexpected service-layer error.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.database import get_sync_db
from src.database.models import BenchCost, EmployeeCost, Project
from src.schemas.financial import (
    BenchBurnCalculateRequest,
    BenchCostRead,
    EmployeeBenchCostItem,
    EmployeeCostCreate,
    EmployeeCostRead,
    MonthlyBenchBurnResponse,
    OrgBenchSummaryResponse,
    ProjectBudgetResponse,
    ProjectFinancialCreate,
    ProjectFinancialRead,
)
from src.services.finance import (
    calculate_and_persist_bench_costs,
    compute_internal_costs,
    get_current_employee_cost,
    get_employee_bench_costs,
    get_monthly_bench_burn,
    get_org_bench_summary,
    get_project_budget_consumed,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/finance",
    tags=["Financial Dashboard"],
)


# ---------------------------------------------------------------------------
# Sync DB dependency (matches the existing `get_sync_db` context manager)
# ---------------------------------------------------------------------------

def _get_db() -> Session:  # type: ignore[return]
    """Yield a synchronous SQLAlchemy session as a FastAPI dependency."""
    with get_sync_db() as session:
        yield session


DbDep = Annotated[Session, Depends(_get_db)]


# =============================================================================
# Employee Cost endpoints
# =============================================================================

@router.post(
    "/employee-costs",
    response_model=EmployeeCostRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create employee cost record",
    description=(
        "Insert a new fully-loaded cost rate for an employee, effective from "
        "`effective_date`. internal_daily_cost and internal_hourly_cost are "
        "computed from annual_salary × overhead_multiplier if not supplied."
    ),
)
def create_employee_cost(
    payload: EmployeeCostCreate,
    db: DbDep,
) -> EmployeeCostRead:
    """Create a new EmployeeCost row."""
    # Auto-compute derived costs if not provided
    if payload.internal_daily_cost is None or payload.internal_hourly_cost is None:
        daily, hourly = compute_internal_costs(
            payload.annual_salary, payload.overhead_multiplier
        )
        daily_cost = payload.internal_daily_cost or daily
        hourly_cost = payload.internal_hourly_cost or hourly
    else:
        daily_cost = payload.internal_daily_cost
        hourly_cost = payload.internal_hourly_cost

    record = EmployeeCost(
        employee_id=payload.employee_id,
        effective_date=payload.effective_date,
        annual_salary=payload.annual_salary,
        currency=payload.currency,
        overhead_multiplier=payload.overhead_multiplier,
        internal_daily_cost=daily_cost,
        internal_hourly_cost=hourly_cost,
    )

    try:
        db.add(record)
        db.flush()
        logger.info(
            "EmployeeCost created: employee=%s effective=%s daily=%s",
            payload.employee_id,
            payload.effective_date,
            daily_cost,
        )
        return EmployeeCostRead.model_validate(record)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Integrity error creating EmployeeCost: {exc.orig}",
        ) from exc


@router.get(
    "/employee-costs/{employee_id}",
    response_model=EmployeeCostRead,
    summary="Get current employee cost",
    description=(
        "Return the currently active EmployeeCost record for an employee "
        "(highest effective_date ≤ today). Pass ?as_of=YYYY-MM-DD to query "
        "the rate at a specific past date."
    ),
)
def get_employee_cost(
    employee_id: UUID,
    db: DbDep,
    as_of: date | None = Query(
        default=None,
        description="Reference date for cost lookup (defaults to today)",
    ),
) -> EmployeeCostRead:
    """Fetch the active cost record for a specific employee."""
    record = get_current_employee_cost(db, employee_id, as_of=as_of)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No EmployeeCost record found for employee {employee_id}"
                + (f" as of {as_of}" if as_of else "")
            ),
        )
    return EmployeeCostRead.model_validate(record)


# =============================================================================
# Project Financial endpoints
# =============================================================================




@router.get(
    "/projects/{project_id}/budget",
    response_model=ProjectBudgetResponse,
    summary="Get project budget consumed",
    description=(
        "Return the current budget consumed vs. total for a project. "
        "Reports budget_remaining and pct_consumed for the dashboard."
    ),
)
def get_project_budget(
    project_id: UUID,
    db: DbDep,
) -> ProjectBudgetResponse:
    """Fetch the budget status for a specific project."""
    result = get_project_budget_consumed(db, project_id)
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )
    return ProjectBudgetResponse(**result)


# =============================================================================
# Bench Cost endpoints
# =============================================================================

@router.post(
    "/bench-costs/calculate",
    response_model=BenchCostRead,
    status_code=status.HTTP_201_CREATED,
    summary="Calculate and persist bench cost",
    description=(
        "Run the bench burn-rate engine for one employee over a specific period. "
        "Fetches the active EmployeeCost rate, computes bench_days × daily_cost, "
        "and writes a BenchCost row. Set overwrite=true to replace an existing row "
        "for the same period (409 Conflict if overwrite=false and row exists)."
    ),
)
def calculate_bench_cost_endpoint(
    payload: BenchBurnCalculateRequest,
    db: DbDep,
) -> BenchCostRead:
    """Trigger bench cost calculation for one employee."""
    try:
        record = calculate_and_persist_bench_costs(
            db,
            employee_id=payload.employee_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            overwrite=payload.overwrite,
        )
        return BenchCostRead.model_validate(record)
    except ValueError as exc:
        msg = str(exc)
        if "already exists" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=msg
            ) from exc
        if "No EmployeeCost record" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=msg
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error in bench cost calculation")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bench cost calculation failed: {exc}",
        ) from exc


@router.get(
    "/bench-costs/employee/{employee_id}",
    response_model=list[EmployeeBenchCostItem],
    summary="Get bench cost history for an employee",
    description=(
        "Return paginated bench cost history for a single employee, "
        "sorted by period_start descending."
    ),
)
def get_employee_bench_cost_history(
    employee_id: UUID,
    db: DbDep,
    limit: int = Query(default=20, ge=1, le=100, description="Max rows"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
) -> list[EmployeeBenchCostItem]:
    """Fetch bench cost history for an employee."""
    rows = get_employee_bench_costs(db, employee_id, limit=limit, offset=offset)
    return [EmployeeBenchCostItem(**r) for r in rows]


# =============================================================================
# Dashboard aggregate endpoints
# =============================================================================

@router.get(
    "/bench-burn/monthly",
    response_model=MonthlyBenchBurnResponse,
    summary="Monthly bench burn (org-wide)",
    description=(
        "Return total bench cost burned across the entire organisation for a "
        "given calendar month. Includes any BenchCost period overlapping the month."
    ),
)
def monthly_bench_burn(
    db: DbDep,
    year: int = Query(..., ge=2000, le=2100, description="Calendar year"),
    month: int = Query(..., ge=1, le=12, description="Calendar month (1–12)"),
) -> MonthlyBenchBurnResponse:
    """Org-wide monthly bench burn total."""
    try:
        result = get_monthly_bench_burn(db, year=year, month=month)
        return MonthlyBenchBurnResponse(**result)
    except Exception as exc:
        logger.exception("Error computing monthly bench burn")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute monthly bench burn: {exc}",
        ) from exc


@router.get(
    "/bench-costs/summary",
    response_model=OrgBenchSummaryResponse,
    summary="Org bench cost summary (date range)",
    description=(
        "Return total bench cost across the org for an arbitrary date range, "
        "with a per-employee breakdown sorted by total cost descending. "
        "Useful for executive dashboards and sprint retrospectives."
    ),
)
def org_bench_summary(
    db: DbDep,
    period_start: date = Query(..., description="Range start (inclusive) YYYY-MM-DD"),
    period_end: date = Query(..., description="Range end (inclusive) YYYY-MM-DD"),
) -> OrgBenchSummaryResponse:
    """Org-wide bench cost summary for a date range."""
    if period_end < period_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_end must be >= period_start",
        )
    try:
        result = get_org_bench_summary(db, period_start=period_start, period_end=period_end)
        return OrgBenchSummaryResponse(**result)
    except Exception as exc:
        logger.exception("Error computing org bench summary")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to compute bench summary: {exc}",
        ) from exc
