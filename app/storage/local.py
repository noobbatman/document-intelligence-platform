import json
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self) -> None:
        self.settings = get_settings()

    async def save_upload(self, upload: UploadFile) -> Path:
        suffix = Path(upload.filename or "").suffix
        stored_name = f"{uuid4()}{suffix}"
        destination = self.settings.upload_dir / stored_name
        content = await upload.read()
        destination.write_bytes(content)
        return destination

    def write_export(self, document_id: str, payload: dict) -> Path:
        destination = self.settings.export_dir / f"{document_id}.json"
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination
