"""Learn reusable drafting preferences from operator edits."""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Document, DraftEdit, DraftOutput, DraftPreference
from app.rag.embedder import get_embedder
from app.rag.gemini_client import GeminiClient
from app.rag.retrieval_service import _cosine


class PreferenceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.embedder = get_embedder()
        self.gemini = GeminiClient()

    def extract_from_edit(self, edit_id: str) -> DraftPreference | None:
        edit = self.db.get(DraftEdit, edit_id)
        if not edit:
            return None
        if self._trivial(edit.original_content, edit.edited_content):
            edit.processed = True
            self.db.commit()
            return None

        document = self.db.get(Document, edit.document_id)
        draft = self.db.get(DraftOutput, edit.draft_id)
        document_type = document.document_type if document else "unknown"
        draft_type = draft.draft_type if draft else "unknown"

        payload = self.gemini.generate_json(
            system_prompt="Extract reusable legal drafting preferences from operator edits. Respond only as JSON.",
            user_prompt=f"""
An operator reviewed a draft and made the following edit to the "{edit.section_key}" section
of a {document_type} {draft_type}:

ORIGINAL:
{edit.original_content}

EDITED TO:
{edit.edited_content}

Extract ONE reusable preference rule that would cause future drafts to be closer to the edited version WITHOUT referencing these specific documents.
The rule should be general enough to apply to future {document_type} documents.

Respond with JSON: {{"preference": "...", "confidence": 0.0-1.0}}
If the edit is too document-specific to generalize, respond: {{"preference": null}}
""",
        )
        preference_text = payload.get("preference")
        if not preference_text:
            edit.processed = True
            self.db.commit()
            return None

        vector = self.embedder.encode_passages([str(preference_text)])[0]
        existing = self._find_duplicate(edit.tenant_id, document_type or "unknown", vector)
        if existing:
            new_confidence = float(payload.get("confidence") or 0.8)
            if new_confidence > (existing.confidence or 0.0):
                existing.preference_text = str(preference_text)
                existing.embedding = vector
            existing.confidence = max(existing.confidence or 0.0, new_confidence)
            edit.processed = True
            self.db.commit()
            return existing

        preference = DraftPreference(
            tenant_id=edit.tenant_id,
            document_type=document_type or "unknown",
            preference_text=str(preference_text),
            source_edit_id=edit.id,
            embedding=vector,
            confidence=float(payload.get("confidence") or 0.8),
            effectiveness_score=0.5,
        )
        self.db.add(preference)
        edit.processed = True
        self.db.commit()
        self.db.refresh(preference)
        return preference

    def get_preferences_for_draft(
        self,
        tenant_id: str | None,
        document_type: str,
        *,
        limit: int | None = None,
    ) -> list[DraftPreference]:
        limit = limit or self.settings.preference_max_per_draft
        stmt = select(DraftPreference).where(DraftPreference.document_type == document_type)
        if tenant_id is None:
            stmt = stmt.where(DraftPreference.tenant_id.is_(None))
        else:
            stmt = stmt.where(DraftPreference.tenant_id == tenant_id)
        score = (DraftPreference.application_count * 0.6) + (DraftPreference.confidence * 0.4)
        stmt = stmt.order_by(desc(score)).limit(limit)
        return list(self.db.scalars(stmt))

    def get_few_shot_examples(
        self,
        tenant_id: str | None,
        document_type: str,
        *,
        limit: int = 2,
    ) -> list[tuple[str, str]]:
        prefs = self.get_preferences_for_draft(tenant_id, document_type, limit=limit)
        examples: list[tuple[str, str]] = []
        for pref in prefs:
            if not pref.source_edit_id:
                continue
            edit = self.db.get(DraftEdit, pref.source_edit_id)
            if edit:
                examples.append((edit.original_content, edit.edited_content))
        return examples[:limit]

    def list_preferences(self, tenant_id: str | None) -> list[DraftPreference]:
        stmt = select(DraftPreference).order_by(DraftPreference.created_at.desc())
        if tenant_id is None:
            stmt = stmt.where(DraftPreference.tenant_id.is_(None))
        else:
            stmt = stmt.where(DraftPreference.tenant_id == tenant_id)
        return list(self.db.scalars(stmt))

    def delete_preference(self, preference_id: str, tenant_id: str | None) -> None:
        stmt = select(DraftPreference).where(DraftPreference.id == preference_id)
        if tenant_id is None:
            stmt = stmt.where(DraftPreference.tenant_id.is_(None))
        else:
            stmt = stmt.where(DraftPreference.tenant_id == tenant_id)
        preference = self.db.scalar(stmt)
        if preference:
            self.db.delete(preference)
            self.db.commit()

    def update_effectiveness_after_review(
        self, draft: DraftOutput, *, edited_section_keys: list[str]
    ) -> None:
        if not draft.preferences_applied:
            return
        if not edited_section_keys:
            delta = 0.10
        else:
            total_sections = len((draft.content or {}).get("sections", [])) or 1
            edit_ratio = len(edited_section_keys) / total_sections
            # Scale penalty by edit coverage so a single unrelated edit doesn't
            # penalise every applied preference equally.
            delta = -0.05 * edit_ratio
        prefs = list(
            self.db.scalars(
                select(DraftPreference).where(DraftPreference.id.in_(draft.preferences_applied))
            )
        )
        for pref in prefs:
            current = pref.effectiveness_score if pref.effectiveness_score is not None else 0.5
            pref.effectiveness_score = max(0.0, min(1.0, current + delta))

    def _find_duplicate(
        self,
        tenant_id: str | None,
        document_type: str,
        vector: list[float],
    ) -> DraftPreference | None:
        candidates = self.get_preferences_for_draft(tenant_id, document_type, limit=50)
        for pref in candidates:
            if _cosine(vector, pref.embedding) >= self.settings.preference_dedup_threshold:
                return pref
        return None

    def _trivial(self, original: str, edited: str) -> bool:
        if original.strip() == edited.strip():
            return True
        return abs(len(edited) - len(original)) < 20 and original.split() == edited.split()
