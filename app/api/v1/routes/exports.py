"""Export endpoints — CSV, Excel, and JSON batch download."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.services.export_service import ExportService

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/csv", summary="Export documents as CSV")
def export_csv(
    document_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> Response:
    """Download all matching documents as a flat CSV file."""
    data = ExportService(db).export_csv(
        document_type=document_type, status=status, tenant_id=tenant_id, limit=limit
    )
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=documents_export.csv"},
    )


@router.get("/xlsx", summary="Export documents as Excel (XLSX)")
def export_xlsx(
    document_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> Response:
    """Download all matching documents as an Excel spreadsheet with styled headers."""
    try:
        data = ExportService(db).export_xlsx(
            document_type=document_type, status=status, tenant_id=tenant_id, limit=limit
        )
    except RuntimeError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=501, detail=str(exc))
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=documents_export.xlsx"},
    )


@router.get("/json", summary="Export documents as JSON batch")
def export_json(
    document_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> Response:
    """Download full extraction payloads as a JSON array."""
    data = ExportService(db).export_json_batch(
        document_type=document_type, status=status, tenant_id=tenant_id, limit=limit
    )
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=documents_export.json"},
    )
