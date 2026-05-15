"""Lightweight jurisdiction tagging for legal retrieval chunks."""

from __future__ import annotations

import re
from typing import Any

_STATE_ABBREVIATIONS = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE_PREFIXES = {
    "ala": "AL",
    "alaska": "AK",
    "ariz": "AZ",
    "ark": "AR",
    "cal": "CA",
    "colo": "CO",
    "conn": "CT",
    "del": "DE",
    "fla": "FL",
    "ga": "GA",
    "haw": "HI",
    "idaho": "ID",
    "ill": "IL",
    "ind": "IN",
    "iowa": "IA",
    "kan": "KS",
    "ky": "KY",
    "la": "LA",
    "me": "ME",
    "md": "MD",
    "mass": "MA",
    "mich": "MI",
    "minn": "MN",
    "miss": "MS",
    "mo": "MO",
    "mont": "MT",
    "neb": "NE",
    "nev": "NV",
    "nh": "NH",
    "nj": "NJ",
    "nm": "NM",
    "ny": "NY",
    "nc": "NC",
    "nd": "ND",
    "ohio": "OH",
    "okla": "OK",
    "or": "OR",
    "pa": "PA",
    "ri": "RI",
    "sc": "SC",
    "sd": "SD",
    "tenn": "TN",
    "tex": "TX",
    "utah": "UT",
    "vt": "VT",
    "va": "VA",
    "wash": "WA",
    "wva": "WV",
    "wis": "WI",
    "wisc": "WI",
    "wyo": "WY",
}
_DISTRICT_RE = re.compile(
    r"\b([NESW]\.D\.|D\.)\s+([A-Z][a-z]+\.?|[A-Z]{2})\b",
    re.IGNORECASE,
)
_STATE_CODE_RE = re.compile(
    r"\b("
    + "|".join(sorted((re.escape(item) for item in _STATE_PREFIXES), key=len, reverse=True))
    + r")\.?\s+(?:Stat\.|Code|Civ\.|CPLR|Rev\.|Admin\.)",
    re.IGNORECASE,
)
_STATE_NAME_RE = re.compile(
    r"\b(?:laws?\s+of\s+(?:the\s+)?state\s+of|state\s+of)\s+([A-Z][A-Za-z ]{2,30})\b",
    re.IGNORECASE,
)


def detect_chunk_jurisdiction(text: str) -> str | None:
    """Return one primary jurisdiction tag for a chunk."""

    tags = detect_jurisdiction_tags(text)
    if not tags:
        return None
    for tag in tags:
        if tag.startswith("federal:"):
            return tag
    for tag in tags:
        if tag.startswith("state:"):
            return tag
    return tags[0]


def detect_document_jurisdiction_tags(text: str, fields: dict[str, Any] | None = None) -> list[str]:
    """Detect document-level jurisdiction tags from extraction fields and OCR text."""

    combined = [text]
    for key in ("jurisdiction", "venue", "governing_law", "court", "statutes"):
        value = (fields or {}).get(key)
        if isinstance(value, list):
            combined.extend(str(item) for item in value)
        elif value:
            combined.append(str(value))
    return detect_jurisdiction_tags("\n".join(combined))


def detect_jurisdiction_tags(text: str) -> list[str]:
    tags: list[str] = []
    if not text:
        return tags
    if re.search(r"\b(?:\d+\s+U\.S\.C\.?|Fed\.\s*R\.|U\.S\.\s*Const\.)", text, re.IGNORECASE):
        tags.append("federal")
    for match in _DISTRICT_RE.finditer(text):
        state = _normalize_state(match.group(2))
        district = _normalize_district(match.group(1))
        if state:
            tags.append(f"federal:{district}{state}")
            tags.append("federal")
    for match in _STATE_CODE_RE.finditer(text):
        state = _normalize_state(match.group(1))
        if state:
            tags.append(f"state:{state}")
    for match in _STATE_NAME_RE.finditer(text):
        state = _normalize_state(match.group(1))
        if state:
            tags.append(f"state:{state}")
    return _dedupe(tags)


def _normalize_district(value: str) -> str:
    compact = value.upper().replace(".", "")
    if compact in {"ED", "ND", "SD", "WD"}:
        return compact
    return "D"


def _normalize_state(value: str) -> str | None:
    raw = value.strip().strip(".").lower()
    if len(raw) == 2 and raw.upper() in _STATE_ABBREVIATIONS.values():
        return raw.upper()
    raw = re.sub(r"\s+", " ", raw)
    if raw in _STATE_ABBREVIATIONS:
        return _STATE_ABBREVIATIONS[raw]
    return _STATE_PREFIXES.get(raw)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output
