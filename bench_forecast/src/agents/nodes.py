"""Agent nodes for the bench forecast workflow.

Nodes define discrete processing steps in the LangGraph state machine:
1. data_extractor_node:   Deterministic — fetch bench employees (forecast horizon) and open demands (win_prob filter)
2. skill_matcher_node:    LLM-driven + RAG — query ChromaDB first, inject passages into prompt
3. forecast_planner_node: LLM-driven — generate allocation strategy from matches
4. human_review_node:     Gate — reads HITL resume payload, routes to execution or revision
5. revision_planner_node: LLM-driven — regenerates plan using manager rejection feedback
6. execution_engine_node: Deterministic — execute approved allocations to SQLite
"""

from typing import Any, Dict, Literal
import logging
import json

from src.agents.state import MAX_REVISIONS, State
from src.database.sql_db import SQLiteManager
from src.database.vector_store import VectorStoreManager
from src.core.llm import LLMFactory, MatchPromptBuilder
from src.schemas.models import MatchJustification


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node 1 — Data Extractor (Deterministic)
# ---------------------------------------------------------------------------

def data_extractor_node(state: State) -> Dict[str, Any]:
    """Deterministic extraction of active bench talent and upcoming project demand.

    Uses two new filtered queries:
    - get_bench_forecast(horizon_days): employees finishing projects within N days
      (the temporal forecast trigger from the spec)
    - get_open_demands(min_win_probability): high-confidence pipeline opportunities
      (avoids wasting matching effort on low-probability deals)

    Args:
        state: Current workflow state

    Returns:
        Updated state with employees, demands, and initialized revision fields
    """
    logger.info("🔄 DATA EXTRACTOR: Fetching bench forecast and high-probability demands")

    db = SQLiteManager()

    # Temporal forecast: employees finishing projects in the next 90 days
    horizon_days = state.get("horizon_days", 90)
    employees = db.get_bench_forecast(horizon_days=horizon_days)

    # Pipeline filter: only deal with ≥75% win-probability opportunities
    min_win_prob = state.get("min_win_probability", 0.75)
    demands = db.get_open_demands(min_win_probability=min_win_prob)

    logger.info(
        f"✓ Extracted {len(employees)} forecast employees "
        f"(horizon={horizon_days}d) and {len(demands)} demands "
        f"(win_prob>={min_win_prob:.0%})"
    )

    return {
        "employees": employees,
        "demands": demands,
        "revision_count": state.get("revision_count", 0),
        "rejection_feedback": state.get("rejection_feedback", None),
    }


# ---------------------------------------------------------------------------
# Node 2 — Skill Matcher (LLM + True RAG)
# ---------------------------------------------------------------------------

def skill_matcher_node(state: State) -> Dict[str, Any]:
    """RAG-augmented semantic matching of employee profiles against demand requirements.

    TRUE RAG implementation:
    1. For each demand, query ChromaDB with the demand's narrative description
    2. Retrieve the top-k most semantically similar employee profile passages
    3. Inject those retrieved passages into the LLM prompt as grounding evidence
    4. The LLM MUST cite specific passages in its reasoning (not hallucinate)

    This is the "Retrieval-Augmented Generation" loop described in the spec:
    demand description → vector → ChromaDB cosine search → passages → LLM prompt

    Args:
        state: Workflow state with employees and demands populated

    Returns:
        Updated state with skill_matches (each match includes rag_passages)
    """
    logger.info("🔄 SKILL MATCHER: RAG-augmented employee-to-demand matching")

    employees = state["employees"]
    demands = state["demands"]

    if not employees or not demands:
        logger.warning("No employees or demands to match")
        return {"skill_matches": []}

    llm = LLMFactory.get_chat_model()
    vector_mgr = VectorStoreManager()

    all_matches = []

    for demand in demands:
        # Step 1: ChromaDB retrieval — find most semantically relevant employee passages
        # for THIS demand's narrative description
        rag_query = f"{demand.role} {demand.description or ''}"
        rag_results = []
        try:
            rag_results = vector_mgr.similarity_search(rag_query, k=3)
            if rag_results:
                logger.debug(
                    f"  RAG: '{demand.role}' → top match: "
                    f"{rag_results[0].get('name')} (score={rag_results[0].get('similarity_score'):.2f})"
                )
        except Exception as e:
            logger.warning(f"  ChromaDB query failed for {demand.role}: {e}")

        # Build a lookup of rag_passages per employee_id from retrieval results
        rag_passages_by_emp: Dict[str, list[str]] = {}
        for r in rag_results:
            emp_id = r.get("id", "")
            excerpt = r.get("document_excerpt", "")
            if emp_id and excerpt:
                rag_passages_by_emp.setdefault(emp_id, []).append(excerpt)

        # Step 2: LLM matching — each employee vs this demand, with RAG context
        for employee in employees:
            try:
                logger.debug(f"  Matching {employee.name} → {demand.role}")

                # Get RAG passages for this specific employee (if retrieved)
                emp_rag_passages = rag_passages_by_emp.get(employee.id, [])

                # Also include the employee's own profile_text as a passage if not already there
                if employee.profile_text and employee.profile_text not in emp_rag_passages:
                    emp_rag_passages = [employee.profile_text] + emp_rag_passages

                system_msg, human_msg = MatchPromptBuilder.build_matching_prompt(
                    employee_profile=employee.profile_text or f"{employee.name}: {', '.join(employee.skills)}",
                    job_description=demand.description or f"{demand.role} requiring: {', '.join(demand.required_skills)}",
                    required_skills=demand.required_skills,
                    employee_skills=employee.skills,
                    rag_passages=emp_rag_passages[:3],  # Inject top 3 passages
                )

                response = llm.invoke([system_msg, human_msg])
                match_json = LLMFactory.parse_json_response(response.content)

                # Validate against schema
                match = MatchJustification(
                    employee_id=employee.id,
                    demand_id=demand.id,
                    match_score=match_json.get("match_score", 0.0),
                    reasoning=match_json.get("reasoning", ""),
                    missing_skills=match_json.get("missing_skills", []),
                    training_recommendation=match_json.get("training_recommendation", None),
                    rag_passages=emp_rag_passages,  # Persist passages in match record
                )

                match_dict = match.model_dump()
                # Add demand metadata for planning node
                match_dict["target_project_id"] = demand.project_id
                all_matches.append(match_dict)
                logger.debug(
                    f"    Score: {match.match_score:.2f} "
                    f"(RAG passages: {len(emp_rag_passages)})"
                )

            except Exception as e:
                logger.error(f"  ✗ Error matching {employee.name} to {demand.role}: {e}")
                continue

    # Sort by match score descending
    all_matches.sort(key=lambda x: x["match_score"], reverse=True)
    logger.info(f"✓ Generated {len(all_matches)} RAG-grounded skill matches")

    return {"skill_matches": all_matches}


# ---------------------------------------------------------------------------
# Node 3 — Forecast Planner (LLM)
# ---------------------------------------------------------------------------

def forecast_planner_node(state: State) -> Dict[str, Any]:
    """Reasoning engine generating structured allocation, training, and hiring recommendations.

    Args:
        state: Workflow state with skill_matches populated

    Returns:
        Updated state with recommendations dict
    """
    logger.info("🔄 FORECAST PLANNER: Generating initial allocation recommendations")

    matches = state.get("skill_matches", [])
    employees = state.get("employees", [])
    demands = state.get("demands", [])

    if not matches:
        logger.warning("No matches to plan from")
        return {
            "recommendations": {
                "recommendation_id": __import__("uuid").uuid4().hex,
                "reallocations": [],
                "trainings": [],
                "hirings": [],
                "confidence_score": 0.0,
                "reasoning": "No viable matches found — all employees may lack required skills.",
                "revision_note": None,
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            }
        }

    llm = LLMFactory.get_chat_model()

    try:
        system_msg, human_msg = MatchPromptBuilder.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
        )

        response = llm.invoke([system_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)

        # Enrich plan with metadata
        plan_json.setdefault("recommendation_id", __import__("uuid").uuid4().hex)
        plan_json.setdefault("revision_note", None)
        plan_json.setdefault("created_at", __import__("datetime").datetime.utcnow().isoformat())

        logger.info(
            f"✓ Plan generated: {len(plan_json.get('reallocations', []))} reallocations, "
            f"{len(plan_json.get('trainings', []))} trainings, "
            f"{len(plan_json.get('hirings', []))} hirings"
        )

        return {"recommendations": plan_json}

    except Exception as e:
        logger.error(f"✗ Error in forecast planning: {e}")
        return {
            "recommendations": {
                "recommendation_id": __import__("uuid").uuid4().hex,
                "reallocations": [],
                "trainings": [],
                "hirings": [],
                "confidence_score": 0.0,
                "reasoning": f"Planning failed: {str(e)}",
                "revision_note": None,
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            }
        }


# ---------------------------------------------------------------------------
# Node 4 — Human Review Gate (HITL checkpoint reader)
# ---------------------------------------------------------------------------

def human_review_node(state: State) -> Dict[str, Any]:
    """HITL gate — reads the resume payload and determines next action.

    This node runs AFTER the LangGraph interrupt_before=["human_review"] fires.
    The /execute endpoint injects human_approved + rejection_feedback via
    Command(resume={...}), which LangGraph merges into state before this runs.

    The node itself does not route — routing is handled by route_after_review().
    This node just increments revision_count on rejection so the router can
    enforce MAX_REVISIONS.

    Args:
        state: State with human_approved and rejection_feedback set by resume

    Returns:
        Updated revision_count if rejected
    """
    approved = state.get("human_approved", False)
    feedback = state.get("rejection_feedback")

    if approved:
        logger.info("✓ HUMAN REVIEW: Manager APPROVED — routing to execution")
        return {}
    elif feedback:
        new_count = state.get("revision_count", 0) + 1
        logger.info(
            f"✗ HUMAN REVIEW: Manager REJECTED with feedback "
            f"(revision {new_count}/{MAX_REVISIONS}): '{feedback[:80]}...'"
        )
        return {"revision_count": new_count}
    else:
        logger.info("✗ HUMAN REVIEW: Manager REJECTED without feedback — terminating")
        return {}


def route_after_review(state: State) -> Literal["execution", "revision_planner", "end"]:
    """Conditional router after human_review_node.

    - Approved → execution
    - Rejected with feedback AND revisions remaining → revision_planner
    - Rejected without feedback OR max revisions reached → end

    Args:
        state: State with human_approved, rejection_feedback, revision_count

    Returns:
        Route name
    """
    if state.get("human_approved"):
        return "execution"

    feedback = state.get("rejection_feedback")
    revision_count = state.get("revision_count", 0)

    if feedback and revision_count <= MAX_REVISIONS:
        logger.info(f"  → Routing to revision_planner (attempt {revision_count}/{MAX_REVISIONS})")
        return "revision_planner"

    logger.info("  → Routing to END (no feedback or max revisions reached)")
    return "end"


# ---------------------------------------------------------------------------
# Node 5 — Revision Planner (LLM — triggered by manager rejection feedback)
# ---------------------------------------------------------------------------

def revision_planner_node(state: State) -> Dict[str, Any]:
    """Generates a revised allocation plan incorporating manager rejection feedback.

    This closes the HITL feedback loop: instead of killing the workflow on
    rejection, the manager's feedback is injected into a new LLM planning call
    with the PREVIOUS plan as context. The LLM must produce a meaningfully
    different plan that addresses the stated concerns.

    Args:
        state: State with rejection_feedback and current recommendations

    Returns:
        Updated recommendations with a new plan and revision_note
    """
    feedback = state.get("rejection_feedback", "No specific feedback provided.")
    previous_plan = state.get("recommendations", {})
    matches = state.get("skill_matches", [])
    employees = state.get("employees", [])
    demands = state.get("demands", [])
    revision_count = state.get("revision_count", 0)

    logger.info(
        f"🔄 REVISION PLANNER: Generating revised plan "
        f"(attempt {revision_count}/{MAX_REVISIONS})\n"
        f"  Manager feedback: '{feedback[:100]}...'"
    )

    llm = LLMFactory.get_chat_model()

    try:
        system_msg, human_msg = MatchPromptBuilder.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
            rejection_feedback=feedback,
            previous_plan=previous_plan,
        )

        response = llm.invoke([system_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)

        # Tag the new plan with revision metadata
        plan_json["recommendation_id"] = __import__("uuid").uuid4().hex
        plan_json["revision_note"] = feedback
        plan_json["created_at"] = __import__("datetime").datetime.utcnow().isoformat()

        logger.info(
            f"✓ Revised plan: {len(plan_json.get('reallocations', []))} reallocations "
            f"(confidence={plan_json.get('confidence_score', 0):.2f})"
        )

        # Reset human_approved so the interrupt fires again for the revised plan
        return {
            "recommendations": plan_json,
            "human_approved": False,
            "rejection_feedback": None,  # Clear so next rejection gets a fresh slot
        }

    except Exception as e:
        logger.error(f"✗ Revision planning failed: {e}")
        return {
            "recommendations": {
                **previous_plan,
                "recommendation_id": __import__("uuid").uuid4().hex,
                "revision_note": f"Revision failed: {e}. Original plan retained.",
                "created_at": __import__("datetime").datetime.utcnow().isoformat(),
            },
            "human_approved": False,
            "rejection_feedback": None,
        }


# ---------------------------------------------------------------------------
# Node 6 — Execution Engine (Deterministic)
# ---------------------------------------------------------------------------

def execution_engine_node(state: State) -> Dict[str, Any]:
    """Deterministic execution of human-approved actions updating operational records.

    Runs after the human_review interrupt is resumed with human_approved=True.
    Writes allocation records to SQLite and updates employees' current_project.

    Args:
        state: Workflow state with human_approved=True and recommendations

    Returns:
        Updated state with execution_status
    """
    logger.info("🔄 EXECUTION ENGINE: Processing approved allocations")

    if not state.get("human_approved"):
        logger.info("  No human approval — skipping execution")
        return {"execution_status": "skipped_no_approval"}

    recommendations = state.get("recommendations", {})
    reallocations = recommendations.get("reallocations", [])

    if not reallocations:
        logger.info("  No reallocations to execute")
        return {"execution_status": "completed_no_actions"}

    db = SQLiteManager()
    executed_count = 0

    for reallocation in reallocations:
        try:
            # Use .get() with safe defaults — LLM sometimes omits role/match_score
            emp_id = reallocation.get("employee_id") or reallocation.get("id", "unknown")
            project_id = reallocation.get("target_project_id") or reallocation.get("project_id", "unknown")
            role = reallocation.get("role", "Unspecified")
            match_score = float(reallocation.get("match_score", 0.0))

            allocation_id = db.update_allocation(
                recommendation_id=recommendations.get("recommendation_id", "unknown"),
                employee_id=emp_id,
                target_project_id=project_id,
                role=role,
                match_score=match_score,
                approved_by=state.get("human_feedback", "system"),
            )
            logger.info(
                f"  ✓ Executed: {emp_id} → {project_id} as '{role}' "
                f"(score={match_score:.2f}, allocation_id={allocation_id})"
            )
            executed_count += 1

        except Exception as e:
            logger.error(
                f"  ✗ Failed reallocation for {reallocation.get('employee_id')}: {e}"
            )
            continue

    logger.info(f"✓ Execution complete: {executed_count}/{len(reallocations)} reallocations")

    return {"execution_status": f"executed_{executed_count}_of_{len(reallocations)}"}
