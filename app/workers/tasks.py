"""Celery tasks: document processing, webhook dispatch, batch processing, email ingestion."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC
from typing import Any

import httpx
from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import webhooks_dispatched_total
from app.db.session import SessionLocal
from app.services.pipeline_service import PipelineService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
settings = get_settings()


def _run_processing(
    self: Any,
    document_id: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    db = SessionLocal()
    try:
        effective_correlation_id = correlation_id or request_id or str(self.request.id)
        logger.info(
            "processing_document",
            extra={
                "document_id": document_id,
                "task_id": self.request.id,
                "correlation_id": effective_correlation_id,
            },
        )
        service = PipelineService(db)
        return service.process_document(document_id, correlation_id=effective_correlation_id)
    except SoftTimeLimitExceeded:
        logger.error(
            "task_soft_time_limit_exceeded",
            extra={"document_id": document_id, "correlation_id": correlation_id},
        )
        raise
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_kwargs={"max_retries": 3},
    name="app.workers.tasks.process_document_task",
)
def process_document_task(
    self,
    document_id: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    return _run_processing(self, document_id, request_id=request_id, correlation_id=correlation_id)


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_kwargs={"max_retries": 5},
    name="app.workers.tasks.process_document_high_priority",
)
def process_document_high_priority(
    self,
    document_id: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    return _run_processing(self, document_id, request_id=request_id, correlation_id=correlation_id)


@celery_app.task(bind=True, name="app.workers.tasks.batch_process_task")
def batch_process_task(
    self,
    document_ids: list[str],
    request_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    results: dict[str, str] = {}
    for doc_id in document_ids:
        task = process_document_task.apply_async(
            args=[doc_id],
            kwargs={"request_id": request_id, "correlation_id": correlation_id},
        )
        results[doc_id] = str(task.id)
        logger.info("batch_enqueued", extra={"document_id": doc_id, "task_id": task.id})
    return {"enqueued": results}


@celery_app.task(
    bind=True,
    autoretry_for=(httpx.RequestError, httpx.HTTPStatusError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_kwargs={"max_retries": settings.webhook_max_retries},
    name="app.workers.tasks.dispatch_webhook_task",
)
def dispatch_webhook_task(self, webhook_id: str, event: str, payload: dict) -> dict:
    from datetime import datetime

    from app.db.models import Webhook, WebhookStatus

    db = SessionLocal()
    try:
        webhook = db.get(Webhook, webhook_id)
        if not webhook or webhook.status != WebhookStatus.active:
            return {"skipped": True}

        body = json.dumps({"event": event, "payload": payload})
        headers = {"Content-Type": "application/json", "X-DocintelEvent": event}
        if webhook.secret:
            sig = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-DocintelSignature"] = f"sha256={sig}"

        with httpx.Client(timeout=settings.webhook_timeout_seconds) as client:
            resp = client.post(webhook.url, content=body, headers=headers)
            resp.raise_for_status()

        webhook.last_triggered_at = datetime.now(UTC)
        webhook.failure_count = 0
        db.commit()
        webhooks_dispatched_total.labels(event=event, success="true").inc()
        logger.info("webhook_dispatched", extra={"webhook_id": webhook_id, "status": resp.status_code})
        return {"status": resp.status_code}

    except Exception as exc:
        if db.is_active:
            wh = db.get(Webhook, webhook_id)
            if wh:
                wh.failure_count += 1
                db.commit()

        webhooks_dispatched_total.labels(event=event, success="false").inc()
        logger.error("webhook_failed", extra={"webhook_id": webhook_id, "error": str(exc)})

        if self.request.retries >= settings.webhook_max_retries:
            try:
                from app.services.webhook_service import WebhookService

                webhook_obj = db.get(Webhook, webhook_id)
                webhook_url = webhook_obj.url if webhook_obj else "unknown"
                WebhookService(db).record_failed_delivery(
                    webhook_id=webhook_id,
                    webhook_url=webhook_url,
                    event=event,
                    payload=payload,
                    error_detail=str(exc),
                    attempts=self.request.retries + 1,
                )
                logger.warning(
                    "webhook_dead_lettered",
                    extra={
                        "webhook_id": webhook_id,
                        "event": event,
                        "attempts": self.request.retries + 1,
                    },
                )
            except Exception as dl_exc:
                logger.error("dead_letter_write_failed", extra={"error": str(dl_exc)})
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="app.workers.tasks.poll_email_task")
def poll_email_task(self) -> dict:
    from app.db.models import Document, DocumentStatus
    from app.services.email_ingestion_service import EmailIngestionService

    svc = EmailIngestionService()
    if not svc.is_configured():
        return {"skipped": True, "reason": "Email not configured"}

    attachments = svc.poll()
    if not attachments:
        return {"enqueued": 0}

    db = SessionLocal()
    enqueued: list[dict] = []
    try:
        for att in attachments:
            doc = Document(
                filename=att["original_filename"],
                stored_path=att["stored_path"],
                content_type=att["content_type"],
                status=DocumentStatus.queued,
                pipeline_version=settings.pipeline_version,
                tags={
                    "source": "email",
                    "sender": att.get("sender", ""),
                    "subject": att.get("subject", ""),
                },
            )
            db.add(doc)
            db.flush()
            task = process_document_task.delay(doc.id)
            enqueued.append({"document_id": doc.id, "task_id": str(task.id)})
        db.commit()
    finally:
        db.close()

    return {"enqueued": len(enqueued), "documents": enqueued}


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    name="app.workers.tasks.embed_document_task",
)
def embed_document_task(self, document_id: str) -> dict:
    from app.rag.embedding_service import EmbeddingService

    db = SessionLocal()
    try:
        count = EmbeddingService().embed_document(document_id, db)
        return {"document_id": document_id, "chunks": count}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    name="app.workers.tasks.generate_draft_task",
)
def generate_draft_task(
    self,
    document_id: str,
    draft_type: str,
    tenant_id: str | None = None,
    draft_id: str | None = None,
) -> dict:
    from app.rag.draft_service import DraftService

    db = SessionLocal()
    try:
        draft = DraftService(db).generate(document_id, draft_type, tenant_id, draft_id=draft_id)
        return {"document_id": document_id, "draft_id": draft.id, "status": draft.status}
    finally:
        db.close()


@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    name="app.workers.tasks.extract_preferences_task",
)
def extract_preferences_task(self, edit_id: str) -> dict:
    from app.rag.preference_service import PreferenceService

    db = SessionLocal()
    try:
        pref = PreferenceService(db).extract_from_edit(edit_id)
        return {"edit_id": edit_id, "preference_id": pref.id if pref else None}
    finally:
        db.close()
