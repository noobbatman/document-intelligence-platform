"""FastAPI dependency injection helpers."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Header, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

settings = get_settings()
_api_key_scheme = APIKeyHeader(name=settings.api_key_header, auto_error=False)


def db_dependency() -> Generator[Session, None, None]:
    yield from get_db()


async def require_api_key(api_key: str | None = Security(_api_key_scheme)) -> None:
    """Validate API key if auth is enabled (API_KEYS is non-empty).
    Leave API_KEYS empty in .env to disable auth for local development.
    """
    if not settings.api_keys:
        return  # auth disabled — dev/test mode
    if not api_key or api_key not in settings.api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


async def get_optional_tenant(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> str | None:
    """Extract optional tenant ID from the X-Tenant-ID request header.
    Returns None when the header is absent (single-tenant / unauthenticated use).
    """
    return x_tenant_id
