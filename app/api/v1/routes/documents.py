"""Document routes — upload, list, search, detail, reprocess, export, delete."""

from __future__ import annotations

from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.core.config import get_settings
from app.db.models import AuditEventType, DocumentStatus
from app.schemas.document import (
    BatchUploadResponse,
    DocumentDetail,
    DocumentListResponse,
    DocumentRead,
    DocumentUploadResponse,
    ReprocessResponse,
)
from app.schemas.rag import DocumentAskRequest, RAGQueryResponse
from app.services.audit_service import AuditService
from app.services.document_service import DocumentService
from app.services.rag_service import RAGService
from app.workers.tasks import process_document_high_priority, process_document_task

# router-level auth: all endpoints require a valid API key (no-op when API_KEYS is empty)
router = APIRouter(dependencies=[Depends(require_api_key)])


# ── Upload ────────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    priority: bool = Query(default=False, description="Route to high-priority queue"),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> DocumentUploadResponse:
    service = DocumentService(db)
    document = await service.create_document(file, tenant_id=tenant_id)
    task_fn = process_document_high_priority if priority else process_document_task
    task = task_fn.delay(document.id, request_id=getattr(request.state, "request_id", None))
    return DocumentUploadResponse(
        document=DocumentRead.model_validate(document),
        task_id=str(task.id),
    )


@router.post(
    "/upload/batch", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_documents_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    priority: bool = Query(default=False),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> BatchUploadResponse:
    service = DocumentService(db)
    task_fn = process_document_high_priority if priority else process_document_task
    items: list[DocumentUploadResponse] = []
    for file in files:
        document = await service.create_document(file, tenant_id=tenant_id)
        task = task_fn.delay(document.id, request_id=getattr(request.state, "request_id", None))
        items.append(
            DocumentUploadResponse(
                document=DocumentRead.model_validate(document),
                task_id=str(task.id),
            )
        )
    return BatchUploadResponse(items=items)


# ── List / search ─────────────────────────────────────────────────────────────


@router.get("", response_model=DocumentListResponse)
def list_documents(
    status: str | None = Query(default=None, description="Filter by document status"),
    document_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> DocumentListResponse:
    service = DocumentService(db)
    items, total = service.list_documents(
        status=status,
        document_type=document_type,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )
    return DocumentListResponse(
        items=[DocumentRead.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=list[DocumentRead])
def search_documents(
    q: str = Query(..., min_length=2, description="Keyword query (filename, type, or OCR text)"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> list[DocumentRead]:
    service = DocumentService(db)
    return [
        DocumentRead.model_validate(d)
        for d in service.search_scoped(q, limit=limit, tenant_id=tenant_id)
    ]


# ── Single document ───────────────────────────────────────────────────────────


@router.get("/{document_id}/status")
def get_document_status(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> dict:
    doc = DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    return {
        "document_id": doc.id,
        "status": doc.status,
        "document_type": doc.document_type,
        "document_confidence": doc.document_confidence,
        "updated_at": doc.updated_at,
    }


@router.get("/{document_id}/result")
def get_document_result(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> dict:
    doc = DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    if not doc.extraction_result:
        raise HTTPException(status_code=404, detail="Extraction result not found.")
    return doc.extraction_result.export_payload


@router.get("/{document_id}/history")
def get_document_history(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> list[dict]:
    doc = DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    return [
        {
            "id": item.id,
            "event_type": item.event_type,
            "actor": item.actor,
            "payload": item.payload,
            "created_at": item.created_at,
        }
        for item in doc.audit_logs
    ]


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> DocumentDetail:
    return DocumentService(db).get_detail(document_id, tenant_id=tenant_id)


@router.post(
    "/{document_id}/reprocess",
    response_model=ReprocessResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_document(
    request: Request,
    document_id: str,
    priority: bool = Query(default=False),
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> ReprocessResponse:
    service = DocumentService(db)
    doc = service.get_document(document_id, tenant_id=tenant_id)
    doc.status = DocumentStatus.queued
    AuditService(db).log(doc.id, AuditEventType.document_reprocessed, payload={})
    db.commit()
    task_fn = process_document_high_priority if priority else process_document_task
    task = task_fn.delay(doc.id, request_id=getattr(request.state, "request_id", None))
    return ReprocessResponse(document_id=doc.id, task_id=str(task.id), status=doc.status)


@router.post("/{document_id}/ask", response_model=RAGQueryResponse)
def ask_document(
    document_id: str,
    payload: DocumentAskRequest,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> RAGQueryResponse:
    if not get_settings().rag_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAG is not enabled. Set RAG_ENABLED=true.",
        )

    DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    return RAGService(db).query(
        payload.question,
        tenant_id=tenant_id,
        document_ids=[document_id],
    )


@router.get("/{document_id}/export")
def export_document(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
):
    settings = get_settings()
    service = DocumentService(db)
    doc = service.get_document(document_id, tenant_id=tenant_id)

    if settings.storage_backend == "s3":
        from app.storage.s3 import S3StorageProvider

        provider = S3StorageProvider()
        try:
            data = provider.get_export_bytes(doc.id)
        except Exception:
            raise HTTPException(status_code=404, detail="Export file not found.")
        return StreamingResponse(
            iter([data]),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={doc.id}.json"},
        )

    export_path = Path(settings.export_dir / f"{doc.id}.json")
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found.")
    return FileResponse(path=export_path, media_type="application/json", filename=export_path.name)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_document(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> Response:
    service = DocumentService(db)
    service.soft_delete(document_id, tenant_id=tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
