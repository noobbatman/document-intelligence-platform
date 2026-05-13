"""Standard retrieval queries by draft type."""
from __future__ import annotations

DRAFT_QUERIES: dict[str, list[str]] = {
    "internal_memo": [
        "key parties involved and their roles",
        "critical dates deadlines obligations",
        "financial terms payment amounts",
        "risks liability indemnification",
        "governing law jurisdiction",
    ],
    "case_fact_summary": [
        "facts of the case parties involved",
        "legal issues questions presented",
        "court ruling holding decision",
        "procedural history timeline",
        "cited statutes regulations precedents",
    ],
    "contract_summary": [
        "contracting parties roles and addresses",
        "effective date term duration renewal",
        "payment terms compensation schedule",
        "termination clauses notice period",
        "intellectual property ownership",
        "confidentiality non-disclosure obligations",
        "indemnification liability limitation",
        "governing law dispute resolution",
    ],
    "notice_summary": [
        "notice type issuing party receiving party",
        "required actions response deadline",
        "legal basis claimed violations",
        "consequences of non-compliance",
    ],
    "document_checklist": [
        "required signatures execution dates",
        "referenced exhibits attachments schedules",
        "conditions precedent requirements",
        "missing or incomplete fields",
    ],
}

DOCUMENT_TYPE_QUERIES: dict[str, list[str]] = {
    "legal_complaint": [
        "case caption plaintiffs defendants court case number",
        "jurisdiction venue factual allegations",
        "causes of action counts claims statutes",
        "subpoena allegations bank records RFPA privacy claim",
        "conspiracy allegations actors agreement overt acts",
        "void subpoena improper service legal basis",
        "prayer for relief damages declaratory injunctive relief",
        "jury demand requested relief",
    ],
}
