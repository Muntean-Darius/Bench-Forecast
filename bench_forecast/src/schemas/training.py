"""Training / upskilling assignment schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TrainingState = Literal["assigned", "in_progress", "completed", "cancelled"]


class TrainingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: UUID
    title: str = Field(..., min_length=1, max_length=255)
    targeted_skills: list[str] = Field(..., min_length=1)
    ai_upskilling_path: Optional[str] = None
    project_id: Optional[UUID] = None
    demand_id: Optional[UUID] = None
    target_completion_date: Optional[date] = None
    notes: Optional[str] = None


class TrainingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    targeted_skills: Optional[list[str]] = Field(default=None, min_length=1)
    ai_upskilling_path: Optional[str] = None
    status: Optional[TrainingState] = None
    target_completion_date: Optional[date] = None
    notes: Optional[str] = None


class TrainingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    project_id: Optional[UUID] = None
    project_name: Optional[str] = None
    demand_id: Optional[UUID] = None
    title: str
    targeted_skills: list[str] = Field(default_factory=list)
    ai_upskilling_path: Optional[str] = None
    status: TrainingState
    assigned_at: datetime
    target_completion_date: Optional[date] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
