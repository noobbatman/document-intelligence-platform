"""Audit logging service — records every significant document lifecycle event."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEventType, AuditLog


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def log(
        self,
        document_id: str,
        event_type: str | AuditEventType,
        payload: dict[str, Any],
        actor: str = "system",
        correlation_id: str | None = None,
    ) -> None:
        entry = AuditLog(
            document_id=document_id,
            event_type=str(event_type),
            actor=actor,
            payload=payload,
            correlation_id=correlation_id,
        )
        self.db.add(entry)
        self.db.flush()
