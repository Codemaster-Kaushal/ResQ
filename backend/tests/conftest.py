"""Test fixtures.

The database URL is set before app import so the suite never touches the demo
database. raise_server_exceptions=False is required for the 500-path tests: without
it Starlette re-raises into the test instead of returning the error envelope.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMPDIR = Path(tempfile.mkdtemp(prefix="rescuenet-tests-"))

os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR / 'test.db'}"
os.environ["MEDIA_STORAGE_PATH"] = str(_TMPDIR / "media")
os.environ["ENABLE_DEBUG_ROUTES"] = "true"
# INFO, not WARNING: a logging statement that only runs at INFO can raise (a reserved
# `extra` key is a KeyError, not a shadowed field), and a quiet test suite would let
# that reach production as a 500. pytest captures the output, so it costs nothing.
os.environ["LOG_LEVEL"] = "INFO"
# The AI engine is off for the suite: Granite behind a timeout is not
# deterministic, and these tests pin exact severities. Adapter tests
# turn it on explicitly.
os.environ["AI_ENGINE_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
