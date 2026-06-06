"""Defined-term extraction and annotation helpers for legal documents."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.config import get_settings
from app.rag.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

_MAX_DEFINITION_CHARS = 360
_TERM_RE = r'"([^"\n]{2,80})"'
_QUOTED_DEFINITION_PATTERNS = [
    re.compile(
        rf"(?:as\s+used\s+herein,\s*)?{_TERM_RE}\s+"
        r"(?:means|shall\s+mean|refers\s+to|is\s+defined\s+as)\s+"
        rf"(.{{2,{_MAX_DEFINITION_CHARS}}}?)(?=(?:\.\s+[A-Z])|\n\s*\n|;|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"(?:the\s+term\s+)?{_TERM_RE}\s+"
        r"(?:shall\s+have\s+the\s+meaning\s+set\s+forth\s+in|has\s+the\s+meaning\s+given\s+in)\s+"
        rf"(.{{2,{_MAX_DEFINITION_CHARS}}}?)(?=(?:\.\s+[A-Z])|\n\s*\n|;|\Z)",
        re.IGNORECASE | re.DOTALL,
    ),
]
_INLINE_DEFINITION_RE = re.compile(
    rf"([A-Z][A-Za-z0-9&.,'\-\s]{{3,180}}?)\s*\(\s*(?:the\s+)?{_TERM_RE}\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_ROLE_PATTERNS = {
    "Plaintiff": re.compile(r"\bPlaintiff\s+([A-Z][A-Za-z0-9 .,'&-]{2,120})", re.IGNORECASE),
    "Defendant": re.compile(r"\bDefendant\s+([A-Z][A-Za-z0-9 .,'&-]{2,120})", re.IGNORECASE),
    "Affiant": re.compile(r"\bAffiant\s+([A-Z][A-Za-z0-9 .,'&-]{2,120})", re.IGNORECASE),
}


def extract_defined_terms(
    text: str,
    *,
    fields: dict[str, Any] | None = None,
    existing: dict[str, str] | None = None,
    confirm_with_llm: bool = True,
) -> dict[str, str]:
    """Extract formal legal defined terms from document text.

    Regex handles the predictable formats cheaply. When Gemini is configured,
    a short confirmation pass can clean up false positives without blocking
    extraction if it fails.
    """

    terms: dict[str, str] = {}
    terms.update(_normalize_terms(existing or {}))
    terms.update(_regex_defined_terms(text))
    terms.update(_role_terms(text, fields or {}))
    terms = _dedupe_terms(terms)

    if confirm_with_llm and terms and get_settings().gemini_api_key:
        return _confirm_terms_with_llm(text, terms)
    return terms


def annotate_defined_terms(text: str, defined_terms: dict[str, str] | None) -> str:
    """Add compact canonical labels near defined-term mentions for embeddings."""

    if not text or not defined_terms:
        return text

    annotated = text
    for term, definition in sorted(
        defined_terms.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if not _term_is_annotatable(term, definition):
            continue
        label = _definition_label(definition)
        if not label:
            continue
        pattern = re.compile(rf'(?<![\w\["])({re.escape(term)})(?![\w"\]])')
        annotated = pattern.sub(lambda match, label=label: f"{match.group(1)} [{label}]", annotated)
    return annotated


def format_defined_terms_block(defined_terms: dict[str, str] | None) -> str:
    if not defined_terms:
        return ""
    lines = ["DEFINED TERMS IN THIS DOCUMENT:"]
    for term, definition in sorted(defined_terms.items(), key=lambda item: item[0].lower()):
        lines.append(f'- "{term}" = {definition}')
    lines.append(
        "When referring to a defined term, use the full definition on first use in each section, "
        "then the short form thereafter."
    )
    return "\n".join(lines)


def _regex_defined_terms(text: str) -> dict[str, str]:
    terms: dict[str, str] = {}
    for pattern in _QUOTED_DEFINITION_PATTERNS:
        for match in pattern.finditer(text):
            term = _clean_term(match.group(1))
            definition = _clean_definition(match.group(2))
            if _valid_pair(term, definition):
                terms[term] = definition

    for match in _INLINE_DEFINITION_RE.finditer(text):
        definition = _clean_inline_definition(match.group(1))
        term = _clean_term(match.group(2))
        if _valid_pair(term, definition):
            terms[term] = definition
    return terms


def _role_terms(text: str, fields: dict[str, Any]) -> dict[str, str]:
    terms: dict[str, str] = {}
    for term, pattern in _ROLE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            definition = _clean_definition(match.group(1))
            if _valid_pair(term, definition):
                terms[term] = definition

    plaintiffs = _first_list_value(fields.get("plaintiffs"))
    defendants = _first_list_value(fields.get("defendants"))
    if plaintiffs and "Plaintiff" not in terms:
        terms["Plaintiff"] = plaintiffs
    if defendants and "Defendant" not in terms:
        terms["Defendant"] = defendants
    return terms


def _confirm_terms_with_llm(text: str, terms: dict[str, str]) -> dict[str, str]:
    context = _term_contexts(text, terms)
    try:
        payload = GeminiClient().generate_json(
            system_prompt=(
                "You confirm formal legal defined terms. Return JSON only. "
                "Keep only reusable document-defined terms, correct obvious OCR mistakes, "
                "and do not invent terms absent from the supplied context."
            ),
            user_prompt=(
                f"CANDIDATE TERMS: {json.dumps(terms, default=str)}\n"
                f"CONTEXT: {json.dumps(context, default=str)}\n\n"
                'Return JSON as {"defined_terms": {"Term": "definition"}}.'
            ),
        )
        confirmed = payload.get("defined_terms", payload)
        if isinstance(confirmed, dict):
            normalized = _dedupe_terms(_normalize_terms(confirmed))
            return normalized or terms
    except Exception as exc:
        logger.warning("defined_terms_llm_confirmation_failed", extra={"error": str(exc)})
    return terms


def _term_contexts(text: str, terms: dict[str, str]) -> dict[str, str]:
    contexts: dict[str, str] = {}
    for term in terms:
        idx = text.lower().find(term.lower())
        if idx == -1:
            continue
        start = max(0, idx - 250)
        end = min(len(text), idx + len(term) + 250)
        contexts[term] = text[start:end]
    return contexts


def _normalize_terms(raw: dict[str, Any]) -> dict[str, str]:
    terms: dict[str, str] = {}
    for key, value in raw.items():
        term = _clean_term(str(key))
        definition = _clean_definition(str(value))
        if _valid_pair(term, definition):
            terms[term] = definition
    return terms


def _dedupe_terms(terms: dict[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    seen: set[str] = set()
    for term, definition in sorted(terms.items(), key=lambda item: item[0].lower()):
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        output[term] = definition
    return output


def _clean_term(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \"'.,;:()[]")


def _clean_definition(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" \"'.,;:")
    cleaned = re.sub(r"\s*\(\s*(?:the\s+)?\"[^\"]+\"\s*\)\s*", " ", cleaned).strip()
    return cleaned[:_MAX_DEFINITION_CHARS].strip()


def _clean_inline_definition(value: str) -> str:
    cleaned = _clean_definition(value)
    stop_words = (
        "between",
        "and",
        "with",
        "by",
        "from",
        "to",
        "of",
        "for",
        "whereas",
    )
    parts = re.split(r"\b(?:" + "|".join(stop_words) + r")\b", cleaned, flags=re.IGNORECASE)
    return parts[-1].strip(" ,.;:") if parts else cleaned


def _definition_label(definition: str) -> str:
    label = re.split(r"[,.;]", definition, maxsplit=1)[0]
    label = re.sub(r"^(?:the|a|an)\s+", "", label.strip(), flags=re.IGNORECASE)
    return label[:80].strip()


def _valid_pair(term: str, definition: str) -> bool:
    if len(term) < 2 or len(definition) < 2:
        return False
    if len(term.split()) > 8:
        return False
    if term.lower() == definition.lower():
        return False
    return not (
        term.lower() in {"section", "page", "date", "agreement"} and len(definition.split()) < 3
    )


def _term_is_annotatable(term: str, definition: str) -> bool:
    if len(term) > 80 or len(definition) > _MAX_DEFINITION_CHARS:
        return False
    return not (re.search(r"\s", term) and len(term) > 40)


def _first_list_value(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str) and value.strip():
        return value
    return None
