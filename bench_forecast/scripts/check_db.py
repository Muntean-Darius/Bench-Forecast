"""Print the database the backend is actually connected to, plus row counts
and the Phase 6 capacity / schema consistency checks.

Run from the repository root:
    make db-check
or:
    cd bench_forecast && ./venv/bin/python -m scripts.check_db

Exits non-zero if a consistency violation is found (over-capacity employee,
allocation/demand above the 8h hard cap, invalid capacity values, or a missing
Alembic revision).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from src.core.config import Config
from src.database.database import SyncSessionLocal


TABLES = (
    "employees",
    "projects",
    "project_demands",
    "allocations",
    "employee_skills",
    "employee_skill_evidence",
    "training_assignments",
    "cv_review_requests",
    "email_notifications",
    "forecast_runs",
    "hitl_review_events",
    "users",
)


def _scalar(session, sql: str):
    try:
        return session.execute(text(sql)).scalar()
    except Exception as exc:
        return f"ERROR ({exc.__class__.__name__})"


def main() -> int:
    print(f"POSTGRES_HOST : {Config.POSTGRES_HOST}")
    print(f"POSTGRES_PORT : {Config.POSTGRES_PORT}")
    print(f"POSTGRES_DB   : {Config.POSTGRES_DB}")
    print(f"POSTGRES_USER : {Config.POSTGRES_USER}")

    violations: list[str] = []
    try:
        with SyncSessionLocal() as session:
            server = session.execute(text("SHOW server_version")).scalar()
            print(f"server_version: {server}")

            alembic = _scalar(
                session,
                "SELECT version_num FROM alembic_version LIMIT 1",
            )
            print(f"alembic_head  : {alembic}")

            print("\nrow counts")
            for table in TABLES:
                try:
                    count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                except Exception as exc:
                    print(f"  {table:<20} MISSING ({exc.__class__.__name__})")
                    continue
                print(f"  {table:<20} {count}")

            print("\ncapacity consistency")
            checks = {
                "allocations_over_8h": (
                    "SELECT count(*) FROM allocations "
                    "WHERE allocated_hours_per_day > 8.0"
                ),
                "allocations_non_positive_hours": (
                    "SELECT count(*) FROM allocations "
                    "WHERE allocated_hours_per_day <= 0"
                ),
                "employees_over_8h_capacity": (
                    "SELECT count(*) FROM employees "
                    "WHERE daily_capacity_hours > 8.0 "
                    "OR daily_capacity_hours <= 0"
                ),
                "demands_over_8h": (
                    "SELECT count(*) FROM project_demands "
                    "WHERE hours_per_day > 8.0 "
                    "OR hours_per_day <= 0"
                ),
                "employees_over_capacity_committed": (
                    "SELECT count(*) FROM ("
                    "  SELECT a.employee_id, e.daily_capacity_hours, "
                    "         SUM(a.allocated_hours_per_day) AS total "
                    "  FROM allocations a "
                    "  JOIN employees e ON e.id = a.employee_id "
                    "  WHERE a.status IN ('active', 'approved_by_manager') "
                    "  GROUP BY a.employee_id, e.daily_capacity_hours "
                    "  HAVING SUM(a.allocated_hours_per_day) "
                    "         > LEAST(e.daily_capacity_hours, 8.0) + 0.0001"
                    ") x"
                ),
                "employees_over_capacity_including_reserved": (
                    "SELECT count(*) FROM ("
                    "  SELECT a.employee_id, e.daily_capacity_hours, "
                    "         SUM(a.allocated_hours_per_day) AS total "
                    "  FROM allocations a "
                    "  JOIN employees e ON e.id = a.employee_id "
                    "  WHERE a.status IN ("
                    "    'active', 'approved_by_manager', "
                    "    'pending_manager_approval', 'proposed_by_ai'"
                    "  ) "
                    "  GROUP BY a.employee_id, e.daily_capacity_hours "
                    "  HAVING SUM(a.allocated_hours_per_day) "
                    "         > LEAST(e.daily_capacity_hours, 8.0) + 0.0001"
                    ") x"
                ),
                "employees_with_manager": (
                    "SELECT count(*) FROM employees WHERE manager_id IS NOT NULL"
                ),
                "project_earned_locked_skills": (
                    "SELECT count(*) FROM employee_skills "
                    "WHERE source = 'project' AND is_locked = true"
                ),
            }
            for label, sql in checks.items():
                value = _scalar(session, sql)
                print(f"  {label:<42} {value}")
                if label.startswith(("allocations_", "employees_over", "demands_over")):
                    if isinstance(value, int) and value > 0:
                        violations.append(f"{label}={value}")
                if label == "employees_with_manager" and value == 0:
                    violations.append("employees_with_manager=0")

            if alembic in (None, "ERROR") or not isinstance(alembic, str):
                violations.append("alembic_head missing")
    except Exception as exc:
        print(f"\nCannot connect: {exc}", file=sys.stderr)
        return 1

    if str(Config.POSTGRES_PORT) == "5432":
        print(
            "\nWARNING: POSTGRES_PORT=5432. The Docker Compose database is exposed "
            "on 5434; 5432 is commonly a local PostgreSQL instance.",
            file=sys.stderr,
        )

    print("\nresult")
    if violations:
        print("  FAIL: " + ", ".join(violations))
        return 1
    print("  PASS: schema + capacity consistency checks are clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
