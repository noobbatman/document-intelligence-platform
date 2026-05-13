"""Active-learning feedback service.

When a reviewer corrects a field, the original + corrected values are stored
in CorrectionRecord so they can be exported as labelled training data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import CorrectionRecord, Document
from app.services.audit_service import AuditService


class CorrectionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)
        self.settings = get_settings()

    def record(
        self,
        *,
        document: Document,
        field_name: str,
        original_value: Any,
        corrected_value: Any,
        ocr_snippet: str | None,
        reviewer_name: str,
    ) -> CorrectionRecord:
        record = CorrectionRecord(
            document_id=document.id,
            document_type=document.document_type or "unknown",
            field_name=field_name,
            original_value=str(original_value) if original_value is not None else None,
            corrected_value=str(corrected_value) if corrected_value is not None else None,
            ocr_snippet=ocr_snippet,
            reviewer_name=reviewer_name,
            pipeline_version=document.pipeline_version,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def export_corrections(
        self,
        *,
        tenant_id: str | None = None,
        document_type: str | None = None,
        field_name: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        """Return all corrections as a list of dicts suitable for training data."""
        stmt = select(CorrectionRecord, Document.tenant_id).join(
            Document, Document.id == CorrectionRecord.document_id
        )
        if document_type:
            stmt = stmt.where(CorrectionRecord.document_type == document_type)
        if field_name:
            stmt = stmt.where(CorrectionRecord.field_name == field_name)
        if since:
            stmt = stmt.where(CorrectionRecord.created_at >= since)
        if tenant_id:
            stmt = stmt.where(Document.tenant_id == tenant_id)
        stmt = stmt.where(Document.deleted_at.is_(None))
        stmt = stmt.order_by(CorrectionRecord.created_at.desc())
        rows = list(self.db.execute(stmt).all())
        return [
            {
                "id": record.id,
                "document_id": record.document_id,
                "tenant_id": tenant,
                "document_type": record.document_type,
                "field_name": record.field_name,
                "original_value": record.original_value,
                "corrected_value": record.corrected_value,
                "ocr_snippet": record.ocr_snippet,
                "reviewer_name": record.reviewer_name,
                "pipeline_version": record.pipeline_version,
                "created_at": record.created_at.isoformat(),
            }
            for record, tenant in rows
        ]

    def correction_stats(self, *, tenant_id: str | None = None) -> dict:
        """Return aggregate stats: corrections per field, common failure patterns."""
        stmt = select(CorrectionRecord).join(Document, Document.id == CorrectionRecord.document_id)
        if tenant_id:
            stmt = stmt.where(Document.tenant_id == tenant_id)
        stmt = stmt.where(Document.deleted_at.is_(None))
        all_records = list(self.db.scalars(stmt))
        by_field: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for record in all_records:
            by_field[record.field_name] = by_field.get(record.field_name, 0) + 1
            by_type[record.document_type] = by_type.get(record.document_type, 0) + 1

        top_failing = sorted(by_field.items(), key=lambda x: -x[1])[:10]
        return {
            "tenant_id": tenant_id or "all",
            "total_corrections": len(all_records),
            "by_field": dict(top_failing),
            "by_document_type": by_type,
        }
