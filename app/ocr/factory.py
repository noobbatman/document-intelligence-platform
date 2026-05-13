from app.core.config import get_settings
from app.ocr.base import OCRProvider


def get_ocr_provider() -> OCRProvider:
    engine = get_settings().ocr_engine.lower()
    if engine == "paddle":
        from app.ocr.paddle_ocr import PaddleOCRProvider

        return PaddleOCRProvider()
    if engine == "trocr":
        from app.ocr.trocr_ocr import TrOCRProvider

        return TrOCRProvider()
    if engine == "auto":
        from app.ocr.auto_ocr import AutoOCRProvider

        return AutoOCRProvider()
    from app.ocr.tesseract_ocr import TesseractOCRProvider

    return TesseractOCRProvider()
