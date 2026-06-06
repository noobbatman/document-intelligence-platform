"""Webhook registration, dispatch, dead-letter, and replay service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FailedWebhookEvent, Webhook, WebhookStatus


class WebhookService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def register(self, name: str, url: str, event: str, secret: str | None = None) -> Webhook:
        webhook = Webhook(name=name, url=url, event=event, secret=secret)
        self.db.add(webhook)
        self.db.commit()
        self.db.refresh(webhook)
        return webhook

    def list_webhooks(self) -> list[Webhook]:
        return list(self.db.scalars(select(Webhook).order_by(Webhook.created_at.desc())))

    def get(self, webhook_id: str) -> Webhook | None:
        return self.db.get(Webhook, webhook_id)

    def deactivate(self, webhook_id: str) -> Webhook | None:
        wh = self.get(webhook_id)
        if wh:
            wh.status = WebhookStatus.inactive
            self.db.commit()
        return wh

    def dispatch_event(self, event: str, payload: dict) -> list[str]:
        from app.workers.tasks import dispatch_webhook_task

        hooks = list(
            self.db.scalars(
                select(Webhook).where(
                    Webhook.event == event, Webhook.status == WebhookStatus.active
                )
            )
        )
        task_ids: list[str] = []
        for hook in hooks:
            task = dispatch_webhook_task.apply_async(args=[hook.id, event, payload])
            task_ids.append(str(task.id))
        return task_ids

    def record_failed_delivery(
        self,
        *,
        webhook_id: str | None,
        webhook_url: str,
        event: str,
        payload: dict,
        error_detail: str | None,
        attempts: int,
    ) -> FailedWebhookEvent:
        failed = FailedWebhookEvent(
            webhook_id=webhook_id,
            webhook_url=webhook_url,
            event=event,
            payload=payload,
            error_detail=error_detail,
            attempts=attempts,
        )
        self.db.add(failed)
        self.db.commit()
        self.db.refresh(failed)
        return failed

    def list_failed(
        self,
        *,
        event: str | None = None,
        replayed: bool | None = None,
        limit: int = 100,
    ) -> list[FailedWebhookEvent]:
        stmt = (
            select(FailedWebhookEvent).order_by(FailedWebhookEvent.created_at.desc()).limit(limit)
        )
        if event:
            stmt = stmt.where(FailedWebhookEvent.event == event)
        if replayed is not None:
            stmt = stmt.where(FailedWebhookEvent.replayed == replayed)
        return list(self.db.scalars(stmt))

    def replay(self, failed_id: str) -> dict:
        from app.workers.tasks import dispatch_webhook_task

        failed = self.db.get(FailedWebhookEvent, failed_id)
        if not failed:
            raise ValueError(f"FailedWebhookEvent {failed_id} not found.")

        task = dispatch_webhook_task.apply_async(
            args=[failed.webhook_id, failed.event, failed.payload]
        )
        failed.replayed = True
        failed.replayed_at = datetime.now(UTC)
        self.db.commit()
        return {"task_id": str(task.id), "failed_event_id": failed_id}
