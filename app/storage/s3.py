"""S3 / MinIO storage provider."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from fastapi import UploadFile

from app.core.config import get_settings
from app.storage.base import StorageProvider


class S3StorageProvider(StorageProvider):
    """Stores uploads and exports in an S3-compatible bucket (AWS or MinIO)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        # For Fargate/ECS with task role, boto3 auto-discovers credentials from environment
        # For local/explicit creds, provide S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY
        kwargs: dict = dict(
            region_name=settings.s3_region,
        )
        # Only pass credentials if explicitly provided
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            kwargs["aws_access_key_id"] = settings.s3_access_key_id
            kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
        # Optional: use custom endpoint for MinIO or other S3-compatible services
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        self._client: BaseClient = boto3.client("s3", **kwargs)
        self._ensure_buckets()

    def _ensure_buckets(self) -> None:
        for bucket in (self.settings.s3_bucket_uploads, self.settings.s3_bucket_exports):
            try:
                self._client.head_bucket(Bucket=bucket)
            except Exception:
                self._client.create_bucket(Bucket=bucket)

    async def save_upload(self, upload: UploadFile) -> Path:
        suffix = Path(upload.filename or "").suffix
        key = f"{uuid4()}{suffix}"
        content = await upload.read()
        self._client.put_object(
            Bucket=self.settings.s3_bucket_uploads,
            Key=key,
            Body=content,
            ContentType=upload.content_type or "application/octet-stream",
        )
        # Return a virtual path: s3://<bucket>/<key>
        return Path(f"s3://{self.settings.s3_bucket_uploads}/{key}")

    def write_export(self, document_id: str, payload: dict) -> Path:
        key = f"{document_id}.json"
        self._client.put_object(
            Bucket=self.settings.s3_bucket_exports,
            Key=key,
            Body=json.dumps(payload, indent=2).encode(),
            ContentType="application/json",
        )
        return Path(f"s3://{self.settings.s3_bucket_exports}/{key}")

    def download_to_tmp(self, stored_path: str) -> Path:
        """Download an S3 object to a local temp file and return its path."""
        import tempfile

        # stored_path looks like s3://bucket/key
        parts = stored_path.removeprefix("s3://").split("/", 1)
        bucket, key = parts[0], parts[1]
        suffix = Path(key).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            self._client.download_fileobj(bucket, key, tmp)
            tmp.flush()
            return Path(tmp.name)

    def get_export_bytes(self, document_id: str) -> bytes:
        key = f"{document_id}.json"
        obj = self._client.get_object(Bucket=self.settings.s3_bucket_exports, Key=key)
        return obj["Body"].read()
