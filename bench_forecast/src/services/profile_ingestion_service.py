"""Re-embed an approved employee profile into the shared Chroma collection.

This module is the only write path used by the CV approval workflow. It keeps
the vector store and the active ``employees.profile_text`` consistent once a
manager approves a staged CV.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Document, Employee
from src.ingestion.chroma_store import ChromaIngestionStore
from src.ingestion.embedder import OllamaEmbedder
from src.ingestion.pdf_extractor import chunk_text
from src.services.skills_service import get_skill_names
from src.services.workforce_service import latest_hourly_cost

logger = logging.getLogger(__name__)


async def reembed_employee_profile(
    session: AsyncSession,
    employee: Employee,
    document: Document,
    profile_text: str,
) -> tuple[str, int]:
    """Replace all Chroma chunks for *employee* with the approved profile.

    Returns ``(chroma_doc_id, chunks_ingested)``.
    """
    text = (profile_text or "").strip()
    raw_cv = (document.text_content or "").strip()
    if raw_cv and raw_cv not in text:
        text = f"{text}\n\n{raw_cv}".strip()
    if not text:
        raise ValueError("Cannot re-embed an empty employee profile")

    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Profile text produced zero embedding chunks")

    skills = await get_skill_names(session, employee.id)
    cost = await latest_hourly_cost(session, employee)
    employee_name = f"{employee.first_name} {employee.last_name}".strip()
    experience_level = (
        employee.experience_level.value
        if hasattr(employee.experience_level, "value")
        else str(employee.experience_level or "mid")
    )

    embedder = OllamaEmbedder()
    embeddings = await asyncio.to_thread(embedder.embed_chunks, chunks)

    store = ChromaIngestionStore()
    await asyncio.to_thread(store.delete_employee_chunks, str(employee.id))
    chroma_doc_id = await asyncio.to_thread(
        store.upsert_document_chunks,
        document_id=str(document.id),
        employee_id=str(employee.id),
        employee_name=employee_name,
        document_type="cv",
        experience_level=experience_level,
        internal_hourly_cost=cost,
        chunks=chunks,
        embeddings=embeddings,
        skills=", ".join(skills),
    )
    logger.info(
        "Re-embedded employee %s from document %s (%d chunks)",
        employee.id,
        document.id,
        len(chunks),
    )
    return chroma_doc_id, len(chunks)
