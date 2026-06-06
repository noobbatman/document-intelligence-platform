from pathlib import Path

import numpy as np

from app.core.config import get_settings
from app.ocr.base import OCRPage, OCRProvider, OCRResult, OCRWord
from app.ocr.preprocessing import preprocess_for_ocr
from app.utils.pdf import ensure_images
from app.utils.text import normalize_whitespace


class PaddleOCRProvider(OCRProvider):
    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self._engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)

    def extract(self, path: str) -> OCRResult:
        settings = get_settings()
        pages: list[OCRPage] = []
        all_text: list[str] = []
        all_confidences: list[float] = []
        for page_index, image in enumerate(ensure_images(Path(path)), start=1):
            if settings.ocr_preprocess:
                image = preprocess_for_ocr(
                    image,
                    deskew=settings.ocr_deskew,
                    denoise=settings.ocr_denoise,
                    enhance_contrast=settings.ocr_enhance_contrast,
                    binarize=False,
                )
            result = self._engine.ocr(np.asarray(image), cls=True)
            words: list[OCRWord] = []
            page_tokens: list[str] = []
            lines = result[0] if result else []
            for line in lines:
                bbox_points, (raw_text, raw_conf) = line
                token = normalize_whitespace(raw_text)
                if not token:
                    continue
                xs = [float(point[0]) for point in bbox_points]
                ys = [float(point[1]) for point in bbox_points]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
                conf = max(0.0, min(1.0, float(raw_conf)))
                words.append(
                    OCRWord(text=token, confidence=conf, page_number=page_index, bbox=bbox)
                )
                page_tokens.append(token)
                all_confidences.append(conf)
            page_text = " ".join(page_tokens)
            all_text.append(page_text)
            page_conf = sum(word.confidence for word in words) / len(words) if words else 0.0
            pages.append(
                OCRPage(page_number=page_index, text=page_text, words=words, confidence=page_conf)
            )
        avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        return OCRResult(
            text="\f".join(all_text),
            pages=pages,
            metadata={
                "page_count": len(pages),
                "average_confidence": avg_conf,
                "engine": "paddleocr",
            },
        )
