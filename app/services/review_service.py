"""Review task service — list, decide, and complete low-confidence review tasks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.db.models import (
    AuditEventType,
    Document,
    DocumentStatus,
    ReviewDecision,
    ReviewStatus,
    ReviewTask,
)
from app.schemas.review import ReviewDecisionCreate
from app.services.audit_service import AuditService
from app.storage.factory import get_storage_provider
from app.utils.text import deep_set


class ReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)
        self.storage = get_storage_provider()
        self.settings = get_settings()

    # ── Read ──────────────────────────────────────────────────────────────────

    def list_pending(self, *, tenant_id: str | None = None) -> list[ReviewTask]:
        stmt = (
            select(ReviewTask)
            .join(ReviewTask.document)
            .where(ReviewTask.status == ReviewStatus.pending)
        )
        from app.db.models import Document as Doc

        stmt = stmt.where(Doc.deleted_at.is_(None))
        if tenant_id is None:
            stmt = stmt.where(Doc.tenant_id.is_(None))
        else:
            stmt = stmt.where(Doc.tenant_id == tenant_id)
        return list(self.db.scalars(stmt.order_by(ReviewTask.created_at.asc())))

    def get_task(self, task_id: str, *, tenant_id: str | None = None) -> ReviewTask:
        from app.db.models import Document as Doc

        stmt = (
            select(ReviewTask)
            .join(ReviewTask.document)
            .where(ReviewTask.id == task_id, Doc.deleted_at.is_(None))
        )
        if tenant_id is None:
            stmt = stmt.where(Doc.tenant_id.is_(None))
        else:
            stmt = stmt.where(Doc.tenant_id == tenant_id)
        task = self.db.scalar(stmt)
        if not task:
            raise HTTPException(status_code=404, detail="Review task not found.")
        return task

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_tasks(self, document: Document, low_confidence_fields: list[dict[str, Any]]) -> None:
        for field in low_confidence_fields:
            task = ReviewTask(
                document_id=document.id,
                field_name=field["name"],
                proposed_value={"value": field["value"]},
                original_value={"value": field["value"]},
                source_snippet=field.get("source_snippet"),
                confidence=field["confidence"],
                # Page-level evidence
                page_number=field.get("page_number", 1),
                bbox=field.get("bbox"),
                validation_reason=field.get("validation_reason"),
            )
            self.db.add(task)
            self.audit.log(
                document_id=document.id,
                event_type=AuditEventType.review_task_created,
                payload={
                    "field_name": field["name"],
                    "confidence": field["confidence"],
                    "page_number": field.get("page_number"),
                    "validation_reason": field.get("validation_reason"),
                },
            )
        self.db.flush()

    def submit_decision(
        self,
        task_id: str,
        payload: ReviewDecisionCreate,
        *,
        tenant_id: str | None = None,
    ) -> ReviewTask:
        task = self.get_task(task_id, tenant_id=tenant_id)
        decision = ReviewDecision(
            review_task_id=task.id,
            reviewer_name=payload.reviewer_name,
            corrected_value=payload.corrected_value,
            comment=payload.comment,
        )
        self.db.add(decision)
        task.status = ReviewStatus.completed

        document = task.document
        corrected_val = payload.corrected_value.get("value")
        original_val = task.original_value.get("value")

        # Update export payload
        if document.extraction_result:
            extraction = document.extraction_result
            export_payload = deepcopy(extraction.export_payload or {})
            normalized_payload = deepcopy(extraction.normalized_payload or {})
            raw_payload = deepcopy(extraction.raw_payload or {})

            deep_set(export_payload, f"fields.{task.field_name}", corrected_val)
            deep_set(normalized_payload, f"fields.{task.field_name}", corrected_val)
            deep_set(raw_payload, f"fields.{task.field_name}", corrected_val)

            field_confidences = export_payload.get("field_confidences", [])
            for item in field_confidences:
                if item.get("name") == task.field_name:
                    item["value"] = corrected_val
                    item["confidence"] = 1.0
                    item["requires_review"] = False

            if field_confidences:
                export_payload["document_confidence"] = round(
                    sum(item.get("confidence", 0.0) for item in field_confidences)
                    / len(field_confidences),
                    4,
                )
                document.document_confidence = export_payload["document_confidence"]

            extraction.export_payload = export_payload
            extraction.normalized_payload = normalized_payload
            extraction.raw_payload = raw_payload
            flag_modified(extraction, "export_payload")
            flag_modified(extraction, "normalized_payload")
            flag_modified(extraction, "raw_payload")
            self.storage.write_export(document.id, export_payload)

        # Auto-complete document when all tasks are resolved
        pending_count = sum(
            1 for t in document.review_tasks if t.status == ReviewStatus.pending and t.id != task.id
        )
        if pending_count == 0 and document.status == DocumentStatus.review_required:
            document.status = DocumentStatus.completed

        self.audit.log(
            document_id=document.id,
            event_type=AuditEventType.review_decision_submitted,
            actor=payload.reviewer_name,
            payload={
                "task_id": task.id,
                "field_name": task.field_name,
                "original_value": original_val,
                "corrected_value": corrected_val,
                "value_changed": str(corrected_val) != str(original_val),
            },
        )
        self.db.commit()
        self.db.refresh(task)
        return task
