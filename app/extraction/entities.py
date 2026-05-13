"""Entity extraction — spaCy NER with regex fallback.

spaCy is optional: if the model is not installed the module falls back to
regex-only extraction so the evaluation harness can run without installing
the full NLP stack.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.ocr.base import OCRResult


@lru_cache(maxsize=1)
def _get_nlp():
    try:
        import spacy

        from app.core.config import get_settings

        model_name = get_settings().spacy_model
        try:
            return spacy.load(model_name)
        except Exception:
            return spacy.blank("en")
    except ImportError:
        return None


def extract_entities(ocr_result: OCRResult) -> list[dict]:
    text = ocr_result.text
    entities: list[dict] = []

    nlp = _get_nlp()
    if nlp is not None:
        doc = nlp(text)
        for ent in getattr(doc, "ents", []):
            entities.append({"label": ent.label_, "text": ent.text, "confidence": 0.75})

    # Regex fallbacks (always run)
    for match in re.findall(r"\b\d{4,18}\b", text)[:20]:
        entities.append({"label": "ACCOUNT_OR_ID", "text": match, "confidence": 0.70})
    for match in re.findall(r"[$€£]?\s?\d[\d,]*\.\d{2}", text)[:20]:
        entities.append({"label": "AMOUNT", "text": match, "confidence": 0.70})
    for match in re.findall(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s+\d{1,2},?\s+\d{4}\b",
        text,
        re.IGNORECASE,
    )[:10]:
        entities.append({"label": "DATE", "text": match, "confidence": 0.80})

    return entities
