from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.ocr.base import OCRResult


@dataclass
class ExtractionOutput:
    document_type: str
    fields: dict[str, Any]
    entities: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    metadata: dict[str, Any]


class Extractor(ABC):
    @abstractmethod
    def extract(self, ocr_result: OCRResult) -> ExtractionOutput:
        raise NotImplementedError
