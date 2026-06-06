"""Evaluate generated draft groundedness against reference sections.

Expected input JSON:
[
  {
    "draft": {"sections": [...]},
    "reference": {"sections": [{"key": "...", "content": "..."}]},
    "evidence": {"chunk_id": "chunk text"}
  }
]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9]{3,}", text)}


def _rouge_lite(candidate: str, reference: str) -> float:
    ref = _tokens(reference)
    if not ref:
        return 0.0
    return len(_tokens(candidate).intersection(ref)) / len(ref)


def _grounded(section: dict, evidence: dict[str, str]) -> float:
    content = section.get("content", "")
    if "[UNSUPPORTED:" in content:
        return 1.0
    source = " ".join(evidence.get(chunk_id, "") for chunk_id in section.get("evidence_chunk_ids", []))
    source_tokens = _tokens(source)
    claims = [part.strip() for part in re.split(r"(?<=[.!?])\s+", content) if part.strip()]
    if not claims:
        return 0.0
    supported = 0
    for claim in claims:
        claim_tokens = _tokens(claim)
        if claim_tokens and len(claim_tokens.intersection(source_tokens)) / len(claim_tokens) >= 0.5:
            supported += 1
    return supported / len(claims)


def evaluate(cases: list[dict]) -> dict:
    rouge_scores: list[float] = []
    grounded_scores: list[float] = []
    unsupported_total = 0
    unsupported_honest = 0

    for case in cases:
        references = {
            section["key"]: section.get("content", "")
            for section in case.get("reference", {}).get("sections", [])
        }
        evidence = case.get("evidence", {})
        for section in case.get("draft", {}).get("sections", []):
            key = section.get("key")
            rouge_scores.append(_rouge_lite(section.get("content", ""), references.get(key, "")))
            grounded_scores.append(_grounded(section, evidence))
            if not references.get(key):
                unsupported_total += 1
                if "[UNSUPPORTED:" in section.get("content", ""):
                    unsupported_honest += 1

    return {
        "cases": len(cases),
        "rouge_lite": round(sum(rouge_scores) / len(rouge_scores), 4) if rouge_scores else 0.0,
        "groundedness": round(sum(grounded_scores) / len(grounded_scores), 4) if grounded_scores else 0.0,
        "unsupported_honesty_rate": round(unsupported_honest / unsupported_total, 4) if unsupported_total else 1.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.cases.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
