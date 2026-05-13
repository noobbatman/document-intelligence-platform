"""Section-aware chunking for legal documents."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(slots=True)
class DocumentChunk:
    chunk_index: int
    page_number: int
    section_header: str | None
    text: str
    char_start: int
    char_end: int
    token_count: int


_NUMBERED_HEADER_RE = re.compile(r"^\s*(?:\d+\.|\([a-z]\)|\([ivx]+\))\s+(.{3,120})$", re.IGNORECASE)
_LEGAL_HEADER_RE = re.compile(
    r"^\s*(WHEREAS|NOW,\s+THEREFORE|IN\s+WITNESS\s+WHEREOF|RECITALS|DEFINITIONS|TERM\s+AND\s+TERMINATION)\s*$",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_ABBREVIATIONS = {
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "esq.",
    "inc.",
    "corp.",
    "ltd.",
    "co.",
    "llc.",
    "plc.",
    "no.",
    "nos.",
    "art.",
    "sec.",
    "v.",
    "vs.",
}


class SectionAwareChunker:
    def __init__(
        self,
        *,
        chunk_size_chars: int | None = None,
        chunk_overlap_chars: int | None = None,
        section_detection_enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.chunk_size_chars = chunk_size_chars or settings.chunk_size_chars
        self.chunk_overlap_chars = chunk_overlap_chars or settings.chunk_overlap_chars
        self.section_detection_enabled = (
            settings.section_detection_enabled
            if section_detection_enabled is None
            else section_detection_enabled
        )

    def chunk(self, text: str) -> list[DocumentChunk]:
        text = text or ""
        if not text.strip():
            return []

        sections = self._detect_sections(text) if self.section_detection_enabled else []
        if not sections:
            sections = [(None, 0, len(text))]

        chunks: list[DocumentChunk] = []
        for header, start, end in sections:
            chunks.extend(self._split_section(text, header, start, end, start_index=len(chunks)))
        return chunks

    def _detect_sections(self, text: str) -> list[tuple[str | None, int, int]]:
        lines = text.splitlines(keepends=True)
        sections: list[tuple[str | None, int]] = []
        cursor = 0

        for line in lines:
            stripped = line.strip()
            header = self._header_from_line(stripped)
            if header:
                sections.append((header, cursor))
            cursor += len(line)

        if not sections:
            return []

        output: list[tuple[str | None, int, int]] = []
        if sections[0][1] > 0:
            output.append((None, 0, sections[0][1]))
        for idx, (header, start) in enumerate(sections):
            end = sections[idx + 1][1] if idx + 1 < len(sections) else len(text)
            output.append((header, start, end))
        return [(h, s, e) for h, s, e in output if text[s:e].strip()]

    def _header_from_line(self, line: str) -> str | None:
        if not line:
            return None
        numbered = _NUMBERED_HEADER_RE.match(line)
        if numbered:
            return numbered.group(1).strip(" .")
        if _LEGAL_HEADER_RE.match(line):
            return line.strip()
        if len(line) < 60 and line.upper() == line and re.search(r"[A-Z]{4,}", line):
            return line.strip(" .")
        return None

    def _split_section(
        self,
        text: str,
        header: str | None,
        start: int,
        end: int,
        *,
        start_index: int,
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        cursor = start
        while cursor < end:
            target_end = min(end, cursor + self.chunk_size_chars)
            chunk_end = self._sentence_boundary(text, cursor, target_end, end)
            chunk_text = text[cursor:chunk_end].strip()
            if chunk_text:
                chunks.append(
                    DocumentChunk(
                        chunk_index=start_index + len(chunks),
                        page_number=self._page_number(text, cursor),
                        section_header=header,
                        text=chunk_text,
                        char_start=cursor,
                        char_end=chunk_end,
                        token_count=self._token_count(chunk_text),
                    )
                )
            if chunk_end >= end:
                break
            cursor = max(start, chunk_end - self.chunk_overlap_chars)
            if cursor >= chunk_end:
                cursor = chunk_end
        return chunks

    def _sentence_boundary(self, text: str, start: int, target_end: int, section_end: int) -> int:
        if target_end >= section_end:
            return section_end
        window = text[start:target_end]
        candidates = [
            match.end()
            for match in _SENTENCE_END_RE.finditer(window)
            if not self._is_abbreviation_boundary(window, match.start())
        ]
        if candidates and candidates[-1] > max(80, int(len(window) * 0.55)):
            return start + candidates[-1]
        forward = text[target_end:section_end]
        for next_match in _SENTENCE_END_RE.finditer(forward[:160]):
            prefix = text[start : target_end + next_match.start()]
            if not self._is_abbreviation_boundary(prefix, len(prefix) - 1):
                return target_end + next_match.end()
        return target_end

    def _is_abbreviation_boundary(self, text: str, punctuation_index: int) -> bool:
        prefix = text[: punctuation_index + 1]
        match = re.search(r"([A-Za-z]{1,10}\.)$", prefix)
        if not match:
            return False
        token = match.group(1).lower()
        if token in _ABBREVIATIONS:
            return True
        return bool(re.fullmatch(r"[A-Z]\.", match.group(1)))

    def _page_number(self, text: str, char_start: int) -> int:
        return text[:char_start].count("\f") + 1

    def _token_count(self, text: str) -> int:
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text.split()))
