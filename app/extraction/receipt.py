"""Receipt extractor."""

import re
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.utils.text import find_snippet, normalize_amount, regex_search


class ReceiptExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text

        store_name = regex_search(r"^([A-Z][A-Za-z0-9 &\.\-]{2,40})$", text, flags=re.MULTILINE)
        receipt_date = regex_search(
            r"(?:date|time)\s*[:#]?\s*([0-9]{1,2}[\s\/\-\.][A-Za-z]{3,9}[\s\/\-\.][0-9]{2,4}|[0-9]{1,2}[\/\-\.][0-9]{1,2}[\/\-\.][0-9]{2,4})",
            text,
        )
        receipt_number = regex_search(
            r"(?:receipt|txn|transaction)\s*(?:no\.?|#|number)?\s*[:#]?\s*([A-Z0-9\-]+)", text
        )
        subtotal = normalize_amount(
            regex_search(r"subtotal\s*[:#]?\s*([$€£]?\s?[\d,]+\.\d{2})", text)
        )
        tax = normalize_amount(
            regex_search(r"(?:tax|vat|gst)[^\n]{0,30}?([$€£]?\s?[\d,]+\.\d{2})", text)
        )
        total = normalize_amount(
            regex_search(
                r"(?:total(?:\s+paid)?|amount\s+paid|grand\s+total)\s*[:#]?\s*([$€£]?\s?[\d,]+\.\d{2})",
                text,
            )
        )
        payment_method = regex_search(
            r"(?:paid\s+by|payment\s+method|tender)\s*[:#]?\s*([A-Za-z ]+)", text
        )
        cashier = regex_search(r"(?:cashier|served\s+by|operator)\s*[:#]?\s*([A-Za-z0-9 ]+)", text)

        fields: dict[str, Any] = {
            "store_name": store_name,
            "receipt_date": receipt_date,
            "receipt_number": receipt_number,
            "subtotal": subtotal,
            "tax": tax,
            "total_amount": total,
            "payment_method": payment_method,
            "cashier": cashier,
        }
        snippets = {
            name: find_snippet(text, str(value)) if value is not None else None
            for name, value in fields.items()
        }
        entities = extract_entities(ocr_result)
        return ExtractionOutput(
            document_type="receipt",
            fields=fields,
            entities=entities,
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": ["total_amount"],
            },
        )
