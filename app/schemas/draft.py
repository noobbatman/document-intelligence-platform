from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

DraftTypeLiteral = Literal[
    "internal_memo",
    "case_fact_summary",
    "contract_summary",
    "notice_summary",
    "document_checklist",
    "affidavit_summary",
    "affidavit",
    "case_brief",
    "legal_notice",
]


class DraftGenerateRequest(BaseModel):
    draft_type: DraftTypeLiteral


class DraftCreateResponse(BaseModel):
    draft_id: str
    task_id: str | None = None
    status: str


class DraftRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    document_id: str
    tenant_id: str | None = None
    draft_type: str
    status: str
    content: dict[str, Any] = Field(default_factory=dict)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    generation_version: int
    word_count: int
    overall_grounding_score: float | None = None
    model_id: str | None = None
    preferences_applied: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DraftSectionEdit(BaseModel):
    key: str
    edited_content: str


class DraftUpdateRequest(BaseModel):
    reviewer_name: str
    sections: list[DraftSectionEdit]


class DraftEvidenceChunk(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    page_number: int
    section_header: str | None = None
    text: str


class DraftPreferenceRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    tenant_id: str | None = None
    document_type: str
    preference_text: str
    source_edit_id: str | None = None
    confidence: float
    application_count: int
    effectiveness_score: float | None = None
    created_at: datetime
    source_edit: dict[str, Any] | None = None
