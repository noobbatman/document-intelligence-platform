from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OCRWord:
    text: str
    confidence: float
    page_number: int
    bbox: list[float] = field(default_factory=list)


@dataclass
class OCRPage:
    page_number: int
    text: str
    words: list[OCRWord]
    confidence: float


@dataclass(init=False)
class OCRResult:
    text: str
    pages: list[OCRPage]
    metadata: dict

    def __init__(
        self,
        text: str,
        pages: list[OCRPage] | None = None,
        metadata: dict | None = None,
        words: list[OCRWord] | None = None,
    ) -> None:
        if pages is None:
            page_words = words or []
            avg_conf = (
                sum(word.confidence for word in page_words) / len(page_words) if page_words else 0.0
            )
            pages = [
                OCRPage(
                    page_number=1,
                    text=text,
                    words=page_words,
                    confidence=avg_conf,
                )
            ]
        self.text = text
        self.pages = pages
        self.metadata = metadata or {}

    @property
    def words(self) -> list[OCRWord]:
        return [word for page in self.pages for word in page.words]


class OCRProvider(ABC):
    @abstractmethod
    def extract(self, path: str) -> OCRResult:
        raise NotImplementedError
