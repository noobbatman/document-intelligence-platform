# Assumptions and Tradeoffs

## Embeddings

The RAG layer defaults to `BAAI/bge-base-en-v1.5` through `sentence-transformers`.
Embeddings stay local, which is a better default for legal documents because it
keeps privileged or sensitive source text out of an external embedding API.

## Vector Store

The system uses pgvector inside PostgreSQL instead of a separate vector
database. This keeps chunks, drafts, edits, preferences, and tenant scoping in
one transactional system. It also keeps deployment simple: Docker Compose swaps
the database image to `pgvector/pgvector:pg16`.

## Drafting Model

Draft generation and preference extraction use Gemini 2.5 Flash via
`google-genai`. The model is configurable with `DRAFT_MODEL`, but the default is
`gemini-2.5-flash` because this implementation is intended to use Gemini rather
than Claude.

## Chunking

Legal documents are chunked by detected sections first, then by a sentence-aware
sliding window when sections are too long or no sections are found. This favors
retrieval chunks that carry useful legal context, such as "Governing Law" or
"Termination", while avoiding mid-sentence cuts.

## Preference Learning

Operator edits are stored as draft edits and processed asynchronously into
reusable preferences. Async extraction keeps the review workflow responsive and
lets future drafts benefit from learned preferences without blocking the current
operator.

## Known Gaps

Noisy scans still depend on OCR quality. A production legal workflow would add
deskewing, binarization, layout detection, and page-image evidence previews.
The current UI exposes evidence chunk IDs first; a richer review surface should
expand those chunks inline with page references and source highlighting.
