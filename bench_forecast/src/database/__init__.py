"""Database package — SQLAlchemy models, connection, and session management.

Public surface:
  models    — ORM model classes (Base, Department, Employee, Skill, EmployeeSkill,
              Document, Project, ProjectRole, Allocation)
  database  — Engine factories, session deps (get_db, get_sync_db, init_db)
"""

from src.database.models import (
    Allocation,
    Base,
    Department,
    Document,
    DocumentTypeEnum,
    Employee,
    EmployeeSkill,
    ExperienceLevelEnum,
    IngestionStatusEnum,
    ProficiencyLevelEnum,
    Project,
    ProjectRole,
    Skill,
    compute_projected_margin,
)
from src.database.database import (
    AsyncSessionLocal,
    SyncSessionLocal,
    check_db_connection,
    get_db,
    get_sync_db,
    init_db,
    init_db_sync,
)

__all__ = [
    # Models
    "Base",
    "Department",
    "Employee",
    "Skill",
    "EmployeeSkill",
    "Document",
    "Project",
    "ProjectRole",
    "Allocation",
    # Enums
    "ExperienceLevelEnum",
    "ProficiencyLevelEnum",
    "DocumentTypeEnum",
    "IngestionStatusEnum",
    # Utilities
    "compute_projected_margin",
    # Session / engine
    "AsyncSessionLocal",
    "SyncSessionLocal",
    "get_db",
    "get_sync_db",
    "init_db",
    "init_db_sync",
    "check_db_connection",
]
