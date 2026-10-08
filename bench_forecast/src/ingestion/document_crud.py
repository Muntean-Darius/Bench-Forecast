"""SQLAlchemy CRUD helpers for the documents table — Phase 2 ingestion writes.

Responsibilities:
  - Create the initial ``documents`` row (status=pending) when a CV is uploaded.
  - Update ``text_content`` after pdfplumber extraction.
  - Write back ``chroma_doc_id`` and flip ``ingestion_status`` to ``ingested``
    after a successful ChromaDB upsert.
  - Flip ``ingestion_status`` to ``failed`` on error so operators can retry.
  - Mark the previous ``is_current=True`` row as ``is_current=False`` on
    re-upload so there is always exactly one current version per employee.

All functions accept a SQLAlchemy ``AsyncSession`` so they can be called
directly from FastAPI endpoint handlers that use ``Depends(get_db)``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import Document, IngestionStatusEnum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Public CRUD functions
# ---------------------------------------------------------------------------


async def create_document_record(
    db: AsyncSession,
    *,
    employee_id: uuid.UUID,
    document_type: str,
    file_name: str,
    file_path: str,
    file_size_bytes: Optional[int] = None,
    mime_type: str = "application/pdf",
    version: int = 1,
) -> Document:
    """Insert a new ``documents`` row with ``ingestion_status='pending'``.

    Also marks the previous ``is_current`` row for the same employee +
    document_type as ``is_current=False`` (one active version policy).

    Args:
        db:               Active async database session.
        employee_id:      FK to ``employees.id``.
        document_type:    One of the ``DocumentTypeEnum`` values (e.g. ``"cv"``).
        file_name:        Original upload filename.
        file_path:        Object-store path (MinIO / local FS) where the binary
                          was saved.
        file_size_bytes:  File size in bytes (optional — filled if known).
        mime_type:        MIME type string (default ``application/pdf``).
        version:          CV version number.  Callers should pass the next
                          version number after querying the latest.

    Returns:
        The newly created, flushed (but not yet committed) ``Document`` ORM
        instance.  The session commit is the caller's responsibility.
    """
    # Mark previous current version as no longer current
    await db.execute(
        update(Document)
        .where(
            Document.employee_id == employee_id,
            Document.document_type == document_type,
            Document.is_current.is_(True),
        )
        .values(is_current=False)
    )

    doc = Document(
        id=uuid.uuid4(),
        employee_id=employee_id,
        document_type=document_type,
        file_name=file_name,
        file_path=file_path,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        version=version,
        is_current=True,
        ingestion_status=IngestionStatusEnum.pending,
        uploaded_at=_utcnow(),
    )
    db.add(doc)
    await db.flush()  # assigns the PK without committing

    logger.info(
        "documents row created: id=%s employee_id=%s file=%s status=pending",
        doc.id,
        employee_id,
        file_name,
    )
    return doc


async def set_text_content(
    db: AsyncSession,
    document_id: uuid.UUID,
    text_content: str,
) -> None:
    """Store the pdfplumber-extracted text on the document row.

    Args:
        db:            Active async database session.
        document_id:   UUID of the target ``documents`` row.
        text_content:  Full extracted plain text.
    """
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(text_content=text_content)
    )
    logger.debug(
        "documents.text_content updated: id=%s chars=%d",
        document_id,
        len(text_content),
    )


async def mark_ingested(
    db: AsyncSession,
    document_id: uuid.UUID,
    chroma_doc_id: str,
) -> None:
    """Write the ChromaDB base document ID and flip status to ``ingested``.

    Called after a successful :meth:`ChromaIngestionStore.upsert_document_chunks`
    call.

    Args:
        db:            Active async database session.
        document_id:   UUID of the ``documents`` row to update.
        chroma_doc_id: The base ChromaDB document ID returned by the upsert
                       (i.e. ``"<document_uuid>"``, without the chunk suffix).
    """
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(
            chroma_doc_id=chroma_doc_id,
            ingestion_status=IngestionStatusEnum.ingested,
        )
    )
    logger.info(
        "documents marked ingested: id=%s chroma_doc_id=%s",
        document_id,
        chroma_doc_id,
    )


async def mark_failed(
    db: AsyncSession,
    document_id: uuid.UUID,
    reason: Optional[str] = None,
) -> None:
    """Flip the document's ``ingestion_status`` to ``failed``.

    Called from the exception handler in the ingestion endpoint so that
    operators can query ``WHERE ingestion_status='failed'`` and retry.

    Args:
        db:          Active async database session.
        document_id: UUID of the ``documents`` row.
        reason:      Optional failure detail (logged at WARNING level).
    """
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(ingestion_status=IngestionStatusEnum.failed)
    )
    logger.warning(
        "documents marked failed: id=%s reason=%s",
        document_id,
        reason or "unknown",
    )


async def get_next_version(
    db: AsyncSession,
    employee_id: uuid.UUID,
    document_type: str,
) -> int:
    """Return the next version number for an employee's document type.

    Queries the maximum existing version for the given (employee_id,
    document_type) pair and returns ``max_version + 1``, or ``1`` if no
    previous versions exist.

    Args:
        db:            Active async database session.
        employee_id:   FK to ``employees.id``.
        document_type: Document category (e.g. ``"cv"``).

    Returns:
        Integer version number starting from ``1``.
    """
    from sqlalchemy import func  # noqa: PLC0415

    result = await db.execute(
        select(func.max(Document.version)).where(
            Document.employee_id == employee_id,
            Document.document_type == document_type,
        )
    )
    max_version = result.scalar()
    return (max_version or 0) + 1


async def get_document_by_id(
    db: AsyncSession,
    document_id: uuid.UUID,
) -> Optional[Document]:
    """Fetch a single ``Document`` by primary key.

    Args:
        db:          Active async database session.
        document_id: UUID of the target row.

    Returns:
        The ``Document`` ORM instance, or ``None`` if not found.
    """
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()
