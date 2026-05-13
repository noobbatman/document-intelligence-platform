"""Semantic retrieval over document chunks."""
from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DocumentChunk
from app.rag.embedder import get_embedder


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    page_number: int
    section_header: str | None
    text: str
    similarity_score: float


class RetrievalService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = get_embedder()

    def retrieve(
        self,
        document_id: str,
        query: str,
        *,
        top_k: int | None = None,
        min_score: float | None = None,
        session: Session,
    ) -> list[RetrievedChunk]:
        query_vec = self.embedder.encode_query(query)
        top_k = top_k or self.settings.retrieval_top_k
        min_score = self.settings.retrieval_min_score if min_score is None else min_score

        dialect_name = session.get_bind().dialect.name
        if dialect_name == "postgresql":
            return self._retrieve_postgres(document_id, query_vec, top_k, min_score, session)
        return self._retrieve_python(document_id, query_vec, top_k, min_score, session)

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
        session: Session,
    ) -> list[RetrievedChunk]:
        query_vec_literal = "[" + ",".join(str(round(v, 8)) for v in query_vec) + "]"
        rows = session.execute(
            text(
                """
                WITH scored AS (
                    SELECT id, document_id, chunk_index, page_number, section_header, text,
                           1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                    FROM document_chunks
                    WHERE document_id = :document_id
                )
                SELECT id, document_id, chunk_index, page_number, section_header, text, similarity
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
            },
        ).mappings()
        return [
            RetrievedChunk(
                chunk_id=row["id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                page_number=row["page_number"],
                section_header=row["section_header"],
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
        session: Session,
    ) -> list[RetrievedChunk]:
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
                text=chunk.text,
                similarity_score=round(_cosine(query_vec, chunk.embedding), 4),
            )
            for chunk in chunks
            if chunk.embedding is not None
        ]
        return [
            item
            for item in sorted(scored, key=lambda c: c.similarity_score, reverse=True)
            if item.similarity_score >= min_score
        ][:top_k]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)
