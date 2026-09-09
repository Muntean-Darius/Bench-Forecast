"""LLM Factory for Groq and Ollama inference with JSON enforcement.

Provides unified interface to switch between cloud (Groq) and local (Ollama) LLM providers.
Includes prompt templates that enforce JSON-only output from Llama-3.
"""

from typing import Any, Literal
import json
import logging
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

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
    """Builds prompts for skill matching with JSON enforcement.
    
    Enforces JSON-only output by:
    1. Including explicit JSON schema in system message
    2. Instructing LLM to output ONLY valid JSON
    3. Providing example JSON structure
    """

    @staticmethod
    def build_matching_prompt(
        employee_profile: str,
        job_description: str,
        required_skills: list[str],
        employee_skills: list[str],
    ) -> tuple[SystemMessage, HumanMessage]:
        """Build a prompt pair for skill matching with JSON enforcement.
        
        Args:
            employee_profile: Unstructured employee CV/experience text
            job_description: Unstructured job description
            required_skills: List of required skills
            employee_skills: List of employee skills
            
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
    "reasoning": "string - minimum 20 characters explaining the match",
    "missing_skills": ["skill1", "skill2"],
    "training_recommendation": "string or null - suggest training if gap exists"
}

Guidelines:
- match_score: float between 0.0 and 1.0 based on skill overlap and experience alignment
- reasoning: Specific examples from the profile and job description justifying the score
- missing_skills: List skills from required_skills that employee lacks
- training_recommendation: If score > 0.6, suggest 2-4 week training for top missing skill, else null
"""

        human_prompt = f"""Analyze this skill match:

EMPLOYEE PROFILE:
{employee_profile}

Employee Current Skills: {', '.join(employee_skills)}

---

JOB DESCRIPTION:
{job_description}

Required Skills: {', '.join(required_skills)}

---

Respond with ONLY the JSON object, no additional text."""

        return SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)

    @staticmethod
    def build_planning_prompt(
        matches: list[dict],
        employees_count: int,
        open_roles_count: int,
    ) -> tuple[SystemMessage, HumanMessage]:
        """Build prompt for allocation planning with JSON enforcement.
        
        Args:
            matches: List of skill match dictionaries from matching node
            employees_count: Total bench employees available
            open_roles_count: Total open positions
            
        Returns:
            Tuple of (system_message, human_message)
        """
        
        system_prompt = """You are a workforce planning strategist generating allocation recommendations.

CRITICAL: You MUST respond with ONLY valid JSON (no markdown, no explanations, just raw JSON).

Your response MUST be a single JSON object with this exact structure:
{
    "reallocations": [
        {"employee_id": "E001", "demand_id": "D001", "role": "Senior Developer", "match_score": 0.92}
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

        matches_json = json.dumps(matches[:5], indent=2)  # Limit to 5 best matches to avoid token overflow
        
        human_prompt = f"""Based on these skill matches, generate a workforce allocation plan:

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


