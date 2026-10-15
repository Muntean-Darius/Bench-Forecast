from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, field_validator

from src.database.models import AllocationStatusEnum

class ProjectBriefResponse(BaseModel):
    id: UUID
    name: str
    status: Optional[str] = "active"
    probability: Optional[int] = 80
    target_margin: Optional[Decimal] = Decimal("25.00")

    model_config = ConfigDict(from_attributes=True)

class DemandResponse(BaseModel):
    id: UUID
    project_id: Optional[UUID] = None
    role: str
    description: Optional[str] = None
    headcount_needed: int = 1
    target_bill_rate: Optional[Decimal] = None
    target_margin: Optional[Decimal] = None
    win_probability: Optional[Decimal] = None
    needed_by_date: Optional[date] = None
    required_skills: Optional[Any] = None
    project: Optional[ProjectBriefResponse] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("required_skills", mode="before")
    @classmethod
    def _normalize_required_skills(cls, v):
        """ProjectDemand.required_skills is JSONB — accept dict/list/str/None."""
        if not v:
            return []
        if isinstance(v, dict):
            inner = v.get("skills")
            if isinstance(inner, (list, tuple, set)):
                return [str(s) for s in inner]
            return [str(k) for k in v.keys()]
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(s) for s in v]
        return [str(v)]

class EmployeeBriefResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    job_title: Optional[str] = None
    experience_level: Optional[str] = None
    skills: Optional[str] = None
    profile_text: Optional[str] = None
    hourly_cost_rate: Optional[Decimal] = None
    cost_rate: Optional[Decimal] = None
    experience_years: Optional[Decimal] = None
    bench_start_date: Optional[date] = None
    bench_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class AllocationPendingResponse(BaseModel):
    id: UUID
    employee_id: UUID
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    role_on_project: str
    allocation_pct: Decimal
    status: AllocationStatusEnum
    ai_justification: Optional[str] = None
    ai_match_score: Optional[Decimal] = None
    ai_projected_margin: Optional[Decimal] = None
    ai_upskilling_path: Optional[str] = None
    start_date: Optional[date] = None
    created_at: Optional[datetime] = None
    project_demand: Optional[DemandResponse] = None
    employee: Optional[EmployeeBriefResponse] = None
    
    model_config = ConfigDict(from_attributes=True)

class HiringRecommendationResponse(BaseModel):
    id: UUID
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    demand_id: Optional[UUID] = None
    role: str
    required_skills: List[str] = []
    headcount: int = 1
    justification: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("required_skills", mode="before")
    @classmethod
    def _normalize_required_skills(cls, v):
        if not v:
            return []
        if isinstance(v, dict):
            inner = v.get("skills")
            if isinstance(inner, (list, tuple, set)):
                return [str(s) for s in inner]
            return [str(k) for k in v.keys()]
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(s) for s in v]
        return [str(v)]


class DismissHiringRequest(BaseModel):
    reviewer_id: Optional[UUID] = None


class RejectRequest(BaseModel):
    feedback: str
    reviewer_id: UUID

class ApproveRequest(BaseModel):
    reviewer_id: UUID

