"""RAG (Retrieval-Augmented Generation) pipeline for employee CV vectorization.

Responsibilities:
1. CV Ingestion:   Reads raw employee CV text and chunks it into passages.
2. Metadata Injection: Stores employee_id + hourly_cost_rate alongside every
   ChromaDB document so the retrieval filter can enforce financial constraints.
3. Financially-Filtered Retrieval: When querying for a ProjectRole, pre-filters
   ChromaDB via a `where` clause that excludes employees whose hourly_cost_rate
   is >= target_bill_rate (which would produce zero or negative margin).

Architectural note:
    This module does NOT import LangGraph state or LLM logic — it is a pure
    data-access layer consumed by nodes.py. Separation of concerns is enforced.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import chromadb

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COLLECTION = "employee_cvs"
DEFAULT_CHUNK_SIZE = 512    # Characters per text chunk
DEFAULT_CHUNK_OVERLAP = 64  # Overlapping characters between consecutive chunks


# ---------------------------------------------------------------------------
# Text chunking utility
# ---------------------------------------------------------------------------

def _chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """Split raw CV text into overlapping character-level chunks.

    Paragraph-aware splitting (on double newlines) is applied first so we
    avoid cutting sentences mid-word wherever possible.

    Args:
        text:       Raw CV content.
        chunk_size: Maximum characters per chunk.
        overlap:    Characters shared between consecutive chunks.

    Returns:
        List of non-empty text chunks.
    """
    if not text or not text.strip():
        return []

    # Try paragraph-aware splitting first
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: List[str] = []
    buffer = ""

    for para in paragraphs:
        if len(buffer) + len(para) + 2 <= chunk_size:
            buffer = (buffer + "\n\n" + para).strip() if buffer else para
        else:
            if buffer:
                chunks.append(buffer)
            # Para itself may exceed chunk_size — slide over it
            if len(para) <= chunk_size:
                buffer = para
            else:
                start = 0
                while start < len(para):
                    chunks.append(para[start: start + chunk_size])
                    start += chunk_size - overlap
                buffer = ""

    if buffer:
        chunks.append(buffer)

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------

class RAGPipeline:
    """ChromaDB-backed RAG pipeline with financial metadata filtering.

    Each document chunk stored in ChromaDB carries metadata:
        employee_id       (str)   — links back to the PostgreSQL Employee row
        hourly_cost_rate  (float) — copied from Employee for pre-filter queries
        full_name         (str)   — for human-readable logging
        primary_role      (str)   — role label
        seniority         (str)   — seniority band
        bench_start_date  (str)   — ISO date string
        chunk_index       (int)   — positional index within the CV
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        """Initialise the ChromaDB client and collection.

        Args:
            persist_dir:      Path to the ChromaDB persistence directory.
                              Defaults to <project_root>/data/chroma_store.
            collection_name:  ChromaDB collection name.
        """
        if persist_dir is None:
            base_dir = Path(__file__).resolve().parents[2]
            self.persist_dir = str(base_dir / "data" / "chroma_store")
        else:
            self.persist_dir = persist_dir

        os.makedirs(self.persist_dir, exist_ok=True)

        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"RAGPipeline initialised: collection='{collection_name}' "
            f"docs={self.collection.count()} persist_dir={self.persist_dir}"
        )

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_employee_cv(
        self,
        employee_id: str,
        cv_text: str,
        hourly_cost_rate: float,
        full_name: str = "",
        primary_role: str = "",
        seniority: str = "",
        bench_start_date: str = "",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> int:
        """Chunk a CV and upsert all chunks into ChromaDB with financial metadata.

        The hourly_cost_rate is stored as a float in ChromaDB metadata so that
        the retrieval layer can apply a `where` pre-filter to exclude employees
        whose cost exceeds a role's billing rate.

        Args:
            employee_id:       UUID string of the Employee record.
            cv_text:           Raw CV / profile text.
            hourly_cost_rate:  Employee's internal cost per hour (EUR/USD).
            full_name:         Display name for logging.
            primary_role:      Job role label.
            seniority:         Seniority band.
            bench_start_date:  ISO date string of bench start.
            chunk_size:        Characters per chunk.
            overlap:           Overlap characters between chunks.

        Returns:
            Number of chunks indexed.

        Raises:
            ValueError: If employee_id is empty.
        """
        if not employee_id:
            raise ValueError("employee_id must not be empty")
        if not cv_text or not cv_text.strip():
            logger.warning(f"Empty CV for employee {employee_id} — skipping ingestion")
            return 0

        chunks = _chunk_text(cv_text, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            logger.warning(f"CV produced 0 chunks for employee {employee_id}")
            return 0

        ids: List[str] = []
        documents: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for idx, chunk in enumerate(chunks):
            chunk_id = f"{employee_id}__chunk_{idx}"
            ids.append(chunk_id)
            documents.append(chunk)
            metadatas.append(
                {
                    # -------------------------------------------------------
                    # Financial metadata — CRITICAL for pre-filter queries
                    # ChromaDB requires numeric metadata to be stored as float
                    # -------------------------------------------------------
                    "employee_id": str(employee_id),
                    "hourly_cost_rate": float(hourly_cost_rate),
                    # -------------------------------------------------------
                    # Descriptive metadata for result enrichment
                    # -------------------------------------------------------
                    "full_name": str(full_name),
                    "primary_role": str(primary_role),
                    "seniority": str(seniority),
                    "bench_start_date": str(bench_start_date),
                    "chunk_index": int(idx),
                    "total_chunks": int(len(chunks)),
                }
            )

        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(
            f"Ingested {len(ids)} chunks for employee {employee_id} "
            f"('{full_name}', cost_rate={hourly_cost_rate}/h)"
        )
        return len(ids)

    def ingest_from_employee_dict(self, employee: Dict[str, Any]) -> int:
        """Convenience wrapper to ingest a CV from an employee dict.

        Expected keys:
            id, cv_text (or profile_text), hourly_cost_rate (or cost_rate),
            full_name (or name), primary_role, seniority, bench_start_date.

        Args:
            employee: Dictionary with employee fields.

        Returns:
            Number of chunks indexed.
        """
        emp_id = str(employee.get("id", ""))
        cv_text = employee.get("cv_text") or employee.get("profile_text", "")
        cost_rate = float(
            employee.get("hourly_cost_rate")
            or employee.get("cost_rate")
            or 0.0
        )
        return self.ingest_employee_cv(
            employee_id=emp_id,
            cv_text=cv_text,
            hourly_cost_rate=cost_rate,
            full_name=str(employee.get("full_name") or employee.get("name", "")),
            primary_role=str(employee.get("primary_role", "")),
            seniority=str(employee.get("seniority", "")),
            bench_start_date=str(
                employee.get("bench_start_date") or employee.get("available_from", "")
            ),
        )

    def bulk_ingest(self, employees: List[Dict[str, Any]]) -> int:
        """Ingest multiple employees. Returns total chunks indexed."""
        total = 0
        for emp in employees:
            try:
                total += self.ingest_from_employee_dict(emp)
            except Exception:
                logger.exception(
                    f"Failed to ingest employee {emp.get('id', 'UNKNOWN')} — skipping"
                )
        logger.info(
            f"Bulk ingestion complete: {total} chunks for {len(employees)} employees"
        )
        return total

    # ------------------------------------------------------------------
    # Financially-Filtered Retrieval
    # ------------------------------------------------------------------

    def retrieve_candidates(
        self,
        role_description: str,
        target_bill_rate: float,
        k: int = 5,
        additional_where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve top-k semantically similar employees that are financially viable.

        Financial pre-filter:
            Excludes any ChromaDB document whose `hourly_cost_rate` metadata
            field is >= `target_bill_rate`. This guarantees every returned
            candidate would produce a positive margin if allocated to the role.

            ChromaDB `where` clause used:
                {"hourly_cost_rate": {"$lt": target_bill_rate}}

        This pre-filter is applied BEFORE the vector similarity search, so the
        k nearest neighbours are drawn exclusively from the financially viable
        subset — ensuring we never surface a candidate that would break margins.

        Args:
            role_description:   Raw job description text for the ProjectRole.
            target_bill_rate:   Hourly billing rate for the role (EUR/USD).
                                Employees with cost_rate >= this are excluded.
            k:                  Number of results to return.
            additional_where:   Optional extra ChromaDB `where` filters to AND
                                with the financial filter.

        Returns:
            List of candidate dicts with fields:
                employee_id, full_name, primary_role, seniority,
                hourly_cost_rate, bench_start_date, similarity_score,
                projected_margin_pct, document_excerpt, chunk_index.

        Raises:
            ValueError: If target_bill_rate <= 0.
        """
        if target_bill_rate <= 0:
            raise ValueError(f"target_bill_rate must be > 0, got {target_bill_rate}")

        collection_size = self.collection.count()
        if collection_size == 0:
            logger.warning("ChromaDB collection is empty — returning no candidates")
            return []

        # ----------------------------------------------------------------
        # Financial pre-filter: cost_rate STRICTLY LESS THAN bill_rate
        # This ensures projected margin > 0% for all returned candidates
        # ----------------------------------------------------------------
        financial_filter: Dict[str, Any] = {
            "hourly_cost_rate": {"$lt": float(target_bill_rate)}
        }

        if additional_where:
            # Merge with an $and compound operator
            where_clause: Dict[str, Any] = {
                "$and": [financial_filter, additional_where]
            }
        else:
            where_clause = financial_filter

        n_results = min(k, collection_size)

        try:
            results = self.collection.query(
                query_texts=[role_description],
                n_results=n_results,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            # ChromaDB raises when the filtered subset has 0 documents
            logger.warning(
                f"ChromaDB query failed (possibly no documents pass filter "
                f"cost_rate < {target_bill_rate}): {exc}"
            )
            return []

        candidates: List[Dict[str, Any]] = []

        if not results or not results.get("ids") or not results["ids"][0]:
            logger.info(
                f"No candidates found with cost_rate < {target_bill_rate} "
                f"for role: {role_description[:80]}..."
            )
            return candidates

        ids = results["ids"][0]
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # Deduplicate by employee_id (multiple chunks per employee may be returned)
        seen_employees: set = set()

        for i, doc_id in enumerate(ids):
            meta = metas[i] if i < len(metas) else {}
            emp_id = meta.get("employee_id", doc_id)

            # Keep only the highest-similarity chunk per employee
            if emp_id in seen_employees:
                continue
            seen_employees.add(emp_id)

            distance = distances[i] if i < len(distances) else 1.0
            # Cosine distance → similarity score in [0.0, 1.0]
            similarity = max(0.0, min(1.0, 1.0 - distance))

            cost_rate = float(meta.get("hourly_cost_rate", 0.0))
            projected_margin = (
                (float(target_bill_rate) - cost_rate) / float(target_bill_rate) * 100
                if float(target_bill_rate) > 0
                else 0.0
            )

            candidates.append(
                {
                    "employee_id": emp_id,
                    "full_name": meta.get("full_name", ""),
                    "primary_role": meta.get("primary_role", ""),
                    "seniority": meta.get("seniority", ""),
                    "hourly_cost_rate": cost_rate,
                    "bench_start_date": meta.get("bench_start_date", ""),
                    "similarity_score": round(similarity, 4),
                    "projected_margin_pct": round(projected_margin, 2),
                    "document_excerpt": docs[i] if i < len(docs) else "",
                    "chunk_index": meta.get("chunk_index", 0),
                }
            )

        # Sort by similarity descending (deduplication may disrupt ChromaDB order)
        candidates.sort(key=lambda c: c["similarity_score"], reverse=True)

        logger.info(
            f"Retrieved {len(candidates)} financially viable candidates "
            f"(cost_rate < {target_bill_rate}) for role: {role_description[:60]}..."
        )
        return candidates

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def delete_employee(self, employee_id: str) -> None:
        """Remove all CV chunks for an employee from the collection.

        Args:
            employee_id: UUID string of the employee to remove.
        """
        self.collection.delete(where={"employee_id": {"$eq": str(employee_id)}})
        logger.info(f"Deleted all chunks for employee {employee_id}")

    def collection_count(self) -> int:
        """Return the total number of document chunks in the collection."""
        return self.collection.count()

    def health_check(self) -> Dict[str, Any]:
        """Return a health status dict for the RAG pipeline."""
        count = self.collection.count()
        return {
            "status": "healthy",
            "collection": self.collection.name,
            "document_chunks": count,
            "persist_dir": self.persist_dir,
        }
