# Architecture — Document Intelligence Platform

## 1. Problem and scope

The platform ingests legal-style documents (federal complaints, contracts, affidavits, legal notices, case briefs) in PDF or image form, extracts structured fields, retrieves relevant passages for drafting tasks, generates grounded legal-style drafts, and improves over time from operator edits.

"Grounded" is a hard constraint, not a best-effort goal: the generation layer is forbidden from inferring or hallucinating — every factual claim must cite a source page, and anything absent from the document must be written as `[UNSUPPORTED: reason]`.

---

## 2. Pipeline stages

### Stage 1 — OCR

**Entry point:** `app/ocr/factory.py` → `app/ocr/auto_ocr.py`

PDFs are rendered to images by PyMuPDF at 3× zoom (≈216 DPI) before OCR. This significantly improves recognition quality on low-resolution scans compared to default DPI.

Preprocessing (`app/ocr/preprocessing.py`) runs before every OCR call when `OCR_PREPROCESS=true`:

1. **Deskew** — `cv2.minAreaRect` on dark pixel coordinates detects rotation angle; `cv2.warpAffine` corrects it. Skips correction when tilt < 0.5°.
2. **Denoise** — `cv2.fastNlMeansDenoising` removes salt-and-pepper noise common in scans without over-blurring thin strokes.
3. **CLAHE** — Contrast Limited Adaptive Histogram Equalization improves faded or uneven text without blowing out already-clear regions.
4. **Adaptive threshold** (optional) — `cv2.adaptiveThreshold` for hard binarization; off by default for PaddleOCR, on for Tesseract.

Engine selection:

| Mode | Behaviour |
|---|---|
| `tesseract` | Tesseract only |
| `paddle` | PaddleOCR only |
| `trocr` | TrOCR only |
| `auto` | Tesseract (primary), re-run with TrOCR if avg word confidence < threshold |

TrOCR (`microsoft/trocr-base-handwritten`) uses PaddleOCR in detection-only mode to find line bounding boxes, then runs each crop through the HuggingFace VisionEncoderDecoder. The model is loaded lazily and cached per worker process.

If `cv2` is unavailable the pipeline falls back to Pillow autocontrast + sharpen.

**Output:** `OCRResult` — full text (pages joined by `\f`), per-word confidence scores, page count.

---

### Stage 2 — Classification

**Entry point:** `app/classification/`

TF-IDF scoring over the document's OCR text, weighted by IDF computed from the full keyword vocabulary. Each document type registers keyword lists and regex patterns in `document_types.yaml` — adding a new type requires only a YAML entry.

A `_strong_legal_complaint_signal()` override fires before scoring when specific statutory patterns (28 U.S.C. § 1331, RFPA) are detected, preventing misclassification to `contract` caused by shared vocabulary (parties, jurisdiction, consideration).

**Output:** `document_type` string (e.g. `legal_complaint`, `contract`, `unknown`).

---

### Stage 3 — Schema-driven extraction

**Entry point:** `app/extraction/schema_extractor.py`

Each document type has a YAML extraction schema at `app/extraction/schemas/{type}.yaml` defining:
- field name, type (`string` / `list` / `number` / `boolean`), required flag
- regex patterns (ordered; first match wins unless `all_matches: true`)
- description (passed to LLM for fill)

Processing:
1. **Regex pre-pass** — all patterns for all fields run against the full OCR text. `_clean_value()` strips OCR noise and field-specific artifacts.
2. **Gemini fill** — any fields still missing after regex are sent to Gemini in one call. Prompt includes field descriptions, current values, and a 24,000-character window of the document text. Failures are logged as warnings but do not break the pipeline.
3. **Snippet extraction** — for each extracted value, `find_snippet()` locates the nearest surrounding sentence in the OCR text to provide a source reference.

**Output:** `ExtractionOutput` — `document_type`, typed `fields`, `entities`, `tables`, `metadata` (includes `field_snippets`, `required_fields`, `extraction_mode`).

---

### Stage 4 — Chunking and embedding

**Entry point:** `app/rag/` (async Celery task after extraction completes)

OCR text is split into overlapping chunks (default 800 chars, 100-char overlap). Each chunk is embedded using BGE (`BAAI/bge-small-en-v1.5` via SentenceTransformers) and stored in the `document_chunks` pgvector table with an IVFFlat cosine index.

Chunks store: `chunk_index`, `page_number`, `section_header` (detected by heading heuristics), `text`, `embedding`.

---

### Stage 5 — Draft generation

**Entry point:** `app/rag/draft_service.py`

#### Retrieval

For each draft, `_retrieve_for_draft()` runs:

1. **Per-section queries** — the draft template YAML defines a `query` hint per section (e.g. `"jurisdiction venue 28 U.S.C. 1331 1343 1391"` for the jurisdiction section). One BGE+pgvector cosine search runs per section query, collecting up to `draft_max_chunks × 5` candidates.
2. **Keyword fallback** — `support_terms` from the template YAML are matched literally against chunk text (`ILIKE`) to ensure critical terms are always represented even when cosine similarity misses them.
3. **Diversity selection** — `_select_diverse_chunks()` limits to 2 chunks per 20-chunk window of the document, preventing retrieval from clustering on one section of a long document.

#### Generation

The system prompt contains hard grounding rules (see below). The user prompt contains:
- document type and structured fields
- per-section instructions from the template YAML
- all retrieved chunks as labeled source blocks: `[Chunk N | Page N | Section Header] <chunk-uuid>`

The model must cite chunk UUIDs it was given. Post-generation, `_evidence_ids()` validates cited IDs against the retrieved set and drops any that were not provided.

**Grounding rules (verbatim from system prompt):**
```
1. Every factual claim must be traceable to a specific source chunk.
   Use inline citations in the format [Page N] or [Page N - Section Title].
2. If information needed for a section is not present in the source material,
   write "[UNSUPPORTED: {reason}]" rather than inferring or hallucinating.
3. Do not draw on general legal knowledge to fill gaps.
   Only use what the documents contain.
```

**Output:** `DraftOutput` — `content` (sections with `key`, `title`, `content`, `evidence_chunk_ids`, `confidence`), `evidence_chunk_ids` (union across sections), `word_count`, `preferences_applied`.

---

### Stage 6 — Preference learning

**Entry point:** `app/rag/preference_service.py`

When an operator submits edited draft sections via `PATCH /documents/{id}/drafts/{draft_id}/sections`:

1. A `DraftEdit` row is stored for each changed section (original content, edited content, section key, reviewer name).
2. A Celery task (`extract_preference_from_edit`) fires for each edit.
3. Gemini reads the diff and extracts ONE reusable preference rule, general enough to apply to future documents of the same type. Document-specific edits (e.g. fixing a case number) are discarded.
4. The rule is embedded (BGE) and cosine-deduplicated against existing preferences (threshold: 0.92). Near-duplicate rules update the existing record rather than creating a new one.
5. On each subsequent draft for the same `(tenant_id, document_type)`, the top preferences (ranked by `application_count × 0.6 + confidence × 0.4`) are injected into the system prompt, along with few-shot (original → edited) excerpt pairs from the source edits.

**Effectiveness scoring:**

After each reviewed draft:
- No sections edited → `+0.10` per applied preference
- Some sections edited → `−0.05 × (edited_sections / total_sections)` per applied preference

Proportional scaling ensures a single minor edit doesn't heavily penalise preferences that were irrelevant to the change.

---

## 3. Data model

| Table | Purpose |
|---|---|
| `documents` | Uploaded file metadata, processing status, document_type |
| `extraction_results` | `ExtractionOutput` JSON, schema version |
| `document_chunks` | Overlapping text chunks with BGE embeddings (pgvector) |
| `draft_outputs` | Generated drafts, section content, evidence_chunk_ids, preferences_applied |
| `draft_edits` | Per-section operator edits (original + edited) |
| `draft_preferences` | Extracted reusable rules with embedding, confidence, effectiveness_score |
| `review_tasks` | Low-confidence fields flagged for human review |
| `audit_logs` | Immutable event log for all pipeline transitions |

---

## 4. Config-driven extensibility

Adding a new document type requires only:

1. `app/classification/document_types.yaml` — add keywords and patterns
2. `app/extraction/schemas/{type}.yaml` — define fields, types, regex patterns, LLM fill flag
3. `app/rag/draft_templates/{template}.yaml` — (optional) define section plan, queries, support_terms

No Python code changes are needed. The classifier, extractor, and draft service all load from YAML at runtime.

---

## 5. Key design decisions

**Regex pre-pass before LLM extraction**
Deterministic patterns are fast, free, fully auditable, and produce no hallucinations. The LLM is invoked only for fields the regex could not match. This keeps token costs low and confines hallucination risk to genuinely ambiguous fields.

**Per-section retrieval rather than a single query**
A single "internal memo" query retrieves chunks biased toward the most common words in the document. Running one targeted query per section (e.g. `"prayer for relief wherefore damages"` for the relief section) produces more relevant context for each part of the draft and prevents any one topic from dominating the chunk budget.

**Cosine-proportional preference penalties**
A flat binary penalty (`-0.05` for any edit) would punish a good jurisdiction-clause preference because an unrelated typo was fixed in the case caption section. Section-proportional scoring (`-0.05 × edit_ratio`) lets stable rules survive routine editorial corrections.

**TrOCR only as a fallback**
TrOCR loads 350MB of weights and processes one line crop at a time — 10–30× slower than Tesseract on a CPU. Routing to it only when average word confidence falls below the configured threshold keeps the happy path fast while still handling degraded or handwritten input when it actually matters.

**Gemini 2.5 Flash**
Mid-tier model chosen for cost and speed in an assessment context. The grounding architecture (strict prompt rules, source chunks in context, `[UNSUPPORTED]` discipline) compensates for the model's lower ceiling. A stronger model (Gemini 1.5 Pro, GPT-4o) would improve citation accuracy and draft depth without any code changes.
