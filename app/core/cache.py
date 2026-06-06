"""
Lightweight Redis-backed cache utility.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_NAMESPACE = "docintel:cache"


class _NullCache:
    def get(self, key: str) -> Any:  # noqa: ANN401
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        pass

    def delete(self, key: str) -> None:
        pass

    def delete_pattern(self, pattern: str) -> None:
        pass


class RedisCache:
    def __init__(self, redis_url: str, default_ttl: int = 300) -> None:
        import redis as redis_lib

        self._client = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=0.3,
            socket_connect_timeout=0.3,
        )
        self._default_ttl = default_ttl

    def _full_key(self, key: str) -> str:
        return f"{_NAMESPACE}:{key}"

    def get(self, key: str) -> Any:
        try:
            raw = self._client.get(self._full_key(key))
            return json.loads(raw) if raw is not None else None
        except Exception as exc:
            logger.debug("cache.get failed — key=%s err=%s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            self._client.setex(
                self._full_key(key),
                ttl if ttl is not None else self._default_ttl,
                json.dumps(value, default=str),
            )
        except Exception as exc:
            logger.debug("cache.set failed — key=%s err=%s", key, exc)

    def delete(self, key: str) -> None:
        try:
            self._client.delete(self._full_key(key))
        except Exception as exc:
            logger.debug("cache.delete failed — key=%s err=%s", key, exc)

    def delete_pattern(self, pattern: str) -> None:
        try:
            full_pattern = f"{_NAMESPACE}:{pattern}"
            keys = self._client.keys(full_pattern)
            if keys:
                self._client.delete(*keys)
        except Exception as exc:
            logger.debug("cache.delete_pattern failed — pattern=%s err=%s", pattern, exc)


@lru_cache(maxsize=1)
def get_cache() -> RedisCache | _NullCache:
    from app.core.config import get_settings

    settings = get_settings()
    redis_url = settings.redis_rate_limit_url or settings.celery_broker_url
    if not redis_url:
        return _NullCache()

    try:
        cache = RedisCache(redis_url, default_ttl=settings.cache_ttl_seconds)
        cache._client.ping()
        logger.info("cache.ready url=%s", redis_url)
        return cache
    except Exception as exc:
        logger.warning("cache.unavailable — running without cache: %s", exc)
        return _NullCache()
