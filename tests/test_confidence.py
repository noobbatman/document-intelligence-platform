"""Tests for the multi-signal ConfidenceScorer."""

from __future__ import annotations

import pytest

from app.pipelines.confidence import ConfidenceScorer


@pytest.fixture
def scorer():
    return ConfidenceScorer(threshold=0.75)


def test_missing_required_flags_review(scorer):
    fc = scorer.score_fields(
        fields={"invoice_number": None, "total_amount": 100.0},
        snippets={"invoice_number": None, "total_amount": "$100"},
        ocr_confidence=0.9,
        classifier_confidence=0.9,
        required_fields=["invoice_number", "total_amount"],
    )
    inv = next(f for f in fc if f.name == "invoice_number")
    tot = next(f for f in fc if f.name == "total_amount")
    assert inv.requires_review is True
    assert tot.requires_review is False


def test_cross_field_total_consistency_boosts_confidence(scorer):
    fc = scorer.score_fields(
        fields={"subtotal": 100.0, "tax": 20.0, "total_amount": 120.0},
        snippets={k: None for k in ("subtotal", "tax", "total_amount")},
        ocr_confidence=0.9,
        classifier_confidence=0.9,
        required_fields=[],
    )
    tot = next(f for f in fc if f.name == "total_amount")
    assert tot.confidence >= 0.70


def test_inconsistent_total_lowers_confidence(scorer):
    # subtotal + tax = 130 but total says 200 — big discrepancy
    fc = scorer.score_fields(
        fields={"subtotal": 100.0, "tax": 30.0, "total_amount": 200.0},
        snippets={k: None for k in ("subtotal", "tax", "total_amount")},
        ocr_confidence=0.9,
        classifier_confidence=0.9,
        required_fields=[],
    )
    tot = next(f for f in fc if f.name == "total_amount")
    # Should have lower confidence than the consistent case
    assert tot.confidence < 0.85


def test_format_validation_raises_bad_invoice_id(scorer):
    fc = scorer.score_fields(
        fields={"invoice_number": "!@#$"},
        snippets={"invoice_number": None},
        ocr_confidence=0.9,
        classifier_confidence=0.9,
        required_fields=["invoice_number"],
    )
    inv = next(f for f in fc if f.name == "invoice_number")
    assert inv.requires_review is True


def test_valid_invoice_id_high_confidence(scorer):
    fc = scorer.score_fields(
        fields={"invoice_number": "INV-2024-001"},
        snippets={"invoice_number": "Invoice Number INV-2024-001"},
        ocr_confidence=0.95,
        classifier_confidence=0.95,
        required_fields=["invoice_number"],
    )
    inv = next(f for f in fc if f.name == "invoice_number")
    assert inv.confidence >= 0.70
    assert inv.requires_review is False


def test_all_confidence_values_in_range(scorer):
    fc = scorer.score_fields(
        fields={"f": "v", "g": None, "h": 100.0},
        snippets={"f": None, "g": None, "h": None},
        ocr_confidence=0.5,
        classifier_confidence=0.5,
        required_fields=[],
    )
    for f in fc:
        assert 0.0 <= f.confidence <= 1.0


def test_document_score_bounded(scorer):
    fc = scorer.score_fields(
        fields={"invoice_number": "INV-1", "total_amount": 99.99},
        snippets={k: None for k in ("invoice_number", "total_amount")},
        ocr_confidence=1.0,
        classifier_confidence=1.0,
        required_fields=["invoice_number", "total_amount"],
    )
    ds = scorer.score_document(fc, 1.0, 1.0, ["invoice_number", "total_amount"])
    assert 0.0 <= ds <= 1.0


def test_empty_fields_zero_document_score(scorer):
    assert scorer.score_document([], 0.9, 0.9, []) == 0.0
