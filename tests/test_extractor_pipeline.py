"""Unit tests for document extractors and the full pipeline output contract."""

from __future__ import annotations

import pytest

from app.extraction.factory import get_extractor
from app.ocr.base import OCRResult


def _make_ocr(text: str) -> OCRResult:
    return OCRResult(
        text=text,
        words=[],
        metadata={"average_confidence": 0.9, "page_count": 1, "engine": "mock"},
    )


class TestInvoiceExtractor:
    def test_extracts_known_fields(self) -> None:
        text = (
            "Invoice Number: INV-2024-001\n"
            "Invoice Date: 2024-01-15\n"
            "Due Date: 2024-02-15\n"
            "Bill To: Acme Corp\n"
            "Subtotal: $950.00\n"
            "Tax: $95.00\n"
            "Total Due: $1,045.00"
        )
        result = get_extractor("invoice").extract(_make_ocr(text))
        assert result.document_type == "invoice"
        assert result.fields.get("invoice_number") is not None
        assert result.fields.get("total_amount") is not None
        assert "required_fields" in result.metadata

    def test_missing_fields_are_none_not_missing(self) -> None:
        result = get_extractor("invoice").extract(_make_ocr("just some random text"))
        assert "invoice_number" in result.fields  # key present, value may be None
        assert "total_amount" in result.fields


class TestBankStatementExtractor:
    def test_extracts_balances(self) -> None:
        text = (
            "Statement Period: 2024-07-01 to 2024-07-31\n"
            "Account Number: 0012-3456\n"
            "Opening Balance: £1,200.00\n"
            "Closing Balance: £1,360.00\n"
            "Available Balance: £1,360.00"
        )
        result = get_extractor("bank_statement").extract(_make_ocr(text))
        assert result.document_type == "bank_statement"
        assert result.fields.get("account_number") is not None
        assert result.fields.get("closing_balance") is not None


class TestReceiptExtractor:
    def test_extracts_total(self) -> None:
        text = "RECEIPT\nThank you for your purchase\nTotal Paid: $25.99\nVisa ending 4321"
        result = get_extractor("receipt").extract(_make_ocr(text))
        assert result.document_type == "receipt"
        assert result.fields.get("total_amount") is not None

    def test_extracts_multiline_store_date_and_tax(self) -> None:
        text = "BARDS QUILL\nDate: 08 Jul 2024\nVAT (20% incl.): 2.61\nTotal Paid: 15.00\n"
        result = get_extractor("receipt").extract(_make_ocr(text))
        assert result.fields.get("store_name") == "BARDS QUILL"
        assert result.fields.get("receipt_date") == "08 Jul 2024"
        assert result.fields.get("tax") == 2.61


class TestContractExtractor:
    def test_extracts_parties(self) -> None:
        text = "This Agreement is between Party A: Acme Ltd and Party B: Globex Inc. Effective Date: 2024-01-01. Governing Law: England."
        result = get_extractor("contract").extract(_make_ocr(text))
        assert result.document_type == "contract"
        assert "effective_date" in result.fields


class TestUnknownExtractor:
    def test_returns_unknown_type(self) -> None:
        result = get_extractor("unknown").extract(_make_ocr("some text"))
        assert result.document_type == "unknown"
        assert result.metadata.get("extraction_mode") == "llm_open_ended"


class TestExtractorFactory:
    @pytest.mark.parametrize(
        "doc_type,expected",
        [
            ("invoice", "invoice"),
            ("bank_statement", "bank_statement"),
            ("receipt", "receipt"),
            ("contract", "contract"),
            ("unknown", "unknown"),
            ("garbage_type", "unknown"),  # falls back gracefully
        ],
    )
    def test_factory_registry(self, doc_type: str, expected: str) -> None:
        result = get_extractor(doc_type).extract(_make_ocr(""))
        assert result.document_type == expected
