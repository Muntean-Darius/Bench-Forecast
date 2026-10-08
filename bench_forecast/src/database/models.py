"""SQLAlchemy 2.0 ORM models — Bench Forecast full schema.

Domain coverage (Phase 1 — Core HR):
  departments      — org units with cost-center codes
  employees        — bench talent with status, profile, and ChromaDB link
  skills           — normalized skill taxonomy
  employee_skills  — many-to-many with proficiency + freshness metadata

Domain coverage (Phase 1 — Documents):
  documents        — file metadata + chroma_doc_id (NO embedding column)

Domain coverage (Phase 1 — Compatibility shim):
  Project / ProjectRole / Allocation — kept from original models.py so that
  postgres_db.py continues to work unmodified until Phase 4 migrates them
  to the full schema defined in plan.md.

All models use:
  - SQLAlchemy 2.0 declarative base (DeclarativeBase)
  - Mapped[T] + mapped_column() type-annotated style
  - UUID primary keys (postgresql UUID type, as_uuid=True)
  - TIMESTAMPTZ via DateTime(timezone=True)
  - Python enums mapped to native PostgreSQL ENUM types via
    sqlalchemy.dialects.postgresql.ENUM (avoids VARCHAR check constraints)

CRITICAL: No embedding columns anywhere. Embeddings live in ChromaDB only.
          documents.chroma_doc_id is a plain VARCHAR that links to Chroma.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Shared declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Shared metadata container for all ORM models."""
    pass


# ---------------------------------------------------------------------------
# Python-side enums
# These are referenced by model columns; PostgreSQL will store them as ENUM.
# ---------------------------------------------------------------------------

class ExperienceLevelEnum(str, enum.Enum):
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    principal = "principal"


class EmploymentTypeEnum(str, enum.Enum):
    full_time = "full_time"
    part_time = "part_time"
    contractor = "contractor"


class BenchStatusEnum(str, enum.Enum):
    on_project = "on_project"
    bench = "bench"
    upcoming_bench = "upcoming_bench"
    internal = "internal"
    on_leave = "on_leave"


class ProficiencyLevelEnum(str, enum.Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"
    expert = "expert"


class DocumentTypeEnum(str, enum.Enum):
    cv = "cv"
    certification = "certification"
    contract = "contract"
    performance_review = "performance_review"
    training_record = "training_record"


class IngestionStatusEnum(str, enum.Enum):
    pending = "pending"
    ingested = "ingested"
    failed = "failed"


class AllocationStatusEnum(str, enum.Enum):
    proposed_by_ai = "proposed_by_ai"
    pending_manager_approval = "pending_manager_approval"
    approved_by_manager = "approved_by_manager"
    rejected_by_manager = "rejected_by_manager"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class DemandStatusEnum(str, enum.Enum):
    open = "open"
    filled = "filled"
    cancelled = "cancelled"


class HitlDecisionEnum(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"
    edited_then_approved = "edited_then_approved"


# ---------------------------------------------------------------------------
# Mixin: auto-managed timestamps
# ---------------------------------------------------------------------------

class TimestampMixin:
    """Adds server-side created_at / updated_at columns to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        comment="Row creation timestamp (UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="Row last-updated timestamp (UTC)",
    )


# =============================================================================
# 3.1 Core HR Domain
# =============================================================================

class Department(TimestampMixin, Base):
    """Organisational unit — Engineering, Data Science, DevOps, etc.

    `head_count_budget` is the approved headcount for this department.
    Used by the executive dashboard to surface over/under-staffing signals.
    """

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Department UUID PK",
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        comment="Department name, e.g. 'Engineering', 'Data Science'",
    )
    cost_center_code: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Finance / ERP cost-center code",
    )
    head_count_budget: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Approved headcount budget for this department",
    )

    # Relationships
    employees: Mapped[list["Employee"]] = relationship(
        "Employee",
        back_populates="department",
        foreign_keys="Employee.department_id",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Department id={self.id} name='{self.name}'>"


class Employee(TimestampMixin, Base):
    """Bench talent record — authoritative HR source of truth.

    `profile_text` is the denormalized text blob (skills + title + summary)
    that gets chunked and embedded into ChromaDB by the ingestion pipeline.
    It is distinct from the actual PDF stored in `documents`.

    `bench_status` drives the agent's data_extractor_node query:
        bench / upcoming_bench employees are the agent's primary targets.

    `manager_id` is a self-referencing FK that builds the org tree used
    by the HITL Streamlit approval UI to scope a manager's visible employees.
    """

    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Employee UUID PK",
    )
    employee_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="Corporate employee ID, e.g. 'EMP-00142'",
    )
    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Legal first name",
    )
    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Legal last name",
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Corporate email — used as login identifier",
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="FK → departments.id",
    )
    job_title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Job title, e.g. 'Senior Data Engineer'",
    )
    experience_level: Mapped[ExperienceLevelEnum] = mapped_column(
        SQLEnum(ExperienceLevelEnum, native_enum=True, create_constraint=True),
        nullable=False,
        default=ExperienceLevelEnum.mid,
        comment="Seniority band: junior | mid | senior | lead | principal",
    )
    hire_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date the employee joined the company",
    )
    employment_type: Mapped[EmploymentTypeEnum] = mapped_column(
        SQLEnum(EmploymentTypeEnum, native_enum=True, create_constraint=True),
        nullable=False,
        default=EmploymentTypeEnum.full_time,
        comment="Contract type: full_time | part_time | contractor",
    )
    location: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Office or remote region, e.g. 'Bucharest', 'Remote EU'",
    )
    manager_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Self-referencing FK for the org hierarchy (→ employees.id)",
    )
    bench_status: Mapped[BenchStatusEnum] = mapped_column(
        SQLEnum(BenchStatusEnum, native_enum=True, create_constraint=True),
        nullable=False,
        default=BenchStatusEnum.on_project,
        index=True,
        comment=(
            "Current bench state: on_project | bench | upcoming_bench | "
            "internal | on_leave"
        ),
    )
    bench_start_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date when the employee became / will become unallocated. NULL if on project.",
    )
    profile_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Denormalized profile blob (skills + title + summary). "
            "Source text for ChromaDB ingestion — NOT the PDF itself."
        ),
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="URL to employee profile photo",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Soft-delete flag. False = terminated / archived.",
    )

    # -------------------------------------------------------------------------
    # TODO: DEPRECATE in Phase 5
    # DANGER: The following legacy columns are kept for temporary backward 
    # compatibility only. They are dangerous to write to and will be removed.
    # -------------------------------------------------------------------------
    # --- Legacy columns kept for backward compat with postgres_db.py ----------
    full_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="[LEGACY] Denormalized full name — use first_name + last_name instead",
    )
    primary_role: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        comment="[LEGACY] Use job_title instead",
    )
    seniority: Mapped[Optional[str]] = mapped_column(
        String(60),
        nullable=True,
        comment="[LEGACY] Use experience_level instead",
    )
    hourly_cost_rate: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="[LEGACY] Use employee_costs.internal_hourly_cost instead",
    )
    experience_years: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 2),
        nullable=True,
        comment="[LEGACY] Total years of professional experience",
    )
    current_project: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="[LEGACY] Current project name. Use allocations table instead.",
    )
    cv_document_uri: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        comment="[LEGACY] URI to raw CV file. Use documents.file_path instead.",
    )
    skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="[LEGACY] Comma-separated skills. Use employee_skills table instead.",
    )

    # Relationships
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        back_populates="employees",
        foreign_keys=[department_id],
    )
    manager: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        remote_side="Employee.id",
        foreign_keys=[manager_id],
        back_populates="direct_reports",
    )
    direct_reports: Mapped[list["Employee"]] = relationship(
        "Employee",
        foreign_keys=[manager_id],
        back_populates="manager",
    )
    employee_skills: Mapped[list["EmployeeSkill"]] = relationship(
        "EmployeeSkill",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    # Legacy relationship kept for postgres_db.py compatibility
    allocations: Mapped[list["Allocation"]] = relationship(
        "Allocation",
        back_populates="employee",
        foreign_keys="Allocation.employee_id",
        cascade="all, delete-orphan",
        lazy="select",
    )
    employee_costs: Mapped[list["EmployeeCost"]] = relationship(
        "EmployeeCost",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    bench_history: Mapped[list["BenchHistory"]] = relationship(
        "BenchHistory",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )
    bench_costs: Mapped[list["BenchCost"]] = relationship(
        "BenchCost",
        back_populates="employee",
        cascade="all, delete-orphan",
        lazy="select",
    )

    @property
    def utilization_rate(self) -> Decimal:
        """
        Dynamically calculates the sum of active allocation percentages.
        TODO: Consider replacing with a SQL view / hybrid property in the future.
        """
        active_allocations = [
            a.allocation_pct for a in self.allocations 
            if a.status == AllocationStatusEnum.active
        ]
        return sum(active_allocations) if active_allocations else Decimal("0.00")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Employee id={self.id} "
            f"name='{self.first_name} {self.last_name}' "
            f"title='{self.job_title}' status='{self.bench_status}'>"
        )


class Skill(Base):
    """Normalized skill taxonomy — prevents 'React' vs 'ReactJS' fragmentation.

    New skills are created as unverified (is_verified=False) by default.
    An admin must set is_verified=True for the skill to appear in autocomplete.
    """

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Skill UUID PK",
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
        comment="Canonical skill name, e.g. 'Python', 'React', 'Kubernetes'",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment=(
            "Skill category for taxonomy browsing: "
            "'Frontend' | 'Backend' | 'Cloud' | 'Data' | 'DevOps' | 'Soft Skills' | ..."
        ),
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Admin-approved canonical name. Unverified skills are user-created aliases.",
    )

    # Relationships
    employee_skills: Mapped[list["EmployeeSkill"]] = relationship(
        "EmployeeSkill",
        back_populates="skill",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill id={self.id} name='{self.name}' category='{self.category}'>"


class EmployeeSkill(Base):
    """Many-to-many link between Employee and Skill with proficiency metadata.

    Composite PK: (employee_id, skill_id) — an employee can have each skill
    at most once. To track proficiency history, create a new row with a
    higher proficiency and archive the old one (or rely on audit_log).

    `last_used_date` is a skill-freshness signal used by the ChromaDB
    metadata filter in RAGPipeline to down-rank stale skills.
    """

    __tablename__ = "employee_skills"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK → employees.id",
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK → skills.id",
    )
    proficiency_level: Mapped[ProficiencyLevelEnum] = mapped_column(
        SQLEnum(ProficiencyLevelEnum, native_enum=True, create_constraint=True),
        nullable=False,
        default=ProficiencyLevelEnum.intermediate,
        comment="Self-assessed or manager-rated level: beginner | intermediate | advanced | expert",
    )
    years_experience: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(4, 1),
        nullable=True,
        comment="Years of hands-on experience with this skill",
    )
    last_used_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment=(
            "Last date the skill was actively used. "
            "Used as a freshness signal by the RAG financial filter."
        ),
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True = top skill highlighted on profile. False = secondary / supporting.",
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", back_populates="employee_skills")
    skill: Mapped["Skill"] = relationship("Skill", back_populates="employee_skills")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EmployeeSkill employee={self.employee_id} "
            f"skill={self.skill_id} level='{self.proficiency_level}'>"
        )


# =============================================================================
# Documents (CV / cert / contract metadata — binary file link table)
#
# CRITICAL: No `embedding` column.
# Embeddings live exclusively in ChromaDB.
# `chroma_doc_id` is a plain VARCHAR that links this row to the
# ChromaDB document chunk(s) generated from this file's text_content.
# =============================================================================

class Document(Base):
    """File metadata record — CVs, certifications, contracts.

    Binary content is stored in MinIO (or S3 in prod) at `file_path`.
    Text is extracted by pdfplumber and stored in `text_content`.
    ChromaDB ingestion writes the resulting doc ID back to `chroma_doc_id`
    and flips `ingestion_status` to 'ingested'.

    NEVER add an embedding/vector column here. Embeddings belong in ChromaDB.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Document UUID PK",
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK → employees.id — owner of this document",
    )
    document_type: Mapped[DocumentTypeEnum] = mapped_column(
        SQLEnum(DocumentTypeEnum, native_enum=True, create_constraint=True),
        nullable=False,
        comment=(
            "Document category: cv | certification | contract | "
            "performance_review | training_record"
        ),
    )
    file_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Original uploaded filename, e.g. 'resume_v3.pdf'",
    )
    file_path: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment=(
            "Object-store path, e.g. 's3://bench-docs/cv/emp-142/resume_v3.pdf' "
            "or '/storage/cv/{employee_id}/resume_v1.pdf'"
        ),
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="application/pdf",
        comment="MIME type: 'application/pdf', 'image/png', etc.",
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True,
        comment="File size in bytes",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="CV revision number — incremented on re-upload",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="True = latest version. Previous versions are kept for audit.",
    )
    text_content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Full text extracted by pdfplumber / PyMuPDF. "
            "This is the source text that gets chunked + embedded into ChromaDB."
        ),
    )
    # -----------------------------------------------------------------
    # ChromaDB link — the ONLY connection between PostgreSQL and ChromaDB
    # -----------------------------------------------------------------
    chroma_doc_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment=(
            "ChromaDB document ID written back after ingestion. "
            "Use this to retrieve the embedding chunks from ChromaDB. "
            "NULL = not yet ingested."
        ),
    )
    ingestion_status: Mapped[IngestionStatusEnum] = mapped_column(
        SQLEnum(IngestionStatusEnum, native_enum=True, create_constraint=True),
        nullable=False,
        default=IngestionStatusEnum.pending,
        comment="ChromaDB ingestion state: pending | ingested | failed",
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        comment="Upload timestamp (UTC)",
    )

    # Relationships
    employee: Mapped["Employee"] = relationship("Employee", back_populates="documents")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Document id={self.id} type='{self.document_type}' "
            f"file='{self.file_name}' status='{self.ingestion_status}'>"
        )


# =============================================================================
# Legacy models — kept for backward compatibility with postgres_db.py
# These will be superseded by the full schema in Phase 2-4.
# =============================================================================

class Project(Base):
    """[LEGACY] Project or pipeline opportunity.

    Will be replaced by the full projects + clients + project_demands schema
    defined in plan.md §3.2 during Phase 4.
    """

    __tablename__ = "projects"

    # -------------------------------------------------------------------------
    # TODO: DEPRECATE in Phase 5
    # DANGER: The following legacy columns are kept for temporary backward 
    # compatibility only. They are dangerous to write to and will be removed.
    # -------------------------------------------------------------------------

    __table_args__ = (
        CheckConstraint("probability >= 0 AND probability <= 100", name="ck_project_probability"),
        CheckConstraint("target_margin >= 0 AND target_margin <= 100", name="ck_project_target_margin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Project UUID PK",
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
        comment="Project lifecycle status: 'Active', 'Pipeline', 'Closed'",
    )
    probability: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=50,
        comment="Win probability [0-100]",
    )
    target_margin: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Expected gross profit margin %",
    )
    total_budget: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Total project budget",
    )

    roles: Mapped[list["ProjectRole"]] = relationship(
        "ProjectRole",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name='{self.name}' status='{self.status}'>"


class ProjectRole(Base):
    """[LEGACY] Open demand slot on a project.

    Will be replaced by project_demands + project_skill_requirements in Phase 4.
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
        comment="ProjectRole UUID PK",
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK → projects.id",
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Role title",
    )
    target_bill_rate: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="Hourly billing rate",
    )
    status: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="Open",
        comment="Filling status: Open | Filled | Requires Training",
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
        comment="Number of open positions",
    )
    required_skills: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="[LEGACY] Comma-separated skills. Use project_skill_requirements in Phase 4.",
    )

    project: Mapped["Project"] = relationship("Project", back_populates="roles")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectRole id={self.id} title='{self.title}' status='{self.status}'>"


class Allocation(TimestampMixin, Base):
    """Approved resource assignment ledger entry with HITL state machine."""

    __tablename__ = "allocations"

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_allocation_date_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    demand_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("project_demands.id", ondelete="SET NULL"), nullable=True)
    role_on_project: Mapped[str] = mapped_column(String(200), nullable=False)
    allocation_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    
    # HITL Status
    status: Mapped[AllocationStatusEnum] = mapped_column(SQLEnum(AllocationStatusEnum, native_enum=True, create_constraint=True), nullable=False, default=AllocationStatusEnum.proposed_by_ai)
    
    # AI Metadata
    ai_justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_match_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    ai_projected_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    ai_upskilling_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Manager Review Data
    manager_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forecast_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("forecast_runs.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], back_populates="allocations")
    project_demand: Mapped[Optional["ProjectDemand"]] = relationship("ProjectDemand", foreign_keys=[demand_id])

# ---------------------------------------------------------------------------
# Utility kept for backward compat (used in postgres_db.py)
# ---------------------------------------------------------------------------

def compute_projected_margin(
    target_bill_rate: Decimal,
    hourly_cost_rate: Decimal,
) -> Decimal:
    if target_bill_rate == 0:
        raise ValueError("target_bill_rate must be > 0 to compute a valid margin")
    margin = (target_bill_rate - hourly_cost_rate) / target_bill_rate * Decimal("100")
    return margin.quantize(Decimal("0.01"))

# =============================================================================
# Phase 4 - HITL & Allocation State Machine
# =============================================================================

class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="viewer")
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    cv_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    forecast_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    triggered_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="in_progress")

    
    # RAGAS Evaluation Metrics
    ragas_faithfulness: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    ragas_answer_relevance: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    ragas_context_recall: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)


class ProjectDemand(TimestampMixin, Base):
    __tablename__ = "project_demands"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    required_skills: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    min_proficiency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    headcount_needed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_bill_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    target_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    win_probability: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    status: Mapped[DemandStatusEnum] = mapped_column(SQLEnum(DemandStatusEnum, native_enum=True, create_constraint=True), nullable=False, default=DemandStatusEnum.open)
    needed_by_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    project: Mapped["Project"] = relationship("Project")


class HitlReviewEvent(Base):
    __tablename__ = "hitl_review_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    forecast_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("forecast_runs.id", ondelete="SET NULL"), nullable=True)
    allocation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("allocations.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[HitlDecisionEnum] = mapped_column(SQLEnum(HitlDecisionEnum, native_enum=True, create_constraint=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejection_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_to_decision_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class KpiSnapshot(Base):
    """Daily KPI metrics for the Bench Forecast system."""

    __tablename__ = "kpi_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    bench_ratio_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    mttr_days: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    human_intervention_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)


# =============================================================================
# Phase 6 - Financial & Bench Observability
# =============================================================================

class EmployeeCost(Base):
    __tablename__ = "employee_costs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, default=date.today)
    annual_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    internal_daily_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    internal_hourly_cost: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    overhead_multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.35"))

    employee: Mapped["Employee"] = relationship("Employee", back_populates="employee_costs")


class BenchHistory(TimestampMixin, Base):
    __tablename__ = "bench_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    bench_start: Mapped[date] = mapped_column(Date, nullable=False)
    bench_end: Mapped[date] = mapped_column(Date, nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)

    employee: Mapped["Employee"] = relationship("Employee", back_populates="bench_history")


class BenchCost(TimestampMixin, Base):
    __tablename__ = "bench_costs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    bench_days: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    employee: Mapped["Employee"] = relationship("Employee", back_populates="bench_costs")
