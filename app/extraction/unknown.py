from app.extraction.base import ExtractionOutput, Extractor
from app.extraction.entities import extract_entities
from app.ocr.base import OCRResult


class UnknownExtractor(Extractor):
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        return ExtractionOutput(
            document_type="unknown",
            fields={},
            entities=extract_entities(ocr_result),
            tables=[],
            metadata={
                "field_snippets": {},
                "required_fields": [],
                "extraction_mode": "llm_open_ended",
            },
        )
