"""ChromaDB upsert logic for the employee_profiles collection.

Writes go through the single shared embedded client in
``src/database/chroma_client.py`` so CV-upload ingestion lands in the SAME
store the agent reads from. It upserts pre-computed embeddings together with
their chunk text and employee metadata.

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
        # Use the single shared client/collection so CV-upload ingestion writes
        # to the SAME store the agent reads from (see chroma_client.py).
        from src.database.chroma_client import get_employee_collection

        self._collection_name = collection_name
        self._collection = get_employee_collection()
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
                    # Canonical keys shared with the seeder / agent retrievers:
                    "id": str(employee_id),
                    "name": employee_name,
                    "employee_name": employee_name,
                    "document_id": base_id,
                    "chunk_index": i,
                    "document_type": document_type,
                    "experience_level": experience_level,
                    # `hourly_cost_rate` is what RAGPipeline's financial pre-filter
                    # queries on; keep internal_hourly_cost for backward compat.
                    "internal_hourly_cost": float(internal_hourly_cost),
                    "hourly_cost_rate": float(internal_hourly_cost),
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

    def delete_employee_chunks(self, employee_id: str) -> None:
        """Remove all existing chunks belonging to employee_id from ChromaDB.

        Used when re-ingesting an updated CV to ensure only the latest embeddings exist.
        """
        try:
            results = self._collection.get(
                where={"employee_id": str(employee_id)},
                include=[],
            )
            ids_to_delete: list[str] = results.get("ids", [])
            if ids_to_delete:
                self._collection.delete(ids=ids_to_delete)
                logger.info(
                    "ChromaDB: deleted %d previous chunks for employee_id=%s",
                    len(ids_to_delete),
                    employee_id,
                )
        except Exception as exc:
            logger.warning(
                "ChromaDB: could not delete old chunks for employee_id=%s: %s",
                employee_id,
                exc,
            )

    def collection_count(self) -> int:
        """Return the total number of chunk vectors currently in the collection."""
        return self._collection.count()

