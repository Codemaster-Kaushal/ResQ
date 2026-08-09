from provenance import error_response, get_provenance, get_thresholds


def test_get_provenance_shape():
    provenance = get_provenance("hybrid", "NORMAL")

    assert provenance["triage_provider"] == "local_granite"
    assert provenance["vision_provider"] == "local_gemma"
    assert provenance["scoring_provider"] == "hybrid"
    assert provenance["fallback_state"] == "NORMAL"
    assert provenance["external_api_calls"] is False
    assert "triage_model" in provenance
    assert "vision_model" in provenance
    assert "embedding_provider" in provenance


def test_get_thresholds_shape():
    thresholds = get_thresholds()
    assert thresholds == {
        "critical": 80,
        "high": 60,
        "medium": 40,
        "authenticity_review": 50,
    }


def test_error_response_shape():
    error = error_response(
        "MODEL_UNAVAILABLE",
        "Local AI model unavailable",
        fallback="rule_based",
        retryable=True,
    )

    assert error["error"]["code"] == "MODEL_UNAVAILABLE"
    assert error["error"]["fallback"] == "rule_based"
    assert error["error"]["retryable"] is True
