"""
Tests for TriageService — end-to-end pipeline with mocked AI provider.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ai_engine.exceptions import AIProviderError, AITimeoutError
from ai_engine.triage_service import TriageService
from shared.schemas.classification import (
    ClassificationReasonCode,
    ClassificationResult,
    FallbackState,
    IncidentType,
    ScoringProvider,
)
from shared.schemas.incident_ai import IncidentAIInput, IncidentAIOutput
from shared.schemas.severity import RiskFactors


def _make_provider(
    classify_result=None,
    classify_raises=None,
    risk_result=None,
    risk_raises=None,
) -> MagicMock:
    provider = MagicMock()
    if classify_raises:
        provider.classify_incident = AsyncMock(side_effect=classify_raises)
    else:
        provider.classify_incident = AsyncMock(return_value=classify_result)
    if risk_raises:
        provider.extract_risk_factors = AsyncMock(side_effect=risk_raises)
    else:
        provider.extract_risk_factors = AsyncMock(return_value=risk_result or RiskFactors())
    return provider


def _flood_input(report_id="RPT-001", image=None) -> IncidentAIInput:
    return IncidentAIInput(
        report_id=report_id,
        description="Five people are trapped and one is injured in a flooded house",
        image=image,
        latitude=12.9716,
        longitude=77.5946,
        reporter_pseudonym="USER-A7F2",
    )


class TestTriageServiceWithMockedAI:
    @pytest.mark.asyncio
    async def test_successful_triage_returns_output(self):
        classify_res = ClassificationResult(
            incident_type=IncidentType.FLOODING,
            confidence=0.94,
            reason_codes=[ClassificationReasonCode.FLOOD_WATER_DETECTED],
            provider=ScoringProvider.LOCAL_GRANITE,
            fallback_state=FallbackState.NORMAL,
        )
        risk_res = RiskFactors(
            people_at_risk=5,
            trapped_persons=True,
            medical_emergency=True,
            rapidly_rising_water=True,
        )
        provider = _make_provider(classify_result=classify_res, risk_result=risk_res)
        service = TriageService(provider=provider)
        result = await service.triage(_flood_input())

        assert isinstance(result, IncidentAIOutput)
        assert result.incident_type == IncidentType.FLOODING
        assert result.classification_confidence == 0.94
        assert result.severity_score > 0
        assert len(result.severity_reason_codes) >= 1
        assert result.scoring_provider == ScoringProvider.LOCAL_GRANITE
        assert result.fallback_state == FallbackState.NORMAL

    @pytest.mark.asyncio
    async def test_fallback_on_provider_error(self):
        provider = _make_provider(classify_raises=AIProviderError("down"))
        service = TriageService(provider=provider)
        result = await service.triage(_flood_input())

        # Must still return a valid result
        assert isinstance(result, IncidentAIOutput)
        assert result.scoring_provider == ScoringProvider.RULE_BASED
        assert result.fallback_state == FallbackState.RULE_BASED

    @pytest.mark.asyncio
    async def test_fallback_on_timeout(self):
        provider = _make_provider(classify_raises=AITimeoutError("timeout"))
        service = TriageService(provider=provider)
        result = await service.triage(_flood_input())

        assert result.scoring_provider == ScoringProvider.RULE_BASED
        assert result.fallback_state == FallbackState.AI_BACKFILL_PENDING

    @pytest.mark.asyncio
    async def test_no_provider_uses_rule_based(self):
        service = TriageService(provider=None)
        result = await service.triage(_flood_input())
        assert result.scoring_provider == ScoringProvider.RULE_BASED
        assert isinstance(result, IncidentAIOutput)

    @pytest.mark.asyncio
    async def test_image_null_succeeds(self):
        """FR-9: image=None must not cause failure."""
        service = TriageService(provider=None)
        result = await service.triage(_flood_input(image=None))
        assert result.severity_score >= 0

    @pytest.mark.asyncio
    async def test_severity_reason_codes_never_empty(self):
        """FR-8: reason codes always present."""
        service = TriageService(provider=None)
        result = await service.triage(_flood_input())
        assert len(result.severity_reason_codes) >= 1

    @pytest.mark.asyncio
    async def test_report_id_preserved(self):
        service = TriageService(provider=None)
        result = await service.triage(_flood_input(report_id="RPT-999"))
        assert result.report_id == "RPT-999"


class TestTriageServiceScenarios:
    """Scenario-based tests covering different incident types."""

    @pytest.mark.asyncio
    async def test_flood_report(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(report_id="RPT-F1", description="Water has entered our house and the street is flooded.")
        result = await service.triage(inp)
        assert result.incident_type == IncidentType.FLOODING

    @pytest.mark.asyncio
    async def test_medical_report(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(report_id="RPT-M1", description="My father is unconscious and bleeding.")
        result = await service.triage(inp)
        assert result.incident_type == IncidentType.MEDICAL

    @pytest.mark.asyncio
    async def test_fire_report(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(report_id="RPT-FR1", description="Flames and heavy smoke are coming from the building.")
        result = await service.triage(inp)
        assert result.incident_type == IncidentType.FIRE

    @pytest.mark.asyncio
    async def test_structural_collapse_report(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(report_id="RPT-SC1", description="The building collapsed. People are inside.")
        result = await service.triage(inp)
        assert result.incident_type in (IncidentType.STRUCTURAL_COLLAPSE, IncidentType.TRAPPED_PERSONS)

    @pytest.mark.asyncio
    async def test_critical_five_trapped_injured(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(
            report_id="RPT-CRIT",
            description="Five people trapped, one injured, rapidly rising water.",
        )
        result = await service.triage(inp)
        from shared.schemas.severity import SeverityLabel
        assert result.severity_label == SeverityLabel.CRITICAL

    @pytest.mark.asyncio
    async def test_low_severity_small_water(self):
        service = TriageService(provider=None)
        inp = IncidentAIInput(report_id="RPT-LOW", description="Small amount of water near the road.")
        result = await service.triage(inp)
        from shared.schemas.severity import SeverityLabel
        assert result.severity_label in (SeverityLabel.LOW, SeverityLabel.MEDIUM)
