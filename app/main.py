"""Application entry-point and startup/shutdown lifecycle."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

import app.core.http_runtime as _runtime_module
from app.api.router import api_router
from app.core.config import get_settings
from app.core.http_runtime import (
    build_rate_limiter,
    normalized_path,
    rate_limit_for_request,
    rate_limit_key,
    request_started_at,
    should_skip_rate_limit,
)
from app.core.logging import configure_logging
from app.core.metrics import http_request_duration_seconds, http_requests_total


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    _runtime_module.rate_limiter = build_rate_limiter(get_settings())
    yield


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.pipeline_version,
    debug=settings.debug,
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_header(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def enforce_rate_limits_and_record_metrics(request: Request, call_next):
    started_at = request_started_at()
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    response = None
    current_settings = get_settings()

    try:
        if current_settings.rate_limit_enabled and not should_skip_rate_limit(
            request, current_settings
        ):
            decision = _runtime_module.rate_limiter.check(
                key=rate_limit_key(request, current_settings),
                limit=rate_limit_for_request(request, current_settings),
            )
            if not decision.allowed:
                response = JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded."},
                )
            else:
                response = await call_next(request)

            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
            response.headers["X-RateLimit-Reset"] = str(decision.retry_after_seconds)
            if not decision.allowed:
                response.headers["Retry-After"] = str(decision.retry_after_seconds)
        else:
            response = await call_next(request)

        status_code = response.status_code
        return response
    except Exception:
        raise
    finally:
        path = normalized_path(request)
        duration = max(0.0, perf_counter() - started_at)
        http_requests_total.labels(
            method=request.method,
            path=path,
            status=str(status_code),
        ).inc()
        http_request_duration_seconds.labels(
            method=request.method,
            path=path,
        ).observe(duration)


# ── Exception handlers ────────────────────────────────────────────────────────


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.exception("unhandled_exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal error occurred."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(api_router, prefix=settings.api_v1_prefix)


# ── Observability endpoints ───────────────────────────────────────────────────


@app.get("/metrics", include_in_schema=False)
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)


_static_dir = Path(__file__).parent / "static"
_frontend_dir = Path(__file__).parent.parent / "frontend"

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static-assets")

if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
