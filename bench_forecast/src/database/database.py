"""SQLAlchemy 2.0 async/sync database connection setup.

Provides:
  - Sync engine (psycopg2) — always available, used by background jobs / seeder
  - Async engine (asyncpg) — optional; created only when asyncpg is installed
  - Scoped session factories for both modes
  - FastAPI dependency: get_db() (async)
  - Script helper: get_sync_db() (sync context manager)
  - Startup helpers: init_db() (async) and init_db_sync() (sync)

Install async driver for FastAPI endpoints:
    pip install asyncpg
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.core.config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synchronous engine (psycopg2) — always available
# ---------------------------------------------------------------------------

_sync_engine = create_engine(
    Config.get_postgres_dsn(),
    echo=Config.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3_600,
)

SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=_sync_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)

# ---------------------------------------------------------------------------
# Asynchronous engine (asyncpg) — created lazily to avoid import errors
# when asyncpg is not yet installed (e.g. during seeding / Alembic runs).
# ---------------------------------------------------------------------------

_async_engine = None
AsyncSessionLocal = None

try:
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    _async_engine = create_async_engine(
        Config.get_async_postgres_dsn(),
        echo=Config.DEBUG,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3_600,
    )

    AsyncSessionLocal = async_sessionmaker(
        bind=_async_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    logger.debug("Async PostgreSQL engine (asyncpg) initialised")

except ModuleNotFoundError:
    logger.warning(
        "asyncpg not installed — async database engine unavailable. "
        "Install it with: pip install asyncpg"
    )


# ---------------------------------------------------------------------------
# FastAPI dependency — yields an async session, rolls back on error
# ---------------------------------------------------------------------------

async def get_db():  # type: ignore[return]
    """FastAPI dependency that provides an async database session.

    Raises RuntimeError if asyncpg / async engine is not available.

    Usage in a router::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    if AsyncSessionLocal is None:
        raise RuntimeError(
            "Async database engine is not available. "
            "Install asyncpg: pip install asyncpg"
        )
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Sync context manager — always available
# ---------------------------------------------------------------------------

@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """Sync context manager for background jobs, seeder scripts, and tests.

    Usage::

        with get_sync_db() as db:
            db.add(some_model)
    """
    session: Session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Sync database session error — rolled back")
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Startup / health helpers
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables that do not yet exist (async, idempotent).

    Called once at application startup via the FastAPI lifespan event.
    Requires asyncpg to be installed.
    In production, prefer Alembic migrations over this helper.
    """
    if _async_engine is None:
        raise RuntimeError("Async engine unavailable — install asyncpg")

    from src.database.models import Base  # local import to avoid circular deps

    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("PostgreSQL schema initialised via async engine")


async def check_db_connection() -> bool:
    """Probe the async database connection. Returns True if healthy."""
    if _async_engine is None:
        return False
    try:
        async with _async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Async database health-check failed")
        return False


def init_db_sync() -> None:
    """Create all tables synchronously (idempotent).

    Safe to call from seeder scripts and Alembic env.py.
    Does not require asyncpg.
    """
    from src.database.models import Base  # local import to avoid circular deps

    Base.metadata.create_all(bind=_sync_engine)
    logger.info("PostgreSQL schema initialised via sync engine")


def check_db_connection_sync() -> bool:
    """Probe the sync database connection. Returns True if healthy."""
    try:
        with _sync_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Sync database health-check failed")
        return False


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

__all__ = [
    "_sync_engine",
    "_async_engine",
    "SyncSessionLocal",
    "AsyncSessionLocal",
    "get_db",
    "get_sync_db",
    "init_db",
    "init_db_sync",
    "check_db_connection",
    "check_db_connection_sync",
]
