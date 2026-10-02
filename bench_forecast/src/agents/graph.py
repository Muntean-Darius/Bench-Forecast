"""LangGraph state definition, workflow routing, graph compilation, and LangSmith tracing.

This module strictly contains:
  - State:              TypedDict defining the complete workflow state schema.
  - build_forecast_graph(): Graph compilation with HITL interrupt and LangSmith config.
  - LangSmith tracing:  Enabled implicitly via standard LangChain environment variables.

LangSmith tracing is configured via environment variables (set in .env):
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=<your-langsmith-api-key>
    LANGCHAIN_PROJECT=bench-forecast        (optional project tag)
    LANGCHAIN_ENDPOINT=https://api.smith.langchain.com  (default)

These vars are read automatically by all LangChain / LangGraph calls — no
explicit callback setup is required in application code.

Graph flow:
  1. data_extractor_node     → Fetch employees (forecast horizon) + demands (win_prob filter)
  2. skill_matcher_node      → True RAG: ChromaDB retrieval → LLM matching with passages
  3. allocation_decider_node → Per-role financial optimization via RAGPipeline + LLM
  4. forecast_planner_node   → Aggregate allocation strategy generation
  5. [NATIVE INTERRUPT]      → LangGraph interrupt_before=["human_review"] pauses here
  6. human_review_node       → Reads human_approved / rejection_feedback from resume payload
  7. route_after_review()    → Conditional routing:
       ├─ approved            → execution_engine_node → END
       ├─ rejected+feedback   → revision_planner_node → [INTERRUPT again] → human_review_node
       └─ rejected+no feedback OR max revisions → END
"""

import logging
import os
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.agents.state import MAX_REVISIONS, State
from src.agents.nodes import (
    allocation_decider_node,
    data_extractor_node,
    execution_engine_node,
    forecast_planner_node,
    human_review_node,
    revision_planner_node,
    route_after_review,
    skill_matcher_node,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LangSmith tracing — enabled via environment variables
# ---------------------------------------------------------------------------
# These are read automatically by all LangChain internals when set.
# The application does not need to manually attach callbacks.
#
#   LANGCHAIN_TRACING_V2=true
#   LANGCHAIN_API_KEY=<langsmith-api-key>
#   LANGCHAIN_PROJECT=bench-forecast
#
# We log the active configuration at import time so operators can verify
# tracing is enabled without inspecting environment variables manually.
# ---------------------------------------------------------------------------

_TRACING_ENABLED = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
_LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "bench-forecast")
_LANGSMITH_KEY_SET = bool(os.getenv("LANGCHAIN_API_KEY", ""))

if _TRACING_ENABLED and _LANGSMITH_KEY_SET:
    logger.info(
        f"✓ LangSmith tracing ENABLED | project='{_LANGSMITH_PROJECT}' "
        f"endpoint='{os.getenv('LANGCHAIN_ENDPOINT', 'https://api.smith.langchain.com')}'"
    )
elif _TRACING_ENABLED and not _LANGSMITH_KEY_SET:
    logger.warning(
        "⚠ LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY is not set — "
        "tracing will fail. Set LANGCHAIN_API_KEY in .env to enable LangSmith."
    )
else:
    logger.info(
        "ℹ LangSmith tracing DISABLED "
        "(set LANGCHAIN_TRACING_V2=true + LANGCHAIN_API_KEY to enable)"
    )


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_forecast_graph() -> Any:
    """Construct the LangGraph workflow with HITL feedback loop.

    Key design decisions:
    - interrupt_before=["human_review"]: pauses BEFORE human_review_node every
      time it is reached — both after initial planning AND after each revision
      cycle. This gives managers a review checkpoint at every iteration.
    - route_after_review() enforces MAX_REVISIONS to prevent infinite loops.
    - revision_planner routes BACK to human_review, re-triggering the interrupt,
      so the manager always reviews the revised plan before execution.
    - MemorySaver persists full state across interrupt/resume cycles;
      no LLM nodes are replayed on resume.
    - LangSmith tracing is captured automatically via LangChain env vars —
      all LLM calls inside nodes are traced without explicit callback wiring.

    Graph topology:
        data_extractor
            ↓
        skill_matcher
            ↓
        allocation_decider   ← financially filtered ChromaDB → LLM
            ↓
        forecast_planner
            ↓
        [INTERRUPT before human_review]
            ↓
        human_review
            ↓ (conditional)
        ┌── execution → END
        ├── revision_planner → human_review (loop, max MAX_REVISIONS times)
        └── END

    Returns:
        Compiled LangGraph workflow (CompiledGraph) ready to invoke.
    """
    workflow = StateGraph(State)

    # Register all nodes
    workflow.add_node("data_extractor", data_extractor_node)
    workflow.add_node("skill_matcher", skill_matcher_node)
    workflow.add_node("allocation_decider", allocation_decider_node)
    workflow.add_node("forecast_planner", forecast_planner_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("revision_planner", revision_planner_node)
    workflow.add_node("execution", execution_engine_node)

    # Linear pipeline: extract → match → decide → plan → review
    workflow.set_entry_point("data_extractor")
    workflow.add_edge("data_extractor", "skill_matcher")
    workflow.add_edge("skill_matcher", "allocation_decider")
    workflow.add_edge("allocation_decider", "forecast_planner")
    workflow.add_edge("forecast_planner", "human_review")

    # HITL decision point: approve → execute | reject+feedback → revise | else → end
    workflow.add_conditional_edges(
        "human_review",
        route_after_review,
        {
            "execution": "execution",
            "revision_planner": "revision_planner",
            "end": END,
        },
    )

    # Revision loop: revised plan → back to human_review (re-triggers interrupt)
    workflow.add_edge("revision_planner", "human_review")

    # Approved execution → done
    workflow.add_edge("execution", END)

    # Compile with:
    # - MemorySaver: persists checkpoints across interrupt/resume cycles
    # - interrupt_before=["human_review"]: pauses BEFORE human_review each time
    graph = workflow.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["human_review"],
    )

    logger.info(
        "✓ LangGraph workflow compiled: "
        "data_extractor → skill_matcher → allocation_decider → forecast_planner → "
        "[INTERRUPT] human_review → {execution | revision_planner→human_review | END}"
    )

    return graph
