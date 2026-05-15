# Document Intelligence Platform Architecture

## 1. System Overview

This repository implements a production-style document intelligence platform with these stages:

1. Ingestion
2. Storage
3. Background orchestration
4. OCR
5. Document classification
6. Extraction
7. Confidence scoring
8. Human review
9. JSON export
10. Evaluation and auditability

The current system focuses on legal document intelligence: classification, schema-driven extraction, retrieval-grounded drafting, and a learning loop from operator edits. New document types are added primarily through YAML classifier entries, extraction schemas, and draft templates.

## 2. Recommended Open-Source Choices

### OCR

- Recommended primary OCR engine: `PaddleOCR`
- Why:
  - Better robustness on scanned and messy layouts than Tesseract in many practical document AI workloads.
  - Supports text detection + recognition instead of only OCR over a pre-segmented page.
  - Better fit for multi-column, noisy, skewed, and heterogeneous forms.
- Tradeoff:
  - Heavier deployment footprint.
  - More moving parts for local CPU deployment.

### OCR in this MVP implementation

- Implemented provider abstraction: `PaddleOCRProvider` and `TesseractOCRProvider`
- Default runtime in `.env.example`: `tesseract`
- Reason:
  - Tesseract is easier to package in Docker for a first local portfolio deployment.
  - The code is already structured so switching to PaddleOCR is a config change when the environment is ready.

### Extraction stack

- `spaCy` for lightweight entity extraction, normalization hooks, and future training pipeline
- `transformers` reserved for richer token classification or document classification upgrades
- Regex pre-pass for high-confidence legal anchors such as case numbers, dates, statutes, parties, and clause headings
- Gemini schema-fill fallback for fields that are hard to capture reliably with OCR-era regex alone

This hybrid design is deliberate. Pure foundation-model extraction looks flashy, but production document pipelines usually need deterministic rules for regulated fields, auditability, and stable confidence signals.

## 3. Component Responsibilities

### API Layer

- Handles uploads, retrieval, draft generation, edit capture, preferences, and history queries
- Validates payloads
- Enqueues long-running processing to Celery

### Service Layer

- Coordinates storage, database writes, audit logging, and workflow transitions
- Keeps business logic out of route handlers

### Pipeline Layer

- Runs OCR, classification, extraction, post-processing, and confidence scoring
- Produces structured outputs and review candidates

### OCR Module

- Converts PDFs to page images
- Produces page text, OCR tokens, and source snippets

### Classification Module

- Labels document type
- Returns classifier confidence and rationale metadata

### Extraction Module

- Uses schema-driven extraction with YAML field definitions
- Produces fields, tables, and supporting snippets

### Confidence Module

- Computes field-level and document-level confidence
- Flags low-confidence items for human review

### Review Workflow

- Stores review tasks for low-confidence fields
- Accepts reviewer corrections
- Rebuilds final export after corrections

### Draft Improvement Workflow

- Stores generated draft sections and evidence chunks
- Captures operator edits section by section
- Extracts reusable drafting preferences from edits
- Applies tenant- and document-type-scoped preferences to future drafts

### Persistence Layer

- PostgreSQL for documents, extraction results, review tasks, review decisions, and audit logs
- Local file storage for uploaded source files and exported JSON

### Evaluation Layer

- Benchmarks classifier accuracy
- Measures field precision/recall/F1
- Tracks low-confidence and correction rates

## 4. Folder Structure

```text
app/
  api/
    v1/routes/
  classification/
  core/
  db/
  extraction/
  ocr/
  pipelines/
  schemas/
  services/
  storage/
  utils/
  workers/
data/
  uploads/
  exports/
docs/
scripts/
tests/
```

## 5. MVP Scope

### Implemented document types

- `contract`
- `legal_complaint`
- `legal_notice`
- `case_brief`
- `affidavit`
- `unknown`

### Implemented MVP capabilities

- Upload single file
- Upload batches of files
- Queue background processing
- OCR scanned PDFs and images
- Legal document classification
- Schema-driven field extraction
- Generic entity extraction
- Confidence scoring
- Draft generation with evidence
- Draft edit capture and preference learning
- Final structured JSON export
- Processing history and audit logs
- Legal RAG and improvement-loop evaluation scripts

## 6. API Design

### Document APIs

- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/upload/batch`
- `GET /api/v1/documents`
- `GET /api/v1/documents/search`
- `GET /api/v1/documents/{document_id}`
- `DELETE /api/v1/documents/{document_id}`
- `GET /api/v1/documents/{document_id}/result`
- `GET /api/v1/documents/{document_id}/history`
- `POST /api/v1/documents/{document_id}/reprocess`
- `GET /api/v1/documents/{document_id}/export`

### Draft / RAG APIs

- `POST /api/v1/documents/{id}/drafts`
- `GET /api/v1/documents/{id}/drafts`
- `GET /api/v1/documents/{id}/drafts/{draft_id}`
- `PUT /api/v1/documents/{id}/drafts/{draft_id}`
- `GET /api/v1/documents/{id}/drafts/{draft_id}/evidence`

### Preference APIs

- `GET /api/v1/preferences`
- `DELETE /api/v1/preferences/{id}`

### Review APIs

- `GET /api/v1/reviews/pending`
- `POST /api/v1/reviews/{task_id}/decision`

### Analytics APIs

- `GET /api/v1/analytics/metrics/overview`
- `GET /api/v1/analytics/metrics/ocr-distribution`
- `GET /api/v1/analytics/corrections/stats`
- `GET /api/v1/analytics/draft-improvement`

### Platform APIs

- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`
- `GET /metrics`

## 7. Confidence Strategy

Field confidence combines:

- OCR confidence for the source tokens
- extraction-method confidence
- schema plausibility checks
- document-type consistency
- review status override

Document confidence combines:

- mean field confidence
- required-field coverage
- classifier confidence
- OCR quality estimate

Low-confidence fields are routed to review when confidence is below the configurable threshold.

## 8. Legal RAG and Drafting Layer

The platform now adds a legal RAG layer on top of the existing extraction
pipeline:

1. OCR text is chunked with section-aware boundaries.
2. Chunks are embedded locally with `BAAI/bge-base-en-v1.5`.
3. Short-form defined terms are annotated before embedding so references like
   `LCU` still retrieve chunks about `Landmark Credit Union`.
4. pgvector stores embeddings in `document_chunks`.
5. Draft-specific retrieval queries gather evidence chunks.
6. Query expansion can use Gemini Flash Lite to add legal synonyms before BGE
   embedding. The expanded query is cached per retrieval service instance and
   falls back to the original query on missing keys, rate limits, or parsing
   failures.
7. Chunks are jurisdiction-tagged from citation patterns such as `28 U.S.C.`,
   `E.D. Wis.`, and `Wis. Stat.`. Retrieval applies a soft jurisdiction filter:
   chunks matching the document's normalized jurisdiction tags are included,
   untagged chunks are included, and chunks with conflicting known jurisdictions
   are excluded.
8. Gemini 2.5 Flash generates structured draft sections with inline page
   citations and evidence chunk IDs.

Jurisdiction detection is intentionally lightweight: it uses citation pattern
matching rather than a full legal citation parser. It reliably identifies common
federal, federal-district, and state-level signals in ordinary litigation and
contract text. Multi-jurisdictional documents may carry multiple document tags;
retrieval includes chunks matching any of those tags plus unknown chunks.

Drafts are stored in `draft_outputs`, with statuses for `generating`, `draft`,
`reviewed`, and `approved`.

## 9. Improvement Loop

The improvement loop is:

```text
operator edits draft section
  -> DraftEdit row is stored
  -> extract_preferences_task runs asynchronously
  -> Gemini extracts one reusable preference
  -> preference is embedded and deduplicated
  -> future drafts for the same tenant/document type inject that preference
```

This is a real feedback loop rather than a version diff: future generations use
the learned preference text and few-shot edit examples, and every draft records
which preference IDs were applied.

