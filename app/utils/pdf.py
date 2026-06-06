from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image

from app.core.config import get_settings


def render_pdf_pages(path: Path, zoom: float | None = None) -> list[Image.Image]:
    images: list[Image.Image] = []
    zoom = zoom if zoom is not None else get_settings().pdf_render_zoom
    document = fitz.open(path)
    matrix = fitz.Matrix(zoom, zoom)
    try:
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
            images.append(image)
    finally:
        document.close()
    return images


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def ensure_images(path: Path) -> Iterable[Image.Image]:
    if is_pdf(path):
        return render_pdf_pages(path)
    return [Image.open(path).convert("RGB")]
