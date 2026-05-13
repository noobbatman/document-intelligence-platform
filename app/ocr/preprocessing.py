"""Image preprocessing pipeline to improve OCR quality on scanned, low-resolution,
and inconsistently formatted documents."""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)


def preprocess_for_ocr(
    image: Image.Image,
    *,
    deskew: bool = True,
    denoise: bool = True,
    enhance_contrast: bool = True,
    binarize: bool = False,
) -> Image.Image:
    """Apply a sequence of image corrections that improve OCR accuracy.

    Works on scanned pages, low-resolution PDFs, and inconsistently
    formatted files. binarize=False by default because PaddleOCR performs
    better on greyscale than hard-binarized images.
    """
    try:
        import cv2
    except ImportError:
        # cv2 unavailable — fall back to Pillow-only path
        return _pillow_preprocess(image, enhance_contrast=enhance_contrast)

    img = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    if denoise:
        # Fast non-local means — effective against salt-and-pepper noise
        # common in scanned docs without over-blurring thin strokes.
        gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    if enhance_contrast:
        # CLAHE improves readability of low-contrast or faded text without
        # blowing out already-clear regions.
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

    if deskew:
        gray = _deskew(gray)

    if binarize:
        # Adaptive threshold handles uneven lighting across the page.
        gray = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=15,
            C=8,
        )

    return Image.fromarray(gray).convert("RGB")


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Detect and correct page skew using the minimum bounding rectangle of
    dark (text) pixels. Skips correction when tilt is below 0.5°."""
    try:
        import cv2
        coords = np.column_stack(np.where(gray < 128))
        if len(coords) < 50:
            return gray
        rect = cv2.minAreaRect(coords.astype(np.float32))
        angle = rect[-1]
        # minAreaRect returns angles in (-90, 0]; convert to (-45, 45]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) < 0.5:
            return gray
        h, w = gray.shape
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            gray, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
    except Exception as exc:
        logger.debug("deskew_failed", extra={"error": str(exc)})
        return gray


def _pillow_preprocess(image: Image.Image, *, enhance_contrast: bool) -> Image.Image:
    """Minimal preprocessing using only Pillow (cv2 unavailable)."""
    image = image.convert("L")
    if enhance_contrast:
        from PIL import ImageOps
        image = ImageOps.autocontrast(image, cutoff=1)
    image = image.filter(ImageFilter.SHARPEN)
    return image.convert("RGB")
