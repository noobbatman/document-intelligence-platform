"""HTTP middleware helpers for request metrics and rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import perf_counter, time
from typing import Protocol

from fastapi import Request

from app.core.config import Settings


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter(Protocol):
    def check(self, key: str, limit: int) -> RateLimitDecision: ...
    def reset(self) -> None: ...


class InMemoryRateLimiter:
    """Fixed-window in-memory limiter for local and single-instance deployments."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    def check(self, key: str, limit: int) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(
                allowed=True, limit=limit, remaining=limit, retry_after_seconds=0
            )

        window = int(time() // 60)
        now = int(time())
        retry_after = max(1, 60 - (now % 60))

        with self._lock:
            active_window, count = self._windows.get(key, (window, 0))
            if active_window != window:
                active_window, count = window, 0

            if count >= limit:
                self._windows[key] = (active_window, count)
                return RateLimitDecision(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            count += 1
            self._windows[key] = (active_window, count)
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=max(0, limit - count),
                retry_after_seconds=retry_after,
            )

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RedisRateLimiter:
    """Fixed-window rate limiter backed by Redis for multi-replica deployments."""

    def __init__(self, redis_url: str) -> None:
        import redis as redis_lib

        self._redis = redis_lib.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=0.5,
        )

    def check(self, key: str, limit: int) -> RateLimitDecision:
        if limit <= 0:
            return RateLimitDecision(
                allowed=True, limit=limit, remaining=limit, retry_after_seconds=0
            )

        now = int(time())
        window = now // 60
        retry_after = max(1, 60 - (now % 60))
        redis_key = f"rl:{window}:{key}"

        try:
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, 120)
            count, _ = pipe.execute()
        except Exception:
            return RateLimitDecision(
                allowed=True, limit=limit, remaining=limit, retry_after_seconds=0
            )

        allowed = count <= limit
        return RateLimitDecision(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after_seconds=retry_after,
        )

    def reset(self) -> None:
        try:
            keys = self._redis.keys("rl:*")
            if keys:
                self._redis.delete(*keys)
        except Exception:
            pass


def build_rate_limiter(settings: Settings) -> RateLimiter:
    redis_url = getattr(settings, "redis_rate_limit_url", None)
    if redis_url:
        try:
            limiter = RedisRateLimiter(redis_url)
            limiter._redis.ping()
            return limiter
        except Exception:
            pass
    return InMemoryRateLimiter()


rate_limiter: RateLimiter = InMemoryRateLimiter()


def request_started_at() -> float:
    return perf_counter()


def should_skip_rate_limit(request: Request, settings: Settings) -> bool:
    path = request.url.path
    if path == "/metrics":
        return True
    if path in {"/docs", "/redoc", f"{settings.api_v1_prefix}/openapi.json"}:
        return True
    return path.endswith(("/health", "/health/live", "/health/ready"))


def rate_limit_bucket(request: Request, settings: Settings) -> str:
    path = request.url.path
    upload_prefix = f"{settings.api_v1_prefix}/documents/upload"
    if path in (upload_prefix, f"{upload_prefix}/batch"):
        return "upload"
    return "default"


def rate_limit_for_request(request: Request, settings: Settings) -> int:
    bucket = rate_limit_bucket(request, settings)
    if bucket == "upload":
        return settings.rate_limit_upload_per_minute
    return settings.rate_limit_default_per_minute


def rate_limit_subject(request: Request, settings: Settings) -> str:
    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return f"tenant:{tenant_id}"

    api_key = request.headers.get(settings.api_key_header)
    if api_key:
        return f"api_key:{api_key}"

    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


def rate_limit_key(request: Request, settings: Settings) -> str:
    bucket = rate_limit_bucket(request, settings)
    subject = rate_limit_subject(request, settings)
    return f"{bucket}:{subject}"


def normalized_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or request.url.path)
