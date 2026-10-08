"""FastAPI router — CV PDF upload & ingestion endpoint.

Endpoint
--------
POST /api/v1/documents/upload

    Accepts a multipart PDF upload alongside ``employee_id`` and optional
    ``document_type`` form fields.  Runs the full ingestion pipeline:

    1. Validates the employee exists in PostgreSQL.
    2. Saves the binary to MinIO (or local /storage/ fallback).
    3. Inserts a ``documents`` row with ``ingestion_status='pending'``.
    4. Extracts text with pdfplumber.
    5. Embeds chunks with Ollama nomic-embed-text.
    6. Upserts chunks into ChromaDB (employee_profiles collection).
    7. Updates ``documents.chroma_doc_id`` and sets ``ingestion_status='ingested'``.

On any error between step 3 and 7, the ``documents`` row is flipped to
``ingestion_status='failed'`` so operators can retry via the status endpoint.

Additional endpoints
--------------------
GET /api/v1/documents/{document_id}        — Fetch document metadata.
GET /api/v1/documents/employee/{employee_id} — List all docs for an employee.

Dependencies
------------
    pdfplumber  — pip install pdfplumber
    ollama      — pip install ollama
    chromadb    — pip install chromadb
    minio       — pip install minio  (optional; local FS used as fallback)
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Config
from src.database.database import get_db
from src.database.models import Document, Employee, IngestionStatusEnum
from src.ingestion.chroma_store import ChromaIngestionStore
from src.ingestion.document_crud import (
    create_document_record,
    get_document_by_id,
    get_next_version,
    mark_failed,
    mark_ingested,
    set_text_content,
)
from src.ingestion.embedder import OllamaEmbedder
from src.ingestion.pdf_extractor import chunk_text, extract_text_from_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

# ---------------------------------------------------------------------------
# Module-level singletons (created once, reused across requests)
# ---------------------------------------------------------------------------

_embedder: Optional[OllamaEmbedder] = None
_chroma_store: Optional[ChromaIngestionStore] = None


def _get_embedder() -> OllamaEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = OllamaEmbedder()
    return _embedder


def _get_chroma_store() -> ChromaIngestionStore:
    global _chroma_store
    if _chroma_store is None:
        _chroma_store = ChromaIngestionStore()
    return _chroma_store


# ---------------------------------------------------------------------------
# Storage helpers (MinIO preferred, local FS fallback)
# ---------------------------------------------------------------------------

def _storage_path(document_type: str, employee_id: str, file_name: str) -> str:
    """Return the object-store key / local file path for a document."""
    return f"{document_type}/{employee_id}/{file_name}"


async def _save_to_storage(
    pdf_bytes: bytes,
    document_type: str,
    employee_id: str,
    file_name: str,
) -> str:
    """Upload *pdf_bytes* to MinIO; fall back to local ``/storage/`` on failure.

    Returns the object-store path string stored in ``documents.file_path``.
    """
    object_key = _storage_path(document_type, employee_id, file_name)

    try:
        from minio import Minio  # noqa: PLC0415
        from minio.error import S3Error  # noqa: PLC0415

        client = Minio(
            Config.MINIO_ENDPOINT,
            access_key=Config.MINIO_ACCESS_KEY,
            secret_key=Config.MINIO_SECRET_KEY,
            secure=Config.MINIO_USE_SSL,
        )

        # Ensure bucket exists
        if not client.bucket_exists(Config.MINIO_BUCKET):
            client.make_bucket(Config.MINIO_BUCKET)

        client.put_object(
            bucket_name=Config.MINIO_BUCKET,
            object_name=object_key,
            data=io.BytesIO(pdf_bytes),
            length=len(pdf_bytes),
            content_type="application/pdf",
        )
        path = f"s3://{Config.MINIO_BUCKET}/{object_key}"
        logger.info("Saved to MinIO: %s", path)
        return path

    except Exception as exc:
        logger.warning(
            "MinIO upload failed (%s). Using local FS fallback.", exc
        )
        local_path = os.path.join("storage", object_key)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(pdf_bytes)
        logger.info("Saved to local FS: %s", local_path)
        return local_path


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class DocumentUploadResponse(BaseModel):
    document_id: str
    employee_id: str
    file_name: str
    file_path: str
    file_size_bytes: int
    version: int
    chroma_doc_id: str
    ingestion_status: str
    chunks_ingested: int
    message: str


class DocumentMetadataResponse(BaseModel):
    document_id: str
    employee_id: str
    document_type: str
    file_name: str
    file_path: str
    file_size_bytes: Optional[int]
    version: int
    is_current: bool
    chroma_doc_id: Optional[str]
    ingestion_status: str
    uploaded_at: str


# ---------------------------------------------------------------------------
# POST /api/v1/documents/upload
# ---------------------------------------------------------------------------

@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CV PDF and ingest it into ChromaDB",
    description=(
        "Upload a PDF file for an employee. The endpoint:\n"
        "1. Saves the binary to MinIO (or local /storage/ fallback)\n"
        "2. Inserts a `documents` row with `ingestion_status=pending`\n"
        "3. Extracts text with pdfplumber\n"
        "4. Chunks the text (~500 tokens, 50-token overlap)\n"
        "5. Embeds each chunk with Ollama `nomic-embed-text`\n"
        "6. Upserts chunks into ChromaDB `employee_profiles` collection\n"
        "7. Updates `documents.chroma_doc_id` and sets `ingestion_status=ingested`"
    ),
)
async def upload_cv(
    file: Annotated[UploadFile, File(description="CV PDF file")],
    employee_id: Annotated[str, Form(description="UUID of the employee this CV belongs to")],
    document_type: Annotated[str, Form(description="Document type: cv | certification | contract | performance_review | training_record")] = "cv",
    db: AsyncSession = Depends(get_db),
) -> DocumentUploadResponse:
    """Full CV ingestion pipeline — upload, extract, embed, store."""

    # ------------------------------------------------------------------
    # 0. Validate inputs
    # ------------------------------------------------------------------
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF files are accepted (file must have a .pdf extension).",
        )

    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"employee_id '{employee_id}' is not a valid UUID.",
        )

    # ------------------------------------------------------------------
    # 1. Verify employee exists and load their profile metadata
    # ------------------------------------------------------------------
    emp_result = await db.execute(select(Employee).where(Employee.id == emp_uuid))
    employee: Optional[Employee] = emp_result.scalar_one_or_none()

    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Employee with id='{employee_id}' not found.",
        )

    employee_name = f"{employee.first_name} {employee.last_name}"
    experience_level = str(employee.experience_level.value if hasattr(employee.experience_level, 'value') else employee.experience_level)

    # Resolve hourly cost — prefer employee_costs table (Phase 3); fall back
    # to legacy hourly_cost_rate column for now.
    internal_hourly_cost: float = float(employee.hourly_cost_rate or 0.0)

    # ------------------------------------------------------------------
    # 2. Read PDF bytes
    # ------------------------------------------------------------------
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    file_size = len(pdf_bytes)
    file_name = file.filename  # original filename kept for audit

    logger.info(
        "CV upload received: employee=%s file=%s size=%d bytes",
        employee_name,
        file_name,
        file_size,
    )

    # ------------------------------------------------------------------
    # 3. Persist binary to object store (MinIO / local FS)
    # ------------------------------------------------------------------
    file_path = await _save_to_storage(
        pdf_bytes=pdf_bytes,
        document_type=document_type,
        employee_id=str(emp_uuid),
        file_name=file_name,
    )

    # ------------------------------------------------------------------
    # 4. Insert documents row with ingestion_status=pending
    # ------------------------------------------------------------------
    version = await get_next_version(db, emp_uuid, document_type)

    doc: Document = await create_document_record(
        db,
        employee_id=emp_uuid,
        document_type=document_type,
        file_name=file_name,
        file_path=file_path,
        file_size_bytes=file_size,
        mime_type="application/pdf",
        version=version,
    )
    doc_id: uuid.UUID = doc.id

    # Commit the pending row immediately so it's visible even if ingestion fails
    await db.commit()

    chroma_doc_id: str = ""
    chunks_ingested: int = 0

    try:
        # ------------------------------------------------------------------
        # 5. Extract text with pdfplumber
        # ------------------------------------------------------------------
        logger.info("Extracting text from PDF: document_id=%s", doc_id)
        full_text = extract_text_from_bytes(pdf_bytes)

        if not full_text.strip():
            raise ValueError(
                "pdfplumber extracted no text from the PDF. "
                "The file may be image-only or password-protected."
            )

        # Persist extracted text for audit / re-ingestion without re-upload
        await set_text_content(db, doc_id, full_text)
        await db.commit()

        # ------------------------------------------------------------------
        # 6. Chunk the text
        # ------------------------------------------------------------------
        chunks = chunk_text(full_text)
        if not chunks:
            raise ValueError("Text chunking produced 0 chunks from the extracted text.")

        logger.info(
            "Chunked PDF text: document_id=%s chunks=%d",
            doc_id,
            len(chunks),
        )

        # ------------------------------------------------------------------
        # 7. Embed chunks with Ollama nomic-embed-text
        # ------------------------------------------------------------------
        logger.info(
            "Generating embeddings: document_id=%s model=nomic-embed-text chunks=%d",
            doc_id,
            len(chunks),
        )
        embedder = _get_embedder()
        embeddings = embedder.embed_chunks(chunks)

        # ------------------------------------------------------------------
        # 8. Upsert chunks into ChromaDB
        # ------------------------------------------------------------------
        logger.info("Upserting to ChromaDB: document_id=%s", doc_id)
        chroma_store = _get_chroma_store()
        chroma_doc_id = chroma_store.upsert_document_chunks(
            document_id=str(doc_id),
            employee_id=str(emp_uuid),
            employee_name=employee_name,
            document_type=document_type,
            experience_level=experience_level,
            internal_hourly_cost=internal_hourly_cost,
            chunks=chunks,
            embeddings=embeddings,
        )
        chunks_ingested = len(chunks)

        # ------------------------------------------------------------------
        # 9. Update documents row: chroma_doc_id + ingestion_status=ingested
        # ------------------------------------------------------------------
        await mark_ingested(db, doc_id, chroma_doc_id)
        await db.commit()

        logger.info(
            "✓ CV ingestion complete: document_id=%s employee=%s "
            "chroma_doc_id=%s chunks=%d",
            doc_id,
            employee_name,
            chroma_doc_id,
            chunks_ingested,
        )

    except Exception as exc:
        logger.error(
            "✗ CV ingestion failed: document_id=%s reason=%s",
            doc_id,
            exc,
            exc_info=True,
        )
        # Mark row as failed so operators can retry
        try:
            await mark_failed(db, doc_id, reason=str(exc))
            await db.commit()
        except Exception:
            logger.exception("Could not update ingestion_status to failed")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"CV ingestion failed at the embedding/ChromaDB stage: {exc}. "
                f"The document row (id={doc_id}) has been marked ingestion_status='failed'. "
                "Check Ollama is running and nomic-embed-text is pulled."
            ),
        )

    return DocumentUploadResponse(
        document_id=str(doc_id),
        employee_id=str(emp_uuid),
        file_name=file_name,
        file_path=file_path,
        file_size_bytes=file_size,
        version=version,
        chroma_doc_id=chroma_doc_id,
        ingestion_status=IngestionStatusEnum.ingested.value,
        chunks_ingested=chunks_ingested,
        message=(
            f"CV successfully ingested. {chunks_ingested} chunk(s) embedded "
            f"into ChromaDB collection 'employee_profiles'."
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/documents/{document_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{document_id}",
    response_model=DocumentMetadataResponse,
    summary="Fetch document metadata by ID",
)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> DocumentMetadataResponse:
    """Return metadata for a single document row."""
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{document_id}' is not a valid UUID.",
        )

    doc = await get_document_by_id(db, doc_uuid)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document '{document_id}' not found.",
        )

    return DocumentMetadataResponse(
        document_id=str(doc.id),
        employee_id=str(doc.employee_id),
        document_type=str(doc.document_type.value if hasattr(doc.document_type, 'value') else doc.document_type),
        file_name=doc.file_name,
        file_path=doc.file_path,
        file_size_bytes=doc.file_size_bytes,
        version=doc.version,
        is_current=doc.is_current,
        chroma_doc_id=doc.chroma_doc_id,
        ingestion_status=str(doc.ingestion_status.value if hasattr(doc.ingestion_status, 'value') else doc.ingestion_status),
        uploaded_at=doc.uploaded_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /api/v1/documents/employee/{employee_id}
# ---------------------------------------------------------------------------

@router.get(
    "/employee/{employee_id}",
    response_model=list[DocumentMetadataResponse],
    summary="List all documents for an employee",
)
async def list_employee_documents(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentMetadataResponse]:
    """Return all document rows for a given employee, newest first."""
    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{employee_id}' is not a valid UUID.",
        )

    result = await db.execute(
        select(Document)
        .where(Document.employee_id == emp_uuid)
        .order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()

    return [
        DocumentMetadataResponse(
            document_id=str(d.id),
            employee_id=str(d.employee_id),
            document_type=str(d.document_type.value if hasattr(d.document_type, 'value') else d.document_type),
            file_name=d.file_name,
            file_path=d.file_path,
            file_size_bytes=d.file_size_bytes,
            version=d.version,
            is_current=d.is_current,
            chroma_doc_id=d.chroma_doc_id,
            ingestion_status=str(d.ingestion_status.value if hasattr(d.ingestion_status, 'value') else d.ingestion_status),
            uploaded_at=d.uploaded_at.isoformat(),
        )
        for d in docs
    ]

