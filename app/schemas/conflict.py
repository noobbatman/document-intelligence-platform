from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ConflictItemRead(BaseModel):
    conflict_type: str
    description: str
    chunk_indices: list[int] = Field(default_factory=list)
    severity: str
    field: str | None = None


class ConflictReport(BaseModel):
    document_id: str
    conflicts: list[ConflictItemRead]
    conflict_count: int
    has_high_severity: bool
    checked_at: datetime
