"""Duplicate detection and fraud check endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.services.deduplication_service import DeduplicationService
from app.services.document_service import DocumentService

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/{document_id}/check")
def check_document(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> dict:
    """Run all duplicate and fraud checks on a document.

    Returns a risk report with:
    - risk_score (0.0–1.0)
    - risk_level (clean / low / medium / high)
    - findings list with type, severity, and detail per issue
    """
    doc = DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    svc = DeduplicationService(db)
    report = svc.check(doc)
    return report


@router.get("/{document_id}/report")
def get_dedup_report(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> dict:
    """Alias for the check endpoint — useful for polling after processing."""
    return check_document(document_id, db, tenant_id=tenant_id)
