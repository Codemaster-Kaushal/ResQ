"""Phase 4: remote providers and defensive JSON parsing.

Driven entirely through httpx.MockTransport — no network is touched, so the suite still
passes with the machine offline (NFR-2). What is tested is what we actually control:
the request we build, and how robustly we read what comes back.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai.base import ProviderError, TriageResult
from app.ai.gemini import GeminiProvider
from app.ai.groq import GroqProvider
from app.ai.parsing import extract_json_object
from app.config import settings
from app.models.enums import IncidentType

pytestmark = pytest.mark.asyncio

VALID_PAYLOAD = {
    "incident_type": "trapped_persons",
    "life_risk_terms": ["trapped", "no_exit"],
    "people_affected_estimate": 6,
    "vulnerability_terms": ["children"],
    "visual_severity_modifier": 5,
    "confidence": 0.82,
}


def gemini_response(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def groq_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Defensive parsing (TRD §5) -------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"incident_type": "fire"}',
        '```json\n{"incident_type": "fire"}\n```',
        '```\n{"incident_type": "fire"}\n```',
        'Here is the JSON you asked for:\n{"incident_type": "fire"}',
        '{"incident_type": "fire"}\n\nLet me know if you need anything else.',
        '  \n {"incident_type": "fire"}  \n ',
        'Sure!\n```json\n{"incident_type": "fire"}\n```\nHope that helps.',
    ],
)
async def test_json_is_recovered_from_conversational_output(raw: str) -> None:
    assert extract_json_object(raw)["incident_type"] == "fire"


async def test_nested_objects_survive_extraction() -> None:
    raw = 'text before {"a": {"b": [1, 2]}, "c": "}"} text after'

    assert extract_json_object(raw) == {"a": {"b": [1, 2]}, "c": "}"}


@pytest.mark.parametrize("raw", ["", "   ", "no json here", "[1, 2, 3]", "{broken"])
async def test_unparseable_output_raises_a_provider_error(raw: str) -> None:
    """Which the router catches and turns into a fall-through."""
    with pytest.raises(ProviderError):
        extract_json_object(raw)


# --- Gemini ------------------------------------------------------------------------------


async def test_gemini_sends_the_image_and_parses_the_result(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_response(json.dumps(VALID_PAYLOAD)))

    async with mock_client(handler) as client:
        result = await GeminiProvider(client).classify("six people trapped", b"fake-image-bytes")

    assert result.incident_type == IncidentType.TRAPPED_PERSONS
    assert result.people_affected_estimate == 6
    assert result.visual_severity_modifier == 5

    assert settings.gemini_model in captured["url"]
    assert "key=test-key" in captured["url"]
    parts = captured["body"]["contents"][0]["parts"]
    assert any("inline_data" in part for part in parts)


async def test_gemini_omits_image_data_when_there_is_no_image(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_response(json.dumps(VALID_PAYLOAD)))

    async with mock_client(handler) as client:
        await GeminiProvider(client).classify("a wall collapsed", None)

    parts = captured["body"]["contents"][0]["parts"]
    assert not any("inline_data" in part for part in parts)


async def test_gemini_is_unavailable_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", None)

    assert GeminiProvider().is_available() is False
    with pytest.raises(ProviderError):
        await GeminiProvider().classify("text", None)


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_gemini_http_errors_become_provider_errors(monkeypatch, status: int) -> None:
    """429 in particular: free-tier rate limits are the expected demo-day failure."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    async with mock_client(lambda _r: httpx.Response(status, json={})) as client:
        with pytest.raises(ProviderError):
            await GeminiProvider(client).classify("text", None)


@pytest.mark.parametrize(
    "body",
    [{}, {"candidates": []}, {"candidates": [{"content": {"parts": []}}]},
     {"candidates": [{"content": {"parts": [{"text": "   "}]}}]}],
)
async def test_gemini_malformed_envelopes_become_provider_errors(monkeypatch, body) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    async with mock_client(lambda _r: httpx.Response(200, json=body)) as client:
        with pytest.raises(ProviderError):
            await GeminiProvider(client).classify("text", None)


async def test_gemini_network_failure_becomes_a_provider_error(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    def handler(_request):
        raise httpx.ConnectError("network is unreachable")

    async with mock_client(handler) as client:
        with pytest.raises(ProviderError):
            await GeminiProvider(client).classify("text", None)


async def test_an_incident_type_outside_the_taxonomy_is_rejected(monkeypatch) -> None:
    """A model that cannot pick from the fixed taxonomy is not one to trust."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    payload = dict(VALID_PAYLOAD, incident_type="alien_invasion")

    async with mock_client(
        lambda _r: httpx.Response(200, json=gemini_response(json.dumps(payload)))
    ) as client:
        with pytest.raises(Exception):  # noqa: B017 — validation error, caught by the router
            await GeminiProvider(client).classify("text", None)


# --- Groq -------------------------------------------------------------------------------------


async def test_groq_authenticates_and_parses(monkeypatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=groq_response(json.dumps(VALID_PAYLOAD)))

    async with mock_client(handler) as client:
        result = await GroqProvider(client).classify("six people trapped", None)

    assert result.incident_type == IncidentType.TRAPPED_PERSONS
    assert captured["auth"] == "Bearer groq-key"
    assert captured["body"]["model"] == settings.groq_model


async def test_groq_never_claims_a_visual_judgement(monkeypatch) -> None:
    """It is text-only, so any modifier it returns is unfounded and is discarded."""
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    payload = dict(VALID_PAYLOAD, visual_severity_modifier=9)

    async with mock_client(
        lambda _r: httpx.Response(200, json=groq_response(json.dumps(payload)))
    ) as client:
        result = await GroqProvider(client).classify("text", b"an image it cannot see")

    assert result.visual_severity_modifier == 0


async def test_groq_is_unavailable_without_a_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", None)

    assert GroqProvider().is_available() is False


async def test_groq_handles_fenced_json(monkeypatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    fenced = f"```json\n{json.dumps(VALID_PAYLOAD)}\n```"

    async with mock_client(
        lambda _r: httpx.Response(200, json=groq_response(fenced))
    ) as client:
        result = await GroqProvider(client).classify("text", None)

    assert result.incident_type == IncidentType.TRAPPED_PERSONS


# --- Lenient where it is safe -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "sent", "expected"),
    [
        ("visual_severity_modifier", 50, 10),
        ("visual_severity_modifier", -50, -10),
        ("confidence", 5.0, 1.0),
        ("confidence", -1, 0.0),
        ("people_affected_estimate", -3, 0),
        ("people_affected_estimate", "twelve", None),
    ],
)
async def test_out_of_range_numbers_are_clamped_not_discarded(field, sent, expected) -> None:
    """Discarding a whole extraction over one bad integer is a poor trade."""
    result = TriageResult.model_validate({**VALID_PAYLOAD, field: sent})

    assert getattr(result, field) == expected


async def test_a_string_term_list_is_coerced() -> None:
    result = TriageResult.model_validate({**VALID_PAYLOAD, "life_risk_terms": "trapped"})

    assert result.life_risk_terms == ["trapped"]


async def test_missing_optional_fields_fall_back_to_defaults() -> None:
    result = TriageResult.model_validate({"incident_type": "fire"})

    assert result.incident_type == IncidentType.FIRE
    assert result.life_risk_terms == []
    assert result.visual_severity_modifier == 0
