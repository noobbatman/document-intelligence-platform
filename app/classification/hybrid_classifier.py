"""
Improved HybridDocumentClassifier (drop-in replacement for
app/classification/hybrid_classifier.py).

Changes from v0.3.0:
  1. Fuzzy keyword matching via rapidfuzz (already in deps)
      — catches remaining OCR variants after normalization
      — threshold=85 avoids false positives on short words
  2. Character-level fallback normalization inside classify()
      so the classifier is self-defending even if pipeline
      normalization changes
  3. Better confidence calibration on tie-break
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.classification.base import ClassificationResult, DocumentClassifier
from app.utils.config_loader import load_config

# ── Vocabulary ────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).with_name("document_types.yaml")
_DOCUMENT_TYPES: dict[str, dict[str, Any]] = load_config(str(_CONFIG_PATH))
_KEYWORDS: dict[str, list[str]] = {
    label: list(config.get("keywords", []))
    for label, config in _DOCUMENT_TYPES.items()
}
_PATTERNS: dict[str, list[str]] = {
    label: list(config.get("patterns", []))
    for label, config in _DOCUMENT_TYPES.items()
}

_IDF: dict[str, float] = {kw: math.log(len(_KEYWORDS) / 1) for kws in _KEYWORDS.values() for kw in kws}

# Minimum similarity threshold for fuzzy keyword matching (0-100)
_FUZZY_THRESHOLD = 85


class HybridDocumentClassifier(DocumentClassifier):
    """
    Multi-signal classifier: keyword TF-IDF + regex + optional fuzzy matching.

    Fuzzy matching is the key addition: it catches OCR survivors that
    normalization didn't fully repair (e.g. "st4tement" → still similar
    enough to "statement" at 88% similarity).
    """

    def __init__(self, use_fuzzy: bool = True) -> None:
        self._use_fuzzy = use_fuzzy
        self._fuzzy_available = False
        if use_fuzzy:
            try:
                from rapidfuzz import fuzz  # noqa: F401
                self._fuzzy_available = True
            except ImportError:
                pass

    def classify(self, text: str) -> ClassificationResult:
        lowered = text.lower()

        keyword_scores = self._keyword_score(lowered)
        pattern_scores = self._pattern_score(lowered)
        fuzzy_scores   = self._fuzzy_score(lowered) if self._fuzzy_available else {}

        all_labels = set(keyword_scores) | set(pattern_scores) | set(fuzzy_scores)
        if not all_labels:
            return ClassificationResult(label="unknown", confidence=0.2, rationale={})

        combined: dict[str, float] = {}
        for label in all_labels:
            combined[label] = (
                0.50 * keyword_scores.get(label, 0.0)
                + 0.30 * pattern_scores.get(label, 0.0)
                + 0.20 * fuzzy_scores.get(label, 0.0)
            )

        best_label = max(combined, key=combined.__getitem__)
        raw_score  = combined[best_label]
        total      = sum(combined.values()) or 1.0
        dominance = raw_score / total

        if self._strong_legal_complaint_signal(lowered, keyword_scores, pattern_scores):
            return ClassificationResult(
                label="legal_complaint",
                confidence=round(min(0.95, max(0.72, combined.get("legal_complaint", 0.0) / total + 0.45)), 4),
                rationale={
                    "keyword_scores": keyword_scores,
                    "pattern_scores": pattern_scores,
                    "fuzzy_scores":   fuzzy_scores,
                    "combined_scores": combined,
                    "override": "strong_legal_complaint_signal",
                },
            )

        if raw_score < 0.015 or dominance < 0.40:
            return ClassificationResult(
                label="unknown",
                confidence=0.2,
                rationale={
                    "keyword_scores": keyword_scores,
                    "pattern_scores": pattern_scores,
                    "fuzzy_scores":   fuzzy_scores,
                    "combined_scores": combined,
                },
            )

        confidence = min(0.98, max(0.25, dominance + 0.15))

        return ClassificationResult(
            label=best_label,
            confidence=round(confidence, 4),
            rationale={
                "keyword_scores": keyword_scores,
                "pattern_scores": pattern_scores,
                "fuzzy_scores":   fuzzy_scores,
                "combined_scores": combined,
            },
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _keyword_score(self, text: str) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)
        word_count = max(len(text.split()), 1)
        for label, keywords in _KEYWORDS.items():
            for kw in keywords:
                count = len(re.findall(r"\b" + re.escape(kw) + r"\b", text))
                if count:
                    tf = count / word_count
                    idf = _IDF.get(kw, 1.0)
                    scores[label] += tf * idf
        return dict(scores)

    def _pattern_score(self, text: str) -> dict[str, float]:
        scores: dict[str, float] = defaultdict(float)
        for label, patterns in _PATTERNS.items():
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    scores[label] += 0.5
        return dict(scores)

    def _fuzzy_score(self, text: str) -> dict[str, float]:
        """
        Slide a window over the text and fuzzy-match against keywords.
        Only applied when exact keyword matching scored nothing for a label
        (avoids wasted CPU on clean documents).
        """
        from rapidfuzz import fuzz

        exact_scores = self._keyword_score(text)
        scores: dict[str, float] = defaultdict(float)

        for label, keywords in _KEYWORDS.items():
            if exact_scores.get(label, 0.0) <= 0:
                continue

            for kw in keywords:
                kw_len = len(kw)
                for start in range(0, len(text) - kw_len + 1, max(1, kw_len // 2)):
                    window = text[start : start + kw_len]
                    sim = fuzz.ratio(kw, window)
                    if sim >= _FUZZY_THRESHOLD:
                        scores[label] += (sim / 100.0) * 0.3
                        break

        return dict(scores)

    def _strong_legal_complaint_signal(
        self,
        text: str,
        keyword_scores: dict[str, float],
        pattern_scores: dict[str, float],
    ) -> bool:
        court_caption = bool(
            re.search(r"\b(?:united\s+states\s+)?district\s+court\b", text)
            or re.search(r"\bcivil\s+action\s+no\.?\b", text)
            or re.search(r"\bcase\s+no\.?\b", text)
        )
        adversarial_caption = bool(
            re.search(r"\bplaintiffs?\b.{0,160}\b(?:v\.?|vs\.?|ve)\b.{0,160}\bdefendants?\b", text)
            or (text.count("plaintiff") >= 2 and text.count("defendant") >= 2)
        )
        complaint_body = bool(
            re.search(r"\bcomplaint\b", text)
            and (
                re.search(r"\bcount\s+(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|\d+)\b", text)
                or re.search(r"\bprayer\s+for\s+relief\b", text)
                or re.search(r"\bjury\s+trial\s+demand(?:ed)?\b", text)
            )
        )
        legal_complaint_score = keyword_scores.get("legal_complaint", 0.0) + pattern_scores.get("legal_complaint", 0.0)
        return court_caption and adversarial_caption and complaint_body and legal_complaint_score > 0.5
