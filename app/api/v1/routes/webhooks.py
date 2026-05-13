"""Webhook management routes — registration, listing, deactivation, dead-letter replay."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from app.api.deps import db_dependency, require_api_key
from app.db.models import WebhookEvent
from app.services.webhook_service import WebhookService

router = APIRouter(dependencies=[Depends(require_api_key)])


class WebhookCreate(BaseModel):
    name: str
    url: HttpUrl
    event: WebhookEvent
    secret: str | None = None


class WebhookRead(BaseModel):
    id: str
    name: str
    url: str
    event: str
    status: str
    failure_count: int
    model_config = {"from_attributes": True}


class FailedWebhookRead(BaseModel):
    id: str
    webhook_id: str | None
    webhook_url: str
    event: str
    payload: dict
    error_detail: str | None
    attempts: int
    replayed: bool
    replayed_at: str | None
    created_at: str
    model_config = {"from_attributes": True}


@router.get("", response_model=list[WebhookRead])
def list_webhooks(db: Session = Depends(db_dependency)) -> list[WebhookRead]:
    return [WebhookRead.model_validate(w) for w in WebhookService(db).list_webhooks()]


@router.post("", response_model=WebhookRead, status_code=status.HTTP_201_CREATED)
def register_webhook(body: WebhookCreate, db: Session = Depends(db_dependency)) -> WebhookRead:
    wh = WebhookService(db).register(
        name=body.name,
        url=str(body.url),
        event=str(body.event),
        secret=body.secret,
    )
    return WebhookRead.model_validate(wh)


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def deactivate_webhook(webhook_id: str, db: Session = Depends(db_dependency)) -> Response:
    wh = WebhookService(db).deactivate(webhook_id)
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/failed", response_model=list[FailedWebhookRead])
def list_failed_webhooks(
    event: str | None = Query(default=None),
    replayed: bool | None = Query(default=None, description="Filter by replay status"),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(db_dependency),
) -> list[FailedWebhookRead]:
    records = WebhookService(db).list_failed(event=event, replayed=replayed, limit=limit)
    return [
        FailedWebhookRead(
            id=r.id,
            webhook_id=r.webhook_id,
            webhook_url=r.webhook_url,
            event=r.event,
            payload=r.payload,
            error_detail=r.error_detail,
            attempts=r.attempts,
            replayed=r.replayed,
            replayed_at=r.replayed_at.isoformat() if r.replayed_at else None,
            created_at=r.created_at.isoformat(),
        )
        for r in records
    ]


@router.post("/failed/{failed_id}/replay")
def replay_failed_webhook(
    failed_id: str,
    db: Session = Depends(db_dependency),
) -> dict:
    svc = WebhookService(db)
    try:
        return svc.replay(failed_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
