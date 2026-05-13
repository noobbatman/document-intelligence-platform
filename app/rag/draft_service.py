"""Draft generation and edit capture services."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.db.models import Document, DocumentChunk, DraftEdit, DraftOutput, DraftStatus
from app.rag.draft_queries import DRAFT_QUERIES
from app.rag.gemini_client import GeminiClient
from app.rag.preference_service import PreferenceService
from app.rag.retrieval_service import RetrievalService, RetrievedChunk


class DraftService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.retrieval = RetrievalService()
        self.gemini = GeminiClient()
        self.preferences = PreferenceService(db)

    def create_placeholder(self, document_id: str, draft_type: str, tenant_id: str | None) -> DraftOutput:
        document = self._get_document(document_id, tenant_id=tenant_id)
        draft = DraftOutput(
            document_id=document.id,
            tenant_id=document.tenant_id,
            draft_type=draft_type,
            status=DraftStatus.generating,
            content={"sections": []},
            evidence_chunk_ids=[],
            generation_version=1,
            word_count=0,
            model_id=self.settings.draft_model,
            preferences_applied=[],
        )
        self.db.add(draft)
        self.db.commit()
        self.db.refresh(draft)
        return draft

    def generate(self, document_id: str, draft_type: str, tenant_id: str | None, draft_id: str | None = None) -> DraftOutput:
        document = self._get_document(document_id, tenant_id=tenant_id)
        draft = self.db.get(DraftOutput, draft_id) if draft_id else None
        if draft is None:
            draft = self.create_placeholder(document.id, draft_type, tenant_id)

        try:
            chunks = self._retrieve_for_draft(document.id, draft_type)
            prefs = self.preferences.get_preferences_for_draft(
                tenant_id=document.tenant_id,
                document_type=document.document_type or "unknown",
                limit=self.settings.preference_max_per_draft,
            )
            examples = self.preferences.get_few_shot_examples(
                tenant_id=document.tenant_id,
                document_type=document.document_type or "unknown",
                limit=2,
            )
            structured_fields = (
                document.extraction_result.export_payload.get("fields", {})
                if document.extraction_result
                else {}
            )
            payload = self.gemini.generate_json(
                system_prompt=self._system_prompt(draft_type, prefs, examples),
                user_prompt=self._user_prompt(document, draft_type, structured_fields, chunks),
            )
            content = self._normalize_content(payload)
            evidence_ids = self._evidence_ids(content, chunks)

            draft.content = content
            draft.evidence_chunk_ids = evidence_ids
            draft.status = DraftStatus.draft
            draft.word_count = self._word_count(content)
            draft.model_id = self.settings.draft_model
            draft.preferences_applied = [pref.id for pref in prefs]
            draft.updated_at = datetime.now(UTC)
            flag_modified(draft, "content")

            for pref in prefs:
                pref.application_count += 1

            self.db.commit()
            self.db.refresh(draft)
            return draft
        except Exception as exc:
            draft.status = DraftStatus.failed
            draft.content = {
                "sections": [
                    {
                        "key": "generation_error",
                        "title": "Generation Error",
                        "content": f"[UNSUPPORTED: Draft generation failed: {exc}]",
                        "evidence_chunk_ids": [],
                        "confidence": "unsupported",
                    }
                ]
            }
            draft.updated_at = datetime.now(UTC)
            flag_modified(draft, "content")
            self.db.commit()
            raise

    def list_drafts(self, document_id: str, tenant_id: str | None) -> list[DraftOutput]:
        self._get_document(document_id, tenant_id=tenant_id)
        return list(
            self.db.scalars(
                select(DraftOutput)
                .where(DraftOutput.document_id == document_id)
                .order_by(DraftOutput.created_at.desc())
            )
        )

    def get_draft(self, document_id: str, draft_id: str, tenant_id: str | None) -> DraftOutput:
        document = self._get_document(document_id, tenant_id=tenant_id)
        draft = self.db.get(DraftOutput, draft_id)
        if not draft or draft.document_id != document.id:
            raise HTTPException(status_code=404, detail="Draft not found.")
        return draft

    def update_draft_sections(
        self,
        *,
        document_id: str,
        draft_id: str,
        tenant_id: str | None,
        reviewer_name: str,
        sections: list[dict[str, str]],
    ) -> tuple[DraftOutput, list[DraftEdit]]:
        draft = self.get_draft(document_id, draft_id, tenant_id)
        content = dict(draft.content or {"sections": []})
        existing_sections = content.get("sections", [])
        by_key = {section.get("key"): section for section in existing_sections}
        edits: list[DraftEdit] = []

        for incoming in sections:
            key = incoming.get("key")
            edited = incoming.get("edited_content", "")
            current = by_key.get(key)
            if not key or not current:
                continue
            original = current.get("content", "")
            if edited == original:
                continue
            current["content"] = edited
            edit = DraftEdit(
                draft_id=draft.id,
                document_id=draft.document_id,
                tenant_id=draft.tenant_id,
                section_key=key,
                original_content=original,
                edited_content=edited,
                reviewer_name=reviewer_name,
            )
            self.db.add(edit)
            edits.append(edit)

        draft.status = DraftStatus.reviewed
        draft.updated_at = datetime.now(UTC)
        self.preferences.update_effectiveness_after_review(draft, edited=bool(edits))
        if edits:
            draft.content = content
            draft.word_count = self._word_count(content)
            flag_modified(draft, "content")
        self.db.commit()
        for edit in edits:
            self.db.refresh(edit)
        self.db.refresh(draft)
        return draft, edits

    def get_evidence(self, document_id: str, draft_id: str, tenant_id: str | None) -> list[DocumentChunk]:
        draft = self.get_draft(document_id, draft_id, tenant_id)
        if not draft.evidence_chunk_ids:
            return []
        return list(
            self.db.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.id.in_(draft.evidence_chunk_ids))
                .order_by(DocumentChunk.page_number.asc(), DocumentChunk.chunk_index.asc())
            )
        )

    def _get_document(self, document_id: str, tenant_id: str | None) -> Document:
        stmt = select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
        if tenant_id is None:
            stmt = stmt.where(Document.tenant_id.is_(None))
        else:
            stmt = stmt.where(Document.tenant_id == tenant_id)
        document = self.db.scalar(stmt)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    def _retrieve_for_draft(self, document_id: str, draft_type: str) -> list[RetrievedChunk]:
        by_id: dict[str, RetrievedChunk] = {}
        for query in DRAFT_QUERIES.get(draft_type, [draft_type.replace("_", " ")]):
            for chunk in self.retrieval.retrieve(
                document_id,
                query,
                top_k=self.settings.draft_max_chunks,
                min_score=0.0,
                session=self.db,
            ):
                current = by_id.get(chunk.chunk_id)
                if current is None or chunk.similarity_score > current.similarity_score:
                    by_id[chunk.chunk_id] = chunk
        return sorted(by_id.values(), key=lambda item: item.similarity_score, reverse=True)[: self.settings.draft_max_chunks]

    def _system_prompt(self, draft_type: str, prefs: list[Any], examples: list[tuple[str, str]]) -> str:
        preference_block = ""
        if prefs:
            preference_block += "\nLEARNED PREFERENCES FROM PRIOR OPERATOR EDITS:\n"
            preference_block += "\n".join(f"- {pref.preference_text}" for pref in prefs)
        for original, edited in examples:
            preference_block += (
                "\n\nEXAMPLE (for reference):\n[ORIGINAL DRAFT EXCERPT]\n"
                f"{original}\n\n[HOW OPERATOR CORRECTED IT]\n{edited}"
            )

        return f"""You are a legal document analyst for Pearson Specter Litt. Your task is to produce a {draft_type} based strictly on the provided source material.

STRICT GROUNDING RULES:
1. Every factual claim must be traceable to a specific source chunk. Use inline citations in the format [Page N] or [Page N - Section Title].
2. If information needed for a section is not present in the source material, write "[UNSUPPORTED: {{reason}}]" rather than inferring or hallucinating.
3. Do not draw on general legal knowledge to fill gaps. Only use what the documents contain.
4. Use formal legal memo style.
{preference_block}

Respond in JSON with this structure:
{{"sections":[{{"key":"section_identifier","title":"Section Title","content":"Full prose content with citations","evidence_chunk_ids":["chunk_uuid"],"confidence":"high|medium|low|unsupported"}}]}}"""

    def _user_prompt(
        self,
        document: Document,
        draft_type: str,
        structured_fields: dict[str, Any],
        chunks: list[RetrievedChunk],
    ) -> str:
        chunk_block = "\n---\n".join(
            (
                f"[Chunk {idx} | Page {chunk.page_number} | {chunk.section_header or ''}] {chunk.chunk_id}\n"
                f"{chunk.text}"
            )
            for idx, chunk in enumerate(chunks, 1)
        )
        return (
            f"DOCUMENT TYPE: {document.document_type or 'unknown'}\n"
            f"STRUCTURED FIELDS EXTRACTED: {json.dumps(structured_fields, default=str)}\n\n"
            f"SOURCE CHUNKS (ordered by relevance):\n---\n{chunk_block}\n---\n\n"
            f"Generate a {draft_type} for this document."
        )

    def _normalize_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections = payload.get("sections", [])
        if not isinstance(sections, list):
            sections = []
        normalized = []
        for idx, section in enumerate(sections):
            normalized.append(
                {
                    "key": str(section.get("key") or f"section_{idx + 1}"),
                    "title": str(section.get("title") or f"Section {idx + 1}"),
                    "content": str(section.get("content") or ""),
                    "evidence_chunk_ids": list(section.get("evidence_chunk_ids") or []),
                    "confidence": str(section.get("confidence") or "low"),
                }
            )
        return {"sections": normalized}

    def _evidence_ids(self, content: dict[str, Any], chunks: list[RetrievedChunk]) -> list[str]:
        valid = {chunk.chunk_id for chunk in chunks}
        ids: list[str] = []
        for section in content.get("sections", []):
            for chunk_id in section.get("evidence_chunk_ids", []):
                if chunk_id in valid and chunk_id not in ids:
                    ids.append(chunk_id)
        return ids

    def _word_count(self, content: dict[str, Any]) -> int:
        return sum(len(str(section.get("content", "")).split()) for section in content.get("sections", []))
