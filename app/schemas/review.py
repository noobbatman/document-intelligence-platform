"""Review task schemas — includes page-level evidence fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReviewTaskRead(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    document_id: str
    field_name: str
    proposed_value: dict[str, Any]
    original_value: dict[str, Any]
    source_snippet: str | None = None
    confidence: float
    status: str
    # Page-level evidence
    page_number: int | None = None
    bbox: list[float] | None = None
    validation_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ReviewDecisionCreate(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=255)
    corrected_value: dict[str, Any]
    comment: str | None = None


class ReviewDecisionResponse(BaseModel):
    task_id: str
    status: str
    corrected_value: dict[str, Any]
    value_changed: bool = False
