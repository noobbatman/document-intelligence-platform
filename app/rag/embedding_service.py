"""Chunk, embed, and store document text."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.models import DocumentChunk as ChunkModel
from app.extraction.defined_terms import annotate_defined_terms
from app.rag.chunker import SectionAwareChunker
from app.rag.embedder import get_embedder
from app.rag.jurisdiction import detect_chunk_jurisdiction


class EmbeddingService:
    def __init__(self) -> None:
        self.chunker = SectionAwareChunker()
        self.embedder = get_embedder()

    def embed_document(self, document_id: str, session: Session) -> int:
        document = session.get(Document, document_id)
        if not document or not document.extraction_result:
            return 0

        ocr_text = document.extraction_result.ocr_text or ""
        defined_terms = (document.extraction_result.export_payload or {}).get("defined_terms", {})
        chunks = self.chunker.chunk(ocr_text)
        passage_texts = [annotate_defined_terms(chunk.text, defined_terms) for chunk in chunks]
        vectors = self.embedder.encode_passages(passage_texts) if chunks else []

        session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
        for chunk, annotated_text, vector in zip(chunks, passage_texts, vectors, strict=True):
            session.add(
                ChunkModel(
                    document_id=document_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section_header=chunk.section_header,
                    jurisdiction=detect_chunk_jurisdiction(chunk.text),
                    text=annotated_text,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding=vector,
                )
            )
        session.commit()
        return len(chunks)

    def delete_embeddings(self, document_id: str, session: Session) -> None:
        session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
        session.commit()
