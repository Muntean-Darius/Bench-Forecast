"""usability & capability expansion

Adds:
  * fractional daily-hour capacity to employees, demands and allocations
  * project client / lifecycle dates
  * cumulative skill provenance on employee_skills + evidence ledger
  * CV approval staging on documents + cv_review_requests
  * training_assignments and email_notifications

Revision ID: 9f3c1a7b5d20
Revises: 806bfe6b3200
Create Date: 2026-10-24 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9f3c1a7b5d20"
down_revision: Union[str, Sequence[str], None] = "806bfe6b3200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_enum_if_missing(name: str, values: Sequence[str]) -> None:
    joined = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$
        BEGIN
            CREATE TYPE {name} AS ENUM ({joined});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def _skill_origin():
    return postgresql.ENUM(
        "cv", "project", "training", "manual",
        name="skilloriginenum",
        create_type=False,
    )


def _cv_approval_status():
    return postgresql.ENUM(
        "not_required", "pending_approval", "approved", "rejected",
        name="cvapprovalstatusenum",
        create_type=False,
    )


def _training_status():
    return postgresql.ENUM(
        "assigned", "in_progress", "completed", "cancelled",
        name="trainingstatusenum",
        create_type=False,
    )


def _experience_level():
    return postgresql.ENUM(
        "junior", "mid", "senior", "lead", "principal",
        name="experiencelevelenum",
        create_type=False,
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Enum values / types
    # ------------------------------------------------------------------
    # ALTER TYPE ... ADD VALUE needs its own transaction on some PG versions.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE demandstatusenum ADD VALUE IF NOT EXISTS 'partially_filled'"
        )
        op.execute(
            "ALTER TYPE demandstatusenum ADD VALUE IF NOT EXISTS 'requires_training'"
        )

    _create_enum_if_missing(
        "cvapprovalstatusenum",
        ("not_required", "pending_approval", "approved", "rejected"),
    )
    _create_enum_if_missing(
        "skilloriginenum", ("cv", "project", "training", "manual")
    )
    _create_enum_if_missing(
        "trainingstatusenum", ("assigned", "in_progress", "completed", "cancelled")
    )

    # ------------------------------------------------------------------
    # 2. projects — client + lifecycle dates
    # ------------------------------------------------------------------
    op.add_column(
        "projects",
        sa.Column("client_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("start_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("end_date", sa.Date(), nullable=True),
    )

    # ------------------------------------------------------------------
    # 3. employees — daily capacity (hard cap 8h)
    # ------------------------------------------------------------------
    op.add_column(
        "employees",
        sa.Column(
            "daily_capacity_hours",
            sa.Numeric(precision=4, scale=2),
            nullable=False,
            server_default=sa.text("8.0"),
            comment="Contracted daily working hours (hard cap 8.0)",
        ),
    )
    op.execute(
        """
        UPDATE employees
        SET daily_capacity_hours =
            CASE WHEN employment_type = 'part_time' THEN 4.0 ELSE 8.0 END
        """
    )
    op.create_check_constraint(
        "ck_employee_daily_capacity",
        "employees",
        "daily_capacity_hours > 0 AND daily_capacity_hours <= 8.0",
    )

    # ------------------------------------------------------------------
    # 4. project_demands — hours/day, seniority, immutable headcount
    # ------------------------------------------------------------------
    op.add_column(
        "project_demands",
        sa.Column(
            "hours_per_day",
            sa.Numeric(precision=4, scale=2),
            nullable=False,
            server_default=sa.text("8.0"),
            comment="Daily hours required by this role",
        ),
    )
    op.add_column(
        "project_demands",
        sa.Column(
            "required_experience_level",
            _experience_level(),
            nullable=True,
            comment="Seniority required for the role",
        ),
    )
    op.add_column(
        "project_demands",
        sa.Column(
            "headcount_required",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Original number of seats requested",
        ),
    )
    op.execute(
        """
        UPDATE project_demands d
        SET headcount_required = GREATEST(
            d.headcount_needed,
            COALESCE((
                SELECT count(*) FROM allocations a WHERE a.demand_id = d.id
            ), 0),
            1
        )
        """
    )
    op.create_check_constraint(
        "ck_project_demand_hours_per_day",
        "project_demands",
        "hours_per_day > 0 AND hours_per_day <= 8.0",
    )
    op.create_index(
        "ix_project_demands_project_status",
        "project_demands",
        ["project_id", "status"],
    )

    # ------------------------------------------------------------------
    # 5. allocations — fractional daily hours
    # ------------------------------------------------------------------
    op.add_column(
        "allocations",
        sa.Column(
            "allocated_hours_per_day",
            sa.Numeric(precision=4, scale=2),
            nullable=False,
            server_default=sa.text("8.0"),
            comment="Hours/day consumed from the employee's daily capacity",
        ),
    )
    op.execute(
        """
        UPDATE allocations a
        SET allocated_hours_per_day = ROUND(LEAST(
                8.0,
                GREATEST(0.5, a.allocation_pct / 100.0 * e.daily_capacity_hours)
            )::numeric, 2)
        FROM employees e
        WHERE e.id = a.employee_id
        """
    )
    op.execute(
        """
        UPDATE allocations a
        SET allocation_pct = ROUND(
                a.allocated_hours_per_day / e.daily_capacity_hours * 100.0, 2)
        FROM employees e
        WHERE e.id = a.employee_id
        """
    )
    op.create_check_constraint(
        "ck_allocation_hours_per_day",
        "allocations",
        "allocated_hours_per_day > 0 AND allocated_hours_per_day <= 8.0",
    )
    op.create_index(
        "ix_allocations_employee_status",
        "allocations",
        ["employee_id", "status"],
    )

    # ------------------------------------------------------------------
    # 6. employee_skills — provenance / non-destructive project skills
    # ------------------------------------------------------------------
    op.add_column(
        "employee_skills",
        sa.Column(
            "source",
            _skill_origin(),
            nullable=False,
            server_default=sa.text("'cv'"),
            comment="Where this skill came from: cv | project | training | manual",
        ),
    )
    op.add_column(
        "employee_skills",
        sa.Column(
            "source_ref",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Demand / project / document UUID that produced this skill",
        ),
    )
    op.add_column(
        "employee_skills",
        sa.Column(
            "is_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="True = project-earned; CV updates must never prune it",
        ),
    )
    op.add_column(
        "employee_skills",
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="When the skill was acquired / first attached",
        ),
    )
    op.create_index(
        "ix_employee_skills_employee_source",
        "employee_skills",
        ["employee_id", "source"],
    )

    # Normalise the legacy comma-separated employees.skills column into the
    # taxonomy + join table. This keeps existing profiles searchable and gives
    # them explicit provenance before the seeder runs again.
    op.execute(
        """
        INSERT INTO skills (id, name, is_verified)
        SELECT gen_random_uuid(), MIN(trim(s.skill_name)), false
        FROM employees e
        CROSS JOIN LATERAL regexp_split_to_table(e.skills, ',')
            AS s(skill_name)
        WHERE trim(s.skill_name) <> ''
        GROUP BY lower(trim(s.skill_name))
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO employee_skills (
            employee_id, skill_id, proficiency_level, source, is_locked,
            acquired_at, is_primary
        )
        SELECT
            e.id, sk.id, 'intermediate', 'cv', false, now(), false
        FROM employees e
        CROSS JOIN LATERAL regexp_split_to_table(e.skills, ',')
            AS s(skill_name)
        JOIN skills sk ON lower(sk.name) = lower(trim(s.skill_name))
        WHERE trim(s.skill_name) <> ''
        ON CONFLICT (employee_id, skill_id) DO NOTHING
        """
    )

    op.create_table(
        "employee_skill_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _skill_origin(), nullable=False),
        sa.Column("source_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_employee_skill_evidence_employee_source",
        "employee_skill_evidence",
        ["employee_id", "source"],
    )

    # ------------------------------------------------------------------
    # 7. documents — CV approval staging
    # ------------------------------------------------------------------
    op.add_column(
        "documents",
        sa.Column(
            "approval_status",
            _cv_approval_status(),
            nullable=False,
            server_default=sa.text("'not_required'"),
            comment="Human review state for CVs",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "pending_profile_updates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Staged summary/skills, applied only after manager approval",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "extracted_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("extracted_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "documents",
        sa.Column("review_comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "profile_applied_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_documents_reviewed_by_users",
        "documents",
        "users",
        ["reviewed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE documents
        SET approval_status = 'approved'
        WHERE document_type = 'cv' AND ingestion_status = 'ingested'
        """
    )
    op.create_index(
        "ix_documents_employee_approval",
        "documents",
        ["employee_id", "approval_status"],
    )

    # ------------------------------------------------------------------
    # 8. cv_review_requests
    # ------------------------------------------------------------------
    op.create_table(
        "cv_review_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manager_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            _cv_approval_status(),
            nullable=False,
            server_default=sa.text("'pending_approval'"),
        ),
        sa.Column(
            "proposed_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("proposed_summary", sa.Text(), nullable=True),
        sa.Column("manager_comment", sa.Text(), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index(
        "ix_cv_review_requests_manager_status",
        "cv_review_requests",
        ["manager_id", "status"],
    )
    op.create_index(
        "ix_cv_review_requests_employee_status",
        "cv_review_requests",
        ["employee_id", "status"],
    )

    # ------------------------------------------------------------------
    # 9. training_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "training_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("demand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "targeted_skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("ai_upskilling_path", sa.Text(), nullable=True),
        sa.Column(
            "status",
            _training_status(),
            nullable=False,
            server_default=sa.text("'assigned'"),
        ),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("target_completion_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["demand_id"], ["project_demands.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_training_assignments_employee_status",
        "training_assignments",
        ["employee_id", "status"],
    )

    # ------------------------------------------------------------------
    # 10. email_notifications (outbox)
    # ------------------------------------------------------------------
    op.create_table(
        "email_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column(
            "recipient_employee_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["recipient_employee_id"], ["employees.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_notifications_status_created",
        "email_notifications",
        ["status", "created_at"],
    )

    # ------------------------------------------------------------------
    # 11. Data backfill — project dates from allocation history
    # ------------------------------------------------------------------
    op.execute(
        """
        UPDATE projects p
        SET start_date = x.min_start,
            end_date = x.max_end
        FROM (
            SELECT d.project_id,
                   MIN(a.start_date) AS min_start,
                   MAX(a.end_date) AS max_end
            FROM allocations a
            JOIN project_demands d ON d.id = a.demand_id
            WHERE a.start_date IS NOT NULL
            GROUP BY d.project_id
        ) x
        WHERE p.id = x.project_id
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_notifications_status_created",
        table_name="email_notifications",
    )
    op.drop_table("email_notifications")

    op.drop_index(
        "ix_training_assignments_employee_status",
        table_name="training_assignments",
    )
    op.drop_table("training_assignments")

    op.drop_index(
        "ix_cv_review_requests_employee_status",
        table_name="cv_review_requests",
    )
    op.drop_index(
        "ix_cv_review_requests_manager_status",
        table_name="cv_review_requests",
    )
    op.drop_table("cv_review_requests")

    op.drop_index(
        "ix_documents_employee_approval", table_name="documents"
    )
    op.drop_constraint(
        "fk_documents_reviewed_by_users", "documents", type_="foreignkey"
    )
    for col in (
        "profile_applied_at",
        "review_comment",
        "reviewed_at",
        "reviewed_by",
        "extracted_summary",
        "extracted_skills",
        "pending_profile_updates",
        "approval_status",
    ):
        op.drop_column("documents", col)

    op.drop_index(
        "ix_employee_skill_evidence_employee_source",
        table_name="employee_skill_evidence",
    )
    op.drop_table("employee_skill_evidence")

    op.drop_index(
        "ix_employee_skills_employee_source", table_name="employee_skills"
    )
    for col in ("acquired_at", "is_locked", "source_ref", "source"):
        op.drop_column("employee_skills", col)

    op.drop_index(
        "ix_allocations_employee_status", table_name="allocations"
    )
    op.drop_constraint(
        "ck_allocation_hours_per_day", "allocations", type_="check"
    )
    op.drop_column("allocations", "allocated_hours_per_day")

    op.drop_index(
        "ix_project_demands_project_status", table_name="project_demands"
    )
    op.drop_constraint(
        "ck_project_demand_hours_per_day",
        "project_demands",
        type_="check",
    )
    for col in (
        "headcount_required",
        "required_experience_level",
        "hours_per_day",
    ):
        op.drop_column("project_demands", col)

    op.drop_constraint(
        "ck_employee_daily_capacity", "employees", type_="check"
    )
    op.drop_column("employees", "daily_capacity_hours")

    for col in ("end_date", "start_date", "client_name"):
        op.drop_column("projects", col)

    op.execute("DROP TYPE IF EXISTS trainingstatusenum")
    op.execute("DROP TYPE IF EXISTS skilloriginenum")
    op.execute("DROP TYPE IF EXISTS cvapprovalstatusenum")

    # PostgreSQL cannot remove individual enum values. Values
    # 'partially_filled' / 'requires_training' intentionally remain available.
