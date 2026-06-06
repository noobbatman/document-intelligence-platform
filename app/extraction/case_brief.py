"""Case brief / court decision extractor."""

from __future__ import annotations

import re
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, regex_search


class CaseBriefExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text
        fields: dict[str, Any] = {
            "case_name": regex_search(
                r"(?:case name|caption)\s*[:#]?\s*([A-Z][A-Za-z0-9 &,\.\-]+ v\.? [A-Z][A-Za-z0-9 &,\.\-]+)",
                text,
            )
            or regex_search(r"\b([A-Z][A-Za-z0-9 &,\.\-]+ v\.? [A-Z][A-Za-z0-9 &,\.\-]+)\b", text),
            "case_number": regex_search(
                r"(?:case|docket)\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Z0-9\-\/:]+)", text
            ),
            "court": regex_search(r"(?:court)\s*[:#]?\s*([A-Z][A-Za-z ,\.\-]+)", text),
            "jurisdiction": regex_search(r"jurisdiction\s*[:#]?\s*([A-Za-z ,]+)", text),
            "filing_date": regex_search(r"filing\s+date\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)", text),
            "decision_date": regex_search(r"decision\s+date\s*[:#]?\s*([A-Za-z0-9,\-/ ]+)", text),
            "plaintiff": regex_search(r"plaintiff\s*[:#]?\s*([A-Z][A-Za-z0-9 &,\.\-]+)", text),
            "defendant": regex_search(r"defendant\s*[:#]?\s*([A-Z][A-Za-z0-9 &,\.\-]+)", text),
            "legal_issues": _list_after_heading(
                text, ["legal issues", "issues presented", "questions presented"]
            ),
            "holding": regex_search(r"holding\s*[:#]?\s*([^\\n]{10,500})", text),
            "cited_statutes": _cited_statutes(text),
        }
        snippets = {
            name: find_snippet(text, str(value)) if value not in (None, [], {}) else None
            for name, value in fields.items()
        }
        return ExtractionOutput(
            document_type="case_brief",
            fields=fields,
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": ["case_name", "court", "legal_issues", "holding"],
            },
        )


def _list_after_heading(text: str, headings: list[str]) -> list[str]:
    for heading in headings:
        pattern = re.compile(
            rf"{re.escape(heading)}\s*[:#]?\s*(.*?)(?:\n\s*\n|holding|facts|analysis|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            raw = match.group(1)
            return [
                item.strip(" -;\n\t")
                for item in re.split(r"(?:\n|;|\?)", raw)
                if len(item.strip()) > 8
            ][:10]
    return []


def _cited_statutes(text: str) -> list[str]:
    patterns = [
        r"\b\d+\s+U\.S\.C\.?\s+§?\s*[\w\-\.]+",
        r"\b[A-Z][A-Za-z ]+\s+Code\s+§?\s*[\w\-\.]+",
        r"\bsection\s+\d+[A-Za-z0-9\.\-]*",
    ]
    found: set[str] = set()
    for pattern in patterns:
        found.update(match.strip() for match in re.findall(pattern, text, re.IGNORECASE))
    return sorted(found)
