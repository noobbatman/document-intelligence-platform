from __future__ import annotations

import pytest

from app.classification.hybrid_classifier import HybridDocumentClassifier
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DraftEdit,
    DraftOutput,
    DraftPreference,
    ExtractionResult,
)
from app.extraction.defined_terms import (
    annotate_defined_terms,
    extract_defined_terms,
    format_defined_terms_block,
)
from app.rag.chunker import SectionAwareChunker
from app.rag.draft_service import DraftService
from app.rag.embedder import Embedder
from app.rag.embedding_service import EmbeddingService
from app.rag.grounding_scorer import overall_score, score, score_sections
from app.rag.jurisdiction import (
    detect_chunk_jurisdiction,
    detect_document_jurisdiction_tags,
)
from app.rag.preference_service import PreferenceService
from app.rag.retrieval_service import RetrievalService, RetrievedChunk


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


def test_grounding_scorer_counts_cited_factual_sentences():
    content = (
        "The plaintiff opened an account at Landmark Credit Union on November 27, 2013 [Page 5]. "
        "The subpoena was issued before valid legal process was confirmed. "
        "[UNSUPPORTED: Venue facts were not located in the source material.] "
        "Conclusion."
    )

    assert score(content) == 0.33


def test_grounding_scorer_adds_section_and_overall_scores():
    content = {
        "sections": [
            {
                "key": "facts",
                "content": "The plaintiff opened an account at Landmark Credit Union on November 27, 2013 [Chunk 1].",
            },
            {
                "key": "venue",
                "content": "Venue is proper because defendants operated in the district.",
            },
        ]
    }

    scored = score_sections(content)

    assert scored["sections"][0]["grounding_score"] == 1.0
    assert scored["sections"][1]["grounding_score"] == 0.0
    assert overall_score(scored) is not None


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


def test_defined_terms_extract_common_legal_patterns():
    text = (
        'Abercrombie & Fitch Management Co. (the "Company") entered this Agreement. '
        '"Effective Date" means May 10, 2017. '
        'As used herein, "Confidential Information" means non-public business information.'
    )

    terms = extract_defined_terms(text, confirm_with_llm=False)

    assert terms["Company"] == "Abercrombie & Fitch Management Co"
    assert terms["Effective Date"] == "May 10, 2017"
    assert terms["Confidential Information"] == "non-public business information"


def test_defined_terms_annotate_chunks_for_embeddings():
    text = "The Company shall preserve Confidential Information."
    terms = {
        "Company": "Abercrombie & Fitch Management Co.",
        "Confidential Information": "non-public business information",
    }

    annotated = annotate_defined_terms(text, terms)

    assert "Company [Abercrombie & Fitch Management Co]" in annotated
    assert "Confidential Information [non-public business information]" in annotated


def test_defined_terms_prompt_block():
    block = format_defined_terms_block({"LCU": "Landmark Credit Union"})

    assert "DEFINED TERMS IN THIS DOCUMENT" in block
    assert '"LCU" = Landmark Credit Union' in block


def test_jurisdiction_detector_tags_federal_and_state_signals():
    text = (
        "Jurisdiction arises under 28 U.S.C. § 1331 in the E.D. Wis. "
        "The complaint also cites Wis. Stat. § 137.01."
    )

    tags = detect_document_jurisdiction_tags(text, {"venue": "28 U.S.C. § 1391(b)"})

    assert "federal" in tags
    assert "federal:EDWI" in tags
    assert "state:WI" in tags
    assert detect_chunk_jurisdiction("Venue is proper in the E.D. Wis.") == "federal:EDWI"


def test_embedding_stores_chunk_jurisdiction(db_session):
    doc = Document(
        filename="complaint.txt",
        stored_path="complaint.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="legal_complaint",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text="Jurisdiction arises under 28 U.S.C. § 1331 in the E.D. Wis.",
            export_payload={"fields": {}, "jurisdiction_tags": ["federal", "federal:EDWI"]},
        )
    )
    db_session.commit()

    EmbeddingService().embed_document(doc.id, db_session)

    chunk = db_session.query(DocumentChunk).filter_by(document_id=doc.id).one()
    assert chunk.jurisdiction == "federal:EDWI"


def test_retrieval_soft_filters_by_document_jurisdiction(db_session, monkeypatch):
    doc = Document(
        filename="complaint.txt",
        stored_path="complaint.txt",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="legal_complaint",
        pipeline_version="test",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(
        ExtractionResult(
            document_id=doc.id,
            ocr_text="",
            export_payload={"jurisdiction_tags": ["federal"]},
        )
    )
    db_session.add_all(
        [
            DocumentChunk(
                document_id=doc.id,
                chunk_index=0,
                page_number=1,
                section_header=None,
                jurisdiction="federal",
                text="Federal question jurisdiction under 28 U.S.C. § 1331.",
                char_start=0,
                char_end=10,
                embedding=[1.0] + [0.0] * 767,
            ),
            DocumentChunk(
                document_id=doc.id,
                chunk_index=1,
                page_number=1,
                section_header=None,
                jurisdiction="state:CA",
                text="California state law issue.",
                char_start=11,
                char_end=20,
                embedding=[1.0] + [0.0] * 767,
            ),
            DocumentChunk(
                document_id=doc.id,
                chunk_index=2,
                page_number=1,
                section_header=None,
                jurisdiction=None,
                text="Untagged factual background.",
                char_start=21,
                char_end=30,
                embedding=[1.0] + [0.0] * 767,
            ),
        ]
    )
    db_session.commit()

    service = RetrievalService()
    service.settings.query_expansion_enabled = False
    monkeypatch.setattr(service.embedder, "encode_query", lambda query: [1.0] + [0.0] * 767)

    results = service.retrieve(
        doc.id,
        "jurisdiction facts",
        top_k=10,
        min_score=-1.0,
        session=db_session,
    )

    texts = [item.text for item in results]
    assert "Federal question jurisdiction under 28 U.S.C. § 1331." in texts
    assert "Untagged factual background." in texts
    assert "California state law issue." not in texts


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


def test_query_expansion_is_cached_and_concatenated(monkeypatch):
    service = RetrievalService()
    service.settings.query_expansion_enabled = True
    service.settings.gemini_api_key = "test-key"
    calls = []

    def fake_generate_json(**kwargs):
        calls.append(kwargs)
        return {"expanded_query": "unauthorized disclosure financial records bank records"}

    monkeypatch.setattr(service.gemini, "generate_json", fake_generate_json)

    first = service._expand_query("wrongful account access")
    second = service._expand_query("wrongful account access")

    assert first == (
        "wrongful account access unauthorized disclosure financial records bank records"
    )
    assert second == first
    assert len(calls) == 1
    assert calls[0]["model_id"] == service.settings.query_expansion_model


def test_query_expansion_fails_open(monkeypatch):
    service = RetrievalService()
    service.settings.query_expansion_enabled = True
    service.settings.gemini_api_key = "test-key"

    def fail_generate_json(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(service.gemini, "generate_json", fail_generate_json)

    assert service._expand_query("venue facts") == "venue facts"


def test_query_expansion_disabled_without_key(monkeypatch):
    service = RetrievalService()
    service.settings.query_expansion_enabled = True
    service.settings.gemini_api_key = ""

    def fail_if_called(**kwargs):
        raise AssertionError("Gemini should not be called without a key")

    monkeypatch.setattr(service.gemini, "generate_json", fail_if_called)

    assert service._expand_query("governing law") == "governing law"


def test_chunker_ignores_common_legal_abbreviations_for_sentence_boundary():
    text = "Pearson Specter Litt, Inc. entered the agreement. The payment terms follow."
    chunker = SectionAwareChunker(chunk_size_chars=120, chunk_overlap_chars=10)
    target_after_inc = text.index(" entered")

    boundary = chunker._sentence_boundary(text, 0, target_after_inc, len(text))

    assert boundary > text.index("agreement.")


def test_chunker_infers_page_numbers_from_court_page_markers():
    text = (
        "Barker v. Landmark Credit Union - Page 1 Case 2:26-cv-00815-PP Filed 05/08/26\n"
        + ("Introductory allegations. " * 25)
        + "\nBarker v. Landmark Credit Union - Page 23 Case 2:26-cv-00815-PP Filed 05/08/26\n"
        + ("COUNT II Conspiracy Against Civil Rights. " * 25)
    )

    chunks = SectionAwareChunker(chunk_size_chars=250, chunk_overlap_chars=20).chunk(text)

    assert any(chunk.page_number == 23 and "COUNT II" in chunk.text for chunk in chunks)


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
        sections=[
            {
                "key": "payment_terms",
                "edited_content": "Payment is due within thirty days after the invoice is received.",
            }
        ],
    )

    assert updated.status == "reviewed"
    assert len(edits) == 1
    assert (
        updated.content["sections"][0]["content"]
        == "Payment is due within thirty days after the invoice is received."
    )
    assert updated.content["sections"][0]["grounding_score"] == 0.0
    assert updated.overall_grounding_score == 0.0


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
    assert draft.overall_grounding_score == 0.0


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


def test_draft_prompt_injects_date_and_ocr_normalization_rule(db_session):
    service = DraftService(db_session)
    prompt = service._system_prompt("internal_memo", prefs=[], examples=[])

    assert "Do not invent filing, review, or memo dates" in prompt
    assert "Landmark Credit Union" in prompt
    assert "TO: Senior Partner" in prompt
    assert "FROM: Legal Document Analyst" in prompt


def test_internal_memo_prompt_deepens_legal_complaint_claims(db_session):
    service = DraftService(db_session)
    doc = Document(
        filename="complaint.pdf",
        stored_path="complaint.pdf",
        content_type="application/pdf",
        status=DocumentStatus.completed,
        document_type="legal_complaint",
        pipeline_version="test",
    )

    prompt = service._user_prompt(doc, "internal_memo", {"claims": ["COUNT I RFPA"]}, [])

    assert "enumerate each cause of action" in prompt
    assert "statute or legal basis" in prompt


def test_draft_chunk_selection_diversifies_long_documents(db_session):
    service = DraftService(db_session)
    candidates = [
        RetrievedChunk(
            chunk_id=f"chunk-{idx}",
            document_id="doc-1",
            chunk_index=idx * 10,
            page_number=1,
            section_header=None,
            jurisdiction=None,
            text=f"chunk {idx}",
            similarity_score=1.0 - (idx * 0.01),
        )
        for idx in range(12)
    ]

    selected = service._select_diverse_chunks(candidates)

    assert len(selected) == service.settings.draft_max_chunks
    assert len({chunk.chunk_index // 20 for chunk in selected}) > 2
