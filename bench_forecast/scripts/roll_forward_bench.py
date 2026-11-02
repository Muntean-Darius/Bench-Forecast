"""Non-destructive roll-forward of bench-cost history to today.

The seeder simulates bench periods only up to the day it ran. If the seed is a
few days old, the dashboard's current-month bench burn has no overlapping
`bench_costs` rows and shows "—" / a gap (or, before the null-handling fix, a
misleading €0).

This script extends the latest bench-cost period of every *currently-benched*
employee up to today, so the monthly burn KPI and the 6-month trend stay
current — without re-seeding (which would destroy demo state).

Safe to re-run: periods already ending today are skipped.

Usage:
    cd bench_forecast && ./venv/bin/python -m scripts.roll_forward_bench
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.database.database import get_sync_db  # noqa: E402
from src.database.models import (  # noqa: E402
    BenchCost,
    BenchHistory,
    BenchStatusEnum,
    Employee,
    EmployeeCost,
)


def main() -> None:
    today = date.today()
    updated = 0
    created = 0
    skipped = 0

    with get_sync_db() as db:
        employees = (
            db.execute(
                select(Employee).where(
                    Employee.is_active.is_(True),
                    Employee.bench_status == BenchStatusEnum.bench,
                )
            )
            .scalars()
            .all()
        )

        for emp in employees:
            latest = (
                db.execute(
                    select(BenchCost)
                    .where(BenchCost.employee_id == emp.id)
                    .order_by(BenchCost.period_end.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )

            if latest is None:
                cost = (
                    db.execute(
                        select(EmployeeCost)
                        .where(EmployeeCost.employee_id == emp.id)
                        .order_by(EmployeeCost.effective_date.desc())
                        .limit(1)
                    )
                    .scalars()
                    .first()
                )
                if cost is None:
                    skipped += 1
                    continue
                start = emp.bench_start_date or (today - timedelta(days=30))
                if start > today:
                    start = today - timedelta(days=30)
                days = (today - start).days + 1
                db.add(
                    BenchCost(
                        employee_id=emp.id,
                        period_start=start,
                        period_end=today,
                        bench_days=days,
                        daily_cost=cost.internal_daily_cost,
                        total_cost=(cost.internal_daily_cost * days).quantize(
                            Decimal("0.01")
                        ),
                    )
                )
                created += 1
                continue

            if latest.period_end >= today:
                skipped += 1
                continue

            days = (today - latest.period_start).days + 1
            latest.period_end = today
            latest.bench_days = days
            latest.total_cost = (latest.daily_cost * days).quantize(Decimal("0.01"))
            updated += 1

            # Keep the open bench-history row in sync as well.
            hist = (
                db.execute(
                    select(BenchHistory)
                    .where(
                        BenchHistory.employee_id == emp.id,
                        BenchHistory.bench_end.is_(None),
                    )
                    .order_by(BenchHistory.bench_start.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if hist is not None:
                hist.duration_days = max((today - hist.bench_start).days, 1)

    print(
        f"bench roll-forward complete: {updated} period(s) extended to {today}, "
        f"{created} created, {skipped} already current"
    )


if __name__ == "__main__":
    main()
