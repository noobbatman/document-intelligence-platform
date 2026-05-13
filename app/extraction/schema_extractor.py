"""Schema-driven extractor with regex pre-pass and optional Gemini fill-in."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult
from app.rag.gemini_client import GeminiClient
from app.utils.config_loader import load_config
from app.utils.text import find_snippet

SCHEMA_DIR = Path(__file__).with_name("schemas")
logger = logging.getLogger(__name__)


class SchemaDrivenExtractor(Extractor):
    def __init__(self, document_type: str) -> None:
        self.document_type = document_type if schema_exists(document_type) else "unknown"
        self.schema = load_schema(self.document_type)

    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        text = ocr_result.text or ""
        fields = self._regex_prepass(text)
        missing = [
            field
            for field in self.schema.get("fields", [])
            if fields.get(field.get("name")) in (None, "", [])
        ]
        if missing and self.schema.get("llm_fill", True):
            fields.update(self._llm_fill(text, fields, missing))

        field_names = [field.get("name") for field in self.schema.get("fields", [])]
        fields = {name: fields.get(name) for name in field_names if name}
        snippets = {
            name: find_snippet(text, _snippet_value(value)) if value not in (None, "", [], {}) else None
            for name, value in fields.items()
        }
        return ExtractionOutput(
            document_type=str(self.schema.get("document_type") or self.document_type),
            fields=fields,
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": snippets,
                "required_fields": [
                    field["name"]
                    for field in self.schema.get("fields", [])
                    if field.get("required")
                ],
                "extraction_mode": "llm_open_ended" if self.document_type == "unknown" else "schema_regex_llm",
                "schema_path": str(SCHEMA_DIR / f"{self.document_type}.yaml"),
            },
        )

    def _regex_prepass(self, text: str) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for field in self.schema.get("fields", []):
            name = field.get("name")
            if not name:
                continue
            output[name] = self._extract_field(text, field)
        return output

    def _extract_field(self, text: str, field: dict[str, Any]) -> Any:
        field_type = field.get("type", "string")
        patterns = field.get("patterns", [])
        if field_type == "boolean":
            return any(re.search(pattern, text, re.IGNORECASE | re.DOTALL | re.MULTILINE) for pattern in patterns)
        matches: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL | re.MULTILINE):
                value = match.group(1) if match.groups() else match.group(0)
                cleaned = _clean_value(value, field.get("name"))
                if cleaned:
                    matches.append(cleaned)
            if matches and not field.get("all_matches"):
                break
        if field_type == "list":
            return _dedupe(matches)
        if field_type == "number" and matches:
            return _to_number(matches[0])
        return matches[0] if matches else None

    def _llm_fill(self, text: str, current_fields: dict[str, Any], missing: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            payload = GeminiClient().generate_json(
                system_prompt=(
                    "You extract structured document fields. Return only JSON. "
                    "Use null for missing values and do not infer unsupported facts."
                ),
                user_prompt=(
                    f"DOCUMENT TYPE: {self.document_type}\n"
                    f"FIELDS TO FILL: {json.dumps(missing, default=str)}\n"
                    f"CURRENT FIELDS: {json.dumps(current_fields, default=str)}\n\n"
                    f"DOCUMENT TEXT:\n{text[:24000]}\n\n"
                    "Return JSON as {\"fields\": {\"field_name\": value}}."
                ),
            )
        except Exception as exc:
            logger.warning(
                "schema_llm_fill_failed",
                extra={
                    "document_type": self.document_type,
                    "missing_fields": [field.get("name") for field in missing],
                    "error": str(exc),
                },
            )
            return {}
        fields = payload.get("fields", payload)
        return fields if isinstance(fields, dict) else {}


def schema_exists(document_type: str) -> bool:
    return (SCHEMA_DIR / f"{document_type}.yaml").exists()


def load_schema(document_type: str) -> dict[str, Any]:
    path = SCHEMA_DIR / f"{document_type}.yaml"
    if not path.exists():
        path = SCHEMA_DIR / "unknown.yaml"
    return load_config(str(path))


def _clean_value(value: str, field_name: str | None = None) -> str:
    cleaned = re.split(r"\n\s*\n", str(value), maxsplit=1)[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:")
    if field_name == "plaintiffs":
        cleaned = re.sub(r"^.*?\b(?:Civil\s+Action\s+No\.?|Case\s+No\.?|Case)\s+[A-Z0-9:\-\/.]+\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^.*?\bDISTRICT\s+COURT\b\s*", "", cleaned, flags=re.IGNORECASE)
    if field_name == "defendants":
        cleaned = re.split(r"\bCOMPLAINT\b|\bCOUNT\s+(?:I|II|III|IV|V|\d+)\b", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
        cleaned = cleaned.strip(" ,.;:")
    return cleaned


def _snippet_value(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _to_number(value: str) -> float | None:
    try:
        return float(re.sub(r"[^0-9.\-]", "", value))
    except ValueError:
        return None
