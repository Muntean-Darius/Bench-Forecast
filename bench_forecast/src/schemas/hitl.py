from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from src.database.models import AllocationStatusEnum

class DemandResponse(BaseModel):
    id: UUID
    role: str
    description: Optional[str] = None
    headcount_needed: int
    target_bill_rate: Optional[Decimal] = None
    
    model_config = ConfigDict(from_attributes=True)

class EmployeeBriefResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    job_title: Optional[str] = None
    experience_level: Optional[str] = None
    skills: Optional[str] = None
    profile_text: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class AllocationPendingResponse(BaseModel):
    id: UUID
    employee_id: UUID
    project_id: Optional[UUID] = None
    role_on_project: str
    allocation_pct: Decimal
    status: AllocationStatusEnum
    ai_justification: Optional[str] = None
    ai_match_score: Optional[Decimal] = None
    ai_projected_margin: Optional[Decimal] = None
    project_demand: Optional[DemandResponse] = None
    employee: Optional[EmployeeBriefResponse] = None
    
    model_config = ConfigDict(from_attributes=True)

class RejectRequest(BaseModel):
    feedback: str
    reviewer_id: UUID

class ApproveRequest(BaseModel):
    reviewer_id: UUID

