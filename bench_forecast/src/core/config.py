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
    # Embedding model served by Ollama — used by the Phase 2 ingestion pipeline.
    # Pull with: ollama pull nomic-embed-text
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

    # -----------------------------------------------------------------------
    # PostgreSQL Configuration (migrated from SQLite)
    # -----------------------------------------------------------------------
    USE_POSTGRES: bool = os.getenv("USE_POSTGRES", "false").lower() in ("true", "1", "yes")
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

    # -----------------------------------------------------------------------
    # ChromaDB server-mode connection (docker-compose service "chromadb")
    # -----------------------------------------------------------------------
    CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
    CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8200"))

    # -----------------------------------------------------------------------
    # MinIO / S3 object storage
    # -----------------------------------------------------------------------
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "bench-docs")
    MINIO_USE_SSL: bool = os.getenv("MINIO_USE_SSL", "false").lower() == "true"

    @classmethod
    def get_postgres_dsn(cls) -> str:
        """Build a SQLAlchemy-compatible PostgreSQL DSN (sync, psycopg2)."""
        return (
            f"postgresql+psycopg2://{cls.POSTGRES_USER}:{cls.POSTGRES_PASSWORD}"
            f"@{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}"
        )

    @classmethod
    def get_async_postgres_dsn(cls) -> str:
        """Build a SQLAlchemy-compatible PostgreSQL DSN (async, asyncpg)."""
        return (
            f"postgresql+asyncpg://{cls.POSTGRES_USER}:{cls.POSTGRES_PASSWORD}"
            f"@{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}"
        )
