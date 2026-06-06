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


class TestContractExtractor:
    def test_extracts_parties(self) -> None:
        text = "This Agreement is between Party A: Acme Ltd and Party B: Globex Inc. Effective Date: 2024-01-01. Governing Law: England."
        result = get_extractor("contract").extract(_make_ocr(text))
        assert result.document_type == "contract"
        assert "effective_date" in result.fields


class TestLegalComplaintExtractor:
    def test_extracts_complaint_fields(self) -> None:
        text = (
            "UNITED STATES DISTRICT COURT FOR THE EASTERN DISTRICT OF WISCONSIN\n"
            "Civil Action No. 23-cv-1001\n"
            "Jane Doe, Plaintiff, v. Landmark Credit Union, Defendant.\n"
            "COMPLAINT FOR DAMAGES DEMAND FOR JURY TRIAL\n"
            "This Court has jurisdiction under 28 U.S.C. § 1331 and 28 U.S.C. § 1343.\n"
            "Venue is proper under 28 U.S.C. § 1391(b).\n"
            "COUNT I Violation of the Right to Financial Privacy Act\n"
            "COUNT II Civil Conspiracy\n"
            "PRAYER FOR RELIEF Plaintiffs request declaratory relief and damages.\n"
            "DEMAND FOR JURY TRIAL"
        )
        result = get_extractor("legal_complaint").extract(_make_ocr(text))
        assert result.document_type == "legal_complaint"
        assert result.fields["case_number"] == "23-cv-1001"
        assert result.fields["claims"]
        assert "1331" in result.fields["jurisdiction"]
        assert "1391" in result.fields["venue"]
        assert result.fields["relief_sought"]
        assert result.fields["jury_demand"] is True


class TestUnknownExtractor:
    def test_returns_unknown_type(self) -> None:
        result = get_extractor("unknown").extract(_make_ocr("some text"))
        assert result.document_type == "unknown"
        assert result.metadata.get("extraction_mode") == "llm_open_ended"


class TestExtractorFactory:
    @pytest.mark.parametrize(
        ("doc_type", "expected"),
        [
            ("contract", "contract"),
            ("legal_complaint", "legal_complaint"),
            ("legal_notice", "legal_notice"),
            ("case_brief", "case_brief"),
            ("affidavit", "affidavit"),
            ("unknown", "unknown"),
            ("garbage_type", "unknown"),  # falls back gracefully
        ],
    )
    def test_factory_registry(self, doc_type: str, expected: str) -> None:
        result = get_extractor(doc_type).extract(_make_ocr(""))
        assert result.document_type == expected
