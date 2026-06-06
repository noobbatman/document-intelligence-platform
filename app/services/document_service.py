"""Document service — upload, list, detail, search."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.metrics import documents_uploaded_total, tenant_label
from app.db.models import AuditEventType, Document, DocumentStatus, ExtractionResult
from app.schemas.common import FieldConfidence
from app.schemas.document import DocumentDetail, DocumentRead, ExtractionResultRead, OCRMetadata
from app.services.audit_service import AuditService
from app.storage.factory import get_storage_provider

_ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/webp",
}


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.storage = get_storage_provider()
        self.audit = AuditService(db)

    async def create_document(
        self,
        upload: UploadFile,
        *,
        tenant_id: str | None = None,
        tags: dict | None = None,
    ) -> Document:
        await self._validate_upload(upload)
        stored_path = await self.storage.save_upload(upload)
        document = Document(
            filename=upload.filename or Path(str(stored_path)).name,
            stored_path=str(stored_path),
            content_type=upload.content_type or "application/octet-stream",
            status=DocumentStatus.queued,
            pipeline_version=self.settings.pipeline_version,
            tenant_id=tenant_id,
            tags=tags or {},
        )
        self.db.add(document)
        self.db.flush()
        self.audit.log(
            document_id=document.id,
            event_type=AuditEventType.document_uploaded,
            payload={"filename": document.filename, "path": document.stored_path},
        )
        self.db.commit()
        documents_uploaded_total.labels(tenant_id=tenant_label(tenant_id)).inc()
        self.db.refresh(document)
        return document

    async def _validate_upload(self, upload: UploadFile) -> None:
        content = await upload.read()
        await upload.seek(0)
        size_mb = len(content) / (1024 * 1024)
        if size_mb > self.settings.max_upload_size_mb:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {self.settings.max_upload_size_mb} MB limit.",
            )
        ct = (upload.content_type or "").split(";")[0].strip().lower()
        if ct not in _ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported file type '{ct}'. Allowed: {sorted(_ALLOWED_CONTENT_TYPES)}",
            )

    def list_documents(
        self,
        *,
        status: str | None = None,
        document_type: str | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        query = self._scoped_documents_query(tenant_id)
        if status:
            query = query.where(Document.status == status)
        if document_type:
            query = query.where(Document.document_type == document_type)

        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = list(
            self.db.scalars(query.order_by(Document.created_at.desc()).offset(offset).limit(limit))
        )
        return items, total

    def search(self, query: str, *, limit: int = 20) -> list[Document]:
        return self._search_query(query, tenant_id=None, scoped=False, limit=limit)

    def search_scoped(
        self, query: str, *, limit: int = 20, tenant_id: str | None
    ) -> list[Document]:
        return self._search_query(query, tenant_id=tenant_id, scoped=True, limit=limit)

    def _search_query(
        self,
        query: str,
        *,
        tenant_id: str | None,
        scoped: bool,
        limit: int,
    ) -> list[Document]:
        like = f"%{query}%"
        stmt = (
            select(Document)
            .outerjoin(Document.extraction_result)
            .where(Document.deleted_at.is_(None))
            .where(
                or_(
                    Document.filename.ilike(like),
                    Document.document_type.ilike(like),
                    ExtractionResult.ocr_text.ilike(like),
                )
            )
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        if scoped and tenant_id is not None:
            stmt = stmt.where(Document.tenant_id == tenant_id)
        return list(self.db.scalars(stmt))

    def get_document(self, document_id: str, tenant_id: str | None = None) -> Document:
        stmt = self._scoped_documents_query(tenant_id).where(Document.id == document_id)
        document = self.db.scalar(stmt)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    def get_detail(self, document_id: str, tenant_id: str | None = None) -> DocumentDetail:
        document = self.get_document(document_id, tenant_id=tenant_id)
        result = None
        if document.extraction_result:
            raw_fields = document.extraction_result.export_payload.get("field_confidences", [])
            result = ExtractionResultRead(
                document_id=document.id,
                ocr_text=document.extraction_result.ocr_text,
                export_payload=document.extraction_result.export_payload,
                low_confidence_fields=[
                    FieldConfidence.model_validate(item)
                    for item in raw_fields
                    if item.get("requires_review")
                ],
                ocr_metadata=OCRMetadata(**document.extraction_result.ocr_metadata),
                extraction_metadata=document.extraction_result.extraction_metadata,
                detected_document_type=document.extraction_result.export_payload.get(
                    "detected_document_type"
                ),
            )
        return DocumentDetail(
            document=DocumentRead.model_validate(document),
            result=result,
            audit_logs=document.audit_logs,
        )

    def soft_delete(self, document_id: str, tenant_id: str | None = None) -> None:
        document = self.get_document(document_id, tenant_id=tenant_id)
        document.deleted_at = datetime.now(UTC)
        self.audit.log(
            document_id=document.id,
            event_type=AuditEventType.document_deleted,
            payload={"deleted_at": document.deleted_at.isoformat()},
        )
        self.db.commit()

    def _scoped_documents_query(self, tenant_id: str | None) -> Select[tuple[Document]]:
        query = select(Document).where(Document.deleted_at.is_(None))
        if tenant_id is None:
            return query.where(Document.tenant_id.is_(None))
        return query.where(Document.tenant_id == tenant_id)
