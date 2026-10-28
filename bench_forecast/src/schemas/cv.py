"""CV upload / approval workflow schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CvApprovalState = Literal["not_required", "pending_approval", "approved", "rejected"]


class UploadedDocumentInfo(BaseModel):
    document_id: UUID
    employee_id: UUID
    file_name: str
    file_path: str
    version: int
    approval_status: CvApprovalState
    ingestion_status: str
    is_current: bool
    uploaded_at: Optional[datetime] = None


class CvReviewSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    review_id: UUID
    document_id: UUID
    employee_id: UUID
    employee_name: str
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    status: CvApprovalState
    proposed_skills: list[str] = Field(default_factory=list)
    requested_at: datetime
    reviewed_at: Optional[datetime] = None
    manager_comment: Optional[str] = None


class CvDiffResponse(BaseModel):
    review_id: UUID
    document_id: UUID
    employee_id: UUID
    employee_name: str
    document: UploadedDocumentInfo
    existing_skills: list[str] = Field(default_factory=list)
    proposed_skills: list[str] = Field(default_factory=list)
    new_skills: list[str] = Field(default_factory=list)
    retained_skills: list[str] = Field(default_factory=list)
    project_earned_skills: list[str] = Field(default_factory=list)
    summary_before: Optional[str] = None
    summary_after: Optional[str] = None
    status: CvApprovalState
    manager_comment: Optional[str] = None


class CvApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: Optional[str] = None


class CvRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(..., min_length=1, max_length=4000)


class CvStatusResponse(BaseModel):
    employee_id: UUID
    state: Literal["live", "pending_approval", "changes_requested"]
    label: str
    document_id: Optional[UUID] = None
    version: Optional[int] = None
    submitted_at: Optional[datetime] = None
    manager_id: Optional[UUID] = None
    manager_name: Optional[str] = None
    manager_comment: Optional[str] = None
    review_id: Optional[UUID] = None
    proposed_skills: list[str] = Field(default_factory=list)
