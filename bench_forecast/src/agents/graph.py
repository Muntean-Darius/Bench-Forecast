"""LangGraph workflow: the forecast PLANNER.

Architecture (canonical HITL flow)
----------------------------------
The graph is a **linear planner** that turns bench supply + open demand into
persisted allocation proposals. It deliberately has NO interrupt / checkpoint
node:

    data_extractor → skill_matcher → allocation_decider → forecast_planner → END

Human-in-the-loop is handled by the PostgreSQL allocation state machine
(``allocations.status``), not by a graph interrupt:

    proposed_by_ai → pending_manager_approval → approved_by_manager → active
                                              ↘ rejected_by_manager → (re-plan)

Why: the per-proposal UI (approve / reject / assign / release) is the single
source of truth. Interrupting the graph and resuming it to "execute" a batch
created a second, conflicting execution path (it would re-allocate the whole
plan on a single approval) and depended on an in-process MemorySaver that lost
state on restart. Removing the interrupt removes that whole class of bugs —
there is no checkpoint state to persist, and runs are safe to execute in a
background task.

LangSmith tracing is enabled implicitly via the standard LangChain environment
variables (see the module docstring in project docs).
"""

import logging
import os
from typing import Any

from langgraph.graph import END, StateGraph

from src.agents.state import State
from src.agents.nodes import (
    allocation_decider_node,
    data_extractor_node,
    forecast_planner_node,
    skill_matcher_node,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LangSmith tracing — enabled via environment variables
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


def build_forecast_graph() -> Any:
    """Compile the linear forecast planner.

    Returns:
        A compiled LangGraph workflow that runs to completion and yields the
        ``decisions`` / ``recommendations`` in its final state.
    """
    workflow = StateGraph(State)

    workflow.add_node("data_extractor", data_extractor_node)
    workflow.add_node("skill_matcher", skill_matcher_node)
    workflow.add_node("allocation_decider", allocation_decider_node)
    workflow.add_node("forecast_planner", forecast_planner_node)

    workflow.set_entry_point("data_extractor")
    workflow.add_edge("data_extractor", "skill_matcher")
    workflow.add_edge("skill_matcher", "allocation_decider")
    workflow.add_edge("allocation_decider", "forecast_planner")
    workflow.add_edge("forecast_planner", END)

    graph = workflow.compile()

    logger.info(
        "✓ LangGraph planner compiled: "
        "data_extractor → skill_matcher → allocation_decider → forecast_planner → END"
    )
    return graph
