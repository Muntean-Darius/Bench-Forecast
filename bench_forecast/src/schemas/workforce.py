"""Workforce / capacity / project-detail API schemas.

All enum values use the lowercase database representation. The frontend is
responsible for title-case presentation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

HoursPerDay = Decimal
RosterStatus = Literal["open", "partially_filled", "filled", "requires_training"]
AllocationState = Literal[
    "proposed_by_ai",
    "pending_manager_approval",
    "approved_by_manager",
    "rejected_by_manager",
    "active",
    "completed",
    "cancelled",
]
ExperienceLevel = Literal["junior", "mid", "senior", "lead", "principal"]
EmploymentType = Literal["full_time", "part_time", "contractor"]


def _validate_hours(value: Decimal) -> Decimal:
    if value is None:
        raise ValueError("hours are required")
    if value <= 0:
        raise ValueError("hours must be greater than 0")
    if value > 8:
        raise ValueError("hours must not exceed the 8.0 h/day hard cap")
    return value


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    client_name: Optional[str] = None
    status: str
    probability: int
    total_budget: float
    target_margin: float
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    open_roles: int = 0
    filled_roles: int = 0
    partially_filled_roles: int = 0
    total_roles: int = 0


class ProjectUtilization(BaseModel):
    demanded_hours_per_day: float
    allocated_hours_per_day: float
    filled_seats: int
    required_seats: int
    utilization_pct: float


class AssignmentOut(BaseModel):
    allocation_id: UUID
    employee_id: UUID
    employee_name: str
    job_title: Optional[str] = None
    allocated_hours_per_day: float
    allocation_pct: float
    remaining_available_hours: float
    status: AllocationState
    ai_match_score: Optional[float] = None
    ai_justification: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class RosterDemandOut(BaseModel):
    demand_id: UUID
    role: str
    description: Optional[str] = None
    seniority_level: Optional[ExperienceLevel] = None
    hours_per_day: float
    headcount_required: int
    headcount_filled: int
    headcount_needed: int
    status: RosterStatus
    required_skills: list[str] = Field(default_factory=list)
    target_bill_rate: Optional[float] = None
    target_margin: Optional[float] = None
    needed_by_date: Optional[date] = None
    assignments: list[AssignmentOut] = Field(default_factory=list)


class ProjectDetailResponse(BaseModel):
    project: ProjectSummary
    utilization: ProjectUtilization
    roster: list[RosterDemandOut] = Field(default_factory=list)


class DemandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    required_skills: list[str] = Field(default_factory=list)
    hours_per_day: HoursPerDay = Decimal("8.00")
    required_experience_level: Optional[ExperienceLevel] = None
    min_proficiency: Optional[str] = None
    headcount_required: int = Field(default=1, ge=1, le=100)
    target_bill_rate: Optional[float] = None
    target_margin: Optional[float] = None
    win_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    needed_by_date: Optional[date] = None

    _hours = field_validator("hours_per_day")(_validate_hours)


class DemandUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    required_skills: Optional[list[str]] = None
    hours_per_day: Optional[HoursPerDay] = None
    required_experience_level: Optional[ExperienceLevel] = None
    min_proficiency: Optional[str] = None
    headcount_required: Optional[int] = Field(default=None, ge=1, le=100)
    target_bill_rate: Optional[float] = None
    target_margin: Optional[float] = None
    win_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    needed_by_date: Optional[date] = None
    status: Optional[RosterStatus] = None
    cancelled: bool = False

    @field_validator("hours_per_day")
    @classmethod
    def validate_hours(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        return _validate_hours(value) if value is not None else value


class DemandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    project_name: Optional[str] = None
    role: str
    description: Optional[str] = None
    required_skills: list[str] = Field(default_factory=list)
    hours_per_day: float
    required_experience_level: Optional[ExperienceLevel] = None
    min_proficiency: Optional[str] = None
    headcount_required: int
    headcount_needed: int
    filled_seats: int
    status: RosterStatus
    target_bill_rate: Optional[float] = None
    target_margin: Optional[float] = None
    win_probability: Optional[float] = None
    needed_by_date: Optional[date] = None


class AllocationAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    demand_id: Optional[UUID] = None
    allocated_hours_per_day: Optional[HoursPerDay] = None
    role_on_project: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    ai_justification: Optional[str] = None

    @field_validator("allocated_hours_per_day")
    @classmethod
    def validate_hours(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        return _validate_hours(value) if value is not None else value


class AllocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allocated_hours_per_day: Optional[HoursPerDay] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: Optional[AllocationState] = None
    manager_notes: Optional[str] = None

    @field_validator("allocated_hours_per_day")
    @classmethod
    def validate_hours(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        return _validate_hours(value) if value is not None else value


class CapacityAllocationOut(BaseModel):
    allocation_id: UUID
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    role_on_project: str
    allocated_hours_per_day: float
    allocation_pct: float
    status: AllocationState
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CapacityOut(BaseModel):
    employee_id: UUID
    employee_name: str
    employment_type: EmploymentType
    daily_capacity_hours: float
    committed_hours_per_day: float
    reserved_hours_per_day: float
    remaining_available_hours: float
    utilization_pct: float
    allocations: list[CapacityAllocationOut] = Field(default_factory=list)


class TeamWorkloadOut(BaseModel):
    employee_id: UUID
    employee_name: str
    job_title: str
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    employment_type: EmploymentType
    daily_capacity_hours: float
    committed_hours_per_day: float
    remaining_available_hours: float
    utilization_pct: float
    bench_status: str
    allocations: list[CapacityAllocationOut] = Field(default_factory=list)
    next_rolloff_date: Optional[date] = None
    pending_cv_reviews: int = 0


class CandidateOut(BaseModel):
    employee_id: UUID
    employee_name: str
    job_title: str
    manager_id: Optional[UUID] = None
    remaining_available_hours: float
    daily_capacity_hours: float
    match_score: float
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    current_allocations: list[CapacityAllocationOut] = Field(default_factory=list)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_capacity_hours: Optional[HoursPerDay] = None
    employment_type: Optional[EmploymentType] = None

    @field_validator("daily_capacity_hours")
    @classmethod
    def validate_hours(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        return _validate_hours(value) if value is not None else value


class EmployeeSkillOut(BaseModel):
    skill_id: UUID
    name: str
    category: Optional[str] = None
    proficiency_level: str
    source: Literal["cv", "project", "training", "manual"]
    is_locked: bool
    acquired_at: Optional[str] = None


class EmployeeSkillsBySource(BaseModel):
    employee_id: UUID
    cv: list[EmployeeSkillOut] = Field(default_factory=list)
    project: list[EmployeeSkillOut] = Field(default_factory=list)
    training: list[EmployeeSkillOut] = Field(default_factory=list)
    manual: list[EmployeeSkillOut] = Field(default_factory=list)
