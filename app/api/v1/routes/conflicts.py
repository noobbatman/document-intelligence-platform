"""Conflict detection endpoint — returns intra-document contradictions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.db.models import Document, DocumentStatus
from app.rag.conflict_detector import detect_conflicts
from app.schemas.conflict import ConflictItemRead, ConflictReport

router = APIRouter(dependencies=[Depends(require_api_key)])
DB_DEP = Depends(db_dependency)
TENANT_DEP = Depends(get_optional_tenant)


@router.get(
    "/documents/{document_id}/conflicts",
    response_model=ConflictReport,
    summary="Get intra-document conflicts",
    description=(
        "Returns governing-law, defined-term, date, and monetary-amount contradictions "
        "detected within the document. Results are cached in the export payload after the "
        "embedding step; documents processed before Priority 6 are computed on-the-fly."
    ),
)
def get_document_conflicts(
    document_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> ConflictReport:
    document = db.get(Document, document_id)
    if not document or (tenant_id and document.tenant_id != tenant_id):
        raise HTTPException(status_code=404, detail="Document not found.")
    if document.status not in (DocumentStatus.completed, DocumentStatus.review_required):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Document is {document.status}; "
                "conflicts are available after processing completes."
            ),
        )

    export_payload = (
        document.extraction_result.export_payload if document.extraction_result else {}
    ) or {}

    cached = export_payload.get("conflicts")
    if cached is not None:
        items = [ConflictItemRead(**item) for item in cached]
    else:
        # On-the-fly for documents embedded before Priority 6 was deployed
        chunk_texts = [c.text for c in sorted(document.chunks, key=lambda c: c.chunk_index)]
        defined_terms = export_payload.get("defined_terms", {})
        raw = detect_conflicts(chunk_texts, defined_terms=defined_terms)
        items = [ConflictItemRead(**c.to_dict()) for c in raw]

    return ConflictReport(
        document_id=document_id,
        conflicts=items,
        conflict_count=len(items),
        has_high_severity=any(item.severity == "high" for item in items),
        checked_at=datetime.now(UTC),
    )
