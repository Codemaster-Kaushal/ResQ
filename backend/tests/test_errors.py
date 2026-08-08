"""Phase 1 acceptance: every failure returns the typed envelope, never a stack trace.

NFR-5: no unhandled exception may reach the client.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def assert_envelope(payload: dict, expected_code: str) -> dict:
    """Every error body must match {"error": {code, message, detail}} (TRD §6)."""
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == {"code", "message", "detail"}
    assert error["code"] == expected_code
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["detail"], dict)
    return error


def test_unhandled_exception_returns_envelope_not_traceback(client: TestClient) -> None:
    response = client.get("/api/_debug/boom")

    assert response.status_code == 500
    assert_envelope(response.json(), "INTERNAL_ERROR")

    # The traceback belongs in the log, not the response body.
    body = response.text
    assert "Traceback" not in body
    assert "RuntimeError" not in body
    assert "deliberate unhandled exception" not in body


def test_typed_app_error_carries_code_and_detail(client: TestClient) -> None:
    response = client.get("/api/_debug/app-error")

    assert response.status_code == 404
    error = assert_envelope(response.json(), "NOT_FOUND")
    assert error["detail"]["hint"]


def test_service_unavailable_maps_to_its_own_code(client: TestClient) -> None:
    response = client.get("/api/_debug/service-unavailable")

    assert response.status_code == 503
    assert_envelope(response.json(), "DATABASE_UNAVAILABLE")


def test_unknown_route_returns_not_found_envelope(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    error = assert_envelope(response.json(), "NOT_FOUND")
    assert error["detail"]["path"] == "/api/does-not-exist"


def test_wrong_method_returns_method_not_allowed_envelope(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405
    assert_envelope(response.json(), "METHOD_NOT_ALLOWED")


def test_validation_failure_lists_offending_fields(client: TestClient) -> None:
    """Query validation is exercised via a route added just for this test."""
    from fastapi import Query

    from app.main import app

    @app.get("/api/_debug/needs-int")
    def _needs_int(count: int = Query()) -> dict[str, int]:  # pragma: no cover - via HTTP
        return {"count": count}

    response = client.get("/api/_debug/needs-int", params={"count": "not-a-number"})

    assert response.status_code == 422
    error = assert_envelope(response.json(), "VALIDATION_ERROR")
    assert error["detail"]["fields"][0]["location"] == "query.count"


def test_error_responses_keep_the_request_id_header(client: TestClient) -> None:
    response = client.get("/api/_debug/app-error", headers={"X-Request-ID": "err-trace"})

    assert response.headers["X-Request-ID"] == "err-trace"


def test_internal_error_is_correlatable_to_the_log(client: TestClient) -> None:
    """A 500 bypasses the request middleware, so correlation is easy to lose here.

    Without the ID the client cannot quote anything and the operator cannot find the
    traceback — "the incident has been logged" would be an empty promise.
    """
    response = client.get("/api/_debug/boom", headers={"X-Request-ID": "boom-trace"})

    assert response.status_code == 500
    error = assert_envelope(response.json(), "INTERNAL_ERROR")
    assert error["detail"]["request_id"] == "boom-trace"
    assert response.headers["X-Request-ID"] == "boom-trace"
