from __future__ import annotations

from app.extraction.affidavit import AffidavitExtractor
from app.extraction.case_brief import CaseBriefExtractor
from app.extraction.contract import ContractExtractor
from app.extraction.legal_complaint import LegalComplaintExtractor
from app.extraction.legal_notice import LegalNoticeExtractor
from app.extraction.unknown import UnknownExtractor
from app.ocr.base import OCRResult


def _ocr(text: str) -> OCRResult:
    return OCRResult(
        text=text,
        words=[],
        metadata={"average_confidence": 0.91, "page_count": 1, "engine": "unit"},
    )


def test_contract_extractor_captures_core_clauses() -> None:
    text = """
    MASTER SERVICES AGREEMENT
    Effective Date: January 5, 2026
    This Agreement is between Acme Corporation, a Delaware corporation and Globex Inc,
    a services company. Governing Law: the laws of the State of New York.
    Contract Value: $125,000.00
    Payment Terms: Client shall pay invoices within thirty days.

    Confidentiality. Each party shall keep confidential information private.
    Intellectual Property. Work product belongs to Acme.
    Indemnification. Supplier shall indemnify and hold harmless Acme.
    Dispute Resolution. Any dispute shall proceed to arbitration.
    Termination Date: 12/31/2026
    Either party may terminate on 30 days written notice.
    Signature: Jane Smith Date: January 6, 2026
    Licensor: Acme Corporation
    """

    result = ContractExtractor().extract(_ocr(text))

    assert result.document_type == "contract"
    assert result.fields["effective_date"] == "January 5, 2026"
    assert result.fields["governing_law"] == "New York"
    assert result.fields["contract_value"] == "$125,000.00"
    assert result.fields["notice_period"] == "30"
    assert result.fields["confidentiality_clause"]["present"] is True
    assert result.fields["indemnification"]["present"] is True
    assert result.fields["dispute_resolution"]
    assert result.fields["signatures"][0]["name"].startswith("Jane Smith")
    assert {"name": "Acme Corporation", "role": "licensor"} in result.fields["parties"]
    assert "effective_date" in result.metadata["required_fields"]


def test_legal_complaint_extractor_captures_caption_claims_and_relief() -> None:
    text = """
    UNITED STATES DISTRICT COURT EASTERN DISTRICT OF WISCONSIN
    Jane Doe, Plaintiff, v. Landmark Credit Union, Defendant.
    Civil Action No. 2:26-cv-00815-PP
    COMPLAINT FOR DAMAGES AND INJUNCTIVE RELIEF
    Filed: May 8, 2026

    JURISDICTION AND VENUE
    This Court has jurisdiction under 28 U.S.C. § 1331 and 28 U.S.C. § 1343.
    Venue is proper under 28 U.S.C. § 1391(b).

    COUNT I Violation of the Right to Financial Privacy Act
    COUNT II Civil Conspiracy

    WHEREFORE, Plaintiff respectfully requests declaratory relief; compensatory damages;
    and injunctive relief preventing further disclosure.
    DEMAND FOR JURY TRIAL
    """

    result = LegalComplaintExtractor().extract(_ocr(text))

    assert result.fields["case_number"] == "2:26-cv-00815-PP"
    assert "Jane Doe" in result.fields["plaintiffs"][0]
    assert result.fields["defendants"] == ["Landmark Credit Union"]
    assert any("Violation" in claim for claim in result.fields["claims"])
    assert result.fields["jurisdiction"].startswith("Federal question")
    assert result.fields["venue"] == "Venue alleged under 28 U.S.C. § 1391(b)"
    assert any("Right to Financial Privacy Act" in statute for statute in result.fields["statutes"])
    assert result.fields["jury_demand"] is True
    assert result.fields["relief_sought"]


def test_legal_notice_extractor_captures_actions_and_references() -> None:
    text = """
    CEASE AND DESIST NOTICE
    From: Acme Legal Department
    To: Globex Products LLC
    Issue Date: May 20, 2026
    Response Deadline: June 1, 2026
    Jurisdiction: New York
    This notice references Agreement No. MSA-2026 and Exhibit A-12.
    You must stop using the protected mark immediately.
    We demand that you preserve all documents related to the campaign.
    """

    result = LegalNoticeExtractor().extract(_ocr(text))

    assert result.fields["notice_type"] == "cease_and_desist"
    assert result.fields["issuing_party"] == "Acme Legal Department"
    assert result.fields["receiving_party"] == "Globex Products LLC"
    assert result.fields["response_deadline"] == "June 1, 2026"
    assert "MSA-2026" in result.fields["referenced_documents"]
    assert any("stop using" in action for action in result.fields["required_actions"])


def test_case_brief_extractor_captures_issues_holding_and_statutes() -> None:
    text = """
    Case Name: Stark Industries v. Wayne Enterprises
    Case No: 24-CV-1001
    Court: Supreme Court of New York
    Jurisdiction: New York County
    Filing Date: January 2, 2024
    Decision Date: March 4, 2024
    Plaintiff: Stark Industries
    Defendant: Wayne Enterprises

    Legal Issues: Whether the service agreement was terminated; Whether section 101 applies?
    Holding: The court held that the termination clause controlled the dispute.
    The decision cites 15 U.S.C. § 78j and New York Code § 101.
    """

    result = CaseBriefExtractor().extract(_ocr(text))

    assert result.fields["case_name"] == "Stark Industries v. Wayne Enterprises"
    assert result.fields["case_number"] == "24-CV-1001"
    assert result.fields["court"] == "Supreme Court of New York"
    assert result.fields["legal_issues"]
    assert "termi" in result.fields["holding"]
    assert any("15 U.S.C." in statute for statute in result.fields["cited_statutes"])


def test_affidavit_and_unknown_extractors_return_expected_contracts() -> None:
    affidavit_text = """
    Affidavit of Maria Garcia
    Role: Compliance Manager
    State of California
    Sworn on May 1, 2026 before Notary Public Name: Daniel Reed.
    I declare under penalty of perjury that the attached records are accurate.
    """

    affidavit = AffidavitExtractor().extract(_ocr(affidavit_text))
    unknown = UnknownExtractor().extract(_ocr("unclassified notes"))

    assert affidavit.fields["declarant_name"] == "Maria Garcia"
    assert affidavit.fields["declarant_role"] == "Compliance Manager"
    assert affidavit.fields["notary_name"].startswith("Daniel Reed")
    assert affidavit.fields["statement_summary"]
    assert unknown.document_type == "unknown"
    assert unknown.metadata["extraction_mode"] == "llm_open_ended"
