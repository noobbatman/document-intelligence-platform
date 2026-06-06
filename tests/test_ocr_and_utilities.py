from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from app.ocr.auto_ocr import AutoOCRProvider, _avg_confidence, _primary_provider
from app.ocr.base import OCRResult, OCRWord
from app.ocr.paddle_ocr import PaddleOCRProvider
from app.ocr.preprocessing import _deskew, _pillow_preprocess, preprocess_for_ocr
from app.ocr.tesseract_ocr import TesseractOCRProvider
from app.ocr.trocr_ocr import TrOCRProvider, _iter_boxes, _looks_like_box
from app.utils.pdf import ensure_images, is_pdf
from app.utils.text import (
    deep_set,
    find_snippet,
    normalize_amount,
    normalize_ocr_artifacts,
    regex_search,
)


def _image() -> Image.Image:
    return Image.new("RGB", (24, 16), color="white")


def test_preprocessing_pillow_fallback_and_helpers(tmp_path) -> None:
    image = _image()
    png_path = tmp_path / "page.png"
    image.save(png_path)

    processed = _pillow_preprocess(image, enhance_contrast=True)
    no_cv_processed = preprocess_for_ocr(image, deskew=False, denoise=False, binarize=False)

    assert processed.mode == "RGB"
    assert no_cv_processed.mode == "RGB"
    assert is_pdf(tmp_path / "brief.pdf") is True
    assert is_pdf(png_path) is False
    assert len(list(ensure_images(png_path))) == 1


def test_text_utility_edge_cases() -> None:
    text = "l N V 0 l C E N0. INV-1O23 {name:str} Total: $1,250.50"
    normalized = normalize_ocr_artifacts(text)
    payload: dict = {}

    deep_set(payload, "fields.invoice.total", 1250.5)

    assert "INVOICE" in normalized
    assert "INV-1023" in normalized
    assert normalize_amount("$1,250.50") == 1250.5
    assert normalize_amount("{bad}") is None
    assert regex_search(r"Total:\s*([$0-9,.]+)", normalized) == "$1,250.50"
    assert find_snippet(normalized, "Total")
    assert payload == {"fields": {"invoice": {"total": 1250.5}}}


def test_auto_ocr_uses_primary_when_confidence_is_high(monkeypatch) -> None:
    primary_result = OCRResult(
        text="typed text",
        words=[OCRWord(text="typed", confidence=0.95, page_number=1, bbox=[0, 0, 1, 1])],
        metadata={"engine": "tesseract"},
    )
    monkeypatch.setattr(
        "app.ocr.auto_ocr._primary_provider",
        lambda engine: SimpleNamespace(extract=lambda path: primary_result),
    )

    result = AutoOCRProvider().extract("file.pdf")

    assert result is primary_result
    assert _avg_confidence(primary_result) == 0.95
    assert _avg_confidence(OCRResult(text="", words=[], metadata={})) == 1.0


def test_auto_ocr_falls_back_to_trocr_and_records_primary(monkeypatch) -> None:
    primary_result = OCRResult(
        text="handwriting",
        words=[OCRWord(text="?", confidence=0.2, page_number=1, bbox=[0, 0, 1, 1])],
        metadata={"engine": "paddleocr"},
    )
    trocr_result = OCRResult(text="clear handwriting", words=[], metadata={"engine": "trocr"})
    monkeypatch.setattr(
        "app.ocr.auto_ocr._primary_provider",
        lambda engine: SimpleNamespace(extract=lambda path: primary_result),
    )
    monkeypatch.setattr("app.ocr.trocr_ocr.TrOCRProvider.extract", lambda self, path: trocr_result)

    result = AutoOCRProvider().extract("handwritten.pdf")

    assert result is trocr_result
    assert result.metadata["primary_engine"] == "paddleocr"
    assert result.metadata["primary_average_confidence"] == 0.2


def test_primary_provider_selects_paddle_or_tesseract(monkeypatch) -> None:
    monkeypatch.setattr("app.ocr.paddle_ocr.PaddleOCRProvider", lambda: "paddle-provider")
    monkeypatch.setattr("app.ocr.tesseract_ocr.TesseractOCRProvider", lambda: "tesseract-provider")

    assert _primary_provider("paddle") == "paddle-provider"
    assert _primary_provider("tesseract") == "tesseract-provider"


def test_tesseract_provider_builds_pages_and_confidence(monkeypatch) -> None:
    monkeypatch.setattr("app.ocr.tesseract_ocr.ensure_images", lambda path: [_image()])
    monkeypatch.setattr(
        "app.ocr.tesseract_ocr.preprocess_for_ocr",
        lambda image, **kwargs: image,
    )
    monkeypatch.setattr(
        "pytesseract.image_to_data",
        lambda image, output_type: {
            "text": ["Hello", " ", "World"],
            "conf": ["90", "-1", "70"],
            "left": [1, 0, 10],
            "top": [2, 0, 4],
            "width": [5, 0, 6],
            "height": [7, 0, 8],
        },
    )

    result = TesseractOCRProvider().extract("scan.png")

    assert result.text == "Hello World"
    assert result.pages[0].confidence == pytest.approx(0.8)
    assert result.metadata["engine"] == "tesseract"


def test_paddle_provider_extracts_tokens_without_importing_engine(monkeypatch) -> None:
    provider = PaddleOCRProvider.__new__(PaddleOCRProvider)
    provider._engine = SimpleNamespace(
        ocr=lambda image, cls: [
            [
                (
                    [[0, 0], [10, 0], [10, 5], [0, 5]],
                    ("Paddle text", 0.88),
                )
            ]
        ]
    )
    monkeypatch.setattr("app.ocr.paddle_ocr.ensure_images", lambda path: [_image()])
    monkeypatch.setattr("app.ocr.paddle_ocr.preprocess_for_ocr", lambda image, **kwargs: image)

    result = provider.extract("scan.png")

    assert result.text == "Paddle text"
    assert result.words[0].bbox == [0.0, 0.0, 10.0, 5.0]
    assert result.metadata["average_confidence"] == 0.88


def test_trocr_box_helpers_and_page_processing(monkeypatch) -> None:
    box = [[0, 0], [12, 0], [12, 6], [0, 6]]
    assert _looks_like_box(box) is True
    assert _iter_boxes([box]) == [box]
    assert _iter_boxes([[box]]) == [box]
    assert _iter_boxes([]) == []

    class FakeProcessor:
        def __call__(self, images, return_tensors):
            return SimpleNamespace(pixel_values="pixels")

        def batch_decode(self, generated_ids, skip_special_tokens):
            return [" handwritten line "]

    class FakeModel:
        def generate(self, pixel_values):
            return ["ids"]

    @contextmanager
    def no_grad():
        yield

    monkeypatch.setattr(
        "app.ocr.trocr_ocr._load_detector", lambda: SimpleNamespace(ocr=lambda *_, **__: [[box]])
    )
    monkeypatch.setattr("app.ocr.trocr_ocr._processor", FakeProcessor())
    monkeypatch.setattr("app.ocr.trocr_ocr._model", FakeModel())
    monkeypatch.setitem(__import__("sys").modules, "torch", SimpleNamespace(no_grad=no_grad))

    text, words = TrOCRProvider()._process_page(_image(), 3)

    assert text == "handwritten line"
    assert words[0].page_number == 3


def test_deskew_returns_original_for_sparse_or_failed_inputs(monkeypatch) -> None:
    import numpy as np

    sparse = np.full((4, 4), 255, dtype=np.uint8)
    assert _deskew(sparse) is sparse

    class BrokenCV2:
        @staticmethod
        def minAreaRect(coords):
            raise RuntimeError("bad geometry")

    monkeypatch.setitem(__import__("sys").modules, "cv2", BrokenCV2)
    dense = np.zeros((10, 10), dtype=np.uint8)

    assert _deskew(dense) is dense
