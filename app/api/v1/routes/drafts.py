"""Draft generation, evidence, edit capture, and learned preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.db.models import DraftEdit, DraftOutput, DraftPreference
from app.rag.draft_service import DraftService
from app.rag.preference_service import PreferenceService
from app.schemas.draft import (
    DraftCreateResponse,
    DraftEvidenceChunk,
    DraftGenerateRequest,
    DraftPreferenceRead,
    DraftRead,
    DraftUpdateRequest,
)
from app.workers.tasks import extract_preferences_task, generate_draft_task

router = APIRouter(dependencies=[Depends(require_api_key)])
DB_DEP = Depends(db_dependency)
TENANT_DEP = Depends(get_optional_tenant)


@router.post(
    "/documents/{document_id}/drafts",
    response_model=DraftCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_draft(
    document_id: str,
    payload: DraftGenerateRequest,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> DraftCreateResponse:
    draft = DraftService(db).create_placeholder(document_id, payload.draft_type, tenant_id)
    task = generate_draft_task.apply_async(
        args=[document_id, payload.draft_type, tenant_id, draft.id],
        queue="documents.high",
    )
    return DraftCreateResponse(draft_id=draft.id, task_id=str(task.id), status=draft.status)


@router.get("/documents/{document_id}/drafts", response_model=list[DraftRead])
def list_drafts(
    document_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> list[DraftRead]:
    return [
        DraftRead.model_validate(d) for d in DraftService(db).list_drafts(document_id, tenant_id)
    ]


@router.get("/documents/{document_id}/drafts/{draft_id}", response_model=DraftRead)
def get_draft(
    document_id: str,
    draft_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> DraftRead:
    return DraftRead.model_validate(DraftService(db).get_draft(document_id, draft_id, tenant_id))


@router.put("/documents/{document_id}/drafts/{draft_id}", response_model=DraftRead)
def update_draft(
    document_id: str,
    draft_id: str,
    payload: DraftUpdateRequest,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> DraftRead:
    draft, edits = DraftService(db).update_draft_sections(
        document_id=document_id,
        draft_id=draft_id,
        tenant_id=tenant_id,
        reviewer_name=payload.reviewer_name,
        sections=[section.model_dump() for section in payload.sections],
    )
    for edit in edits:
        extract_preferences_task.apply_async(args=[edit.id], queue="documents.normal")
    return DraftRead.model_validate(draft)


@router.get(
    "/documents/{document_id}/drafts/{draft_id}/evidence", response_model=list[DraftEvidenceChunk]
)
def get_draft_evidence(
    document_id: str,
    draft_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> list[DraftEvidenceChunk]:
    chunks = DraftService(db).get_evidence(document_id, draft_id, tenant_id)
    return [
        DraftEvidenceChunk(
            id=chunk.id,
            document_id=chunk.document_id,
            chunk_index=chunk.chunk_index,
            page_number=chunk.page_number,
            section_header=chunk.section_header,
            jurisdiction=chunk.jurisdiction,
            text=chunk.text,
        )
        for chunk in chunks
    ]


@router.get("/preferences", response_model=list[DraftPreferenceRead])
def list_preferences(
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> list[DraftPreferenceRead]:
    items: list[DraftPreferenceRead] = []
    for pref in PreferenceService(db).list_preferences(tenant_id):
        item = DraftPreferenceRead.model_validate(pref)
        if pref.source_edit_id:
            edit = db.get(DraftEdit, pref.source_edit_id)
            if edit:
                item.source_edit = {
                    "section_key": edit.section_key,
                    "original_content": edit.original_content,
                    "edited_content": edit.edited_content,
                    "reviewer_name": edit.reviewer_name,
                    "created_at": edit.created_at.isoformat(),
                }
        items.append(item)
    return items


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(
    preference_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> Response:
    PreferenceService(db).delete_preference(preference_id, tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/analytics/draft-improvement")
def draft_improvement(
    tenant_id: str | None = TENANT_DEP,
    db: Session = DB_DEP,
) -> dict:
    draft_filter = []
    edit_filter = []
    pref_filter = []
    if tenant_id is None:
        draft_filter.append(DraftOutput.tenant_id.is_(None))
        edit_filter.append(DraftEdit.tenant_id.is_(None))
        pref_filter.append(DraftPreference.tenant_id.is_(None))
    else:
        draft_filter.append(DraftOutput.tenant_id == tenant_id)
        edit_filter.append(DraftEdit.tenant_id == tenant_id)
        pref_filter.append(DraftPreference.tenant_id == tenant_id)

    total_drafts = db.scalar(select(func.count(DraftOutput.id)).where(*draft_filter)) or 0
    total_edits = db.scalar(select(func.count(DraftEdit.id)).where(*edit_filter)) or 0

    draft_period = cast(DraftOutput.created_at, Date)
    edit_period = cast(DraftEdit.created_at, Date)
    draft_rows = db.execute(
        select(draft_period, func.count(DraftOutput.id)).where(*draft_filter).group_by(draft_period)
    ).all()
    edit_rows = db.execute(
        select(edit_period, func.count(func.distinct(DraftEdit.draft_id)))
        .where(*edit_filter)
        .group_by(edit_period)
    ).all()

    drafts_by_period = {_period_key(period): count for period, count in draft_rows}
    edits_by_period = {_period_key(period): count for period, count in edit_rows}

    edit_rate_over_time = []
    for period in sorted(set(drafts_by_period) | set(edits_by_period)):
        drafts_generated = drafts_by_period.get(period, 0)
        drafts_edited = edits_by_period.get(period, 0)
        edit_rate_over_time.append(
            {
                "period": period,
                "drafts_generated": drafts_generated,
                "drafts_edited": drafts_edited,
                "edit_rate": round(drafts_edited / drafts_generated, 4)
                if drafts_generated
                else 0.0,
            }
        )

    edited_rows = db.execute(
        select(DraftEdit.section_key, func.count(DraftEdit.id))
        .where(*edit_filter)
        .group_by(DraftEdit.section_key)
        .order_by(func.count(DraftEdit.id).desc())
        .limit(10)
    ).all()
    top_preferences = list(
        db.scalars(
            select(DraftPreference)
            .where(*pref_filter)
            .order_by(
                DraftPreference.application_count.desc(),
                DraftPreference.confidence.desc(),
            )
            .limit(10)
        )
    )

    return {
        "edit_rate_over_time": edit_rate_over_time,
        "top_preferences": [
            {
                "text": pref.preference_text,
                "application_count": pref.application_count,
                "effectiveness_score": pref.effectiveness_score,
            }
            for pref in top_preferences
        ],
        "most_edited_sections": [
            {"section_key": section_key, "edit_count": count} for section_key, count in edited_rows
        ],
        "preference_count": db.scalar(select(func.count(DraftPreference.id)).where(*pref_filter))
        or 0,
        "total_drafts": total_drafts,
        "total_edits": total_edits,
        "edit_rate": round(total_edits / total_drafts, 4) if total_drafts else 0.0,
    }


def _period_key(value) -> str:  # noqa: ANN001
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
