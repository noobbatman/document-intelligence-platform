"""Embedding wrapper with lazy sentence-transformers loading."""

from __future__ import annotations

import hashlib
import logging
import math
from functools import lru_cache

from app.core.config import get_settings

_DIMENSIONS = 768
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._model = None
        self._model_load_failed = False

    def encode_passages(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([f"{_BGE_QUERY_INSTRUCTION}{text}"])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        if model is None:
            return [_hashed_embedding(text) for text in texts]

        vectors = model.encode(
            texts,
            batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() if hasattr(vec, "tolist") else list(vec) for vec in vectors]

    def _get_model(self):
        if self._model is not None:
            return self._model
        if self._model_load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.settings.embedding_model)
            return self._model
        except Exception as exc:
            self._model_load_failed = True
            logger.warning(
                "embedding_model_unavailable_using_hash_fallback",
                extra={"model": self.settings.embedding_model, "error": str(exc)},
            )
            return None


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


def _hashed_embedding(text: str) -> list[float]:
    """Deterministic fallback used when sentence-transformers is unavailable."""
    vector = [0.0] * _DIMENSIONS
    tokens = [tok for tok in text.lower().split() if tok]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % _DIMENSIONS
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[idx] += sign
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]
