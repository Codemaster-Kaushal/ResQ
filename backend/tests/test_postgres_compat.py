"""The schema and every live query must compile for Postgres, not just SQLite.

TRD §1: SQLite for dev and demo, Postgres-compatible in production. Nothing here
connects to a server — SQLAlchemy can render DDL and compile statements for a dialect
offline, which is exactly what catches a SQLite-only construct before deploy day
rather than during it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select as sa_select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlmodel import SQLModel, select

import app.models  # noqa: F401  — registers the tables
from app.config import Settings
from app.models import Assignment, ProcessEvent, Report, ReportStatus, Responder, ResponderStatus

PG = postgresql.dialect()

TABLES = ["report", "responder", "assignment", "process_event"]


def ddl_for(table_name: str) -> str:
    table = SQLModel.metadata.tables[table_name]
    return str(CreateTable(table).compile(dialect=PG))


# --- Connection strings ------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # What Supabase actually puts on the clipboard.
        ("postgresql://u:p@db.abc.supabase.co:5432/postgres",
         "postgresql+psycopg://u:p@db.abc.supabase.co:5432/postgres"),
        # The legacy scheme Heroku and some tools still emit.
        ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
        # Already explicit — left alone.
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./rescuenet.db", "sqlite:///./rescuenet.db"),
    ],
)
def test_connection_strings_are_normalised(given: str, expected: str) -> None:
    """A pasted Supabase URL resolves to psycopg2 by default, which we do not ship."""
    assert Settings(database_url=given).sqlalchemy_url == expected


def test_dialect_detection(tmp_path) -> None:
    postgres = Settings(database_url="postgresql://u:p@h/db")
    sqlite = Settings(database_url="sqlite:///./x.db")

    assert postgres.is_postgres and not postgres.is_sqlite
    assert sqlite.is_sqlite and not sqlite.is_postgres


def test_the_psycopg_driver_is_installed() -> None:
    """Needed before Phase 9, after which TRD §10 freezes dependencies."""
    import psycopg

    assert psycopg.__version__


# --- Schema ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", TABLES)
def test_every_table_renders_for_postgres(table_name: str) -> None:
    statement = ddl_for(table_name)

    assert statement.strip().startswith("CREATE TABLE")
    assert table_name in statement


@pytest.mark.parametrize("table_name", TABLES)
def test_every_index_renders_for_postgres(table_name: str) -> None:
    table = SQLModel.metadata.tables[table_name]

    for index in table.indexes:
        rendered = str(CreateIndex(index).compile(dialect=PG)).upper()
        # Unique indexes render as CREATE UNIQUE INDEX, so match on both words.
        assert rendered.startswith("CREATE") and "INDEX" in rendered


def test_enums_are_portable_varchars_not_native_types() -> None:
    """native_enum=False on purpose: a Postgres ENUM needs ALTER TYPE every time the
    taxonomy grows, and the demo adds incident types as the product evolves.

    No CHECK constraint either — one listing the allowed values would need a migration
    to extend for exactly the same reason. Validity is enforced at the application
    boundary instead.
    """
    statement = ddl_for("report")

    assert "status VARCHAR(32) NOT NULL" in statement
    assert "incident_type VARCHAR(32)" in statement
    assert "CREATE TYPE" not in statement
    assert "CHECK" not in statement.upper()


def test_json_columns_render() -> None:
    assert "JSON" in ddl_for("report").upper()
    assert "JSON" in ddl_for("process_event").upper()


def test_uuid_primary_keys_use_the_native_type() -> None:
    assert "UUID" in ddl_for("report").upper()


def test_the_metadata_column_keeps_its_name() -> None:
    assert "metadata JSON" in ddl_for("process_event")


def test_timestamps_are_stored_without_a_timezone() -> None:
    """Matches the naive-UTC convention, so Postgres behaves exactly as SQLite does."""
    statement = ddl_for("report")

    assert "client_created_at TIMESTAMP WITHOUT TIME ZONE" in statement
    assert "received_at TIMESTAMP WITHOUT TIME ZONE" in statement


def test_no_sqlite_only_constructs_leak_into_the_schema() -> None:
    for table_name in TABLES:
        statement = ddl_for(table_name).upper()
        assert "AUTOINCREMENT" not in statement
        assert "PRAGMA" not in statement


# --- Queries -----------------------------------------------------------------------------


def compiles(statement) -> str:
    return str(statement.compile(dialect=PG, compile_kwargs={"literal_binds": True}))


def test_the_queue_query_compiles() -> None:
    """app.services.priority.build_queue"""
    statement = select(Report).where(
        Report.status.in_((ReportStatus.VERIFIED, ReportStatus.QUEUED))
    )

    assert "IN (" in compiles(statement)


def test_the_duplicate_lookup_compiles() -> None:
    """app.services.authenticity"""
    assert "image_phash" in compiles(select(Report).where(Report.image_phash.is_not(None)))


def test_the_candidate_query_compiles() -> None:
    """app.services.dispatch.find_candidates"""
    statement = select(Responder).where(Responder.status == ResponderStatus.AVAILABLE)

    assert "available" in compiles(statement)


def test_the_pending_sweep_queries_compile() -> None:
    """app.services.triage / authenticity retry queues"""
    compiles(select(Report.id).where(Report.severity_score.is_(None)))
    compiles(
        select(Report.id)
        .where(Report.authenticity_score.is_(None))
        .where(Report.severity_score.is_not(None))
        .order_by(Report.received_at, Report.id)
    )


def test_the_event_log_query_compiles() -> None:
    statement = sa_select(ProcessEvent).where(
        ProcessEvent.case_id == uuid.uuid4(), ProcessEvent.activity == "ASSIGNED"
    )

    assert "process_event" in compiles(statement)


def test_the_assignment_lookup_compiles() -> None:
    statement = select(Assignment).where(Assignment.report_id == uuid.uuid4())

    assert "assignment" in compiles(statement)


def test_pagination_compiles() -> None:
    statement = (
        select(Report).order_by(Report.received_at.desc(), Report.id).limit(50).offset(100)
    )
    rendered = compiles(statement)

    assert "LIMIT" in rendered and "OFFSET" in rendered
