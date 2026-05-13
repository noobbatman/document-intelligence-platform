from app.core.config import get_settings
from app.storage.base import StorageProvider


def get_storage_provider() -> StorageProvider:
    settings = get_settings()
    if settings.storage_backend == "s3":
        from app.storage.s3 import S3StorageProvider

        return S3StorageProvider()
    from app.storage.local import LocalStorageProvider

    return LocalStorageProvider()
