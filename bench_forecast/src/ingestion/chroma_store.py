"""ChromaDB upsert logic for the employee_profiles collection.

This module is the authoritative writer to ChromaDB in Phase 2.
It connects to ChromaDB in HTTP client mode (pointing at the Docker
container on CHROMA_HOST:CHROMA_PORT) and upserts pre-computed embeddings
together with their chunk text and employee metadata.

Collection schema (matches plan.md §5)::

    Collection: "employee_profiles"
    Document:   chunk text (str)
    Embedding:  768-dim float list from nomic-embed-text
    Metadata: {
        "employee_id":           str  — UUID of the employee (FK reference),
        "employee_name":         str  — "<first> <last>",
        "document_id":           str  — UUID of the documents row,
        "chunk_index":           int  — 0-based chunk position in the document,
        "document_type":         str  — "cv" | "certification" | ...,
        "experience_level":      str  — "junior" | "mid" | "senior" | ...,
        "internal_hourly_cost":  float — from employee_costs.internal_hourly_cost
                                         (for RAGPipeline financial pre-filter),
    }

Usage::

    from src.ingestion.chroma_store import ChromaIngestionStore

    store = ChromaIngestionStore()
    chroma_doc_id = store.upsert_document_chunks(
        document_id="uuid",
        employee_id="uuid",
        employee_name="Jane Doe",
        document_type="cv",
        experience_level="senior",
        internal_hourly_cost=75.0,
        chunks=["chunk text …"],
        embeddings=[[0.1, 0.2, …]],
    )
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "employee_profiles"


class ChromaIngestionStore:
    """Thin wrapper around chromadb.HttpClient for ingestion writes.

    Reads CHROMA_HOST and CHROMA_PORT from Config; falls back to the
    persistent local path (CHROMA_PERSIST_DIR) when the server is
    unreachable (useful for unit tests / offline dev).

    Args:
        collection_name: ChromaDB collection name. Defaults to
                         ``"employee_profiles"``.
    """

    def __init__(self, collection_name: str = _COLLECTION_NAME) -> None:
        import chromadb  # noqa: PLC0415

        from src.core.config import Config  # noqa: PLC0415

        self._collection_name = collection_name

        # Prefer HTTP client (docker-compose service) for consistency across
        # the API and seed scripts.  Fall back to PersistentClient for local dev.
        try:
            self._client = chromadb.HttpClient(
                host=Config.CHROMA_HOST,
                port=Config.CHROMA_PORT,
            )
            # Smoke-test: will raise if the server isn't running
            self._client.heartbeat()
            logger.info(
                "ChromaIngestionStore: connected via HttpClient to %s:%s",
                Config.CHROMA_HOST,
                Config.CHROMA_PORT,
            )
        except Exception as exc:
            logger.warning(
                "ChromaDB HTTP server not reachable (%s). "
                "Falling back to PersistentClient at '%s'.",
                exc,
                Config.CHROMA_PERSIST_DIR,
            )
            import os  # noqa: PLC0415
            os.makedirs(Config.CHROMA_PERSIST_DIR, exist_ok=True)
            self._client = chromadb.PersistentClient(path=Config.CHROMA_PERSIST_DIR)

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaIngestionStore: collection '%s' ready (%d existing docs)",
            collection_name,
            self._collection.count(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert_document_chunks(
        self,
        *,
        document_id: str,
        employee_id: str,
        employee_name: str,
        document_type: str,
        experience_level: str,
        internal_hourly_cost: float,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> str:
        """Upsert all chunks of a single document into ChromaDB.

        Each chunk gets a deterministic ID based on the document UUID and
        chunk index so that re-ingesting the same document is idempotent
        (upsert semantics).

        Args:
            document_id:          UUID of the ``documents`` row (str).
            employee_id:          UUID of the employee (str).
            employee_name:        Human-readable name for metadata.
            document_type:        E.g. ``"cv"``, ``"certification"``.
            experience_level:     E.g. ``"senior"``.
            internal_hourly_cost: Loaded cost in dollars/hour — stored in
                                  metadata for the RAGPipeline financial filter.
            chunks:               Ordered list of text chunks.
            embeddings:           Parallel list of 768-dim float vectors.

        Returns:
            The *base* ChromaDB document ID (``"<document_id>_chunk_0"``'s
            prefix without the chunk index).  Store this in
            ``documents.chroma_doc_id``.

        Raises:
            ValueError: If ``chunks`` and ``embeddings`` lengths do not match
                        or if either list is empty.
        """
        if not chunks:
            raise ValueError("chunks list must not be empty")
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
                "must have the same length"
            )

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        base_id = str(document_id)  # ensure string

        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{base_id}_chunk_{i}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append(
                {
                    "employee_id": str(employee_id),
                    "employee_name": employee_name,
                    "document_id": base_id,
                    "chunk_index": i,
                    "document_type": document_type,
                    "experience_level": experience_level,
                    "internal_hourly_cost": float(internal_hourly_cost),
                }
            )

        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        logger.info(
            "ChromaDB upsert complete: document_id=%s employee=%s chunks=%d",
            base_id,
            employee_name,
            len(chunks),
        )
        return base_id

    def delete_document_chunks(self, document_id: str) -> None:
        """Remove all chunks belonging to *document_id* from ChromaDB.

        Used when re-ingesting a CV to replace the previous version's chunks.
        The caller should set the previous document's ``is_current=False``
        in PostgreSQL before calling this.

        Args:
            document_id: UUID of the ``documents`` row whose chunks to delete.
        """
        results = self._collection.get(
            where={"document_id": str(document_id)},
            include=[],
        )
        ids_to_delete: list[str] = results.get("ids", [])

        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
            logger.info(
                "ChromaDB: deleted %d chunks for document_id=%s",
                len(ids_to_delete),
                document_id,
            )
        else:
            logger.debug(
                "ChromaDB: no chunks found for document_id=%s — nothing deleted",
                document_id,
            )

    def collection_count(self) -> int:
        """Return the total number of chunk vectors currently in the collection."""
        return self._collection.count()

