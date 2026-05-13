"""Purchase Order management and matching endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, get_optional_tenant, require_api_key
from app.services.document_service import DocumentService
from app.services.po_matching_service import POMatchingService

router = APIRouter(dependencies=[Depends(require_api_key)])


class POCreate(BaseModel):
    po_number: str
    vendor_name: str
    total_amount: float | None = None
    currency: str = "GBP"
    line_items: list = []


class PORead(BaseModel):
    id: str
    po_number: str
    vendor_name: str
    total_amount: float | None
    currency: str
    status: str
    line_items: list
    model_config = {"from_attributes": True}


class POMatchRead(BaseModel):
    id: str
    document_id: str
    po_id: str | None
    match_status: str
    match_score: float
    discrepancies: list
    matched_fields: dict
    model_config = {"from_attributes": True}


@router.post("", response_model=PORead, status_code=201)
def register_po(
    body: POCreate,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> PORead:
    svc = POMatchingService(db)
    po = svc.register_po(
        po_number=body.po_number,
        vendor_name=body.vendor_name,
        total_amount=body.total_amount,
        currency=body.currency,
        line_items=body.line_items,
        tenant_id=tenant_id,
    )
    return PORead.model_validate(po)


@router.get("", response_model=list[PORead])
def list_pos(
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> list[PORead]:
    return [PORead.model_validate(p) for p in POMatchingService(db).list_pos(tenant_id=tenant_id)]


@router.post("/match/{document_id}", response_model=POMatchRead)
def match_document(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> POMatchRead:
    """Run PO matching for a processed document."""
    doc = DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    match = POMatchingService(db).match(doc)
    return POMatchRead.model_validate(match)


@router.get("/match/{document_id}", response_model=POMatchRead)
def get_match(
    document_id: str,
    db: Session = Depends(db_dependency),
    tenant_id: str | None = Depends(get_optional_tenant),
) -> POMatchRead:
    DocumentService(db).get_document(document_id, tenant_id=tenant_id)
    match = POMatchingService(db).get_match(document_id)
    if not match:
        raise HTTPException(status_code=404, detail="No PO match found for this document.")
    return POMatchRead.model_validate(match)
