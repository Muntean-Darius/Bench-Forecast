"""Apply Alembic migrations, tolerating a schema created by ``init_db_sync``.

The FastAPI startup and the seeder call ``init_db_sync()`` (SQLAlchemy
``create_all``), which is useful for a quick start but leaves no
``alembic_version`` row. A later ``alembic upgrade`` would then try to create
existing tables and fail. This wrapper detects that case and stamps the current
baseline instead.

Run from the repository root:
    make migrate
or:
    cd bench_forecast && ./venv/bin/python -m scripts.migrate
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect

from src.core.config import Config


def main() -> int:
    alembic_cfg = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", Config.get_postgres_dsn())

    engine = create_engine(Config.get_postgres_dsn(), pool_pre_ping=True)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    target = f"{Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{Config.POSTGRES_DB}"
    if "alembic_version" not in tables and "employees" in tables:
        print(
            f"Existing schema found at {target} without alembic_version; "
            "stamping the current baseline instead of re-creating tables."
        )
        command.stamp(alembic_cfg, "head")
        return 0

    print(f"Applying migrations to {target}")
    command.upgrade(alembic_cfg, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
