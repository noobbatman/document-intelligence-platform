"""Portable SQLAlchemy types for vector-backed RAG tables.

PostgreSQL uses pgvector/ARRAY types in production. SQLite stores the same
values as JSON so the existing lightweight test harness can create the schema.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import TypeDecorator


class EmbeddingVector(TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, dimensions: int = 768, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dimensions))
            except Exception:
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect):  # noqa: ANN001
        if value is None:
            return None
        if hasattr(value, "tolist"):
            return value.tolist()
        return list(value)

    def process_result_value(self, value: Any, dialect):  # noqa: ANN001
        if value is None:
            return None
        if hasattr(value, "tolist"):
            return value.tolist()
        return list(value)


class TextList(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.ARRAY(Text()))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect):  # noqa: ANN001
        if value is None:
            return []
        return list(value)

    def process_result_value(self, value: Any, dialect):  # noqa: ANN001
        if value is None:
            return []
        return list(value)
