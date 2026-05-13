"""Contract / agreement extractor."""

import re
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, regex_search


class ContractExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text

        effective_date = regex_search(
            r"effective\s+(?:as\s+of\s+)?date\s*[:#]?\s*([A-Za-z0-9,\-\/ ]+)", text
        )
        # Stop before " and <Capital>" to avoid capturing Party B text.
        party_a = regex_search(
            r"(?:between|party\s+a|first\s+party)\s*[:#]?\s*([A-Za-z0-9 &,\.\-]+?)(?=\s+and\s+[A-Z]|\s*,\s*a\s+[A-Za-z]|$)",
            text,
        )
        # Standard executive agreement preamble: (the "Company"), and Kristin Scott (the "Executive").
        party_b = (
            regex_search(
                r'["”]\s*\)\s*,?\s+and\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\(',
                text,
            )
            or regex_search(
                r"(?:corporation|company|llc|inc|ltd)[^,\n]{0,30},?\s+and\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*[,\(]",
                text,
            )
            or regex_search(r"(?:party\s+b|second\s+party)\s*[:#]?\s*([A-Za-z0-9 &,\.\-]+)", text)
        )
        # "governed by … laws of the State of Ohio" — span up to 120 chars after "governed by".
        governing_law = regex_search(
            r"governed\s+by[^.]{0,120}?(?:laws?\s+of\s+(?:the\s+)?(?:state\s+of\s+)?|state\s+of\s+)([A-Z][A-Za-z ]+?)(?=[\.,;\n])",
            text,
        ) or regex_search(
            r"state\s+of\s+([A-Z][A-Za-z]+)(?=[^\w])",
            text,
        )
        # Only match when a date value (containing digits) follows the heading.
        termination_date = regex_search(
            r"[Tt]ermination\s+[Dd]ate\s*[:#]?\s*((?:[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}))",
            text,
        )
        contract_value = regex_search(
            r"(?:contract\s+value|total\s+value|consideration)\s*[:#]?\s*([$€£]?\s?[\d,]+(?:\.\d{2})?)",
            text,
        )
        consideration = contract_value or regex_search(
            r"(good\s+and\s+valuable\s+consideration|consideration\s+of\s+[$€£]?\s?[\d,]+(?:\.\d{2})?)",
            text,
        )
        payment_terms = regex_search(
            r"(payment\s+terms?\s*[:#]?\s*(?:.{0,240}?))(?:\n\s*\n|termination|confidentiality|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        notice_period = regex_search(
            r"(\d{1,3})\s+days?\s+(?:prior\s+)?(?:written\s+)?notice", text
        )
        confidentiality_snippet = find_snippet(text, "confidentiality") or find_snippet(
            text, "confidential"
        )
        ip_ownership = _clause_snippet(
            text, ["intellectual property", "ip ownership", "work product"]
        )
        indemnification = _clause_snippet(text, ["indemnification", "indemnify", "hold harmless"])
        dispute_resolution = _clause_snippet(
            text, ["arbitration", "mediation", "dispute resolution", "litigation"]
        )
        signatures = _extract_signatures(text)

        fields: dict[str, Any] = {
            "effective_date": effective_date,
            "party_a": party_a,
            "party_b": party_b,
            "parties": _extract_parties(text, party_a, party_b),
            "governing_law": governing_law,
            "termination_date": termination_date,
            "contract_value": contract_value,
            "consideration": consideration,
            "payment_terms": payment_terms,
            "notice_period": notice_period,
            "confidentiality_clause": {
                "present": confidentiality_snippet is not None,
                "snippet": confidentiality_snippet,
            },
            "ip_ownership": ip_ownership,
            "indemnification": {
                "present": indemnification is not None,
                "snippet": indemnification,
            },
            "dispute_resolution": dispute_resolution,
            "signatures": signatures,
        }
        snippets = {
            name: find_snippet(text, str(value)) if value is not None else None
            for name, value in fields.items()
        }
        entities = extract_entities(ocr_result)
        return ExtractionOutput(
            document_type="contract",
            fields=fields,
            entities=entities,
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": ["effective_date", "party_a", "party_b", "governing_law"],
            },
        )


def _extract_parties(text: str, party_a: str | None, party_b: str | None) -> list[dict[str, str]]:
    parties: list[dict[str, str]] = []
    if party_a:
        parties.append({"name": party_a, "role": "party_a"})
    if party_b:
        parties.append({"name": party_b, "role": "party_b"})

    role_pattern = re.compile(
        r"\b(grantor|grantee|plaintiff|defendant|licensor|licensee)\b\s*[:\-]?\s*([A-Z][A-Za-z0-9 &,\.\-]+)",
        re.IGNORECASE,
    )
    for role, name in role_pattern.findall(text):
        candidate = {"name": name.strip(" .,"), "role": role.lower()}
        if candidate["name"] and candidate not in parties:
            parties.append(candidate)
    return parties


def _clause_snippet(text: str, needles: list[str]) -> str | None:
    for needle in needles:
        snippet = find_snippet(text, needle, window=220)
        if snippet:
            return snippet
    return None


def _extract_signatures(text: str) -> list[dict[str, str | None]]:
    signatures: list[dict[str, str | None]] = []
    pattern = re.compile(
        r"(?:signature|signed\s+by|by)\s*[:_\- ]+\s*([A-Z][A-Za-z .,'-]{2,80})(?:\s+date\s*[:_\- ]+\s*([A-Za-z0-9,/\- ]{4,40}))?",
        re.IGNORECASE,
    )
    for name, date in pattern.findall(text):
        signatures.append({"name": name.strip(" .,"), "date": date.strip(" .,") or None})
    return signatures
