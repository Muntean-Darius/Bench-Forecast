"""LangGraph node execution logic for the bench forecast workflow.

Each node represents a discrete processing step in the state machine.
STRICT RESPONSIBILITY: This module contains ONLY node execution functions
and the conditional router. No LLM prompt templates, no DB schema definitions.

Nodes:
  1. data_extractor_node     — Fetch bench employees + open demands
  2. skill_matcher_node      — RAG-augmented semantic matching (ChromaDB → LLM)
  3. allocation_decider_node — Per-role financial optimization (FinancialMatchPrompts → LLM)
  4. forecast_planner_node   — Aggregate multi-role plan generation
  5. human_review_node       — HITL gate: reads resume payload
  6. revision_planner_node   — LLM revision incorporating manager feedback
  7. execution_engine_node   — Writes approved allocations to the database

OBSERVABILITY: Every node logs the full state payload at ENTRY and EXIT
using logging.info so every transition is fully traceable in production.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from src.agents.state import MAX_REVISIONS, State
from src.database.vector_store import VectorStoreManager
from src.database.rag import RAGPipeline
from src.core.llm import (
    AllocationDecision,
    FinancialMatchPrompts,
    LLMFactory,
    MatchJustification,
    MatchPromptBuilder,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observability helpers
# ---------------------------------------------------------------------------

def _log_state_entry(node_name: str, state: State) -> None:
    """Emit a structured ENTRY log for a node including the full state payload.

    Lists with more than ~200 chars of content are summarised to avoid flooding
    logs, while scalar fields are printed verbatim for full traceability.
    """
    summary = {
        k: (f"<list: {len(v)} items>" if isinstance(v, list) and len(str(v)) > 200 else v)
        for k, v in state.items()
    }
    logger.info(
        f"\n{'='*70}\n"
        f">>> ENTERING NODE: {node_name}\n"
        f"    STATE PAYLOAD:\n{json.dumps(summary, default=str, indent=4)}\n"
        f"{'='*70}"
    )


def _log_state_exit(node_name: str, updates: Dict[str, Any]) -> None:
    """Emit a structured EXIT log for a node including the state delta."""
    summary = {
        k: (f"<list: {len(v)} items>" if isinstance(v, list) and len(str(v)) > 200 else v)
        for k, v in updates.items()
    }
    logger.info(
        f"\n{'='*70}\n"
        f"<<< EXITING NODE:  {node_name}\n"
        f"    STATE DELTA:\n{json.dumps(summary, default=str, indent=4)}\n"
        f"{'='*70}"
    )


def _get_db():
    """Lazy import of PostgresManager to avoid circular imports at module load time."""
    from src.database.postgres_db import PostgresManager
    return PostgresManager()


# ===========================================================================
# Node 1 — Data Extractor (Deterministic)
# ===========================================================================

def data_extractor_node(state: State) -> Dict[str, Any]:
    """Deterministic extraction of bench talent and open project demands.

    Queries:
    - get_bench_forecast(horizon_days): employees finishing within N days.
    - get_open_demands(min_win_probability): high-confidence pipeline roles.

    Args:
        state: Current workflow state (reads horizon_days, min_win_probability).

    Returns:
        State delta: employees, demands, initialised revision fields.
    """
    _log_state_entry("data_extractor_node", state)

    db = _get_db()
    horizon_days: int = state.get("horizon_days", 90)
    min_win_prob: float = state.get("min_win_probability", 0.75)

    try:
        employees = db.get_bench_forecast(horizon_days=horizon_days)
        demands = db.get_open_demands(min_win_probability=min_win_prob)
        logger.info(
            f"data_extractor_node: fetched {len(employees)} employees "
            f"(horizon={horizon_days}d), {len(demands)} demands (p>={min_win_prob:.0%})"
        )
    except Exception:
        logger.exception("data_extractor_node: database query failed")
        employees, demands = [], []

    updates = {
        "employees": employees,
        "demands": demands,
        "revision_count": state.get("revision_count", 0),
        "rejection_feedback": state.get("rejection_feedback", None),
    }
    _log_state_exit("data_extractor_node", updates)
    return updates


# ===========================================================================
# Node 2 — Skill Matcher (LLM + True RAG)
# ===========================================================================

def skill_matcher_node(state: State) -> Dict[str, Any]:
    """RAG-augmented semantic matching of employee profiles against demands.

    TRUE RAG implementation:
    1. Query ChromaDB with the demand's narrative description.
    2. Retrieve top-k semantically similar employee profile passages.
    3. Inject those passages into the LLM prompt as grounding evidence.
    4. The LLM cites specific passages in its reasoning.

    Args:
        state: Workflow state with employees and demands populated.

    Returns:
        State delta: skill_matches (each match includes rag_passages).
    """
    _log_state_entry("skill_matcher_node", state)

    employees = state.get("employees", [])
    demands = state.get("demands", [])

    if not employees or not demands:
        logger.warning("skill_matcher_node: no employees or demands — skipping matching")
        updates = {"skill_matches": []}
        _log_state_exit("skill_matcher_node", updates)
        return updates

    llm = LLMFactory.get_chat_model()
    vector_mgr = VectorStoreManager()
    all_matches: List[Dict[str, Any]] = []

    for demand in demands:
        rag_query = f"{demand.role} {demand.description or ''}"
        rag_results: List[Dict[str, Any]] = []

        try:
            rag_results = vector_mgr.similarity_search(rag_query, k=3)
            if rag_results:
                logger.debug(
                    f"skill_matcher: '{demand.role}' RAG top match: "
                    f"{rag_results[0].get('name')} "
                    f"(score={rag_results[0].get('similarity_score', 0):.2f})"
                )
        except Exception:
            logger.exception(
                f"skill_matcher_node: ChromaDB query failed for '{demand.role}'"
            )

        # Build passage lookup per employee_id
        rag_passages_by_emp: Dict[str, List[str]] = {}
        candidate_ids = set()
        for r in rag_results:
            emp_id = r.get("id", "")
            excerpt = r.get("document_excerpt", "")
            if emp_id:
                candidate_ids.add(emp_id)
            if emp_id and excerpt:
                rag_passages_by_emp.setdefault(emp_id, []).append(excerpt)

        # Only evaluate candidates retrieved by ChromaDB (O(Demands * K) vs O(Demands * Employees))
        candidates = [emp for emp in employees if emp.id in candidate_ids]
        
        # Fallback to random 3 if vector search fails or returns nothing
        if not candidates:
            candidates = employees[:3]

        for employee in candidates:
            try:
                emp_rag = rag_passages_by_emp.get(employee.id, [])
                # Prepend the employee's own profile text as the primary passage
                if employee.profile_text and employee.profile_text not in emp_rag:
                    emp_rag = [employee.profile_text] + emp_rag

                sys_msg, human_msg = MatchPromptBuilder.build_matching_prompt(
                    employee_profile=(
                        employee.profile_text
                        or f"{employee.name}: {', '.join(employee.skills)}"
                    ),
                    job_description=(
                        demand.description
                        or f"{demand.role} requiring: {', '.join(demand.required_skills)}"
                    ),
                    required_skills=demand.required_skills,
                    employee_skills=employee.skills,
                    rag_passages=emp_rag[:3],
                )

                response = llm.invoke([sys_msg, human_msg])
                match_json = LLMFactory.parse_json_response(response.content)

                match = MatchJustification(
                    employee_id=employee.id,
                    demand_id=demand.id,
                    match_score=float(match_json.get("match_score", 0.0)),
                    reasoning=match_json.get("reasoning") or "No reasoning provided",
                    missing_skills=match_json.get("missing_skills", []),
                    training_recommendation=match_json.get("training_recommendation"),
                    rag_passages=emp_rag,
                )

                match_dict = match.model_dump()
                match_dict["target_project_id"] = demand.project_id
                all_matches.append(match_dict)

                logger.debug(
                    f"  {employee.name} → {demand.role}: "
                    f"score={match.match_score:.2f} passages={len(emp_rag)}"
                )

            except Exception:
                logger.exception(
                    f"skill_matcher_node: error matching "
                    f"employee={getattr(employee, 'name', '?')} "
                    f"demand={demand.role}"
                )

    all_matches.sort(key=lambda x: x["match_score"], reverse=True)
    logger.info(
        f"skill_matcher_node: generated {len(all_matches)} RAG-grounded matches"
    )

    updates = {"skill_matches": all_matches}
    _log_state_exit("skill_matcher_node", updates)
    return updates


# ===========================================================================
# Node 3 — Allocation Decider (Per-Role Financial Optimization)
# ===========================================================================

def allocation_decider_node(state: State) -> Dict[str, Any]:
    """Per-role financial optimization using the RAGPipeline financial pre-filter.

    For each demand:
    1. Call RAGPipeline.retrieve_candidates() with financial pre-filter
       (cost_rate < target_bill_rate) to obtain only profitable candidates.
    2. Build FinancialMatchPrompts.build_role_evaluation_prompt() injecting
       target_bill_rate and target_margin.
    3. Parse and validate the LLM output against AllocationDecision schema.
    4. Store per-role decisions in state for the planning node.

    Args:
        state: Workflow state with demands populated.

    Returns:
        State delta: allocation_decisions (list of per-role decision dicts).
    """
    _log_state_entry("allocation_decider_node", state)

    demands = state.get("demands", [])
    if not demands:
        logger.warning("allocation_decider_node: no demands — skipping")
        updates = {"allocation_decisions": []}
        _log_state_exit("allocation_decider_node", updates)
        return updates

    rag = RAGPipeline()
    llm = LLMFactory.get_chat_model()
    decisions: List[Dict[str, Any]] = []

    for demand in demands:
        role_title = getattr(demand, "role", "Unknown Role")
        role_description = getattr(demand, "description", "") or role_title

        # Resolve billing rate: use target_bill_rate if present (new schema),
        # else derive a heuristic from win_probability
        target_bill_rate = float(
            getattr(demand, "target_bill_rate", None)
            or (getattr(demand, "win_probability", 0.5) * 200)
            or 100.0
        )
        target_margin = float(getattr(demand, "target_margin", None) or 25.0)

        logger.info(
            f"allocation_decider: evaluating '{role_title}' "
            f"bill_rate=${target_bill_rate:.2f}/h target_margin={target_margin:.1f}%"
        )

        try:
            # Step 1: Financially filtered ChromaDB retrieval
            candidates = rag.retrieve_candidates(
                role_description=role_description,
                target_bill_rate=target_bill_rate,
                k=5,
            )
            logger.info(
                f"  RAG candidates (cost < ${target_bill_rate:.2f}/h): "
                f"{len(candidates)} returned"
            )

            # Step 2: LLM evaluation with financial context
            sys_msg, human_msg = FinancialMatchPrompts.build_role_evaluation_prompt(
                role_title=role_title,
                role_description=role_description,
                target_bill_rate=target_bill_rate,
                target_margin=target_margin,
                candidates=candidates,
            )

            response = llm.invoke([sys_msg, human_msg])
            raw_json = LLMFactory.parse_json_response(response.content)

            # Step 3: Validate against AllocationDecision Pydantic schema
            decision = AllocationDecision(**raw_json)

            decision_dict = decision.model_dump()
            decision_dict.update(
                {
                    "role_id": getattr(demand, "id", ""),
                    "role_title": role_title,
                    "target_project_id": getattr(demand, "project_id", ""),
                    "target_bill_rate": target_bill_rate,
                    "target_margin": target_margin,
                    "candidate_count": len(candidates),
                }
            )
            decisions.append(decision_dict)

            logger.info(
                f"  Decision for '{role_title}': "
                f"action={decision.action_type} "
                f"employee={decision.employee_id} "
                f"margin={decision.projected_margin:.1f}%"
            )

        except Exception:
            logger.exception(
                f"allocation_decider_node: failed to evaluate role '{role_title}'"
            )
            # Safe fallback: recommend hiring when evaluation fails
            decisions.append(
                {
                    "action_type": "hire",
                    "employee_id": None,
                    "projected_margin": 0.0,
                    "justification": (
                        f"Evaluation failed for role '{role_title}'. "
                        "External hiring recommended as safe fallback."
                    ),
                    "upskilling_path": None,
                    "role_id": getattr(demand, "id", ""),
                    "role_title": role_title,
                    "target_project_id": getattr(demand, "project_id", ""),
                    "target_bill_rate": target_bill_rate,
                    "target_margin": target_margin,
                    "candidate_count": 0,
                }
            )

    updates = {"allocation_decisions": decisions}
    _log_state_exit("allocation_decider_node", updates)
    return updates


# ===========================================================================
# Node 4 — Forecast Planner (LLM Aggregate)
# ===========================================================================

def forecast_planner_node(state: State) -> Dict[str, Any]:
    """Reasoning engine generating structured allocation, training, and hiring plan.

    Consumes skill_matches to produce a consolidated plan with
    financially-aware reasoning across all open roles.

    Args:
        state: Workflow state with skill_matches populated.

    Returns:
        State delta: recommendations dict.
    """
    _log_state_entry("forecast_planner_node", state)

    matches = state.get("skill_matches", [])
    employees = state.get("employees", [])
    demands = state.get("demands", [])

    if not matches:
        logger.warning("forecast_planner_node: no skill matches — returning empty plan")
        empty_plan = {
            "recommendation_id": uuid.uuid4().hex,
            "reallocations": [],
            "trainings": [],
            "hirings": [],
            "confidence_score": 0.0,
            "reasoning": "No viable matches found — all employees may lack required skills.",
            "revision_note": None,
            "created_at": datetime.utcnow().isoformat(),
        }
        updates = {"recommendations": empty_plan}
        _log_state_exit("forecast_planner_node", updates)
        return updates

    llm = LLMFactory.get_chat_model()

    try:
        sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
        )

        response = llm.invoke([sys_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)

        plan_json.setdefault("recommendation_id", uuid.uuid4().hex)
        plan_json.setdefault("revision_note", None)
        plan_json.setdefault("created_at", datetime.utcnow().isoformat())

        logger.info(
            f"forecast_planner_node: plan generated — "
            f"{len(plan_json.get('reallocations', []))} reallocations, "
            f"{len(plan_json.get('trainings', []))} trainings, "
            f"{len(plan_json.get('hirings', []))} hirings "
            f"(confidence={plan_json.get('confidence_score', 0):.2f})"
        )

        updates = {"recommendations": plan_json}

    except Exception:
        logger.exception("forecast_planner_node: planning LLM call failed")
        updates = {
            "recommendations": {
                "recommendation_id": uuid.uuid4().hex,
                "reallocations": [],
                "trainings": [],
                "hirings": [],
                "confidence_score": 0.0,
                "reasoning": "Planning failed due to LLM error.",
                "revision_note": None,
                "created_at": datetime.utcnow().isoformat(),
            }
        }

    _log_state_exit("forecast_planner_node", updates)
    return updates


# ===========================================================================
# Node 5 — Human Review Gate (HITL checkpoint reader)
# ===========================================================================

def human_review_node(state: State) -> Dict[str, Any]:
    """HITL gate — reads the resume payload after LangGraph interrupt fires.

    This node runs AFTER interrupt_before=["human_review"] pauses the graph.
    The /execute endpoint injects human_approved + rejection_feedback via
    Command(resume={...}), which LangGraph merges into state before this runs.

    Routing is handled by route_after_review() — this node only updates
    revision_count on rejection.

    Args:
        state: State with human_approved and rejection_feedback set by resume.

    Returns:
        State delta: updated revision_count if rejected.
    """
    _log_state_entry("human_review_node", state)

    approved = state.get("human_approved", False)
    feedback = state.get("rejection_feedback")

    if approved:
        logger.info("human_review_node: Manager APPROVED — routing to execution")
        updates: Dict[str, Any] = {}
    elif feedback:
        new_count = state.get("revision_count", 0) + 1
        logger.info(
            f"human_review_node: Manager REJECTED with feedback "
            f"(revision {new_count}/{MAX_REVISIONS}): '{str(feedback)[:80]}'"
        )
        updates = {"revision_count": new_count}
    else:
        logger.info("human_review_node: Manager REJECTED without feedback — terminating")
        updates = {}

    _log_state_exit("human_review_node", updates)
    return updates


def route_after_review(
    state: State,
) -> Literal["execution", "revision_planner", "end"]:
    """Conditional router after human_review_node.

    - Approved                               → execution
    - Rejected + feedback + revisions left   → revision_planner
    - Rejected + no feedback / max revisions → end

    Args:
        state: State with human_approved, rejection_feedback, revision_count.

    Returns:
        Next node name: 'execution', 'revision_planner', or 'end'.
    """
    if state.get("human_approved"):
        logger.info("route_after_review: → execution")
        return "execution"

    feedback = state.get("rejection_feedback")
    revision_count = state.get("revision_count", 0)

    if feedback and revision_count <= MAX_REVISIONS:
        logger.info(
            f"route_after_review: → revision_planner "
            f"(attempt {revision_count}/{MAX_REVISIONS})"
        )
        return "revision_planner"

    logger.info("route_after_review: → end (no feedback or max revisions)")
    return "end"


# ===========================================================================
# Node 6 — Revision Planner (LLM — feedback-driven)
# ===========================================================================

def revision_planner_node(state: State) -> Dict[str, Any]:
    """Generates a revised allocation plan incorporating manager rejection feedback.

    Closes the HITL feedback loop: instead of terminating on rejection, the
    manager's feedback is injected into a new LLM planning call along with
    the previous plan, forcing a meaningfully different strategy.

    Args:
        state: State with rejection_feedback and current recommendations.

    Returns:
        State delta: updated recommendations, human_approved=False, cleared feedback.
    """
    _log_state_entry("revision_planner_node", state)

    feedback = state.get("rejection_feedback") or "No specific feedback provided."
    previous_plan = state.get("recommendations", {})
    matches = state.get("skill_matches", [])
    employees = state.get("employees", [])
    demands = state.get("demands", [])
    revision_count = state.get("revision_count", 0)

    logger.info(
        f"revision_planner_node: generating revision #{revision_count} — "
        f"feedback='{str(feedback)[:100]}'"
    )

    llm = LLMFactory.get_chat_model()

    try:
        sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
            rejection_feedback=str(feedback),
            previous_plan=previous_plan,
        )

        response = llm.invoke([sys_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)

        plan_json["recommendation_id"] = uuid.uuid4().hex
        plan_json["revision_note"] = str(feedback)
        plan_json["created_at"] = datetime.utcnow().isoformat()

        logger.info(
            f"revision_planner_node: revised plan ready — "
            f"{len(plan_json.get('reallocations', []))} reallocations "
            f"(confidence={plan_json.get('confidence_score', 0):.2f})"
        )

        updates = {
            "recommendations": plan_json,
            "human_approved": False,
            "rejection_feedback": None,  # Clear so next cycle starts fresh
        }

    except Exception:
        logger.exception("revision_planner_node: revision LLM call failed")
        updates = {
            "recommendations": {
                **previous_plan,
                "recommendation_id": uuid.uuid4().hex,
                "revision_note": "Revision failed. Original plan retained.",
                "created_at": datetime.utcnow().isoformat(),
            },
            "human_approved": False,
            "rejection_feedback": None,
        }

    _log_state_exit("revision_planner_node", updates)
    return updates


# ===========================================================================
# Node 7 — Execution Engine (Deterministic write to DB)
# ===========================================================================

def execution_engine_node(state: State) -> Dict[str, Any]:
    """Deterministic execution of human-approved allocations.

    Writes approved allocation records to the operational database.
    Bridges to SQLiteManager for backward compatibility with existing data;
    production deployments should use the PostgreSQL models from models.py.

    Args:
        state: Workflow state with human_approved=True and recommendations.

    Returns:
        State delta: execution_status.
    """
    _log_state_entry("execution_engine_node", state)

    if not state.get("human_approved"):
        logger.info("execution_engine_node: no human approval — skipping")
        updates = {"execution_status": "skipped_no_approval"}
        _log_state_exit("execution_engine_node", updates)
        return updates

    recommendations = state.get("recommendations", {})
    reallocations = recommendations.get("reallocations", [])

    if not reallocations:
        logger.info("execution_engine_node: no reallocations to execute")
        updates = {"execution_status": "completed_no_actions"}
        _log_state_exit("execution_engine_node", updates)
        return updates

    db = _get_db()
    executed_count = 0

    for reallocation in reallocations:
        try:
            emp_id = (
                reallocation.get("employee_id")
                or reallocation.get("id", "unknown")
            )
            project_id = (
                reallocation.get("target_project_id")
                or reallocation.get("project_id", "unknown")
            )
            role = reallocation.get("role", "Unspecified")
            match_score = float(reallocation.get("match_score", 0.0))

            allocation_id = db.update_allocation(
                recommendation_id=recommendations.get("recommendation_id", "unknown"),
                employee_id=emp_id,
                target_project_id=project_id,
                role=role,
                match_score=match_score,
                approved_by=state.get("human_feedback") or "system",
            )
            logger.info(
                f"  execution_engine: allocated {emp_id} → {project_id} "
                f"role='{role}' score={match_score:.2f} allocation_id={allocation_id}"
            )
            executed_count += 1

        except Exception:
            logger.exception(
                f"execution_engine_node: failed reallocation for "
                f"{reallocation.get('employee_id', '?')}"
            )

    status = f"executed_{executed_count}_of_{len(reallocations)}"
    logger.info(f"execution_engine_node: {status}")

    updates = {"execution_status": status}
    _log_state_exit("execution_engine_node", updates)
    return updates
