"""Chunk, embed, and store document text."""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.models import DocumentChunk as ChunkModel
from app.rag.chunker import SectionAwareChunker
from app.rag.embedder import get_embedder


class EmbeddingService:
    def __init__(self) -> None:
        self.chunker = SectionAwareChunker()
        self.embedder = get_embedder()

    def embed_document(self, document_id: str, session: Session) -> int:
        document = session.get(Document, document_id)
        if not document or not document.extraction_result:
            return 0

        ocr_text = document.extraction_result.ocr_text or ""
        chunks = self.chunker.chunk(ocr_text)
        vectors = self.embedder.encode_passages([chunk.text for chunk in chunks]) if chunks else []

        session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                ChunkModel(
                    document_id=document_id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section_header=chunk.section_header,
                    text=chunk.text,
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

