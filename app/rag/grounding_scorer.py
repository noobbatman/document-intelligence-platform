"""Deterministic grounding-score utilities for generated legal drafts."""

from __future__ import annotations

import re
from typing import Any

_SENTENCE_RE = re.compile(
    # Split after .!? followed by whitespace (standard sentence boundary)
    # OR after ] followed by a newline (handles [UNSUPPORTED: ...] and [Page N] markers
    # that end lines without a bare period before the line break)
    r"(?<=[.!?])\s+|(?<=\])\s*\n\s*"
)
_WORD_RE = re.compile(r"\b[\w'-]+\b")
_CITATION_RE = re.compile(
    r"\[(?:Page\s+\d+(?:\s+-\s+[^\]]+)?|Chunk\s+\d+|structured_fields)\]",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(r"\[UNSUPPORTED", re.IGNORECASE)


def score(content: str) -> float:
    """Return the share of qualifying sentences that include source citations."""
    sentences = _qualifying_sentences(content)
    if not sentences:
        return 1.0
    grounded = 0
    for sentence in sentences:
        if _UNSUPPORTED_RE.search(sentence):
            continue
        if _CITATION_RE.search(sentence):
            grounded += 1
    return round(grounded / len(sentences), 2)


def score_sections(content: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copy content dict with per-section grounding scores."""
    sections = []
    for section in content.get("sections", []):
        updated = dict(section)
        updated["grounding_score"] = score(str(updated.get("content") or ""))
        sections.append(updated)
    return {**content, "sections": sections}


def overall_score(content: dict[str, Any]) -> float | None:
    """Compute word-count-weighted mean section grounding score."""
    total_words = 0
    weighted = 0.0
    for section in content.get("sections", []):
        section_words = len(_WORD_RE.findall(str(section.get("content") or "")))
        if section_words == 0:
            continue
        section_score = section.get("grounding_score")
        if section_score is None:
            section_score = score(str(section.get("content") or ""))
        total_words += section_words
        weighted += float(section_score) * section_words
    if total_words == 0:
        return None
    return round(weighted / total_words, 2)


def _qualifying_sentences(content: str) -> list[str]:
    sentences = [item.strip() for item in _SENTENCE_RE.split(content.strip()) if item.strip()]
    return [sentence for sentence in sentences if _is_qualifying_sentence(sentence)]


def _is_qualifying_sentence(sentence: str) -> bool:
    words = _WORD_RE.findall(sentence)
    if len(words) < 8:
        return False
    return _has_noun_phrase_signal(words)


def _has_noun_phrase_signal(words: list[str]) -> bool:
    """Lightweight noun-phrase proxy without loading an NLP model.

    Filters out structural filler sentences (e.g. "This section addresses the
    following matters.") that would otherwise dilute the score with uncitable
    boilerplate.  A sentence qualifies if it contains at least one title-cased
    word longer than two characters (proper noun / defined term) OR a known
    legal keyword.  This is intentionally permissive — it excludes only
    sentences with zero noun-like tokens, not sentences that merely lack
    citations.
    """
    nounish = 0
    for word in words:
        if word[:1].isupper() and len(word) > 2:
            nounish += 1
            continue
        if word.lower() in {
            "agreement",
            "plaintiff",
            "defendant",
            "court",
            "claim",
            "claims",
            "party",
            "parties",
            "subpoena",
            "jurisdiction",
            "venue",
            "relief",
            "damages",
            "account",
            "accounts",
            "document",
            "documents",
        }:
            nounish += 1
    return nounish > 0
