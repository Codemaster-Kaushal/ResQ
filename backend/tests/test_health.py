"""Phase 1 acceptance: /health reports DB status, /docs renders."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_reports_database_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "ok"
    assert body["database"]["dialect"] == "sqlite"
    assert body["database"]["latency_ms"] >= 0
    assert body["uptime_seconds"] >= 0
    assert body["version"]


def test_root_returns_service_metadata(client: TestClient) -> None:
    body = client.get("/").json()

    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


def test_openapi_schema_renders(client: TestClient) -> None:
    """/docs is a static page over this schema — if it builds, /docs renders."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/health" in schema["paths"]
    assert client.get("/docs").status_code == 200


def test_request_id_header_is_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


def test_request_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["X-Request-ID"]) == 32
