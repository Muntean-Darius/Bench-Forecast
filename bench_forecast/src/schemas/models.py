from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class Employee(BaseModel):
    """Employee record with skills and availability."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    id: str = Field(..., min_length=1, description="Unique employee ID")
    name: str = Field(..., min_length=1, description="Employee name")
    skills: List[str] = Field(default_factory=list, description="List of technical skills")
    current_project: Optional[str] = Field(None, description="Current project assignment")
    available_from: date = Field(..., description="Date when employee becomes available")
    experience_years: float = Field(..., ge=0, description="Years of experience")
    profile_text: Optional[str] = Field(None, description="Unstructured CV/profile for RAG")

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, v: List[str]) -> List[str]:
        """Normalize and deduplicate skills."""
        return list(set(s.lower().strip() for s in v if s.strip()))


class Demand(BaseModel):
    """Open role/project demand."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    id: str = Field(..., min_length=1, description="Unique demand ID")
    role: str = Field(..., min_length=1, description="Job title/role name")
    required_skills: List[str] = Field(default_factory=list, description="Required skills")
    project_id: str = Field(..., min_length=1, description="Project identifier")
    start_date: date = Field(..., description="Role start date")
    headcount: int = Field(default=1, ge=1, description="Number of positions available")
    description: Optional[str] = Field(None, description="Unstructured role description for RAG")

    @field_validator("required_skills")
    @classmethod
    def validate_required_skills(cls, v: List[str]) -> List[str]:
        """Normalize and deduplicate required skills."""
        return list(set(s.lower().strip() for s in v if s.strip()))


class MatchJustification(BaseModel):
    """AI-generated justification for a skill match (enforced JSON from LLM)."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    employee_id: str = Field(..., description="Matched employee ID")
    demand_id: str = Field(..., description="Matched demand ID")
    match_score: float = Field(..., ge=0.0, le=1.0, description="Similarity score [0.0, 1.0]")
    reasoning: str = Field(..., min_length=10, description="Detailed justification from LLM")
    missing_skills: List[str] = Field(default_factory=list, description="Skills employee lacks")
    training_recommendation: Optional[str] = Field(
        None, description="Suggested training if applicable"
    )


class Reallocation(BaseModel):
    """Approved reallocation action."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    employee_id: str = Field(..., min_length=1)
    target_project_id: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    match_score: float = Field(..., ge=0.0, le=1.0)


class Training(BaseModel):
    """Proposed training intervention."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    employee_id: str = Field(..., min_length=1)
    target_skills: List[str] = Field(default_factory=list, min_length=1)
    duration_weeks: int = Field(..., ge=1, le=52)


class Hiring(BaseModel):
    """Required new hire specification."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    role: str = Field(..., min_length=1)
    required_skills: List[str] = Field(default_factory=list, min_length=1)
    headcount: int = Field(..., ge=1)


class AllocationRecommendation(BaseModel):
    """Complete allocation forecast recommendation (output of planning node)."""
    
    model_config = ConfigDict(
        strict=True,
        validate_assignment=True,
        extra="forbid"
    )
    
    recommendation_id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    reallocations: List[Reallocation] = Field(default_factory=list)
    trainings: List[Training] = Field(default_factory=list)
    hirings: List[Hiring] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=20)
    created_at: str = Field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat()
    )


