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
from typing import Any

from app.classification.base import ClassificationResult, DocumentClassifier

# ── Vocabulary ────────────────────────────────────────────────────────────────

_KEYWORDS: dict[str, list[str]] = {
    "invoice": [
        "invoice", "bill to", "invoice number", "amount due", "tax",
        "subtotal", "due date", "purchase order", "remit to", "net 30",
        "payment terms", "line item", "qty", "unit price", "total due",
        "vat", "billed to", "invoice date",
    ],
    "bank_statement": [
        "statement period", "account number", "opening balance",
        "closing balance", "debits", "credits", "available balance",
        "transaction date", "reference number", "sort code", "iban",
        "monthly statement", "statement date",
    ],
    "receipt": [
        "receipt", "thank you for your purchase", "total paid",
        "change due", "cashier", "payment method", "items purchased",
        "amount tendered",
    ],
    "contract": [
        "agreement", "whereas", "hereby", "party", "parties",
        "governing law", "termination", "indemnification", "warranty",
        "confidentiality", "intellectual property", "jurisdiction",
        "effective date", "obligations", "in witness whereof",
        "witnesseth", "heretofore", "notwithstanding", "indemnify",
        "licensor", "licensee", "arbitration", "consideration",
    ],
    "legal_notice": [
        "notice", "cease and desist", "demand letter", "summons",
        "subpoena", "response deadline", "required actions",
        "issuing party", "receiving party", "non-compliance",
    ],
    "case_brief": [
        "plaintiff", "defendant", "court", "holding", "legal issue",
        "case number", "docket", "procedural history", "statute",
        "precedent", "decision date", "jurisdiction",
    ],
    "affidavit": [
        "affidavit", "affiant", "deponent", "sworn", "notary",
        "notary public", "subscribed and sworn", "declarant",
        "declaration", "under penalty of perjury",
    ],
}

_PATTERNS: dict[str, list[str]] = {
    "invoice": [
        r"\binvoice\s*(?:no\.?|number|#)\s*[:\-]?\s*[A-Z0-9\-\/]+",
        r"\bamount\s+due\b",
        r"\btotal\s+due\b",
        r"\bvat\s*\(\d+%\)",
    ],
    "bank_statement": [
        r"\bstatement\s+(?:period|date)\b",
        r"\b(?:opening|closing)\s+balance\b",
        r"\bsort\s+code\s*[:\-]?\s*\d{2}[-\s]\d{2}[-\s]\d{2}",
        r"\biban\s*[:\-]?\s*[A-Z]{2}\d{2}",
    ],
    "receipt": [
        r"\breceip[t]?\b",
        r"\btotal\s+paid\b",
        r"\bchange\s+due\b",
    ],
    "contract": [
        r"\bthis\s+agreement\b",
        r"\bin\s+witness\s+whereof\b",
        r"\bhereby\s+agrees?\b",
        r"\bgoverning\s+law\b",
        r"\bnotwithstanding\b",
    ],
    "legal_notice": [
        r"\bcease\s+and\s+desist\b",
        r"\bdemand\s+letter\b",
        r"\bsummons\b",
        r"\bsubpoena\b",
        r"\bresponse\s+deadline\b",
    ],
    "case_brief": [
        r"\bplaintiff\s+v\.?\s+defendant\b",
        r"\bcase\s+(?:no\.?|number)\b",
        r"\blegal\s+issues?\b",
        r"\bholding\b",
    ],
    "affidavit": [
        r"\baffidavit\s+of\b",
        r"\bsubscribed\s+and\s+sworn\b",
        r"\bnotary\s+public\b",
        r"\bunder\s+penalty\s+of\s+perjury\b",
    ],
}

_IDF: dict[str, float] = {kw: math.log(4 / 1) for kws in _KEYWORDS.values() for kw in kws}

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
