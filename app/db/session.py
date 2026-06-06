from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def get_engine() -> Engine:
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    kwargs: dict = dict(
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if not is_sqlite:
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
        )
    engine = create_engine(settings.database_url, **kwargs)

    # Enforce UTC on every new connection (PostgreSQL)
    if not is_sqlite:

        @event.listens_for(engine, "connect")
        def set_timezone(dbapi_conn, _rec):  # noqa: ANN001
            with dbapi_conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'UTC'")

    return engine


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
