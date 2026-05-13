"""Unit tests for HybridDocumentClassifier."""

from __future__ import annotations

import pytest

from app.classification.hybrid_classifier import HybridDocumentClassifier


@pytest.fixture()
def clf():
    return HybridDocumentClassifier()


class TestInvoiceDetection:
    def test_basic_invoice(self, clf) -> None:
        result = clf.classify("Invoice Number INV-1 Amount Due $20.00 Bill To Acme Corp")
        assert result.label == "invoice"
        assert result.confidence >= 0.5

    def test_invoice_with_line_items(self, clf) -> None:
        text = "INVOICE\nInvoice No: INV-2024-001\nBill To: John Doe\nQty  Unit Price  Subtotal\n1    100.00     100.00\nTax: 10.00\nTotal Due: 110.00"
        result = clf.classify(text)
        assert result.label == "invoice"
        assert result.confidence >= 0.6

    def test_invoice_has_rationale(self, clf) -> None:
        result = clf.classify("Invoice Number INV-001 Amount Due $50")
        assert "keyword_scores" in result.rationale
        assert result.rationale["keyword_scores"].get("invoice", 0) > 0


class TestBankStatementDetection:
    def test_basic_statement(self, clf) -> None:
        text = "Statement Period 2025-07-01 to 2025-07-31 Account Number 123456 Closing Balance $400.00"
        result = clf.classify(text)
        assert result.label == "bank_statement"
        assert result.confidence >= 0.5

    def test_full_statement_text(self, clf) -> None:
        text = (
            "Monthly Statement  Account Number: 0012-3456  IBAN: GB29NWBK60161331926819\n"
            "Opening Balance: £1,200.00\nDebits: £340.00   Credits: £500.00\n"
            "Closing Balance: £1,360.00   Available Balance: £1,360.00"
        )
        result = clf.classify(text)
        assert result.label == "bank_statement"


class TestReceiptDetection:
    def test_basic_receipt(self, clf) -> None:
        text = "RECEIPT\nThank you for your purchase\nTotal Paid: $25.99\nChange Due: $0.01"
        result = clf.classify(text)
        assert result.label == "receipt"

    def test_generic_substrings_do_not_trigger_receipt(self, clf) -> None:
        text = "Items were stored after the position changed during processing."
        result = clf.classify(text)
        assert result.label == "unknown"


class TestContractDetection:
    def test_basic_contract(self, clf) -> None:
        text = "This Agreement is entered into between Party A and Party B. Governing Law: England. In Witness Whereof the parties have signed."
        result = clf.classify(text)
        assert result.label == "contract"


class TestUnknownDocument:
    def test_empty_text_returns_unknown(self, clf) -> None:
        result = clf.classify("")
        assert result.label == "unknown"
        assert result.confidence <= 0.3

    def test_gibberish_returns_unknown(self, clf) -> None:
        result = clf.classify("xyzzy lorem ipsum dolor sit amet consectetur")
        assert result.label == "unknown"


class TestConfidenceRange:
    def test_confidence_in_valid_range(self, clf) -> None:
        for text in [
            "Invoice Number INV-001 Amount Due $100",
            "Statement Period 2024-01 Closing Balance $500",
            "",
        ]:
            result = clf.classify(text)
            assert 0.0 <= result.confidence <= 1.0
