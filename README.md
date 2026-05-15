<div align="center">

# Document Intelligence Platform

### Legal document ingestion → structured extraction → grounded draft generation → operator-driven improvement

<br/>

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Celery](https://img.shields.io/badge/Celery-5.4-37814a?style=flat-square&logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-dc382d?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ed?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose)

</div>

---

## What this system does

1. **Document Processing** — accepts scanned PDFs, low-resolution images, and noisy legal documents; applies an OCR preprocessing pipeline (deskew, denoise, CLAHE contrast enhancement) and routes to the best engine (Tesseract, PaddleOCR, or TrOCR for handwriting) based on per-page confidence.

2. **Structured Extraction** — classifies the document type, then runs a schema-driven extractor: regex pre-pass for high-signal fields, Gemini fill for gaps, with every extracted value linked back to a source text snippet.

3. **Defined-Term Resolution** — scans extracted text for `"Term" means …` patterns, builds a canonical term → definition map, annotates chunks with labels (e.g. `Company [Acme Corp]`) before BGE embedding so retrieval stays semantically accurate, and injects a `DEFINED TERMS` block into every draft prompt so the model uses consistent terminology throughout.

4. **Jurisdiction-Aware Retrieval** — tags every chunk with a jurisdiction label (e.g. `federal`, `state:NY`, `federal:EDNY`) detected from the text; at query time, chunks whose jurisdiction conflicts with the query's detected jurisdiction are excluded from the candidate set, while untagged and matching chunks are always included.

5. **Intra-Document Conflict Detection** — deterministically detects four classes of contradiction across chunks: conflicting governing-law clauses, duplicate defined-term definitions with different meanings, inconsistent dates for the same label, and mismatched monetary amounts. Conflicts are cached in the export payload after embedding, surfaced via a dedicated REST endpoint, shown as a severity-labelled warning banner in the UI, and injected into draft prompts so the model acknowledges discrepancies rather than asserting either value as authoritative.

6. **Query Expansion** — before BGE embedding, the retrieval service asks Gemini Flash Lite for 6–10 synonymous legal terms for each section query (e.g. "wrongful account access" → "unauthorized disclosure of financial records, improper access to nonpublic information …"), concatenates them with the original query, and caches the result per document. Falls back silently to the original query if Gemini is unavailable.

7. **Grounded Draft Generation** — retrieves relevant passages from the document using expanded BGE queries + pgvector and generates a structured legal-style draft (internal memo, case fact summary, affidavit summary, etc.) with mandatory `[Page N]` inline citations and `[UNSUPPORTED: reason]` markers for anything not present in the source.

8. **Improvement Loop** — operator edits to generated drafts are captured, distilled into reusable preference rules by Gemini, stored per tenant/document-type, and automatically injected into all future drafts. Preference effectiveness is tracked and decays when rules stop helping.

---

## Architecture

```
PDF / Image
     │
     ▼
┌──────────────────────────────────────────┐
│ OCR Pipeline                             │
│  PyMuPDF → 3× zoom render               │
│  → preprocess: deskew · denoise · CLAHE  │
│  → engine routing:                       │
│     Tesseract (default)                  │
│     PaddleOCR (paddle mode)              │
│     TrOCR — microsoft/trocr-base-        │
│             handwritten (auto fallback   │
│             when avg confidence < 0.70)  │
└─────────────────┬────────────────────────┘
                  │ raw text + word confidences
                  ▼
┌──────────────────────────────────────────┐
│ Classification                           │
│  TF-IDF · keyword signals · IDF weights  │
│  → document_type (legal_complaint /      │
│    contract / affidavit / legal_notice / │
│    case_brief / unknown …)               │
└─────────────────┬────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────┐
│ Schema-Driven Extraction                 │
│  Regex pre-pass (patterns from YAML)     │
│  → Gemini fill for missing fields        │
│  → ExtractionOutput: typed fields +      │
│    source snippets + entity list         │
│  → Defined-Term Resolution:              │
│    "Term" means … → canonical map        │
└─────────────────┬────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────┐
│ Chunking + Embedding (async)             │
│  Overlapping text chunks                 │
│  → Defined-term annotation on chunks     │
│    (Company → Company [Acme Corp])       │
│  → Jurisdiction tagging per chunk        │
│    (federal · state:NY · federal:EDNY …) │
│  → BGE embeddings → pgvector IVFFlat     │
│  → Conflict detection cached in          │
│    export_payload (governing_law /       │
│    defined_term / date / amount)         │
└─────────────────┬────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────┐
│ Draft Generation                         │
│  Per-section query expansion (Gemini     │
│  Flash Lite → synonymous legal terms)    │
│  → Jurisdiction-filtered BGE retrieval   │
│    (one query per template section)      │
│  → Gemini with strict grounding rules:   │
│    · DEFINED TERMS block injected        │
│    · KNOWN CONFLICTS block injected      │
│    · cite every fact as [Page N]         │
│    · write [UNSUPPORTED: reason] for     │
│      anything not in the source          │
│  → structured sections + evidence_ids   │
│  → grounding_score per section           │
└─────────────────┬────────────────────────┘
                  │ operator reviews, edits sections
                  ▼
┌──────────────────────────────────────────┐
│ Preference Learning                      │
│  DraftEdit captured per section          │
│  → Celery task → Gemini extracts rule    │
│  → DraftPreference (tenant-scoped)       │
│  → injected into future system prompts   │
│  → effectiveness scored + decayed        │
└──────────────────────────────────────────┘
```

For a detailed breakdown see [`docs/architecture.md`](docs/architecture.md).

---

## Quick start

### Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| [Docker Desktop](https://docs.docker.com/get-docker/) | 24+ | Full stack |
| `GEMINI_API_KEY` | — | Gemini 2.5 Flash (extraction + drafts + preference learning) |

### Run

```bash
git clone <repo-url>
cd improved
cp .env.example .env
# Set GEMINI_API_KEY=<your-key> in .env
# Get a key at https://aistudio.google.com/app/apikey
docker compose up --build -d
docker compose exec api alembic upgrade head
```

The API is available at `http://localhost:8000`. Swagger UI at `http://localhost:8000/docs`.

### Upload a document and generate a draft

```bash
# Upload (returns document ID immediately; processing runs async)
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -F "file=@sample_docs/sample_legal_complaint.txt"

# Poll until status = completed
curl http://localhost:8000/api/v1/documents/{id}/status

# View structured extraction result
curl http://localhost:8000/api/v1/documents/{id}/result

# View intra-document conflicts detected during embedding
curl http://localhost:8000/api/v1/documents/{id}/conflicts

# Generate an internal memo draft
curl -X POST http://localhost:8000/api/v1/documents/{id}/drafts \
  -H "Content-Type: application/json" \
  -d '{"draft_type": "internal_memo"}'

# Retrieve draft with evidence chunk IDs
curl http://localhost:8000/api/v1/documents/{id}/drafts/{draft_id}

# Inspect the source chunks that grounded the draft
curl http://localhost:8000/api/v1/documents/{id}/drafts/{draft_id}/evidence
```

---

## Sample inputs and outputs

The `sample_docs/` directory contains synthetic legal documents and the expected outputs they produce.

### Input — synthetic federal court complaint

[`sample_docs/sample_legal_complaint.txt`](sample_docs/sample_legal_complaint.txt) — a synthetic federal civil complaint suitable for upload as a plain-text or PDF document.

### Output — structured extraction

[`sample_docs/sample_extraction_output.json`](sample_docs/sample_extraction_output.json) — the `ExtractionOutput` the pipeline produces for the above complaint: typed fields (case number, court, plaintiffs, defendants, claims, statutes, relief sought), entity list, and per-field source snippets linking each value back to the original text.

### Output — defined terms

[`sample_docs/sample_defined_terms.json`](sample_docs/sample_defined_terms.json) — the canonical defined-term map extracted from a contract document, showing how `"Confidential Information"`, `"Company"`, and other terms are resolved to their authoritative definitions and how they appear annotated in the chunk text.

### Output — conflict report

[`sample_docs/sample_conflicts.json`](sample_docs/sample_conflicts.json) — an example `ConflictReport` for a contract with three detected contradictions: a governing-law conflict (New York vs. Delaware), a defined-term redefinition (Confidential Information), and a monthly-fee mismatch ($5,000 vs. $5,500).

### Output — grounded draft

[`sample_docs/sample_draft_output.json`](sample_docs/sample_draft_output.json) — a complete `internal_memo` draft for the same document, showing:
- `[Page N]` inline citations on every factual claim
- `[UNSUPPORTED: reason]` where information was absent from the source
- `confidence` rating per section (`high` / `medium` / `low` / `unsupported`)
- `evidence_chunk_ids` linking each section to the specific pgvector chunks used

### Output — operator edit and learned preference

[`sample_docs/sample_improvement_loop.json`](sample_docs/sample_improvement_loop.json) — shows the full improvement loop: the original draft section, the operator's edit, the preference rule Gemini extracted from the diff, and how that rule appears in the next draft's system prompt.

---

## Draft types

| Draft type | Best for |
|---|---|
| `internal_memo` | Legal complaints, contracts, any document needing a structured senior-partner memo |
| `case_fact_summary` | Condensed factual chronology for case review |
| `notice_summary` | Legal notices, demand letters |
| `affidavit_summary` | Affidavits and sworn statements |
| `case_brief` | Case briefs and appellate filings |
| `legal_notice` | Formal legal notice documents |
| `affidavit` | Full affidavit analysis |
| `document_checklist` | Completeness audit against required fields |

---

## Defined-term resolution

Legal documents define their own vocabulary. A clause that says `"Company" means Acme Corp` in Section 1 but then refers to "the Company's obligations" in Section 12 presents a retrieval problem: the embedding of "Company" and the embedding of "Acme Corp" are different vectors.

The defined-term resolver (`app/extraction/defined_terms.py`) runs after schema extraction and before chunking:

1. **Extraction** — scans the full document text for `"Term" means …`, `"Term" shall mean …`, `"Term" refers to …`, and `"Term" is defined as …` patterns (up to 180 characters). Optionally confirmed by Gemini when a definition is ambiguous.
2. **Annotation** — before BGE embedding, every chunk text is annotated in-place: occurrences of each defined term become `Term [Canonical Definition]`, e.g. `the Company [Acme Corp] shall deliver…`. This ensures the embedding captures both the shorthand and the actual entity.
3. **Prompt injection** — every draft generation call receives a `DEFINED TERMS` block at the top of the user prompt:

```
DEFINED TERMS (extracted from this document):
Use these canonical definitions consistently throughout the draft.
  · "Confidential Information" → any non-public information disclosed by either party
  · "Company" → Acme Corp
  · "Effective Date" → January 1, 2024
```

The term map is stored in `export_payload["defined_terms"]` alongside the conflict list, so it is available without re-parsing for every subsequent draft or API call.

---

## Jurisdiction-aware retrieval

Jurisdiction tags (`app/rag/jurisdiction.py`) are assigned to every chunk during the embedding step by scanning the chunk text for jurisdiction signals:

| Signal | Tag assigned |
|---|---|
| "United States District Court", "federal law", "28 U.S.C." | `federal` |
| "Eastern District of Wisconsin", "E.D. Wis." | `federal:EDWI` |
| "State of New York", "N.Y. C.P.L.R.", "New York law" | `state:NY` |
| "California Civil Code", "State of California" | `state:CA` |
| _(no jurisdiction signal)_ | `None` (untagged) |

At retrieval time, the service detects a jurisdiction in the incoming query (same patterns). Chunks are then filtered:

- **Matching jurisdiction** — always included
- **No tag** — always included (neutral chunks are useful everywhere)
- **Conflicting jurisdiction** — excluded from the candidate set

This prevents a query about New York law from returning chunks that discuss Delaware-specific statutes. Jurisdiction tags are stored on the `DocumentChunk` model with a composite index on `(document_id, jurisdiction)` for efficient filtering.

---

## Intra-document conflict detection

The conflict detector (`app/rag/conflict_detector.py`) runs automatically at the end of the embedding step, scanning all chunks deterministically. Four conflict types are detected:

| Type | Severity | What it catches |
|---|---|---|
| `governing_law` | high | Two or more distinct jurisdiction names appear in "governed by" / "construed under" phrases |
| `defined_term` | medium | The same quoted term is given different definitions in different sections |
| `date` | medium | A date label (Effective Date, Execution Date, …) appears with two different dates |
| `amount` | high | A monetary label (monthly fee, purchase price, …) appears with two different dollar figures |

Conflicts are:

- **Cached** in `export_payload["conflicts"]` immediately after embedding — no re-computation on subsequent reads
- **Served** via `GET /documents/{id}/conflicts` → `ConflictReport` (JSON with `conflict_count`, `has_high_severity`, per-item chunk indices)
- **Displayed** in the UI as a severity-labelled banner above the extraction grid (high = red, medium = yellow)
- **Injected** into every draft prompt as a `KNOWN CONFLICTS` block, instructing the model to acknowledge the discrepancy rather than asserting either fact

```
KNOWN CONFLICTS IN THIS DOCUMENT:
  [HIGH] GOVERNING_LAW: Conflicting governing law references: "new york", "delaware".
  [HIGH] AMOUNT: Inconsistent "monthly fee": $5000 vs. $5500.
  [MEDIUM] DEFINED_TERM: "confidential information" has conflicting definitions across sections.
```

Documents embedded before conflict detection was deployed are computed on-the-fly from stored chunks when the endpoint is called.

---

## Grounding and unsupported-claim control

The system enforces source-grounded generation at the prompt level:

```
STRICT GROUNDING RULES:
1. Every factual claim must be traceable to a specific source chunk.
   Use inline citations in the format [Page N] or [Page N - Section Title].
2. If information needed for a section is not present in the source material,
   write "[UNSUPPORTED: {reason}]" rather than inferring or hallucinating.
3. Do not draw on general legal knowledge to fill gaps.
   Only use what the documents contain.
```

Each draft section additionally carries a `confidence` field (`high` / `medium` / `low` / `unsupported`). Sections backed by multiple high-similarity chunks score `high`; sections where the model had to acknowledge gaps score `unsupported`.

### Grounding score

The platform also computes a deterministic `grounding_score` for every draft section. The scorer splits section prose into sentences, ignores structural fragments shorter than eight words, and counts a qualifying sentence as grounded only when it includes `[Page N]`, `[Page N - Section Title]`, `[Chunk N]`, or `[structured_fields]`. Any sentence containing `[UNSUPPORTED` counts as ungrounded.

The section score is:

```
grounded qualifying sentences / total qualifying sentences
```

Each `DraftOutput` row stores `overall_grounding_score`, a word-count-weighted mean across its sections. In the Barker v. Landmark walkthrough, the generated internal memo scores 100% on heavily cited sections such as Factual Background and Relief Sought, while sections containing explicit `[UNSUPPORTED]` gaps score lower. The UI shows both the per-section grounding bar and the overall draft percentage (`Draft v2 · 1044 words · 91% grounded · reviewed`).

Retrieved chunks are passed to the model as labeled source blocks:

```
[Chunk 3 | Page 7 | JURISDICTION AND VENUE] <chunk-uuid>
This Court has jurisdiction over this action pursuant to 28 U.S.C. § 1331 ...
```

The model can only cite chunk IDs it was given. Cited IDs are validated post-generation; invalid IDs are dropped from `evidence_chunk_ids`.

---

## Improvement loop

```
operator edits a draft section
          │
          ▼
PATCH /documents/{id}/drafts/{draft_id}/sections
          │  DraftEdit stored (original, edited, section_key, reviewer)
          ▼
Celery: extract_preference_from_edit
          │  Gemini reads the diff → ONE reusable rule (general, not doc-specific)
          │  Rule embedded (BGE) + deduplicated against existing preferences
          ▼
DraftPreference stored (tenant + document_type scoped)
          │
          ▼
Next draft for same (tenant, document_type):
  system prompt includes:
    LEARNED PREFERENCES FROM PRIOR OPERATOR EDITS:
    - When specifying governing law, include a clause to disregard
      conflict of laws principles.
  plus top-2 (original → corrected) few-shot excerpt pairs
```

**Effectiveness scoring** — after each reviewed draft, every applied preference receives a score update proportional to edit coverage:

- No sections edited → `+0.10` (draft accepted as-is)
- Some sections edited → `−0.05 × (edited_sections / total_sections)`

This means editing one section of a six-section memo penalises each preference by `−0.008`, not the flat `−0.05`. Preferences that consistently produce accepted drafts rise; those requiring repeated correction decay toward zero.

---

## Document types supported

New document types require only YAML — no Python code changes:

| Type | Extraction schema | Draft template |
|---|---|---|
| `legal_complaint` | `extraction/schemas/legal_complaint.yaml` | `internal_memo`, `case_fact_summary` |
| `contract` | `extraction/schemas/contract.yaml` | `internal_memo` |
| `affidavit` | `extraction/schemas/affidavit.yaml` | `affidavit`, `affidavit_summary` |
| `legal_notice` | `extraction/schemas/legal_notice.yaml` | `legal_notice`, `notice_summary` |
| `case_brief` | `extraction/schemas/case_brief.yaml` | `case_brief` |
| `unknown` | `extraction/schemas/unknown.yaml` | all templates (best-effort) |

---

## OCR pipeline

| Input condition | Handling |
|---|---|
| Clean PDF | PyMuPDF text extraction (no OCR needed) |
| Scanned / low-res | 3× zoom render → deskew → denoise → CLAHE → Tesseract |
| Noisy / skewed | `fastNlMeansDenoising` + `cv2.minAreaRect` deskew |
| Handwritten / degraded | Auto-routed to `microsoft/trocr-base-handwritten` when avg word confidence < 0.70 |
| cv2 unavailable | Pillow fallback: autocontrast + sharpen |

OCR preprocessing is configurable:

```env
OCR_ENGINE=auto                        # tesseract | paddle | trocr | auto
OCR_ENGINE_PRIMARY=tesseract
OCR_PREPROCESS=true
OCR_DESKEW=true
OCR_DENOISE=true
OCR_ENHANCE_CONTRAST=true
HANDWRITING_CONFIDENCE_THRESHOLD=0.70
PDF_RENDER_ZOOM=3.0
```

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | **required** | Gemini API key (extraction + drafts + preference learning) |
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis broker |
| `OCR_ENGINE` | `tesseract` | OCR engine (`tesseract` \| `paddle` \| `trocr`) |
| `OCR_PREPROCESS` | `true` | Enable image preprocessing pipeline |
| `HANDWRITING_CONFIDENCE_THRESHOLD` | `0.70` | Auto-route to TrOCR below this confidence |
| `PDF_RENDER_ZOOM` | `3.0` | DPI multiplier for PDF page rendering |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Sentence embedding model for retrieval |
| `RETRIEVAL_TOP_K` | `8` | Chunks retrieved per draft section |
| `QUERY_EXPANSION_ENABLED` | `true` | Expand legal retrieval queries with Gemini synonyms before BGE embedding |
| `QUERY_EXPANSION_MODEL` | `gemini-2.0-flash-lite` | Low-cost model used only for query expansion |
| `QUERY_EXPANSION_MAX_TOKENS` | `256` | Max output tokens for query expansion calls |
| `DRAFT_MODEL` | `gemini-2.5-flash` | Gemini model for draft generation |
| `DRAFT_MAX_CHUNKS` | `10` | Max retrieved chunks per draft |
| `PREFERENCE_MAX_PER_DRAFT` | `5` | Max learned preferences injected per draft |
| `PREFERENCE_DEDUP_THRESHOLD` | `0.85` | Cosine similarity threshold for deduplication |

Full configuration: `.env.example`.

---

## Benchmarks

The retrieval evaluator is LegalBench-RAG-compatible: it accepts the official `data/corpus` + `data/benchmarks` layout, chunks text with the production `SectionAwareChunker`, embeds with the production BGE wrapper, and reports Recall@K, Precision@K, and MRR. See [`evaluation/README.md`](evaluation/README.md).

The committed result file [`evaluation/results/retrieval_results.json`](evaluation/results/retrieval_results.json) is a **smoke-fixture run only** — 3 queries, 9 chunks, `embedding_backend: sentence_transformers`. Perfect scores on a 9-chunk corpus are trivially achievable by any retriever and carry little validity as a benchmark claim. The `expanded` run is identical to baseline because `GEMINI_API_KEY` was absent at run time. Full benchmark runs require `sentence-transformers` installed and intentionally fail without it.

| Dataset | Backend | Variant | Queries | Recall@5 | Recall@10 | MRR |
|---|---|---|---:|---:|---:|---:|
| Smoke fixture ⚠ | BGE (real) | baseline | 3 | 1.00 | 1.00 | 1.00 |
| LegalBench-RAG mini | BGE (real) | baseline | 25 | **0.72** | **0.84** | **0.43** |

> **LegalBench-RAG mini** — 25 queries sampled across ContractNLI, CUAD, MAUD, and PrivacyQA. Corpus scoped to referenced documents only (`--corpus-scope referenced`); chunk settings 600 chars / 120 overlap; `embedding_backend: sentence_transformers`. Full result: [`evaluation/results/legalbench_rag_mini_results.json`](evaluation/results/legalbench_rag_mini_results.json).

To reproduce:

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data\legalbenchrag --limit 25 --output evaluation\results\legalbench_rag_mini_results.json
```

For a full 500-query run with query expansion (requires `GEMINI_API_KEY`):

```powershell
.\.venv\Scripts\python evaluation\retrieval\run_legalbench_rag.py --data-root data\legalbenchrag --limit 500 --corpus-scope all --with-expansion --output evaluation\results\legalbench_rag_mini_results.json
```

---

## Running tests

```bash
pytest -v --tb=short
```

Tests cover API routes, OCR pipeline, schema extraction, retrieval, draft generation, conflict detection, and the preference learning loop.

---

## Project structure

```
app/
  api/v1/routes/
    conflicts.py        — GET /documents/{id}/conflicts
    documents.py        — upload, status, result
    drafts.py           — generate, edit, evidence
    analytics.py        — usage analytics
    reviews.py          — operator review submission
    health.py           — liveness / readiness probes
  classification/       — TF-IDF + keyword document classifier
  core/                 — config, logging, metrics
  db/                   — SQLAlchemy models (Document, DocumentChunk,
  |                       DraftOutput, DraftEdit, DraftPreference …)
  extraction/
    schemas/            — per-document-type YAML extraction schemas
    defined_terms.py    — "Term" means … extractor + chunk annotator
    schema_extractor.py — regex pre-pass + Gemini fill
  ocr/
    preprocessing.py    — deskew · denoise · CLAHE · binarize
    paddle_ocr.py       — PaddleOCR provider
    tesseract_ocr.py    — Tesseract provider
    trocr_ocr.py        — TrOCR handwriting provider
    auto_ocr.py         — confidence-based auto-routing
    factory.py          — engine selection
  rag/
    conflict_detector.py  — deterministic 4-type intra-document conflict detection
    draft_service.py      — retrieval + draft generation + edit capture
    draft_templates/      — per-draft-type YAML section plans
    embedding_service.py  — chunk + embed + tag + detect conflicts
    grounding_scorer.py   — deterministic sentence-level grounding metric
    jurisdiction.py       — chunk jurisdiction tagging + retrieval filter
    preference_service.py — preference extraction + scoring
    retrieval_service.py  — BGE + query expansion + pgvector search
    gemini_client.py      — Gemini wrapper with JSON parse + retry
  schemas/
    conflict.py         — ConflictItemRead, ConflictReport Pydantic models
    document.py         — document upload / status schemas
    draft.py            — draft request / response schemas
  utils/
    pdf.py              — PyMuPDF rendering
alembic/                — database migration scripts
evaluation/
  retrieval/
    corpus_loader.py    — selective corpus loading (--corpus-scope referenced)
    run_legalbench_rag.py — LegalBench-RAG evaluator
  results/
    legalbench_rag_mini_results.json — 25-query real benchmark results
docs/
  architecture.md       — detailed architecture notes
sample_docs/            — synthetic sample documents and example outputs
  sample_legal_complaint.txt
  sample_extraction_output.json
  sample_defined_terms.json
  sample_conflicts.json
  sample_draft_output.json
  sample_improvement_loop.json
tests/
  test_conflict_detector.py
  test_legalbench_eval.py
  … (API routes, OCR, schema extraction, retrieval, drafts, preferences)
```

---

## Design decisions

**Why regex pre-pass before LLM extraction?**
Deterministic regex is fast, free, and fully auditable. The LLM fill only runs for fields the regex couldn't find — this keeps costs low and avoids hallucination on fields that regex handles reliably (case numbers, dates, statute citations).

**Why annotate chunks with defined-term labels before embedding?**
"Company" and "Acme Corp" have different vector representations. Without annotation, a query mentioning "Acme Corp" may not retrieve chunks that only use the shorthand "the Company". Injecting `Company [Acme Corp]` into the chunk text before embedding bridges this gap deterministically, without requiring a separate lookup at query time.

**Why per-section retrieval queries?**
A single query for "internal memo" returns chunks biased toward whatever words appear most in the document. Running one targeted query per section (e.g. "jurisdiction venue 28 U.S.C. 1331" for the jurisdiction section) produces much more relevant context for each part of the draft.

**Why query expansion before retrieval?**
Legal passages often use different words for the same concept: a template query may say "wrongful account access" while the source says "unauthorized disclosure of financial records." When enabled, the retrieval service asks Gemini Flash Lite for 6-10 synonymous legal terms, concatenates them with the original query, caches the result, and embeds that expanded text with BGE. If Gemini is unavailable, retrieval silently uses the original query.

**Why jurisdiction-filter at retrieval time rather than tagging the query?**
Tagging the query and doing a hard filter would silently drop chunks when the detector misclassifies a query's jurisdiction. The soft approach — include untagged chunks, exclude only confirmed conflicts — means retrieval degrades gracefully: worst case the model sees a slightly less targeted context window, not an empty one.

**Why detect conflicts deterministically rather than with LLM?**
An LLM-based conflict detector would require sending the full document to Gemini on every embedding run. The deterministic regex approach runs in milliseconds, produces structured output (chunk indices, severity, field), has no API cost, and is fully unit-testable. The tradeoff is that it only catches the four most common legal contradictions; semantic inconsistencies beyond those patterns are not detected.

**Why scale preference penalties by edit coverage?**
With a flat binary penalty, editing one typo in a six-section memo would penalise a good jurisdiction preference as much as a fully rewritten draft. Section-proportional scoring lets good rules survive minor editorial corrections.

**Why TrOCR only as a fallback?**
TrOCR loads a 350MB transformer model and processes one line crop at a time — it is 10–30× slower than Tesseract. Routing to it only on low-confidence pages keeps the happy path fast while still handling genuinely degraded or handwritten input.

---

## Known limitations

These are honest gaps in the current implementation — things that work by design but would need more work before production use at scale.

**OCR on real messy documents** — The preprocessing pipeline (deskew, denoise, CLAHE) and TrOCR fallback are implemented and tested in isolation, but the `sample_docs/` directory contains clean synthetic text files rather than genuinely scanned PDFs. The pipeline has never been validated end-to-end on a real degraded scan. PaddleOCR and TrOCR are also behind optional extras (`pip install ".[paddle]"` / `".[ml]"`) because they require large model downloads that are impractical in CI.

**LegalBench-RAG benchmark scope** — The committed benchmark result covers 25 queries (`--limit 25`, `--corpus-scope referenced`). The full 500-query mini run and full-corpus run have not been completed because they require the 79M-character dataset and several hours of embedding time. The 25-query numbers (Recall@5 = 0.72, Recall@10 = 0.84, MRR = 0.43) are real BGE results on real legal text, but a larger sample would give more confidence in the figures.

**Query expansion requires Gemini quota** — `QUERY_EXPANSION_ENABLED=true` by default, but the comparison between baseline and expanded retrieval in the evaluator is only meaningful when `GEMINI_API_KEY` is set. Without it, both runs are identical. The code falls back silently so nothing breaks, but the Recall@10 uplift (+8–12% per Stanford RegLab) cannot be demonstrated without a live key.

**Conflict detection is intra-document only** — The current detector finds contradictions within a single document's chunks. Cross-document conflict detection (e.g., a contract and its amendment disagreeing on a payment term) is architecturally straightforward to add but not yet implemented.

**Preference learning quality is Gemini-dependent** — The improvement loop captures edits and distills rules correctly, but the quality of the extracted preference text depends on Gemini 2.5 Flash's ability to generalise from a diff to a reusable rule. Bad distillation produces vague rules that don't help. There is no human review step before a preference is persisted.

**No authentication layer** — API key auth (`API_KEYS` env var) and tenant isolation are wired through every route, but there is no user login, JWT, or OAuth flow. This is a single-tenant dev setup, not a multi-tenant SaaS deployment.

**Table extraction returns empty** — `extracted.tables` is always `[]` in the current pipeline. The field exists in the schema and export payload but the extractor never populates it. Structured table data (e.g., payment schedules, asset lists) would improve retrieval quality for contract-heavy documents.

---

## Future work

The six priorities implemented for this assessment (grounding score, LegalBench-RAG evaluator, defined-term resolution, query expansion, jurisdiction tagging, conflict detection) are the foundation. The next meaningful improvements are listed below in rough priority order.

### Retrieval and grounding

**Legal-domain embedding fine-tuning** — BGE `bge-base-en-v1.5` is a strong general-purpose model but was not trained on legal text. Fine-tuning on a contrastive dataset of (legal query, relevant passage) pairs from LegalBench-RAG or CUAD would likely push Recall@10 from 0.84 toward 0.92+. LoRA fine-tuning on `bge-base-en-v1.5` requires ~16GB VRAM and roughly 2 hours on a single A100.

**Cross-document retrieval for case files** — The current retrieval layer operates per-document. A realistic legal workflow involves a folder of related documents (complaint + exhibits + prior orders). Adding a `case_id` grouping on `documents` and a `retrieve_across_case()` method in `RetrievalService` would allow drafts to draw evidence from any file in the case, not just the uploaded document.

**Hybrid BM25 + dense retrieval** — Pure dense retrieval struggles with rare legal terms (statute numbers, case citations, defined proper nouns) that appear verbatim in the query but get diluted in the embedding space. Adding a BM25 sparse pass (via `rank-bm25` or pgvector's upcoming sparse support) and reciprocal-rank fusion with the dense results would improve precision on citation-heavy queries.

**Full LegalBench-RAG 500-query benchmark** — Run `--limit 500 --corpus-scope all --with-expansion` once Gemini quota is available, commit the result to `evaluation/results/legalbench_rag_mini_results.json`, and update the README table. This is the standard comparison point for legal RAG systems in the literature.

### OCR and document processing

**Table extraction** — `pdfplumber` already ships with `page.extract_tables()`. Populating `extracted.tables` and storing structured table rows in a `document_tables` relation would unlock retrieval over financial schedules, asset inventories, and timeline grids that are currently invisible to the RAG layer.

**Multi-page form stitching** — Legal forms often spread a single logical record (e.g., a party's affidavit) across many pages with repeated headers. A post-OCR stitching pass that collapses page-boundary duplicates before chunking would improve chunk coherence and reduce redundant evidence.

**Confidence-based ensemble** — For each page, run both Tesseract and PaddleOCR, compare word-level confidence scores, and take the higher-confidence output field-by-field. This is more expensive but produces measurably better results on degraded scans with mixed print and handwriting.

### Draft generation and improvement loop

**Streaming draft output** — The current API returns the full draft JSON in one response after Gemini finishes generating. Adding a Server-Sent Events endpoint (`GET /documents/{id}/drafts/{draft_id}/stream`) would let the UI render sections as they complete, which matters for long documents where generation takes 15–30 seconds.

**Draft export to Word / PDF** — Operators ultimately need to file or send drafts. A `GET /drafts/{id}/export?format=docx` endpoint using `python-docx` would turn the structured JSON sections into a formatted Word document with proper headings, page numbers, and citation footnotes.

**Human review before preference is persisted** — Currently a preference rule is extracted from every edit and written to the database automatically. Adding a `status: pending_review` field and a `POST /preferences/{id}/approve` endpoint would let a senior operator verify that the distilled rule is sensible before it influences production drafts.

**A/B testing draft templates** — The YAML template system already supports multiple section plans per draft type. Adding a flag on `DraftOutput` to record which template variant was used, and tracking edit rates per variant, would let the system identify which template structure produces the fewest operator corrections.

**Preference clustering and merging** — As the rule store grows, semantically redundant preferences accumulate (e.g., "cite page numbers" and "always include a [Page N] reference" are the same rule). A periodic background job that clusters embeddings with DBSCAN and merges near-duplicate rules would keep the preference store lean and improve injection quality.

### Analytics and observability

**Grounding score trend dashboard** — `overall_grounding_score` is stored per draft. Plotting this over time per document type and per tenant would show whether the improvement loop is actually raising the average grounding score across the platform — the clearest signal that preference learning is working.

**Edit heatmap** — Track which sections of which draft types are edited most often. A section that is edited on 80% of generated memos is a signal that the template or the retrieval query for that section is wrong, not that operators have unusual preferences.

**Token cost tracking** — Log Gemini input/output tokens per generation, expansion, and preference extraction call. Sum by tenant and document type. Legal documents vary enormously in length; knowing which document types cost 10× more than average makes it possible to set per-tenant budgets.

### Infrastructure

**Cross-document conflict detection** — Extend `conflict_detector.py` to accept a list of `(document_id, chunk_texts)` tuples and flag contradictions across documents in the same case (e.g., two contract versions with different governing law clauses). The detection logic is identical; only the data model changes.

**Legal citation resolution** — Parse statute citations (`28 U.S.C. § 1331`), case citations (`Ashcroft v. Iqbal, 556 U.S. 662`), and regulation references from extracted text and resolve them against an external API (CourtListener, OpenStates) to confirm they exist and retrieve the canonical text. Broken citations are a common source of hallucinated grounding.

**Redaction / PII anonymization** — Before storing OCR text, run a NER pass to detect personal names, SSNs, financial account numbers, and addresses. Offer a `redact=true` upload flag that replaces detected PII with `[REDACTED]` in the stored text while preserving a reversible mapping in an encrypted column. Required for HIPAA/GDPR compliance in a production deployment.

**Role-based access control** — Add a `users` table, JWT authentication on all endpoints, and a simple RBAC model (admin / reviewer / read-only). Multi-tenant isolation already exists at the data layer; adding auth at the API layer makes the system deployable as a real SaaS product.
