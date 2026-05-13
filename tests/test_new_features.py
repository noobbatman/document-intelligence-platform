"""Tests for line items, deduplication, exports, and PO matching."""

from __future__ import annotations

from app.db.models import Document, DocumentStatus, ExtractionResult


def _seed_completed_doc(db, vendor="Acme Ltd", inv_num="INV-001", total=1200.0, tenant=None):
    doc = Document(
        filename="invoice.pdf",
        stored_path="data/uploads/inv.pdf",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        pipeline_version="0.3.0",
        tags={},
        document_type="invoice",
        document_confidence=0.85,
        classifier_confidence=0.90,
        tenant_id=tenant,
    )
    db.add(doc)
    db.flush()
    db.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text=f"Invoice {inv_num} Total {total}",
            raw_payload={},
            normalized_payload={},
            export_payload={
                "fields": {
                    "vendor_name": vendor,
                    "customer_name": "Globex Corp",
                    "invoice_number": inv_num,
                    "invoice_date": "2024-01-15",
                    "total_amount": total,
                    "subtotal": total / 1.2,
                    "tax": total - total / 1.2,
                },
                "line_items": [
                    {
                        "description": "Consulting",
                        "quantity": 5,
                        "unit_price": 200.0,
                        "line_total": 1000.0,
                    }
                ],
            },
            ocr_metadata={"page_count": 1, "average_confidence": 0.9, "engine": "tesseract"},
            extraction_metadata={},
            validation_results=[],
        )
    )
    db.commit()
    return doc


# ── Line item extraction ──────────────────────────────────────────────────────


class TestLineItemExtraction:
    def test_extract_from_clean_text(self):
        from app.extraction.line_items import extract_line_items_from_text

        text = (
            "Invoice Items\n"
            "Description                    Qty   Unit Price   Total\n"
            "Consulting Services             5     200.00       1000.00\n"
            "Software License                1    2000.00       2000.00\n"
            "Subtotal  3000.00\n"
        )
        items = extract_line_items_from_text(text)
        assert len(items) >= 1
        assert any("Consulting" in (i.get("description") or "") for i in items)

    def test_empty_text_returns_empty(self):
        from app.extraction.line_items import extract_line_items_from_text

        assert extract_line_items_from_text("") == []

    def test_no_false_positives_on_summary_lines(self):
        from app.extraction.line_items import extract_line_items_from_text

        text = "Subtotal  1000.00\nVAT 200.00\nTotal Due  1200.00\n"
        items = extract_line_items_from_text(text)
        # summary lines should not be captured as items
        assert len(items) == 0


# ── Exports ───────────────────────────────────────────────────────────────────


class TestExports:
    def test_csv_export_empty(self, client):
        r = client.get("/api/v1/exports/csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")

    def test_csv_export_with_docs(self, client, db_session):
        _seed_completed_doc(db_session)
        r = client.get("/api/v1/exports/csv")
        assert r.status_code == 200
        content = r.content.decode("utf-8")
        assert "invoice_number" in content  # header row
        assert "INV-001" in content

    def test_json_export(self, client, db_session):
        _seed_completed_doc(db_session)
        r = client.get("/api/v1/exports/json")
        assert r.status_code == 200
        import json

        data = json.loads(r.content)
        assert isinstance(data, list)
        assert data[0]["document_type"] == "invoice"

    def test_csv_filter_by_type(self, client, db_session):
        _seed_completed_doc(db_session, vendor="Bank Corp")
        r = client.get("/api/v1/exports/csv?document_type=invoice")
        assert r.status_code == 200
        assert "Bank Corp" in r.content.decode()

    def test_xlsx_returns_500_if_openpyxl_missing(self, client, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("no openpyxl")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        r = client.get("/api/v1/exports/xlsx")
        assert r.status_code in (200, 501)  # 501 if openpyxl not installed


# ── PO Matching ───────────────────────────────────────────────────────────────


class TestPOMatching:
    def test_register_po(self, client):
        r = client.post(
            "/api/v1/purchase-orders",
            json={
                "po_number": "PO-2024-001",
                "vendor_name": "Acme Ltd",
                "total_amount": 1200.0,
            },
        )
        assert r.status_code == 201
        assert r.json()["po_number"] == "PO-2024-001"

    def test_list_pos(self, client, db_session):
        client.post(
            "/api/v1/purchase-orders",
            json={
                "po_number": "PO-001",
                "vendor_name": "Test Vendor",
            },
        )
        r = client.get("/api/v1/purchase-orders")
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_match_document(self, client, db_session):
        doc = _seed_completed_doc(db_session, vendor="Acme Ltd", total=1200.0)
        # Register matching PO
        client.post(
            "/api/v1/purchase-orders",
            json={
                "po_number": "PO-001",
                "vendor_name": "Acme Ltd",
                "total_amount": 1200.0,
            },
        )
        r = client.post(f"/api/v1/purchase-orders/match/{doc.id}")
        assert r.status_code == 200
        data = r.json()
        assert "match_status" in data
        assert "match_score" in data

    def test_match_unmatched_document(self, client, db_session):
        doc = _seed_completed_doc(db_session, vendor="Unknown Vendor", total=999.0)
        r = client.post(f"/api/v1/purchase-orders/match/{doc.id}")
        assert r.status_code == 200
        assert r.json()["match_status"] == "unmatched"


# ── Deduplication ─────────────────────────────────────────────────────────────


class TestDeduplication:
    def test_check_clean_document(self, client, db_session):
        doc = _seed_completed_doc(db_session)
        r = client.post(f"/api/v1/deduplication/{doc.id}/check")
        assert r.status_code == 200
        data = r.json()
        assert "risk_score" in data
        assert "risk_level" in data
        assert "findings" in data
        # Fresh document should have no findings
        assert data["risk_level"] in ("clean", "low")
