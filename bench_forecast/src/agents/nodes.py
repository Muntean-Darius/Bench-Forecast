"""Agent nodes for the bench forecast workflow.

Nodes define discrete processing steps in the LangGraph state machine:
1. data_extractor_node: Deterministic - fetch bench employees and open demands
2. skill_matcher_node: LLM-driven - match employees to roles with reasoning
3. forecast_planner_node: LLM-driven - generate allocation strategy
4. execution_engine_node: Deterministic - execute approved allocations

The extraction and execution nodes are deterministic functions.
The matching and planning nodes use Llama-3 to generate reasoning in strict JSON.
"""

from typing import Any, Dict
import logging
import json

from src.agents.state import State
from src.database.sql_db import SQLiteManager
from src.core.llm import LLMFactory, MatchPromptBuilder
from src.schemas.models import MatchJustification


logger = logging.getLogger(__name__)


def data_extractor_node(state: State) -> Dict[str, Any]:
    """Deterministic extraction of active bench talent and upcoming project demand.
    
    This node:
    - Fetches all available employees from SQLite
    - Fetches all open demands from SQLite
    - Stores them in state for downstream processing
    
    Why deterministic: No reasoning needed, just data retrieval.
    
    Args:
        state: Current workflow state
        
    Returns:
        Updated state with employees and demands populated
    """
    logger.info("🔄 DATA EXTRACTOR: Fetching bench employees and open demands")
    
    db = SQLiteManager()
    
    # Fetch from database
    employees = db.get_bench_employees()
    demands = db.get_open_demands()
    
    logger.info(
        f"✓ Extracted {len(employees)} bench employees and {len(demands)} open demands"
    )
    
    return {
        "employees": employees,
        "demands": demands,
    }


def skill_matcher_node(state: State) -> Dict[str, Any]:
    """RAG-based semantic matching of employee profiles against demand requirements.
    
    This node:
    - Iterates each employee against each demand
    - Uses Llama-3 to generate match_score and justification
    - Enforces JSON output from LLM
    - Stores matches sorted by score
    
    Why LLM-driven: Requires reasoning about semantic skill alignment and experience fit.
    
    Args:
        state: Workflow state with employees and demands populated
        
    Returns:
        Updated state with skill_matches (list of match dictionaries)
    """
    logger.info("🔄 SKILL MATCHER: Running employee-to-demand matching")
    
    employees = state["employees"]
    demands = state["demands"]
    
    if not employees or not demands:
        logger.warning("No employees or demands to match")
        return {"skill_matches": []}
    
    # Initialize LLM
    llm = LLMFactory.get_chat_model()
    
    all_matches = []
    
    # Match each employee to each demand
    for employee in employees:
        for demand in demands:
            try:
                logger.debug(f"  Matching {employee.name} -> {demand.role}")
                
                # Build prompt with JSON enforcement
                system_msg, human_msg = MatchPromptBuilder.build_matching_prompt(
                    employee_profile=employee.profile_text or f"{employee.name} with skills: {', '.join(employee.skills)}",
                    job_description=demand.description or f"{demand.role} requiring: {', '.join(demand.required_skills)}",
                    required_skills=demand.required_skills,
                    employee_skills=employee.skills,
                )
                
                # Call LLM and extract JSON
                response = llm.invoke([system_msg, human_msg])
                response_text = response.content
                
                match_json = LLMFactory.parse_json_response(response_text)
                
                # Validate against schema and normalize
                match = MatchJustification(
                    employee_id=employee.id,
                    demand_id=demand.id,
                    match_score=match_json.get("match_score", 0.0),
                    reasoning=match_json.get("reasoning", ""),
                    missing_skills=match_json.get("missing_skills", []),
                    training_recommendation=match_json.get("training_recommendation", None),
                )
                
                all_matches.append(match.model_dump())
                logger.debug(f"    Score: {match.match_score:.2f}")
                
            except Exception as e:
                logger.error(f"  ✗ Error matching {employee.name} to {demand.role}: {e}")
                continue
    
    # Sort by match score descending
    all_matches.sort(key=lambda x: x["match_score"], reverse=True)
    
    logger.info(f"✓ Generated {len(all_matches)} skill matches")
    
    return {
        "skill_matches": all_matches,
    }


def forecast_planner_node(state: State) -> Dict[str, Any]:
    """Reasoning engine generating structured allocation, training, and hiring recommendations.
    
    This node:
    - Analyzes top skill matches
    - Uses Llama-3 to generate allocation strategy
    - Recommends reallocations, trainings, and hirings
    - Enforces JSON output from LLM
    
    Why LLM-driven: Requires strategic reasoning about resource constraints and ROI.
    
    Args:
        state: Workflow state with skill_matches populated
        
    Returns:
        Updated state with recommendations (AllocationRecommendation object)
    """
    logger.info("🔄 FORECAST PLANNER: Generating allocation recommendations")
    
    matches = state.get("skill_matches", [])
    employees = state.get("employees", [])
    demands = state.get("demands", [])
    
    if not matches:
        logger.warning("No matches to plan from")
        return {
            "recommendations": {
                "reallocations": [],
                "trainings": [],
                "hirings": [],
                "confidence_score": 0.0,
                "reasoning": "No viable matches found",
            }
        }
    
    # Initialize LLM
    llm = LLMFactory.get_chat_model()
    
    try:
        # Build planning prompt
        system_msg, human_msg = MatchPromptBuilder.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
        )
        
        # Call LLM
        response = llm.invoke([system_msg, human_msg])
        response_text = response.content
        
        # Extract and parse JSON
        plan_json = LLMFactory.parse_json_response(response_text)
        
        logger.info(
            f"✓ Generated plan with "
            f"{len(plan_json.get('reallocations', []))} reallocations, "
            f"{len(plan_json.get('trainings', []))} trainings, "
            f"{len(plan_json.get('hirings', []))} hirings"
        )
        
        return {
            "recommendations": plan_json,
        }
        
    except Exception as e:
        logger.error(f"✗ Error in forecast planning: {e}")
        return {
            "recommendations": {
                "reallocations": [],
                "trainings": [],
                "hirings": [],
                "confidence_score": 0.0,
                "reasoning": f"Planning failed: {str(e)}",
            }
        }


def execution_engine_node(state: State) -> Dict[str, Any]:
    """Deterministic execution of human-approved actions updating operational records.
    
    This node:
    - Reads approved recommendations from HITL review
    - For each approved reallocation, updates employee's current_project in DB
    - Creates audit trail of executions
    - This is where human approval flows into deterministic action
    
    Why deterministic: No reasoning, just executing the human-approved plan.
    
    Args:
        state: Workflow state with human_approved flag set
        
    Returns:
        Updated state with execution_status
    """
    logger.info("🔄 EXECUTION ENGINE: Processing approved allocations")
    
    if not state.get("human_approved"):
        logger.info("  No human approval, skipping execution")
        return {
            "execution_status": "skipped_no_approval",
        }
    
    recommendations = state.get("recommendations", {})
    reallocations = recommendations.get("reallocations", [])
    
    if not reallocations:
        logger.info("  No reallocations to execute")
        return {
            "execution_status": "completed_no_actions",
        }
    
    db = SQLiteManager()
    
    executed_count = 0
    for reallocation in reallocations:
        try:
            # This is the critical "deterministic function that executes database updates"
            allocation_id = db.update_allocation(
                recommendation_id=recommendations.get("recommendation_id", "unknown"),
                employee_id=reallocation["employee_id"],
                target_project_id=reallocation["target_project_id"],
                role=reallocation["role"],
                match_score=reallocation["match_score"],
                approved_by=state.get("human_feedback", "system"),  # Use feedback as approver name
            )
            
            logger.info(
                f"  ✓ Executed: {reallocation['employee_id']} -> "
                f"{reallocation['target_project_id']} (allocation_id: {allocation_id})"
            )
            executed_count += 1
            
        except Exception as e:
            logger.error(
                f"  ✗ Failed to execute reallocation for {reallocation.get('employee_id')}: {e}"
            )
            continue
    
    logger.info(f"✓ Execution complete: {executed_count}/{len(reallocations)} reallocations executed")
    
    return {
        "execution_status": f"executed_{executed_count}_of_{len(reallocations)}",
    }

