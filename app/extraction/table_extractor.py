from collections import defaultdict
from pathlib import Path
from typing import Any

import pdfplumber

from app.ocr.base import OCRResult, OCRWord


class TableExtractor:
    def extract_from_pdf(self, path: str) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        if Path(path).suffix.lower() != ".pdf":
            return tables
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                extracted = page.extract_tables()
                for table in extracted:
                    if not table:
                        continue
                    header, *rows = table
                    tables.append(
                        {
                            "page_number": page_number,
                            "columns": header or [],
                            "rows": rows,
                            "source": "pdfplumber",
                        }
                    )
        return tables

    def extract_from_ocr_words(self, ocr_result: OCRResult) -> list[dict[str, Any]]:
        grouped: list[dict[str, Any]] = []
        for page in ocr_result.pages:
            by_line: dict[int, list[OCRWord]] = defaultdict(list)
            for word in page.words:
                row_key = int(word.bbox[1] // 20) if word.bbox else 0
                by_line[row_key].append(word)
            rows = []
            for _, line_words in sorted(by_line.items()):
                ordered = sorted(line_words, key=lambda item: item.bbox[0] if item.bbox else 0)
                row = [word.text for word in ordered]
                if len(row) >= 3:
                    rows.append(row)
            if rows:
                grouped.append(
                    {
                        "page_number": page.page_number,
                        "columns": [],
                        "rows": rows[:25],
                        "source": "ocr-row-grouping",
                    }
                )
        return grouped
