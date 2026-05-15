"""Chunk, embed, and store document text."""

from __future__ import annotations

import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Document
from app.db.models import DocumentChunk as ChunkModel
from app.extraction.defined_terms import annotate_defined_terms
from app.rag.chunker import SectionAwareChunker
from app.rag.conflict_detector import detect_conflicts
from app.rag.embedder import get_embedder
from app.rag.jurisdiction import detect_chunk_jurisdiction

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self) -> None:
        self.chunker = SectionAwareChunker()
        self.embedder = get_embedder()

    def embed_document(self, document_id: str, session: Session) -> int:
        document = session.get(Document, document_id)
        if not document or not document.extraction_result:
            return 0

        export_payload = document.extraction_result.export_payload or {}
        ocr_text = document.extraction_result.ocr_text or ""
        defined_terms = export_payload.get("defined_terms", {})
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

        # Detect and cache conflicts now that chunks exist
        try:
            raw_chunk_texts = [c.text for c in chunks]
            conflict_items = detect_conflicts(raw_chunk_texts, defined_terms=defined_terms)
            updated_payload = dict(export_payload)
            updated_payload["conflicts"] = [c.to_dict() for c in conflict_items]
            document.extraction_result.export_payload = updated_payload
            flag_modified(document.extraction_result, "export_payload")
            if conflict_items:
                logger.info(
                    "conflicts_detected",
                    extra={"document_id": document_id, "count": len(conflict_items)},
                )
        except Exception as exc:
            logger.warning(
                "conflict_detection_failed",
                extra={"document_id": document_id, "error": str(exc)},
            )

        session.commit()
        return len(chunks)

    def delete_embeddings(self, document_id: str, session: Session) -> None:
        session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
        session.commit()
