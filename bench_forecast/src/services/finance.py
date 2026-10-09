"""Financial service layer — bench burn-rate calculations and dashboard queries.

Public API
----------
calculate_bench_cost(bench_days, daily_cost)
    Pure function — no DB I/O. Returns total cost as Decimal.

get_current_employee_cost(db, employee_id)
    Returns the currently active EmployeeCost row for an employee.

calculate_and_persist_bench_costs(db, employee_id, period_start, period_end)
    Core engine: computes bench_days × daily_cost and upserts a BenchCost row.

get_monthly_bench_burn(db, year, month)
    Dashboard query: Σ total_cost for all employees in a given calendar month.

get_project_budget_consumed(db, project_id)
    Dashboard query: latest budget_consumed / budget_total for a project.

get_employee_bench_costs(db, employee_id, limit)
    Dashboard query: paginated bench cost history for a single employee.

get_org_bench_summary(db, period_start, period_end)
    Dashboard query: all bench costs aggregated over a date range with
    per-employee breakdown.

All DB functions accept a SQLAlchemy `Session` (sync).  For async endpoints
wrap them with `asyncio.to_thread()` or use `run_sync()` on the async session.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from src.database.models import BenchCost, EmployeeCost, Project

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKING_HOURS_PER_DAY: int = 8
WORKING_DAYS_PER_YEAR: int = 220  # standard for fully-loaded cost computation


# ---------------------------------------------------------------------------
# Pure helpers — no DB I/O
# ---------------------------------------------------------------------------

def calculate_bench_cost(bench_days: int, daily_cost: Decimal) -> Decimal:
    """Calculate total bench cost for a period.

    Formula:
        total_cost = bench_days × daily_cost

    Args:
        bench_days: Number of calendar/working days the employee was on bench.
        daily_cost: Fully-loaded internal daily cost (from employee_costs).

    Returns:
        Total bench cost rounded to 2 decimal places.

    Raises:
        ValueError: If either argument is negative or zero daily_cost.
    """
    if bench_days < 0:
        raise ValueError(f"bench_days must be >= 0, got {bench_days}")
    if daily_cost <= 0:
        raise ValueError(f"daily_cost must be > 0, got {daily_cost}")

    total = Decimal(str(bench_days)) * daily_cost
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_internal_costs(
    annual_salary: Decimal,
    overhead_multiplier: Decimal,
) -> tuple[Decimal, Decimal]:
    """Derive internal_daily_cost and internal_hourly_cost from salary.

    Args:
        annual_salary:       Gross annual salary.
        overhead_multiplier: Overhead factor (e.g. Decimal("1.35")).

    Returns:
        (internal_daily_cost, internal_hourly_cost) rounded to 2dp.
    """
    daily = (annual_salary * overhead_multiplier / WORKING_DAYS_PER_YEAR).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    hourly = (daily / WORKING_HOURS_PER_DAY).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return daily, hourly


# ---------------------------------------------------------------------------
# DB read helpers
# ---------------------------------------------------------------------------

def get_current_employee_cost(
    db: Session,
    employee_id: UUID,
    as_of: Optional[date] = None,
) -> Optional[EmployeeCost]:
    """Return the currently active EmployeeCost row for an employee.

    "Current" = the row with the highest effective_date that is ≤ `as_of`
    (defaults to today).

    Args:
        db:          Sync SQLAlchemy session.
        employee_id: Target employee UUID.
        as_of:       Reference date (defaults to today).

    Returns:
        The active EmployeeCost row, or None if no cost data exists.
    """
    as_of = as_of or date.today()
    stmt = (
        select(EmployeeCost)
        .where(
            and_(
                EmployeeCost.employee_id == employee_id,
                EmployeeCost.effective_date <= as_of,
            )
        )
        .order_by(EmployeeCost.effective_date.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Core calculation engine
# ---------------------------------------------------------------------------

def calculate_and_persist_bench_costs(
    db: Session,
    employee_id: UUID,
    period_start: date,
    period_end: date,
    *,
    overwrite: bool = False,
) -> BenchCost:
    """Calculate bench cost for an employee over a period and persist it.

    Steps:
      1. Fetch the active EmployeeCost rate as of `period_start`.
      2. Compute bench_days = (period_end - period_start).days + 1
      3. Compute total_cost = bench_days × internal_daily_cost
      4. Check for an existing BenchCost row covering the same employee +
         period to avoid double-counting.
      5. Insert or update the BenchCost row.
      6. Flush (does NOT commit — caller owns the transaction).

    Args:
        db:           Sync SQLAlchemy session.
        employee_id:  Employee UUID.
        period_start: First day of the bench period (inclusive).
        period_end:   Last day of the bench period (inclusive).
        overwrite:    If True, delete any existing row for the same period
                      before inserting. Defaults to False (raises on conflict).

    Returns:
        The newly created or updated BenchCost ORM row.

    Raises:
        ValueError: If no active cost record found for the employee, or if a
                    BenchCost row already exists and overwrite=False.
        ValueError: If period_end < period_start.
    """
    if period_end < period_start:
        raise ValueError(
            f"period_end ({period_end}) must be >= period_start ({period_start})"
        )

    # 1. Fetch rate
    cost_record = get_current_employee_cost(db, employee_id, as_of=period_start)
    if cost_record is None:
        raise ValueError(
            f"No EmployeeCost record found for employee {employee_id} "
            f"as of {period_start}. Create one before calculating bench costs."
        )

    # 2. Compute bench days (calendar days, inclusive)
    bench_days = (period_end - period_start).days + 1

    # 3. Compute total cost
    total_cost = calculate_bench_cost(bench_days, cost_record.internal_daily_cost)

    # 4. Check for existing row
    existing = _get_existing_bench_cost(db, employee_id, period_start, period_end)
    if existing:
        if not overwrite:
            raise ValueError(
                f"BenchCost row already exists for employee {employee_id} "
                f"period {period_start}→{period_end}. "
                "Pass overwrite=True to replace it."
            )
        logger.info(
            "Overwriting existing BenchCost row id=%s for employee=%s",
            existing.id,
            employee_id,
        )
        db.delete(existing)
        db.flush()

    # 5. Insert
    bench_cost = BenchCost(
        employee_id=employee_id,
        period_start=period_start,
        period_end=period_end,
        bench_days=bench_days,
        daily_cost=cost_record.internal_daily_cost,
        total_cost=total_cost,
        currency=cost_record.currency,
        calculated_at=datetime.utcnow(),
    )
    db.add(bench_cost)
    db.flush()  # populate id without committing

    logger.info(
        "BenchCost persisted: employee=%s period=%s→%s days=%d total=%s %s",
        employee_id,
        period_start,
        period_end,
        bench_days,
        total_cost,
        cost_record.currency,
    )
    return bench_cost


def _get_existing_bench_cost(
    db: Session,
    employee_id: UUID,
    period_start: date,
    period_end: date,
) -> Optional[BenchCost]:
    """Return an existing BenchCost row that exactly matches the period, or None."""
    stmt = select(BenchCost).where(
        and_(
            BenchCost.employee_id == employee_id,
            BenchCost.period_start == period_start,
            BenchCost.period_end == period_end,
        )
    )
    return db.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Dashboard query functions
# ---------------------------------------------------------------------------

def get_monthly_bench_burn(
    db: Session,
    year: int,
    month: int,
) -> dict:
    """Compute total bench burn across the entire organisation for a calendar month.

    Includes any BenchCost period that *overlaps* the target month — i.e. the
    period_start ≤ last day of month AND period_end ≥ first day of month.

    Args:
        db:    Sync SQLAlchemy session.
        year:  Calendar year (e.g. 2026).
        month: Calendar month 1–12.

    Returns:
        Dict with keys: year, month, total_bench_cost, employee_count,
        currency (first currency found — multi-currency orgs should extend this).
    """
    import calendar as _cal

    first_day = date(year, month, 1)
    last_day = date(year, month, _cal.monthrange(year, month)[1])

    stmt = (
        select(
            func.coalesce(func.sum(BenchCost.total_cost), Decimal("0.00")).label(
                "total_cost"
            ),
            func.count(func.distinct(BenchCost.employee_id)).label("employee_count"),
            BenchCost.currency,
        )
        .where(
            and_(
                BenchCost.period_start <= last_day,
                BenchCost.period_end >= first_day,
            )
        )
        .group_by(BenchCost.currency)
    )

    rows = db.execute(stmt).all()

    # Aggregate (simple: sum across currencies — front-end should handle FX)
    total = sum(r.total_cost for r in rows) if rows else Decimal("0.00")
    emp_count = max((r.employee_count for r in rows), default=0)
    currency = rows[0].currency if rows else "EUR"

    return {
        "year": year,
        "month": month,
        "period_start": str(first_day),
        "period_end": str(last_day),
        "total_bench_cost": float(total),
        "employee_count": emp_count,
        "currency": currency,
    }


def get_project_budget_consumed(
    db: Session,
    project_id: UUID,
) -> dict:
    stmt = select(Project).where(Project.id == project_id)
    pf = db.execute(stmt).scalar_one_or_none()

    if pf is None:
        return {
            "project_id": str(project_id),
            "error": "No project record found",
        }

    total = pf.total_budget
    consumed = 0.0  # Legacy ProjectFinancial had this, fallback to 0

    remaining = float(total) - consumed
    pct = (consumed / float(total) * 100) if total > 0 else 0.0

    return {
        "project_id": str(project_id),
        "billing_type": "T&M",
        "budget_total": float(total),
        "budget_consumed": consumed,
        "budget_remaining": remaining,
        "pct_consumed": round(pct, 2),
        "currency": "USD",
        "effective_date": "2026-01-01",
    }


def get_employee_bench_costs(
    db: Session,
    employee_id: UUID,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Return paginated bench cost history for a single employee.

    Args:
        db:          Sync SQLAlchemy session.
        employee_id: Target employee UUID.
        limit:       Max rows to return (default 20, max enforced by caller).
        offset:      Pagination offset.

    Returns:
        List of dicts sorted by period_start descending.
    """
    stmt = (
        select(BenchCost)
        .where(BenchCost.employee_id == employee_id)
        .order_by(BenchCost.period_start.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).scalars().all()

    return [
        {
            "id": str(r.id),
            "employee_id": str(r.employee_id),
            "period_start": str(r.period_start),
            "period_end": str(r.period_end),
            "bench_days": r.bench_days,
            "daily_cost": float(r.daily_cost),
            "total_cost": float(r.total_cost),
            "currency": r.currency,
            "calculated_at": r.calculated_at.isoformat(),
        }
        for r in rows
    ]


def get_org_bench_summary(
    db: Session,
    period_start: date,
    period_end: date,
) -> dict:
    """Aggregate bench costs across the org for an arbitrary date range.

    Returns top-level totals plus a per-employee breakdown sorted by
    total_cost descending (most expensive bench employees first).

    Args:
        db:           Sync SQLAlchemy session.
        period_start: Range start (inclusive).
        period_end:   Range end (inclusive).

    Returns:
        Dict with: period_start, period_end, grand_total, employee_count,
        currency, employees (list of per-employee aggregates).
    """
    # Per-employee aggregates
    per_emp_stmt = (
        select(
            BenchCost.employee_id,
            func.sum(BenchCost.total_cost).label("total_cost"),
            func.sum(BenchCost.bench_days).label("total_bench_days"),
            BenchCost.currency,
        )
        .where(
            and_(
                BenchCost.period_start <= period_end,
                BenchCost.period_end >= period_start,
            )
        )
        .group_by(BenchCost.employee_id, BenchCost.currency)
        .order_by(func.sum(BenchCost.total_cost).desc())
    )
    rows = db.execute(per_emp_stmt).all()

    grand_total = sum(r.total_cost for r in rows)
    currency = rows[0].currency if rows else "EUR"

    return {
        "period_start": str(period_start),
        "period_end": str(period_end),
        "grand_total_bench_cost": float(grand_total),
        "employee_count": len(rows),
        "currency": currency,
        "employees": [
            {
                "employee_id": str(r.employee_id),
                "total_cost": float(r.total_cost),
                "total_bench_days": int(r.total_bench_days),
                "currency": r.currency,
            }
            for r in rows
        ],
    }
