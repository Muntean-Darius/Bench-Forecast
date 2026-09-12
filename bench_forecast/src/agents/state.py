from typing import Any, Dict, List, Optional, TypedDict
from src.schemas.models import AllocationRecommendation, Demand, Employee

MAX_REVISIONS = 2  # Maximum feedback-driven revision cycles before forcing END


class State(TypedDict):
    """LangGraph workflow state for bench forecast pipeline."""
    employees: List[Employee]            # Bench employees within forecast horizon
    demands: List[Demand]                # High-probability open demands
    skill_matches: List[Dict[str, Any]]  # LLM match results with RAG context
    recommendations: Optional[Dict[str, Any]]  # Current allocation plan
    human_approved: bool                 # Set by HITL resume: True=approve, False=reject
    human_feedback: Optional[str]        # Approver name (on approval)
    rejection_feedback: Optional[str]    # Manager's reason for rejection + revision guidance
    revision_count: int                  # Number of revision cycles completed
    execution_status: Optional[str]      # Final execution result
    # Forecast parameters — passed from API into data_extractor_node
    horizon_days: int                    # Look-ahead window in days (default 90)
    min_win_probability: float           # Pipeline filter threshold (default 0.75)
