from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field


class Employee(BaseModel):
    id: str
    name: str
    skills: List[str]
    current_project: Optional[str] = None
    available_from: date
    experience_years: float


class Demand(BaseModel):
    id: str
    role: str
    required_skills: List[str]
    project_id: str
    start_date: date
    headcount: int = 1


class Reallocation(BaseModel):
    employee_id: str
    target_project_id: str
    role: str
    match_score: float


class Training(BaseModel):
    employee_id: str
    target_skills: List[str]
    duration_weeks: int


class Hiring(BaseModel):
    role: str
    required_skills: List[str]
    headcount: int


class AllocationRecommendation(BaseModel):
    reallocations: List[Reallocation] = Field(default_factory=list)
    trainings: List[Training] = Field(default_factory=list)
    hirings: List[Hiring] = Field(default_factory=list)
    confidence_score: float
    reasoning: str

