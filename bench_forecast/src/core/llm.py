"""LLM Factory for Groq and Ollama inference with JSON enforcement.

Provides unified interface to switch between cloud (Groq) and local (Ollama) LLM providers.
Includes prompt templates that:
1. Enforce JSON-only output from Llama-3
2. Inject ChromaDB-retrieved RAG passages into skill-matching prompts
3. Support revision planning with manager rejection feedback
"""

from typing import Any, Optional
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.config import Config


logger = logging.getLogger(__name__)


class LLMFactory:
    """Unified factory for Groq and Ollama inference clients."""

    @staticmethod
    def get_chat_model(provider: str = None, model_name: str = None) -> Any:
        """Get a chat model instance based on configuration.

        Args:
            provider: "groq" or "ollama" (defaults to Config.LLM_PROVIDER)
            model_name: Model identifier (defaults to configured model)

        Returns:
            ChatOllama or ChatGroq instance

        Raises:
            ValueError: If provider is invalid or required credentials missing
        """
        provider = provider or Config.LLM_PROVIDER

        if provider == "groq":
            if not Config.GROQ_API_KEY:
                raise ValueError(
                    "Groq provider selected but GROQ_API_KEY not set. "
                    "Either set the API key in .env or switch to LLM_PROVIDER=ollama"
                )
            logger.info(f"Initializing Groq client with model: {Config.GROQ_MODEL}")
            return ChatGroq(
                api_key=Config.GROQ_API_KEY,
                model_name=model_name or Config.GROQ_MODEL,
                temperature=0.7,
            )

        elif provider == "ollama":
            logger.info(f"Initializing Ollama client with model: {Config.OLLAMA_MODEL}")
            return ChatOllama(
                base_url=Config.OLLAMA_BASE_URL,
                model=model_name or Config.OLLAMA_MODEL,
                temperature=0.7,
            )

        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    @staticmethod
    def parse_json_response(response_text: str) -> dict:
        """Extract and parse JSON from LLM response with error handling.

        Args:
            response_text: Raw LLM output text

        Returns:
            Parsed JSON dictionary

        Raises:
            ValueError: If no valid JSON found in response
        """
        # Try direct parse first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks (e.g., ```json {...}```)
        import re
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try extracting any JSON object-like string
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not extract valid JSON from LLM response: {response_text[:200]}")


class MatchPromptBuilder:
    """Builds prompts for skill matching with JSON enforcement and RAG context injection.

    Enforces JSON-only output by:
    1. Including explicit JSON schema in system message
    2. Instructing LLM to output ONLY valid JSON
    3. Providing example JSON structure

    RAG-augments prompts by:
    4. Injecting retrieved ChromaDB passages as grounding evidence
    5. Requiring the LLM to cite specific passages in its reasoning
    """

    @staticmethod
    def build_matching_prompt(
        employee_profile: str,
        job_description: str,
        required_skills: list[str],
        employee_skills: list[str],
        rag_passages: Optional[list[str]] = None,
    ) -> tuple[SystemMessage, HumanMessage]:
        """Build a RAG-augmented prompt pair for skill matching with JSON enforcement.

        The RAG passages are retrieved from ChromaDB by querying the demand description
        against the indexed employee profile corpus. They are injected as grounding
        evidence so the LLM cites specific experience snippets rather than hallucinating.

        Args:
            employee_profile: Unstructured employee CV/experience text
            job_description: Unstructured job description
            required_skills: List of required skills
            employee_skills: List of employee skills
            rag_passages: Optional ChromaDB-retrieved passages most relevant to the demand

        Returns:
            Tuple of (system_message, human_message)
        """
        system_prompt = """You are an expert talent manager analyzing skill matches between employees and open roles.

CRITICAL: You MUST respond with ONLY valid JSON (no markdown, no explanations, just raw JSON).

Your response MUST be a single JSON object with this exact structure:
{
    "employee_id": "string",
    "demand_id": "string",
    "match_score": 0.85,
    "reasoning": "string - minimum 20 characters, MUST cite specific evidence from the RAG CONTEXT if provided",
    "missing_skills": ["skill1", "skill2"],
    "training_recommendation": "string or null - suggest training if gap exists"
}

Guidelines:
- match_score: float between 0.0 and 1.0 based on skill overlap, experience alignment, and RAG evidence
- reasoning: Reference specific technologies, projects, or phrases from the RAG CONTEXT to justify the score
- missing_skills: List skills from required_skills that employee lacks
- training_recommendation: If score > 0.6, suggest 2-4 week training for top missing skill, else null
"""

        # Build RAG context block if passages are available
        rag_block = ""
        if rag_passages:
            passages_text = "\n\n".join(
                f"[Passage {i+1}]: {p}" for i, p in enumerate(rag_passages)
            )
            rag_block = f"""
RAG CONTEXT (retrieved from employee profile corpus — cite these in your reasoning):
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
        matches: list[dict],
        employees_count: int,
        open_roles_count: int,
        rejection_feedback: Optional[str] = None,
        previous_plan: Optional[dict] = None,
    ) -> tuple[SystemMessage, HumanMessage]:
        """Build prompt for allocation planning (or revision) with JSON enforcement.

        When rejection_feedback and previous_plan are provided, this becomes a
        REVISION prompt: the LLM sees what the manager rejected and why, and must
        produce a meaningfully different plan that addresses those concerns.

        Args:
            matches: List of skill match dictionaries from matching node
            employees_count: Total bench employees available
            open_roles_count: Total open positions
            rejection_feedback: Manager's reason for rejecting the previous plan
            previous_plan: The plan the manager rejected (for context)

        Returns:
            Tuple of (system_message, human_message)
        """
        system_prompt = """You are a workforce planning strategist generating allocation recommendations.

CRITICAL: You MUST respond with ONLY valid JSON (no markdown, no explanations, just raw JSON).

Your response MUST be a single JSON object with this exact structure:
{
    "reallocations": [
        {"employee_id": "E001", "target_project_id": "PRJ-001", "role": "Senior Developer", "match_score": 0.92}
    ],
    "trainings": [
        {"employee_id": "E002", "target_skills": ["Kubernetes", "Go"], "duration_weeks": 4}
    ],
    "hirings": [
        {"role": "Data Scientist", "required_skills": ["Python", "ML"], "headcount": 2}
    ],
    "confidence_score": 0.82,
    "reasoning": "string - minimum 50 characters explaining the overall strategy"
}

Guidelines:
- Prioritize reallocations where match_score >= 0.75 (low risk)
- Include trainings for mid-tier matches (0.6-0.75) if skill gaps are bridgeable
- Recommend hiring only when supply gap cannot be filled by reallocation or training
- confidence_score: 0.0-1.0 reflecting recommendation certainty
- reasoning: Strategic rationale including risk mitigation and timeline
"""

        matches_json = json.dumps(matches[:5], indent=2)  # Top 5 to avoid token overflow

        # Build revision context block if manager rejected the previous plan
        revision_block = ""
        if rejection_feedback and previous_plan:
            prev_json = json.dumps(previous_plan, indent=2)
            revision_block = f"""
⚠️  REVISION REQUEST — The manager REJECTED the previous plan with this feedback:
"{rejection_feedback}"

PREVIOUS REJECTED PLAN:
{prev_json}

You MUST generate a DIFFERENT plan that directly addresses the manager's concerns.
Do not repeat the same reallocations or reasoning. Adjust employee assignments,
consider alternative candidates, or propose training/hiring where previously you
suggested reallocation.
---
"""

        human_prompt = f"""Based on these skill matches, generate a workforce allocation plan:{revision_block}

TOP MATCHES (sorted by match_score):
{matches_json}

CONTEXT:
- Available bench employees: {employees_count}
- Open positions: {open_roles_count}

Generate a comprehensive recommendation balancing:
1. Quick reallocation wins (minimal training)
2. Training investments for high-potential mismatches
3. Hiring gaps that cannot be filled internally

Respond with ONLY the JSON object, no additional text."""

        return SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)
