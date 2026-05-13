"""Confidence scoring with OCR signal, field validation, and cross-field consistency."""
from __future__ import annotations

import re
from typing import Any

from app.schemas.common import FieldConfidence

W_BASE = 0.40
W_OCR = 0.15
W_CLF = 0.10
W_FMT = 0.20
W_CROSS = 0.15

W_DOC_FIELD = 0.45
W_DOC_CLF = 0.20
W_DOC_OCR = 0.10
W_DOC_COV = 0.25

_CROSS_NEUTRAL = 0.5
_CONF_CAP = 0.99

_DATE_PATTERNS = [
    r"\d{1,2}/\d{1,2}/\d{2,4}",
    r"\d{1,2}-\d{1,2}-\d{2,4}",
    r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}",
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}",
]
_DATE_RE = re.compile("|".join(_DATE_PATTERNS), re.IGNORECASE)
_INVOICE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-\/\._ ]{2,40}$", re.IGNORECASE)
_ACCOUNT_RE = re.compile(r"^[\d\-\*X ]{4,30}$")
_CASE_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9:\-\/\.]{3,60}$", re.IGNORECASE)


def _validate_date(value: Any) -> float:
    if not value:
        return 0.0
    return 1.0 if _DATE_RE.search(str(value)) else 0.3


def _validate_amount(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return 1.0 if float(value) > 0 else 0.2
    except (TypeError, ValueError):
        return 0.2


def _validate_invoice_id(value: Any) -> float:
    if not value:
        return 0.0
    return 1.0 if _INVOICE_ID_RE.match(str(value).strip()) else 0.4


def _validate_case_number(value: Any) -> float:
    if not value:
        return 0.0
    return 1.0 if _CASE_NUMBER_RE.match(str(value).strip()) else 0.4


def _validate_account(value: Any) -> float:
    if not value:
        return 0.0
    s = str(value).strip()
    return 1.0 if (_ACCOUNT_RE.match(s) and len(s) >= 4) else 0.4


def _validate_period(value: Any) -> float:
    if not value:
        return 0.0
    s = str(value)
    has_separator = bool(re.search(r"\s+(?:to|-)\s+", s, re.IGNORECASE))
    has_dates = len(_DATE_RE.findall(s)) >= 1
    return 1.0 if (has_separator and has_dates) else (0.6 if has_dates else 0.3)


def _validate_nonempty_text(value: Any) -> float:
    return 0.8 if value and len(str(value)) > 2 else 0.0


def _validate_nonempty_list(value: Any) -> float:
    return 0.85 if isinstance(value, list) and len(value) > 0 else 0.0


_FIELD_VALIDATORS: dict[str, Any] = {
    "invoice_number": _validate_invoice_id,
    "invoice_date": _validate_date,
    "due_date": _validate_date,
    "vendor_name": _validate_nonempty_text,
    "customer_name": _validate_nonempty_text,
    "subtotal": _validate_amount,
    "tax": _validate_amount,
    "total_amount": _validate_amount,
    "account_number": _validate_account,
    "statement_period": _validate_period,
    "opening_balance": _validate_amount,
    "closing_balance": _validate_amount,
    "available_balance": _validate_amount,
    "receipt_date": _validate_date,
    "receipt_number": _validate_invoice_id,
    "payment_method": lambda v: 0.8 if v and len(str(v)) > 1 else 0.0,
    "effective_date": _validate_date,
    "termination_date": _validate_date,
    "party_a": _validate_nonempty_text,
    "party_b": _validate_nonempty_text,
    "governing_law": _validate_nonempty_text,
    "case_caption": _validate_nonempty_text,
    "case_number": _validate_case_number,
    "court": _validate_nonempty_text,
    "plaintiffs": _validate_nonempty_list,
    "defendants": _validate_nonempty_list,
    "claims": _validate_nonempty_list,
    "causes_of_action": _validate_nonempty_list,
    "jurisdiction": _validate_nonempty_text,
    "venue": _validate_nonempty_text,
    "statutes": _validate_nonempty_list,
    "relief_sought": _validate_nonempty_list,
    "filing_date": _validate_date,
    "jury_demand": lambda v: 0.8 if isinstance(v, bool) else 0.0,
}

def _DEFAULT_VALIDATOR(value: Any) -> float:
    return 0.7 if value not in (None, "", []) else 0.0


def _cross_field_consistency(fields: dict[str, Any]) -> dict[str, float]:
    bonuses: dict[str, float] = {}

    sub = fields.get("subtotal")
    tax = fields.get("tax")
    tot = fields.get("total_amount")
    if sub is not None and tax is not None and tot is not None:
        try:
            sub_f, tax_f, tot_f = float(sub), float(tax), float(tot)
            if tot_f > 0:
                computed = sub_f + tax_f
                rel_err = abs(computed - tot_f) / tot_f
                score = max(0.0, 1.0 - rel_err * 10)
                bonuses["subtotal"] = score
                bonuses["tax"] = score
                bonuses["total_amount"] = score
        except (TypeError, ValueError):
            pass

    opening = fields.get("opening_balance")
    closing = fields.get("closing_balance")
    available = fields.get("available_balance")
    if opening is not None and closing is not None:
        try:
            float(opening), float(closing)
            bonuses["opening_balance"] = 1.0
            bonuses["closing_balance"] = 1.0
        except (TypeError, ValueError):
            pass
    if available is not None and closing is not None:
        try:
            av_f, cl_f = float(available), float(closing)
            diff_pct = abs(av_f - cl_f) / max(cl_f, 1.0)
            score = max(0.0, 1.0 - diff_pct * 5)
            bonuses["available_balance"] = score
            bonuses["closing_balance"] = max(bonuses.get("closing_balance", 0.0), score)
        except (TypeError, ValueError):
            pass

    return bonuses


class ConfidenceScorer:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def score_fields(
        self,
        fields: dict[str, Any],
        snippets: dict[str, str | None],
        ocr_confidence: float,
        classifier_confidence: float,
        required_fields: list[str],
    ) -> list[FieldConfidence]:
        cross = _cross_field_consistency(fields)
        scored: list[FieldConfidence] = []

        for name, value in fields.items():
            is_empty = value in (None, "", [])

            base = 0.0 if is_empty else W_BASE
            ocr_sig = W_OCR * ocr_confidence
            clf_sig = W_CLF * classifier_confidence
            validator = _FIELD_VALIDATORS.get(name, _DEFAULT_VALIDATOR)
            fmt_score = 0.0 if is_empty else validator(value)
            fmt_sig = W_FMT * fmt_score
            cross_sig = W_CROSS * cross.get(name, _CROSS_NEUTRAL)

            confidence = min(_CONF_CAP, max(0.0, base + ocr_sig + clf_sig + fmt_sig + cross_sig))

            if name in required_fields and is_empty:
                confidence = min(confidence, 0.30)
            elif not is_empty and fmt_score < 0.5:
                confidence = min(confidence, 0.49)

            scored.append(FieldConfidence(
                name=name,
                value=value,
                confidence=round(confidence, 4),
                source_snippet=snippets.get(name),
                requires_review=confidence < self.threshold,
            ))

        return scored

    def score_document(
        self,
        field_confidences: list[FieldConfidence],
        classifier_confidence: float,
        ocr_confidence: float,
        required_fields: list[str],
    ) -> float:
        if not field_confidences:
            return 0.0

        mean_field = sum(f.confidence for f in field_confidences) / len(field_confidences)
        required_present = sum(
            1 for f in field_confidences
            if f.name in required_fields and f.value not in (None, "", [])
        )
        coverage = required_present / max(len(required_fields), 1)

        overall = (
            W_DOC_FIELD * mean_field
            + W_DOC_CLF * classifier_confidence
            + W_DOC_OCR * ocr_confidence
            + W_DOC_COV * coverage
        )
        return round(min(_CONF_CAP, overall), 4)
