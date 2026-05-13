"""Bank statement field extractor — regex + noisy OCR variants."""

from __future__ import annotations

from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.extraction.table_extractor import TableExtractor
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, normalize_amount, regex_search


class BankStatementExtractor(Extractor):
    def __init__(self) -> None:
        self.table_extractor = TableExtractor()

    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text

        # ── Account number ────────────────────────────────────────────────────
        account_number = (
            regex_search(r"account(?:\s+number|:)?\s*[:#]?\s*([\d\-\*]{4,20})", text)
            or regex_search(r"acc0unt\s*[:#]?\s*([\dOl\-\*]{4,20})", text)
            or regex_search(r"\b([A-Z]{2}\d{2}[A-Z0-9]{4,})\b", text)  # IBAN
        )

        # ── Statement period ──────────────────────────────────────────────────
        statement_period = regex_search(
            r"(?:statement\s+period|period|stat\w*\s+p\w+)\s*[:#]?\s*([\dA-Za-z ,\-\/]+ (?:to|-) [\dA-Za-z ,\-\/]+)",
            text,
        ) or regex_search(
            r"(?:p[e3]ri[o0]d)\s*[:#]?\s*([\d\/\-A-Za-z, ]+(?:to|-|–)[\d\/\-A-Za-z, ]+)", text
        )

        # ── Balances — handle noisy OCR: 0→O, 1→l ──────────────────────────
        def find_balance(patterns):
            for pat in patterns:
                v = normalize_amount(regex_search(pat, text))
                if v is not None:
                    return v
            return None

        opening_balance = find_balance(
            [
                r"opening\s+bal(?:ance)?\s*[:#]?\s*([$€£GBP]?\s?[\d,]+\.\d{2})",
                r"0pening\s+bal\s*[:#]?\s*([\d,]+\.\d{2})",
                r"opening\s*[:#]?\s*([\d,]+\.\d{2})",
            ]
        )
        closing_balance = find_balance(
            [
                r"c(?:losing|1osing)\s+bal(?:ance)?\s*[:#]?\s*([$€£GBP]?\s?[\d,]+\.\d{2})",
                r"C1osing\s+Bal\s*[:#]?\s*([\d,]+\.\d{2})",
                r"closing\s*[:#]?\s*([\d,]+\.\d{2})",
            ]
        )
        available_balance = find_balance(
            [
                r"avai(?:l|1)able\s+bal(?:ance)?\s*[:#]?\s*([$€£GBP]?\s?[\d,]+\.\d{2})",
                r"avai1able\s*[:#]?\s*([\d,]+\.\d{2})",
            ]
        )

        # Derive available from closing if missing
        if available_balance is None and closing_balance is not None:
            available_balance = closing_balance

        fields: dict[str, Any] = {
            "account_number": account_number,
            "statement_period": statement_period,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "available_balance": available_balance,
        }

        tables = self.table_extractor.extract_from_ocr_words(ocr_result)
        entities = extract_entities(ocr_result)
        field_snippets = {
            name: find_snippet(text, str(value)) if value is not None else None
            for name, value in fields.items()
        }
        page_count = ocr_result.metadata.get("page_count", 1)
        page_map = {name: 1 for name in fields}
        if page_count > 1:
            for f in ("closing_balance", "available_balance"):
                page_map[f] = page_count

        return ExtractionOutput(
            document_type="bank_statement",
            fields=fields,
            entities=entities,
            tables=tables,
            metadata={
                "field_snippets": field_snippets,
                "required_fields": ["account_number", "closing_balance"],
                "field_page_map": page_map,
            },
        )
