from pathlib import Path

import pytesseract

from app.core.config import get_settings
from app.ocr.base import OCRPage, OCRProvider, OCRResult, OCRWord
from app.ocr.preprocessing import preprocess_for_ocr
from app.utils.pdf import ensure_images
from app.utils.text import normalize_whitespace


class TesseractOCRProvider(OCRProvider):
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
                    binarize=True,
                )
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            words: list[OCRWord] = []
            text_lines: list[str] = []
            for idx, raw_text in enumerate(data["text"]):
                token = normalize_whitespace(raw_text)
                if not token:
                    continue
                raw_conf = data["conf"][idx]
                conf = 0.0 if str(raw_conf) == "-1" else max(0.0, min(1.0, float(raw_conf) / 100.0))
                bbox = [
                    float(data["left"][idx]),
                    float(data["top"][idx]),
                    float(data["left"][idx] + data["width"][idx]),
                    float(data["top"][idx] + data["height"][idx]),
                ]
                words.append(OCRWord(text=token, confidence=conf, page_number=page_index, bbox=bbox))
                text_lines.append(token)
                all_confidences.append(conf)
            page_text = " ".join(text_lines)
            all_text.append(page_text)
            page_conf = sum(word.confidence for word in words) / len(words) if words else 0.0
            pages.append(OCRPage(page_number=page_index, text=page_text, words=words, confidence=page_conf))
        avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        return OCRResult(
            text="\f".join(all_text),
            pages=pages,
            metadata={
                "page_count": len(pages),
                "average_confidence": avg_conf,
                "engine": "tesseract",
            },
        )
