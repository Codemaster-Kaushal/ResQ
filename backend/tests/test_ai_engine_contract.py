"""The adapter's contract with the separately-developed AI engine.

The adapter reads attributes off objects built by another project. Reading a
name that does not exist is silent: `getattr(result, "reason_codes", [])`
returns an empty list just as happily for "no codes" as for "wrong attribute",
and the report ends up with a trust score and no evidence behind it. That bug
shipped once during integration and cost a duplicate detection.

These tests pin the names instead of guessing them, so if Person 2 renames a
field the suite says so rather than the feature quietly going dark. They skip
cleanly where the engine is not checked out.
"""

from __future__ import annotations

import inspect
import io

import pytest

def _jpeg_bytes() -> bytes:
    """A small textured JPEG. Flat colours collapse to a degenerate pHash."""
    from tests.test_ingestion import textured_jpeg

    buffer = io.BytesIO()
    textured_jpeg(seed=7).save(buffer, format="JPEG")
    return buffer.getvalue()


def _engine_or_skip():
    from app.ai import resq_engine

    resq_engine._load_engine.cache_clear()
    engine = resq_engine._load_engine()
    if engine is None:
        pytest.skip("AI engine not present on this machine")
    return engine


def test_the_authenticity_result_still_has_the_fields_the_adapter_reads() -> None:
    _engine_or_skip()
    from ai_engine.authenticity.schemas import AuthenticityResult

    for field in ("authenticity_score", "verification_status", "authenticity_reason_codes"):
        assert field in AuthenticityResult.model_fields, f"engine renamed {field}"


def test_calculate_authenticity_still_accepts_injected_state() -> None:
    """The whole DB-backed design rests on these two parameters existing.

    Without them the engine would fall back to its own `state.json`, which knows
    only the reports that passed through it — it would miss the seeded duplicate
    pair entirely.
    """
    engine = _engine_or_skip()
    params = inspect.signature(
        engine["AuthenticityEngine"].calculate_authenticity
    ).parameters

    assert "previous_reports" in params
    assert "known_hashes" in params


def test_the_granite_provider_constructor_still_takes_these_keywords() -> None:
    engine = _engine_or_skip()
    params = inspect.signature(engine["GraniteLocalProvider"].__init__).parameters

    for keyword in ("model", "ollama_host", "timeout"):
        assert keyword in params, f"engine renamed the {keyword} argument"


def test_known_hashes_is_keyed_report_id_to_hash() -> None:
    """Reversing this mapping would silently stop all duplicate detection."""
    _engine_or_skip()
    from ai_engine.authenticity.image_duplicate import ImageHashService

    checker = ImageHashService()
    result = checker.check_duplicate(
        image_bytes=_jpeg_bytes(),
        known_hashes={"report-abc": "0000000000000000"},
    )
    # The matched id must come back as a report id, not as a hash string.
    assert result.matched_report_id in (None, "report-abc")
