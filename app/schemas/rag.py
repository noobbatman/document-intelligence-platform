"""Schemas for retrieval-augmented document question answering."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RAGQueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    document_ids: list[str] | None = None


class DocumentAskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


class RAGSource(BaseModel):
    document_id: str
    filename: str
    chunk_text: str
    similarity_score: float


class RAGQueryResponse(BaseModel):
    answer: str
    sources: list[RAGSource]
    documents_searched: int
