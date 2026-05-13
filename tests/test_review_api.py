"""Integration tests for the review task workflow — including page evidence and correction tracking."""

from __future__ import annotations

from app.db.models import (
    Document,
    DocumentStatus,
    ExtractionResult,
    ReviewStatus,
    ReviewTask,
)


def _seed(db):
    doc = Document(
        filename="invoice.pdf",
        stored_path="data/uploads/invoice.pdf",
        content_type="application/pdf",
        status=DocumentStatus.review_required,
        pipeline_version="0.3.0",
        tags={},
    )
    db.add(doc)
    db.flush()
    db.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text="Invoice Number INV-001 Total GBP 120.00",
            raw_payload={},
            normalized_payload={},
            export_payload={"fields": {"invoice_number": "INV-001", "total_amount": 120.0}},
            ocr_metadata={"page_count": 1, "average_confidence": 0.9, "engine": "tesseract"},
            extraction_metadata={},
            validation_results=[],
        )
    )
    task = ReviewTask(
        document_id=doc.id,
        field_name="invoice_number",
        proposed_value={"value": "INV-001"},
        original_value={"value": "INV-001"},
        source_snippet="Invoice Number INV-001",
        confidence=0.42,
        page_number=1,
        bbox=[50.0, 200.0, 300.0, 215.0],
        validation_reason="invoice_number does not match expected pattern",
    )
    db.add(task)
    db.commit()
    return doc, task


def test_list_pending_includes_page_evidence(client, db_session):
    _seed(db_session)
    r = client.get("/api/v1/reviews/pending")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    t = items[0]
    assert t["page_number"] == 1
    assert t["bbox"] == [50.0, 200.0, 300.0, 215.0]
    assert "pattern" in (t["validation_reason"] or "")


def test_submit_decision_completes_task(client, db_session):
    doc, task = _seed(db_session)
    r = client.post(
        f"/api/v1/reviews/{task.id}/decision",
        json={
            "reviewer_name": "analyst",
            "corrected_value": {"value": "INV-999"},
            "comment": "OCR error",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value_changed"] is True
    db_session.refresh(task)
    db_session.refresh(doc)
    assert task.status == ReviewStatus.completed
    assert doc.status == DocumentStatus.completed


def test_submit_decision_no_change_not_recorded(client, db_session):
    _, task = _seed(db_session)
    r = client.post(
        f"/api/v1/reviews/{task.id}/decision",
        json={
            "reviewer_name": "analyst",
            "corrected_value": {"value": "INV-001"},
            "comment": None,
        },
    )
    assert r.status_code == 200
    assert r.json()["value_changed"] is False


def test_submit_updates_export_payload(client, db_session):
    doc, task = _seed(db_session)
    client.post(
        f"/api/v1/reviews/{task.id}/decision",
        json={
            "reviewer_name": "editor",
            "corrected_value": {"value": "INV-777"},
            "comment": None,
        },
    )
    db_session.refresh(doc)
    assert doc.extraction_result.export_payload["fields"]["invoice_number"] == "INV-777"


def test_get_task_not_found(client):
    r = client.post(
        "/api/v1/reviews/nonexistent/decision",
        json={
            "reviewer_name": "x",
            "corrected_value": {"value": "y"},
            "comment": None,
        },
    )
    assert r.status_code == 404
