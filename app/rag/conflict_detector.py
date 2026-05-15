"""Deterministic intra-document conflict detection for legal documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class ConflictItem:
    conflict_type: str  # "governing_law" | "defined_term" | "date" | "amount"
    description: str
    chunk_indices: list[int]
    severity: str  # "high" | "medium" | "low"
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "description": self.description,
            "chunk_indices": self.chunk_indices,
            "severity": self.severity,
            "field": self.field,
        }


# ── Patterns ──────────────────────────────────────────────────────────────────

_GOV_LAW_RE = re.compile(
    r"(?:governed\s+by|construed\s+(?:in\s+accordance\s+with|under)|"
    r"subject\s+to\s+(?:the\s+)?laws?\s+of)"
    r"\s+(?:the\s+)?(?:laws?\s+of\s+(?:the\s+(?:state|commonwealth)\s+of\s+)?)?"
    r"([A-Z][A-Za-z ]{2,40})(?=[,.\s]|$)",
    re.IGNORECASE,
)

_DATE_LABEL_RE = re.compile(
    r"(effective\s+date|execution\s+date|commencement\s+date|agreement\s+date|"
    r"dated\s+(?:as\s+of\s+)?|signed\s+(?:on|as\s+of)\s+)"
    r"[:\s]*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

_AMOUNT_LABEL_RE = re.compile(
    r"((?:monthly|annual|yearly|quarterly|total|base|license|service)\s+fee[s]?|"
    r"(?:monthly|annual|yearly|quarterly|total)\s+payment[s]?|"
    r"purchase\s+price|contract\s+value|base\s+salary)"
    r"[^$\d\n]{0,40}\$?([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

_QUOTED_DEF_RE = re.compile(
    r'"([^"\n]{2,60})"\s+(?:means?|shall\s+mean|refers?\s+to|is\s+defined\s+as)\s+'
    r"([^.;]{5,180}?)(?=[.;]|$)",
    re.IGNORECASE | re.DOTALL,
)

_GOV_LAW_NOISE = {
    "the",
    "this",
    "such",
    "any",
    "all",
    "an",
    "a",
    "its",
    "their",
    "our",
    "agreement",
    "contract",
    "jurisdiction",
    "section",
    "article",
    "law",
    "laws",
    "applicable law",
    "applicable laws",
    "general principles",
}


# ── Public API ────────────────────────────────────────────────────────────────


def detect_conflicts(
    chunks: list[str],
    *,
    defined_terms: dict[str, str] | None = None,
    fields: dict[str, Any] | None = None,
) -> list[ConflictItem]:
    """Return all detected intra-document conflicts across the given chunk texts."""
    conflicts: list[ConflictItem] = []
    conflicts.extend(_governing_law_conflicts(chunks))
    conflicts.extend(_defined_term_conflicts(chunks, defined_terms or {}))
    conflicts.extend(_date_label_conflicts(chunks))
    conflicts.extend(_amount_label_conflicts(chunks))
    return conflicts


def format_conflicts_block(conflicts: list[ConflictItem]) -> str:
    """Format a conflict warning block for injection into draft prompts."""
    if not conflicts:
        return ""
    severity_prefix = {"high": "[HIGH]", "medium": "[MEDIUM]", "low": "[LOW]"}
    lines = [
        "KNOWN CONFLICTS IN THIS DOCUMENT:",
        "These contradictions were detected automatically. Acknowledge them when relevant "
        "and do not assert either fact as authoritative without noting the discrepancy.",
    ]
    for item in conflicts:
        prefix = severity_prefix.get(item.severity, "[?]")
        lines.append(f"  {prefix} {item.conflict_type.upper()}: {item.description}")
    return "\n".join(lines)


# ── Per-type detectors ────────────────────────────────────────────────────────


def _governing_law_conflicts(chunks: list[str]) -> list[ConflictItem]:
    law_chunks: dict[str, list[int]] = {}
    for idx, text in enumerate(chunks):
        for match in _GOV_LAW_RE.finditer(text):
            value = _normalize_value(match.group(1))
            if not value or value in _GOV_LAW_NOISE or len(value) < 3:
                continue
            law_chunks.setdefault(value, []).append(idx)

    unique_laws = list(law_chunks.keys())
    if len(unique_laws) < 2:
        return []

    all_indices = sorted({idx for idxs in law_chunks.values() for idx in idxs})
    top_laws = unique_laws[:4]
    quoted = ", ".join(f'"{law}"' for law in top_laws)
    return [
        ConflictItem(
            conflict_type="governing_law",
            description=f"Conflicting governing law references: {quoted}.",
            chunk_indices=all_indices,
            severity="high",
            field="governing_law",
        )
    ]


def _defined_term_conflicts(
    chunks: list[str], existing_terms: dict[str, str]
) -> list[ConflictItem]:
    # Seed with document-level defined terms (Priority 3); index -1 means non-chunk source
    term_defs: dict[str, list[tuple[str, int]]] = {}
    for term, defn in existing_terms.items():
        term_defs.setdefault(term.lower(), []).append((_clean_defn(defn), -1))

    for idx, text in enumerate(chunks):
        for match in _QUOTED_DEF_RE.finditer(text):
            term = match.group(1).strip()
            defn = _clean_defn(match.group(2))
            if len(term) < 2 or len(defn) < 5:
                continue
            term_defs.setdefault(term.lower(), []).append((defn, idx))

    conflicts: list[ConflictItem] = []
    for term_key, entries in term_defs.items():
        unique_defs = list(dict.fromkeys(d for d, _ in entries))
        if len(unique_defs) < 2:
            continue
        chunk_indices = sorted({idx for _, idx in entries if idx >= 0})
        conflicts.append(
            ConflictItem(
                conflict_type="defined_term",
                description=f'"{term_key}" has conflicting definitions across sections.',
                chunk_indices=chunk_indices,
                severity="medium",
                field=None,
            )
        )
    return conflicts


def _date_label_conflicts(chunks: list[str]) -> list[ConflictItem]:
    date_by_label: dict[str, list[tuple[str, int]]] = {}
    for idx, text in enumerate(chunks):
        for match in _DATE_LABEL_RE.finditer(text):
            label = _normalize_value(match.group(1))
            date_val = match.group(2).strip()
            date_by_label.setdefault(label, []).append((date_val, idx))

    conflicts: list[ConflictItem] = []
    for label, entries in date_by_label.items():
        unique_dates = list(dict.fromkeys(d for d, _ in entries))
        if len(unique_dates) < 2:
            continue
        chunk_indices = sorted({idx for _, idx in entries})
        conflicts.append(
            ConflictItem(
                conflict_type="date",
                description=f'Inconsistent "{label}": {" vs. ".join(unique_dates[:3])}.',
                chunk_indices=chunk_indices,
                severity="medium",
                field="date",
            )
        )
    return conflicts


def _amount_label_conflicts(chunks: list[str]) -> list[ConflictItem]:
    amount_by_label: dict[str, list[tuple[str, int]]] = {}
    for idx, text in enumerate(chunks):
        for match in _AMOUNT_LABEL_RE.finditer(text):
            label = _normalize_value(match.group(1))
            amount = match.group(2).replace(",", "").strip()
            amount_by_label.setdefault(label, []).append((amount, idx))

    conflicts: list[ConflictItem] = []
    for label, entries in amount_by_label.items():
        unique_amounts = list(dict.fromkeys(a for a, _ in entries))
        if len(unique_amounts) < 2:
            continue
        chunk_indices = sorted({idx for _, idx in entries})
        conflicts.append(
            ConflictItem(
                conflict_type="amount",
                description=f'Inconsistent "{label}": ${" vs. $".join(unique_amounts[:3])}.',
                chunk_indices=chunk_indices,
                severity="high",
                field="amount",
            )
        )
    return conflicts


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,;:'\"-").lower()


def _clean_defn(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" .,;:'\"")[:200]
