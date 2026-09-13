"""Configuration management for Bench Forecast backend.

Handles environment variables with sensible defaults:
- LLM provider toggle (groq or ollama, defaults to ollama)
- Database paths
- Phoenix tracing endpoint
"""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

# Load .env from the bench_forecast directory (one level above src/)
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=_env_path)


class Config:
    """Centralized configuration from .env and defaults."""

    # LLM Configuration
    LLM_PROVIDER: Literal["groq", "ollama"] = os.getenv("LLM_PROVIDER", "ollama")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama2")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # Database Configuration
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/mock_db.sqlite")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_store")

    # Phoenix Tracing Configuration
    PHOENIX_COLLECTOR_ENDPOINT: str = os.getenv(
        "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006"
    )
    ENABLE_PHOENIX: bool = os.getenv("ENABLE_PHOENIX", "false").lower() == "true"

    # FastAPI Configuration
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration at startup.
        
        Raises:
            ValueError: If required environment variables are missing or invalid.
        """
        if cls.LLM_PROVIDER == "groq":
            if not cls.GROQ_API_KEY:
                raise ValueError(
                    "GROQ_API_KEY is required when LLM_PROVIDER=groq. "
                    "Set it in .env or switch to LLM_PROVIDER=ollama for local inference."
                )
        elif cls.LLM_PROVIDER not in ("groq", "ollama"):
            raise ValueError(
                f"LLM_PROVIDER must be 'groq' or 'ollama', got '{cls.LLM_PROVIDER}'"
            )
