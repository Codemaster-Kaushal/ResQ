"""Phase 4: the fallback chain.

TRD §10: "The AI router never raises. Ever." Every test here is an attempt to make it
do so — timeouts, exceptions, garbage results, a broken local scorer — and to confirm
that a valid TriageResult comes back regardless.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ai.base import LOCAL_PROVIDER_NAME, ProviderError, TriageResult
from app.ai.local import LocalScorer
from app.ai.router import TriageRouter
from app.config import settings
from app.models.enums import IncidentType

pytestmark = pytest.mark.asyncio

TEXT = "building has collapsed, six people trapped under the debris"


class FakeProvider:
    """A provider that fails, stalls, or answers on command."""

    def __init__(self, name: str, *, available: bool = True, behaviour: str = "ok",
                 incident: IncidentType = IncidentType.FIRE) -> None:
        self.name = name
        self._available = available
        self._behaviour = behaviour
        self._incident = incident
        self.calls = 0

    def is_available(self) -> bool:
        if self._behaviour == "available_raises":
            raise RuntimeError("availability check exploded")
        return self._available

    async def classify(self, text: str, image_bytes: bytes | None) -> TriageResult:
        self.calls += 1
        if self._behaviour == "raise":
            raise ProviderError(f"{self.name} is down")
        if self._behaviour == "crash":
            raise RuntimeError("unexpected internal error")
        if self._behaviour == "hang":
            await asyncio.sleep(30)
        if self._behaviour == "garbage":
            raise ValueError("could not parse response")
        return TriageResult(incident_type=self._incident, confidence=0.9)


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    """Keep the suite quick; the behaviour under test is unchanged."""
    monkeypatch.setattr(settings, "ai_timeout_seconds", 0.05)
    monkeypatch.setattr(settings, "ai_retry_attempts", 1)


def build(*providers, order=None) -> TriageRouter:
    mapping = {provider.name: provider for provider in providers}
    return TriageRouter(providers=mapping, order=order or [p.name for p in providers])


# --- The guarantee ----------------------------------------------------------------------


async def test_every_provider_failing_still_returns_a_valid_result() -> None:
    """The single most important behaviour in the codebase."""
    routed = await build(
        FakeProvider("gemini", behaviour="raise"),
        FakeProvider("groq", behaviour="crash"),
        LocalScorer(),
    ).classify(TEXT)

    assert routed.provider == LOCAL_PROVIDER_NAME
    assert routed.result.incident_type == IncidentType.TRAPPED_PERSONS
    assert routed.degraded is True


async def test_router_survives_even_a_broken_local_scorer() -> None:
    """If the floor itself fails that is a bug, not an outage — but ingestion lives."""
    routed = await build(
        FakeProvider("gemini", behaviour="raise"),
        FakeProvider(LOCAL_PROVIDER_NAME, behaviour="crash"),
    ).classify(TEXT)

    assert isinstance(routed.result, TriageResult)
    assert routed.result.incident_type == IncidentType.OTHER
    assert routed.result.confidence == 0.0
    assert routed.degraded is True


async def test_a_provider_raising_during_the_availability_check_is_skipped() -> None:
    routed = await build(
        FakeProvider("gemini", behaviour="available_raises"),
        LocalScorer(),
    ).classify(TEXT)

    assert routed.provider == LOCAL_PROVIDER_NAME


@pytest.mark.parametrize("behaviour", ["raise", "crash", "hang", "garbage"])
async def test_no_failure_mode_escapes_the_router(behaviour: str) -> None:
    routed = await build(
        FakeProvider("gemini", behaviour=behaviour), LocalScorer()
    ).classify(TEXT)

    assert routed.provider == LOCAL_PROVIDER_NAME
    assert 0 <= routed.result.confidence <= 1


# --- Chain order and short-circuiting ------------------------------------------------------


async def test_the_first_healthy_provider_wins() -> None:
    gemini = FakeProvider("gemini", incident=IncidentType.FIRE)
    groq = FakeProvider("groq", incident=IncidentType.FLOODING)

    routed = await build(gemini, groq, LocalScorer()).classify(TEXT)

    assert routed.provider == "gemini"
    assert routed.result.incident_type == IncidentType.FIRE
    assert groq.calls == 0  # never reached
    assert routed.degraded is False


async def test_the_chain_moves_on_after_a_failure() -> None:
    gemini = FakeProvider("gemini", behaviour="raise")
    groq = FakeProvider("groq", incident=IncidentType.FLOODING)

    routed = await build(gemini, groq, LocalScorer()).classify(TEXT)

    assert routed.provider == "groq"
    assert routed.attempted == ("gemini", "groq")


async def test_unavailable_providers_are_skipped_without_being_called() -> None:
    """No API key means no pointless four-second wait on demo day."""
    gemini = FakeProvider("gemini", available=False)

    routed = await build(gemini, LocalScorer()).classify(TEXT)

    assert gemini.calls == 0
    assert "gemini" not in routed.attempted
    assert routed.provider == LOCAL_PROVIDER_NAME
    # Nothing was actually tried and failed, so this is not a degraded run.
    assert routed.degraded is False


async def test_order_follows_configuration() -> None:
    gemini = FakeProvider("gemini", incident=IncidentType.FIRE)
    groq = FakeProvider("groq", incident=IncidentType.FLOODING)

    routed = await build(gemini, groq, LocalScorer(), order=["groq", "gemini", "local"]).classify(TEXT)

    assert routed.provider == "groq"


async def test_local_is_appended_when_configuration_omits_it() -> None:
    """The floor is never optional."""
    router = build(FakeProvider("gemini", behaviour="raise"), LocalScorer(), order=["gemini"])

    assert router.order[-1] == LOCAL_PROVIDER_NAME
    assert (await router.classify(TEXT)).provider == LOCAL_PROVIDER_NAME


async def test_unknown_provider_names_in_configuration_are_ignored() -> None:
    router = build(LocalScorer(), order=["nonexistent", "local"])

    assert router.order == [LOCAL_PROVIDER_NAME]
    assert (await router.classify(TEXT)).provider == LOCAL_PROVIDER_NAME


# --- Timeout and retry ----------------------------------------------------------------------


async def test_a_stalled_provider_is_abandoned_not_waited_on() -> None:
    router = build(FakeProvider("gemini", behaviour="hang"), LocalScorer())

    started = asyncio.get_event_loop().time()
    routed = await router.classify(TEXT)
    elapsed = asyncio.get_event_loop().time() - started

    assert routed.provider == LOCAL_PROVIDER_NAME
    # Two attempts at 50 ms, nowhere near the 30 s the fake wanted to sleep for.
    assert elapsed < 1.0


async def test_each_provider_is_retried_the_configured_number_of_times(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_retry_attempts", 1)
    gemini = FakeProvider("gemini", behaviour="raise")

    await build(gemini, LocalScorer()).classify(TEXT)

    assert gemini.calls == 2  # first attempt plus one retry


async def test_retries_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_retry_attempts", 0)
    gemini = FakeProvider("gemini", behaviour="raise")

    await build(gemini, LocalScorer()).classify(TEXT)

    assert gemini.calls == 1


# --- Provenance ---------------------------------------------------------------------------------


async def test_availability_is_reportable_for_governance() -> None:
    router = build(
        FakeProvider("gemini", available=False), FakeProvider("groq"), LocalScorer()
    )

    assert router.available_providers() == {"gemini": False, "groq": True, "local": True}


async def test_attempted_providers_are_recorded() -> None:
    routed = await build(
        FakeProvider("gemini", behaviour="raise"),
        FakeProvider("groq", behaviour="raise"),
        LocalScorer(),
    ).classify(TEXT)

    assert routed.attempted == ("gemini", "groq", LOCAL_PROVIDER_NAME)
    assert routed.provider == LOCAL_PROVIDER_NAME
