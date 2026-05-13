from abc import ABC, abstractmethod
from pathlib import Path

from fastapi import UploadFile


class StorageProvider(ABC):
    @abstractmethod
    async def save_upload(self, upload: UploadFile) -> Path:
        raise NotImplementedError

    @abstractmethod
    def write_export(self, document_id: str, payload: dict) -> Path:
        raise NotImplementedError
