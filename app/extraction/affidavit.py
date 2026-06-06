"""Affidavit / declaration extractor."""

from __future__ import annotations

from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, regex_search


class AffidavitExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text
        fields: dict[str, Any] = {
            "declarant_name": regex_search(
                r"(?:affidavit|declaration)\s+of\s+([A-Z][A-Za-z .,'-]+)", text
            )
            or regex_search(
                r"(?:affiant|declarant|deponent)\s*[:#]?\s*([A-Z][A-Za-z .,'-]+)", text
            ),
            "declarant_role": regex_search(
                r"(?:capacity|role|title)\s*[:#]?\s*([A-Za-z ,.'-]+)", text
            ),
            "notary_name": regex_search(
                r"notary\s+(?:public\s+)?(?:name)?\s*[:#]?\s*([A-Z][A-Za-z .,'-]+)", text
            ),
            "notary_jurisdiction": regex_search(
                r"(?:state|county|jurisdiction)\s+of\s+([A-Z][A-Za-z ,.'-]+)", text
            ),
            "execution_date": regex_search(
                r"(?:executed|sworn|signed|dated)\s+(?:on\s+)?([A-Za-z0-9,\-/ ]+)", text
            ),
            "statement_summary": find_snippet(text, "declare", window=350)
            or find_snippet(text, "sworn", window=350),
        }
        snippets = {
            name: find_snippet(text, str(value)) if value is not None else None
            for name, value in fields.items()
        }
        return ExtractionOutput(
            document_type="affidavit",
            fields=fields,
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": ["declarant_name", "execution_date", "statement_summary"],
            },
        )
