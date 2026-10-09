"""Alembic environment configuration for Bench Forecast.

This file is loaded by every alembic command. It:
  1. Adds the src/ directory to sys.path so model imports work.
  2. Reads the PostgreSQL URL from Config (which reads .env).
  3. Wires Base.metadata into alembic so autogenerate works.
  4. Supports both offline (SQL script generation) and online (live DB) modes.
"""

import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Path setup — ensure src/ is importable
# ---------------------------------------------------------------------------
# Bench-Forecast/bench_forecast/alembic/env.py
#   → parents[1] = bench_forecast/
_bench_forecast_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_bench_forecast_root))

# ---------------------------------------------------------------------------
# Load application config and models
# ---------------------------------------------------------------------------
from src.core.config import Config  # noqa: E402
from src.database.models import Base  # noqa: E402  — registers all models

# ---------------------------------------------------------------------------
# Alembic Config object — gives access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Inject the DSN from our Config class (overrides alembic.ini sqlalchemy.url)
config.set_main_option("sqlalchemy.url", Config.get_postgres_dsn())

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for autogenerate support
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — generates a SQL script without a live DB connection
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout / file instead of connecting to the DB.
    Useful for generating review-able migration scripts.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connects to PostgreSQL and runs migrations
# ---------------------------------------------------------------------------
def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # Single-use connection — safe for migration runs
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
