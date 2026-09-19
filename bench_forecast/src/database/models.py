"""SQLAlchemy ORM models for PostgreSQL database.

Schema:
- Employee:     Bench talent with skills, rates, and availability
- Project:      Active/pipeline projects with margin targets
- ProjectRole:  Open demands with billing rates and job descriptions
- Allocation:   Ledger of approved resource assignments with projected margins

All primary keys use UUID for distributed-safe identity generation.
Numeric precision types ensure financial calculations remain accurate.
"""

import uuid
import logging
import os
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Generator, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# Employee
# ---------------------------------------------------------------------------

class Employee(Base):
    """Bench talent record.

    Stores the employee's identity, cost rate, and bench start date.
    The cv_document_uri points to a raw file that the RAG pipeline reads
    to generate embeddings stored in ChromaDB.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique employee identifier (UUID)",
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Employee full display name",
    )
    primary_role: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        comment="Primary job function, e.g. 'Backend Engineer'",
    )
    seniority: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        comment="Seniority band, e.g. 'Junior', 'Mid', 'Senior', 'Principal'",
    )
    hourly_cost_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="Internal cost per billable hour in EUR/USD",
    )
    bench_start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date the employee becomes unallocated (forecast trigger)",
    )
    cv_document_uri: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        comment="Filesystem or object-store URI to the raw CV file for RAG ingestion",
    )
    profile_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Unstructured CV/profile for direct LLM access without RAG",
    )
    skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Comma-separated list of skills",
    )
    experience_years: Mapped[float] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        default=0.0,
        comment="Years of professional experience",
    )
    current_project: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Current project assignment name or ID",
    )

    # Relationships
    allocations: Mapped[List["Allocation"]] = relationship(
        "Allocation",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Employee id={self.id} name='{self.full_name}' "
            f"role='{self.primary_role}' seniority='{self.seniority}' "
            f"cost_rate={self.hourly_cost_rate}/h>"
        )


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project(Base):
    """Project or pipeline opportunity.

    Captures margin targets and pipeline probability so the financial
    optimization engine can weigh allocation profitability.
    """

    __tablename__ = "projects"

    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 100", name="ck_project_probability"),
        CheckConstraint("target_margin >= 0 AND target_margin <= 100", name="ck_project_target_margin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique project identifier (UUID)",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-readable project name",
    )
    status: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="Pipeline",
        comment="Project lifecycle status, e.g. 'Active', 'Pipeline', 'Closed'",
    )
    probability: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
        comment="Win probability percentage [0-100]",
    )
    target_margin: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Expected gross profit margin percentage, e.g. 30.00 = 30%",
    )
    total_budget: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Total project budget in EUR/USD",
    )

    # Relationships
    roles: Mapped[List["ProjectRole"]] = relationship(
        "ProjectRole",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Project id={self.id} name='{self.name}' "
            f"status='{self.status}' probability={self.probability}% "
            f"margin={self.target_margin}%>"
        )


# ---------------------------------------------------------------------------
# ProjectRole (Demand)
# ---------------------------------------------------------------------------

class ProjectRole(Base):
    """Open demand slot on a project.

    The description field is the raw unstructured text vectorized by the RAG
    pipeline and stored in ChromaDB for semantic matching.
    target_bill_rate is the hourly rate charged to the client — used with
    Employee.hourly_cost_rate to compute projected_margin on Allocation.
    """

    __tablename__ = "project_roles"

    __table_args__ = (
        CheckConstraint(
            "status IN ('Open', 'Filled', 'Requires Training')",
            name="ck_project_role_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique role identifier (UUID)",
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Parent project (FK → projects.id)",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Role title, e.g. 'Senior Backend Engineer'",
    )
    target_bill_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="Hourly billing rate charged to the client in EUR/USD",
    )
    status: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="Open",
        comment="Filling status: 'Open', 'Filled', or 'Requires Training'",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Unstructured job description — vectorized for ChromaDB semantic search",
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Role start date",
    )
    headcount: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Number of positions available",
    )
    required_skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Comma-separated list of required skills",
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="roles")
    allocations: Mapped[List["Allocation"]] = relationship(
        "Allocation",
        back_populates="project_role",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProjectRole id={self.id} title='{self.title}' "
            f"project_id={self.project_id} bill_rate={self.target_bill_rate}/h "
            f"status='{self.status}'>"
        )


# ---------------------------------------------------------------------------
# Allocation (Ledger)
# ---------------------------------------------------------------------------

class Allocation(Base):
    """Immutable ledger record of an approved resource assignment.

    projected_margin is calculated at write time as:
        (target_bill_rate - hourly_cost_rate) / target_bill_rate * 100

    This de-normalises the margin for fast financial reporting without
    requiring a join across three tables on every read.
    """

    __tablename__ = "allocations"

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_allocation_date_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique allocation ledger entry (UUID)",
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Allocated employee (FK → employees.id)",
    )
    project_role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_roles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Target project role (FK → project_roles.id)",
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Allocation start date",
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Allocation end date (must be >= start_date)",
    )
    projected_margin: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment=(
            "Pre-computed gross margin: "
            "(bill_rate - cost_rate) / bill_rate * 100. "
            "Negative values indicate unprofitable allocations."
        ),
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", back_populates="allocations")
    project_role: Mapped["ProjectRole"] = relationship(
        "ProjectRole", back_populates="allocations"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Allocation id={self.id} "
            f"employee_id={self.employee_id} role_id={self.project_role_id} "
            f"margin={self.projected_margin}% "
            f"{self.start_date}→{self.end_date}>"
        )


# ---------------------------------------------------------------------------
# Database engine / session factory helpers
# ---------------------------------------------------------------------------

def build_database_url() -> str:
    """Construct PostgreSQL DSN from environment variables.

    Expected env vars (all with defaults suitable for local Docker Compose):
        POSTGRES_HOST     (default: localhost)
        POSTGRES_PORT     (default: 5432)
        POSTGRES_DB       (default: bench_forecast)
        POSTGRES_USER     (default: postgres)
        POSTGRES_PASSWORD (default: postgres)

    Returns:
        SQLAlchemy-compatible DSN string.
    """
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "bench_forecast")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def create_db_engine(database_url: Optional[str] = None, echo: bool = False):
    """Create a SQLAlchemy engine configured for PostgreSQL.

    Args:
        database_url: Full DSN (falls back to build_database_url() if None).
        echo:         Enable SQL statement logging (useful for debugging).

    Returns:
        SQLAlchemy Engine instance.
    """
    url = database_url or build_database_url()
    engine = create_engine(
        url,
        echo=echo,
        pool_pre_ping=True,       # Detect stale connections before use
        pool_size=10,             # Maintain up to 10 idle connections
        max_overflow=20,          # Allow up to 20 extra connections under load
        pool_timeout=30,          # Seconds to wait for a connection from the pool
        pool_recycle=3600,        # Recycle connections older than 1 hour
    )
    logger.info(f"PostgreSQL engine created: {url.split('@')[-1]}")
    return engine


def create_session_factory(engine) -> sessionmaker:
    """Create a sessionmaker bound to the given engine.

    Args:
        engine: SQLAlchemy Engine.

    Returns:
        sessionmaker factory.
    """
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,  # Prevent lazy-loading after commit in async contexts
    )


@contextmanager
def get_db_session(session_factory: sessionmaker) -> Generator[Session, None, None]:
    """Context manager providing a scoped database session.

    Automatically commits on success and rolls back on any exception,
    then closes the session regardless of outcome.

    Args:
        session_factory: Bound sessionmaker instance.

    Yields:
        Active SQLAlchemy Session.

    Raises:
        Re-raises any exception after rollback.
    """
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Database session error — rolled back")
        raise
    finally:
        session.close()


def init_db(engine) -> None:
    """Create all tables that do not yet exist in the database.

    Safe to call on every application startup — DDL is idempotent via
    CREATE TABLE IF NOT EXISTS semantics of SQLAlchemy's create_all.

    Args:
        engine: Bound SQLAlchemy Engine.
    """
    Base.metadata.create_all(bind=engine)
    logger.info("PostgreSQL schema initialised (all tables created / verified)")


def compute_projected_margin(
    target_bill_rate: Decimal,
    hourly_cost_rate: Decimal,
) -> Decimal:
    """Compute the projected gross margin for an allocation.

    Formula:
        projected_margin = (bill_rate - cost_rate) / bill_rate * 100

    Args:
        target_bill_rate:  Hourly client billing rate.
        hourly_cost_rate:  Internal employee cost rate.

    Returns:
        Margin as a percentage (e.g. Decimal('25.00') means 25%).

    Raises:
        ValueError: If bill_rate is zero (would produce division by zero).
    """
    if target_bill_rate == 0:
        raise ValueError("target_bill_rate must be > 0 to compute a valid margin")
    margin = (target_bill_rate - hourly_cost_rate) / target_bill_rate * Decimal("100")
    return margin.quantize(Decimal("0.01"))
