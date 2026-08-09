"""
FusionEngine — combines text severity and image visual signals into a unified score.

Fusion weights:
    TEXT_FUSION_WEIGHT (default 0.60) + IMAGE_FUSION_WEIGHT (default 0.40) = 1.00

Modes:
    TEXT_ONLY         — no image provided
    TEXT_AND_IMAGE    — both text and image analysis succeeded
    TEXT_ONLY_FALLBACK — image provided but vision failed or unavailable
"""

import logging
from typing import List, Optional

from ai_engine.config import IMAGE_FUSION_WEIGHT, TEXT_FUSION_WEIGHT
from ai_engine.fusion.schemas import FusedResult
from ai_engine.vision.schemas import ImageAnalysisResult, MultimodalMode, VisualSignals
from shared.schemas.severity import RiskFactors, SeverityLabel, SeverityReasonCode

logger = logging.getLogger(__name__)

# Scores for individual visual signals (out of 100)
_VISUAL_SIGNAL_SCORES: dict[str, int] = {
    "flood_water": 65,
    "people_visible": 70,
    "structural_damage": 75,
    "fire_present": 90,
    "smoke_visible": 70,
    "road_blocked": 50,
    "vehicle_submerged": 60,
    "unsafe_environment": 55,
}

# Mapping from visual signals to severity reason codes
_VISUAL_SIGNAL_TO_REASON: dict[str, SeverityReasonCode] = {
    "flood_water": SeverityReasonCode.FLOODING,
    "fire_present": SeverityReasonCode.FIRE_PRESENT,
    "structural_damage": SeverityReasonCode.STRUCTURAL_DAMAGE,
    "road_blocked": SeverityReasonCode.INFRASTRUCTURE_FAILURE,
    "unsafe_environment": SeverityReasonCode.ENVIRONMENTAL_DANGER,
    "people_visible": SeverityReasonCode.PEOPLE_AT_RISK,
}


def _label_from_score(score: int) -> SeverityLabel:
    from ai_engine.severity.config import CRITICAL_THRESHOLD, HIGH_THRESHOLD, MEDIUM_THRESHOLD
    if score >= CRITICAL_THRESHOLD:
        return SeverityLabel.CRITICAL
    if score >= HIGH_THRESHOLD:
        return SeverityLabel.HIGH
    if score >= MEDIUM_THRESHOLD:
        return SeverityLabel.MEDIUM
    return SeverityLabel.LOW


def _score_visual_signals(signals: VisualSignals) -> int:
    """Compute a 0-100 severity score from visual signals."""
    if not signals.visual_confidence or signals.visual_confidence < 0.1:
        return 0

    active_scores: list[int] = []
    for field, score in _VISUAL_signal_scores_items():
        if getattr(signals, field, False):
            active_scores.append(score)

    if not active_scores:
        return 0

    # Use max active score, modulated by confidence
    base = max(active_scores)
    return int(round(base * signals.visual_confidence))


def _VISUAL_signal_scores_items():
    return _VISUAL_SIGNAL_SCORES.items()


def _visual_reason_codes_from_signals(signals: VisualSignals) -> list[SeverityReasonCode]:
    """Extract severity reason codes triggered by visual signals."""
    codes: list[SeverityReasonCode] = []
    for field, code in _VISUAL_SIGNAL_TO_REASON.items():
        if getattr(signals, field, False):
            codes.append(code)
    return codes


class FusionEngine:
    """
    Combines text-derived severity and image visual signals into a single score.
    """

    def __init__(
        self,
        text_weight: float = TEXT_FUSION_WEIGHT,
        image_weight: float = IMAGE_FUSION_WEIGHT,
    ) -> None:
        # Normalize weights in case they don't sum to exactly 1.0
        total = text_weight + image_weight
        if total <= 0:
            total = 1.0
        self._text_weight = text_weight / total
        self._image_weight = image_weight / total

    def fuse(
        self,
        text_severity_score: int,
        text_reason_codes: List[SeverityReasonCode],
        image_analysis: Optional[ImageAnalysisResult],
        classification_confidence: float = 0.5,
    ) -> FusedResult:
        """
        Fuse text severity and image analysis into a single FusedResult.

        Args:
            text_severity_score: Severity score from text analysis (0-100).
            text_reason_codes: Reason codes from text severity engine.
            image_analysis: Optional ImageAnalysisResult. None = text-only mode.
            classification_confidence: Classification confidence (0-1).

        Returns:
            FusedResult with combined score, label, and reason codes.
        """
        # ── Determine mode ────────────────────────────────────────────────────
        if image_analysis is None:
            mode = MultimodalMode.TEXT_ONLY
        elif image_analysis.vision_available:
            mode = MultimodalMode.TEXT_AND_IMAGE
        else:
            mode = MultimodalMode.TEXT_ONLY_FALLBACK

        # ── Calculate fusion weights ──────────────────────────────────────────
        if mode == MultimodalMode.TEXT_ONLY or mode == MultimodalMode.TEXT_ONLY_FALLBACK:
            # No vision — full weight goes to text
            applied_text_weight = 1.0
            applied_image_weight = 0.0
        else:
            applied_text_weight = self._text_weight
            applied_image_weight = self._image_weight

        # ── Calculate image score ─────────────────────────────────────────────
        image_score: Optional[int] = None
        visual_reason_codes = []

        if mode == MultimodalMode.TEXT_AND_IMAGE and image_analysis is not None:
            signals = image_analysis.visual_signals
            image_score = _score_visual_signals(signals)
            visual_reason_codes = image_analysis.visual_reason_codes or []

        # ── Combine scores ────────────────────────────────────────────────────
        if image_score is not None:
            combined_raw = (
                applied_text_weight * text_severity_score
                + applied_image_weight * image_score
            )
        else:
            combined_raw = float(text_severity_score)

        final_score = int(round(max(0.0, min(100.0, combined_raw))))
        final_label = _label_from_score(final_score)

        # ── Merge reason codes ────────────────────────────────────────────────
        combined_codes: list[SeverityReasonCode] = list(text_reason_codes)
        if image_score is not None:
            visual_sev_codes = _visual_reason_codes_from_signals(
                image_analysis.visual_signals  # type: ignore[union-attr]
            )
            for code in visual_sev_codes:
                if code not in combined_codes:
                    combined_codes.append(code)

        if not combined_codes:
            combined_codes = [SeverityReasonCode.INSUFFICIENT_RISK_INFORMATION]

        logger.debug(
            "Fusion: mode=%s text_score=%d image_score=%s final=%d label=%s",
            mode, text_severity_score, image_score, final_score, final_label,
        )

        return FusedResult(
            severity_score=final_score,
            severity_label=final_label,
            mode=mode,
            text_severity_score=text_severity_score,
            image_severity_score=image_score,
            severity_reason_codes=combined_codes,
            visual_reason_codes=visual_reason_codes,
            text_weight_applied=applied_text_weight,
            image_weight_applied=applied_image_weight,
        )
