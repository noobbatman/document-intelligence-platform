"""Analytics routes used by the built-in web UI."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.db.models import (
    CorrectionRecord,
    Document,
    DraftEdit,
    DraftOutput,
    DraftPreference,
    ReviewStatus,
    ReviewTask,
)

router = APIRouter(dependencies=[Depends(require_api_key)])
DB_DEP = Depends(db_dependency)
TENANT_DEP = Depends(get_optional_tenant)


def _document_scope(stmt, tenant_id: str | None):
    stmt = stmt.where(Document.deleted_at.is_(None))
    if tenant_id is None:
        return stmt.where(Document.tenant_id.is_(None))
    return stmt.where(Document.tenant_id == tenant_id)


@router.get("/metrics/overview")
def metrics_overview(db: Session = DB_DEP, tenant_id: str | None = TENANT_DEP) -> dict:
    total_documents = db.scalar(_document_scope(select(func.count(Document.id)), tenant_id)) or 0
    avg_confidence = db.scalar(
        _document_scope(select(func.avg(Document.document_confidence)), tenant_id).where(
            Document.document_confidence.is_not(None)
        )
    )

    status_rows = db.execute(
        _document_scope(
            select(Document.status, func.count(Document.id)).group_by(Document.status),
            tenant_id,
        )
    ).all()
    type_rows = db.execute(
        _document_scope(
            select(Document.document_type, func.count(Document.id))
            .where(Document.document_type.is_not(None))
            .group_by(Document.document_type),
            tenant_id,
        )
    ).all()

    review_stmt = (
        select(func.count(ReviewTask.id))
        .join(ReviewTask.document)
        .where(ReviewTask.status == ReviewStatus.pending)
    )
    pending_review = db.scalar(_document_scope(review_stmt, tenant_id)) or 0

    corrections_stmt = select(func.count(CorrectionRecord.id)).join(Document)
    total_corrections = db.scalar(_document_scope(corrections_stmt, tenant_id)) or 0

    grounding_stmt = (
        select(func.avg(DraftOutput.overall_grounding_score))
        .join(Document)
        .where(DraftOutput.overall_grounding_score.is_not(None))
    )
    avg_grounding = db.scalar(_document_scope(grounding_stmt, tenant_id))

    return {
        "total_documents": total_documents,
        "avg_document_confidence": float(avg_confidence) if avg_confidence is not None else None,
        "avg_draft_grounding_score": float(avg_grounding) if avg_grounding is not None else None,
        "pending_review_tasks": pending_review,
        "total_corrections": total_corrections,
        "by_status": {status: count for status, count in status_rows},
        "by_document_type": {doc_type: count for doc_type, count in type_rows if doc_type},
    }


@router.get("/metrics/ocr-distribution")
def ocr_distribution(db: Session = DB_DEP, tenant_id: str | None = TENANT_DEP) -> dict:
    rows = db.execute(
        _document_scope(
            select(Document.document_confidence).where(Document.document_confidence.is_not(None)),
            tenant_id,
        )
    ).all()
    buckets = Counter({"0-20%": 0, "20-40%": 0, "40-60%": 0, "60-80%": 0, "80-100%": 0})
    for (confidence,) in rows:
        value = float(confidence or 0.0)
        if value < 0.2:
            buckets["0-20%"] += 1
        elif value < 0.4:
            buckets["20-40%"] += 1
        elif value < 0.6:
            buckets["40-60%"] += 1
        elif value < 0.8:
            buckets["60-80%"] += 1
        else:
            buckets["80-100%"] += 1
    return {"buckets": dict(buckets)}


@router.get("/corrections/stats")
def correction_stats(db: Session = DB_DEP, tenant_id: str | None = TENANT_DEP) -> dict:
    rows = db.execute(
        _document_scope(
            select(CorrectionRecord.field_name, func.count(CorrectionRecord.id))
            .join(Document)
            .group_by(CorrectionRecord.field_name),
            tenant_id,
        )
    ).all()
    return {
        "by_field": {field: count for field, count in rows},
        "total": sum(count for _, count in rows),
    }


@router.get("/draft-improvement")
def draft_improvement(db: Session = DB_DEP, tenant_id: str | None = TENANT_DEP) -> dict:
    draft_stmt = select(DraftOutput).join(Document).where(Document.deleted_at.is_(None))
    edit_stmt = select(DraftEdit).join(Document).where(Document.deleted_at.is_(None))
    pref_stmt = select(DraftPreference)
    if tenant_id is None:
        draft_stmt = draft_stmt.where(DraftOutput.tenant_id.is_(None), Document.tenant_id.is_(None))
        edit_stmt = edit_stmt.where(DraftEdit.tenant_id.is_(None), Document.tenant_id.is_(None))
        pref_stmt = pref_stmt.where(DraftPreference.tenant_id.is_(None))
    else:
        draft_stmt = draft_stmt.where(
            DraftOutput.tenant_id == tenant_id, Document.tenant_id == tenant_id
        )
        edit_stmt = edit_stmt.where(
            DraftEdit.tenant_id == tenant_id, Document.tenant_id == tenant_id
        )
        pref_stmt = pref_stmt.where(DraftPreference.tenant_id == tenant_id)

    drafts = list(db.scalars(draft_stmt))
    edits = list(db.scalars(edit_stmt))
    edited_by_day: dict[str, set[str]] = {}
    drafts_by_day: Counter[str] = Counter()
    for draft in drafts:
        drafts_by_day[draft.created_at.date().isoformat()] += 1
    for edit in edits:
        edited_by_day.setdefault(edit.created_at.date().isoformat(), set()).add(edit.draft_id)

    periods = sorted(set(drafts_by_day) | set(edited_by_day))
    top_preferences = list(
        db.scalars(
            pref_stmt.order_by(
                DraftPreference.application_count.desc(), DraftPreference.confidence.desc()
            ).limit(10)
        )
    )
    most_edited = Counter(edit.section_key for edit in edits)
    return {
        "edit_rate_over_time": [
            {
                "period": period,
                "drafts_generated": drafts_by_day.get(period, 0),
                "drafts_edited": len(edited_by_day.get(period, set())),
                "edit_rate": (
                    len(edited_by_day.get(period, set())) / drafts_by_day[period]
                    if drafts_by_day.get(period, 0)
                    else 0.0
                ),
            }
            for period in periods
        ],
        "top_preferences": [
            {
                "text": pref.preference_text,
                "application_count": pref.application_count,
                "effectiveness_score": pref.effectiveness_score,
            }
            for pref in top_preferences
        ],
        "most_edited_sections": [
            {"section_key": key, "edit_count": count} for key, count in most_edited.most_common(10)
        ],
        "preference_count": db.scalar(select(func.count()).select_from(pref_stmt.subquery())) or 0,
        "total_drafts": len(drafts),
        "total_edits": len(edits),
    }
