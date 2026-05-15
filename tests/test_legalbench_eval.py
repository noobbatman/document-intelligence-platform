from __future__ import annotations

from pathlib import Path

from evaluation.retrieval.corpus_loader import (
    build_chunks,
    load_benchmark,
    load_corpus,
    relevant_chunk_ids,
)
from evaluation.retrieval.metrics import RetrievalCaseResult, summarize


def test_legalbench_loader_maps_gold_snippets_to_chunks():
    root = Path("evaluation/retrieval/sample_legalbench_rag")
    corpus = load_corpus(root / "corpus")
    cases = load_benchmark(root / "benchmarks" / "sample_benchmark.json")
    chunks = build_chunks(corpus)

    assert corpus
    assert len(cases) == 3
    assert chunks
    assert relevant_chunk_ids(cases[0], chunks)


def test_retrieval_metrics_compute_recall_precision_and_mrr():
    results = [
        RetrievalCaseResult(
            case_id="hit-first",
            query="q1",
            relevant_ids=["a"],
            retrieved_ids=["a", "b", "c"],
            first_hit_rank=1,
        ),
        RetrievalCaseResult(
            case_id="hit-third",
            query="q2",
            relevant_ids=["z"],
            retrieved_ids=["x", "y", "z"],
            first_hit_rank=3,
        ),
        RetrievalCaseResult(
            case_id="miss",
            query="q3",
            relevant_ids=["m"],
            retrieved_ids=["x", "y", "z"],
            first_hit_rank=None,
        ),
    ]

    summary = summarize(results, top_ks=[1, 3])

    assert summary["recall@1"] == 0.3333
    assert summary["recall@3"] == 0.6667
    assert summary["precision@1"] == 0.3333
    assert summary["mrr"] == 0.4444
