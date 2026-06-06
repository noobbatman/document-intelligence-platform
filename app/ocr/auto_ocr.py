"""Auto-routing OCR: reruns low-confidence documents with TrOCR."""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.ocr.base import OCRProvider, OCRResult

logger = logging.getLogger(__name__)


class AutoOCRProvider(OCRProvider):
    def extract(self, path: str) -> OCRResult:
        settings = get_settings()
        primary = _primary_provider(settings.ocr_engine_primary)
        result = primary.extract(path)
        avg_conf = _avg_confidence(result)
        if avg_conf < settings.handwriting_confidence_threshold:
            try:
                from app.ocr.trocr_ocr import TrOCRProvider

                trocr_result = TrOCRProvider().extract(path)
                trocr_result.metadata["primary_engine"] = result.metadata.get("engine")
                trocr_result.metadata["primary_average_confidence"] = avg_conf
                return trocr_result
            except Exception as exc:
                logger.warning("trocr_fallback_failed", extra={"error": str(exc)})
                result.metadata["trocr_fallback_failed"] = str(exc)
                result.metadata["primary_average_confidence"] = avg_conf
        return result


def _primary_provider(engine: str) -> OCRProvider:
    if engine.lower() == "paddle":
        from app.ocr.paddle_ocr import PaddleOCRProvider

        return PaddleOCRProvider()
    from app.ocr.tesseract_ocr import TesseractOCRProvider

    return TesseractOCRProvider()


def _avg_confidence(result: OCRResult) -> float:
    words = result.words
    if not words:
        return 1.0
    return sum(word.confidence for word in words) / len(words)
