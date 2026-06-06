from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    rationale: dict


class DocumentClassifier(ABC):
    @abstractmethod
    def classify(self, text: str) -> ClassificationResult:
        raise NotImplementedError
