"""RAG query routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.core.config import get_settings
from app.schemas.rag import RAGQueryRequest, RAGQueryResponse
from app.services.rag_service import RAGService

router = APIRouter(dependencies=[Depends(require_api_key)])


def _ensure_rag_enabled() -> None:
    if not get_settings().rag_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG is not enabled. Set RAG_ENABLED=true.",
        )


@router.post("/query", response_model=RAGQueryResponse)
def query_documents(
    payload: RAGQueryRequest,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> RAGQueryResponse:
    _ensure_rag_enabled()
    return RAGService(db).query(
        payload.question,
        tenant_id=tenant_id,
        document_ids=payload.document_ids,
    )
