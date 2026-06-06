"""Load LegalBench-RAG-style corpora and build an in-memory retrieval index."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.rag.chunker import SectionAwareChunker
from app.rag.embedder import Embedder


@dataclass(frozen=True, slots=True)
class GoldSnippet:
    corpus_path: str | None
    start: int | None
    end: int | None
    text: str


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    query: str
    snippets: list[GoldSnippet]


@dataclass(frozen=True, slots=True)
class CorpusChunk:
    chunk_id: str
    corpus_path: str
    chunk_index: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    corpus_path: str
    text: str
    score: float


class InMemoryVectorIndex:
    def __init__(self, chunks: list[CorpusChunk], embeddings: list[list[float]]) -> None:
        self.chunks = chunks
        self.embeddings = embeddings

    def retrieve(self, query_vector: list[float], *, top_k: int) -> list[RetrievedChunk]:
        ranked = sorted(
            zip(self.chunks, self.embeddings, strict=True),
            key=lambda item: _cosine(query_vector, item[1]),
            reverse=True,
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                corpus_path=chunk.corpus_path,
                text=chunk.text,
                score=_cosine(query_vector, embedding),
            )
            for chunk, embedding in ranked[:top_k]
        ]


def load_corpus(corpus_dir: Path, include_paths: set[str] | None = None) -> dict[str, str]:
    corpus: dict[str, str] = {}
    if include_paths is not None:
        for rel in sorted(include_paths):
            path = corpus_dir / rel
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".txt", ".md", ".text"}:
                continue
            corpus[rel.replace("\\", "/")] = path.read_text(encoding="utf-8", errors="replace")
        return corpus

    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".md", ".text"}:
            continue
        rel = path.relative_to(corpus_dir).as_posix()
        corpus[rel] = path.read_text(encoding="utf-8", errors="replace")
    return corpus


def load_benchmark(path: Path) -> list[BenchmarkCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = _case_records(raw)
    cases: list[BenchmarkCase] = []
    for idx, record in enumerate(records):
        query = str(record.get("query") or record.get("question") or "").strip()
        if not query:
            continue
        snippets = [_snippet_from_raw(item) for item in _snippet_records(record)]
        snippets = [snippet for snippet in snippets if snippet.text or snippet.corpus_path]
        if not snippets:
            continue
        case_id = str(record.get("id") or record.get("case_id") or f"{path.stem}-{idx + 1}")
        cases.append(BenchmarkCase(case_id=case_id, query=query, snippets=snippets))
    return cases


def build_chunks(
    corpus: dict[str, str], *, chunker: SectionAwareChunker | None = None
) -> list[CorpusChunk]:
    chunker = chunker or SectionAwareChunker()
    output: list[CorpusChunk] = []
    for corpus_path, text in corpus.items():
        for chunk in chunker.chunk(text):
            output.append(
                CorpusChunk(
                    chunk_id=f"{corpus_path}::{chunk.chunk_index}",
                    corpus_path=corpus_path,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                )
            )
    return output


def build_index(
    chunks: list[CorpusChunk], *, embedder: Embedder | None = None
) -> InMemoryVectorIndex:
    embedder = embedder or Embedder()
    embeddings = embedder.encode_passages([chunk.text for chunk in chunks])
    return InMemoryVectorIndex(chunks, embeddings)


def relevant_chunk_ids(case: BenchmarkCase, chunks: list[CorpusChunk]) -> list[str]:
    ids: list[str] = []
    for snippet in case.snippets:
        for chunk in chunks:
            if _snippet_matches_chunk(snippet, chunk) and chunk.chunk_id not in ids:
                ids.append(chunk.chunk_id)
    return ids


def _case_records(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("tests", "test_cases", "cases", "queries", "data", "benchmark"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "query" in raw or "question" in raw:
            return [raw]
    return []


def _snippet_records(record: dict[str, Any]) -> list[Any]:
    for key in ("ground_truth", "ground_truths", "snippets", "gold_snippets", "answers"):
        value = record.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    return []


def _snippet_from_raw(raw: Any) -> GoldSnippet:
    if isinstance(raw, str):
        return GoldSnippet(corpus_path=None, start=None, end=None, text=raw)
    if not isinstance(raw, dict):
        return GoldSnippet(corpus_path=None, start=None, end=None, text="")
    corpus_path = raw.get("file_path") or raw.get("filepath") or raw.get("path")
    corpus_path = corpus_path or raw.get("corpus_path") or raw.get("document_path")
    span = raw.get("span")
    if isinstance(span, list | tuple) and len(span) == 2:
        start, end = span
    else:
        start = _first_present(raw, "start", "start_index", "char_start")
        end = _first_present(raw, "end", "end_index", "char_end")
    text = raw.get("text") or raw.get("snippet") or raw.get("answer") or ""
    return GoldSnippet(
        corpus_path=str(corpus_path).replace("\\", "/") if corpus_path else None,
        start=int(start) if isinstance(start, int | float) else None,
        end=int(end) if isinstance(end, int | float) else None,
        text=str(text),
    )


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _snippet_matches_chunk(snippet: GoldSnippet, chunk: CorpusChunk) -> bool:
    if snippet.corpus_path and not chunk.corpus_path.endswith(snippet.corpus_path):
        return False
    if snippet.start is not None and snippet.end is not None:
        return chunk.char_start <= snippet.end and chunk.char_end >= snippet.start
    if snippet.text:
        normalized_snippet = _normalize(snippet.text)
        normalized_chunk = _normalize(chunk.text)
        return normalized_snippet in normalized_chunk or normalized_chunk in normalized_snippet
    return False


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
