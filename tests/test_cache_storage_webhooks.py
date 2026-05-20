from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import cache as cache_module
from app.db.models import FailedWebhookEvent, WebhookStatus
from app.services.webhook_service import WebhookService
from app.storage.local import LocalStorageProvider
from app.storage.s3 import S3StorageProvider


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[tuple[str, ...]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.values[f"{key}:ttl"] = str(ttl)

    def delete(self, *keys: str) -> None:
        self.deleted.append(keys)
        for key in keys:
            self.values.pop(key, None)

    def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [key for key in self.values if key.startswith(prefix) and not key.endswith(":ttl")]

    def ping(self) -> bool:
        return True


def test_redis_cache_serializes_values_and_deletes_patterns(monkeypatch) -> None:
    fake = FakeRedisClient()
    monkeypatch.setattr("redis.from_url", lambda *_, **__: fake)

    cache = cache_module.RedisCache("redis://cache", default_ttl=45)
    cache.set("doc:1", {"status": "ready"})
    cache.set("doc:2", {"status": "queued"}, ttl=5)

    assert cache.get("doc:1") == {"status": "ready"}
    assert fake.values["docintel:cache:doc:1:ttl"] == "45"
    assert fake.values["docintel:cache:doc:2:ttl"] == "5"

    cache.delete("doc:1")
    assert ("docintel:cache:doc:1",) in fake.deleted

    cache.delete_pattern("doc:*")
    assert any("docintel:cache:doc:2" in keys for keys in fake.deleted)


def test_cache_fails_open_when_redis_raises(monkeypatch) -> None:
    class BrokenClient(FakeRedisClient):
        def get(self, key: str) -> str | None:
            raise RuntimeError("redis unavailable")

        def setex(self, key: str, ttl: int, value: str) -> None:
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr("redis.from_url", lambda *_, **__: BrokenClient())

    cache = cache_module.RedisCache("redis://cache")

    assert cache.get("anything") is None
    cache.set("anything", {"ok": True})
    cache.delete("anything")
    cache.delete_pattern("anything:*")


def test_get_cache_returns_null_cache_when_ping_fails(monkeypatch) -> None:
    class BrokenRedisCache:
        def __init__(self, *_args, **_kwargs) -> None:
            self._client = self

        def ping(self) -> None:
            raise RuntimeError("no redis")

    cache_module.get_cache.cache_clear()
    monkeypatch.setattr(cache_module, "RedisCache", BrokenRedisCache)

    cache = cache_module.get_cache()

    assert cache.get("missing") is None
    cache.set("missing", "value")
    cache.delete("missing")
    cache.delete_pattern("missing:*")
    cache_module.get_cache.cache_clear()


@pytest.mark.asyncio
async def test_local_storage_saves_upload_and_export(tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(upload_dir=tmp_path / "uploads", export_dir=tmp_path / "exports")
    settings.upload_dir.mkdir()
    settings.export_dir.mkdir()
    monkeypatch.setattr("app.storage.local.get_settings", lambda: settings)

    upload = SimpleNamespace(filename="sample.pdf", read=AsyncMock(return_value=b"%PDF test"))

    provider = LocalStorageProvider()
    saved_path = await provider.save_upload(upload)
    export_path = provider.write_export("doc-1", {"ok": True})

    assert saved_path.suffix == ".pdf"
    assert saved_path.read_bytes() == b"%PDF test"
    assert json.loads(export_path.read_text(encoding="utf-8")) == {"ok": True}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.created_buckets: list[str] = []

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803
        if Bucket == "missing-exports":
            raise RuntimeError("missing")

    def create_bucket(self, Bucket: str) -> None:  # noqa: N803
        self.created_buckets.append(Bucket)

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else bytes(Body)

    def download_fileobj(self, bucket: str, key: str, target) -> None:
        target.write(self.objects[(bucket, key)])

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        return {"Body": SimpleNamespace(read=lambda: self.objects[(Bucket, Key)])}


@pytest.mark.asyncio
async def test_s3_storage_saves_upload_export_and_downloads(monkeypatch) -> None:
    fake_client = FakeS3Client()
    settings = SimpleNamespace(
        s3_region="us-east-1",
        s3_access_key_id="key",
        s3_secret_access_key="secret",
        s3_endpoint_url="http://minio:9000",
        s3_bucket_uploads="uploads",
        s3_bucket_exports="missing-exports",
    )
    monkeypatch.setattr("app.storage.s3.get_settings", lambda: settings)
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: fake_client)

    upload = SimpleNamespace(
        filename="brief.pdf",
        content_type="application/pdf",
        read=AsyncMock(return_value=b"%PDF"),
    )

    provider = S3StorageProvider()
    saved = await provider.save_upload(upload)
    export_path = provider.write_export("doc-99", {"field": "value"})
    fake_client.objects[("uploads", "original.pdf")] = b"%PDF"
    downloaded = provider.download_to_tmp("s3://uploads/original.pdf")

    assert "missing-exports" in fake_client.created_buckets
    assert str(saved).startswith("s3:")
    assert str(export_path).startswith("s3:")
    assert provider.get_export_bytes("doc-99")
    assert Path(downloaded).read_bytes() == b"%PDF"


def test_webhook_service_lifecycle_dispatch_and_replay(db_session, monkeypatch) -> None:
    service = WebhookService(db_session)
    webhook = service.register("audit", "https://example.test/hook", "document.completed", "s")
    task_ids: list[str] = []

    def fake_apply_async(args):
        task_ids.append(args[0])
        return SimpleNamespace(id=f"task-{len(task_ids)}")

    monkeypatch.setattr(
        "app.workers.tasks.dispatch_webhook_task.apply_async",
        fake_apply_async,
    )

    assert service.get(webhook.id).url == "https://example.test/hook"
    assert service.list_webhooks()[0].id == webhook.id
    assert service.dispatch_event("document.completed", {"document_id": "doc-1"}) == ["task-1"]

    deactivated = service.deactivate(webhook.id)
    assert deactivated.status == WebhookStatus.inactive

    failed = service.record_failed_delivery(
        webhook_id=webhook.id,
        webhook_url=webhook.url,
        event="document.completed",
        payload={"document_id": "doc-1"},
        error_detail="timeout",
        attempts=3,
    )
    assert isinstance(failed, FailedWebhookEvent)
    assert service.list_failed(event="document.completed", replayed=False)[0].id == failed.id

    replay = service.replay(failed.id)
    assert replay["failed_event_id"] == failed.id
    assert db_session.get(FailedWebhookEvent, failed.id).replayed is True

    with pytest.raises(ValueError):
        service.replay("missing")
