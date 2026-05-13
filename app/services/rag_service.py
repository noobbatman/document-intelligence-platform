"""Retrieval-augmented question answering over processed document chunks."""

from __future__ import annotations

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk
from app.schemas.rag import RAGQueryResponse, RAGSource

FALLBACK_ANSWER = "I could not find that information in the processed documents."


class RAGService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def query(
        self,
        question: str,
        *,
        tenant_id: str | None = None,
        document_ids: list[str] | None = None,
    ) -> RAGQueryResponse:
        query_vector = self._embed_query(question)
        filters = [DocumentChunk.tenant_id == tenant_id, DocumentChunk.embedding.is_not(None)]
        if document_ids:
            filters.append(DocumentChunk.document_id.in_(document_ids))

        distance = DocumentChunk.embedding.cosine_distance(query_vector)
        rows = (
            self.db.query(DocumentChunk, Document.filename, distance.label("distance"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .filter(*filters)
            .order_by(distance)
            .limit(self.settings.rag_top_k)
            .all()
        )

        sources = [
            RAGSource(
                document_id=chunk.document_id,
                filename=filename,
                chunk_text=chunk.chunk_text,
                similarity_score=max(0.0, 1.0 - float(distance_value or 0.0)),
            )
            for chunk, filename, distance_value in rows
        ]
        documents_searched = (
            self.db.query(func.count(distinct(DocumentChunk.document_id))).filter(*filters).scalar()
            or 0
        )

        if not sources:
            return RAGQueryResponse(
                answer=FALLBACK_ANSWER,
                sources=[],
                documents_searched=documents_searched,
            )

        answer = self._answer(question, sources)
        return RAGQueryResponse(
            answer=answer,
            sources=sources,
            documents_searched=documents_searched,
        )

    def _embed_query(self, question: str) -> list[float]:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        embedder = GoogleGenerativeAIEmbeddings(model=self.settings.embedding_model)
        return embedder.embed_query(question)

    def _answer(self, question: str, sources: list[RAGSource]) -> str:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_google_genai import ChatGoogleGenerativeAI

        context = "\n\n".join(
            f"Source {index} ({source.filename}):\n{source.chunk_text}"
            for index, source in enumerate(sources, start=1)
        )
        prompt = ChatPromptTemplate.from_template(
            "You are a document analyst. Answer the user's question using ONLY the provided "
            "document excerpts.\n"
            f'If the answer is not in the excerpts, say "{FALLBACK_ANSWER}"\n\n'
            "DOCUMENT EXCERPTS:\n{context}\n\n"
            "QUESTION: {question}\n\n"
            "ANSWER:"
        )
        model = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)
        response = (prompt | model).invoke({"context": context, "question": question})
        content = getattr(response, "content", response)
        return str(content).strip()
