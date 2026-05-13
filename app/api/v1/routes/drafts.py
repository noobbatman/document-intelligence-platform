"""Draft generation, evidence, edit capture, and learned preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
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
            text=chunk.text,
        )
        for chunk in chunks
    ]


@router.get("/preferences", response_model=list[DraftPreferenceRead])
def list_preferences(
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> list[DraftPreferenceRead]:
    return [
        DraftPreferenceRead.model_validate(pref)
        for pref in PreferenceService(db).list_preferences(tenant_id)
    ]


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preference(
    preference_id: str,
    db: Session = DB_DEP,
    tenant_id: str | None = TENANT_DEP,
) -> Response:
    PreferenceService(db).delete_preference(preference_id, tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
