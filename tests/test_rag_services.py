from __future__ import annotations

import pytest

from app.classification.hybrid_classifier import HybridDocumentClassifier
from app.db.models import (
    Document,
    DocumentStatus,
    DraftEdit,
    DraftOutput,
    DraftPreference,
    ExtractionResult,
)
from app.rag.chunker import SectionAwareChunker
from app.rag.draft_service import DraftService
from app.rag.embedder import Embedder
from app.rag.embedding_service import EmbeddingService
from app.rag.preference_service import PreferenceService
from app.rag.retrieval_service import RetrievalService


def test_section_chunker_preserves_legal_headers():
    text = (
        "RECITALS\n"
        "This agreement is made between Acme Corp and Globex Inc. It is effective today.\n\n"
        "GOVERNING LAW\n"
        "This agreement shall be governed by the laws of New York. Venue is New York County."
    )

    chunks = SectionAwareChunker(chunk_size_chars=90, chunk_overlap_chars=15).chunk(text)

    assert chunks
    assert any(chunk.section_header == "RECITALS" for chunk in chunks)
    assert any(chunk.section_header == "GOVERNING LAW" for chunk in chunks)
    assert all(chunk.text.strip() for chunk in chunks)


def test_classifier_recognizes_legal_notice():
    result = HybridDocumentClassifier().classify(
        "CEASE AND DESIST NOTICE. You must stop using the mark within 10 days. "
        "Response deadline: May 20, 2026."
    )

    assert result.label == "legal_notice"
    assert result.confidence > 0.4


def test_embedding_and_retrieval_round_trip(db_session):
    doc = Document(
        filename="contract.txt",
        stored_path="contract.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="contract",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text=(
                "PAYMENT TERMS\n"
                "The client shall pay Acme Corp $10,000 within thirty days.\n\n"
                "GOVERNING LAW\n"
                "This agreement is governed by New York law."
            ),
            export_payload={"fields": {"vendor_name": "Acme Corp"}},
        )
    )
    db_session.commit()

    count = EmbeddingService().embed_document(doc.id, db_session)
    results = RetrievalService().retrieve(
        doc.id,
        "payment terms amount due",
        top_k=3,
        min_score=-1.0,
        session=db_session,
    )

    assert count >= 1
    assert results
    assert any("pay Acme" in chunk.text for chunk in results)


def test_bge_prefixes_are_query_only(monkeypatch):
    captured: list[list[str]] = []

    def fake_encode(self, texts):
        captured.append(texts)
        return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(Embedder, "_encode", fake_encode)
    embedder = Embedder()

    embedder.encode_passages(["payment terms"])
    embedder.encode_query("governing law")

    assert captured[0] == ["payment terms"]
    assert captured[1] == ["Represent this sentence for searching relevant passages: governing law"]


def test_chunker_ignores_common_legal_abbreviations_for_sentence_boundary():
    text = "Pearson Specter Litt, Inc. entered the agreement. The payment terms follow."
    chunker = SectionAwareChunker(chunk_size_chars=120, chunk_overlap_chars=10)
    target_after_inc = text.index(" entered")

    boundary = chunker._sentence_boundary(text, 0, target_after_inc, len(text))

    assert boundary > text.index("agreement.")


def test_draft_edit_capture_updates_content(db_session):
    doc = Document(
        filename="contract.txt",
        stored_path="contract.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="contract",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    draft = DraftOutput(
        document_id=doc.id,
        draft_type="contract_summary",
        status="draft",
        content={
            "sections": [
                {
                    "key": "payment_terms",
                    "title": "Payment Terms",
                    "content": "Payment is due soon.",
                    "evidence_chunk_ids": [],
                    "confidence": "medium",
                }
            ]
        },
        evidence_chunk_ids=[],
        generation_version=1,
        word_count=4,
        preferences_applied=[],
    )
    db_session.add(draft)
    db_session.commit()

    updated, edits = DraftService(db_session).update_draft_sections(
        document_id=doc.id,
        draft_id=draft.id,
        tenant_id=None,
        reviewer_name="analyst",
        sections=[{"key": "payment_terms", "edited_content": "Payment is due within thirty days."}],
    )

    assert updated.status == "reviewed"
    assert len(edits) == 1
    assert updated.content["sections"][0]["content"] == "Payment is due within thirty days."


def test_draft_generation_failure_marks_failed(db_session, monkeypatch):
    doc = Document(
        filename="contract.txt",
        stored_path="contract.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="contract",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.commit()

    service = DraftService(db_session)

    def fail_generation(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(service.gemini, "generate_json", fail_generation)

    with pytest.raises(RuntimeError):
        service.generate(doc.id, "contract_summary", tenant_id=None)

    draft = db_session.query(DraftOutput).filter_by(document_id=doc.id).one()
    assert draft.status == "failed"


def test_preference_duplicate_updates_higher_confidence_text(db_session, monkeypatch):
    doc = Document(
        filename="contract.txt",
        stored_path="contract.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="contract",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    draft = DraftOutput(
        document_id=doc.id,
        draft_type="contract_summary",
        status="reviewed",
        content={"sections": []},
        evidence_chunk_ids=[],
        generation_version=1,
        word_count=0,
        preferences_applied=[],
    )
    db_session.add(draft)
    db_session.flush()
    edit = DraftEdit(
        draft_id=draft.id,
        document_id=doc.id,
        section_key="governing_law",
        original_content="Use short governing law language.",
        edited_content="Always cite the governing law clause with venue.",
        reviewer_name="analyst",
    )
    existing = DraftPreference(
        document_type="contract",
        preference_text="Old wording",
        source_edit_id=None,
        embedding=[1.0] + [0.0] * 767,
        confidence=0.4,
        effectiveness_score=0.5,
    )
    db_session.add_all([edit, existing])
    db_session.commit()

    service = PreferenceService(db_session)
    monkeypatch.setattr(
        service.gemini,
        "generate_json",
        lambda **_: {"preference": "Always cite governing law with venue.", "confidence": 0.9},
    )
    monkeypatch.setattr(service.embedder, "encode_passages", lambda texts: [[1.0] + [0.0] * 767])

    updated = service.extract_from_edit(edit.id)

    assert updated.id == existing.id
    assert updated.preference_text == "Always cite governing law with venue."
    assert updated.confidence == 0.9


def test_preference_effectiveness_updates_on_review(db_session):
    doc = Document(
        filename="contract.txt",
        stored_path="contract.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="contract",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    pref = DraftPreference(
        document_type="contract",
        preference_text="Always cite governing law.",
        embedding=[1.0] + [0.0] * 767,
        confidence=0.8,
        effectiveness_score=0.5,
    )
    db_session.add(pref)
    db_session.flush()
    draft = DraftOutput(
        document_id=doc.id,
        draft_type="contract_summary",
        status="draft",
        content={
            "sections": [
                {
                    "key": "governing_law",
                    "title": "Governing Law",
                    "content": "New York law applies.",
                    "evidence_chunk_ids": [],
                    "confidence": "high",
                }
            ]
        },
        evidence_chunk_ids=[],
        generation_version=1,
        word_count=4,
        preferences_applied=[pref.id],
    )
    db_session.add(draft)
    db_session.commit()

    DraftService(db_session).update_draft_sections(
        document_id=doc.id,
        draft_id=draft.id,
        tenant_id=None,
        reviewer_name="analyst",
        sections=[{"key": "governing_law", "edited_content": "New York law applies."}],
    )
    assert round(pref.effectiveness_score, 2) == 0.6

    DraftService(db_session).update_draft_sections(
        document_id=doc.id,
        draft_id=draft.id,
        tenant_id=None,
        reviewer_name="analyst",
        sections=[{"key": "governing_law", "edited_content": "New York law and venue apply."}],
    )
    assert round(pref.effectiveness_score, 2) == 0.55


def test_get_preferences_for_draft_enforces_sql_limit(db_session):
    for idx in range(8):
        db_session.add(
            DraftPreference(
                tenant_id="tenant-a",
                document_type="contract",
                preference_text=f"Preference {idx}",
                embedding=[1.0] + [0.0] * 767,
                confidence=0.5 + (idx * 0.01),
                application_count=idx,
                effectiveness_score=0.5,
            )
        )
    db_session.commit()

    prefs = PreferenceService(db_session).get_preferences_for_draft(
        tenant_id="tenant-a",
        document_type="contract",
        limit=3,
    )

    assert len(prefs) == 3
    assert [pref.preference_text for pref in prefs] == [
        "Preference 7",
        "Preference 6",
        "Preference 5",
    ]
