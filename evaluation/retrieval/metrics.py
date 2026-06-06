"""Metric helpers for legal retrieval evaluations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    case_id: str
    query: str
    relevant_ids: list[str]
    retrieved_ids: list[str]
    first_hit_rank: int | None


def recall_at_k(results: list[RetrievalCaseResult], k: int) -> float:
    if not results:
        return 0.0
    hits = 0
    for result in results:
        relevant = set(result.relevant_ids)
        if relevant.intersection(result.retrieved_ids[:k]):
            hits += 1
    return hits / len(results)


def precision_at_k(results: list[RetrievalCaseResult], k: int) -> float:
    if not results or k <= 0:
        return 0.0
    total = 0.0
    for result in results:
        relevant = set(result.relevant_ids)
        retrieved = result.retrieved_ids[:k]
        total += len(relevant.intersection(retrieved)) / k
    return total / len(results)


def mean_reciprocal_rank(results: list[RetrievalCaseResult]) -> float:
    if not results:
        return 0.0
    total = 0.0
    for result in results:
        total += 1.0 / result.first_hit_rank if result.first_hit_rank else 0.0
    return total / len(results)


def summarize(results: list[RetrievalCaseResult], *, top_ks: list[int]) -> dict[str, float | int]:
    summary: dict[str, float | int] = {"total_queries": len(results)}
    for k in top_ks:
        summary[f"recall@{k}"] = round(recall_at_k(results, k), 4)
        summary[f"precision@{k}"] = round(precision_at_k(results, k), 4)
    summary["mrr"] = round(mean_reciprocal_rank(results), 4)
    return summary
