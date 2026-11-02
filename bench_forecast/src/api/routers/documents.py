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

import asyncio
import io
import logging
import os
import uuid
from typing import Annotated, Optional

from langchain_core.messages import HumanMessage
from src.core.llm import LLMFactory

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import (
    assert_can_manage_employee,
    get_current_user,
    get_optional_user,
)
from src.core.config import Config
from src.core.observability import workflow
from src.database.database import get_db
from src.database.models import (
    CvApprovalStatusEnum,
    CvReviewRequest,
    Document,
    Employee,
    IngestionStatusEnum,
)
from src.ingestion.document_crud import (
    create_document_record,
    get_document_by_id,
    get_next_version,
    mark_failed,
    set_text_content,
)
from src.ingestion.pdf_extractor import chunk_text, extract_text_from_bytes
from src.services.cv_review_service import CvReviewService
from src.services.notifications import NotificationService, schedule_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

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
    approval_status: str
    review_id: Optional[str] = None
    chunks_ingested: int
    message: str
    summary: Optional[str] = None
    extracted_skills: Optional[list] = None


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
    approval_status: str
    review_id: Optional[str] = None
    review_comment: Optional[str] = None
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
@workflow(name="cv-ingestion", version="v2", context={"entrypoint": "cv-upload"})
async def upload_cv(
    file: Annotated[UploadFile, File(description="CV PDF file")],
    employee_id: Annotated[str, Form(description="UUID of the employee this CV belongs to")],
    document_type: Annotated[str, Form(description="Document type: cv | certification | contract | performance_review | training_record")] = "cv",
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> DocumentUploadResponse:
    """Upload a CV.

    * employee upload → staged as ``pending_approval`` (no profile/Chroma write)
    * manager uploading their own CV → auto-approved and ingested
    * admin upload for another employee → auto-approved and ingested
    """

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

    uploader_role = str(user.get("role") or "").lower()
    uploader_employee_id = user.get("employee_id")
    is_self_upload = (
        uploader_employee_id is not None
        and str(uploader_employee_id) == str(emp_uuid)
    )
    if uploader_role == "admin" and is_self_upload:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin accounts do not upload personal CVs.",
        )
    if uploader_role in ("employee", "manager") and not is_self_upload:
        await assert_can_manage_employee(db, user, emp_uuid)
    if uploader_role not in ("admin", "manager", "employee"):
        raise HTTPException(status_code=403, detail="Unsupported role")

    auto_approve = (
        (uploader_role == "admin" and not is_self_upload)
        or (uploader_role == "manager" and is_self_upload)
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
        is_current=False,
    )
    doc_id: uuid.UUID = doc.id

    # Commit the pending row immediately so it's visible even if extraction fails
    await db.commit()

    chroma_doc_id: str = ""
    chunks_ingested: int = 0
    review_id: Optional[str] = None
    approval_status = CvApprovalStatusEnum.pending_approval.value
    pending_notifications = []

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

        # Save raw extracted text to documents table for audit
        await set_text_content(db, doc_id, full_text)
        await db.commit()

        # Save copies to local storage/cv paths for immediate inline display & download
        for s_dir in ["storage/cv", "bench_forecast/storage/cv"]:
            try:
                os.makedirs(s_dir, exist_ok=True)
                with open(os.path.join(s_dir, f"{emp_uuid}.pdf"), "wb") as f_out:
                    f_out.write(pdf_bytes)
            except Exception as e_save:
                logger.warning(f"Failed to write fallback CV to {s_dir}: {e_save}")

        # ------------------------------------------------------------------
        # 6. Chunk the text (only needed for the auto-approve/ingest path)
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
        # 6b. LLM CV Summarization (Excluding Personal Data)
        # ------------------------------------------------------------------
        logger.info("Generating anonymized professional CV summary via LLM: document_id=%s", doc_id)
        cv_summary: Optional[str] = None
        skills_list: list = []
        try:
            llm = LLMFactory.get_chat_model()
            summary_prompt = (
                "You are an expert HR and workforce allocation analyst.\n"
                "Analyze the following candidate CV and create a comprehensive professional summary in JSON format.\n\n"
                "CRITICAL PRIVACY RULE:\n"
                "Strictly EXCLUDE ALL personal data (candidate name, email address, phone number, physical address, age, gender, personal social links).\n\n"
                "IMPORTANT INFORMATION TO INCLUDE:\n"
                "- professional_summary: Detailed professional summary containing core strengths, domains, and career highlights.\n"
                "- seniority_level: Junior, Mid, Senior, Lead, or Principal.\n"
                "- years_of_experience: Number of years (float or int).\n"
                "- core_skills: Array of primary technical skills, frameworks, languages, and tools.\n"
                "- key_achievements: Array of notable projects or deliverables.\n"
                "- domain_expertise: Array of business domains (e.g. FinTech, Healthcare, Cloud, etc.).\n\n"
                "JSON schema:\n"
                "{\n"
                '  "professional_summary": "string",\n'
                '  "seniority_level": "string",\n'
                '  "years_of_experience": float,\n'
                '  "core_skills": ["string"],\n'
                '  "key_achievements": ["string"],\n'
                '  "domain_expertise": ["string"]\n'
                "}\n\n"
                f"Candidate CV text:\n{full_text[:6000]}"
            )
            llm_res = await asyncio.to_thread(llm.invoke, [HumanMessage(content=summary_prompt)])
            parsed_summary = LLMFactory.parse_json_response(llm_res.content)

            prof_sum = parsed_summary.get("professional_summary") or ""
            skills_list = parsed_summary.get("core_skills", [])
            domains_list = parsed_summary.get("domain_expertise", [])
            achieve_list = parsed_summary.get("key_achievements", [])

            summary_parts = []
            if prof_sum:
                summary_parts.append(prof_sum)
            if skills_list:
                summary_parts.append(f"Core Skills: {', '.join(skills_list)}")
            if domains_list:
                summary_parts.append(f"Domains: {', '.join(domains_list)}")
            if achieve_list:
                summary_parts.append(f"Key Achievements: {'; '.join(achieve_list)}")

            if summary_parts:
                cv_summary = "\n\n".join(summary_parts)
                logger.info("✓ LLM CV summary successfully generated: document_id=%s (%d chars)", doc_id, len(cv_summary))
        except Exception as e_sum:
            logger.warning(f"LLM CV summarization failed (falling back to extracted text): {e_sum}")

        # ------------------------------------------------------------------
        # 6c. Extract skills from the CV. These are STAGED, never written to
        #     the employee until a manager approves the review.
        # ------------------------------------------------------------------
        from src.agents.nodes import _extract_skills_from_text

        cv_skills: list = []
        for raw_skill in skills_list:
            if raw_skill and str(raw_skill).strip():
                cv_skills.append(str(raw_skill).strip())
        cv_skills.extend(_extract_skills_from_text(full_text))

        seen_skills: set = set()
        deduped_skills: list = []
        for skill in cv_skills:
            key = skill.lower()
            if key and key not in seen_skills:
                seen_skills.add(key)
                deduped_skills.append(skill)

        extracted_skills = deduped_skills[:25]
        logger.info(
            "Extracted %d skill(s) from CV for employee=%s: %s",
            len(extracted_skills),
            employee_name,
            extracted_skills[:10],
        )

        await db.refresh(doc)
        review_service = CvReviewService(db)
        review = await review_service.stage_cv(
            employee=employee,
            document=doc,
            extracted_skills=extracted_skills,
            summary=cv_summary if cv_summary else full_text[:6000],
        )
        review_id = str(review.id)

        if auto_approve:
            approved = await review_service.approve(
                review,
                reviewer_id=uuid.UUID(user["id"]),
                comment="Auto-approved upload (manager self / admin).",
            )
            doc = await get_document_by_id(db, doc_id)
            chroma_doc_id = doc.chroma_doc_id or ""
            chunks_ingested = 0
            approval_status = CvApprovalStatusEnum.approved.value
            logger.info(
                "✓ CV auto-approved: document_id=%s employee=%s",
                doc_id,
                employee_name,
            )
            try:
                pending_notifications.append(
                    await NotificationService(db).notify_cv_decision(
                        employee,
                        approved,
                        approved=True,
                        comment="Auto-approved upload.",
                    )
                )
                await db.commit()
            except Exception:
                logger.exception("Could not queue cv_approved notification")
                await db.rollback()
        else:
            try:
                pending_notifications.append(
                    await NotificationService(db).notify_cv_approval_needed(
                        employee, doc, review
                    )
                )
            except Exception:
                logger.exception("Could not queue cv_approval_needed notification")
            await db.commit()
            approval_status = CvApprovalStatusEnum.pending_approval.value
            logger.info(
                "CV staged for manager approval: document_id=%s review_id=%s",
                doc_id,
                review_id,
            )

    except Exception as exc:
        logger.error(
            "✗ CV upload failed: document_id=%s reason=%s",
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
                f"CV upload failed: {exc}. The document row (id={doc_id}) "
                "has been marked ingestion_status='failed'."
            ),
        )

    for notification in pending_notifications:
        if notification.status == "queued":
            schedule_notification(notification.id, background_tasks)

    return DocumentUploadResponse(
        document_id=str(doc_id),
        employee_id=str(emp_uuid),
        file_name=file_name,
        file_path=file_path,
        file_size_bytes=file_size,
        version=version,
        chroma_doc_id=chroma_doc_id,
        ingestion_status=(
            IngestionStatusEnum.ingested.value
            if approval_status == CvApprovalStatusEnum.approved.value
            else IngestionStatusEnum.pending.value
        ),
        approval_status=approval_status,
        review_id=review_id,
        chunks_ingested=chunks_ingested,
        message=(
            "CV approved and embedded into ChromaDB."
            if approval_status == CvApprovalStatusEnum.approved.value
            else "CV submitted for manager approval. Your live profile has not changed yet."
        ),
        summary=cv_summary,
        extracted_skills=extracted_skills,
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

    review = (
        await db.execute(
            select(CvReviewRequest).where(CvReviewRequest.document_id == doc.id)
        )
    ).scalars().first()

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
        approval_status=str(doc.approval_status.value if hasattr(doc.approval_status, 'value') else doc.approval_status),
        review_id=str(review.id) if review else None,
        review_comment=doc.review_comment,
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
    user: dict = Depends(get_current_user),
) -> list[DocumentMetadataResponse]:
    """Return all document rows for a given employee, newest first."""
    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{employee_id}' is not a valid UUID.",
        )

    await assert_can_manage_employee(db, user, emp_uuid)

    result = await db.execute(
        select(Document)
        .where(Document.employee_id == emp_uuid)
        .order_by(Document.uploaded_at.desc())
    )
    docs = result.scalars().all()

    reviews = (
        await db.execute(
            select(CvReviewRequest).where(
                CvReviewRequest.document_id.in_([doc.id for doc in docs])
            )
        )
    ).scalars().all() if docs else []
    review_by_doc = {review.document_id: review for review in reviews}

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
            approval_status=str(d.approval_status.value if hasattr(d.approval_status, 'value') else d.approval_status),
            review_id=(
                str(review_by_doc[d.id].id) if d.id in review_by_doc else None
            ),
            review_comment=d.review_comment,
            uploaded_at=d.uploaded_at.isoformat(),
        )
        for d in docs
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/documents/employee/{employee_id}/download
# ---------------------------------------------------------------------------

# CVs are mutable (a new upload overwrites the previous file), so responses
# must never be cached by the browser / PDF viewer plugin.
_CV_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@router.get(
    "/employee/{employee_id}/download",
    summary="Download CV document for an employee",
)
async def download_employee_cv(
    employee_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """Returns the current approved CV PDF for an employee."""
    from fastapi.responses import FileResponse, Response
    try:
        emp_uuid = uuid.UUID(employee_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{employee_id}' is not a valid UUID.",
        )

    if user is not None:
        await assert_can_manage_employee(db, user, emp_uuid)

    emp_res = await db.execute(select(Employee).where(Employee.id == emp_uuid))
    employee = emp_res.scalar_one_or_none()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Prefer the current approved CV; fall back to the latest upload so the
    # uploader can still inspect a pending document when necessary.
    doc_res = await db.execute(
        select(Document)
        .where(Document.employee_id == emp_uuid)
        .order_by(
            Document.is_current.desc(),
            Document.approval_status.desc(),
            Document.uploaded_at.desc(),
        )
    )
    doc = doc_res.scalars().first()

    candidate_paths = []
    if doc and doc.file_path:
        candidate_paths.append(doc.file_path)
        candidate_paths.append(os.path.join("bench_forecast", doc.file_path))
    if employee.cv_document_uri:
        candidate_paths.append(employee.cv_document_uri)
        candidate_paths.append(os.path.join("bench_forecast", employee.cv_document_uri))
    candidate_paths.append(f"./storage/cv/{emp_uuid}.pdf")
    candidate_paths.append(f"storage/cv/{emp_uuid}.pdf")
    candidate_paths.append(f"./bench_forecast/storage/cv/{emp_uuid}.pdf")
    candidate_paths.append(f"bench_forecast/storage/cv/{emp_uuid}.pdf")

    for path in candidate_paths:
        if path and not path.startswith("s3://") and os.path.exists(path):
            filename = f"{employee.first_name}_{employee.last_name}_CV.pdf"
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=filename,
                content_disposition_type="inline",
                headers=_CV_NO_CACHE_HEADERS,
            )

    # If no file exists but profile_text exists, generate PDF dynamically
    if employee.profile_text:
        try:
            from fpdf import FPDF
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", "B", 14)
            pdf.cell(0, 10, f"{employee.first_name} {employee.last_name} - CV Profile", ln=True)
            pdf.set_font("Arial", size=10)
            safe_text = employee.profile_text.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 5, txt=safe_text)
            
            gen_path = f"./bench_forecast/storage/cv/{emp_uuid}.pdf"
            os.makedirs(os.path.dirname(gen_path), exist_ok=True)
            pdf.output(gen_path)
            return FileResponse(
                gen_path,
                media_type="application/pdf",
                filename=f"{employee.first_name}_{employee.last_name}_CV.pdf",
                content_disposition_type="inline",
                headers=_CV_NO_CACHE_HEADERS,
            )
        except Exception as e:
            logger.warning(f"Could not generate PDF on the fly: {e}")
            return Response(
                content=employee.profile_text,
                media_type="text/plain",
                headers={
                    "Content-Disposition": f"inline; filename={employee.first_name}_{employee.last_name}_CV.txt",
                    **_CV_NO_CACHE_HEADERS,
                },
            )

    raise HTTPException(status_code=404, detail="No CV document found for this employee")


