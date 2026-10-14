from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict

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

class EmployeeBriefResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    job_title: Optional[str] = None
    experience_level: Optional[str] = None
    skills: Optional[str] = None
    profile_text: Optional[str] = None
    hourly_cost_rate: Optional[Decimal] = None
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

class RejectRequest(BaseModel):
    feedback: str
    reviewer_id: UUID

class ApproveRequest(BaseModel):
    reviewer_id: UUID

