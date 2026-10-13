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

from sqlalchemy import select, or_
from src.database.database import AsyncSessionLocal
from src.database.models import Employee, ProjectDemand, BenchStatusEnum, DemandStatusEnum, ForecastRun, Allocation, AllocationStatusEnum

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


from sqlalchemy.orm import selectinload

# ===========================================================================
# Node 1 — Data Extractor (Deterministic)
# ===========================================================================

async def data_extractor_node(state: State) -> Dict[str, Any]:
    """Deterministic extraction of bench talent and open project demands using AsyncSession.
    """
    _log_state_entry("data_extractor_node", state)

    horizon_days: int = state.get("horizon_days", 90)
    min_win_prob: float = state.get("min_win_probability", 0.75)

    try:
        async with AsyncSessionLocal() as db:
            # Query active employees on bench or upcoming_bench with their costs
            emp_stmt = select(Employee).options(
                selectinload(Employee.employee_costs)
            ).where(
                Employee.is_active == True,
                Employee.bench_status.in_([BenchStatusEnum.bench, BenchStatusEnum.upcoming_bench])
            )
            emp_res = await db.execute(emp_stmt)
            employees_db = emp_res.scalars().all()

            employees = []
            for emp in employees_db:
                # Resolve seniority
                sen = "Mid"
                if emp.experience_level:
                    sen = emp.experience_level.value.capitalize()
                elif getattr(emp, "seniority", None):
                    sen = str(emp.seniority).capitalize()

                # Cascading hourly cost lookup
                cost_val = 0.0
                if emp.employee_costs:
                    costs = sorted(
                        emp.employee_costs,
                        key=lambda c: getattr(c, "effective_date", None) or date.min,
                        reverse=True
                    )
                    cost_val = float(costs[0].internal_hourly_cost)
                elif getattr(emp, "hourly_cost_rate", None):
                    cost_val = float(emp.hourly_cost_rate)

                # Never allow 0 cost for an active employee
                if cost_val <= 0:
                    sen_map = {
                        "junior": 40.0,
                        "mid": 65.0,
                        "senior": 95.0,
                        "lead": 125.0,
                        "principal": 160.0,
                    }
                    cost_val = sen_map.get(sen.lower(), 65.0)

                emp_dict = {
                    "id": str(emp.id),
                    "name": f"{emp.first_name} {emp.last_name}".strip(),
                    "full_name": f"{emp.first_name} {emp.last_name}".strip(),
                    "skills": getattr(emp, "skills") or [], # Legacy fallback or use empty
                    "profile_text": emp.profile_text,
                    "bench_status": emp.bench_status.value if hasattr(emp.bench_status, "value") else str(emp.bench_status),
                    "seniority": sen,
                    "hourly_cost": cost_val,
                    "hourly_cost_rate": cost_val,
                    "daily_cost": cost_val * 8.0,
                }
                employees.append(emp_dict)

            # Query open demands with their projects
            dem_stmt = select(ProjectDemand).options(
                selectinload(ProjectDemand.project)
            ).where(
                ProjectDemand.status == DemandStatusEnum.open,
                or_(ProjectDemand.win_probability >= min_win_prob, ProjectDemand.win_probability == None)
            )
            dem_res = await db.execute(dem_stmt)
            demands_db = dem_res.scalars().all()

            demands = []
            for d in demands_db:
                proj = d.project
                # required_skills is a JSONB column — may be a dict, a list, or None.
                # Normalise to list so downstream code can safely call join() on it.
                raw_skills = d.required_skills or []
                if isinstance(raw_skills, dict):
                    # Support both {"skills": [...]} and {"Python": true, ...} shapes
                    raw_skills = raw_skills.get("skills", list(raw_skills.keys()))
                if not isinstance(raw_skills, list):
                    raw_skills = []

                dem_dict = {
                    "id": str(d.id),
                    "project_id": str(d.project_id),
                    "role": d.role,
                    "description": d.description,
                    "required_skills": raw_skills
                }
                
                # Attach financial data, falling back to project if not explicitly set on demand
                dem_dict["target_bill_rate"] = float(d.target_bill_rate) if d.target_bill_rate else 0.0
                dem_dict["target_margin"] = float(d.target_margin) if d.target_margin else (float(proj.target_margin) if proj and proj.target_margin else 25.0)
                dem_dict["win_probability"] = float(d.win_probability) if d.win_probability else (float(proj.probability)/100.0 if proj else 0.5)
                
                demands.append(dem_dict)

            logger.info(
                f"data_extractor_node: fetched {len(employees)} employees "
                f"(horizon={horizon_days}d), {len(demands)} demands (p>={min_win_prob:.0%})"
            )
    except Exception as exc:
        logger.exception("data_extractor_node: database query failed")
        raise

    updates = {
        "bench_employees": employees,
        "open_demands": demands,
        "revision_count": state.get("revision_count", 0),
        "rejection_feedback": state.get("rejection_feedback", None),
    }
    _log_state_exit("data_extractor_node", updates)
    return updates


# ===========================================================================
# Node 2 — Skill Matcher (LLM + True RAG)
# ===========================================================================

def skill_matcher_node(state: State) -> Dict[str, Any]:
    """RAG-augmented semantic matching of employee profiles against demands."""
    _log_state_entry("skill_matcher_node", state)

    employees = state.get("bench_employees", [])
    demands = state.get("open_demands", [])

    if not employees or not demands:
        logger.warning("skill_matcher_node: no employees or demands — skipping matching")
        updates = {"skill_matches": []}
        _log_state_exit("skill_matcher_node", updates)
        return updates

    llm = LLMFactory.get_chat_model()
    vector_mgr = VectorStoreManager()
    all_matches: List[Dict[str, Any]] = []

    bench_ids = [str(emp["id"]) for emp in employees if emp.get("id")]
    where_filter = {"employee_id": {"$in": bench_ids}} if bench_ids else None

    for demand in demands:
        rag_query = f"{demand.get('role', '')} {demand.get('description') or ''}"
        rag_results = vector_mgr.similarity_search(rag_query, k=3, where=where_filter)
        if rag_results:
            logger.debug(
                f"skill_matcher: '{demand.get('role')}' RAG top match: "
                f"{rag_results[0].get('name')} "
                f"(score={rag_results[0].get('similarity_score', 0):.2f})"
            )

        rag_passages_by_emp: Dict[str, List[str]] = {}
        candidate_ids = set()
        for r in rag_results:
            emp_id = r.get("id", "")
            excerpt = r.get("document_excerpt", "")
            if emp_id:
                candidate_ids.add(emp_id)
            if emp_id and excerpt:
                rag_passages_by_emp.setdefault(emp_id, []).append(excerpt)

        candidates = [emp for emp in employees if emp.get("id") in candidate_ids]

        for employee in candidates:
            emp_rag = rag_passages_by_emp.get(employee.get("id"), [])
            if employee.get("profile_text") and employee.get("profile_text") not in emp_rag:
                emp_rag = [employee.get("profile_text")] + emp_rag

            sys_msg, human_msg = MatchPromptBuilder.build_matching_prompt(
                employee_profile=(
                    employee.get("profile_text")
                    or f"{employee.get('name')}: {', '.join(employee.get('skills') or [])}"
                ),
                job_description=(
                    demand.get("description")
                    or f"{demand.get('role')} requiring: {', '.join(demand.get('required_skills') or [])}"
                ),
                required_skills=demand.get("required_skills") or [],
                employee_skills=employee.get("skills") or [],
                rag_passages=emp_rag[:3],
            )

            response = llm.invoke([sys_msg, human_msg])
            match_json = LLMFactory.parse_json_response(response.content)

            match = MatchJustification(
                employee_id=str(employee.get("id")),
                demand_id=str(demand.get("id")),
                match_score=float(match_json.get("match_score", 0.0)),
                reasoning=match_json.get("reasoning") or "No reasoning provided",
                missing_skills=match_json.get("missing_skills", []),
                training_recommendation=match_json.get("training_recommendation"),
                rag_passages=emp_rag,
            )

            match_dict = match.model_dump()
            match_dict["target_project_id"] = demand.get("project_id")
            all_matches.append(match_dict)

    all_matches.sort(key=lambda x: x["match_score"], reverse=True)
    updates = {"skill_matches": all_matches}
    _log_state_exit("skill_matcher_node", updates)
    return updates


# ===========================================================================
# Node 3 — Allocation Decider (Per-Role Financial Optimization)
# ===========================================================================

def allocation_decider_node(state: State) -> Dict[str, Any]:
    """Per-role financial optimization using the RAGPipeline financial pre-filter."""
    _log_state_entry("allocation_decider_node", state)

    demands = state.get("open_demands", [])
    if not demands:
        logger.warning("allocation_decider_node: no demands — skipping")
        updates = {"decisions": []}
        _log_state_exit("allocation_decider_node", updates)
        return updates

    rag = RAGPipeline()
    llm = LLMFactory.get_chat_model()
    decisions: List[Dict[str, Any]] = []
    allocated_employee_ids: set = set()

    for demand in demands:
        role_title = demand.get("role", "Unknown Role")
        role_description = demand.get("description", "") or role_title

        target_bill_rate = float(
            demand.get("target_bill_rate")
            or (demand.get("win_probability", 0.5) * 200)
            or 100.0
        )
        target_margin = float(demand.get("target_margin") or 25.0)

        logger.info(
            f"allocation_decider: evaluating '{role_title}' "
            f"bill_rate=${target_bill_rate:.2f}/h target_margin={target_margin:.1f}%"
        )

        bench_employees = state.get("bench_employees", [])
        bench_ids = [str(emp["id"]) for emp in bench_employees if emp.get("id")]
        emp_by_id = {str(emp["id"]): emp for emp in bench_employees}

        # Exclude employees that have already been allocated to earlier demands in this run
        unallocated_bench_ids = [b for b in bench_ids if b not in allocated_employee_ids]

        if not unallocated_bench_ids and bench_ids:
            logger.info(
                f"allocation_decider: all bench employees already allocated — recommending hire for '{role_title}'"
            )
            decisions.append({
                "action_type": "hire",
                "employee_id": None,
                "projected_margin": 0.0,
                "justification": f"All qualified bench personnel are already allocated to priority projects. External hiring recommended for '{role_title}'.",
                "upskilling_path": None,
                "role_id": demand.get("id", ""),
                "role_title": role_title,
                "target_project_id": demand.get("project_id", ""),
                "target_bill_rate": target_bill_rate,
                "target_margin": target_margin,
                "candidate_count": 0,
            })
            continue

        additional_where = {"employee_id": {"$in": unallocated_bench_ids}} if unallocated_bench_ids else None
        candidates = rag.retrieve_candidates(
            role_description=role_description,
            target_bill_rate=target_bill_rate,
            k=5,
            additional_where=additional_where,
        )

        # Filter candidates strictly to available bench employees not yet allocated
        if emp_by_id:
            candidates = [
                c for c in candidates
                if str(c.get("employee_id") or c.get("id")) in emp_by_id
                and str(c.get("employee_id") or c.get("id")) not in allocated_employee_ids
            ]
        else:
            candidates = [
                c for c in candidates
                if str(c.get("employee_id") or c.get("id")) not in allocated_employee_ids
            ]

        # If ChromaDB returned 0 bench candidates, fallback to evaluating unallocated bench employees directly
        if not candidates and unallocated_bench_ids:
            logger.info(
                f"RAG query returned no unallocated bench candidates for '{role_title}'. "
                f"Evaluating unallocated bench employees directly."
            )
            for b_id in unallocated_bench_ids[:5]:
                emp = emp_by_id[b_id]
                cost = float(emp.get("hourly_cost") or emp.get("hourly_cost_rate") or 0.0)
                sen = emp.get("seniority") or emp.get("experience_level") or "Mid"
                if cost <= 0:
                    sen_map = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                    cost = sen_map.get(str(sen).lower(), 65.0)
                margin = round(((target_bill_rate - cost) / target_bill_rate) * 100, 2) if target_bill_rate > 0 else 0.0
                candidates.append({
                    "employee_id": b_id,
                    "full_name": emp.get("name") or emp.get("full_name") or f"Employee {b_id[:8]}",
                    "primary_role": emp.get("job_title") or (emp.get("skills")[0] if emp.get("skills") else "Engineer"),
                    "seniority": sen,
                    "hourly_cost_rate": cost,
                    "bench_start_date": emp.get("available_from") or "",
                    "similarity_score": 0.60,
                    "projected_margin_pct": margin,
                    "document_excerpt": emp.get("profile_text") or f"Skills: {', '.join(emp.get('skills', []))}",
                    "chunk_index": 0,
                })

        logger.info(
            f"  Viable bench candidates for '{role_title}': {len(candidates)} candidate(s)"
        )

        # ENRICH CANDIDATES WITH ACCURATE COST AND SENIORITY FROM DB / EXTRACTOR
        for cand in candidates:
            cand_id = str(cand.get("employee_id") or cand.get("id"))
            if cand_id in emp_by_id:
                emp = emp_by_id[cand_id]
                cand["full_name"] = emp.get("name") or emp.get("full_name") or cand.get("full_name", "")
                cand["seniority"] = emp.get("seniority") or cand.get("seniority") or "Senior"
                if not cand.get("primary_role") and emp.get("skills"):
                    cand["primary_role"] = emp["skills"][0]

                cost = float(emp.get("hourly_cost") or emp.get("hourly_cost_rate") or cand.get("hourly_cost_rate") or 0.0)
                if cost <= 0:
                    sen_map = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                    cost = sen_map.get(str(cand["seniority"]).lower(), 65.0)
                cand["hourly_cost_rate"] = cost
                if target_bill_rate > 0:
                    cand["projected_margin_pct"] = round(((target_bill_rate - cost) / target_bill_rate) * 100, 2)
            else:
                cost = float(cand.get("hourly_cost_rate") or cand.get("cost") or 0.0)
                if cost <= 0:
                    cost = 65.0
                cand["hourly_cost_rate"] = cost
                if target_bill_rate > 0:
                    cand["projected_margin_pct"] = round(((target_bill_rate - cost) / target_bill_rate) * 100, 2)

        if not candidates:
            decisions.append({
                "action_type": "hire",
                "employee_id": None,
                "projected_margin": 0.0,
                "justification": f"No available bench candidate meets both technical and financial criteria for '{role_title}'. External hire recommended.",
                "upskilling_path": None,
                "role_id": demand.get("id", ""),
                "role_title": role_title,
                "target_project_id": demand.get("project_id", ""),
                "target_bill_rate": target_bill_rate,
                "target_margin": target_margin,
                "candidate_count": 0,
            })
            continue

        sys_msg, human_msg = FinancialMatchPrompts.build_role_evaluation_prompt(
            role_title=role_title,
            role_description=role_description,
            target_bill_rate=target_bill_rate,
            target_margin=target_margin,
            candidates=candidates,
        )

        response = llm.invoke([sys_msg, human_msg])
        raw_json = LLMFactory.parse_json_response(response.content)

        decision = AllocationDecision(**raw_json)

        chosen_emp_id = str(decision.employee_id) if decision.employee_id else None
        chosen_cand = next(
            (c for c in candidates if str(c.get("employee_id") or c.get("id")) == chosen_emp_id),
            None
        )

        # Ensure no duplicate employee allocations across demands
        if decision.action_type in ("allocate", "train") and chosen_emp_id:
            if chosen_emp_id in allocated_employee_ids:
                logger.warning(
                    f"allocation_decider: Employee {chosen_emp_id} was already allocated! Picking next available candidate."
                )
                alt = next(
                    (c for c in candidates if str(c.get("employee_id") or c.get("id")) not in allocated_employee_ids),
                    None
                )
                if alt:
                    chosen_emp_id = str(alt.get("employee_id") or alt.get("id"))
                    decision.employee_id = chosen_emp_id
                    chosen_cand = alt
                    allocated_employee_ids.add(chosen_emp_id)
                else:
                    decision.action_type = "hire"
                    decision.employee_id = None
                    decision.justification += " (Remaining candidates already allocated to other roles. Hiring recommended)."
            else:
                allocated_employee_ids.add(chosen_emp_id)

        if chosen_cand and (decision.projected_margin == 0.0 or decision.projected_margin is None):
            decision.projected_margin = chosen_cand.get("projected_margin_pct", 0.0)

        decision_dict = decision.model_dump()
        decision_dict.update(
            {
                "role_id": demand.get("id", ""),
                "role_title": role_title,
                "target_project_id": demand.get("project_id", ""),
                "target_bill_rate": target_bill_rate,
                "target_margin": target_margin,
                "candidate_count": len(candidates),
                "seniority": chosen_cand.get("seniority") if chosen_cand else None,
                "full_name": chosen_cand.get("full_name") if chosen_cand else None,
                "hourly_cost_rate": chosen_cand.get("hourly_cost_rate") if chosen_cand else None,
            }
        )
        decisions.append(decision_dict)

        logger.info(
            f"  Decision for '{role_title}': "
            f"action={decision.action_type} "
            f"employee={decision.employee_id} "
            f"margin={decision.projected_margin:.1f}%"
        )

    updates = {"decisions": decisions}
    _log_state_exit("allocation_decider_node", updates)
    return updates


# ===========================================================================
# Node 4 — Forecast Planner (LLM Aggregate)
# ===========================================================================

def _build_plan_from_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministically compile a workforce plan directly from per-role decisions."""
    reallocations = []
    trainings = []
    hirings = []

    for d in decisions:
        act = d.get("action_type") or "allocate"
        if act == "allocate" and d.get("employee_id"):
            reallocations.append({
                "employee_id": str(d.get("employee_id")),
                "target_project_id": str(d.get("target_project_id") or ""),
                "role": d.get("role_title") or "Engineer",
                "match_score": float(d.get("match_score") or 0.85),
                "projected_margin": float(d.get("projected_margin") or 25.0),
                "action_type": "allocate",
                "justification": d.get("justification") or f"Direct allocation for {d.get('role_title', 'role')}.",
            })
        elif act in ("train", "training"):
            trainings.append({
                "employee_id": str(d.get("employee_id") or ""),
                "target_skills": d.get("missing_skills") or ["Required Skills"],
                "duration_weeks": 4,
                "projected_margin": float(d.get("projected_margin") or 20.0),
                "upskilling_path": d.get("upskilling_path") or "Structured training curriculum",
            })
        elif act in ("hire", "hiring"):
            hirings.append({
                "role": d.get("role_title") or "Engineer",
                "required_skills": d.get("missing_skills") or [],
                "headcount": 1,
                "justification": d.get("justification") or f"External hire recommended for {d.get('role_title', 'role')}.",
            })

    total = len(reallocations) + len(trainings) + len(hirings)
    confidence = (
        round(
            (len(reallocations) * 0.9 + len(trainings) * 0.75 + len(hirings) * 0.6)
            / max(total, 1),
            2,
        )
        if total > 0
        else 0.5
    )

    return {
        "recommendation_id": uuid.uuid4().hex,
        "reallocations": reallocations,
        "trainings": trainings,
        "hirings": hirings,
        "confidence_score": confidence,
        "reasoning": (
            f"Workforce optimization plan formulated across {len(decisions)} demands: "
            f"{len(reallocations)} direct allocation(s), {len(trainings)} upskilling program(s), "
            f"and {len(hirings)} external hire(s) based on margin requirements and skill match."
        ),
        "revision_note": None,
        "created_at": datetime.utcnow().isoformat(),
    }


def forecast_planner_node(state: State) -> Dict[str, Any]:
    """Aggregate multi-role planning: synthesises reallocations, trainings, and hirings.

    Combines skill_matches with aggregate bench constraints to generate a
    holistic workforce plan. Uses LLM with FinancialMatchPrompts for
    financially-aware reasoning across all open roles.

    Args:
        state: Workflow state with skill_matches populated.

    Returns:
        State delta: recommendations dict.
    """
    _log_state_entry("forecast_planner_node", state)

    matches = state.get("skill_matches", [])
    employees = state.get("bench_employees", [])
    demands = state.get("open_demands", [])
    decisions = state.get("decisions", [])

    if not matches and not decisions:
        logger.warning("forecast_planner_node: no skill matches or decisions — returning empty plan")
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

    sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
        matches=matches,
        employees_count=len(employees),
        open_roles_count=len(demands),
    )

    plan_json = {}
    try:
        response = llm.invoke([sys_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)
    except Exception as exc:
        logger.warning(
            f"forecast_planner_node: LLM planning call failed or produced unparseable JSON ({exc}). "
            f"Synthesizing plan directly from allocation decisions."
        )

    # Fallback to deterministic synthesis if LLM returned non-dict or empty plan
    if not isinstance(plan_json, dict) or (
        not plan_json.get("reallocations")
        and not plan_json.get("trainings")
        and not plan_json.get("hirings")
    ):
        logger.info(
            f"forecast_planner_node: synthesizing plan from {len(decisions)} state decisions"
        )
        plan_json = _build_plan_from_decisions(decisions)

    plan_json.setdefault("recommendation_id", uuid.uuid4().hex)
    plan_json.setdefault("revision_note", None)
    plan_json.setdefault("created_at", datetime.utcnow().isoformat())

    # Deduplicate reallocations so no employee is assigned twice
    seen_realloc_emps = set()
    deduped_reallocs = []
    for r in plan_json.get("reallocations", []):
        r_emp = r.get("employee_id") or r.get("id")
        if r_emp and r_emp in seen_realloc_emps:
            continue
        if r_emp:
            seen_realloc_emps.add(r_emp)
        deduped_reallocs.append(r)
    plan_json["reallocations"] = deduped_reallocs

    logger.info(
        f"forecast_planner_node: plan generated — "
        f"{len(plan_json.get('reallocations', []))} reallocations, "
        f"{len(plan_json.get('trainings', []))} trainings, "
        f"{len(plan_json.get('hirings', []))} hirings "
        f"(confidence={plan_json.get('confidence_score', 0):.2f})"
    )

    updates = {"recommendations": plan_json}

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
    employees = state.get("bench_employees", [])
    demands = state.get("open_demands", [])
    revision_count = state.get("revision_count", 0)

    logger.info(
        f"revision_planner_node: generating revision #{revision_count} — "
        f"feedback='{str(feedback)[:100]}'"
    )

    llm = LLMFactory.get_chat_model()

    sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
        matches=matches,
        employees_count=len(employees),
        open_roles_count=len(demands),
        rejection_feedback=str(feedback),
        previous_plan=previous_plan,
    )

    plan_json = {}
    try:
        response = llm.invoke([sys_msg, human_msg])
        plan_json = LLMFactory.parse_json_response(response.content)
    except Exception as exc:
        logger.warning(
            f"revision_planner_node: LLM planning call failed or produced unparseable JSON ({exc}). "
            f"Synthesizing plan from decisions."
        )

    if not isinstance(plan_json, dict) or (
        not plan_json.get("reallocations")
        and not plan_json.get("trainings")
        and not plan_json.get("hirings")
    ):
        decisions = state.get("decisions", [])
        plan_json = _build_plan_from_decisions(decisions)

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

    _log_state_exit("revision_planner_node", updates)
    return updates


# ===========================================================================
# Node 7 — Execution Engine (Deterministic write to DB)
# ===========================================================================

async def execution_engine_node(state: State) -> Dict[str, Any]:
    """Deterministic execution of human-approved allocations using AsyncSession."""
    _log_state_entry("execution_engine_node", state)

    if not state.get("human_approved"):
        logger.info("execution_engine_node: no human approval — skipping")
        updates = {"execution_status": "skipped_no_approval"}
        _log_state_exit("execution_engine_node", updates)
        return updates

    recommendations = state.get("recommendations", {})
    reallocations = recommendations.get("reallocations", [])
    forecast_run_id = state.get("forecast_run_id")

    if not reallocations:
        logger.info("execution_engine_node: no reallocations to execute")
        updates = {"execution_status": "completed_no_actions"}
        _log_state_exit("execution_engine_node", updates)
        return updates

    executed_count = 0

    try:
        async with AsyncSessionLocal() as db:
            for reallocation in reallocations:
                # Resolve employee ID
                emp_id_str = reallocation.get("employee_id") or reallocation.get("id")
                if not emp_id_str:
                    continue
                emp_id = uuid.UUID(emp_id_str)
                
                # Resolve project and role info
                project_id_str = reallocation.get("target_project_id") or reallocation.get("project_id")
                role = reallocation.get("role", "Unspecified")
                role_id_str = reallocation.get("role_id") or reallocation.get("demand_id")
                match_score = float(reallocation.get("match_score", 0.0))

                demand_uuid = None
                demand = None
                
                # First try to find by role_id / demand_id
                if role_id_str:
                    dem_stmt = select(ProjectDemand).where(
                        ProjectDemand.id == uuid.UUID(role_id_str),
                        ProjectDemand.status == DemandStatusEnum.open
                    )
                    dem_res = await db.execute(dem_stmt)
                    demand = dem_res.scalars().first()
                
                # Fallback to project_id and role
                if not demand and project_id_str:
                    dem_stmt = select(ProjectDemand).where(
                        ProjectDemand.project_id == uuid.UUID(project_id_str),
                        ProjectDemand.role == role,
                        ProjectDemand.status == DemandStatusEnum.open
                    )
                    dem_res = await db.execute(dem_stmt)
                    demand = dem_res.scalars().first()
                    
                if demand:
                    demand_uuid = demand.id

                    # F5: Guard the floor at 0 to prevent negative headcount on retry.
                    if demand.headcount_needed > 0:
                        demand.headcount_needed -= 1
                    if demand.headcount_needed <= 0:
                        demand.status = DemandStatusEnum.filled

                # forecast_run_id is stored as str in state (MemorySaver serialisation).
                # Cast back to UUID for the FK column.
                forecast_run_uuid = uuid.UUID(forecast_run_id) if forecast_run_id else None

                # Create allocation record
                allocation = Allocation(
                    employee_id=emp_id,
                    demand_id=demand_uuid,
                    role_on_project=role,
                    allocation_pct=100.0,
                    status=AllocationStatusEnum.active,
                    ai_match_score=match_score,
                    forecast_run_id=forecast_run_uuid,
                )
                db.add(allocation)

                # Update employee bench status
                emp_stmt = select(Employee).where(Employee.id == emp_id)
                emp_res = await db.execute(emp_stmt)
                employee = emp_res.scalars().first()
                if employee:
                    employee.bench_status = BenchStatusEnum.on_project
                    employee.bench_start_date = None

                logger.info(f"  execution_engine: allocated {emp_id} → demand {demand_uuid} role='{role}' score={match_score:.2f}")
                executed_count += 1

            # Update forecast run status
            if forecast_run_id:
                run_uuid = uuid.UUID(forecast_run_id)
                run_stmt = select(ForecastRun).where(ForecastRun.id == run_uuid)
                run_res = await db.execute(run_stmt)
                run_rec = run_res.scalars().first()
                if run_rec:
                    run_rec.status = "completed"

            await db.commit()
    except Exception as exc:
        logger.exception("execution_engine_node: failed reallocation batch")
        raise

    status_msg = f"executed_{executed_count}_of_{len(reallocations)}"
    logger.info(f"execution_engine_node: {status_msg}")

    updates = {"execution_status": status_msg}
    _log_state_exit("execution_engine_node", updates)
    return updates
