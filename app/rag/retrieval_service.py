"""Semantic retrieval over document chunks."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk
from app.rag.embedder import get_embedder
from app.rag.gemini_client import GeminiClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    page_number: int
    section_header: str | None
    jurisdiction: str | None
    text: str
    similarity_score: float


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = get_embedder()
        self.gemini = GeminiClient()
        self._query_expansion_cache: dict[str, str] = {}

    def retrieve(
        self,
        document_id: str,
        query: str,
        *,
        top_k: int | None = None,
        min_score: float | None = None,
        session: Session,
    ) -> list[RetrievedChunk]:
        expanded_query = self._expand_query(query)
        query_vec = self.embedder.encode_query(expanded_query)
        top_k = top_k or self.settings.retrieval_top_k
        min_score = self.settings.retrieval_min_score if min_score is None else min_score
        jurisdiction_tags = self._document_jurisdiction_tags(document_id, session)

        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            return self._retrieve_postgres(
                document_id, query_vec, top_k, min_score, jurisdiction_tags, session
            )
        return self._retrieve_python(
            document_id, query_vec, top_k, min_score, jurisdiction_tags, session
        )

    def retrieve_multi_document(
        self,
        document_ids: list[str],
        query: str,
        *,
        top_k_per_doc: int = 4,
        session: Session,
    ) -> list[RetrievedChunk]:
        results: list[RetrievedChunk] = []
        for document_id in document_ids:
            results.extend(
                self.retrieve(
                    document_id,
                    query,
                    top_k=top_k_per_doc,
                    min_score=self.settings.retrieval_min_score,
                    session=session,
                )
            )
        return sorted(results, key=lambda item: item.similarity_score, reverse=True)

    def _retrieve_postgres(
        self,
        document_id: str,
        query_vec: list[float],
        top_k: int,
        min_score: float,
        jurisdiction_tags: list[str],
        session: Session,
    ) -> list[RetrievedChunk]:
        query_vec_literal = "[" + ",".join(str(round(v, 8)) for v in query_vec) + "]"
        jurisdiction_csv = ",".join(jurisdiction_tags)
        rows = session.execute(
            text(
                """
                WITH scored AS (
                    SELECT id, document_id, chunk_index, page_number, section_header,
                           jurisdiction, text,
                           1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                    FROM document_chunks
                    WHERE document_id = :document_id
                      AND (
                        :jurisdiction_filter = false
                        OR jurisdiction IS NULL
                        OR jurisdiction = ANY(string_to_array(:jurisdiction_tags, ','))
                      )
                )
                SELECT id, document_id, chunk_index, page_number, section_header,
                       jurisdiction, text, similarity
                FROM scored
                WHERE similarity >= :min_score
                ORDER BY similarity DESC
                LIMIT :top_k
                """
            ),
            {
                "query_vec": query_vec_literal,
                "document_id": document_id,
                "min_score": min_score,
                "top_k": top_k,
                "jurisdiction_filter": bool(jurisdiction_tags),
                "jurisdiction_tags": jurisdiction_csv,
            },
        ).mappings()
        return [
            RetrievedChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                page_number=row["page_number"],
                section_header=row["section_header"],
                jurisdiction=row["jurisdiction"],
                text=row["text"],
                similarity_score=round(float(row["similarity"]), 4),
            )
            for row in rows
        ]

    def _retrieve_python(
        self,
        document_id: str,
        query_vec: list[float],
        top_k: int,
        min_score: float,
        jurisdiction_tags: list[str],
        session: Session,
    ) -> list[RetrievedChunk]:
        allowed = set(jurisdiction_tags)
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )
        scored = [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                section_header=chunk.section_header,
                jurisdiction=chunk.jurisdiction,
                text=chunk.text,
                similarity_score=round(_cosine(query_vec, chunk.embedding), 4),
            )
            for chunk in chunks
            if chunk.embedding is not None
            and (not allowed or chunk.jurisdiction is None or chunk.jurisdiction in allowed)
        ]
        return [
            item
            for item in sorted(scored, key=lambda c: c.similarity_score, reverse=True)
            if item.similarity_score >= min_score
        ][:top_k]

    def _expand_query(self, query: str) -> str:
        query = query.strip()
        if not query:
            return query
        if not self.settings.query_expansion_enabled or not self.settings.gemini_api_key:
            return query
        cached = self._query_expansion_cache.get(query)
        if cached is not None:
            return cached
        try:
            payload = self.gemini.generate_json(
                system_prompt=(
                    "You are a legal search assistant. Expand retrieval queries with "
                    "6-10 synonymous legal terms and phrases that may appear in relevant "
                    "legal document passages. Return JSON only as "
                    '{"expanded_query": "..."}; do not add explanations.'
                ),
                user_prompt=f"Original query: {query}",
                model_id=self.settings.query_expansion_model,
                max_output_tokens=self.settings.query_expansion_max_tokens,
                temperature=0,
            )
            expansion = str(payload.get("expanded_query") or "").strip()
            expanded_query = _join_query_expansion(query, expansion)
        except Exception as exc:
            logger.warning(
                "query_expansion_failed",
                extra={"query": query, "error": str(exc)},
            )
            expanded_query = query
        self._query_expansion_cache[query] = expanded_query
        return expanded_query

    def _document_jurisdiction_tags(self, document_id: str, session: Session) -> list[str]:
        document = session.get(Document, document_id)
        if not document or not document.extraction_result:
            return []
        payload = document.extraction_result.export_payload or {}
        tags = payload.get("jurisdiction_tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return [str(tag) for tag in tags if tag]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)


def _join_query_expansion(query: str, expansion: str) -> str:
    if not expansion:
        return query
    if expansion.lower() == query.lower():
        return query
    if expansion.lower().startswith(query.lower()):
        return expansion
    return f"{query} {expansion}"
