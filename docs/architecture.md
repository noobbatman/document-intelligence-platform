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

The MVP fully implements invoices and bank statements. The pipeline is intentionally modular so insurance claims, KYC forms, lease/property documents, and medical forms can be added through new classifier labels, extractor classes, and schema definitions.

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
- Regex + rule-based parsing for high-value structured fields in invoices and bank statements
- `pdfplumber` plus OCR-token row grouping for table extraction

This hybrid design is deliberate. Pure foundation-model extraction looks flashy, but production document pipelines usually need deterministic rules for regulated fields, auditability, and stable confidence signals.

## 3. Component Responsibilities

### API Layer

- Handles uploads, retrieval, review actions, export, and history queries
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

- Uses document-type-specific extractors
- Produces fields, tables, and supporting snippets

### Confidence Module

- Computes field-level and document-level confidence
- Flags low-confidence items for human review

### Review Workflow

- Stores review tasks for low-confidence fields
- Accepts reviewer corrections
- Rebuilds final export after corrections

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
review_ui/
scripts/
tests/
```

## 5. MVP Scope

### Implemented document types

- `invoice`
- `bank_statement`

### Planned extension types

- `kyc_form`
- `insurance_claim`
- `lease_document`
- `medical_form`
- `unknown`

### Implemented MVP capabilities

- Upload single file
- Upload batches of files
- Queue background processing
- OCR scanned PDFs and images
- Document classification for invoice vs bank statement
- Key-value extraction
- Basic table extraction
- Generic entity extraction
- Confidence scoring
- Human review queue and correction API
- Final structured JSON export
- Processing history and audit logs
- Evaluation scripts with synthetic data generation

## 6. API Design

### Document APIs

- `POST /api/v1/documents/upload`
- `GET /api/v1/documents`
- `GET /api/v1/documents/{document_id}`
- `GET /api/v1/documents/{document_id}/result`
- `GET /api/v1/documents/{document_id}/history`
- `POST /api/v1/documents/{document_id}/reprocess`
- `GET /api/v1/documents/{document_id}/export`

### Review APIs

- `GET /api/v1/reviews/queue`
- `GET /api/v1/reviews/{task_id}`
- `POST /api/v1/reviews/{task_id}/decision`

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
3. pgvector stores embeddings in `document_chunks`.
4. Draft-specific retrieval queries gather evidence chunks.
5. Gemini 2.5 Flash generates structured draft sections with inline page
   citations and evidence chunk IDs.

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

## 10. RAG API Surface

- `POST /api/v1/documents/{id}/drafts`
- `GET /api/v1/documents/{id}/drafts`
- `GET /api/v1/documents/{id}/drafts/{draft_id}`
- `PUT /api/v1/documents/{id}/drafts/{draft_id}`
- `GET /api/v1/documents/{id}/drafts/{draft_id}/evidence`
- `GET /api/v1/preferences`
- `DELETE /api/v1/preferences/{id}`
- `GET /api/v1/analytics/draft-improvement`
