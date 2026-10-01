"""Configuration management for Bench Forecast backend.

Handles environment variables with sensible defaults:
- LLM provider toggle (groq or ollama, defaults to ollama)
- PostgreSQL database connection parameters
- ChromaDB persistence directory
- LangSmith tracing configuration
- FastAPI server settings
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

    # -----------------------------------------------------------------------
    # LLM Configuration
    # -----------------------------------------------------------------------
    LLM_PROVIDER: Literal["groq", "ollama"] = os.getenv("LLM_PROVIDER", "ollama")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama2")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # -----------------------------------------------------------------------
    # PostgreSQL Configuration (migrated from SQLite)
    # -----------------------------------------------------------------------
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "bench_forecast")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")

    # -----------------------------------------------------------------------
    # Legacy SQLite (kept for backward compat with existing mock data tooling)
    # -----------------------------------------------------------------------
    SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", "./data/mock_db.sqlite")

    # -----------------------------------------------------------------------
    # Vector Store Configuration
    # -----------------------------------------------------------------------
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_store")

    # -----------------------------------------------------------------------
    # LangSmith Tracing Configuration
    # Standard LangChain environment variables — read automatically by all
    # LangChain/LangGraph internals. Set here for centralised documentation.
    # -----------------------------------------------------------------------
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "bench-forecast")
    LANGCHAIN_ENDPOINT: str = os.getenv(
        "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
    )

    # -----------------------------------------------------------------------
    # Legacy Phoenix Tracing Configuration (kept for backward compat)
    # -----------------------------------------------------------------------
    PHOENIX_COLLECTOR_ENDPOINT: str = os.getenv(
        "PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006"
    )
    ENABLE_PHOENIX: bool = os.getenv("ENABLE_PHOENIX", "false").lower() == "true"

    # -----------------------------------------------------------------------
    # FastAPI Configuration
    # -----------------------------------------------------------------------
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

        if cls.LANGCHAIN_TRACING_V2.lower() == "true" and not cls.LANGCHAIN_API_KEY:
            import logging
            logging.getLogger(__name__).warning(
                "LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY is not set — "
                "LangSmith traces will not be recorded."
            )

    @classmethod
    def get_postgres_dsn(cls) -> str:
        """Build a SQLAlchemy-compatible PostgreSQL DSN from config fields."""
        return (
            f"postgresql+psycopg2://{cls.POSTGRES_USER}:{cls.POSTGRES_PASSWORD}"
            f"@{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}"
        )
