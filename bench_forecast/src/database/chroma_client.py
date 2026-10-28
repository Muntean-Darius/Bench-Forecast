"""Single source of truth for the ChromaDB client and the employee collection.

Every reader and writer in the app goes through this module so there is
exactly ONE client, ONE persistence path and ONE collection:

  * the seeder (``scripts/seed_simulation.py``)
  * the agent's semantic retrieval (``VectorStoreManager``)
  * the RAG pipeline financial pre-filter (``RAGPipeline``)
  * CV-upload ingestion (``ChromaIngestionStore``)

Transport decision: we use Chroma's **embedded PersistentClient** at a single
absolute path (``Config.CHROMA_PERSIST_DIR``). The previous code mixed an
embedded client (agent/seed) with an ``HttpClient`` to the Docker Chroma server
(CV upload), which meant an uploaded CV could land in a different store than the
agent read from. Server mode is intentionally not used; if the project later
needs multiple workers, swap the client here in one place.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb

from src.core.config import Config

logger = logging.getLogger(__name__)

# The one-and-only employee collection name.
EMPLOYEE_COLLECTION = "employee_profiles"

_client: Any = None


def get_chroma_client() -> Any:
    """Return the singleton embedded ChromaDB client."""
    global _client
    if _client is None:
        path = Config.CHROMA_PERSIST_DIR
        Path(path).mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=path)
        logger.info("ChromaDB PersistentClient initialised at %s", path)
    return _client


def get_employee_collection() -> Any:
    """Return the shared ``employee_profiles`` collection (created if missing)."""
    return get_chroma_client().get_or_create_collection(
        name=EMPLOYEE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def reset_employee_collection() -> Any:
    """Delete and recreate the employee collection (used by the seeder)."""
    client = get_chroma_client()
    try:
        client.delete_collection(EMPLOYEE_COLLECTION)
        logger.info("ChromaDB: deleted collection '%s'", EMPLOYEE_COLLECTION)
    except Exception:
        # Collection may not exist yet — that's fine.
        pass
    return client.get_or_create_collection(
        name=EMPLOYEE_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def get_embedding_function() -> Any:
    """Return the Ollama embedding function used for all embedding calls."""
    from chromadb.utils import embedding_functions  # noqa: PLC0415

    return embedding_functions.OllamaEmbeddingFunction(
        url=f"{Config.OLLAMA_BASE_URL}/api/embeddings",
        model_name=Config.EMBEDDING_MODEL,
    )
