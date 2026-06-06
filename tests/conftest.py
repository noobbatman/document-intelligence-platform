"""Test fixtures — in-memory SQLite, test client, seed helpers."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

_tmp = tempfile.mkdtemp(prefix="docintel-tests-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
        "UPLOAD_DIR": f"{_tmp}/uploads",
        "EXPORT_DIR": f"{_tmp}/exports",
        "API_KEYS": "",
        "STORAGE_BACKEND": "local",
        "RATE_LIMIT_ENABLED": "false",
        "PIPELINE_VERSION": "0.3.0",
    }
)

from app.api.deps import db_dependency  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

get_settings.cache_clear()
Base.metadata.create_all(bind=engine)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[db_dependency] = lambda: (yield db_session)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
