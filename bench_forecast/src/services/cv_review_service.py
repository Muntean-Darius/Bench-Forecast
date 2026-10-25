"""Staged CV uploads, manager diff review and atomic approval.

Employee uploads never touch the active profile or ChromaDB. A
``CvReviewRequest`` is created instead. Only an authorised manager/admin
approval merges skills, rebuilds ``profile_text``, replaces Chroma embeddings
and flips document state.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database.models import (
    CvApprovalStatusEnum,
    CvReviewRequest,
    Document,
    DocumentTypeEnum,
    Employee,
    EmployeeSkill,
    IngestionStatusEnum,
    SkillOriginEnum,
)
from src.services.profile_ingestion_service import reembed_employee_profile
from src.services.skills_service import (
    get_employee_skill_links,
    merge_skills,
    normalize_skills,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class CvReviewService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    async def stage_cv(
        self,
        *,
        employee: Employee,
        document: Document,
        extracted_skills: list[str],
        summary: Optional[str],
    ) -> CvReviewRequest:
        proposed = normalize_skills(extracted_skills)
        document.approval_status = CvApprovalStatusEnum.pending_approval
        document.ingestion_status = IngestionStatusEnum.pending
        document.is_current = False
        document.extracted_skills = proposed
        document.extracted_summary = summary
        document.pending_profile_updates = {
            "summary": summary,
            "skills": proposed,
            "document_id": str(document.id),
            "staged_at": _utcnow().isoformat(),
        }

        existing = (
            await self.session.execute(
                select(CvReviewRequest).where(
                    CvReviewRequest.document_id == document.id
                )
            )
        ).scalars().first()
        if existing is not None:
            review = existing
            review.status = CvApprovalStatusEnum.pending_approval
            review.proposed_skills = proposed
            review.proposed_summary = summary
            review.manager_id = employee.manager_id
        else:
            review = CvReviewRequest(
                document_id=document.id,
                employee_id=employee.id,
                manager_id=employee.manager_id,
                status=CvApprovalStatusEnum.pending_approval,
                proposed_skills=proposed,
                proposed_summary=summary,
                requested_at=_utcnow(),
            )
            self.session.add(review)

        await self.session.flush()
        logger.info(
            "CV staged: employee=%s document=%s review=%s manager=%s",
            employee.id,
            document.id,
            review.id,
            review.manager_id,
        )
        return review

    # ------------------------------------------------------------------
    # Reading / diff
    # ------------------------------------------------------------------

    async def get_review(self, review_id: uuid.UUID) -> Optional[CvReviewRequest]:
        result = await self.session.execute(
            select(CvReviewRequest)
            .options(
                joinedload(CvReviewRequest.document),
                joinedload(CvReviewRequest.employee),
                joinedload(CvReviewRequest.manager),
            )
            .where(CvReviewRequest.id == review_id)
        )
        return result.scalars().first()

    async def list_reviews(
        self,
        *,
        manager_id: Optional[uuid.UUID] = None,
        employee_id: Optional[uuid.UUID] = None,
        status: Optional[CvApprovalStatusEnum] = None,
    ) -> list[CvReviewRequest]:
        stmt = (
            select(CvReviewRequest)
            .options(
                joinedload(CvReviewRequest.document),
                joinedload(CvReviewRequest.employee),
                joinedload(CvReviewRequest.manager),
            )
            .order_by(CvReviewRequest.requested_at.desc())
        )
        if manager_id is not None:
            stmt = stmt.where(CvReviewRequest.manager_id == manager_id)
        if employee_id is not None:
            stmt = stmt.where(CvReviewRequest.employee_id == employee_id)
        if status is not None:
            stmt = stmt.where(CvReviewRequest.status == status)
        return list((await self.session.execute(stmt)).scalars().all())

    async def build_diff(self, review: CvReviewRequest) -> dict:
        employee = review.employee or await self.session.get(
            Employee, review.employee_id
        )
        document = review.document or await self.session.get(
            Document, review.document_id
        )
        links = await get_employee_skill_links(self.session, review.employee_id)
        existing = sorted(
            {link.skill.name for link in links if link.skill}, key=str.lower
        )
        proposed = normalize_skills(review.proposed_skills or [])
        existing_keys = {name.lower() for name in existing}
        proposed_keys = {name.lower() for name in proposed}

        project_earned = sorted(
            {
                link.skill.name
                for link in links
                if link.skill
                and (
                    link.source == SkillOriginEnum.project
                    or link.is_locked
                )
            },
            key=str.lower,
        )
        new_skills = [name for name in proposed if name.lower() not in existing_keys]
        retained = [
            name for name in existing if name.lower() not in proposed_keys
        ]

        return {
            "review_id": str(review.id),
            "document_id": str(review.document_id),
            "employee_id": str(review.employee_id),
            "employee_name": (
                f"{employee.first_name} {employee.last_name}".strip()
                if employee
                else ""
            ),
            "document": {
                "document_id": str(document.id),
                "employee_id": str(document.employee_id),
                "file_name": document.file_name,
                "file_path": document.file_path,
                "version": document.version,
                "approval_status": (
                    document.approval_status.value
                    if hasattr(document.approval_status, "value")
                    else str(document.approval_status)
                ),
                "ingestion_status": (
                    document.ingestion_status.value
                    if hasattr(document.ingestion_status, "value")
                    else str(document.ingestion_status)
                ),
                "is_current": document.is_current,
                "uploaded_at": (
                    document.uploaded_at.isoformat()
                    if document.uploaded_at
                    else None
                ),
            },
            "existing_skills": existing,
            "proposed_skills": proposed,
            "new_skills": new_skills,
            "retained_skills": retained,
            "project_earned_skills": project_earned,
            "summary_before": employee.profile_text if employee else None,
            "summary_after": (
                review.proposed_summary
                or document.extracted_summary
                or (document.text_content or "")[:4000]
            ),
            "status": (
                review.status.value
                if hasattr(review.status, "value")
                else str(review.status)
            ),
            "manager_comment": review.manager_comment,
        }

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    async def approve(
        self,
        review: CvReviewRequest,
        *,
        reviewer_id: Optional[uuid.UUID] = None,
        comment: Optional[str] = None,
    ) -> CvReviewRequest:
        if review.status != CvApprovalStatusEnum.pending_approval:
            raise ValueError(
                f"CV review {review.id} is '{review.status}', not pending_approval"
            )

        employee = review.employee or await self.session.get(
            Employee, review.employee_id
        )
        document = review.document or await self.session.get(
            Document, review.document_id
        )
        if employee is None or document is None:
            raise ValueError("CV review is missing its employee or document")

        proposed = review.proposed_skills or document.extracted_skills or []
        summary = (
            review.proposed_summary
            or document.extracted_summary
            or document.text_content
            or employee.profile_text
            or ""
        )

        await merge_skills(
            self.session,
            employee.id,
            proposed,
            source=SkillOriginEnum.cv,
            source_ref=document.id,
            note=f"CV v{document.version} approved.",
        )

        links = await get_employee_skill_links(self.session, employee.id)
        project_skills = sorted(
            {
                link.skill.name
                for link in links
                if link.skill
                and (link.source == SkillOriginEnum.project or link.is_locked)
            },
            key=str.lower,
        )
        training_skills = sorted(
            {
                link.skill.name
                for link in links
                if link.skill and link.source == SkillOriginEnum.training
            },
            key=str.lower,
        )

        profile_parts = [summary.strip()]
        if project_skills:
            profile_parts.append(
                "Project-earned skills: " + ", ".join(project_skills)
            )
        if training_skills:
            profile_parts.append(
                "Training skills: " + ", ".join(training_skills)
            )
        profile_text = "\n\n".join(part for part in profile_parts if part)

        employee.profile_text = profile_text
        employee.cv_document_uri = document.file_path
        employee.updated_at = _utcnow()

        # Resolve Chroma/embedding first; a failure leaves the review pending.
        try:
            chroma_doc_id, chunks = await reembed_employee_profile(
                self.session, employee, document, profile_text
            )
        except Exception:
            await self.session.rollback()
            logger.exception(
                "CV approval failed while re-embedding employee %s", employee.id
            )
            raise

        await self.session.execute(
            update(Document)
            .where(
                Document.employee_id == employee.id,
                Document.document_type == DocumentTypeEnum.cv,
                Document.id != document.id,
            )
            .values(is_current=False)
        )

        document.is_current = True
        document.approval_status = CvApprovalStatusEnum.approved
        document.ingestion_status = IngestionStatusEnum.ingested
        document.chroma_doc_id = chroma_doc_id
        document.reviewed_by = reviewer_id
        document.reviewed_at = _utcnow()
        document.review_comment = comment
        document.profile_applied_at = _utcnow()

        review.status = CvApprovalStatusEnum.approved
        review.reviewed_by = reviewer_id
        review.reviewed_at = _utcnow()
        review.manager_comment = comment

        await self.session.commit()
        await self.session.refresh(review)
        logger.info(
            "CV approved: review=%s employee=%s chunks=%d",
            review.id,
            employee.id,
            chunks,
        )
        return review

    async def reject(
        self,
        review: CvReviewRequest,
        *,
        reviewer_id: Optional[uuid.UUID] = None,
        comment: str,
    ) -> CvReviewRequest:
        if review.status != CvApprovalStatusEnum.pending_approval:
            raise ValueError(
                f"CV review {review.id} is '{review.status}', not pending_approval"
            )
        document = review.document or await self.session.get(
            Document, review.document_id
        )
        review.status = CvApprovalStatusEnum.rejected
        review.reviewed_by = reviewer_id
        review.reviewed_at = _utcnow()
        review.manager_comment = comment
        if document is not None:
            document.approval_status = CvApprovalStatusEnum.rejected
            document.is_current = False
            document.reviewed_by = reviewer_id
            document.reviewed_at = _utcnow()
            document.review_comment = comment
        await self.session.commit()
        await self.session.refresh(review)
        logger.info("CV rejected: review=%s comment=%s", review.id, comment)
        return review

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def cv_status(self, employee_id: uuid.UUID) -> dict:
        employee = await self.session.get(Employee, employee_id)
        if employee is None:
            raise ValueError(f"Employee {employee_id} not found")

        manager = (
            await self.session.get(Employee, employee.manager_id)
            if employee.manager_id
            else None
        )
        manager_name = (
            f"{manager.first_name} {manager.last_name}".strip()
            if manager
            else None
        )

        pending = (
            await self.session.execute(
                select(CvReviewRequest)
                .options(joinedload(CvReviewRequest.document))
                .where(
                    CvReviewRequest.employee_id == employee_id,
                    CvReviewRequest.status == CvApprovalStatusEnum.pending_approval,
                )
                .order_by(CvReviewRequest.requested_at.desc())
            )
        ).scalars().first()

        latest_rejected = (
            await self.session.execute(
                select(CvReviewRequest)
                .where(
                    CvReviewRequest.employee_id == employee_id,
                    CvReviewRequest.status == CvApprovalStatusEnum.rejected,
                )
                .order_by(CvReviewRequest.reviewed_at.desc())
            )
        ).scalars().first()

        current = (
            await self.session.execute(
                select(Document)
                .where(
                    Document.employee_id == employee_id,
                    Document.document_type == DocumentTypeEnum.cv,
                    Document.approval_status == CvApprovalStatusEnum.approved,
                    Document.is_current.is_(True),
                )
                .order_by(Document.uploaded_at.desc())
            )
        ).scalars().first()

        if pending is not None:
            return {
                "employee_id": str(employee_id),
                "state": "pending_approval",
                "label": "Pending Manager Approval",
                "document_id": str(pending.document_id),
                "version": (
                    pending.document.version if pending.document else None
                ),
                "submitted_at": pending.requested_at.isoformat(),
                "manager_id": str(employee.manager_id) if employee.manager_id else None,
                "manager_name": manager_name,
                "manager_comment": None,
                "review_id": str(pending.id),
                "proposed_skills": pending.proposed_skills or [],
            }

        if latest_rejected is not None and (
            current is None
            or latest_rejected.reviewed_at is None
            or latest_rejected.reviewed_at > current.uploaded_at
        ):
            return {
                "employee_id": str(employee_id),
                "state": "changes_requested",
                "label": "Changes Requested",
                "document_id": str(latest_rejected.document_id),
                "version": None,
                "submitted_at": latest_rejected.requested_at.isoformat(),
                "manager_id": str(employee.manager_id) if employee.manager_id else None,
                "manager_name": manager_name,
                "manager_comment": latest_rejected.manager_comment,
                "review_id": str(latest_rejected.id),
                "proposed_skills": latest_rejected.proposed_skills or [],
            }

        return {
            "employee_id": str(employee_id),
            "state": "live",
            "label": "Live / Active",
            "document_id": str(current.id) if current else None,
            "version": current.version if current else None,
            "submitted_at": (
                current.uploaded_at.isoformat() if current else None
            ),
            "manager_id": str(employee.manager_id) if employee.manager_id else None,
            "manager_name": manager_name,
            "manager_comment": None,
            "review_id": None,
            "proposed_skills": [],
        }
