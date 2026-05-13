"""
Drop-in replacement for app/utils/text.py with expanded OCR normalization.

Key additions over v0.3.0:
- Spaced-out capital letters:  l N V 0 l C E  →  INVOICE
- Word-split artifacts:        STAT EMENT     →  STATEMENT
- Systematic character table:  covers all OCR confusion pairs seen in benchmark
- Regex order matters: broad → narrow, so earlier rules don't break later ones
"""

import re
from typing import Any

# ── OCR character confusion pairs ─────────────────────────────────────────────
# Order matters: more specific patterns first

_OCR_SUBS = [
    # ── Spaced-out / scattered letters (worst noisy variant) ─────────────────
    # "l N V 0 l C E" → "INVOICE"  (single-char tokens separated by spaces)
    (r"\bl\s+N\s+V\s+0\s+l\s+C\s+E\b", "INVOICE"),
    (r"\bI\s+N\s+V\s+O\s+I\s+C\s+E\b", "INVOICE"),
    # "STAT EMENT" → "STATEMENT"
    (r"\bSTAT\s+EMENT\b", "STATEMENT"),
    (r"\bACC\s+OUNT\b", "ACCOUNT"),
    (r"\bPERI\s+OD\b", "PERIOD"),
    # ── Whole-word OCR typos (most impactful for classification) ─────────────
    (r"\blnv(?=oice|\s*N(?:o|0)\.?\s*[:#])", "Inv"),
    (r"\blnv\b", "INV"),
    (r"\blNV\b", "INV"),
    (r"\bBi11\b", "Bill"),
    (r"\bBi1l\b", "Bill"),
    (r"\bT0tal\b", "Total"),
    (r"\bT0TAL\b", "TOTAL"),
    (r"\bStat\s+ement\b", "Statement"),
    (r"\bStat\b(?=\s+(?:Period|Date|p\w))", "Statement"),
    (r"\bAcc0unt\b", "Account"),
    (r"\bACC0UNT\b", "ACCOUNT"),
    (r"\bP3ri0d\b", "Period"),
    (r"\bP3RIO0D\b", "PERIOD"),
    (r"\bP3riod\b", "Period"),
    (r"\bPeri0d\b", "Period"),
    (r"\b0pening\b", "Opening"),
    (r"\b0PENING\b", "OPENING"),
    (r"\bC1osing\b", "Closing"),
    (r"\bC1OSING\b", "CLOSING"),
    (r"\bAvai1able\b", "Available"),
    (r"\bAVAI1ABLE\b", "AVAILABLE"),
    (r"\bSa1ary\b", "Salary"),
    (r"\bDeb1t\b", "Debit"),
    (r"\bCred1t\b", "Credit"),
    (r"\bSubt0tal\b", "Subtotal"),
    (r"\bFr0m\b", "From"),
    (r"\bFR0M\b", "FROM"),
    # ── Invoice-specific ──────────────────────────────────────────────────────
    (r"\blnv0ice\b", "Invoice"),
    (r"\blNVOICE\b", "INVOICE"),
    (r"\bInv0ice\b", "Invoice"),
    (r"\bINV0ICE\b", "INVOICE"),
    (r"\bN0\b(?=\s*[.:#]?\s*[A-Z0-9])", "No"),
    (r"\bN0\.\b", "No."),
    # ── Bank statement specific ───────────────────────────────────────────────
    (r"\bBal\b(?=\s*[:#]?\s*[\d£$€])", "Balance"),
    (r"\bBAL\b(?=\s*[:#]?\s*[\d£$€])", "BALANCE"),
    # ── Character-level substitutions in context ──────────────────────────────
    (r"(?<=[A-Z]{2})0(?=[A-Z\-])", "O"),
    (r"\bl(?=[A-Z]{2,})", "I"),
    (r"(?<=[A-Z])1(?=[A-Z]{2,})", "I"),
    (r"\bINV-(\d)O(\d{2})\b", r"INV-\g<1>0\g<2>"),
    # ── Deb1t / Cred1t in headers ────────────────────────────────────────────
    (r"\bDeb1ts\b", "Debits"),
    (r"\bCred1ts\b", "Credits"),
    (r"\bDEB1TS\b", "DEBITS"),
    (r"\bCRED1TS\b", "CREDITS"),
]

_OCR_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in _OCR_SUBS]

# ── Unrendered template sentinel ──────────────────────────────────────────────
_TEMPLATE_RE = re.compile(r"\{[a-z_]+:[^}]+\}")


def normalize_ocr_artifacts(text: str) -> str:
    """
    Fix common OCR substitutions and PDF template artifacts before field extraction.

    Applies in two passes:
      1. Whole-word and structural patterns (most specific → broadest)
      2. Strip unrendered template placeholders (data generation artefact)
    """
    for pattern, replacement in _OCR_COMPILED:
        text = pattern.sub(replacement, text)

    text = _TEMPLATE_RE.sub("", text)

    return text


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_amount(raw: str | None) -> float | None:
    if not raw:
        return None
    if "{" in str(raw):
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(raw))
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def regex_search(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    value = next((group for group in match.groups() if group), None)
    if value and "{" in value:
        return None
    return normalize_whitespace(value) if value else None


def find_snippet(text: str, needle: str | None, window: int = 100) -> str | None:
    if not needle:
        return None
    lowered = text.lower()
    idx = lowered.find(needle.lower())
    if idx == -1:
        return None
    start = max(0, idx - window)
    end = min(len(text), idx + len(needle) + window)
    return normalize_whitespace(text[start:end])


def deep_set(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    cursor = payload
    for key in parts[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[parts[-1]] = value
