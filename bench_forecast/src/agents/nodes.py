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
import math
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

# Importing Config loads the root .env before the module-level os.getenv reads
# below. Without this, a direct script that imports nodes.py before config.py
# would silently use the code defaults instead of .env.
from src.core.config import Config  # noqa: F401

# Number of concurrent LLM calls fired within a node. Requires the Ollama
# server to allow parallelism (OLLAMA_NUM_PARALLEL) to help materially, but it
# also overlaps embeddings + per-call latency.
_LLM_PARALLELISM = int(os.getenv("FORECAST_LLM_PARALLELISM", "4"))
_LLM_RATE_LIMIT_RETRIES = int(os.getenv("LLM_RATE_LIMIT_RETRIES", "5"))
_LLM_RATE_LIMIT_MAX_WAIT = float(os.getenv("LLM_RATE_LIMIT_MAX_WAIT", "45"))

# Maximum number of candidates shown to the LLM per role. Small local models
# degrade sharply on long prompts and start echoing the list index instead of
# the UUID; the full ranked pool is still kept for deterministic selection.
_MAX_PROMPT_CANDIDATES = int(os.getenv("FORECAST_MAX_PROMPT_CANDIDATES", "4"))

# Candidates assessed by the skill_matcher per demand. Keeping this close to the
# decider's prompt window makes the LLM assessment available for the candidates
# the decider actually sees.
_SKILL_MATCH_TOP_K = int(os.getenv("SKILL_MATCH_TOP_K", "6"))

# A projected margin below this floor is never proposed as an allocation.
_MIN_ACCEPTABLE_MARGIN = float(os.getenv("FORECAST_MIN_ACCEPTABLE_MARGIN", "0"))

# How the decider resolves skill fit vs target margin:
#   balanced    — allow a below-target choice when the skill advantage is real
#   margin_first — prefer a candidate meeting the target margin
#   skill_first  — prefer full skill coverage even below target margin
_DECISION_POLICY = os.getenv("FORECAST_DECISION_POLICY", "balanced").strip().lower()
if _DECISION_POLICY not in ("balanced", "margin_first", "skill_first"):
    _DECISION_POLICY = "balanced"
_BALANCED_SKILL_ADVANTAGE = float(
    os.getenv("FORECAST_BALANCED_SKILL_ADVANTAGE", "0.2")
)

# The plan summary is generated deterministically from the decisions by default.
# Small local models hallucinate employee names/roles when asked to narrate a
# plan, so the LLM path is opt-in for experimentation only.
_LLM_PLAN_REASONING = os.getenv("FORECAST_LLM_REASONING", "false").lower() in (
    "1", "true", "yes", "on"
)
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
      1. strongest blended score (deterministic coverage + skill_matcher
         assessment + vector similarity),
      2. highest fraction of the role's required skills covered,
      3. highest projected margin (financial tie-breaker).
    """
    coverage = candidate.get("skill_coverage")
    return (
        float(candidate.get("similarity_score") or 0.0),
        float(coverage) if coverage is not None else 0.0,
        float(candidate.get("projected_margin_pct") or 0.0),
    )


def _candidate_id(candidate: Dict[str, Any]) -> str:
    """Return the candidate's employee id (RAG rows use ``id`` or ``employee_id``)."""
    return str(candidate.get("employee_id") or candidate.get("id") or "")


def _candidate_margin(candidate: Dict[str, Any]) -> Optional[float]:
    """Return the candidate's deterministic projected margin, if known."""
    value = candidate.get("projected_margin_pct")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp01(value: Any) -> Optional[float]:
    """Coerce an LLM score to a finite [0, 1] float, or None if unusable."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return max(0.0, min(1.0, number))


def _retry_wait_seconds(error_text: str, attempt: int) -> float:
    """Respect Groq's suggested retry delay, with a bounded exponential fallback."""
    match = re.search(r"try again in ([\d.]+)s", error_text)
    if match:
        return min(float(match.group(1)) + 1.0, _LLM_RATE_LIMIT_MAX_WAIT)
    return min(5.0 * attempt, _LLM_RATE_LIMIT_MAX_WAIT)


def _invoke_with_retry(llm: Any, messages: List[Any]) -> Any:
    """Invoke a chat model, retrying Groq 429/TPM errors with backoff.

    ``ChatGroq`` has its own retry counter, but free-tier TPM limits can outlast
    it. This wrapper parses the "try again in Xs" hint from the API error and
    waits accordingly, so a single rate-limited batch does not fail the run.
    """
    for attempt in range(1, _LLM_RATE_LIMIT_RETRIES + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            text = str(exc).lower()
            rate_limited = any(
                marker in text
                for marker in ("429", "rate limit", "tokens per minute", "tpm")
            )
            if not rate_limited or attempt >= _LLM_RATE_LIMIT_RETRIES:
                raise
            wait = _retry_wait_seconds(text, attempt)
            logger.warning(
                f"LLM rate limit hit (attempt {attempt}/{_LLM_RATE_LIMIT_RETRIES}); "
                f"sleeping {wait:.1f}s before retry."
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _target_margin_candidates(
    candidates: List[Dict[str, Any]],
    target_margin: float,
    exclude_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Return target-margin candidates, best-ranked first."""
    excluded = exclude_ids or set()
    viable = [
        c for c in candidates
        if _candidate_id(c)
        and _candidate_id(c) not in excluded
        and _candidate_margin(c) is not None
        and float(_candidate_margin(c)) >= target_margin
    ]
    return sorted(viable, key=_candidate_rank, reverse=True)


def _format_candidate_shortlist(
    candidates: List[Dict[str, Any]],
    limit: int = 3,
) -> str:
    """Render a compact shortlist for a re-prompt."""
    lines = []
    for index, candidate in enumerate(candidates[:limit], 1):
        coverage = candidate.get("skill_coverage")
        coverage_text = f"{float(coverage):.0%}" if coverage is not None else "n/a"
        lines.append(
            f"{index}. [{_candidate_id(candidate)}] "
            f"{candidate.get('full_name') or 'Unknown'} — "
            f"margin {float(_candidate_margin(candidate) or 0.0):.1f}%, "
            f"skill coverage {coverage_text}, "
            f"missing: {', '.join(candidate.get('missing_skills') or []) or 'none'}"
        )
    return "\n".join(lines)


def _sanitize_alternatives(
    raw_alternatives: Any,
    candidates: List[Dict[str, Any]],
    primary_employee_id: Optional[str],
    limit: int = 2,
) -> List[Dict[str, Any]]:
    """Ground and trim the LLM's advisory alternatives.

    Alternatives never drive persistence, so invalid entries are simply
    dropped rather than failing the whole decision.
    """
    if not isinstance(raw_alternatives, list):
        return []

    sanitized: List[Dict[str, Any]] = []
    seen = {str(primary_employee_id)} if primary_employee_id else set()
    for raw in raw_alternatives:
        if not isinstance(raw, dict):
            continue
        employee_id = _resolve_candidate_id(raw.get("employee_id"), candidates)
        if not employee_id or employee_id in seen:
            continue
        candidate = next(
            (c for c in candidates if _candidate_id(c) == employee_id),
            None,
        )
        if candidate is None:
            continue
        margin = _candidate_margin(candidate)
        if margin is None:
            try:
                margin = float(raw.get("projected_margin") or 0.0)
            except (TypeError, ValueError):
                margin = 0.0
        seen.add(employee_id)
        sanitized.append({
            "employee_id": employee_id,
            "projected_margin": round(float(margin or 0.0), 2),
            "rationale": str(raw.get("rationale") or "")[:300],
        })
        if len(sanitized) >= limit:
            break
    return sanitized


def _reconsider_decision(
    llm: Any,
    item: Dict[str, Any],
    role_title: str,
    target_margin: float,
    target_candidates: List[Dict[str, Any]],
) -> Optional[tuple]:
    """Ask the LLM to reconsider a below-target choice.

    Returns ``(action_type, candidate_or_None, justification_or_None)`` when the
    model produces a usable answer, otherwise ``None``. The caller falls back to
    the best target-margin candidate when this returns ``None``.
    """
    messages = item.get("_messages")
    if not messages or not target_candidates:
        return None

    sys_msg, human_msg = messages
    prompt = (
        human_msg.content
        + "\n\nRECONSIDER: Your previous choice is below the role's target margin "
        f"of {target_margin:.1f}%. Choose one of the target-margin candidates "
        "below, or return action_type='hire' if none is acceptable.\n"
        f"{_format_candidate_shortlist(target_candidates)}\n"
        "Return ONLY the same JSON object. employee_id must be one of the "
        "bracketed ids above."
    )
    try:
        content = _invoke_with_retry(
            llm, [sys_msg, HumanMessage(content=prompt)]
        ).content
        parsed = LLMFactory.parse_json_response(content)
    except Exception as exc:
        logger.warning(
            f"allocation_decider: reconsideration call failed for '{role_title}' "
            f"({exc}); falling back to deterministic choice."
        )
        return None

    if not isinstance(parsed, dict):
        return None

    action = parsed.get("action_type")
    justification = str(parsed.get("justification") or "").strip() or None
    if action == "hire":
        return ("hire", None, justification)

    if action in ("allocate", "train"):
        employee_id = _resolve_candidate_id(
            parsed.get("employee_id"), target_candidates
        )
        candidate = next(
            (c for c in target_candidates if _candidate_id(c) == employee_id),
            None,
        )
        if candidate is not None:
            return (action, candidate, justification)

    return None


def _best_candidate(
    candidates: List[Dict[str, Any]],
    *,
    min_margin: float = _MIN_ACCEPTABLE_MARGIN,
    exclude_ids: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Return the highest-ranked candidate meeting the margin floor."""
    excluded = exclude_ids or set()
    viable = [
        c for c in candidates
        if _candidate_id(c)
        and _candidate_id(c) not in excluded
        and _candidate_margin(c) is not None
        and float(_candidate_margin(c)) >= min_margin
    ]
    if not viable:
        return None
    return max(viable, key=_candidate_rank)


def _resolve_candidate_id(
    raw_id: Any,
    candidates: List[Dict[str, Any]],
) -> Optional[str]:
    """Ground an LLM-provided employee id against the actual candidate pool.

    Local models routinely return the 1-based list number shown in the prompt
    (e.g. ``"12"``) instead of the UUID printed in brackets. This resolver
    accepts a verbatim id, a case-insensitive id, a 1-based list index, or an
    exact candidate name — and returns ``None`` for anything that cannot be
    grounded. Callers must never persist an ungrounded id.
    """
    raw = str(raw_id).strip() if raw_id is not None else ""
    if not raw:
        return None

    ids = [_candidate_id(c) for c in candidates if _candidate_id(c)]
    if raw in ids:
        return raw

    lowered = {i.lower(): i for i in ids}
    if raw.lower() in lowered:
        return lowered[raw.lower()]

    # 1-based index as printed by _format_candidates_block().
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(candidates):
            candidate_id = _candidate_id(candidates[index - 1])
            if candidate_id:
                return candidate_id

    for candidate in candidates:
        name = str(candidate.get("full_name") or candidate.get("name") or "").strip()
        if name and name.lower() == raw.lower():
            candidate_id = _candidate_id(candidate)
            if candidate_id:
                return candidate_id

    return None


_PLACEHOLDER_MARKERS = (
    "minimum 50 characters",
    "explain overall strategy",
    "your reasoning here",
    "<string>",
    "string —",
    "string -",
    "lorem ipsum",
    "2-4 sentences",
    "actual allocation",
    "margin rationale",
    "four bench engineers can be allocated",
    "two roles require external hiring",
    "prj-001",
)


def _is_usable_reasoning(text: Any) -> bool:
    """Reject schema-echo / placeholder text returned by small local models."""
    value = str(text or "").strip()
    if len(value) < 50:
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    if lowered.startswith(("string", "reasoning:", "example")):
        return False
    return True


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
    other_names: Optional[List[str]] = None,
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
        # Drop text that discusses a different candidate (common when the LLM
        # reasons over the whole shortlist while justifying one choice).
        own_name = (full_name or "").strip().lower()
        for other in other_names or []:
            other_name = str(other or "").strip().lower()
            if other_name and other_name != own_name and other_name in lower:
                contradicts = True
                break
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
            # Include bench, upcoming-bench AND on-project employees that still
            # have capacity. Fractional allocations mean "on_project" no longer
            # implies fully utilised.
            emp_stmt = select(Employee).options(
                selectinload(Employee.employee_costs),
                selectinload(Employee.allocations),
            ).where(
                Employee.is_active == True,
                Employee.bench_status.in_([
                    BenchStatusEnum.bench,
                    BenchStatusEnum.upcoming_bench,
                    BenchStatusEnum.on_project,
                ]),
            )
            emp_res = await db.execute(emp_stmt)
            employees_db = emp_res.scalars().all()

            employees = []
            for emp in employees_db:
                remaining_hours = emp.remaining_daily_hours
                if remaining_hours <= 0:
                    continue
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
                    "daily_capacity_hours": float(emp.effective_daily_capacity),
                    "committed_hours_per_day": float(emp.committed_daily_hours),
                    "remaining_available_hours": float(remaining_hours),
                }
                employees.append(emp_dict)

            # Query open demands with their projects
            target_project_id = state.get("target_project_id")
            dem_stmt = select(ProjectDemand).options(
                selectinload(ProjectDemand.project)
            ).where(
                ProjectDemand.status.in_([
                    DemandStatusEnum.open,
                    DemandStatusEnum.partially_filled,
                ]),
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
                    "required_skills": raw_skills,
                    "hours_per_day": float(d.hours_per_day or 8),
                    "headcount_needed": int(d.headcount_needed or 0),
                    "headcount_required": int(d.headcount_required or 1),
                    "seniority_level": (
                        d.required_experience_level.value
                        if d.required_experience_level is not None
                        and hasattr(d.required_experience_level, "value")
                        else None
                    ),
                    "status": (
                        d.status.value
                        if hasattr(d.status, "value")
                        else str(d.status)
                    ),
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
        rag_results = vector_mgr.similarity_search(rag_query, k=_SKILL_MATCH_TOP_K, where=where_filter)
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
        return _invoke_with_retry(llm, [task["sys"], task["human"]]).content

    contents: List[str] = []
    if tasks:
        with ThreadPoolExecutor(max_workers=_LLM_PARALLELISM) as executor:
            # copy_context() keeps the active observability workflow/span in the
            # worker thread so concurrent LLM spans nest under the trace instead
            # of becoming detached roots.
            futures = [executor.submit(copy_context().run, _invoke, task) for task in tasks]
            contents = [future.result() for future in futures]

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
    PROPOSES a candidate. A deterministic post-pass grounds the proposed
    employee id, prevents double-allocating the same person across roles,
    enforces the margin floor/target preference, and refines ``allocate`` vs
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
    # Employees whose remaining hours have reached zero in this run. They are
    # excluded, but partially-allocated employees may still take a second role
    # if their remaining tracker covers it.
    allocated_employee_ids: set = set()
    # Cache profile-text embeddings across demands so the on-the-fly semantic
    # scoring only embeds each employee's CV once per run.
    emp_emb_cache: Dict[str, List[float]] = {}

    bench_employees = state.get("bench_employees", [])
    bench_ids = [str(emp["id"]) for emp in bench_employees if emp.get("id")]
    emp_by_id = {str(emp["id"]): emp for emp in bench_employees}
    remaining_hours_by_emp: Dict[str, float] = {
        str(emp["id"]): float(
            emp.get("remaining_available_hours")
            or emp.get("daily_capacity_hours")
            or 8.0
        )
        for emp in bench_employees
        if emp.get("id")
    }

    def _has_hours(emp_id: str, hours: float) -> bool:
        # Legacy/unit-test candidate pools may not include bench_employees.
        # Unknown candidates are allowed through here; the DB-level capacity
        # guard still enforces the hard cap at execution time.
        if emp_id not in remaining_hours_by_emp:
            return emp_id not in allocated_employee_ids
        return (
            emp_id not in allocated_employee_ids
            and float(remaining_hours_by_emp.get(emp_id, 0.0)) + 1e-9 >= hours
        )

    def _reserve_hours(emp_id: str, hours: float) -> None:
        remaining_hours_by_emp[emp_id] = (
            float(remaining_hours_by_emp.get(emp_id, 0.0)) - hours
        )
        if remaining_hours_by_emp[emp_id] <= 1e-9:
            remaining_hours_by_emp[emp_id] = 0.0
            allocated_employee_ids.add(emp_id)
        else:
            allocated_employee_ids.discard(emp_id)

    def _release_hours(emp_id: str, hours: float) -> None:
        remaining_hours_by_emp[emp_id] = (
            float(remaining_hours_by_emp.get(emp_id, 0.0)) + hours
        )
        if remaining_hours_by_emp[emp_id] > 1e-9:
            allocated_employee_ids.discard(emp_id)

    # The skill_matcher's semantic assessment, keyed by (demand_id, employee_id).
    # The decider uses it as a soft signal in ranking and as prompt context; the
    # deterministic guards still own the hard constraints.
    skill_assessments: Dict[tuple, Dict[str, Any]] = {}
    for match in state.get("skill_matches", []) or []:
        key = (
            str(match.get("demand_id") or ""),
            str(match.get("employee_id") or ""),
        )
        if all(key):
            skill_assessments[key] = match

    # ------------------------------------------------------------------
    # Phase 1 — build the candidate set + prompt per role (no LLM yet).
    # ------------------------------------------------------------------
    prepared: List[Dict[str, Any]] = []
    for demand in demands:
        role_title = demand.get("role", "Unknown Role")
        role_description = demand.get("description", "") or role_title
        demand_hours = float(demand.get("hours_per_day") or 8.0)

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

        unallocated_bench_ids = [
            b for b in bench_ids if _has_hours(b, demand_hours)
        ]

        if not unallocated_bench_ids and bench_ids:
            logger.info(
                f"allocation_decider: no employee has {demand_hours}h/day remaining "
                f"— recommending hire for '{role_title}'"
            )
            prepared.append({"decision": {
                "action_type": "hire",
                "employee_id": None,
                "projected_margin": 0.0,
                "justification": (
                    f"No employee has {demand_hours}h/day of remaining capacity "
                    f"for '{role_title}'. External hiring recommended."
                ),
                "upskilling_path": None,
                "role_id": demand.get("id", ""),
                "role_title": role_title,
                "target_project_id": demand.get("project_id", ""),
                "target_bill_rate": target_bill_rate,
                "target_margin": target_margin,
                "candidate_count": 0,
                "required_skills": required_skills,
                "hours_per_day": demand_hours,
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
                and _has_hours(
                    str(c.get("employee_id") or c.get("id")), demand_hours
                )
            ]
        else:
            candidates = [
                c for c in candidates
                if _has_hours(
                    str(c.get("employee_id") or c.get("id")), demand_hours
                )
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
                skipped_unprofitable = 0
                for b_id, sim in zip(fallback_ids, sims):
                    emp = emp_by_id[b_id]
                    cost = float(emp.get("hourly_cost") or emp.get("hourly_cost_rate") or 0.0)
                    sen = emp.get("seniority") or emp.get("experience_level") or "Mid"
                    if cost <= 0:
                        sen_map = {"junior": 40.0, "mid": 65.0, "senior": 95.0, "lead": 125.0, "principal": 160.0}
                        cost = sen_map.get(str(sen).lower(), 65.0)
                    margin = round(((target_bill_rate - cost) / target_bill_rate) * 100, 2) if target_bill_rate > 0 else 0.0
                    # Never widen the pool with a loss-making profile: the prompt
                    # tells the model the list is financially pre-filtered.
                    if margin < _MIN_ACCEPTABLE_MARGIN:
                        skipped_unprofitable += 1
                        continue
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
                if skipped_unprofitable:
                    logger.info(
                        f"Augmenting '{role_title}': skipped {skipped_unprofitable} "
                        f"candidate(s) below the {_MIN_ACCEPTABLE_MARGIN:.1f}% margin floor."
                    )

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
            cand["remaining_available_hours"] = float(
                remaining_hours_by_emp.get(cand_id, 0.0)
            )

            # Attach the independent skill_matcher assessment, when this
            # candidate was in its top-K pool.
            assessment = skill_assessments.get(
                (str(demand.get("id", "")), cand_id)
            )
            if assessment:
                cand["llm_match_score"] = _clamp01(assessment.get("match_score"))
                cand["llm_reasoning"] = assessment.get("reasoning")
                cand["llm_missing_skills"] = _normalize_skill_list(
                    assessment.get("missing_skills")
                )
                cand["llm_training_recommendation"] = assessment.get(
                    "training_recommendation"
                )

            rag_sim = cand.get("similarity_score")
            rag_sim = _clamp01(rag_sim)
            llm_sim = cand.get("llm_match_score")

            # Blend deterministic skill coverage with the independent LLM
            # assessment and the vector similarity. Only available signals are
            # used, so missing assessments do not penalise a candidate.
            weighted = []
            if coverage is not None:
                weighted.append((float(coverage), 0.55))
            if llm_sim is not None:
                weighted.append((float(llm_sim), 0.25))
            if rag_sim is not None and rag_sim > 0:
                weighted.append((float(rag_sim), 0.20))

            if weighted:
                total_weight = sum(w for _, w in weighted)
                score = sum(value * w for value, w in weighted) / total_weight
            else:
                score = 0.6

            cand["similarity_score"] = round(min(1.0, max(0.0, score)), 4)
            cand["matched_skills"] = matched
            cand["missing_skills"] = missing
            cand["skill_coverage"] = round(coverage, 4) if coverage is not None else None

        # Financial floor: never let a loss-making profile reach the LLM or the
        # deterministic fallback. Unknown margins are kept (no data to judge).
        unprofitable = [
            c for c in candidates
            if _candidate_margin(c) is not None
            and float(_candidate_margin(c)) < _MIN_ACCEPTABLE_MARGIN
        ]
        if unprofitable:
            logger.info(
                f"  Dropping {len(unprofitable)} candidate(s) for '{role_title}' "
                f"below the {_MIN_ACCEPTABLE_MARGIN:.1f}% margin floor."
            )
            candidates = [
                c for c in candidates
                if _candidate_margin(c) is None
                or float(_candidate_margin(c)) >= _MIN_ACCEPTABLE_MARGIN
            ]

        # Best-ranked first: deterministic fallbacks (invalid LLM id, duplicate
        # employee, margin guard) then always pick the strongest viable person.
        candidates.sort(key=_candidate_rank, reverse=True)

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
                "required_skills": required_skills,
                "hours_per_day": demand_hours,
            }})
            continue

        prepared.append({
            "demand": demand,
            "role_title": role_title,
            "role_description": role_description,
            "target_bill_rate": target_bill_rate,
            "target_margin": target_margin,
            "required_skills": required_skills,
            "hours_per_day": demand_hours,
            "candidates": candidates,
        })

    # ------------------------------------------------------------------
    # Phase 2 — one LLM evaluation per role, fired CONCURRENTLY.
    # ------------------------------------------------------------------
    llm_items = [p for p in prepared if "candidates" in p]

    def _evaluate(p: Dict[str, Any]) -> str:
        # A local 3B model starts returning list indices on long prompts; show
        # only the strongest candidates while keeping the full pool in memory.
        prompt_candidates = p["candidates"][:_MAX_PROMPT_CANDIDATES]
        p["_prompt_candidate_ids"] = [
            _candidate_id(c) for c in prompt_candidates if _candidate_id(c)
        ]
        sys_msg, human_msg = FinancialMatchPrompts.build_role_evaluation_prompt(
            role_title=p["role_title"],
            role_description=p["role_description"],
            target_bill_rate=p["target_bill_rate"],
            target_margin=p["target_margin"],
            candidates=prompt_candidates,
        )
        # Keep the messages so Phase 3 can retry once on unparseable output.
        p["_messages"] = (sys_msg, human_msg)
        return _invoke_with_retry(llm, [sys_msg, human_msg]).content

    responses: List[str] = []
    if llm_items:
        with ThreadPoolExecutor(max_workers=_LLM_PARALLELISM) as executor:
            # Preserve the observability context per task (see skill_matcher).
            futures = [executor.submit(copy_context().run, _evaluate, item) for item in llm_items]
            responses = [future.result() for future in futures]
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
            allowed_ids = ", ".join(item.get("_prompt_candidate_ids") or [])
            strict = HumanMessage(content=(
                human_msg.content
                + "\n\nReturn ONLY one valid JSON object with keys: "
                  "action_type ('allocate'|'train'|'hire'), employee_id, "
                  "projected_margin, justification, upskilling_path.\n"
                  f"employee_id MUST be copied verbatim from this list: {allowed_ids}. "
                  "Never return the list number. Use null when action_type is 'hire'."
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
            fallback = _best_candidate(candidates)
            action = raw_json.get("action_type")
            if action not in ("allocate", "train", "hire"):
                action = "allocate" if fallback else "hire"
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
                employee_id=(
                    _candidate_id(fallback)
                    if action != "hire" and fallback
                    else None
                ),
                projected_margin=float(raw_json.get("projected_margin") or 0.0),
                justification=justification,
                upskilling_path=upskilling,
                match_score=raw_json.get("match_score"),
            )

        # Guard 0: ground the LLM's employee_id in the real candidate pool.
        # Small local models frequently return the prompt's list number (e.g.
        # "12") instead of the UUID. An ungrounded id must never reach
        # persistence, where it would silently drop the whole demand.
        if decision.action_type in ("allocate", "train"):
            grounded = _resolve_candidate_id(decision.employee_id, candidates)
            if grounded is None:
                logger.warning(
                    f"allocation_decider: '{role_title}' returned unusable "
                    f"employee_id {decision.employee_id!r}; falling back to the "
                    f"best-ranked candidate."
                )
                best = _best_candidate(candidates)
                if best is None:
                    decision.action_type = "hire"
                    decision.employee_id = None
                    decision.upskilling_path = None
                    decision.justification = (
                        f"No financially viable bench candidate is available for "
                        f"'{role_title}'. External hire recommended."
                    )
                else:
                    decision.employee_id = _candidate_id(best)
            else:
                if grounded != str(decision.employee_id):
                    logger.info(
                        f"allocation_decider: '{role_title}' employee_id "
                        f"{decision.employee_id!r} resolved to candidate {grounded}."
                    )
                decision.employee_id = grounded

        # Respect the LLM's proposed employee/action — the model drives
        # selection. Deterministic guards below only correct unsafe choices.
        chosen_emp_id = str(decision.employee_id) if decision.employee_id else None
        chosen_cand = next(
            (c for c in candidates if _candidate_id(c) == chosen_emp_id),
            None,
        )

        # Skill-coverage extremes over the whole candidate pool.
        coverages = [
            c.get("skill_coverage") for c in candidates
            if c.get("skill_coverage") is not None
        ]
        best_cov = max(coverages) if coverages else None

        # Guard 1: never recommend an external hire when a financially viable
        # bench candidate FULLY covers the role's required skills.
        if decision.action_type == "hire" and required_skills:
            full_matches = [
                c for c in candidates
                if c.get("skill_coverage") == 1.0
                and _candidate_margin(c) is not None
                and float(_candidate_margin(c)) >= _MIN_ACCEPTABLE_MARGIN
            ]
            if full_matches:
                best = max(full_matches, key=_candidate_rank)
                best_id = _candidate_id(best)
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

        # Guard 3: never over-allocate a person's remaining daily hours.
        # Multi-project fractional allocation is allowed, but the in-memory
        # tracker is decremented after every decision in this run.
        if decision.action_type in ("allocate", "train") and chosen_emp_id:
            if not _has_hours(chosen_emp_id, demand_hours):
                logger.warning(
                    f"allocation_decider: Employee {chosen_emp_id} has no "
                    f"{demand_hours}h/day remaining; picking another candidate."
                )
                alt = _best_candidate(
                    [
                        c for c in candidates
                        if _has_hours(_candidate_id(c), demand_hours)
                    ],
                    exclude_ids=allocated_employee_ids,
                )
                if alt:
                    chosen_emp_id = _candidate_id(alt)
                    decision.employee_id = chosen_emp_id
                    chosen_cand = alt
                    _reserve_hours(chosen_emp_id, demand_hours)
                else:
                    decision.action_type = "hire"
                    decision.employee_id = None
                    decision.upskilling_path = None
                    chosen_cand = None
                    chosen_emp_id = None
                    decision.justification += (
                        " (No employee has enough remaining daily hours. "
                        "Hiring recommended)."
                    )
            else:
                _reserve_hours(chosen_emp_id, demand_hours)

        # Guard 4: financial viability + skill-vs-margin policy.
        #
        # Hard constraint: never propose a loss-making allocation.
        # Soft policy: target margin is a preference, not a silent override.
        #   balanced    — accept a below-target choice only when skill coverage
        #                 materially beats the best target-margin candidate;
        #                 otherwise ask the LLM to reconsider.
        #   margin_first — ask the LLM to reconsider / switch deterministically.
        #   skill_first  — accept below-target choices with strong coverage.
        margin_note = None
        if chosen_cand and decision.action_type in ("allocate", "train"):
            chosen_margin = _candidate_margin(chosen_cand)
            decision.projected_margin = round(float(chosen_margin or 0.0), 2)

            if chosen_margin is None or chosen_margin < _MIN_ACCEPTABLE_MARGIN:
                replacement = _best_candidate(
                    candidates,
                    exclude_ids=allocated_employee_ids | {chosen_emp_id},
                )
                if replacement is not None:
                    logger.info(
                        f"allocation_decider: '{role_title}' — candidate "
                        f"{chosen_emp_id} projects {chosen_margin}% margin; switching "
                        f"to {_candidate_id(replacement)}."
                    )
                    _release_hours(chosen_emp_id, demand_hours)
                    chosen_cand = replacement
                    chosen_emp_id = _candidate_id(replacement)
                    decision.employee_id = chosen_emp_id
                    _reserve_hours(chosen_emp_id, demand_hours)
                    decision.projected_margin = round(
                        float(_candidate_margin(replacement) or 0.0), 2
                    )
                    decision.justification = ""
                else:
                    logger.info(
                        f"allocation_decider: '{role_title}' — no candidate clears "
                        f"the {_MIN_ACCEPTABLE_MARGIN:.1f}% margin floor; "
                        f"recommending hire."
                    )
                    _release_hours(chosen_emp_id, demand_hours)
                    decision.action_type = "hire"
                    decision.employee_id = None
                    decision.upskilling_path = None
                    decision.projected_margin = 0.0
                    decision.justification = (
                        f"No financially viable bench candidate is available for "
                        f"'{role_title}' (margin floor "
                        f"{_MIN_ACCEPTABLE_MARGIN:.1f}%). External hire recommended."
                    )
                    chosen_emp_id = None
                    chosen_cand = None
            elif chosen_margin < target_margin:
                target_candidates = _target_margin_candidates(
                    candidates, target_margin, allocated_employee_ids
                )
                target_candidate = target_candidates[0] if target_candidates else None
                chosen_coverage = float(chosen_cand.get("skill_coverage") or 0.0)
                target_coverage = (
                    float(target_candidate.get("skill_coverage") or 0.0)
                    if target_candidate is not None
                    else 0.0
                )

                accept_below_target = False
                if _DECISION_POLICY == "skill_first":
                    accept_below_target = chosen_coverage >= 0.8
                elif _DECISION_POLICY == "balanced":
                    accept_below_target = (
                        chosen_coverage
                        >= target_coverage + _BALANCED_SKILL_ADVANTAGE
                        or (
                            chosen_coverage >= 0.99
                            and target_coverage < 0.99
                        )
                    )

                if accept_below_target:
                    margin_note = (
                        f"Accepted below the {target_margin:.1f}% target margin "
                        f"({chosen_margin:.1f}%) because skill coverage "
                        f"({chosen_coverage:.0%}) materially exceeds the best "
                        f"target-margin candidate ({target_coverage:.0%})."
                    )
                    logger.info(
                        f"allocation_decider: '{role_title}' — {margin_note}"
                    )
                elif target_candidate is not None:
                    reconsidered = _reconsider_decision(
                        llm, item, role_title, target_margin, target_candidates
                    )
                    if reconsidered is not None:
                        new_action, new_candidate, new_justification = reconsidered
                        _release_hours(chosen_emp_id, demand_hours)
                        if new_action == "hire":
                            decision.action_type = "hire"
                            decision.employee_id = None
                            decision.upskilling_path = None
                            decision.projected_margin = 0.0
                            chosen_emp_id = None
                            chosen_cand = None
                            decision.justification = new_justification or (
                                f"No target-margin bench candidate is acceptable "
                                f"for '{role_title}'. External hire recommended."
                            )
                        else:
                            chosen_cand = new_candidate
                            chosen_emp_id = _candidate_id(new_candidate)
                            decision.action_type = new_action
                            decision.employee_id = chosen_emp_id
                            _reserve_hours(chosen_emp_id, demand_hours)
                            decision.projected_margin = round(
                                float(_candidate_margin(new_candidate) or 0.0), 2
                            )
                            if new_justification:
                                decision.justification = new_justification
                            else:
                                decision.justification = ""
                            margin_note = (
                                f"Reconsidered to meet the {target_margin:.1f}% "
                                f"target margin "
                                f"({decision.projected_margin:.1f}%)."
                            )
                            logger.info(
                                f"allocation_decider: '{role_title}' — {margin_note}"
                            )
                    else:
                        # Deterministic fallback: move to the best target-margin
                        # candidate. Reset the justification so the skill-level
                        # builder describes the new candidate, not the old one.
                        _release_hours(chosen_emp_id, demand_hours)
                        chosen_cand = target_candidate
                        chosen_emp_id = _candidate_id(target_candidate)
                        decision.action_type = "allocate"
                        decision.employee_id = chosen_emp_id
                        _reserve_hours(chosen_emp_id, demand_hours)
                        decision.projected_margin = round(
                            float(_candidate_margin(target_candidate) or 0.0), 2
                        )
                        decision.justification = ""
                        margin_note = (
                            f"Switched to the best target-margin candidate "
                            f"({decision.projected_margin:.1f}% vs "
                            f"{chosen_margin:.1f}%)."
                        )
                        logger.info(
                            f"allocation_decider: '{role_title}' — {margin_note}"
                        )
                else:
                    margin_note = (
                        f"Projected margin {chosen_margin:.1f}% is below the "
                        f"{target_margin:.1f}% target; no higher-margin bench "
                        f"candidate is available."
                    )
                    logger.warning(
                        f"allocation_decider: '{role_title}' — {margin_note}"
                    )
        elif decision.action_type == "hire":
            decision.projected_margin = 0.0

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
                other_names=[
                    c.get("full_name")
                    for c in candidates
                    if _candidate_id(c) != chosen_emp_id
                ],
            )
            if margin_note:
                decision.justification = f"{decision.justification} {margin_note}"

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

        decision.alternatives = _sanitize_alternatives(
            decision.alternatives, candidates, chosen_emp_id
        )

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
                "required_skills": required_skills,
                "hours_per_day": demand_hours,
                "allocated_hours_per_day": demand_hours,
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

def _build_plan_reasoning(decisions: List[Dict[str, Any]]) -> str:
    """Build an accurate plan summary directly from the decisions.

    The LLM is deliberately not asked to narrate the plan: local models
    repeatedly invented employee names, roles and hire counts that did not
    exist in the deterministic plan. This builder can only state facts that are
    already present in ``decisions``.
    """
    allocations = [
        d for d in decisions
        if d.get("action_type") == "allocate" and d.get("employee_id")
    ]
    trainings = [
        d for d in decisions
        if d.get("action_type") in ("train", "training")
    ]
    hirings = [
        d for d in decisions
        if d.get("action_type") in ("hire", "hiring")
    ]

    def _name(decision: Dict[str, Any]) -> str:
        name = str(decision.get("full_name") or "").strip()
        if name:
            return name
        employee_id = str(decision.get("employee_id") or "")
        return f"employee {employee_id[:8]}" if employee_id else "a bench employee"

    def _skills(decision: Dict[str, Any]) -> str:
        skills = _normalize_skill_list(
            decision.get("missing_skills")
            or decision.get("required_skills")
            or []
        )
        return ", ".join(skills) if skills else "the required skills"

    def _margin(decision: Dict[str, Any]) -> float:
        try:
            return float(decision.get("projected_margin") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _margin_text(decision: Dict[str, Any]) -> str:
        margin = _margin(decision)
        text = f"{margin:.1f}% projected margin"
        target = decision.get("target_margin")
        if target is not None:
            try:
                target_value = float(target)
            except (TypeError, ValueError):
                target_value = None
            if target_value is not None and margin < target_value:
                text += f" (below the {target_value:.1f}% target)"
        return text

    parts: List[str] = [
        f"Across {len(decisions)} demand(s), the plan proposes "
        f"{len(allocations)} direct allocation(s), {len(trainings)} training(s), "
        f"and {len(hirings)} external hire(s)."
    ]

    if allocations:
        details = "; ".join(
            f"{_name(d)} to {d.get('role_title') or 'the role'} at "
            f"{_margin_text(d)}"
            for d in allocations
        )
        parts.append(f"Direct allocations: {details}.")

    if trainings:
        details = "; ".join(
            f"{_name(d)} to {d.get('role_title') or 'the role'} with a "
            f"4-week plan covering {_skills(d)} at {_margin_text(d)}"
            for d in trainings
        )
        parts.append(f"Upskilling: {details}.")

    if hirings:
        details = "; ".join(
            f"{d.get('role_title') or 'a role'} ({_skills(d)})"
            for d in hirings
        )
        parts.append(
            f"External hires: {details}, because no bench candidate meets both "
            f"the required skills and the margin target."
        )

    return " ".join(parts)


def _build_plan_from_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministically compile a workforce plan directly from per-role decisions."""
    reallocations = []
    trainings = []
    hirings = []

    for d in decisions:
        act = d.get("action_type") or "allocate"
        if act == "allocate" and d.get("employee_id"):
            match_score = d.get("match_score")
            margin = d.get("projected_margin")
            reallocations.append({
                "employee_id": str(d.get("employee_id")),
                "target_project_id": str(d.get("target_project_id") or ""),
                "role": d.get("role_title") or "Engineer",
                "match_score": float(match_score) if match_score is not None else 0.0,
                "projected_margin": float(margin) if margin is not None else 0.0,
                "hours_per_day": float(d.get("hours_per_day") or 8),
                "action_type": "allocate",
                "justification": d.get("justification") or f"Direct allocation for {d.get('role_title', 'role')}.",
                "alternatives": d.get("alternatives") or [],
            })
        elif act in ("train", "training"):
            margin = d.get("projected_margin")
            trainings.append({
                "employee_id": str(d.get("employee_id") or ""),
                "target_skills": d.get("missing_skills") or ["Required Skills"],
                "duration_weeks": 4,
                "projected_margin": float(margin) if margin is not None else 0.0,
                "upskilling_path": d.get("upskilling_path") or "Structured training curriculum",
                "alternatives": d.get("alternatives") or [],
            })
        elif act in ("hire", "hiring"):
            required = _normalize_skill_list(
                d.get("required_skills") or d.get("missing_skills")
            )
            skills_text = ", ".join(required) if required else "the required skills"
            role = d.get("role_title") or "Engineer"
            # Never reuse the decider's internal-candidate justification for an
            # external hire: it can name a bench employee or quote a similarity
            # score, which is nonsensical in a recruitment recommendation.
            hirings.append({
                "role": role,
                "required_skills": required,
                "headcount": 1,
                "justification": (
                    f"External hire recommended for '{role}': no bench candidate "
                    f"covers {skills_text} within the target margin."
                ),
                "alternatives": d.get("alternatives") or [],
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
        "reasoning": _build_plan_reasoning(decisions),
        "revision_note": None,
        "created_at": datetime.utcnow().isoformat(),
    }


def forecast_planner_node(state: State) -> Dict[str, Any]:
    """Aggregate multi-role planning: synthesises reallocations, trainings, and hirings.

    The plan structure and audit summary are deterministic, compiled directly
    from the per-role decisions. An optional LLM narrative can be enabled with
    ``FORECAST_LLM_REASONING=true`` but is never required.

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

    # The plan structure and its audit summary are ALWAYS derived
    # deterministically from the per-role decisions. The LLM narrative is
    # opt-in only (FORECAST_LLM_REASONING=true) because local models repeatedly
    # invented employee names, roles and hire counts.
    plan_json = _build_plan_from_decisions(decisions)

    if _LLM_PLAN_REASONING:
        try:
            llm = LLMFactory.get_chat_model()
            allocation_margins = [
                float(d.get("projected_margin") or 0.0)
                for d in decisions
                if d.get("action_type") in ("allocate", "train")
            ]
            plan_facts = {
                "reallocations": len(plan_json.get("reallocations", [])),
                "trainings": len(plan_json.get("trainings", [])),
                "hirings": len(plan_json.get("hirings", [])),
                "roles": [
                    d.get("role_title")
                    for d in decisions
                    if d.get("role_title")
                ],
                "average_projected_margin_pct": (
                    round(sum(allocation_margins) / len(allocation_margins), 2)
                    if allocation_margins
                    else 0.0
                ),
            }
            sys_msg, human_msg = FinancialMatchPrompts.build_planning_prompt(
                matches=matches,
                employees_count=len(employees),
                open_roles_count=len(demands),
                plan_facts=plan_facts,
            )
            response = _invoke_with_retry(llm, [sys_msg, human_msg])
            parsed = LLMFactory.parse_json_response(response.content)
            reasoning = str(parsed.get("reasoning") or "").strip() if isinstance(parsed, dict) else ""
            if _is_usable_reasoning(reasoning):
                plan_json["reasoning"] = reasoning
            elif reasoning:
                logger.warning(
                    "forecast_planner_node: rejecting placeholder LLM reasoning; "
                    "using deterministic reasoning."
                )
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
                
                # First try to find by role_id / demand_id. Partially-filled
                # demands are still active work.
                active_demand_statuses = [
                    DemandStatusEnum.open,
                    DemandStatusEnum.partially_filled,
                ]
                if role_id_str:
                    dem_stmt = select(ProjectDemand).where(
                        ProjectDemand.id == uuid.UUID(role_id_str),
                        ProjectDemand.status.in_(active_demand_statuses),
                    )
                    dem_res = await db.execute(dem_stmt)
                    demand = dem_res.scalars().first()

                # Fallback to project_id and role
                if not demand and project_id_str:
                    dem_stmt = select(ProjectDemand).where(
                        ProjectDemand.project_id == uuid.UUID(project_id_str),
                        ProjectDemand.role == role,
                        ProjectDemand.status.in_(active_demand_statuses),
                    )
                    dem_res = await db.execute(dem_stmt)
                    demand = dem_res.scalars().first()

                # forecast_run_id is stored as str in state (MemorySaver serialisation).
                # Cast back to UUID for the FK column.
                forecast_run_uuid = uuid.UUID(forecast_run_id) if forecast_run_id else None

                # Load the employee with their allocations so the capacity
                # guard sees everything already committed/reserved.
                emp_stmt = (
                    select(Employee)
                    .options(selectinload(Employee.allocations))
                    .where(Employee.id == emp_id)
                )
                employee = (await db.execute(emp_stmt)).scalars().first()
                if employee is None:
                    logger.warning(
                        "execution_engine: employee %s not found — skipping", emp_id
                    )
                    continue

                def _safe_decimal(value, default: str = "8") -> Decimal:
                    try:
                        number = Decimal(str(value))
                        if number > 0:
                            return number
                    except Exception:
                        pass
                    return Decimal(default)

                def _safe_int(value, default: int = 1) -> int:
                    try:
                        return int(value)
                    except Exception:
                        return default

                hours = _safe_decimal(
                    (demand.hours_per_day if demand else None)
                    or reallocation.get("hours_per_day")
                    or reallocation.get("allocated_hours_per_day"),
                    "8",
                )

                from src.services.workforce_service import (
                    CapacityExceededError,
                    CapacityService,
                )

                try:
                    await CapacityService(db).assert_capacity(
                        emp_id, hours, employee=employee
                    )
                except CapacityExceededError as exc:
                    logger.error(
                        "execution_engine: capacity guard rejected %s for demand %s: %s",
                        emp_id,
                        role_id_str,
                        exc,
                    )
                    continue

                # Update demand counters only after the capacity guard passes.
                if demand:
                    demand_uuid = demand.id
                    needed = _safe_int(demand.headcount_needed, 0)
                    if needed > 0:
                        demand.headcount_needed = needed - 1
                        needed -= 1
                    required = max(_safe_int(demand.headcount_required, 1), 1)
                    demand.status = (
                        DemandStatusEnum.filled
                        if needed <= 0
                        else DemandStatusEnum.partially_filled
                        if needed < required
                        else DemandStatusEnum.open
                    )

                capacity = _safe_decimal(
                    getattr(employee, "daily_capacity_hours", None), "8"
                )
                capacity = min(capacity, Decimal("8.00"))
                pct = (hours / capacity * Decimal("100")).quantize(Decimal("0.01"))

                # Create allocation record
                allocation = Allocation(
                    employee_id=emp_id,
                    demand_id=demand_uuid,
                    role_on_project=role,
                    allocated_hours_per_day=hours,
                    allocation_pct=pct,
                    status=AllocationStatusEnum.active,
                    ai_match_score=match_score,
                    forecast_run_id=forecast_run_uuid,
                    start_date=date.today(),
                )
                db.add(allocation)
                await db.flush()
                # Force the next iteration to re-read the collection so the
                # capacity guard sees this newly committed allocation.
                db.expire(employee, ["allocations"])

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

                if demand is not None:
                    from src.services.skills_service import apply_project_skills

                    await apply_project_skills(db, emp_id, demand)

                logger.info(
                    "  execution_engine: allocated %s → demand %s role='%s' "
                    "%sh/day score=%.2f",
                    emp_id,
                    demand_uuid,
                    role,
                    hours,
                    match_score,
                )
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
