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
from app.rag.draft_queries import DOCUMENT_TYPE_QUERIES, DRAFT_QUERIES
from app.rag.draft_template_loader import load_draft_template
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

    def create_placeholder(
        self, document_id: str, draft_type: str, tenant_id: str | None
    ) -> DraftOutput:
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

    def generate(
        self, document_id: str, draft_type: str, tenant_id: str | None, draft_id: str | None = None
    ) -> DraftOutput:
        document = self._get_document(document_id, tenant_id=tenant_id)
        draft = self.db.get(DraftOutput, draft_id) if draft_id else None
        if draft is None:
            draft = self.create_placeholder(document.id, draft_type, tenant_id)

        try:
            chunks = self._retrieve_for_draft(
                document.id, draft_type, document.document_type or "unknown"
            )
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
        self.preferences.update_effectiveness_after_review(
            draft, edited_section_keys=[e.section_key for e in edits]
        )
        if edits:
            draft.content = content
            draft.word_count = self._word_count(content)
            flag_modified(draft, "content")
        self.db.commit()
        for edit in edits:
            self.db.refresh(edit)
        self.db.refresh(draft)
        return draft, edits

    def get_evidence(
        self, document_id: str, draft_id: str, tenant_id: str | None
    ) -> list[DocumentChunk]:
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

    def _retrieve_for_draft(
        self, document_id: str, draft_type: str, document_type: str = "unknown"
    ) -> list[RetrievedChunk]:
        by_id: dict[str, RetrievedChunk] = {}
        template = load_draft_template(draft_type)
        section_queries = [
            section.get("query")
            for section in (template or {}).get("sections", [])
            if section.get("query")
        ]
        queries = section_queries or [
            *DRAFT_QUERIES.get(draft_type, [draft_type.replace("_", " ")]),
            *DOCUMENT_TYPE_QUERIES.get(document_type, []),
        ]
        candidate_limit = max(self.settings.draft_max_chunks * 5, 30)
        for query in queries:
            for chunk in self.retrieval.retrieve(
                document_id,
                query,
                top_k=candidate_limit,
                min_score=0.0,
                session=self.db,
            ):
                current = by_id.get(chunk.chunk_id)
                if current is None or chunk.similarity_score > current.similarity_score:
                    by_id[chunk.chunk_id] = chunk
        for chunk in self._keyword_support_chunks(document_id, draft_type, document_type):
            by_id.setdefault(chunk.chunk_id, chunk)
        return self._select_diverse_chunks(list(by_id.values()))

    def _keyword_support_chunks(
        self, document_id: str, draft_type: str, document_type: str
    ) -> list[RetrievedChunk]:
        template = load_draft_template(draft_type) or {}
        override = template.get("document_type_overrides", {}).get(document_type, {})
        terms = [
            *template.get("support_terms", []),
            *override.get("support_terms", []),
        ]
        if not terms:
            return []
        limit_per_term = int(
            override.get("support_limit_per_term") or template.get("support_limit_per_term") or 3
        )
        chunks: list[RetrievedChunk] = []
        seen: set[str] = set()
        for term in terms:
            matches = list(
                self.db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .where(DocumentChunk.text.ilike(f"%{term}%"))
                    .order_by(DocumentChunk.chunk_index.asc())
                    .limit(limit_per_term)
                )
            )
            for chunk in matches:
                if chunk.id in seen:
                    continue
                seen.add(chunk.id)
                chunks.append(
                    RetrievedChunk(
                        chunk_id=chunk.id,
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        page_number=chunk.page_number,
                        section_header=chunk.section_header,
                        text=chunk.text,
                        similarity_score=0.5,
                    )
                )
        return chunks

    def _select_diverse_chunks(self, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if len(candidates) <= self.settings.draft_max_chunks:
            return sorted(candidates, key=lambda item: item.similarity_score, reverse=True)

        ranked = sorted(candidates, key=lambda item: item.similarity_score, reverse=True)
        selected: list[RetrievedChunk] = []
        selected_ids: set[str] = set()
        window_counts: dict[int, int] = {}

        def window_for(chunk: RetrievedChunk) -> int:
            return max(0, chunk.chunk_index) // 20

        for chunk in ranked:
            window = window_for(chunk)
            if window_counts.get(window, 0) >= 2:
                continue
            selected.append(chunk)
            selected_ids.add(chunk.chunk_id)
            window_counts[window] = window_counts.get(window, 0) + 1
            if len(selected) >= self.settings.draft_max_chunks:
                return selected

        for chunk in ranked:
            if chunk.chunk_id not in selected_ids:
                selected.append(chunk)
                if len(selected) >= self.settings.draft_max_chunks:
                    break
        return selected

    def _system_prompt(
        self, draft_type: str, prefs: list[Any], examples: list[tuple[str, str]]
    ) -> str:
        preference_block = ""
        if prefs:
            preference_block += "\nLEARNED PREFERENCES FROM PRIOR OPERATOR EDITS:\n"
            preference_block += "\n".join(f"- {pref.preference_text}" for pref in prefs)
        for original, edited in examples:
            preference_block += (
                "\n\nEXAMPLE (for reference):\n[ORIGINAL DRAFT EXCERPT]\n"
                f"{original}\n\n[HOW OPERATOR CORRECTED IT]\n{edited}"
            )

        draft_date = self._draft_date()
        template_rules = self._template_default_rules(draft_type)
        return f"""You are a legal document analyst for Pearson Specter Litt. Your task is to produce a {draft_type} based strictly on the provided source material.

STRICT GROUNDING RULES:
1. Every factual claim must be traceable to a specific source chunk. Use inline citations in the format [Page N] or [Page N - Section Title].
2. If information needed for a section is not present in the source material, write "[UNSUPPORTED: {{reason}}]" rather than inferring or hallucinating.
3. Do not draw on general legal knowledge to fill gaps. Only use what the documents contain.
4. Use formal legal memo style.
5. If the draft needs a memo date, use exactly this date: {draft_date}. Do not invent filing, review, or memo dates.
6. Normalize obvious OCR artifacts only when the intended term is clear from context; for example, use "Landmark Credit Union" when OCR shows "Iandmark Credit Union".
{template_rules}
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
        template_instructions = self._template_instructions(
            draft_type, document.document_type or "unknown"
        )
        return (
            f"DOCUMENT TYPE: {document.document_type or 'unknown'}\n"
            f"DRAFT DATE: {self._draft_date()}\n"
            f"STRUCTURED FIELDS EXTRACTED: {json.dumps(structured_fields, default=str)}\n\n"
            f"{template_instructions}\n\n"
            f"SOURCE CHUNKS (ordered by relevance):\n---\n{chunk_block}\n---\n\n"
            f"Generate a {draft_type} for this document."
        )

    def _template_default_rules(self, draft_type: str) -> str:
        template = load_draft_template(draft_type) or {}
        defaults = template.get("defaults", {})
        if not defaults:
            return ""
        resolved = {
            key: (self._draft_date() if value == "{{draft_date}}" else value)
            for key, value in defaults.items()
        }
        lines = ["", "TEMPLATE DEFAULT RULES:"]
        for key, value in resolved.items():
            lines.append(f"- Use {key.upper()}: {value}.")
        lines.append(
            "- Treat template defaults as metadata, not document facts; do not mark them unsupported only because they are absent from the source."
        )
        return "\n".join(lines)

    def _template_instructions(self, draft_type: str, document_type: str) -> str:
        template = load_draft_template(draft_type)
        if not template:
            return "DRAFT TEMPLATE: No external template found; use the requested draft type."
        applicable = template.get("applicable_document_types", [])
        applicability = (
            f"Applicable to {document_type}."
            if not applicable or document_type in applicable
            else f"Template is not explicitly listed for {document_type}; use it as a best-effort fallback."
        )
        lines = [f"DRAFT TEMPLATE: {template.get('draft_type', draft_type)}. {applicability}"]
        defaults = template.get("defaults", {})
        if defaults:
            resolved = {
                key: (self._draft_date() if value == "{{draft_date}}" else value)
                for key, value in defaults.items()
            }
            lines.append(f"Defaults: {json.dumps(resolved, default=str)}")
        lines.append("Required section plan:")
        for section in template.get("sections", []):
            required = "required" if section.get("required") else "optional"
            lines.append(
                f"- {section.get('title', section.get('key'))} ({required}): "
                f"{section.get('instruction', '')}"
            )
        override = template.get("document_type_overrides", {}).get(document_type, {})
        if override.get("instructions"):
            lines.append(f"{document_type} instructions:")
            for instruction in override["instructions"]:
                lines.append(f"- {instruction}")
        return "\n".join(lines)

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
        return sum(
            len(str(section.get("content", "")).split()) for section in content.get("sections", [])
        )

    def _draft_date(self) -> str:
        now = datetime.now(UTC)
        return f"{now:%B} {now.day}, {now:%Y}"
