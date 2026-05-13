"""Tests for analytics, corrections, and tenant-scoped metrics."""

from __future__ import annotations

from app.db.models import (
    CorrectionRecord,
    Document,
    DocumentStatus,
    ExtractionResult,
)


def _seed_doc(db, tenant=None):
    doc = Document(
        filename="inv.pdf",
        stored_path="data/uploads/inv.pdf",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        pipeline_version="0.2.0",
        tags={},
        document_type="invoice",
        document_confidence=0.82,
        tenant_id=tenant,
    )
    db.add(doc)
    db.flush()
    db.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text="Invoice INV-001",
            raw_payload={},
            normalized_payload={},
            export_payload={"fields": {"invoice_number": "INV-001"}},
            ocr_metadata={"page_count": 1, "average_confidence": 0.88, "engine": "tesseract"},
            extraction_metadata={},
            validation_results=[],
        )
    )
    db.commit()
    return doc


def test_overview_metrics_empty(client):
    r = client.get("/api/v1/analytics/metrics/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total_documents"] == 0
    assert data["pending_review_tasks"] == 0


def test_overview_metrics_with_docs(client, db_session):
    _seed_doc(db_session)
    r = client.get("/api/v1/analytics/metrics/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total_documents"] == 1
    assert data["by_document_type"]["invoice"] == 1
    assert data["avg_document_confidence"] == 0.82


def test_overview_metrics_tenant_scoped(client, db_session):
    _seed_doc(db_session, tenant="tenant-a")
    _seed_doc(db_session, tenant="tenant-b")
    r = client.get("/api/v1/analytics/metrics/overview", headers={"X-Tenant-ID": "tenant-a"})
    assert r.status_code == 200
    assert r.json()["total_documents"] == 1
    assert r.json()["tenant_id"] == "tenant-a"


def test_ocr_distribution(client, db_session):
    _seed_doc(db_session)
    r = client.get("/api/v1/analytics/metrics/ocr-distribution")
    assert r.status_code == 200
    assert "buckets" in r.json()


def test_corrections_empty(client):
    r = client.get("/api/v1/analytics/corrections")
    assert r.status_code == 200
    assert r.json() == []


def test_corrections_stats(client, db_session):
    doc = _seed_doc(db_session)
    cr = CorrectionRecord(
        document_id=doc.id,
        document_type="invoice",
        field_name="invoice_number",
        original_value="INV-001",
        corrected_value="INV-002",
        ocr_snippet="Invoice INV-001",
        reviewer_name="qa",
        pipeline_version="0.2.0",
    )
    db_session.add(cr)
    db_session.commit()
    r = client.get("/api/v1/analytics/corrections/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total_corrections"] == 1
    assert data["by_field"]["invoice_number"] == 1


def test_corrections_list_tenant_scoped(client, db_session):
    tenant_a_doc = _seed_doc(db_session, tenant="tenant-a")
    tenant_b_doc = _seed_doc(db_session, tenant="tenant-b")
    db_session.add_all(
        [
            CorrectionRecord(
                document_id=tenant_a_doc.id,
                document_type="invoice",
                field_name="invoice_number",
                original_value="INV-001",
                corrected_value="INV-101",
                ocr_snippet="Invoice INV-001",
                reviewer_name="qa-a",
                pipeline_version="0.3.0",
            ),
            CorrectionRecord(
                document_id=tenant_b_doc.id,
                document_type="invoice",
                field_name="invoice_number",
                original_value="INV-001",
                corrected_value="INV-202",
                ocr_snippet="Invoice INV-001",
                reviewer_name="qa-b",
                pipeline_version="0.3.0",
            ),
        ]
    )
    db_session.commit()

    r = client.get("/api/v1/analytics/corrections", headers={"X-Tenant-ID": "tenant-a"})

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["tenant_id"] == "tenant-a"
    assert data[0]["corrected_value"] == "INV-101"


def test_corrections_stats_tenant_scoped(client, db_session):
    tenant_a_doc = _seed_doc(db_session, tenant="tenant-a")
    tenant_b_doc = _seed_doc(db_session, tenant="tenant-b")
    db_session.add_all(
        [
            CorrectionRecord(
                document_id=tenant_a_doc.id,
                document_type="invoice",
                field_name="invoice_number",
                original_value="INV-001",
                corrected_value="INV-101",
                ocr_snippet="Invoice INV-001",
                reviewer_name="qa-a",
                pipeline_version="0.3.0",
            ),
            CorrectionRecord(
                document_id=tenant_b_doc.id,
                document_type="invoice",
                field_name="closing_balance",
                original_value="100.00",
                corrected_value="120.00",
                ocr_snippet="Closing balance 100.00",
                reviewer_name="qa-b",
                pipeline_version="0.3.0",
            ),
        ]
    )
    db_session.commit()

    r = client.get("/api/v1/analytics/corrections/stats", headers={"X-Tenant-ID": "tenant-a"})

    assert r.status_code == 200
    data = r.json()
    assert data["tenant_id"] == "tenant-a"
    assert data["total_corrections"] == 1
    assert data["by_field"] == {"invoice_number": 1}
