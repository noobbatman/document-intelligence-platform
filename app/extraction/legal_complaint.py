"""Federal/state civil complaint extractor."""

from __future__ import annotations

import re
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, regex_search


class LegalComplaintExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text
        fields: dict[str, Any] = {
            "case_caption": _case_caption(text),
            "case_number": _case_number(text),
            "court": _court(text),
            "plaintiffs": _caption_parties(text, "plaintiff"),
            "defendants": _caption_parties(text, "defendant"),
            "filing_date": regex_search(
                r"(?:filed|dated)\s*[:#]?\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
                text,
            ),
            "claims": _claims(text),
            "causes_of_action": _claims(text),
            "jurisdiction": _jurisdiction(text),
            "venue": _venue(text),
            "statutes": _statutes(text),
            "relief_sought": _relief_sought(text),
            "jury_demand": _jury_demand(text),
        }
        snippets = {
            name: find_snippet(text, str(value)) if value not in (None, [], {}) else None
            for name, value in fields.items()
        }
        return ExtractionOutput(
            document_type="legal_complaint",
            fields=fields,
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": [
                    "case_caption",
                    "case_number",
                    "court",
                    "plaintiffs",
                    "defendants",
                    "claims",
                ],
            },
        )


def _case_caption(text: str) -> str | None:
    return regex_search(
        r"([A-Z][A-Za-z0-9 &,.'\-]+,\s*Plaintiffs?.{0,120}?[A-Z][A-Za-z0-9 &,.'\-]+,\s*Defendants?)",
        text,
    ) or regex_search(r"\b([A-Z][A-Za-z0-9 &,.'\-]+ v\.? [A-Z][A-Za-z0-9 &,.'\-]+)\b", text)


def _caption_parties(text: str, role: str) -> list[str]:
    caption = re.search(
        r"(?P<plaintiff>[A-Z][A-Z0-9 .,'&-]{3,120}),?\s+Plaintiff,?\s+(?:v\.?|vs\.?|ve)\s+(?P<defendant>.+?),?\s+Defendants?\b",
        text[:2500],
        re.IGNORECASE | re.DOTALL,
    )
    if caption:
        value = caption.group("plaintiff" if role == "plaintiff" else "defendant")
        value = re.sub(r"\s+", " ", value).strip(" ,;.")
        value = re.sub(r"^.*\bDISTRICT\s+OF\s+[A-Z]+\s+", "", value, flags=re.IGNORECASE)
        if role == "defendant":
            return _dedupe(
                part.strip(" ,;.")
                for part in re.split(
                    r",\s+and\s+(?=[A-Z])|,\s+or,\s+in\s+the\s+alternative,\s+", value
                )
                if len(part.strip()) > 3
            )[:12]
        return [value]

    pattern = re.compile(
        rf"([A-Z][A-Za-z0-9 &,.'\-]{{2,160}}),?\s+(?:{role}|{role}s)\b",
        re.IGNORECASE,
    )
    parties = []
    for match in pattern.finditer(text[:6000]):
        value = re.sub(r"\s+", " ", match.group(1)).strip(" ,;")
        if value and value.lower() not in {"and"}:
            parties.append(value)
    return _dedupe(parties)[:12]


def _case_number(text: str) -> str | None:
    return (
        regex_search(r"\bCase\s+(\d+:\d{2}-cv-\d{3,6}-[A-Z]+)\b", text)
        or regex_search(r"\b(\d{2}-[A-Z]-\d{3,6})\s+Case\s+No\.?\b", text)
        or regex_search(
            r"(?:civil\s+action|case|docket)\s*(?:no\.?|number|#)\s*[:#]?\s*((?:\d+:\d{2}-cv-\d{3,6}-[A-Z]+)|(?:\d{2}-[A-Z]-\d{3,6})|[A-Z0-9:\-\/\.]+)",
            text,
        )
    )


def _court(text: str) -> str | None:
    federal = re.search(
        r"\b(UNITED\s+STATES\s+DISTRICT\s+court\S*)\s*:?\s*([A-Z ]+)(?=\s+[A-Z][A-Z .,'-]+,\s+Plaintiff)",
        text,
        re.IGNORECASE,
    )
    if federal:
        district = re.sub(r"\s+", " ", federal.group(2)).strip(" ,:")
        district_match = re.search(
            r"\b((?:EASTERN|WESTERN|NORTHERN|SOUTHERN|MIDDLE|CENTRAL)\s+DISTRICT\s+OF\s+[A-Z]+)\b",
            district,
            re.IGNORECASE,
        )
        if district_match:
            district = district_match.group(1)
        return f"UNITED STATES DISTRICT COURT, {district.upper()}"
    return regex_search(
        r"((?:UNITED\s+STATES\s+)?DISTRICT\s+COURT[^\\n]{0,120}|[A-Z][A-Za-z ,]+ Court)", text
    )


def _claims(text: str) -> list[str]:
    patterns = [
        r"(?:^|\n)\s*COUNT\s+((?:I|II|III|IV|V|VI|VII|VIII|IX|X|\d+)[^\n]{0,180})",
        r"(?:^|\n)\s*(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH)?\s*CAUSE\s+OF\s+ACTION\s*[:#-]?\s*([^\n]{8,180})",
    ]
    claims: list[str] = []
    for pattern in patterns:
        claims.extend(
            re.sub(r"\s+", " ", match).strip(" :-")
            for match in re.findall(pattern, text, re.IGNORECASE)
        )
    title = regex_search(
        r"COMPLAINT\s+FOR\s+(.+?)(?:DEMAND\s+FOR\s+JURY\s+TRIAL|JURY\s+TRIAL\s+DEMANDED|PRELIMINARY\s+STATEMENT)",
        text,
    )
    if title:
        claims.extend(
            re.sub(r"^(?:and|or)\s+", "", item.strip(" ,.;"), flags=re.IGNORECASE)
            for item in re.split(r",\s*|\s+AND\s+", title, flags=re.IGNORECASE)
            if len(item.strip()) > 8
        )
    return _dedupe([claim for claim in claims if len(claim) > 5])[:20]


def _statutes(text: str) -> list[str]:
    patterns = [
        r"\b\d+\s+U\.S\.C\.?\s+§+\s*[\w\-.()]+",
        r"\b\d+\s+C\.F\.R\.?\s+§+\s*[\w\-.()]+",
        r"\b[A-Z][A-Za-z ]{2,80}\s+Act\b",
        r"\bFederal\s+Rule\s+of\s+Civil\s+Procedure\s+\d+",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(_clean_statute(match) for match in re.findall(pattern, text, re.IGNORECASE))
    return _dedupe(match for match in found if match)[:25]


def _clean_statute(value: str) -> str:
    cleaned = re.split(r"\n\s*\n|\n\s*[A-Z][A-Z ,]{4,80}\n", value, maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:")
    return cleaned if len(cleaned) <= 120 else ""


def _jurisdiction(text: str) -> str | None:
    section = _section_snippet(text, ["jurisdiction", "jurisdiction and venue"])
    federal_question = re.search(r"\b28\s+U\.S\.C\.?\s+§+\s*1331\b", text, re.IGNORECASE)
    civil_rights = re.search(r"\b28\s+U\.S\.C\.?\s+§+\s*1343\b", text, re.IGNORECASE)
    parts: list[str] = []
    if federal_question:
        parts.append("Federal question jurisdiction under 28 U.S.C. § 1331")
    if civil_rights:
        parts.append("Civil rights jurisdiction under 28 U.S.C. § 1343")
    if parts:
        return "; ".join(parts)
    return section


def _venue(text: str) -> str | None:
    section = _section_snippet(text, ["venue", "jurisdiction and venue"])
    venue_statute = re.search(r"\b28\s+U\.S\.C\.?\s+§+\s*1391(?:\([a-z]\))?", text, re.IGNORECASE)
    if venue_statute:
        return f"Venue alleged under {venue_statute.group(0)}"
    return section


def _relief_sought(text: str) -> list[str]:
    relief = _section_items(
        text,
        ["prayer for relief", "relief requested", "request for relief", "wherefore"],
        limit=12,
    )
    if relief:
        return relief
    match = re.search(
        r"(?is)\bWHEREFORE\b,?\s*(?:Plaintiffs?|Petitioners?)?\s*(?:respectfully\s+)?(?:requests?|prays?)\s+(.*?)(?=\n\s*(?:JURY\s+DEMAND|DEMAND\s+FOR\s+JURY|DATED|Respectfully submitted|$))",
        text,
    )
    if not match:
        return []
    block = re.sub(r"\s+", " ", match.group(1)).strip()
    pieces = re.split(r";\s+|\(\w\)\s+|\n\s*\d+\.\s+", block)
    return [piece.strip(" ;.") for piece in pieces if len(piece.strip()) > 20][:12]


def _jury_demand(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:jury\s+trial\s+demand(?:ed)?|demand\s+for\s+jury\s+trial|demands?\s+a\s+jury\s+trial|jury\s+demand)\b",
            text,
            re.IGNORECASE,
        )
    )


def _section_snippet(text: str, headings: list[str]) -> str | None:
    items = _section_items(text, headings, limit=1)
    return items[0] if items else None


def _section_items(text: str, headings: list[str], *, limit: int = 8) -> list[str]:
    heading_alt = "|".join(re.escape(heading) for heading in headings)
    pattern = re.compile(
        rf"(?:^|\n)\s*(?:[IVX]+\.\s*)?(?:{heading_alt})\s*(.*?)(?=\n\s*(?:[IVX]+\.\s*)?[A-Z][A-Z ,]{{4,80}}\n|\n\s*COUNT\s+|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return []
    raw = re.sub(r"\s+", " ", match.group(1)).strip()
    if not raw:
        return []
    pieces = re.split(r"(?<=[.;])\s+|\(\w\)\s+", raw)
    return [piece.strip(" ;") for piece in pieces if len(piece.strip()) > 20][:limit]


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output
