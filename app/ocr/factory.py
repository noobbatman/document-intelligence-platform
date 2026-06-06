from app.core.config import get_settings
from app.ocr.base import OCRProvider
from app.ocr.paddle_ocr import PaddleOCRProvider
from app.ocr.tesseract_ocr import TesseractOCRProvider


def get_ocr_provider() -> OCRProvider:
    engine = get_settings().ocr_engine.lower()
    if engine == "paddle":
        return PaddleOCRProvider()
    return TesseractOCRProvider()
