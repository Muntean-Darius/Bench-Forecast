"""LangGraph workflow state definition and constants.

This module defines the canonical State TypedDict and MAX_REVISIONS used
across the entire bench forecast workflow. It has NO imports from other
src.agents modules to prevent circular import chains:

    nodes.py  → state.py   (OK: nodes imports State)
    graph.py  → state.py   (OK: graph imports State)
    graph.py  → nodes.py   (OK: graph imports node functions)

The architectural rule is: state.py is a leaf module that nothing in agents/
depends on at import time — only State and MAX_REVISIONS flow outward.
"""

from typing import Any, Dict, List, Optional, TypedDict


MAX_REVISIONS = 2  # Maximum feedback-driven revision cycles before forcing END


class State(TypedDict):
    """LangGraph workflow state for the bench forecast pipeline.

    All fields are optional at initialisation; nodes progressively populate
    them as the workflow executes. TypedDict provides static type safety
    without requiring a Pydantic BaseModel (LangGraph requires TypedDict).
    """

    # -- Data Layer --
    employees: List[Any]               # Bench employees within forecast horizon
    demands: List[Any]                 # High-probability open demands / ProjectRoles

    # -- AI Matching Layer --
    skill_matches: List[Dict[str, Any]]        # LLM match results with RAG context
    allocation_decisions: List[Dict[str, Any]] # Per-role financial allocation decisions

    # -- Planning Layer --
    recommendations: Optional[Dict[str, Any]]  # Current allocation plan (aggregate)

    # -- HITL Layer --
    human_approved: bool               # Set by HITL resume: True=approve, False=reject
    human_feedback: Optional[str]      # Approver name (on approval)
    rejection_feedback: Optional[str]  # Manager's reason for rejection + revision guidance
    revision_count: int                # Number of revision cycles completed

    # -- Execution Layer --
    execution_status: Optional[str]    # Final execution result

    # -- Configuration (passed from API into data_extractor_node) --
    horizon_days: int                  # Look-ahead window in days (default 90)
    min_win_probability: float         # Pipeline filter threshold (default 0.75)
