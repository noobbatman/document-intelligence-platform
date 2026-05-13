"""Embedding generation and persistence for document RAG."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk


class EmbeddingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def embed_document(self, document_id: str, tenant_id: str | None = None) -> int:
        """Embed processed OCR text and return the number of chunks stored."""
        if not self.settings.rag_enabled:
            return 0

        document = self.db.get(Document, document_id)
        if not document:
            raise ValueError(f"Document {document_id} not found.")

        self.db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()

        ocr_text = document.extraction_result.ocr_text if document.extraction_result else ""
        chunks = self.chunk_text(ocr_text)
        if not chunks:
            self.db.commit()
            return 0

        embeddings = self._embed(chunks)
        effective_tenant_id = tenant_id if tenant_id is not None else document.tenant_id
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            self.db.add(
                DocumentChunk(
                    document_id=document.id,
                    tenant_id=effective_tenant_id,
                    chunk_index=index,
                    chunk_text=chunk,
                    embedding=embedding,
                )
            )
        self.db.commit()
        return len(chunks)

    def chunk_text(self, text: str) -> list[str]:
        text = " ".join((text or "").split())
        if not text:
            return []

        chunk_size = max(1, self.settings.rag_chunk_size)
        overlap = min(max(0, self.settings.rag_chunk_overlap), chunk_size - 1)
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            if end == len(text):
                break
            start = end - overlap
        return chunks

    def _embed(self, chunks: list[str]) -> list[list[float]]:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        embedder = GoogleGenerativeAIEmbeddings(model=self.settings.embedding_model)
        return embedder.embed_documents(chunks)
