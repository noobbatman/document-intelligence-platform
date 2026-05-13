"""Legal notice extractor."""
from __future__ import annotations

import re
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, regex_search


class LegalNoticeExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text
        notice_type = _notice_type(text)
        fields: dict[str, Any] = {
            "notice_type": notice_type,
            "issuing_party": regex_search(r"(?:from|issuing party)\s*[:#]?\s*([A-Z][A-Za-z0-9 &,\.\-]+)", text),
            "receiving_party": regex_search(r"(?:to|receiving party)\s*[:#]?\s*([A-Z][A-Za-z0-9 &,\.\-]+)", text),
            "issue_date": regex_search(r"(?:date|issue date)\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)", text),
            "response_deadline": regex_search(r"(?:response deadline|respond by|deadline)\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)", text),
            "jurisdiction": regex_search(r"jurisdiction\s*[:#]?\s*([A-Za-z ,]+)", text),
            "referenced_documents": _referenced_documents(text),
            "required_actions": _required_actions(text),
        }
        snippets = {
            name: find_snippet(text, str(value)) if value not in (None, [], {}) else None
            for name, value in fields.items()
        }
        return ExtractionOutput(
            document_type="legal_notice",
            fields=fields,
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": ["notice_type", "issuing_party", "receiving_party", "response_deadline"],
            },
        )


def _notice_type(text: str) -> str | None:
    lowered = text.lower()
    checks = {
        "cease_and_desist": "cease and desist",
        "demand": "demand",
        "summons": "summons",
        "subpoena": "subpoena",
        "termination": "termination",
    }
    return next((label for label, needle in checks.items() if needle in lowered), None)


def _referenced_documents(text: str) -> list[str]:
    pattern = re.compile(r"\b(?:case|contract|agreement|exhibit|schedule|document)\s+(?:no\.?|number|[A-Z])?\s*[:#]?\s*([A-Z0-9\-\/]{3,})", re.IGNORECASE)
    return sorted({match.strip() for match in pattern.findall(text)})


def _required_actions(text: str) -> list[str]:
    actions: list[str] = []
    for match in re.finditer(r"\b(?:must|shall|required to|demand that you)\s+([^.;\n]{8,180})", text, re.IGNORECASE):
        actions.append(match.group(1).strip())
    return actions[:10]
