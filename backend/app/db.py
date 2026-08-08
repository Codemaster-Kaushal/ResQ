"""Database engine, session dependency, and health probe.

SQLite for dev/demo, Postgres-compatible in production — so no SQLite-only SQL
anywhere in this codebase (TRD §1).
"""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "pool_pre_ping": True}
    if settings.is_sqlite:
        # FastAPI serves requests from a threadpool; SQLite objects are otherwise
        # bound to their creating thread.
        kwargs["connect_args"] = {"check_same_thread": False}
    return kwargs


engine: Engine = create_engine(settings.sqlalchemy_url, **_engine_kwargs())


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Foreign keys are off by default in SQLite; WAL keeps reads unblocked by writes."""
    if not settings.is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a database session."""
    with Session(engine) as session:
        yield session


def ensure_storage_paths() -> None:
    """Create the media directory and the SQLite file's parent before first use."""
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    if settings.is_sqlite:
        db_path = engine.url.database
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """Create any tables declared by SQLModel metadata.

    A no-op until Phase 2 introduces the models; importing them here is what
    registers them on the shared metadata.
    """
    import app.models  # noqa: F401  — registers models on SQLModel.metadata

    SQLModel.metadata.create_all(engine)
    table_count = len(SQLModel.metadata.tables)
    logger.info("database schema ready", extra={"table_count": table_count})


def check_database() -> dict[str, Any]:
    """Round-trip a trivial query. Consumed by /health; never raises."""
    started = time.perf_counter()
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        return {
            "status": "ok",
            "dialect": engine.dialect.name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 — health must report, not crash
        logger.error(
            "database health check failed",
            extra={"exception_type": type(exc).__name__},
            exc_info=True,
        )
        return {
            "status": "error",
            "dialect": engine.dialect.name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }
