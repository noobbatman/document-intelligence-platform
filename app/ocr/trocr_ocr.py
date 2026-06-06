"""TrOCR provider for handwritten and degraded text."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.config import get_settings
from app.ocr.base import OCRPage, OCRProvider, OCRResult, OCRWord
from app.ocr.preprocessing import preprocess_for_ocr
from app.utils.pdf import ensure_images
from app.utils.text import normalize_whitespace

logger = logging.getLogger(__name__)

_processor = None
_model = None
_detector = None


def _load_trocr() -> None:
    global _processor, _model
    if _processor is None:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel

        _processor = TrOCRProcessor.from_pretrained("microsoft/trocr-base-handwritten")
        _model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-base-handwritten")
        _model.eval()


def _load_detector():
    global _detector
    if _detector is None:
        from paddleocr import PaddleOCR

        _detector = PaddleOCR(use_angle_cls=False, lang="en", rec=False, show_log=False)
    return _detector


class TrOCRProvider(OCRProvider):
    def extract(self, path: str) -> OCRResult:
        _load_trocr()
        settings = get_settings()
        pages: list[OCRPage] = []
        all_text: list[str] = []
        all_confidences: list[float] = []

        for page_number, image in enumerate(ensure_images(Path(path)), start=1):
            if settings.ocr_preprocess:
                image = preprocess_for_ocr(
                    image,
                    deskew=settings.ocr_deskew,
                    denoise=settings.ocr_denoise,
                    enhance_contrast=settings.ocr_enhance_contrast,
                    binarize=False,
                )
            page_text, words = self._process_page(image, page_number)
            all_text.append(page_text)
            all_confidences.extend(word.confidence for word in words)
            page_conf = sum(word.confidence for word in words) / len(words) if words else 0.0
            pages.append(
                OCRPage(page_number=page_number, text=page_text, words=words, confidence=page_conf)
            )

        avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        return OCRResult(
            text="\f".join(all_text),
            pages=pages,
            metadata={
                "page_count": len(pages),
                "average_confidence": avg_conf,
                "engine": "trocr",
            },
        )

    def _process_page(self, image: Image.Image, page_number: int) -> tuple[str, list[OCRWord]]:
        import torch

        detector = _load_detector()
        img_np = np.asarray(image.convert("RGB"))
        result = detector.ocr(img_np, det=True, rec=False, cls=False)

        words: list[OCRWord] = []
        lines: list[str] = []
        for box in _iter_boxes(result):
            xs = [int(point[0]) for point in box]
            ys = [int(point[1]) for point in box]
            x1, y1 = max(0, min(xs)), max(0, min(ys))
            x2, y2 = min(image.width, max(xs)), min(image.height, max(ys))
            if x2 <= x1 or y2 <= y1:
                continue

            crop = image.crop((x1, y1, x2, y2)).convert("RGB")
            pixel_values = _processor(images=crop, return_tensors="pt").pixel_values
            with torch.no_grad():
                generated_ids = _model.generate(pixel_values)
            recognized = normalize_whitespace(
                _processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
            )

            if recognized:
                lines.append(recognized)
                words.append(
                    OCRWord(
                        text=recognized,
                        confidence=0.85,
                        page_number=page_number,
                        bbox=[float(x1), float(y1), float(x2), float(y2)],
                    )
                )

        return " ".join(lines), words


def _iter_boxes(result) -> list:
    if not result:
        return []
    first = result[0]
    if not first:
        return []
    if _looks_like_box(first):
        return result
    return first


def _looks_like_box(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in value)
    )
