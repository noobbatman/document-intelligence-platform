"""Health check routes — liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import db_dependency
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()
DB_DEP = Depends(db_dependency)


@router.get("/health")
@router.get("/health/live")
def liveness() -> dict:
    """Fast liveness probe — no external checks, just confirms the process is alive."""
    return {"status": "ok", "version": settings.pipeline_version}


@router.get("/health/ready")
def readiness(db: Session = DB_DEP) -> dict:
    """Readiness probe — confirms database and Redis are reachable."""
    checks: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    try:
        import redis as redis_lib

        redis_client = redis_lib.from_url(settings.celery_broker_url, socket_timeout=1.0)
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    all_ok = all(value == "ok" for value in checks.values())
    payload = {
        "status": "ok" if all_ok else "degraded",
        "version": settings.pipeline_version,
        "env": settings.app_env,
        "storage_backend": settings.storage_backend,
        **checks,
    }
    if all_ok:
        return payload
    return JSONResponse(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
