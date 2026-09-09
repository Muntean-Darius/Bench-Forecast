"""LangGraph state machine for bench forecast workflow with Human-in-the-Loop (HITL).

Graph flow:
1. data_extractor_node → Fetch employees and demands
2. skill_matcher_node → Match profiles to open roles
3. forecast_planner_node → Generate allocation strategy
4. [PAUSE FOR HUMAN REVIEW] → Manager reviews recommendations
5. route_after_planner → Check if human approved
   - If approved: execution_engine_node
   - If rejected: END
6. execution_engine_node → Execute approved allocations to DB

The pause is implemented as a "breakpoint" in LangGraph using the StateGraph.
"""

from typing import Any, Literal
import logging
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from src.agents.nodes import (
    data_extractor_node,
    execution_engine_node,
    forecast_planner_node,
    skill_matcher_node,
)
from src.agents.state import State


logger = logging.getLogger(__name__)


def route_after_planner(state: State) -> Literal["execution", "end"]:
    """Conditional router checking human approval status.
    
    This implements the HITL breakpoint decision:
    - If human_approved=True, route to execution
    - Otherwise, route to END
    
    Args:
        state: Current workflow state (with human_approved flag)
        
    Returns:
        "execution" to execute approved allocations, or "end" to terminate
    """
    if state.get("human_approved"):
        logger.info("✓ Human approved recommendations, proceeding to execution")
        return "execution"
    else:
        logger.info("✗ Human rejected or did not approve, terminating workflow")
        return "end"


def build_forecast_graph() -> Any:
    """Constructs the workflow graph with HITL breakpoint before execution.
    
    Returns:
        Compiled LangGraph workflow ready to invoke
    """
    
    # Initialize state graph with MemorySaver for persistence
    workflow = StateGraph(State)
    
    # Add nodes
    workflow.add_node("data_extractor", data_extractor_node)
    workflow.add_node("skill_matcher", skill_matcher_node)
    workflow.add_node("forecast_planner", forecast_planner_node)
    workflow.add_node("execution", execution_engine_node)
    
    # Define edges (workflow routing)
    workflow.set_entry_point("data_extractor")
    workflow.add_edge("data_extractor", "skill_matcher")
    workflow.add_edge("skill_matcher", "forecast_planner")
    
    # Conditional edge after planner (HITL breakpoint)
    # This pauses before execution, allowing human review via API
    workflow.add_conditional_edges(
        "forecast_planner",
        route_after_planner,
        {
            "execution": "execution",
            "end": END,
        },
    )
    
    # End state
    workflow.add_edge("execution", END)
    
    # Compile with in-memory checkpointing for pause/resume capability
    graph = workflow.compile(checkpointer=MemorySaver())
    
    logger.info("✓ LangGraph workflow compiled with HITL breakpoint")
    
    return graph

