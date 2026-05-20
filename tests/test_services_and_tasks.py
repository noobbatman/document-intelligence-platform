from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.db.models import (
    Document,
    DocumentStatus,
    ExtractionResult,
    ReviewStatus,
    ReviewTask,
    Webhook,
    WebhookStatus,
)
from app.schemas.review import ReviewDecisionCreate
from app.services.pipeline_service import PipelineService
from app.services.review_service import ReviewService
from app.workers import tasks


def _document(**overrides) -> Document:
    values = {
        "filename": "contract.pdf",
        "stored_path": "contract.pdf",
        "content_type": "application/pdf",
        "status": DocumentStatus.queued,
        "document_type": None,
        "pipeline_version": "test",
    }
    values.update(overrides)
    return Document(**values)


def test_review_service_creates_tasks_and_submits_decision(db_session, monkeypatch) -> None:
    document = _document(status=DocumentStatus.review_required, document_confidence=0.4)
    db_session.add(document)
    db_session.flush()
    extraction = ExtractionResult(
        document_id=document.id,
        raw_payload={"fields": {"amount": "$10"}},
        normalized_payload={"fields": {"amount": "$10"}},
        export_payload={
            "fields": {"amount": "$10"},
            "field_confidences": [{"name": "amount", "value": "$10", "confidence": 0.4}],
        },
    )
    db_session.add(extraction)
    db_session.commit()

    written_exports: list[tuple[str, dict]] = []
    service = ReviewService(db_session)
    monkeypatch.setattr(
        service.storage,
        "write_export",
        lambda doc_id, payload: written_exports.append((doc_id, payload)),
    )

    service.create_tasks(
        document,
        [
            {
                "name": "amount",
                "value": "$10",
                "confidence": 0.4,
                "source_snippet": "Total amount $10",
                "page_number": 2,
                "bbox": [1, 2, 3, 4],
                "validation_reason": "too low",
            }
        ],
    )
    db_session.commit()
    task = db_session.query(ReviewTask).filter_by(document_id=document.id).one()

    result = service.submit_decision(
        task.id,
        ReviewDecisionCreate(
            reviewer_name="analyst",
            corrected_value={"value": "$100"},
            comment="fixed decimal place",
        ),
    )

    assert result.status == ReviewStatus.completed
    assert document.status == DocumentStatus.completed
    assert extraction.export_payload["fields"]["amount"] == "$100"
    assert extraction.export_payload["field_confidences"][0]["confidence"] == 1.0
    assert written_exports[0][0] == document.id
    assert service.list_pending() == []


def test_pipeline_service_success_path_updates_document_and_dispatches(
    db_session, monkeypatch
) -> None:
    document = _document(status=DocumentStatus.queued, stored_path="local.pdf")
    db_session.add(document)
    db_session.commit()

    output = {
        "ocr_text": "Agreement text",
        "raw_payload": {"fields": {"party": "Acme"}},
        "normalized_payload": {"fields": {"party": "Acme"}},
        "export_payload": {"fields": {"party": "Acme"}},
        "ocr_metadata": {"average_confidence": 0.95},
        "extraction_metadata": {
            "validation_results": [{"field": "party", "valid": True, "reason": "ok"}]
        },
        "document_type": "contract",
        "classifier_confidence": 0.9,
        "document_confidence": 0.93,
        "low_confidence_fields": [],
    }

    service = PipelineService(db_session)
    monkeypatch.setattr(service.pipeline, "run", lambda path: output)
    monkeypatch.setattr(service.storage, "write_export", Mock())
    monkeypatch.setattr(service.webhook_service, "dispatch_event", Mock(return_value=[]))
    monkeypatch.setattr("app.workers.tasks.embed_document_task.apply_async", Mock())

    result = service.process_document(document.id, correlation_id="corr-1")

    assert result == output
    assert document.status == DocumentStatus.completed
    assert document.document_type == "contract"
    assert document.extraction_result.export_payload == {"fields": {"party": "Acme"}}
    service.storage.write_export.assert_called_once()
    service.webhook_service.dispatch_event.assert_called_once()


def test_pipeline_service_failure_marks_document_failed_and_dispatches(
    db_session, monkeypatch
) -> None:
    document = _document(status=DocumentStatus.queued, stored_path="broken.pdf")
    db_session.add(document)
    db_session.commit()

    service = PipelineService(db_session)
    monkeypatch.setattr(service.pipeline, "run", Mock(side_effect=RuntimeError("ocr failed")))
    monkeypatch.setattr(service.webhook_service, "dispatch_event", Mock(return_value=[]))

    with pytest.raises(RuntimeError, match="ocr failed"):
        service.process_document(document.id, correlation_id="corr-2")

    assert document.status == DocumentStatus.failed
    assert document.error_message == "ocr failed"
    service.webhook_service.dispatch_event.assert_called_once()


def test_pipeline_service_missing_document_raises(db_session) -> None:
    with pytest.raises(ValueError, match="not found"):
        PipelineService(db_session).process_document("missing")


def test_batch_process_task_enqueues_each_document(monkeypatch) -> None:
    enqueued: list[tuple[list[str], dict]] = []

    def fake_apply_async(args, kwargs):
        enqueued.append((args, kwargs))
        return SimpleNamespace(id=f"task-{args[0]}")

    monkeypatch.setattr(tasks.process_document_task, "apply_async", fake_apply_async)

    result = tasks.batch_process_task.run(
        ["doc-1", "doc-2"],
        request_id="req-1",
        correlation_id="corr-1",
    )

    assert result == {"enqueued": {"doc-1": "task-doc-1", "doc-2": "task-doc-2"}}
    assert enqueued[0][1] == {"request_id": "req-1", "correlation_id": "corr-1"}


def test_run_processing_uses_effective_correlation_id_and_closes_session(monkeypatch) -> None:
    fake_db = Mock()
    captured: dict[str, str | None] = {}

    class FakePipelineService:
        def __init__(self, db) -> None:
            assert db is fake_db

        def process_document(self, document_id: str, correlation_id: str | None = None) -> dict:
            captured["document_id"] = document_id
            captured["correlation_id"] = correlation_id
            return {"ok": True}

    monkeypatch.setattr(tasks, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(tasks, "PipelineService", FakePipelineService)

    result = tasks._run_processing(
        SimpleNamespace(request=SimpleNamespace(id="task-id")),
        "doc-1",
        request_id="req-1",
    )

    assert result == {"ok": True}
    assert captured == {"document_id": "doc-1", "correlation_id": "req-1"}
    fake_db.close.assert_called_once()


def test_dispatch_webhook_task_signs_success_and_skips_inactive(db_session, monkeypatch) -> None:
    active = Webhook(
        name="active",
        url="https://example.test/hook",
        event="document.completed",
        secret="secret",
    )
    inactive = Webhook(
        name="inactive",
        url="https://example.test/inactive",
        event="document.completed",
        status=WebhookStatus.inactive,
    )
    db_session.add_all([active, inactive])
    db_session.commit()
    active_id = active.id
    active_url = active.url
    inactive_id = inactive.id

    class FakeSessionFactory:
        def __call__(self):
            return db_session

    posted: dict = {}

    class FakeClient:
        def __init__(self, timeout: int) -> None:
            posted["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url: str, content: str, headers: dict):
            posted.update({"url": url, "content": content, "headers": headers})
            return SimpleNamespace(status_code=204, raise_for_status=lambda: None)

    monkeypatch.setattr(tasks, "SessionLocal", FakeSessionFactory())
    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = tasks.dispatch_webhook_task.run(active_id, "document.completed", {"id": "doc-1"})
    skipped = tasks.dispatch_webhook_task.run(inactive_id, "document.completed", {"id": "doc-1"})

    assert result == {"status": 204}
    assert skipped == {"skipped": True}
    assert posted["url"] == active_url
    assert posted["headers"]["X-DocintelSignature"].startswith("sha256=")


def test_simple_worker_tasks_delegate_to_services(monkeypatch) -> None:
    fake_db = Mock()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: fake_db)

    monkeypatch.setattr(
        "app.rag.embedding_service.EmbeddingService",
        lambda: SimpleNamespace(embed_document=lambda document_id, db: 7),
    )
    monkeypatch.setattr(
        "app.rag.draft_service.DraftService",
        lambda db: SimpleNamespace(
            generate=lambda document_id, draft_type, tenant_id, draft_id=None: SimpleNamespace(
                id="draft-1", status="draft"
            )
        ),
    )
    monkeypatch.setattr(
        "app.rag.preference_service.PreferenceService",
        lambda db: SimpleNamespace(extract_from_edit=lambda edit_id: SimpleNamespace(id="pref-1")),
    )

    assert tasks.embed_document_task.run("doc-1") == {
        "document_id": "doc-1",
        "chunks": 7,
    }
    assert tasks.generate_draft_task.run(
        "doc-1", "contract_summary", tenant_id=None, draft_id="draft-1"
    ) == {"document_id": "doc-1", "draft_id": "draft-1", "status": "draft"}
    assert tasks.extract_preferences_task.run("edit-1") == {
        "edit_id": "edit-1",
        "preference_id": "pref-1",
    }
    assert fake_db.close.call_count == 3
