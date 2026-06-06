"""Integration tests for document upload, listing, and status endpoints."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

from app.api.deps import db_dependency
from app.db.models import Document, DocumentStatus, ExtractionResult
from app.main import app

# ── Health ────────────────────────────────────────────────────────────────────


def test_liveness_endpoint(client) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_endpoint(client) -> None:
    with patch("redis.from_url") as redis_from_url:
        redis_from_url.return_value.ping.return_value = True
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert data["redis"] == "ok"


def test_readiness_returns_503_when_db_unavailable(client) -> None:
    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    def broken_db():
        yield BrokenSession()

    original_override = app.dependency_overrides[db_dependency]
    app.dependency_overrides[db_dependency] = broken_db
    try:
        with patch("redis.from_url") as redis_from_url:
            redis_from_url.return_value.ping.return_value = True
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides[db_dependency] = original_override

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["database"] == "error"


def test_readiness_returns_503_when_redis_unavailable(client) -> None:
    with patch("redis.from_url") as redis_from_url:
        redis_from_url.return_value.ping.side_effect = RuntimeError("redis unavailable")
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["redis"] == "error"


# ── Upload ────────────────────────────────────────────────────────────────────


def _fake_task():
    m = MagicMock()
    m.id = "task-abc-123"
    return m


def test_upload_document_returns_202(client, monkeypatch) -> None:
    """Upload a valid PDF (mocked content-type) and expect 202 Accepted."""
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    response = client.post(
        "/api/v1/documents/upload",
        files={
            "file": (
                "invoice.pdf",
                BytesIO(b"%PDF-1.4 Invoice Number INV-001 Total $12.00"),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["task_id"] == "task-abc-123"
    assert payload["document"]["filename"] == "invoice.pdf"
    assert payload["document"]["status"] == "queued"


def test_upload_rejects_unsupported_content_type(client) -> None:
    """Plain-text files must be rejected with 415."""
    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("notes.txt", BytesIO(b"some text"), "text/plain")},
    )
    assert response.status_code == 415, response.text


def test_upload_high_priority(client, monkeypatch) -> None:
    """priority=true should dispatch to the high-priority task."""
    dispatched = {}

    def fake_high(doc_id: str, request_id: str | None = None):
        dispatched["doc_id"] = doc_id
        dispatched["request_id"] = request_id
        return _fake_task()

    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_high_priority.delay",
        fake_high,
    )
    response = client.post(
        "/api/v1/documents/upload?priority=true",
        files={"file": ("invoice.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert response.status_code == 202
    assert "doc_id" in dispatched
    assert dispatched["request_id"] is not None


# ── List ──────────────────────────────────────────────────────────────────────


def test_list_documents_empty(client) -> None:
    response = client.get("/api/v1/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert "limit" in body
    assert "offset" in body


def test_list_documents_with_upload(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    client.post(
        "/api/v1/documents/upload",
        files={"file": ("report.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    response = client.get("/api/v1/documents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "report.pdf"


def test_list_documents_status_filter(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    client.post(
        "/api/v1/documents/upload",
        files={"file": ("x.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    r_queued = client.get("/api/v1/documents?status=queued")
    r_completed = client.get("/api/v1/documents?status=completed")
    assert r_queued.json()["total"] == 1
    assert r_completed.json()["total"] == 0


# ── Detail / status ───────────────────────────────────────────────────────────


def test_get_document_not_found(client) -> None:
    response = client.get("/api/v1/documents/nonexistent-id")
    assert response.status_code == 404


def test_get_document_status(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    upload_resp = client.post(
        "/api/v1/documents/upload",
        files={"file": ("check.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    doc_id = upload_resp.json()["document"]["id"]
    status_resp = client.get(f"/api/v1/documents/{doc_id}/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["document_id"] == doc_id


def test_get_document_detail_includes_detected_document_type(client, db_session) -> None:
    document = Document(
        filename="notes.pdf",
        stored_path="data/uploads/notes.pdf",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="unknown",
        pipeline_version="0.3.0",
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(
        ExtractionResult(
            document_id=document.id,
            ocr_text="project notes",
            raw_payload={},
            normalized_payload={},
            export_payload={
                "document_type": "unknown",
                "detected_document_type": "study_notes",
                "fields": {"topic": "classification"},
                "field_confidences": [],
            },
            ocr_metadata={"average_confidence": 0.9, "page_count": 1, "engine": "mock"},
            extraction_metadata={"required_fields": [], "extraction_mode": "llm_open_ended"},
            validation_results=[],
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/documents/{document.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["detected_document_type"] == "study_notes"


# ── Search ────────────────────────────────────────────────────────────────────


def test_search_returns_list(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    client.post(
        "/api/v1/documents/upload",
        files={"file": ("invoice_acme.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    r = client.get("/api/v1/documents/search?q=acme")
    assert r.status_code == 200
    results = r.json()
    assert isinstance(results, list)
    assert any("acme" in d["filename"].lower() for d in results)


# ── Delete ────────────────────────────────────────────────────────────────────


def test_delete_document(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.documents.process_document_task.delay",
        lambda doc_id, request_id=None: _fake_task(),
    )
    upload_resp = client.post(
        "/api/v1/documents/upload",
        files={"file": ("todelete.pdf", BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    doc_id = upload_resp.json()["document"]["id"]
    del_resp = client.delete(f"/api/v1/documents/{doc_id}")
    assert del_resp.status_code == 204
    get_resp = client.get(f"/api/v1/documents/{doc_id}")
    assert get_resp.status_code == 404
