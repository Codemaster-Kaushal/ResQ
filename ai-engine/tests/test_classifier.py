"""
Tests for the incident classifier — covers AI path (mocked) and fallback path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ai_engine.classification.classifier import classify
from ai_engine.exceptions import AIProviderError, AITimeoutError
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)


def _make_provider(result: ClassificationResult | None = None, raises=None) -> MagicMock:
    """Build a mock AIProvider."""
    provider = MagicMock()
    if raises:
        provider.classify_incident = AsyncMock(side_effect=raises)
    else:
        provider.classify_incident = AsyncMock(return_value=result)
    return provider


class TestClassifierWithProvider:
    @pytest.mark.asyncio
    async def test_uses_ai_result_when_available(self):
        ai_result = ClassificationResult(
            incident_type=IncidentType.FLOODING,
            confidence=0.94,
            reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
        provider = _make_provider(result=ai_result)
        result = await classify("flooded house", provider)
        assert result.incident_type == IncidentType.FLOODING
        assert result.provider == ScoringProvider.LOCAL_GRANITE
        assert result.fallback_state == FallbackState.NORMAL

    @pytest.mark.asyncio
    async def test_falls_back_on_provider_error(self):
        provider = _make_provider(raises=AIProviderError("unavailable"))
        result = await classify("flames and smoke", provider)
        assert result.provider == ScoringProvider.RULE_BASED
        assert result.fallback_state == FallbackState.RULE_BASED
        assert result.incident_type == IncidentType.FIRE

    @pytest.mark.asyncio
    async def test_falls_back_on_timeout(self):
        provider = _make_provider(raises=AITimeoutError("timeout"))
        result = await classify("water is rising", provider)
        assert result.provider == ScoringProvider.RULE_BASED
        assert result.fallback_state == FallbackState.AI_BACKFILL_PENDING

    @pytest.mark.asyncio
    async def test_no_provider_uses_rule_based(self):
        result = await classify("building collapsed", provider=None)
        assert result.provider == ScoringProvider.RULE_BASED

    @pytest.mark.asyncio
    async def test_medical_classification(self):
        provider = _make_provider(raises=AIProviderError("offline"))
        result = await classify("my father is unconscious and bleeding", provider)
        assert result.incident_type == IncidentType.MEDICAL

    @pytest.mark.asyncio
    async def test_trapped_classification(self):
        provider = _make_provider(raises=AIProviderError("offline"))
        result = await classify("three people are trapped and cannot escape", provider)
        assert result.incident_type == IncidentType.TRAPPED_PERSONS


class TestClassifierDirectCases:
    """Integration-style tests using rule-based only (no AI mock)."""

    @pytest.mark.asyncio
    async def test_flooding(self):
        r = await classify("Water has entered our house and the street is flooded.", None)
        assert r.incident_type == IncidentType.FLOODING

    @pytest.mark.asyncio
    async def test_fire(self):
        r = await classify("Flames and heavy smoke are coming from the building.", None)
        assert r.incident_type == IncidentType.FIRE

    @pytest.mark.asyncio
    async def test_medical(self):
        r = await classify("My father is unconscious and bleeding.", None)
        assert r.incident_type == IncidentType.MEDICAL

    @pytest.mark.asyncio
    async def test_landslide(self):
        r = await classify("The entire hillside has collapsed onto the road.", None)
        assert r.incident_type == IncidentType.LANDSLIDE

    @pytest.mark.asyncio
    async def test_infrastructure(self):
        r = await classify("The bridge is damaged and vehicles cannot cross.", None)
        assert r.incident_type == IncidentType.INFRASTRUCTURE

    @pytest.mark.asyncio
    async def test_trapped_persons(self):
        r = await classify("We cannot get out of the second floor.", None)
        assert r.incident_type == IncidentType.TRAPPED_PERSONS

    @pytest.mark.asyncio
    async def test_other(self):
        r = await classify("Something unusual happened in the area.", None)
        assert r.incident_type == IncidentType.OTHER
