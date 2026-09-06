from typing import Any, Dict, List, Optional, TypedDict
from src.schemas.models import AllocationRecommendation, Demand, Employee


class State(TypedDict):
    employees: List[Employee]
    demands: List[Demand]
    skill_matches: Dict[str, Any]
    recommendations: Optional[AllocationRecommendation]
    human_approved: bool
    human_feedback: Optional[str]
    execution_status: Optional[str]
