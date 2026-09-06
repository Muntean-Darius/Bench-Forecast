from typing import Any
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from src.agents.nodes import (
    data_extractor_node,
    execution_engine_node,
    forecast_planner_node,
    skill_matcher_node,
)
from src.agents.state import State


def route_after_planner(state: State) -> str:
    """Conditional router checking human approval status."""
    ...


def build_forecast_graph() -> Any:
    """Constructs the workflow graph with HITL breakpoint before execution."""
    ...
