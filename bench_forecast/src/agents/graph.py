"""LangGraph state machine for bench forecast workflow with HITL feedback loop.

Graph flow:
1. data_extractor_node    → Fetch employees (forecast horizon) + demands (win_prob filter)
2. skill_matcher_node     → True RAG: ChromaDB retrieval → LLM matching with passages
3. forecast_planner_node  → Generate initial allocation strategy
4. [NATIVE INTERRUPT]     → LangGraph interrupt_before=["human_review"] pauses here
5. human_review_node      → Reads human_approved / rejection_feedback from resume payload
6. route_after_review()   → Conditional routing:
   ├─ approved            → execution_engine_node → END
   ├─ rejected+feedback   → revision_planner_node → [INTERRUPT again] → human_review_node
   └─ rejected+no feedback OR max revisions → END

The revision loop (step 6 middle path) can repeat up to MAX_REVISIONS times,
giving managers multiple chances to refine the plan through feedback.
"""

from typing import Any
import logging
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from src.agents.nodes import (
    data_extractor_node,
    execution_engine_node,
    forecast_planner_node,
    human_review_node,
    revision_planner_node,
    route_after_review,
    skill_matcher_node,
)
from src.agents.state import State


logger = logging.getLogger(__name__)


def build_forecast_graph() -> Any:
    """Constructs the workflow graph with HITL feedback loop.

    Key design decisions:
    - interrupt_before=["human_review"] pauses before every review checkpoint,
      including after initial planning AND after each revision cycle.
    - route_after_review() enforces MAX_REVISIONS to prevent infinite loops.
    - revision_planner routes BACK to human_review, re-triggering the interrupt,
      so the manager always gets a second look at the revised plan.
    - MemorySaver persists full state across interrupts; no LLM nodes are replayed.

    Returns:
        Compiled LangGraph workflow ready to invoke
    """
    workflow = StateGraph(State)

    # Register all nodes
    workflow.add_node("data_extractor", data_extractor_node)
    workflow.add_node("skill_matcher", skill_matcher_node)
    workflow.add_node("forecast_planner", forecast_planner_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("revision_planner", revision_planner_node)
    workflow.add_node("execution", execution_engine_node)

    # Linear pipeline: extract → match → plan → review
    workflow.set_entry_point("data_extractor")
    workflow.add_edge("data_extractor", "skill_matcher")
    workflow.add_edge("skill_matcher", "forecast_planner")
    workflow.add_edge("forecast_planner", "human_review")

    # HITL decision point: approve → execute, reject+feedback → revise, else → end
    workflow.add_conditional_edges(
        "human_review",
        route_after_review,
        {
            "execution": "execution",
            "revision_planner": "revision_planner",
            "end": END,
        },
    )

    # Revision loop: after generating revised plan, go back to human_review
    # This re-triggers interrupt_before=["human_review"] for another manager review
    workflow.add_edge("revision_planner", "human_review")

    # Approved execution → done
    workflow.add_edge("execution", END)

    # Compile with:
    # - MemorySaver: persists checkpoints across interrupt/resume cycles
    # - interrupt_before=["human_review"]: pauses BEFORE human_review every time it's reached
    #   (both after initial planning and after each revision)
    graph = workflow.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["human_review"],
    )

    logger.info(
        "✓ LangGraph workflow compiled: "
        "data_extractor → skill_matcher → forecast_planner → "
        "[INTERRUPT] human_review → {execution|revision_planner→human_review|END}"
    )

    return graph
