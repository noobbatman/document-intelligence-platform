"""Line item extraction — parse invoice tables into structured rows.

Strategy (in priority order):
  1. pdfplumber table extraction (best for digital PDFs)
  2. OCR word-grid reconstruction (for scanned documents)
  3. Regex line-by-line parsing (fallback for any text)
"""

from __future__ import annotations

import re
from typing import Any

from app.ocr.base import OCRResult

# ── Amount pattern ────────────────────────────────────────────────────────────
_AMT = re.compile(r"[$€£GBP]?\s?(\d[\d,]*\.\d{2})")
_QTY = re.compile(r"\b(\d{1,4}(?:\.\d{1,3})?)\b")


def _parse_amount(s: str) -> float | None:
    m = _AMT.search(s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_qty(s: str) -> float | None:
    m = _QTY.search(s.strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ── Regex line parser ─────────────────────────────────────────────────────────

# Pattern: description ... qty ... unit_price ... line_total
# Handles lines like:
#   "Consulting Services   5   750.00   3750.00"
#   "Platform License (annual)  1  8000.00  8000.00"
_LINE_ITEM_RE = re.compile(
    r"^(.{3,50}?)\s{2,}"  # description (lazy up to 50 chars, then 2+ spaces)
    r"(\d{1,4}(?:\.\d{1,3})?)\s{2,}"  # qty
    r"([$€£]?\s?[\d,]+\.\d{2})\s{2,}"  # unit price
    r"([$€£]?\s?[\d,]+\.\d{2})",  # line total
    re.IGNORECASE,
)

# Simpler two-column: description ... total
_SIMPLE_LINE_RE = re.compile(
    r"^(.{3,60}?)\s{2,}([$€£]?\s?[\d,]+\.\d{2})\s*$",
    re.IGNORECASE,
)

# Header / footer markers to skip
_SKIP_LINES = re.compile(
    r"^\s*(?:description|item|qty|quantity|unit\s*price|amount|total|subtotal"
    r"|tax|vat|invoice|bill|from|to|date|due|page|\-{3,}|={3,}|_{3,})\s*$",
    re.IGNORECASE,
)


def extract_line_items_from_text(text: str) -> list[dict[str, Any]]:
    """Parse line items from raw OCR or PDF text."""
    items: list[dict[str, Any]] = []
    in_items_section = False

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect start of items section
        if re.search(r"\b(?:description|item|service|product)\b", line, re.IGNORECASE):
            if re.search(r"\b(?:qty|quantity|amount|price|total)\b", line, re.IGNORECASE):
                in_items_section = True
                continue

        # Detect end of items section
        if re.search(
            r"\b(?:subtotal|sub\s*total|total\s*due|amount\s*due|vat|tax\s*total)\b",
            line,
            re.IGNORECASE,
        ):
            if in_items_section:
                break

        if _SKIP_LINES.match(line):
            continue

        # Try full 4-column match first
        m = _LINE_ITEM_RE.match(line)
        if m:
            items.append(
                {
                    "description": m.group(1).strip(),
                    "quantity": _parse_qty(m.group(2)),
                    "unit_price": _parse_amount(m.group(3)),
                    "line_total": _parse_amount(m.group(4)),
                }
            )
            continue

        # Try simple description + total
        if in_items_section:
            m2 = _SIMPLE_LINE_RE.match(line)
            if m2:
                desc = m2.group(1).strip()
                total = _parse_amount(m2.group(2))
                # Skip if description looks like a label (subtotal, tax, etc.)
                if total and not re.search(r"\b(?:subtotal|vat|tax|total|discount)\b", desc, re.I):
                    items.append(
                        {
                            "description": desc,
                            "quantity": None,
                            "unit_price": None,
                            "line_total": total,
                        }
                    )

    return items[:50]  # cap at 50 items to avoid noise


def extract_line_items(
    ocr_result: OCRResult, stored_path: str | None = None
) -> list[dict[str, Any]]:
    """Main entry: try pdfplumber first, fall back to text parsing."""
    # 1. pdfplumber (best quality for native PDFs)
    if stored_path and not stored_path.startswith("s3://"):
        try:
            import pdfplumber

            items: list[dict[str, Any]] = []
            with pdfplumber.open(stored_path) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables() or []:
                        if not table:
                            continue
                        # Detect header row
                        header = [str(c or "").lower().strip() for c in (table[0] or [])]
                        desc_idx = next(
                            (
                                i
                                for i, h in enumerate(header)
                                if "desc" in h or "item" in h or "service" in h
                            ),
                            None,
                        )
                        qty_idx = next(
                            (i for i, h in enumerate(header) if "qty" in h or "quant" in h), None
                        )
                        up_idx = next(
                            (
                                i
                                for i, h in enumerate(header)
                                if "unit" in h or "rate" in h or "price" in h
                            ),
                            None,
                        )
                        tot_idx = next(
                            (
                                i
                                for i, h in enumerate(header)
                                if "total" in h or "amount" in h or "line" in h
                            ),
                            None,
                        )

                        if desc_idx is None and tot_idx is None:
                            continue

                        for row in table[1:]:
                            if not row:
                                continue
                            cells = [str(c or "").strip() for c in row]
                            desc = (
                                cells[desc_idx]
                                if desc_idx is not None and desc_idx < len(cells)
                                else ""
                            )
                            qty = (
                                _parse_qty(cells[qty_idx])
                                if qty_idx is not None and qty_idx < len(cells)
                                else None
                            )
                            up = (
                                _parse_amount(cells[up_idx])
                                if up_idx is not None and up_idx < len(cells)
                                else None
                            )
                            tot = (
                                _parse_amount(cells[tot_idx])
                                if tot_idx is not None and tot_idx < len(cells)
                                else None
                            )

                            if not desc or not (qty or up or tot):
                                continue
                            if re.search(r"subtotal|total|vat|tax|discount", desc, re.I):
                                break

                            items.append(
                                {
                                    "description": desc,
                                    "quantity": qty,
                                    "unit_price": up,
                                    "line_total": tot,
                                }
                            )

            if items:
                return items[:50]
        except Exception:
            pass

    # 2. Text-based fallback
    return extract_line_items_from_text(ocr_result.text)
