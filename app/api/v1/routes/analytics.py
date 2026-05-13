"""Analytics, active-learning corrections, and per-tenant metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.core.cache import get_cache
from app.db.models import (
    AuditLog,
    CorrectionRecord,
    Document,
    ExtractionResult,
    ReviewStatus,
    ReviewTask,
)
from app.services.correction_service import CorrectionService

router = APIRouter(dependencies=[Depends(require_api_key)])

_CACHE_TTL = 30


@router.get("/metrics/overview")
def overview_metrics(
    tenant_id: str | None = Depends(get_optional_tenant),
    db: Session = Depends(db_dependency),
) -> dict:
    cache_key = f"analytics:overview:{tenant_id or 'all'}"
    cache = get_cache()

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    base_filter = [Document.deleted_at.is_(None)]
    if tenant_id:
        base_filter.append(Document.tenant_id == tenant_id)

    # Aggregate counts grouped by status — avoids loading every row into Python.
    status_rows = db.execute(
        select(Document.status, func.count(Document.id))
        .where(*base_filter)
        .group_by(Document.status)
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}
    total_documents = sum(by_status.values())

    # Aggregate counts grouped by document type.
    type_rows = db.execute(
        select(Document.document_type, func.count(Document.id))
        .where(*base_filter, Document.document_type.isnot(None))
        .group_by(Document.document_type)
    ).all()
    by_type = {row[0]: row[1] for row in type_rows}

    # Average confidence in a single aggregate query.
    avg_conf = db.scalar(
        select(func.avg(Document.document_confidence)).where(
            *base_filter, Document.document_confidence.isnot(None)
        )
    )

    pending_review = (
        db.scalar(
            select(func.count(ReviewTask.id))
            .join(Document, Document.id == ReviewTask.document_id)
            .where(ReviewTask.status == ReviewStatus.pending, *base_filter)
        )
        or 0
    )

    corrections_filter = [Document.deleted_at.is_(None)]
    if tenant_id:
        corrections_filter.append(Document.tenant_id == tenant_id)
    total_corrections = (
        db.scalar(
            select(func.count(CorrectionRecord.id))
            .join(Document, Document.id == CorrectionRecord.document_id)
            .where(*corrections_filter)
        )
        or 0
    )

    result = {
        "tenant_id": tenant_id or "all",
        "total_documents": total_documents,
        "by_status": by_status,
        "by_document_type": by_type,
        "avg_document_confidence": round(avg_conf, 4) if avg_conf is not None else None,
        "pending_review_tasks": pending_review,
        "total_corrections": total_corrections,
    }
    cache.set(cache_key, result, ttl=_CACHE_TTL)
    return result


@router.get("/metrics/ocr-distribution")
def ocr_distribution(
    tenant_id: str | None = Depends(get_optional_tenant),
    db: Session = Depends(db_dependency),
) -> dict:
    cache_key = f"analytics:ocr-dist:{tenant_id or 'all'}"
    cache = get_cache()

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    stmt = (
        select(ExtractionResult.ocr_metadata)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(Document.deleted_at.is_(None))
    )
    if tenant_id:
        stmt = stmt.where(Document.tenant_id == tenant_id)

    buckets = {"<0.5": 0, "0.5-0.7": 0, "0.7-0.85": 0, "0.85-0.95": 0, ">0.95": 0}
    for meta in db.scalars(stmt):
        conf = meta.get("average_confidence", 0.0) if meta else 0.0
        if conf < 0.5:
            buckets["<0.5"] += 1
        elif conf < 0.7:
            buckets["0.5-0.7"] += 1
        elif conf < 0.85:
            buckets["0.7-0.85"] += 1
        elif conf < 0.95:
            buckets["0.85-0.95"] += 1
        else:
            buckets[">0.95"] += 1

    result = {"tenant_id": tenant_id or "all", "buckets": buckets}
    cache.set(cache_key, result, ttl=_CACHE_TTL)
    return result


@router.get("/corrections")
def list_corrections(
    tenant_id: str | None = Depends(get_optional_tenant),
    document_type: str | None = Query(default=None),
    field_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(db_dependency),
) -> list[dict]:
    svc = CorrectionService(db)
    return svc.export_corrections(
        tenant_id=tenant_id,
        document_type=document_type,
        field_name=field_name,
    )[:limit]


@router.get("/corrections/stats")
def correction_stats(
    tenant_id: str | None = Depends(get_optional_tenant),
    db: Session = Depends(db_dependency),
) -> dict:
    return CorrectionService(db).correction_stats(tenant_id=tenant_id)


@router.get("/audit/tenant")
def tenant_audit(
    tenant_id: str | None = Depends(get_optional_tenant),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(db_dependency),
) -> list[dict]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if tenant_id is None:
        stmt = stmt.where(AuditLog.tenant_id.is_(None))
    else:
        stmt = stmt.where(AuditLog.tenant_id == tenant_id)
    logs = list(db.scalars(stmt))
    return [
        {
            "id": log.id,
            "event_type": log.event_type,
            "actor": log.actor,
            "payload": log.payload,
            "correlation_id": log.correlation_id,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
