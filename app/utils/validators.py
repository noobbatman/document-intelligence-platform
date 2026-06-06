"""Per-document-type field validators and normalizers.

Each validator returns a (normalized_value, is_valid, reason) tuple.
Normalizers convert raw extracted strings into canonical typed values.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ── Date normalisation ────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%B %d %Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d/%m/%y",
    "%m/%d/%y",
]


def parse_date(raw: str | None) -> str | None:
    """Normalise a raw date string to ISO-8601 (YYYY-MM-DD). Returns None on failure."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def validate_date(raw: Any) -> tuple[Any, bool, str]:
    s = str(raw).strip() if raw else ""
    norm = parse_date(s)
    if norm:
        return norm, True, "ok"
    return raw, False, f"unrecognised date format: {s!r}"


# ── Currency normalisation ─────────────────────────────────────────────────────

_CURRENCY_RE = re.compile(r"[$€£¥]?\s*([0-9,]+(?:\.[0-9]{1,2})?)")


def parse_amount(raw: str | None) -> float | None:
    """Normalise a currency string to float. Returns None on failure."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    m = _CURRENCY_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def validate_amount(raw: Any, *, field_name: str = "amount") -> tuple[Any, bool, str]:
    if raw is None:
        return None, False, f"{field_name} is missing"
    norm = parse_amount(raw)
    if norm is None:
        return raw, False, f"{field_name}: cannot parse {raw!r} as amount"
    if norm < 0:
        return norm, False, f"{field_name}: negative amount {norm}"
    if norm > 1_000_000_000:
        return norm, False, f"{field_name}: suspiciously large {norm:,.2f}"
    return norm, True, "ok"


# ── Invoice-specific validators ────────────────────────────────────────────────

_INVOICE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-\/\._ ]{1,39}$", re.IGNORECASE)


def validate_invoice_number(raw: Any) -> tuple[Any, bool, str]:
    if not raw:
        return None, False, "invoice_number is missing"
    s = str(raw).strip().upper()
    if not _INVOICE_ID_RE.match(s):
        return raw, False, f"invoice_number {raw!r} does not match expected pattern"
    return s, True, "ok"


def validate_invoice_total_consistency(
    subtotal: float | None, tax: float | None, total: float | None, tolerance: float = 0.05
) -> tuple[bool, str]:
    """Checks subtotal + tax ≈ total within relative tolerance."""
    if subtotal is None or tax is None or total is None:
        return True, "skip (incomplete)"
    if total == 0:
        return False, "total is zero"
    computed = subtotal + tax
    rel_err = abs(computed - total) / total
    if rel_err > tolerance:
        return (
            False,
            f"subtotal({subtotal}) + tax({tax}) = {computed:.2f} but total = {total:.2f} (error {rel_err:.1%})",
        )
    return True, "ok"


# ── Bank statement validators ──────────────────────────────────────────────────

_ACCOUNT_RE = re.compile(r"^[\d\-\*X ]{4,30}$")
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$")
_SORT_CODE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")


def validate_account_number(raw: Any) -> tuple[Any, bool, str]:
    if not raw:
        return None, False, "account_number is missing"
    s = str(raw).strip()
    if _IBAN_RE.match(s.replace(" ", "")):
        return s, True, "ok (IBAN)"
    if _ACCOUNT_RE.match(s):
        return s, True, "ok"
    return raw, False, f"account_number {raw!r} does not match expected format"


def validate_statement_period(raw: Any) -> tuple[Any, bool, str]:
    if not raw:
        return None, False, "statement_period is missing"
    s = str(raw).strip()
    has_sep = bool(re.search(r"\s+(?:to|-)\s+", s, re.IGNORECASE) or "/" in s)
    has_year = bool(re.search(r"\b(20\d{2})\b", s))
    if has_sep and has_year:
        return s, True, "ok"
    return raw, False, f"statement_period {raw!r}: could not identify two dates"


def validate_balance_consistency(
    opening: float | None,
    closing: float | None,
    available: float | None,
    net_transactions: float | None = None,
    tolerance: float = 0.02,
) -> tuple[bool, str]:
    """Checks balance coherence.

    - opening + net_transactions ≈ closing (when net_transactions known)
    - closing ≈ available_balance (within tolerance)
    """
    msgs = []
    ok = True
    if closing is not None and available is not None and closing != 0:
        err = abs(closing - available) / abs(closing)
        if err > tolerance:
            ok = False
            msgs.append(f"closing({closing}) vs available({available}): {err:.1%} discrepancy")

    if net_transactions is not None and opening is not None and closing is not None:
        computed = opening + net_transactions
        if closing != 0:
            err = abs(computed - closing) / abs(closing)
            if err > tolerance:
                ok = False
                msgs.append(
                    f"opening({opening}) + net({net_transactions:.2f}) = {computed:.2f} vs closing({closing}): {err:.1%}"
                )

    return ok, "; ".join(msgs) if msgs else "ok"


# ── Per-document-type validation runner ───────────────────────────────────────


def validate_invoice_fields(fields: dict[str, Any]) -> list[dict]:
    """Run all invoice validators. Returns list of {field, valid, reason, normalized_value}."""
    results = []

    def check(field, fn, *args, **kwargs):
        raw = fields.get(field)
        norm, valid, reason = fn(raw, *args, **kwargs) if not args else fn(raw)
        results.append(
            {
                "field": field,
                "raw_value": raw,
                "normalized_value": norm,
                "valid": valid,
                "reason": reason,
            }
        )

    check("invoice_number", validate_invoice_number)
    check("invoice_date", validate_date)
    check("due_date", validate_date)

    for f in ("subtotal", "tax", "total_amount"):
        raw = fields.get(f)
        norm, valid, reason = validate_amount(raw, field_name=f)
        results.append(
            {
                "field": f,
                "raw_value": raw,
                "normalized_value": norm,
                "valid": valid,
                "reason": reason,
            }
        )

    # Cross-field total consistency
    sub = parse_amount(fields.get("subtotal"))
    tax = parse_amount(fields.get("tax"))
    tot = parse_amount(fields.get("total_amount"))
    ok, reason = validate_invoice_total_consistency(sub, tax, tot)
    results.append(
        {
            "field": "_cross_total",
            "raw_value": None,
            "normalized_value": None,
            "valid": ok,
            "reason": reason,
        }
    )

    return results


def validate_bank_statement_fields(fields: dict[str, Any]) -> list[dict]:
    results = []

    def check(field, fn):
        raw = fields.get(field)
        norm, valid, reason = fn(raw)
        results.append(
            {
                "field": field,
                "raw_value": raw,
                "normalized_value": norm,
                "valid": valid,
                "reason": reason,
            }
        )

    check("account_number", validate_account_number)
    check("statement_period", validate_statement_period)

    for f in ("opening_balance", "closing_balance", "available_balance"):
        raw = fields.get(f)
        norm, valid, reason = validate_amount(raw, field_name=f)
        results.append(
            {
                "field": f,
                "raw_value": raw,
                "normalized_value": norm,
                "valid": valid,
                "reason": reason,
            }
        )

    # Balance coherence
    op = parse_amount(fields.get("opening_balance"))
    cl = parse_amount(fields.get("closing_balance"))
    av = parse_amount(fields.get("available_balance"))
    ok, reason = validate_balance_consistency(op, cl, av)
    results.append(
        {
            "field": "_cross_balance",
            "raw_value": None,
            "normalized_value": None,
            "valid": ok,
            "reason": reason,
        }
    )

    return results


def run_validators(document_type: str, fields: dict[str, Any]) -> list[dict]:
    if document_type == "invoice":
        return validate_invoice_fields(fields)
    if document_type == "bank_statement":
        return validate_bank_statement_fields(fields)
    return []
