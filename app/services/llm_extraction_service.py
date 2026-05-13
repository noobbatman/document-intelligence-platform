"""LLM-fallback extraction service."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_INVOICE_SCHEMA = {
    "invoice_number": "string — the invoice ID (e.g. INV-2024-001)",
    "invoice_date": "string — issue date in any format found",
    "due_date": "string — payment due date",
    "vendor_name": "string — company or person issuing the invoice",
    "customer_name": "string — company or person being billed",
    "subtotal": "number — pre-tax subtotal amount (numeric only)",
    "tax": "number — tax/VAT amount (numeric only)",
    "total_amount": "number — final total due (numeric only)",
    "currency": "string — currency code (GBP/USD/EUR etc.)",
    "payment_terms": "string — e.g. Net 30",
    "purchase_order": "string — PO number if present",
}

_BANK_STATEMENT_SCHEMA = {
    "account_number": "string — the account identifier",
    "account_holder": "string — name of account holder",
    "iban": "string — IBAN if present",
    "sort_code": "string — UK sort code if present",
    "statement_period": "string — period covered",
    "opening_balance": "number — balance at start of period (numeric only)",
    "closing_balance": "number — balance at end of period (numeric only)",
    "available_balance": "number — available balance (numeric only)",
    "total_debits": "number — sum of debits (numeric only)",
    "total_credits": "number — sum of credits (numeric only)",
}

_SCHEMAS: dict[str, dict[str, str]] = {
    "invoice": _INVOICE_SCHEMA,
    "bank_statement": _BANK_STATEMENT_SCHEMA,
}


def _build_unknown_prompt(ocr_text: str) -> str:
    truncated = ocr_text[:3000] if len(ocr_text) > 3000 else ocr_text
    return f"""You are a document analysis assistant.
Given the following document text, do two things:
1. Identify the document type (e.g. "browser_extension_plan", "study_notes", "payslip", "medical_report")
2. Extract all meaningful structured fields you can find

Return JSON only. Example:
{{"detected_type": "technical_plan", "project_name": "...", "api_used": "...", "timeline_weeks": 5}}

Document text:
{truncated}
"""


def _build_prompt(document_type: str, ocr_text: str, failed_fields: list[str]) -> str:
    schema = _SCHEMAS.get(document_type, _INVOICE_SCHEMA)
    fields_to_extract = {k: v for k, v in schema.items() if k in failed_fields} or schema
    schema_str = json.dumps(fields_to_extract, indent=2)
    truncated = ocr_text[:6000] if len(ocr_text) > 6000 else ocr_text

    return f"""You are a precise document field extractor. Extract ONLY the requested fields from this {document_type.replace("_", " ")} text.

DOCUMENT TEXT:
{truncated}

FIELDS TO EXTRACT (with descriptions):
{schema_str}

RULES:
- Return ONLY a valid JSON object with the field names as keys
- Use null for fields not found in the document
- For numeric fields (amounts, balances): return the number as a float, not a string
- Do NOT add any explanation, preamble, or markdown
- Do NOT hallucinate values that are not present in the text

JSON output:"""


class LLMExtractionService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def _enabled(self) -> bool:
        return bool(getattr(self.settings, "llm_extraction_enabled", False))

    @property
    def _threshold(self) -> float:
        return float(getattr(self.settings, "low_confidence_threshold", 0.75))

    @property
    def _unknown_enabled(self) -> bool:
        return bool(getattr(self.settings, "llm_unknown_extraction_enabled", True))

    def _call_llm(self, prompt: str, *, max_tokens: int = 512) -> dict[str, Any]:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(raw)

    def _extract_unknown_document(self, ocr_text: str) -> dict[str, Any]:
        if not self._unknown_enabled:
            return {}

        prompt = _build_unknown_prompt(ocr_text)
        try:
            extracted = self._call_llm(prompt, max_tokens=768)
            if isinstance(extracted, dict):
                return extracted
        except Exception as exc:
            logger.warning("llm_unknown_extraction_failed", extra={"error": str(exc)})
        return {}

    def extract_failed_fields(
        self,
        document_type: str,
        ocr_text: str,
        current_fields: dict[str, Any],
        low_confidence_field_names: list[str],
    ) -> dict[str, Any]:
        if not self._enabled or not low_confidence_field_names or document_type not in _SCHEMAS:
            return {}

        null_fields = [
            f
            for f in low_confidence_field_names
            if current_fields.get(f) is None and f in _SCHEMAS.get(document_type, {})
        ]
        if not null_fields:
            return {}

        prompt = _build_prompt(document_type, ocr_text, null_fields)

        try:
            extracted = self._call_llm(prompt)
            return {k: v for k, v in extracted.items() if k in null_fields and v is not None}
        except Exception as exc:
            logger.warning(
                "llm_extraction_failed", extra={"error": str(exc), "doc_type": document_type}
            )
            return {}

    def enrich_fields(
        self,
        document_type: str,
        ocr_text: str,
        current_fields: dict[str, Any],
        confidence_threshold: float | None = None,
        field_confidences: list | None = None,
    ) -> dict[str, Any]:
        if document_type == "unknown":
            return self._extract_unknown_document(ocr_text)

        if not self._enabled:
            return current_fields

        threshold = confidence_threshold if confidence_threshold is not None else self._threshold

        low_conf_names: list[str] = []
        if field_confidences:
            low_conf_names = [
                fc.name if hasattr(fc, "name") else fc.get("name", "")
                for fc in field_confidences
                if (fc.confidence if hasattr(fc, "confidence") else fc.get("confidence", 1.0))
                < threshold
            ]

        llm_results = self.extract_failed_fields(
            document_type=document_type,
            ocr_text=ocr_text,
            current_fields=current_fields,
            low_confidence_field_names=low_conf_names or list(current_fields.keys()),
        )

        if llm_results:
            enriched = dict(current_fields)
            enriched.update(llm_results)
            logger.info(
                "llm_enriched_fields",
                extra={"fields": list(llm_results.keys()), "doc_type": document_type},
            )
            return enriched

        return current_fields
