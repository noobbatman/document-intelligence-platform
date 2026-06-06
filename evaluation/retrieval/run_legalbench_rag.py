"""LegalBench-RAG retrieval benchmark runner.

This script expects the official LegalBench-RAG download layout:

    data/
      corpus/       # raw legal text files
      benchmarks/   # JSON files with query + ground_truth snippet refs

It also works with the tiny smoke fixture committed under
`evaluation/retrieval/sample_legalbench_rag`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rag.embedder import Embedder  # noqa: E402
from app.rag.gemini_client import GeminiClient  # noqa: E402
from evaluation.retrieval.corpus_loader import (  # noqa: E402
    BenchmarkCase,
    build_chunks,
    build_index,
    load_benchmark,
    load_corpus,
    relevant_chunk_ids,
)
from evaluation.retrieval.metrics import RetrievalCaseResult, summarize  # noqa: E402


class QueryExpander:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._cache: dict[str, str] = {}
        self._client: GeminiClient | None = None

    def expand(self, query: str) -> str:
        if not self.enabled:
            return query
        if query in self._cache:
            return self._cache[query]
        expanded = self._expand_with_gemini(query)
        self._cache[query] = expanded
        return expanded

    def _expand_with_gemini(self, query: str) -> str:
        if not os.getenv("GEMINI_API_KEY"):
            return query
        try:
            if self._client is None:
                self._client = GeminiClient()
            payload = self._client.generate_json(
                system_prompt=(
                    "You are a legal search assistant. Expand retrieval queries with "
                    "6-10 synonymous legal terms that may appear in relevant legal passages. "
                    'Return JSON only: {"expanded_query": "..."}.'
                ),
                user_prompt=f"Original query: {query}",
                model_id=self._client.settings.query_expansion_model,
                max_output_tokens=self._client.settings.query_expansion_max_tokens,
                temperature=0,
            )
            expanded = str(payload.get("expanded_query") or "").strip()
            return f"{query} {expanded}" if expanded else query
        except Exception:
            return query


def evaluate_cases(
    cases: list[BenchmarkCase],
    *,
    chunks,
    index,
    embedder: Embedder,
    top_ks: list[int],
    expander: QueryExpander,
    progress: bool = False,
) -> tuple[dict, list[dict]]:
    max_k = max(top_ks)
    case_results: list[RetrievalCaseResult] = []
    details: list[dict] = []
    for idx, case in enumerate(cases, start=1):
        if progress and (idx == 1 or idx % 10 == 0 or idx == len(cases)):
            print(f"Evaluating query {idx}/{len(cases)}...", flush=True)
        relevant = relevant_chunk_ids(case, chunks)
        if not relevant:
            continue
        expanded_query = expander.expand(case.query)
        query_vector = embedder.encode_query(expanded_query)
        retrieved = index.retrieve(query_vector, top_k=max_k)
        retrieved_ids = [item.chunk_id for item in retrieved]
        relevant_set = set(relevant)
        first_hit_rank = next(
            (idx + 1 for idx, chunk_id in enumerate(retrieved_ids) if chunk_id in relevant_set),
            None,
        )
        case_results.append(
            RetrievalCaseResult(
                case_id=case.case_id,
                query=case.query,
                relevant_ids=relevant,
                retrieved_ids=retrieved_ids,
                first_hit_rank=first_hit_rank,
            )
        )
        details.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expanded_query": expanded_query if expander.enabled else None,
                "relevant_chunk_ids": relevant,
                "retrieved_chunk_ids": retrieved_ids,
                "first_hit_rank": first_hit_rank,
            }
        )
    return summarize(case_results, top_ks=top_ks), details


def benchmark_files(data_root: Path, explicit: list[Path]) -> list[Path]:
    if explicit:
        return explicit
    benchmarks_dir = data_root / "benchmarks"
    return sorted(benchmarks_dir.glob("*.json"))


def run(args: argparse.Namespace) -> dict:
    corpus_dir = args.corpus_dir or args.data_root / "corpus"
    files = benchmark_files(args.data_root, args.benchmark)
    if not files:
        raise SystemExit(f"No benchmark JSON files found under {args.data_root / 'benchmarks'}")
    cases: list[BenchmarkCase] = []
    for file in files:
        cases.extend(load_benchmark(file))
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("No benchmark cases with queries and ground-truth snippets were loaded")

    include_paths = None
    if args.corpus_scope == "referenced":
        include_paths = {
            snippet.corpus_path
            for case in cases
            for snippet in case.snippets
            if snippet.corpus_path
        }
        print(
            f"Loading referenced corpus files for {len(cases)} cases "
            f"({len(include_paths)} unique documents)...",
            flush=True,
        )
    else:
        print("Loading full corpus...", flush=True)
    corpus = load_corpus(corpus_dir, include_paths=include_paths)
    if not corpus:
        raise SystemExit(f"No text corpus files found under {corpus_dir}")

    embedder = Embedder()
    print(f"Chunking {len(corpus)} corpus documents...", flush=True)
    chunks = build_chunks(corpus)
    print(f"Embedding {len(chunks)} chunks with {embedder.settings.embedding_model}...", flush=True)
    index = build_index(chunks, embedder=embedder)
    if getattr(embedder, "_model_load_failed", False) and not args.allow_hash_fallback:
        raise SystemExit(
            "sentence-transformers/BGE is unavailable, so the evaluator would use hash fallback. "
            r"Install dependencies with `.\.venv\Scripts\python -m pip install -r requirements.txt` "
            "or rerun with --allow-hash-fallback for smoke tests only."
        )
    top_ks = sorted(set(args.top_k))

    runs: dict[str, dict] = {}
    details: dict[str, list[dict]] = {}
    baseline_summary, baseline_details = evaluate_cases(
        cases,
        chunks=chunks,
        index=index,
        embedder=embedder,
        top_ks=top_ks,
        expander=QueryExpander(enabled=False),
        progress=True,
    )
    runs["baseline"] = baseline_summary
    details["baseline"] = baseline_details

    if args.with_expansion:
        expanded_summary, expanded_details = evaluate_cases(
            cases,
            chunks=chunks,
            index=index,
            embedder=embedder,
            top_ks=top_ks,
            expander=QueryExpander(enabled=True),
            progress=True,
        )
        runs["expanded"] = expanded_summary
        details["expanded"] = expanded_details

    return {
        "benchmark": "LegalBench-RAG compatible retrieval evaluation",
        "data_root": str(args.data_root),
        "benchmark_files": [str(path) for path in files],
        "model": embedder.settings.embedding_model,
        "embedding_backend": (
            "hash_fallback"
            if getattr(embedder, "_model_load_failed", False)
            else "sentence_transformers"
        ),
        "query_expansion": {
            "requested": bool(args.with_expansion),
            "backend": "gemini"
            if args.with_expansion and os.getenv("GEMINI_API_KEY")
            else "disabled_or_no_key",
        },
        "chunk_size_chars": embedder.settings.chunk_size_chars,
        "chunk_overlap_chars": embedder.settings.chunk_overlap_chars,
        "corpus_documents": len(corpus),
        "corpus_scope": args.corpus_scope,
        "corpus_chunks": len(chunks),
        "evaluated_at": datetime.now(UTC).isoformat(),
        "runs": runs,
        "case_details": details if args.include_details else {},
        "notes": [
            "Uses the production SectionAwareChunker and Embedder code paths.",
            "If embedding_backend is hash_fallback, numbers are smoke-test only and do not represent production BGE quality.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LegalBench-RAG retrieval evaluation")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("evaluation/retrieval/sample_legalbench_rag"),
        help="Directory containing corpus/ and benchmarks/ subdirectories.",
    )
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--benchmark", type=Path, action="append", default=[])
    parser.add_argument("--top-k", type=int, nargs="+", default=[5, 10])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--corpus-scope",
        choices=["referenced", "all"],
        default="referenced",
        help=(
            "Use only corpus files referenced by the selected benchmark cases for fast "
            "iteration, or all corpus files for a stricter full-corpus retrieval run."
        ),
    )
    parser.add_argument("--with-expansion", action="store_true")
    parser.add_argument("--include-details", action="store_true")
    parser.add_argument(
        "--allow-hash-fallback",
        action="store_true",
        help="Allow deterministic hash embeddings when sentence-transformers/BGE is unavailable. Use for smoke tests only.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results/retrieval_results.json"),
    )
    args = parser.parse_args()
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(_format_table(result))
    print(f"Saved results to {args.output}")


def _format_table(result: dict) -> str:
    lines = [
        "LegalBench-RAG Evaluation Results",
        f"Model: {result['model']} ({result['embedding_backend']}) | Chunk: {result['chunk_size_chars']} chars / {result['chunk_overlap_chars']} overlap",
        "-" * 48,
    ]
    for run_name, summary in result["runs"].items():
        lines.append(f"{run_name}:")
        for key, value in summary.items():
            lines.append(f"  {key}: {value}")
    lines.append("-" * 48)
    lines.append(
        f"Corpus scope: {result['corpus_scope']} | Documents: {result['corpus_documents']} | Chunks: {result['corpus_chunks']}"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
