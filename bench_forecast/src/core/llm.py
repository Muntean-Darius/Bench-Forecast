"""LLM interaction logic, prompt templates, and Pydantic validation schemas.

This module strictly contains:
  - LLMFactory:            Unified factory for Groq and Ollama chat models.
  - Pydantic schemas:      Strongly-typed output contracts for LLM responses.
  - FinancialMatchPrompts: Prompt builders that inject financial constraints
                           (target_bill_rate, target_margin) alongside RAG
                           context so the LLM evaluates candidates through
                           both a semantic AND financial lens.

The LLM must classify every candidate into one of three action buckets:
    - allocate:  High similarity + healthy margin → direct allocation.
    - train:     Medium similarity + cheap resource → upskilling recommended.
    - hire:      Internal candidates lack skills OR are too expensive.

Separation of concerns:
    This module does NOT import LangGraph state or database models.
    It is consumed by nodes.py which owns graph execution logic.
"""

import json
import logging
import math
import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field, field_validator, model_validator

from src.core.config import Config

logger = logging.getLogger(__name__)


# ===========================================================================
# Pydantic Schemas — Strict output contracts for LLM JSON responses
# ===========================================================================

class AllocationDecision(BaseModel):
    """Strongly-typed LLM output for a single candidate allocation decision.

    The LLM MUST classify its strategy into one of:
        - allocate:  Direct allocation — high semantic similarity + healthy margin.
        - train:     Training allocation — medium similarity, profitable resource gap.
        - hire:      Hiring recommendation — no viable internal match.

    JSON contract expected from the LLM:
    {
        "action_type": "allocate" | "train" | "hire",
        "employee_id": "<uuid-string>" | null,
        "projected_margin": <float>,
        "justification": "<string>"
    }
    """

    action_type: Literal["allocate", "train", "hire"] = Field(
        ...,
        description=(
            "Decision bucket: "
            "'allocate' = direct assignment, "
            "'train' = assign + upskill, "
            "'hire' = open external recruitment"
        ),
    )
    employee_id: Optional[str] = Field(
        None,
        description=(
            "UUID of the selected employee. "
            "Must be null when action_type is 'hire'."
        ),
    )
    projected_margin: float = Field(
        ...,
        description=(
            "Projected gross margin % for this allocation. "
            "Formula: (bill_rate - cost_rate) / bill_rate * 100. "
            "Must be > 0 for 'allocate' and 'train'. Set to 0.0 for 'hire'."
        ),
    )
    justification: str = Field(
        ...,
        min_length=20,
        description=(
            "Narrative explanation citing specific skills, experience evidence, "
            "and financial reasoning for the chosen action_type."
        ),
    )
    upskilling_path: Optional[str] = Field(
        None,
        description=(
            "For action_type='train': a brief upskilling plan (technologies, "
            "estimated duration, resources). Null for 'allocate' and 'hire'."
        ),
    )

    @field_validator("projected_margin")
    @classmethod
    def margin_must_be_finite(cls, v: float) -> float:
        """Reject NaN and Inf values that would corrupt financial reporting."""
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"projected_margin must be a finite number, got {v}")
        return round(v, 2)

    @model_validator(mode="after")
    def hire_has_no_employee(self) -> "AllocationDecision":
        """When action_type is 'hire', employee_id must be null."""
        if self.action_type == "hire" and self.employee_id is not None:
            raise ValueError(
                "employee_id must be null when action_type is 'hire' "
                "(no internal candidate is being assigned)"
            )
        if self.action_type in ("allocate", "train") and not self.employee_id:
            raise ValueError(
                f"employee_id is required when action_type is '{self.action_type}'"
            )
        return self

    @model_validator(mode="after")
    def train_requires_upskilling_path(self) -> "AllocationDecision":
        """Training allocations must include an upskilling path."""
        if self.action_type == "train" and not self.upskilling_path:
            raise ValueError(
                "upskilling_path is required when action_type is 'train'"
            )
        return self


class RolePlanningOutput(BaseModel):
    """Aggregate planning output for a single ProjectRole across all candidates."""

    role_id: str = Field(..., description="ProjectRole UUID")
    role_title: str = Field(..., description="ProjectRole title")
    decision: AllocationDecision = Field(
        ..., description="Primary allocation decision for this role"
    )
    alternative_candidates: List[str] = Field(
        default_factory=list,
        description="List of employee_ids considered but not selected",
    )
    financial_rationale: str = Field(
        ...,
        min_length=20,
        description=(
            "Explanation of the financial analysis: which candidates were "
            "excluded for margin reasons, and why the selected candidate "
            "satisfies target_margin constraints."
        ),
    )


class WorkforcePlan(BaseModel):
    """Top-level output of the financial optimization planning node."""

    recommendation_id: str = Field(
        default_factory=lambda: __import__("uuid").uuid4().hex,
        description="Unique ID for this planning cycle",
    )
    role_decisions: List[RolePlanningOutput] = Field(
        default_factory=list,
        description="One decision per open ProjectRole processed",
    )
    overall_confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Aggregate confidence score across all role decisions",
    )
    strategic_summary: str = Field(
        ..., min_length=30,
        description="High-level workforce strategy rationale",
    )
    revision_note: Optional[str] = Field(
        None,
        description="Manager feedback that triggered this revision cycle (if applicable)",
    )
    created_at: str = Field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat(),
    )


# ---------------------------------------------------------------------------
# Legacy schemas (preserved for backward compatibility with existing nodes)
# ---------------------------------------------------------------------------

class MatchJustification(BaseModel):
    """Legacy skill-match justification schema (used by skill_matcher_node)."""

    employee_id: str = Field(..., description="Matched employee ID")
    demand_id: str = Field(..., description="Matched demand / role ID")
    match_score: float = Field(..., ge=0.0, le=1.0, description="Similarity score [0.0, 1.0]")
    reasoning: str = Field(..., min_length=10, description="LLM justification")
    missing_skills: List[str] = Field(default_factory=list)
    training_recommendation: Optional[str] = Field(None)
    rag_passages: List[str] = Field(default_factory=list)


# ===========================================================================
# LLM Factory
# ===========================================================================

class LLMFactory:
    """Unified factory for Groq and Ollama inference clients."""

    @staticmethod
    def get_chat_model(
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Any:
        """Get a chat model instance based on configuration.

        Args:
            provider:   'groq' or 'ollama' (defaults to Config.LLM_PROVIDER).
            model_name: Model identifier (defaults to configured model).

        Returns:
            ChatOllama or ChatGroq instance.

        Raises:
            ValueError: If provider is invalid or required credentials missing.
        """
        provider = provider or Config.LLM_PROVIDER

        if provider == "groq":
            if not Config.GROQ_API_KEY:
                raise ValueError(
                    "Groq provider selected but GROQ_API_KEY not set. "
                    "Either set the API key in .env or switch to LLM_PROVIDER=ollama"
                )
            logger.info(f"Initializing Groq client: model={Config.GROQ_MODEL}")
            return ChatGroq(
                api_key=Config.GROQ_API_KEY,
                model_name=model_name or Config.GROQ_MODEL,
                temperature=0.3,  # Lower temperature for deterministic financial reasoning
            )

        elif provider == "ollama":
            logger.info(f"Initializing Ollama client: model={Config.OLLAMA_MODEL}")
            return ChatOllama(
                base_url=Config.OLLAMA_BASE_URL,
                model=model_name or Config.OLLAMA_MODEL,
                temperature=0.3,
                format="json",
                num_predict=8192,
            )

        else:
            raise ValueError(f"Unknown LLM provider: '{provider}'")

    @staticmethod
    def parse_json_response(response_text: str) -> dict:
        """Extract and parse JSON from LLM response with layered fallback.

        Attempts (in order):
        1. Direct json.loads()
        2. Extract from ```json ... ``` markdown fences
        3. Extract first { ... } block via regex

        Args:
            response_text: Raw LLM output text.

        Returns:
            Parsed JSON dictionary.

        Raises:
            ValueError: If no valid JSON found in response.
        """
        text = response_text.strip()

        # 1. Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Markdown code fence extraction
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # 3. Greedy JSON object extraction
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(
            f"Could not extract valid JSON from LLM response. "
            f"First 300 chars: {text[:300]}"
        )


# ===========================================================================
# Financial Prompt Templates
# ===========================================================================

class FinancialMatchPrompts:
    """Prompt builders for financially-aware candidate evaluation.

    Every prompt injects:
    - target_bill_rate:  Hourly billing rate the client pays.
    - target_margin:     Minimum acceptable gross profit margin %.
    - top candidates:    Metadata from ChromaDB retrieval including each
                         candidate's hourly_cost_rate and projected_margin_pct.

    The LLM must classify each role into one of three action buckets and
    output strict JSON validated against AllocationDecision.
    """

    # -----------------------------------------------------------------------
    # System prompt (shared across all role evaluations)
    # -----------------------------------------------------------------------

    _SYSTEM_PROMPT = """\
You are an expert Workforce Financial Optimizer for a professional services firm.
Your job is to allocate bench employees to open project roles, balancing:
  1. Technical fit   — semantic skill match between CV and job description.
  2. Financial health — the allocation must respect the project's margin target.

You must classify every role into exactly ONE of three action types:

  • "allocate" — DIRECT ALLOCATION
    Trigger: candidate has HIGH semantic similarity (score >= 0.70) AND
    projected_margin >= target_margin.
    Result:  Employee is immediately assigned to the role.

  • "train"    — TRAINING ALLOCATION
    Trigger: candidate has MEDIUM similarity (0.45 <= score < 0.70) and is
    missing only 1-2 bridgeable skills, BUT their cost rate is low enough
    that projected_margin exceeds target_margin even after a training investment.
    Result:  Employee is assigned + a brief upskilling plan is prescribed.

  • "hire"     — EXTERNAL HIRING RECOMMENDATION
    Trigger: No internal candidate has the required skills OR every viable
    candidate has a cost_rate that would push projected_margin below target_margin.
    Result:  An external hire is recommended. employee_id must be null.

CRITICAL RULES:
  - Respond ONLY with a valid JSON object. NO markdown, NO explanations outside JSON.
  - The JSON MUST conform to this exact schema:
    {
      "action_type": "allocate" | "train" | "hire",
      "employee_id": "<uuid-string>" | null,
      "projected_margin": <float>,
      "justification": "<string, min 20 chars, cite specific skills/evidence>",
      "upskilling_path": "<string>" | null
    }
  - projected_margin = (bill_rate - cost_rate) / bill_rate * 100
  - upskilling_path is REQUIRED when action_type = "train", null otherwise.
  - employee_id MUST be null when action_type = "hire".
  - Do not invent employee IDs not present in the candidate list.
"""

    # -----------------------------------------------------------------------
    # Candidate evaluation for a single role
    # -----------------------------------------------------------------------

    @staticmethod
    def build_role_evaluation_prompt(
        role_title: str,
        role_description: str,
        target_bill_rate: float,
        target_margin: float,
        candidates: List[Dict[str, Any]],
        rag_passages: Optional[List[str]] = None,
    ) -> tuple:
        """Build a prompt for evaluating candidates against a single ProjectRole.

        Injects financial constraints and top candidate metadata so the LLM
        can perform both semantic AND financial filtering in a single pass.

        Args:
            role_title:        Title of the ProjectRole.
            role_description:  Unstructured job description.
            target_bill_rate:  Hourly billing rate (EUR/USD).
            target_margin:     Minimum acceptable margin % (e.g. 25.0).
            candidates:        List of dicts from RAGPipeline.retrieve_candidates().
                               Each must contain: employee_id, full_name, primary_role,
                               seniority, hourly_cost_rate, projected_margin_pct,
                               similarity_score, document_excerpt.
            rag_passages:      Optional additional context passages.

        Returns:
            Tuple of (SystemMessage, HumanMessage) for the LLM.
        """
        candidates_block = _format_candidates_block(candidates, target_bill_rate)

        rag_block = ""
        if rag_passages:
            joined = "\n\n".join(
                f"[Evidence {i+1}]: {p}" for i, p in enumerate(rag_passages[:3])
            )
            rag_block = f"\nRAG EVIDENCE (retrieved CV passages — cite these):\n{joined}\n"

        human_prompt = f"""Evaluate the following open role and select the best allocation strategy.

=== ROLE ===
Title:            {role_title}
Target Bill Rate: ${target_bill_rate:.2f}/h (hourly rate billed to client)
Target Margin:    {target_margin:.1f}% minimum gross profit margin required

Job Description:
{role_description}
{rag_block}
=== CANDIDATE POOL (financially pre-filtered: cost_rate < ${target_bill_rate:.2f}/h) ===
{candidates_block}
=== INSTRUCTIONS ===
Review each candidate's semantic similarity AND projected_margin_pct.
Select the action_type that best serves both technical fit and financial health.
If no candidate meets both criteria, recommend hiring.

Respond with ONLY the JSON object."""

        return (
            SystemMessage(content=FinancialMatchPrompts._SYSTEM_PROMPT),
            HumanMessage(content=human_prompt),
        )

    # -----------------------------------------------------------------------
    # Workforce planning (aggregate across all roles)
    # -----------------------------------------------------------------------

    @staticmethod
    def build_planning_prompt(
        matches: List[Dict[str, Any]],
        employees_count: int,
        open_roles_count: int,
        rejection_feedback: Optional[str] = None,
        previous_plan: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Build aggregate workforce planning prompt.

        Used by forecast_planner_node and revision_planner_node for multi-role
        planning. Injects financial awareness alongside skill-match data.

        Args:
            matches:            Skill match dicts from skill_matcher_node.
            employees_count:    Total bench employees available.
            open_roles_count:   Total open ProjectRoles.
            rejection_feedback: Manager's reason for rejecting a prior plan.
            previous_plan:      The rejected plan dict (for revision context).

        Returns:
            Tuple of (SystemMessage, HumanMessage).
        """
        system_prompt = """\
You are a workforce planning strategist generating financially-optimised allocation recommendations.

CRITICAL: You MUST respond with ONLY valid JSON (no markdown, no explanations, just raw JSON).

Your response MUST be a single JSON object with this exact structure:
{
    "reallocations": [
        {
            "employee_id": "E001",
            "target_project_id": "PRJ-001",
            "role": "Senior Developer",
            "match_score": 0.92,
            "projected_margin": 32.5,
            "action_type": "allocate"
        }
    ],
    "trainings": [
        {
            "employee_id": "E002",
            "target_skills": ["Kubernetes", "Go"],
            "duration_weeks": 4,
            "projected_margin": 28.0
        }
    ],
    "hirings": [
        {"role": "Data Scientist", "required_skills": ["Python", "ML"], "headcount": 2}
    ],
    "confidence_score": 0.82,
    "reasoning": "string — minimum 50 characters explaining overall strategy including financial rationale"
}

Financial Guidelines:
- ONLY include reallocations/trainings where projected_margin > 0
- Prioritise reallocations: match_score >= 0.70 AND margin > target_margin
- Include trainings for: 0.45 <= match_score < 0.70 AND resource is cost-effective
- Recommend hiring when no internal candidate satisfies both skill AND margin requirements
- confidence_score: 0.0-1.0 reflecting recommendation certainty
"""

        matches_json = json.dumps(matches[:5], indent=2)

        revision_block = ""
        if rejection_feedback and previous_plan:
            prev_json = json.dumps(previous_plan, indent=2)
            revision_block = f"""
⚠️  REVISION REQUEST — Manager REJECTED the previous plan:
\"{rejection_feedback}\"

PREVIOUS REJECTED PLAN:
{prev_json}

Generate a DIFFERENT plan addressing the manager's concerns. Do not repeat
the same reallocations or reasoning.
---
"""

        human_prompt = f"""Generate a financially-optimised workforce allocation plan:{revision_block}

TOP SKILL MATCHES (sorted by match_score):
{matches_json}

CONTEXT:
- Available bench employees: {employees_count}
- Open positions:            {open_roles_count}

Respond with ONLY the JSON object."""

        return SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)

    # -----------------------------------------------------------------------
    # Backward-compatible alias
    # -----------------------------------------------------------------------

    @staticmethod
    def build_matching_prompt(
        employee_profile: str,
        job_description: str,
        required_skills: List[str],
        employee_skills: List[str],
        rag_passages: Optional[List[str]] = None,
    ) -> tuple:
        """Backward-compatible alias that delegates to MatchPromptBuilder."""
        return MatchPromptBuilder.build_matching_prompt(
            employee_profile=employee_profile,
            job_description=job_description,
            required_skills=required_skills,
            employee_skills=employee_skills,
            rag_passages=rag_passages,
        )


# ---------------------------------------------------------------------------
# Legacy prompt builder (preserved for backward compatibility with nodes.py)
# ---------------------------------------------------------------------------

class MatchPromptBuilder:
    """Legacy prompt builder — preserved for backward compatibility with nodes.py.

    New code should use FinancialMatchPrompts which injects financial context.
    """

    @staticmethod
    def build_matching_prompt(
        employee_profile: str,
        job_description: str,
        required_skills: List[str],
        employee_skills: List[str],
        rag_passages: Optional[List[str]] = None,
    ) -> tuple:
        """Build a RAG-augmented skill-matching prompt."""
        system_prompt = """You are an expert talent manager analyzing skill matches between employees and open roles.

CRITICAL: You MUST respond with ONLY valid JSON (no markdown, no explanations, just raw JSON).

Your response MUST be a single JSON object with this exact structure:
{
    "employee_id": "string",
    "demand_id": "string",
    "match_score": 0.85,
    "reasoning": "string - minimum 20 characters, MUST cite specific evidence from the RAG CONTEXT if provided",
    "missing_skills": ["skill1", "skill2"],
    "training_recommendation": "string or null"
}

Guidelines:
- match_score: float 0.0-1.0 based on skill overlap and experience alignment
- reasoning: Reference specific technologies or phrases from RAG CONTEXT
- missing_skills: Skills from required_skills that the employee lacks
- training_recommendation: If score > 0.6, suggest 2-4 week training for top missing skill, else null
"""

        rag_block = ""
        if rag_passages:
            passages_text = "\n\n".join(
                f"[Passage {i+1}]: {p}" for i, p in enumerate(rag_passages)
            )
            rag_block = f"""
RAG CONTEXT (retrieved from employee profile corpus):
{passages_text}

---
"""

        human_prompt = f"""Analyze this skill match:

EMPLOYEE PROFILE:
{employee_profile}

Employee Current Skills: {', '.join(employee_skills)}

---

JOB DESCRIPTION:
{job_description}

Required Skills: {', '.join(required_skills)}
{rag_block}
Respond with ONLY the JSON object, no additional text."""

        return SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)

    @staticmethod
    def build_planning_prompt(
        matches: List[Dict[str, Any]],
        employees_count: int,
        open_roles_count: int,
        rejection_feedback: Optional[str] = None,
        previous_plan: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """Build prompt for allocation planning — delegates to FinancialMatchPrompts."""
        return FinancialMatchPrompts.build_planning_prompt(
            matches=matches,
            employees_count=employees_count,
            open_roles_count=open_roles_count,
            rejection_feedback=rejection_feedback,
            previous_plan=previous_plan,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _format_candidates_block(
    candidates: List[Dict[str, Any]],
    target_bill_rate: float,
) -> str:
    """Format candidate list as a readable table for prompt injection."""
    if not candidates:
        return "No financially viable candidates found in ChromaDB."

    lines = []
    for i, c in enumerate(candidates, 1):
        cost = float(c.get("hourly_cost_rate", 0))
        margin = float(c.get("projected_margin_pct", 0))
        viability = "✓ above target" if margin > 0 else "✗ negative margin"
        lines.append(
            f"{i}. [{c.get('employee_id', 'N/A')}] {c.get('full_name', 'Unknown')} "
            f"({c.get('seniority', '')} {c.get('primary_role', '')})"
        )
        lines.append(
            f"   Similarity: {c.get('similarity_score', 0):.2f} | "
            f"Cost Rate: ${cost:.2f}/h | "
            f"Projected Margin: {margin:.1f}% ({viability})"
        )
        excerpt = (c.get("document_excerpt") or "")[:200]
        if excerpt:
            lines.append(f"   CV Excerpt: {excerpt}")
        lines.append("")
    return "\n".join(lines)
