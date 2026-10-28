"""Analytics endpoints for the dashboard — real, data-backed time series.

Everything here is computed from persisted rows (no synthetic projections):

* ``GET /api/v1/analytics/bench-trend``       — weekly bench vs allocated headcount,
  reconstructed from ``bench_history``.
* ``GET /api/v1/analytics/bench-burn-trend``  — monthly (prorated) bench burn from
  ``bench_costs``.
"""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Any, Dict, List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.database import get_sync_db
from src.database.models import (
    Allocation,
    AllocationStatusEnum,
    BenchCost,
    BenchHistory,
    Employee,
    EmployeeCost,
    KpiSnapshot,
    Project,
    ProjectDemand,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


def _get_db() -> Session:  # type: ignore[return]
    with get_sync_db() as session:
        yield session


DbDep = Annotated[Session, Depends(_get_db)]


@router.get("/bench-trend", status_code=status.HTTP_200_OK)
def bench_trend(
    db: DbDep,
    weeks: int = Query(default=8, ge=2, le=26, description="Number of trailing weeks"),
) -> Dict[str, Any]:
    """Weekly bench headcount reconstructed from bench_history.

    For each week (oldest → newest) we count employees whose bench period
    covers that week's date. ``allocated`` is total headcount minus that.
    """
    total = db.scalar(
        select(func.count(Employee.id)).where(Employee.is_active == True)  # noqa: E712
    ) or 0
    rows: List[BenchHistory] = list(db.execute(select(BenchHistory)).scalars().all())

    today = date.today()
    series = []
    for i in range(weeks):
        week_start = today - timedelta(days=(weeks - 1 - i) * 7)
        on_bench = sum(
            1 for h in rows
            if h.bench_start
            and h.bench_start <= week_start
            and (h.bench_end is None or h.bench_end >= week_start)
        )
        series.append({
            "week_start": week_start.isoformat(),
            "label": week_start.strftime("%d %b"),
            "on_bench": on_bench,
            "allocated": max(total - on_bench, 0),
        })

    return {"total_employees": total, "weeks": series}


@router.get("/bench-burn-trend", status_code=status.HTTP_200_OK)
def bench_burn_trend(
    db: DbDep,
    months: int = Query(default=6, ge=2, le=24, description="Number of trailing months"),
) -> Dict[str, Any]:
    """Monthly internal-cost trend (real data), last ``months`` months.

    Two series per month:
    * ``total_bench_cost``      — bench burn, prorated per day, from bench_costs.
    * ``total_workforce_cost``  — fully-loaded internal cost of the whole active
                                  workforce for that month (Σ daily cost × days),
                                  from employee_costs.
    """
    bench_rows: List[BenchCost] = list(db.execute(select(BenchCost)).scalars().all())

    # Latest fully-loaded daily internal cost per active employee.
    daily_cost_by_emp: Dict[Any, float] = {}
    for emp, cost in db.execute(
        select(Employee, EmployeeCost)
        .outerjoin(EmployeeCost, Employee.id == EmployeeCost.employee_id)
        .where(Employee.is_active == True)  # noqa: E712
    ).all():
        if cost and cost.internal_daily_cost:
            daily_cost_by_emp[emp.id] = max(
                daily_cost_by_emp.get(emp.id, 0.0), float(cost.internal_daily_cost)
            )
    workforce_daily_total = sum(daily_cost_by_emp.values())

    today = date.today()
    series = []
    for i in range(months - 1, -1, -1):
        month_index = today.month - i
        year = today.year
        while month_index <= 0:
            month_index += 12
            year -= 1
        first = date(year, month_index, 1)
        days_in_month = calendar.monthrange(year, month_index)[1]
        last = date(year, month_index, days_in_month)

        bench_total = 0.0
        for c in bench_rows:
            ov_start = max(c.period_start, first)
            ov_end = min(c.period_end, last)
            if ov_start <= ov_end:
                bench_total += float(c.daily_cost) * ((ov_end - ov_start).days + 1)

        series.append({
            "year": year,
            "month": month_index,
            "label": first.strftime("%b %Y"),
            "total_bench_cost": round(bench_total, 2),
            "total_workforce_cost": round(workforce_daily_total * days_in_month, 2),
        })

    return {"months": series}


@router.get("/kpi-trend", status_code=status.HTTP_200_OK)
def kpi_trend(
    db: DbDep,
    days: int = Query(default=30, ge=2, le=365, description="Trailing days"),
) -> Dict[str, Any]:
    """Daily operating KPIs from kpi_snapshots (bench ratio, MTTR, intervention)."""
    rows: List[KpiSnapshot] = list(
        db.execute(select(KpiSnapshot).order_by(KpiSnapshot.snapshot_date.asc()))
        .scalars()
        .all()
    )
    rows = rows[-days:]
    return {
        "days": [
            {
                "date": r.snapshot_date.isoformat(),
                "label": r.snapshot_date.strftime("%d %b"),
                "bench_ratio_pct": float(r.bench_ratio_pct),
                "mttr_days": float(r.mttr_days) if r.mttr_days is not None else None,
                "human_intervention_pct": float(r.human_intervention_pct),
            }
            for r in rows
        ]
    }


@router.get("/project-margins", status_code=status.HTTP_200_OK)
def project_margins(db: DbDep) -> Dict[str, Any]:
    """Gross margin per active project, from real allocations + costs + bill rates.

    For every project with active allocations:
      * blended bill rate   = mean(project_demand.target_bill_rate)
      * blended cost rate    = mean(employee internal hourly cost)
      * margin %            = (bill − cost) / bill
      * monthly revenue/margin at 160 billable hours per FTE
    """
    rows = db.execute(
        select(Allocation, ProjectDemand, Project, Employee, EmployeeCost)
        .join(ProjectDemand, Allocation.demand_id == ProjectDemand.id)
        .join(Project, ProjectDemand.project_id == Project.id)
        .join(Employee, Allocation.employee_id == Employee.id)
        .outerjoin(EmployeeCost, Employee.id == EmployeeCost.employee_id)
        .where(Allocation.status == AllocationStatusEnum.active)
    ).all()

    HOURS_PER_MONTH = 160.0
    agg: Dict[Any, Dict[str, Any]] = defaultdict(
        lambda: {"name": None, "status": None, "target_margin": None,
                 "bill_sum": 0.0, "cost_sum": 0.0, "n": 0}
    )
    for alloc, demand, project, emp, cost in rows:
        bill = float(demand.target_bill_rate) if demand.target_bill_rate else 0.0
        cost_rate = (
            float(cost.internal_hourly_cost)
            if cost and cost.internal_hourly_cost
            else (float(emp.hourly_cost_rate) if emp.hourly_cost_rate else 0.0)
        )
        # Fall back to a seniority default so a missing cost row never yields
        # a bogus 100% margin.
        if cost_rate <= 0:
            sen = str(emp.experience_level.value if emp.experience_level else "mid").lower()
            cost_rate = {"junior": 40.0, "mid": 65.0, "senior": 95.0,
                         "lead": 125.0, "principal": 160.0}.get(sen, 65.0)

        a = agg[project.id]
        a["name"] = project.name
        a["status"] = project.status
        a["target_margin"] = float(project.target_margin) if project.target_margin else None
        a["bill_sum"] += bill
        a["cost_sum"] += cost_rate
        a["n"] += 1

    series = []
    for pid, a in agg.items():
        if a["n"] == 0:
            continue
        bill = a["bill_sum"] / a["n"]
        cost = a["cost_sum"] / a["n"]
        margin = round((bill - cost) / bill * 100, 1) if bill > 0 else 0.0
        monthly_revenue = bill * HOURS_PER_MONTH * a["n"]
        monthly_cost = cost * HOURS_PER_MONTH * a["n"]
        series.append({
            "project_id": str(pid),
            "name": a["name"],
            "status": a["status"],
            "allocated_fte": a["n"],
            "blended_bill_rate": round(bill, 2),
            "blended_cost_rate": round(cost, 2),
            "margin_pct": margin,
            "target_margin": a["target_margin"],
            "monthly_revenue": round(monthly_revenue),
            "monthly_margin": round(monthly_revenue - monthly_cost),
        })

    series.sort(key=lambda x: x["monthly_margin"], reverse=True)
    return {"projects": series}
