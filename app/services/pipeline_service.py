"""Pipeline service — orchestrates OCR → classify → extract → validate → review → webhook."""

from __future__ import annotations

from contextlib import suppress
from time import perf_counter

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.metrics import (
    document_confidence_histogram,
    documents_processed_total,
    field_validation_failures_total,
    ocr_confidence_histogram,
    pipeline_latency_seconds,
    review_tasks_total,
    tenant_label,
)
from app.db.models import AuditEventType, Document, DocumentStatus, ExtractionResult, WebhookEvent
from app.pipelines.document_pipeline import DocumentPipeline
from app.services.audit_service import AuditService
from app.services.review_service import ReviewService
from app.services.webhook_service import WebhookService
from app.storage.factory import get_storage_provider

logger = get_logger(__name__)


class PipelineService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)
        self.review_service = ReviewService(db)
        self.webhook_service = WebhookService(db)
        self.storage = get_storage_provider()
        self.pipeline = DocumentPipeline()

    def process_document(self, document_id: str, correlation_id: str | None = None) -> dict:
        document = self.db.get(Document, document_id)
        if not document:
            raise ValueError(f"Document {document_id} not found.")

        tenant = tenant_label(document.tenant_id)
        started = perf_counter()

        document.status = DocumentStatus.processing
        self.audit.log(
            document_id=document_id,
            event_type=AuditEventType.processing_started,
            payload={},
            correlation_id=correlation_id,
        )
        self.db.commit()

        try:
            file_path = self._resolve_path(document.stored_path)
            try:
                output = self.pipeline.run(str(file_path))
            finally:
                self._cleanup_tmp(str(file_path), document.stored_path)

            existing = document.extraction_result
            if existing is None:
                existing = ExtractionResult(document_id=document.id)
                self.db.add(existing)

            existing.ocr_text = output["ocr_text"]
            existing.raw_payload = output["raw_payload"]
            existing.normalized_payload = output["normalized_payload"]
            existing.export_payload = output["export_payload"]
            existing.ocr_metadata = output["ocr_metadata"]
            existing.extraction_metadata = output["extraction_metadata"]
            existing.validation_results = output["extraction_metadata"].get(
                "validation_results", []
            )

            document.document_type = output["document_type"]
            document.classifier_confidence = output["classifier_confidence"]
            document.document_confidence = output["document_confidence"]

            for task in list(document.review_tasks):
                self.db.delete(task)
            self.db.flush()

            low_conf = output["low_confidence_fields"]
            if low_conf:
                document.status = DocumentStatus.review_required
                self.review_service.create_tasks(document, low_conf)
                review_tasks_total.labels(status="pending").inc(len(low_conf))
                webhook_event = WebhookEvent.review_required
            else:
                document.status = DocumentStatus.completed
                webhook_event = WebhookEvent.processing_completed

            self.storage.write_export(document.id, existing.export_payload)

            elapsed = perf_counter() - started
            validation_failures = sum(
                1
                for v in existing.validation_results
                if not v.get("valid") and not v["field"].startswith("_cross")
            )

            self.audit.log(
                document_id=document.id,
                event_type=AuditEventType.processing_completed,
                payload={
                    "document_type": document.document_type,
                    "doc_confidence": document.document_confidence,
                    "review_tasks": len(low_conf),
                    "latency_seconds": round(elapsed, 3),
                    "validation_failures": validation_failures,
                },
                correlation_id=correlation_id,
            )
            self.db.commit()

            documents_processed_total.labels(
                status=document.status,
                document_type=document.document_type or "unknown",
                tenant_id=tenant,
            ).inc()
            pipeline_latency_seconds.observe(elapsed)
            document_confidence_histogram.observe(document.document_confidence or 0.0)
            ocr_confidence_histogram.observe(output["ocr_metadata"].get("average_confidence", 0.0))

            for v in existing.validation_results:
                if not v.get("valid") and not v["field"].startswith("_cross"):
                    field_validation_failures_total.labels(
                        document_type=document.document_type or "unknown",
                        field_name=v["field"],
                    ).inc()

            self.webhook_service.dispatch_event(
                event=str(webhook_event),
                payload={
                    "document_id": document.id,
                    "document_type": document.document_type,
                    "status": document.status,
                    "doc_confidence": document.document_confidence,
                    "tenant_id": tenant,
                    "correlation_id": correlation_id,
                },
            )

            try:
                from app.workers.tasks import embed_document_task

                embed_document_task.apply_async(args=[document.id], queue="documents.normal")
            except Exception as embed_exc:
                logger.warning(
                    "embedding_enqueue_failed",
                    extra={"document_id": document.id, "error": str(embed_exc)},
                )

            return output

        except Exception as exc:
            document.status = DocumentStatus.failed
            document.error_message = str(exc)
            self.audit.log(
                document_id=document.id,
                event_type=AuditEventType.processing_failed,
                payload={"error": str(exc)},
                correlation_id=correlation_id,
            )
            self.db.commit()
            documents_processed_total.labels(
                status="failed",
                document_type=document.document_type or "unknown",
                tenant_id=tenant,
            ).inc()
            try:
                self.webhook_service.dispatch_event(
                    event=str(WebhookEvent.processing_failed),
                    payload={
                        "document_id": document.id,
                        "error": str(exc),
                        "tenant_id": tenant,
                        "correlation_id": correlation_id,
                    },
                )
            except Exception as wh_exc:
                logger.warning(
                    "webhook_dispatch_failed_after_processing_error",
                    extra={"document_id": document.id, "error": str(wh_exc)},
                )
            raise

    def _resolve_path(self, stored_path: str) -> str:
        if stored_path.startswith("s3://"):
            from app.storage.s3 import S3StorageProvider

            return str(S3StorageProvider().download_to_tmp(stored_path))
        return stored_path

    def _cleanup_tmp(self, path: str, stored_path: str) -> None:
        if stored_path.startswith("s3://"):
            import os

            with suppress(OSError):
                os.unlink(path)
