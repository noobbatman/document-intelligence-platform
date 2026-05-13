"""SQLAlchemy ORM models — all timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.vector_types import EmbeddingVector, TextList


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    review_required = "review_required"
    completed = "completed"
    failed = "failed"


class ReviewStatus(StrEnum):
    pending = "pending"
    completed = "completed"
    dismissed = "dismissed"


class AuditEventType(StrEnum):
    document_uploaded = "document_uploaded"
    document_deleted = "document_deleted"
    processing_started = "processing_started"
    processing_completed = "processing_completed"
    processing_failed = "processing_failed"
    review_task_created = "review_task_created"
    review_decision_submitted = "review_decision_submitted"
    document_reprocessed = "document_reprocessed"
    webhook_dispatched = "webhook_dispatched"
    webhook_failed = "webhook_failed"
    correction_exported = "correction_exported"


class WebhookEvent(StrEnum):
    processing_completed = "processing_completed"
    processing_failed = "processing_failed"
    review_required = "review_required"


class WebhookStatus(StrEnum):
    active = "active"
    inactive = "inactive"


class DraftType(StrEnum):
    internal_memo = "internal_memo"
    case_fact_summary = "case_fact_summary"
    contract_summary = "contract_summary"
    notice_summary = "notice_summary"
    document_checklist = "document_checklist"
    affidavit_summary = "affidavit_summary"


class DraftStatus(StrEnum):
    generating = "generating"
    draft = "draft"
    reviewed = "reviewed"
    approved = "approved"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_document_type", "document_type"),
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_tenant_id", "tenant_id"),
        Index("ix_documents_deleted_at", "deleted_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default=DocumentStatus.uploaded)
    document_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(40), nullable=False)
    classifier_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    extraction_result: Mapped[ExtractionResult | None] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )
    review_tasks: Mapped[list[ReviewTask]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    draft_outputs: Mapped[list[DraftOutput]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    export_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ocr_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_results: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    document: Mapped[Document] = relationship(back_populates="extraction_result")


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (Index("ix_review_tasks_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    proposed_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    original_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default=ReviewStatus.pending)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    validation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    document: Mapped[Document] = relationship(back_populates="review_tasks")
    decisions: Mapped[list[ReviewDecision]] = relationship(
        back_populates="review_task", cascade="all, delete-orphan"
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    review_task_id: Mapped[str] = mapped_column(ForeignKey("review_tasks.id"))
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    corrected_value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    review_task: Mapped[ReviewTask] = relationship(back_populates="decisions")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_event_type", "event_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), default="system")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    document: Mapped[Document] = relationship(back_populates="audit_logs")


class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = (
        UniqueConstraint("url", "event", name="uq_webhook_url_event"),
        Index("ix_webhooks_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default=WebhookStatus.active)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FailedWebhookEvent(Base):
    __tablename__ = "failed_webhook_events"
    __table_args__ = (
        Index("ix_failed_webhook_event", "event"),
        Index("ix_failed_webhook_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    webhook_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    webhook_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class CorrectionRecord(Base):
    __tablename__ = "correction_records"
    __table_args__ = (Index("ix_corrections_document_type", "document_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        Index("ix_po_number", "po_number"),
        Index("ix_po_vendor", "vendor_name"),
        Index("ix_po_tenant", "tenant_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    po_number: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="GBP")
    line_items: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), default="open")
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class POMatch(Base):
    __tablename__ = "po_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    po_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"), nullable=True)
    match_status: Mapped[str] = mapped_column(String(40), default="unmatched")
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    discrepancies: Mapped[list] = mapped_column(JSON, default=list)
    matched_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        Index("ix_document_chunks_document_id", "document_id"),
        Index("ix_document_chunks_document_chunk", "document_id", "chunk_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    section_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    document: Mapped[Document] = relationship(back_populates="chunks")


class DraftOutput(Base):
    __tablename__ = "draft_outputs"
    __table_args__ = (
        Index("ix_draft_outputs_document_id", "document_id"),
        Index("ix_draft_outputs_tenant_id", "tenant_id"),
        Index("ix_draft_outputs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    draft_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default=DraftStatus.generating)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_chunk_ids: Mapped[list[str]] = mapped_column(TextList, default=list)
    generation_version: Mapped[int] = mapped_column(Integer, default=1)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    model_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    preferences_applied: Mapped[list[str]] = mapped_column(TextList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    document: Mapped[Document] = relationship(back_populates="draft_outputs")
    edits: Mapped[list[DraftEdit]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )


class DraftEdit(Base):
    __tablename__ = "draft_edits"
    __table_args__ = (
        Index("ix_draft_edits_draft_id", "draft_id"),
        Index("ix_draft_edits_document_id", "document_id"),
        Index("ix_draft_edits_processed", "processed"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    draft_id: Mapped[str] = mapped_column(ForeignKey("draft_outputs.id"), nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    section_key: Mapped[str] = mapped_column(String(120), nullable=False)
    original_content: Mapped[str] = mapped_column(Text, nullable=False)
    edited_content: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    draft: Mapped[DraftOutput] = relationship(back_populates="edits")


class DraftPreference(Base):
    __tablename__ = "draft_preferences"
    __table_args__ = (
        Index("ix_draft_preferences_tenant_type", "tenant_id", "document_type"),
        Index("ix_draft_preferences_source_edit", "source_edit_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    document_type: Mapped[str] = mapped_column(String(80), nullable=False)
    preference_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_edit_id: Mapped[str | None] = mapped_column(ForeignKey("draft_edits.id"), nullable=True)
    embedding: Mapped[list[float]] = mapped_column(EmbeddingVector(768), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    application_count: Mapped[int] = mapped_column(Integer, default=0)
    effectiveness_score: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
