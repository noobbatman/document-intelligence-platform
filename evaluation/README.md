# Evaluation

This directory contains reproducible evaluation scripts for the legal document RAG pipeline.

## LegalBench-RAG-compatible retrieval benchmark

`evaluation/retrieval/run_legalbench_rag.py` evaluates retrieval against a LegalBench-RAG-style directory layout:

```text
data/
  corpus/       # legal text corpus files
  benchmarks/   # JSON benchmark cases with query + gold snippets
```

The official LegalBench-RAG benchmark is described in arXiv:2408.10343 and the public repository at https://github.com/zeroentropy-ai/legalbenchrag. It contains 6,858 legal query-answer pairs over more than 79M characters of legal text. This project does not vendor that large dataset; instead it ships a tiny smoke fixture under `evaluation/retrieval/sample_legalbench_rag` so the runner and metric plumbing can be verified in CI.

### Run the committed smoke fixture

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --with-expansion --include-details --allow-hash-fallback --output evaluation\results\retrieval_results.json
```

Current committed smoke results:

| Run | Queries | Recall@5 | Recall@10 | Precision@5 | Precision@10 | MRR | Backend |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 3 | 1.00 | 1.00 | 0.20 | 0.10 | 1.00 | hash fallback |
| expanded | 3 | 1.00 | 1.00 | 0.20 | 0.10 | 1.00 | hash fallback |

These smoke numbers are deliberately not presented as production retrieval quality. They prove that the evaluator loads a corpus, chunks it with the production `SectionAwareChunker`, maps gold snippets to chunks, retrieves top-k passages, and computes metrics. When `sentence-transformers` and `BAAI/bge-base-en-v1.5` are installed, omit `--allow-hash-fallback`; the evaluator will then require real embeddings and report `embedding_backend: sentence_transformers` in `evaluation/results/retrieval_results.json`.

### Run the official LegalBench-RAG data

After downloading the official dataset so that `data/corpus` and `data/benchmarks` exist, install the declared dependencies first (`.\.venv\Scripts\python -m pip install -r requirements.txt`). The official-data commands intentionally do not pass `--allow-hash-fallback`, so they fail fast if real BGE embeddings are unavailable:

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data --with-expansion --output evaluation\results\legalbench_rag_full_results.json
```

Use `--limit 500` for a LegalBench-RAG-mini style run:

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data --limit 500 --with-expansion --output evaluation\results\legalbench_rag_mini_results.json
```

## Metrics

- **Recall@K**: fraction of queries where at least one gold passage is retrieved in the top K.
- **Precision@K**: fraction of top-K retrieved chunks that are gold-relevant.
- **MRR**: mean reciprocal rank of the first retrieved gold passage.

The evaluator runs two variants when `--with-expansion` is set:

- **baseline**: raw query -> production BGE query embedding -> in-memory cosine retrieval.
- **expanded**: raw query -> Gemini legal query expansion -> production BGE query embedding -> retrieval.

If `GEMINI_API_KEY` is not configured, expansion safely falls back to the original query and the output records `query_expansion.backend = disabled_or_no_key`.

## Methodology limits

**The committed fixture is a smoke test, not a benchmark result.** It covers 3 queries over a 9-chunk corpus. Perfect scores (Recall@5 = 1.0, MRR = 1.0) on a 9-chunk corpus are trivially achievable by any retriever — there is almost nowhere to rank the gold chunk except in the top K. These numbers say nothing about retrieval quality on a real legal corpus and must not be compared to any prior art or cited as evidence of system performance.

The `embedding_backend: hash_fallback` field in the result JSON confirms real BGE embeddings were not used. Hash embeddings do not capture semantic similarity — they merely verify that the evaluation pipeline runs end-to-end.

The `expanded` run shows `expanded_query` identical to the raw query because `GEMINI_API_KEY` was not set at run time. The baseline vs. expansion comparison is therefore meaningless in the committed results. To produce a valid comparison, set `GEMINI_API_KEY` before running.

Full benchmark results should be generated from the official dataset (see below), committed to `evaluation/results/legalbench_rag_mini_results.json`, and reported in the README Benchmarks table. Only those numbers reflect actual retrieval quality.
