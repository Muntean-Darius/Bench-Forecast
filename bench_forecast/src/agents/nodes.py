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
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage
from sqlalchemy import select, or_

# Number of concurrent LLM calls fired within a node. Requires the Ollama
# server to allow parallelism (OLLAMA_NUM_PARALLEL) to help materially, but it
# also overlaps embeddings + per-call latency.
_LLM_PARALLELISM = int(os.getenv("FORECAST_LLM_PARALLELISM", "4"))
from src.database.database import AsyncSessionLocal
from src.database.models import Employee, Project, ProjectDemand, BenchStatusEnum, DemandStatusEnum, ForecastRun, Allocation, AllocationStatusEnum

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
# Skill-matching helpers (deterministic, explainable scoring)
# ===========================================================================

def _normalize_skill_list(raw: Any) -> List[str]:
    """Coerce a skills value (comma string / list / dict / None) into a list."""
    if not raw:
        return []
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, dict):
        # Support {"skills": [...]} and {"Python": true, "SQL": true} shapes
        if "skills" in raw and isinstance(raw["skills"], (list, tuple, set, str)):
            return _normalize_skill_list(raw["skills"])
        return [str(k).strip() for k in raw.keys() if str(k).strip()]
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        for item in raw:
            out.extend(_normalize_skill_list(item))
        return out
    return [str(raw).strip()]


# Curated skill vocabulary used to recover skills from free-text CVs when the
# structured ``employees.skills`` column is empty (common in seeded data).
SKILL_VOCABULARY: List[str] = [
    # Languages
    "Python", "Java", "JavaScript", "TypeScript", "C", "C++", "C#", "Go", "Rust",
    "Ruby", "PHP", "Scala", "Kotlin", "Swift", "Perl", "R", "MATLAB", "SQL",
    "Bash", "Shell", "PowerShell", "VBA", "Dart", "Elixir", "Haskell", "Lua",
    # Web / frontend
    "React", "Angular", "Vue", "Next.js", "Node.js", "Express", "Django",
    "Flask", "FastAPI", "Spring", "Spring Boot", "Rails", "Laravel", ".NET",
    "ASP.NET", "HTML", "CSS", "SASS", "Tailwind", "Redux", "GraphQL", "REST",
    "RESTful", "jQuery", "Bootstrap",
    # Data / ML
    "Machine Learning", "Deep Learning", "NLP", "TensorFlow", "PyTorch",
    "scikit-learn", "Pandas", "NumPy", "Spark", "Hadoop", "Kafka", "Airflow",
    "dbt", "ETL", "Data Warehouse", "Data Science", "Statistics", "Tableau",
    "Power BI", "Looker", "Excel", "Big Data", "Computer Vision", "LLM",
    # Databases
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Oracle",
    "SQL Server", "SQLite", "Cassandra", "DynamoDB", "Snowflake", "BigQuery",
    "Neo4j",
    # Cloud / DevOps
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Ansible",
    "Jenkins", "CI/CD", "Git", "GitHub", "GitLab", "Linux", "Unix", "Nginx",
    "Prometheus", "Grafana", "Serverless", "Microservices", "DevOps",
    # Testing / QA
    "Selenium", "Cypress", "Playwright", "JUnit", "PyTest", "Jest", "Mocha",
    "TestNG", "Postman", "JMeter", "QA", "Test Automation", "Manual Testing",
    "Cucumber", "BDD", "TDD",
    # Security / Networking
    "Cybersecurity", "Penetration Testing", "Firewall", "TCP/IP", "Networking",
    "Active Directory", "SIEM", "SOC", "Encryption", "OAuth", "SSO",
    # ERP / enterprise
    "SAP", "Salesforce", "Workday", "ServiceNow", "SharePoint", "Jira",
    "Confluence", "PeopleSoft", "Oracle ERP",
    # Business / soft skills
    "Agile", "Scrum", "Kanban", "Project Management", "Product Management",
    "Business Analysis", "Stakeholder Management", "Leadership", "Communication",
    "Customer Service", "Accounting", "Finance", "Marketing", "Sales",
    "Recruiting", "HR", "Payroll", "Compliance", "HIPAA", "Data Analysis",
]

# Pre-compiled lookups for case-insensitive, word-boundary skill detection.
_SKILL_LOOKUP = {s.lower(): s for s in SKILL_VOCABULARY}


def _skill_mentioned(haystack: str, skill: str) -> bool:
    """Return True if *skill* appears in *haystack* as a whole word/token.

    Word boundaries prevent false positives such as ``R`` matching
    "develope**r**" or ``Go`` matching "Djan**go**", while still allowing
    ``Java`` to match "Java 8" and ``C`` to match "C++"/"C#".
    """
    if not haystack or not skill:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(str(skill).lower()) + r"(?![a-z0-9])"
    return re.search(pattern, str(haystack).lower()) is not None


def _extract_skills_from_text(text: Optional[str]) -> List[str]:
    """Recover known skills mentioned in free-text CV content.

    Matches whole words only (so "Java" does not match "JavaScript") and
    returns canonical vocabulary casing, ordered by first appearance.
    """
    if not text:
        return []
    lowered = str(text).lower()
    found: List[str] = []
    for key, canonical in _SKILL_LOOKUP.items():
        pattern = r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])"
        if re.search(pattern, lowered):
            found.append(canonical)
    return found


def _employee_skill_profile(employee: Dict[str, Any]) -> List[str]:
    """Combine structured skills, CV-extracted skills and the job title."""
    skills = _normalize_skill_list(employee.get("skills"))
    skills += _extract_skills_from_text(employee.get("profile_text"))
    skills += _extract_skills_from_text(employee.get("job_title"))
    out: List[str] = []
    seen: set = set()
    for s in skills:
        key = s.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _match_skills(employee_skills: List[str], required_skills: List[str]) -> tuple:
    """Return ``(matched, missing, coverage)`` for an employee vs. a role.

    Matching is case-insensitive and also treats substring matches as hits
    (e.g. role requires "Java" and the employee lists "Java 8"). ``coverage``
    is the fraction of required skills matched (``None`` when the role has no
    explicit required skills).
    """
    emp = [s.strip() for s in employee_skills if s and s.strip()]
    required = [r for r in required_skills if r and str(r).strip()]
    matched: List[str] = []
    missing: List[str] = []
    for req in required:
        req_str = str(req).strip()
        if any(
            _skill_mentioned(e, req_str) or _skill_mentioned(req_str, e)
            for e in emp
        ):
            matched.append(req_str)
        else:
            missing.append(req_str)
    coverage = (len(matched) / len(required)) if required else None
    return matched, missing, coverage


def _derive_required_skills(
    role_title: str,
    role_description: str,
    employees: List[Dict[str, Any]],
) -> List[str]:
    """Derive required skills from the role text when a demand has none.

    Scans the free-text role title/description for known skills so scoring and
    gap detection still work for demands without an explicit ``required_skills``.
    """
    return _extract_skills_from_text(f"{role_title or ''} {role_description or ''}")


def _candidate_rank(candidate: Dict[str, Any]) -> tuple:
    """Rank candidates deterministically.

    Priority order:
      1. highest fraction of the role's required skills covered,
      2. strongest blended match score (skill coverage + RAG similarity),
      3. highest projected margin (financial tie-breaker).
    """
    coverage = candidate.get("skill_coverage")
    return (
        float(coverage) if coverage is not None else 0.0,
        float(candidate.get("similarity_score") or 0.0),
        float(candidate.get("projected_margin_pct") or 0.0),
    )


def _build_upskilling_path(missing_skills: List[str], role_title: str) -> str:
    """Generate a concrete training recommendation naming the missing skills."""
    skills = ", ".join(missing_skills)
    primary = missing_skills[0]
    if len(missing_skills) == 1:
        return (
            f"Recommended training: complete a {primary} course to build the "
            f"{primary} proficiency required for the {role_title} role."
        )
    return (
        f"Recommended training: enrol in a structured programme covering {skills} "
        f"(start with a {primary} course) to close the skill gap for the {role_title} role."
    )


def _build_match_justification(
    full_name: Optional[str],
    role_title: str,
    matched: List[str],
    missing: List[str],
    llm_text: Optional[str],
    action: Optional[str] = None,
    candidate_skills: Optional[List[str]] = None,
) -> str:
    """Build a justification that cites the specific skills driving the match.

    The LLM's own text is appended only when it is specific and consistent with
    the final action — generic phrases ("high semantic similarity") and
    contradictory statements are dropped.
    """
    name = full_name or "This candidate"
    parts: List[str] = []
    if matched:
        parts.append(
            f"{name} has {', '.join(matched)}, directly matching the skills required "
            f"for the {role_title} role."
        )
    if missing:
        parts.append(
            f"Identified skill gap: {', '.join(missing)} — a targeted upskilling "
            f"plan is recommended before full allocation."
        )
    if not parts:
        skills = candidate_skills or []
        if skills:
            parts.append(
                f"{name}'s profile highlights {', '.join(skills[:5])}, providing "
                f"relevant experience for the {role_title} role."
            )
        else:
            parts.append(f"{name} was evaluated against the {role_title} role.")

    if llm_text and str(llm_text).strip():
        text = str(llm_text).strip()
        lower = text.lower()
        generic = any(
            g in lower
            for g in ("semantic similarity", "high similarity", "similarity score", "match score")
        )
        # Drop LLM text that recommends an external hire / says no candidate
        # fits when we are actually allocating or training someone.
        hire_language = any(
            h in lower
            for h in ("hire", "hiring", "external", "no internal", "no suitable",
                      "no candidate", "no bench", "not a fit", "cannot be met")
        )
        contradicts = action in ("allocate", "train") and hire_language
        if action == "allocate" and ("train" in lower or "upskill" in lower):
            contradicts = True
        if not generic and not contradicts:
            parts.append(text)

    return " ".join(parts)

# ===========================================================================
# Node 1 — Data Extractor (Deterministic)
# ===========================================================================

async def data_extractor_node(state: State) -> Dict[str, Any]:
    """Deterministic extraction of bench talent and open project demands using AsyncSession.
    """
    _log_state_entry("data_extractor_node", state)

    horizon_days: int = state.get("horizon_days", 90)
    min_win_prob: float = state.get("min_win_probability", 0.75)
    cutoff = date.today() + timedelta(days=horizon_days)

    try:
        async with AsyncSessionLocal() as db:
            # Query active employees on bench or becoming available within the
            # forecast horizon (upcoming_bench whose availability is inside the
            # window). bench_start_date is null for immediately-available talent.
            emp_stmt = select(Employee).options(
                selectinload(Employee.employee_costs)
            ).where(
                Employee.is_active == True,
                Employee.bench_status.in_([BenchStatusEnum.bench, BenchStatusEnum.upcoming_bench]),
                or_(Employee.bench_start_date.is_(None), Employee.bench_start_date <= cutoff),
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
                    "job_title": getattr(emp, "job_title", "") or "",
                    "skills": _employee_skill_profile({
                        "skills": getattr(emp, "skills"),
                        "profile_text": emp.profile_text,
                        "job_title": getattr(emp, "job_title", ""),
                    }),
                    "profile_text": emp.profile_text,
                    "bench_status": emp.bench_status.value if hasattr(emp.bench_status, "value") else str(emp.bench_status),
                    "seniority": sen,
                    "hourly_cost": cost_val,
                    "hourly_cost_rate": cost_val,
                    "daily_cost": cost_val * 8.0,
                }
                employees.append(emp_dict)

            # Query open demands with their projects
            target_project_id = state.get("target_project_id")
            dem_stmt = select(ProjectDemand).options(
                selectinload(ProjectDemand.project)
            ).where(
                ProjectDemand.status == DemandStatusEnum.open,
                or_(ProjectDemand.win_probability >= min_win_prob, ProjectDemand.win_probability == None),
                # Only plan for roles that are actually needed within the
                # forecast horizon window.
                or_(ProjectDemand.needed_by_date.is_(None), ProjectDemand.needed_by_date <= cutoff),
            )
            if target_project_id:
                try:
                    target_uuid = uuid.UUID(target_project_id)
                    dem_stmt = dem_stmt.where(ProjectDemand.project_id == target_uuid)
                except ValueError:
                    pass
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
    bench_by_id = {str(emp["id"]): emp for emp in employees if emp.get("id")}
    where_filter = {"employee_id": {"$in": bench_ids}} if bench_ids else None

    # ---- Phase 1: retrieve candidates + build ONE prompt per demand --------
    tasks: List[Dict[str, Any]] = []
    for demand in demands:
        rag_query = f"{demand.get('role', '')} {demand.get('description') or ''}"
        rag_results = vector_mgr.similarity_search(rag_query, k=5, where=where_filter)
        if rag_results:
            logger.debug(
                f"skill_matcher: '{demand.get('role')}' RAG top match: "
                f"{rag_results[0].get('name')} "
                f"(score={rag_results[0].get('similarity_score', 0):.2f})"
            )

        candidates = []
        for r in rag_results:
            emp_id = r.get("id", "")
            emp = bench_by_id.get(emp_id)
            if not emp:
                continue
            passages = [r.get("document_excerpt", "")]
            if emp.get("profile_text") and emp.get("profile_text") not in passages:
                passages.append(emp.get("profile_text"))
            candidates.append({"employee": emp, "passages": passages})

        if not candidates:
            continue

        sys_msg, human_msg = MatchPromptBuilder.build_batch_matching_prompt(
            candidates=candidates,
            job_description=(
                demand.get("description")
                or f"{demand.get('role')} requiring: {', '.join(demand.get('required_skills') or [])}"
            ),
            required_skills=demand.get("required_skills") or [],
        )
        tasks.append({"demand": demand, "sys": sys_msg, "human": human_msg, "candidates": candidates})

    # ---- Phase 2: one LLM call per demand, run concurrently ---------------
    def _invoke(task: Dict[str, Any]) -> str:
        return llm.invoke([task["sys"], task["human"]]).content

    contents: List[str] = []
    if tasks:
        with ThreadPoolExecutor(max_workers=_LLM_PARALLELISM) as executor:
            contents = list(executor.map(_invoke, tasks))

    logger.info(
        f"skill_matcher_node: {len(tasks)} role(s) matched "
        f"({len(tasks)} LLM calls, parallelism={_LLM_PARALLELISM})"
    )

    # ---- Phase 3: parse each batched response into per-candidate matches ---
    for task, content in zip(tasks, contents):
        demand = task["demand"]
        parsed = LLMFactory.parse_json_response(content)
        rows = parsed.get("matches") if isinstance(parsed, dict) else parsed
        if not isinstance(rows, list):
            rows = []

        passages_by_id = {
            str(c["employee"].get("id")): c.get("passages") or [] for c in task["candidates"]
        }
        valid_ids = set(passages_by_id.keys())

        for row in rows:
            if not isinstance(row, dict):
                continue
            emp_id = str(row.get("employee_id") or "")
            if emp_id not in valid_ids:
                continue
            match = MatchJustification(
                employee_id=emp_id,
                demand_id=str(demand.get("id")),
                match_score=float(row.get("match_score", 0.0) or 0.0),
                reasoning=row.get("reasoning") or "No reasoning provided",
                missing_skills=row.get("missing_skills", []) or [],
                training_recommendation=row.get("training_recommendation"),
                rag_passages=passages_by_id.get(emp_id, [])[:3],
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
    """Per-role financial optimization using the RAGPipeline financial pre-filter.

    The LLM evaluates each open role (one call per role, fired CONCURRENTLY) and
    PROPOSES a candidate. A deterministic post-pass only guards against
    double-allocating the same person across roles and refines ``allocate`` vs
    ``train`` from the chosen candidate's actual missing skills.
    """
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
    # Cache profile-text embeddings across demands so the on-the-fly semantic
    # scoring only embeds each employee's CV once per run.
    emp_emb_cache: Dict[str, List[float]] = {}

    bench_employees = state.get("bench_employees", [])
    bench_ids = [str(emp["id"]) for emp in bench_employees if emp.get("id")]
    emp_by_id = {str(emp["id"]): emp for emp in bench_employees}

    # ------------------------------------------------------------------
    # Phase 1 — build the candidate set + prompt per role (no LLM yet).
    # ------------------------------------------------------------------
    prepared: List[Dict[str, Any]] = []
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

        # Required skills drive both scoring and the training decision. If the
        # demand has none, derive them from the role text against the bench
        # skill vocabulary so scores are still varied and explainable.
        required_skills = _normalize_skill_list(demand.get("required_skills"))
        if not required_skills:
            required_skills = _derive_required_skills(
                role_title, role_description, bench_employees
            )

        unallocated_bench_ids = [b for b in bench_ids if b not in allocated_employee_ids]

        if not unallocated_bench_ids and bench_ids:
            logger.info(
                f"allocation_decider: all bench employees already allocated — recommending hire for '{role_title}'"
            )
            prepared.append({"decision": {
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
            }})
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

        # ALWAYS evaluate the FULL unallocated bench pool (on top of any RAG
        # hits) so a strong candidate the vector index ranked poorly is never
        # missed.
        if unallocated_bench_ids:
            existing_ids = {
                str(c.get("employee_id") or c.get("id")) for c in candidates
            }
            fallback_ids = [b for b in unallocated_bench_ids if b not in existing_ids]
            if fallback_ids:
                logger.info(
                    f"Augmenting '{role_title}' candidates with {len(fallback_ids)} "
                    f"additional unallocated bench employee(s)."
                )
                profile_texts = [
                    (emp_by_id[b].get("profile_text")
                     or emp_by_id[b].get("job_title")
                     or f"{emp_by_id[b].get('name', '')}")
                    for b in fallback_ids
                ]
                # Embed the role + each CV on the fly so scores are semantic and
                # varied even when the vector index is stale/unavailable.
                sims = rag.semantic_similarity(
                    role_description, profile_texts, cache=emp_emb_cache
                )
                for b_id, sim in zip(fallback_ids, sims):
                    emp = emp_by_id[b_id]
                    cost = float(emp.get("hourly_cost") or emp.get("hourly_cost_rate") or 0.0)
                    sen = emp.get("seniority") or emp.get("experience_level") or "Mid"
                    if cost <= 0:
                        sen_map = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                        cost = sen_map.get(str(sen).lower(), 65.0)
                    margin = round(((target_bill_rate - cost) / target_bill_rate) * 100, 2) if target_bill_rate > 0 else 0.0
                    emp_skills = _employee_skill_profile(emp)
                    candidates.append({
                        "employee_id": b_id,
                        "full_name": emp.get("name") or emp.get("full_name") or f"Employee {b_id[:8]}",
                        "primary_role": emp.get("job_title") or (emp_skills[0] if emp_skills else "Engineer"),
                        "seniority": sen,
                        "hourly_cost_rate": cost,
                        "bench_start_date": emp.get("available_from") or "",
                        "similarity_score": float(sim) if sim else None,
                        "projected_margin_pct": margin,
                        "document_excerpt": emp.get("profile_text") or f"Skills: {', '.join(emp_skills)}",
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

            # --- Deterministic, explainable skill-based scoring ---
            emp_meta = emp_by_id.get(cand_id)
            emp_skills = (
                _employee_skill_profile(emp_meta)
                if emp_meta
                else _normalize_skill_list(cand.get("skills"))
            )
            matched, missing, coverage = _match_skills(emp_skills, required_skills)
            cand["employee_skills"] = emp_skills

            rag_sim = cand.get("similarity_score")
            rag_sim = float(rag_sim) if rag_sim is not None else None

            if coverage is not None:
                score = (0.7 * coverage + 0.3 * rag_sim) if (rag_sim and rag_sim > 0) else coverage
            elif rag_sim is not None and rag_sim > 0:
                score = rag_sim
            else:
                score = 0.6

            cand["similarity_score"] = round(min(1.0, max(0.0, score)), 4)
            cand["matched_skills"] = matched
            cand["missing_skills"] = missing
            cand["skill_coverage"] = round(coverage, 4) if coverage is not None else None

        if not candidates:
            prepared.append({"decision": {
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
            }})
            continue

        prepared.append({
            "demand": demand,
            "role_title": role_title,
            "role_description": role_description,
            "target_bill_rate": target_bill_rate,
            "target_margin": target_margin,
            "required_skills": required_skills,
            "candidates": candidates,
        })

    # ------------------------------------------------------------------
    # Phase 2 — one LLM evaluation per role, fired CONCURRENTLY.
    # ------------------------------------------------------------------
    llm_items = [p for p in prepared if "candidates" in p]

    def _evaluate(p: Dict[str, Any]) -> str:
        sys_msg, human_msg = FinancialMatchPrompts.build_role_evaluation_prompt(
            role_title=p["role_title"],
            role_description=p["role_description"],
            target_bill_rate=p["target_bill_rate"],
            target_margin=p["target_margin"],
            candidates=p["candidates"],
        )
        # Keep the messages so Phase 3 can retry once on unparseable output.
        p["_messages"] = (sys_msg, human_msg)
        return llm.invoke([sys_msg, human_msg]).content

    responses: List[str] = []
    if llm_items:
        with ThreadPoolExecutor(max_workers=_LLM_PARALLELISM) as executor:
            responses = list(executor.map(_evaluate, llm_items))
    for p, content in zip(llm_items, responses):
        p["response_content"] = content

    logger.info(
        f"allocation_decider: completed {len(llm_items)} role evaluation(s) via LLM "
        f"(parallelism={_LLM_PARALLELISM})"
    )

    # ------------------------------------------------------------------
    # Phase 3 — parse each LLM decision + apply deterministic guards.
    # ------------------------------------------------------------------
    for item in prepared:
        if "decision" in item:
            decisions.append(item["decision"])
            continue

        demand = item["demand"]
        role_title = item["role_title"]
        target_bill_rate = item["target_bill_rate"]
        target_margin = item["target_margin"]
        required_skills = item["required_skills"]
        candidates = item["candidates"]

        def _parse(content: str) -> Dict[str, Any]:
            try:
                parsed = LLMFactory.parse_json_response(content or "")
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}

        raw_json = _parse(item.get("response_content") or "")

        # One retry when the model returned something we could not parse as a
        # decision object (keeps LLM-driven output, adds resilience).
        if not raw_json.get("action_type") and item.get("_messages"):
            logger.warning(
                f"allocation_decider: unparseable LLM output for '{role_title}'; retrying once."
            )
            sys_msg, human_msg = item["_messages"]
            strict = HumanMessage(content=(
                human_msg.content
                + "\n\nReturn ONLY one valid JSON object with keys: "
                  "action_type ('allocate'|'train'|'hire'), employee_id, "
                  "projected_margin, justification."
            ))
            raw_json = _parse(llm.invoke([sys_msg, strict]).content)

        try:
            decision = AllocationDecision(**raw_json)
        except Exception as validation_err:
            # Smaller local models sometimes omit required fields (e.g. the
            # upskilling_path for a 'train' decision). Coerce into a safe,
            # schema-valid decision instead of failing the whole run.
            logger.warning(
                f"allocation_decider: LLM output failed validation for '{role_title}' "
                f"({validation_err}). Coercing to a safe decision."
            )
            fallback_emp = (
                str(candidates[0].get("employee_id") or candidates[0].get("id"))
                if candidates else None
            )
            action = raw_json.get("action_type")
            if action not in ("allocate", "train", "hire"):
                action = "allocate"
            justification = str(raw_json.get("justification") or "").strip()
            if len(justification) < 20:
                justification = (
                    f"Candidate evaluated against the {role_title} role based on "
                    f"skills, seniority and projected margin."
                )
            upskilling = raw_json.get("upskilling_path")
            if action == "train" and not upskilling:
                upskilling = f"Structured upskilling recommended for the {role_title} role."
            decision = AllocationDecision(
                action_type=action,
                employee_id=None if action == "hire" else fallback_emp,
                projected_margin=float(raw_json.get("projected_margin") or 0.0),
                justification=justification,
                upskilling_path=upskilling,
                match_score=raw_json.get("match_score"),
            )

        # Respect the LLM's proposed employee/action — the model drives
        # selection. The only deterministic guards are the two below.
        chosen_emp_id = str(decision.employee_id) if decision.employee_id else None
        chosen_cand = next(
            (c for c in candidates if str(c.get("employee_id") or c.get("id")) == chosen_emp_id),
            None,
        )

        # Skill-coverage extremes over the whole candidate pool.
        coverages = [
            c.get("skill_coverage") for c in candidates
            if c.get("skill_coverage") is not None
        ]
        best_cov = max(coverages) if coverages else None

        # Guard 1: never recommend an external hire when a bench candidate
        # FULLY covers the role's required skills.
        if decision.action_type == "hire" and required_skills:
            full_matches = [c for c in candidates if c.get("skill_coverage") == 1.0]
            if full_matches:
                best = max(full_matches, key=_candidate_rank)
                best_id = str(best.get("employee_id") or best.get("id") or "")
                if best_id:
                    logger.info(
                        f"allocation_decider: '{role_title}' — LLM proposed hire but "
                        f"{best_id} fully covers the required skills; allocating internally."
                    )
                    decision.action_type = "allocate"
                    decision.employee_id = best_id
                    chosen_emp_id = best_id
                    chosen_cand = best

        # Guard 2: no meaningful internal match → recommend an external hire.
        # (Best candidate covers less than half the required skills.)
        if (
            required_skills
            and best_cov is not None
            and best_cov < 0.5
            and decision.action_type != "hire"
        ):
            logger.info(
                f"allocation_decider: '{role_title}' — best bench coverage {best_cov:.0%} "
                f"< 50%; overriding '{decision.action_type}' with an external hire."
            )
            decision.action_type = "hire"
            decision.employee_id = None
            chosen_emp_id = None
            chosen_cand = None
            decision.justification = (
                f"No bench employee covers enough of the required skills for "
                f"'{role_title}' (best match {best_cov:.0%}). External hire recommended."
            )

        # Guard 3: never allocate the same person to two roles in one run.
        if decision.action_type in ("allocate", "train") and chosen_emp_id:
            if chosen_emp_id in allocated_employee_ids:
                logger.warning(
                    f"allocation_decider: Employee {chosen_emp_id} was already allocated! Picking next available candidate."
                )
                alt = next(
                    (c for c in candidates if str(c.get("employee_id") or c.get("id")) not in allocated_employee_ids),
                    None,
                )
                if alt:
                    chosen_emp_id = str(alt.get("employee_id") or alt.get("id"))
                    decision.employee_id = chosen_emp_id
                    chosen_cand = alt
                    allocated_employee_ids.add(chosen_emp_id)
                else:
                    decision.action_type = "hire"
                    decision.employee_id = None
                    chosen_cand = None
                    chosen_emp_id = None
                    decision.justification += " (Remaining candidates already allocated to other roles. Hiring recommended)."
            else:
                allocated_employee_ids.add(chosen_emp_id)

        if chosen_cand and (decision.projected_margin == 0.0 or decision.projected_margin is None):
            decision.projected_margin = chosen_cand.get("projected_margin_pct", 0.0)

        # Resolve the chosen employee's metadata with DB/extractor fallbacks so
        # the HITL UI always has cost rate, seniority and name available.
        chosen_emp_meta = emp_by_id.get(str(chosen_emp_id)) if chosen_emp_id else None
        resolved_full_name = (
            (chosen_cand.get("full_name") if chosen_cand else None)
            or (chosen_emp_meta.get("full_name") if chosen_emp_meta else None)
            or (chosen_emp_meta.get("name") if chosen_emp_meta else None)
        )
        resolved_seniority = (
            (chosen_cand.get("seniority") if chosen_cand else None)
            or (chosen_emp_meta.get("seniority") if chosen_emp_meta else None)
            or "Mid"
        )
        resolved_cost_rate = (
            (chosen_cand.get("hourly_cost_rate") if chosen_cand else None)
            or (chosen_emp_meta.get("hourly_cost_rate") if chosen_emp_meta else None)
            or (chosen_emp_meta.get("hourly_cost") if chosen_emp_meta else None)
        )

        # Decide allocate vs. train from ACTUAL missing skills (not a bare score
        # threshold), and generate a training recommendation that names them.
        coverage = chosen_cand.get("skill_coverage") if chosen_cand else None
        if chosen_cand and decision.action_type in ("allocate", "train") and coverage is not None:
            missing_skills = chosen_cand.get("missing_skills") or []
            if missing_skills:
                decision.action_type = "train"
                generated = _build_upskilling_path(missing_skills, role_title)
                llm_path = (decision.upskilling_path or "").strip()
                if llm_path and llm_path.lower() not in generated.lower():
                    generated = f"{generated} {llm_path}"
                decision.upskilling_path = generated
            else:
                decision.action_type = "allocate"
                decision.upskilling_path = None

        # Replace/augment the LLM justification with an explicit, skill-level
        # explanation of why this candidate fits (or needs training for) the role.
        if chosen_cand and decision.action_type in ("allocate", "train"):
            decision.justification = _build_match_justification(
                resolved_full_name,
                role_title,
                chosen_cand.get("matched_skills") or [],
                chosen_cand.get("missing_skills") or [],
                decision.justification,
                action=decision.action_type,
                candidate_skills=chosen_cand.get("employee_skills") or [],
            )

        # Match score: prefer the deterministic skill-based score computed for
        # the chosen candidate, then the LLM value, then skill_matches.
        resolved_match_score = chosen_cand.get("similarity_score") if chosen_cand else None
        if resolved_match_score is None or float(resolved_match_score) == 0.0:
            resolved_match_score = decision.match_score

        if (resolved_match_score is None or float(resolved_match_score) == 0.0) and chosen_emp_id:
            skill_matches = state.get("skill_matches") or []
            relevant = [
                m for m in skill_matches
                if str(m.get("employee_id")) == str(chosen_emp_id)
                and str(m.get("demand_id")) == str(demand.get("id", ""))
            ]
            if not relevant:
                relevant = [
                    m for m in skill_matches
                    if str(m.get("employee_id")) == str(chosen_emp_id)
                ]
            if relevant:
                resolved_match_score = max(
                    float(m.get("match_score") or 0.0) for m in relevant
                )

        if resolved_match_score is None:
            resolved_match_score = 0.0
        decision.match_score = round(float(resolved_match_score), 4)

        decision_dict = decision.model_dump()
        decision_dict.update(
            {
                "role_id": demand.get("id", ""),
                "role_title": role_title,
                "target_project_id": demand.get("project_id", ""),
                "target_bill_rate": target_bill_rate,
                "target_margin": target_margin,
                "candidate_count": len(candidates),
                "match_score": decision.match_score,
                "similarity_score": decision.match_score,
                "seniority": resolved_seniority,
                "full_name": resolved_full_name,
                "hourly_cost_rate": resolved_cost_rate,
                "matched_skills": chosen_cand.get("matched_skills") if chosen_cand else [],
                "missing_skills": chosen_cand.get("missing_skills") if chosen_cand else [],
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

    # The plan is ALWAYS derived deterministically from the per-role decisions
    # (the decider already selected the candidates). The LLM is used only to
    # write the human-readable reasoning — it must never invent allocations,
    # employees or project ids.
    plan_json = _build_plan_from_decisions(decisions)

    try:
        llm = LLMFactory.get_chat_model()
        sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
            matches=matches,
            employees_count=len(employees),
            open_roles_count=len(demands),
        )
        response = llm.invoke([sys_msg, human_msg])
        parsed = LLMFactory.parse_json_response(response.content)
        reasoning = str(parsed.get("reasoning") or "").strip() if isinstance(parsed, dict) else ""
        if reasoning:
            plan_json["reasoning"] = reasoning
    except Exception as exc:
        logger.warning(
            f"forecast_planner_node: LLM reasoning call failed ({exc}); "
            f"using deterministic reasoning."
        )

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
                    project_name = None
                    if demand and getattr(demand, "project_id", None):
                        proj_stmt = select(Project).where(Project.id == demand.project_id)
                        proj_res = await db.execute(proj_stmt)
                        proj = proj_res.scalars().first()
                        if proj:
                            project_name = proj.name
                    if not project_name:
                        project_name = reallocation.get("project_name") or role or "Assigned Project"
                    employee.current_project = project_name

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
