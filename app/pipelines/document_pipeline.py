"""Document pipeline: OCR → normalize → classify → extract → validate → score → package."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.classification.hybrid_classifier import HybridDocumentClassifier
from app.core.config import get_settings
from app.extraction.defined_terms import extract_defined_terms
from app.extraction.factory import get_extractor
from app.ocr.factory import get_ocr_provider
from app.pipelines.confidence import ConfidenceScorer
from app.utils.text import normalize_ocr_artifacts
from app.utils.validators import run_validators


class DocumentPipeline:
    def __init__(self) -> None:
        settings = get_settings()
        self.ocr_provider = get_ocr_provider()
        self.classifier = HybridDocumentClassifier()
        self.scorer = ConfidenceScorer(settings.low_confidence_threshold)
        self.settings = settings

    def run(self, path: str) -> dict[str, Any]:
        # 1. OCR
        ocr_result = self.ocr_provider.extract(path)

        # 2. Normalize OCR artifacts for downstream classification/extraction
        raw_text = ocr_result.text
        normalized_text = normalize_ocr_artifacts(raw_text)
        ocr_result.text = normalized_text

        # 3. Classify
        classification = self.classifier.classify(normalized_text)
        extractor = get_extractor(classification.label)

        # 4. Extract header fields
        extracted = extractor.extract(ocr_result)
        snippets = extracted.metadata.get("field_snippets", {})
        required_fields = extracted.metadata.get("required_fields", [])
        page_map = extracted.metadata.get("field_page_map", {})
        bbox_map = extracted.metadata.get("field_bbox_map", {})

        detected_document_type: str | None = None

        # 5. Schema-driven extraction already performs any configured LLM fill.
        fields = dict(extracted.fields)
        detected_document_type = fields.pop("detected_type", None)
        if detected_document_type:
            extracted.document_type = detected_document_type
        defined_terms = extract_defined_terms(
            normalized_text,
            fields=fields,
            existing=extracted.defined_terms,
        )

        # 6. Field validation
        validation_results = run_validators(extracted.document_type, fields)

        # 7. Confidence scoring
        ocr_confidence = ocr_result.metadata.get("average_confidence", 0.0)
        field_confidences = self.scorer.score_fields(
            fields=fields,
            snippets=snippets,
            ocr_confidence=ocr_confidence,
            classifier_confidence=classification.confidence,
            required_fields=required_fields,
        )
        document_confidence = self.scorer.score_document(
            field_confidences=field_confidences,
            classifier_confidence=classification.confidence,
            ocr_confidence=ocr_confidence,
            required_fields=required_fields,
        )

        # 8. Annotate low-confidence fields with page evidence
        low_conf_fields = []
        for fc in field_confidences:
            if fc.requires_review:
                val_reason = next(
                    (
                        v["reason"]
                        for v in validation_results
                        if v["field"] == fc.name and not v["valid"]
                    ),
                    None,
                )
                low_conf_fields.append(
                    {
                        **fc.model_dump(),
                        "page_number": page_map.get(fc.name, 1),
                        "bbox": bbox_map.get(fc.name),
                        "validation_reason": val_reason,
                    }
                )

        export_payload = {
            "document_type": extracted.document_type,
            "detected_document_type": detected_document_type,
            "schema_version": "2.0",
            "source_file": Path(path).name,
            "fields": fields,
            "defined_terms": defined_terms,
            "entities": extracted.entities,
            "tables": extracted.tables,
            "field_confidences": [fc.model_dump() for fc in field_confidences],
            "document_confidence": document_confidence,
            "validation_results": validation_results,
        }

        return {
            "document_type": extracted.document_type,
            "detected_document_type": detected_document_type,
            "classifier_confidence": classification.confidence,
            "document_confidence": document_confidence,
            "ocr_text": raw_text,
            "ocr_metadata": ocr_result.metadata,
            "raw_payload": {
                "classification": asdict(classification),
                "fields": fields,
                "defined_terms": defined_terms,
                "entities": extracted.entities,
                "tables": extracted.tables,
            },
            "normalized_payload": {
                "fields": fields,
                "defined_terms": defined_terms,
                "entities": extracted.entities,
                "tables": extracted.tables,
            },
            "export_payload": export_payload,
            "extraction_metadata": {
                "field_snippets": snippets,
                "required_fields": required_fields,
                "extraction_mode": extracted.metadata.get("extraction_mode"),
                "pipeline_version": self.settings.pipeline_version,
                "defined_terms_count": len(defined_terms),
                "validation_results": validation_results,
            },
            "low_confidence_fields": low_conf_fields,
        }
