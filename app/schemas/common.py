from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class APIMessage(BaseModel):
    message: str


class FieldConfidence(BaseModel):
    name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_snippet: str | None = None
    requires_review: bool = False


class AuditLogRead(BaseModel):
    id: str
    event_type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
