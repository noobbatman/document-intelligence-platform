from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import FieldConfidence


class OCRMetadata(BaseModel):
    average_confidence: float = 0.0
    page_count: int = 0
    engine: str = "unknown"


class DocumentRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    filename: str
    content_type: str
    status: str
    document_type: str | None = None
    pipeline_version: str
    classifier_confidence: float | None = None
    document_confidence: float | None = None
    error_message: str | None = None
    tenant_id: str | None = None
    tags: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ExtractionResultRead(BaseModel):
    document_id: str
    ocr_text: str
    export_payload: dict[str, Any]
    low_confidence_fields: list[FieldConfidence]
    ocr_metadata: OCRMetadata
    extraction_metadata: dict[str, Any]
    detected_document_type: str | None = None


class DocumentDetail(BaseModel):
    document: DocumentRead
    result: ExtractionResultRead | None = None
    audit_logs: list[Any] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    document: DocumentRead
    task_id: str


class BatchUploadResponse(BaseModel):
    items: list[DocumentUploadResponse]


class ReprocessResponse(BaseModel):
    document_id: str
    task_id: str
    status: str


class DocumentListResponse(BaseModel):
    """Paginated document list — offset-based cursor."""

    items: list[DocumentRead]
    total: int
    limit: int
    offset: int
