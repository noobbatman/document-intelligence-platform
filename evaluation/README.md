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
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --with-expansion --include-details --output evaluation\results\retrieval_results.json
```

Current committed smoke results:

| Run | Queries | Recall@5 | Recall@10 | Precision@5 | Precision@10 | MRR | Backend |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 3 | 1.00 | 1.00 | 0.20 | 0.10 | 1.00 | sentence-transformers/BGE |
| expanded | 3 | 1.00 | 1.00 | 0.20 | 0.10 | 1.00 | sentence-transformers/BGE |

These smoke numbers are deliberately not presented as production retrieval quality. They prove that the evaluator loads a corpus, chunks it with the production `SectionAwareChunker`, maps gold snippets to chunks, embeds with `BAAI/bge-base-en-v1.5`, retrieves top-k passages, and computes metrics. The committed result file reports `embedding_backend: sentence_transformers`.

### Run the official LegalBench-RAG data

After downloading the official dataset so that `data/corpus` and `data/benchmarks` exist, install the declared dependencies first (`.\.venv\Scripts\python -m pip install -r requirements.txt`). The official-data commands intentionally do not pass `--allow-hash-fallback`, so they fail fast if real BGE embeddings are unavailable.

**Fast 25-query run** (scopes corpus to referenced files only — much faster):

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data\legalbenchrag --limit 25 --output evaluation\results\legalbench_rag_mini_results.json
```

**500-query mini run** (full corpus, with query expansion):

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data\legalbenchrag --limit 500 --corpus-scope all --with-expansion --output evaluation\results\legalbench_rag_mini_results.json
```

**Full dataset run**:

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data\legalbenchrag --corpus-scope all --with-expansion --output evaluation\results\legalbench_rag_full_results.json
```

### Committed mini results

| Queries | Corpus scope | Recall@5 | Recall@10 | Precision@5 | Precision@10 | MRR | Backend |
|---:|---|---:|---:|---:|---:|---:|---|
| 25 | referenced | **0.72** | **0.84** | 0.184 | 0.152 | **0.43** | sentence-transformers/BGE |

Full result file: [`evaluation/results/legalbench_rag_mini_results.json`](results/legalbench_rag_mini_results.json).

## Metrics

- **Recall@K**: fraction of queries where at least one gold passage is retrieved in the top K.
- **Precision@K**: fraction of top-K retrieved chunks that are gold-relevant.
- **MRR**: mean reciprocal rank of the first retrieved gold passage.

The evaluator runs two variants when `--with-expansion` is set:

- **baseline**: raw query -> production BGE query embedding -> in-memory cosine retrieval.
- **expanded**: raw query -> Gemini legal query expansion -> production BGE query embedding -> retrieval.

If `GEMINI_API_KEY` is not configured, expansion safely falls back to the original query and the output records `query_expansion.backend = disabled_or_no_key`.

## Methodology limits

**The committed fixture is a smoke test, not a benchmark result.** It covers 3 queries over a 9-chunk corpus. Perfect scores (Recall@5 = 1.0, MRR = 1.0) on a 9-chunk corpus are trivially achievable by any retriever — there is almost nowhere to rank the gold chunk except in the top K. These numbers say little about retrieval quality on a real legal corpus and must not be compared to prior art as a performance claim.

The committed JSON records `embedding_backend: sentence_transformers`, so the smoke run did use the real BGE embedding path. If a future run reports `hash_fallback`, it should be treated only as a pipeline smoke test.

The `expanded` run shows `expanded_query` identical to the raw query because `GEMINI_API_KEY` was not set at run time. The baseline vs. expansion comparison is therefore meaningless in the committed results. To produce a valid comparison, set `GEMINI_API_KEY` before running.

The committed mini result file (`evaluation/results/legalbench_rag_mini_results.json`) was produced from 25 queries sampled across ContractNLI, CUAD, MAUD, and PrivacyQA with `corpus_scope: referenced`. These are real retrieval numbers from real legal text. For a statistically stronger claim, run the full 500-query mini with `--corpus-scope all`.
