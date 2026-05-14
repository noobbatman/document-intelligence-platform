"""Unit tests for HybridDocumentClassifier."""

from __future__ import annotations

import pytest

from app.classification.hybrid_classifier import HybridDocumentClassifier


@pytest.fixture()
def clf():
    return HybridDocumentClassifier()


class TestContractDetection:
    def test_basic_contract(self, clf) -> None:
        text = "This Agreement is entered into between Party A and Party B. Governing Law: England. In Witness Whereof the parties have signed."
        result = clf.classify(text)
        assert result.label == "contract"

    def test_contract_has_rationale(self, clf) -> None:
        result = clf.classify("This Agreement includes indemnification and governing law clauses.")
        assert "keyword_scores" in result.rationale
        assert result.rationale["keyword_scores"].get("contract", 0) > 0


class TestLegalComplaintDetection:
    def test_federal_civil_complaint_beats_contract_terms(self, clf) -> None:
        text = (
            "UNITED STATES DISTRICT COURT\n"
            "Civil Action No. 23-cv-1001\n"
            "COMPLAINT AND JURY TRIAL DEMANDED\n"
            "Jane Doe, Plaintiff v. Landmark Credit Union, Defendant.\n"
            "COUNT I Right to Financial Privacy Act. Prayer for Relief."
        )
        result = clf.classify(text)
        assert result.label == "legal_complaint"


class TestUnknownDocument:
    def test_invoice_text_is_unknown_after_legal_cleanup(self, clf) -> None:
        result = clf.classify("Invoice Number INV-1 Amount Due $20.00 Bill To Acme Corp")
        assert result.label == "unknown"

    def test_generic_substrings_do_not_trigger_known_type(self, clf) -> None:
        text = "Items were stored after the position changed during processing."
        result = clf.classify(text)
        assert result.label == "unknown"

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
            "This Agreement includes governing law and indemnification.",
            "UNITED STATES DISTRICT COURT COMPLAINT COUNT I PRAYER FOR RELIEF",
            "",
        ]:
            result = clf.classify(text)
            assert 0.0 <= result.confidence <= 1.0
