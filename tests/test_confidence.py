"""Composite confidence routing: deterministic-led, model capped, failures forced."""

from freight.extraction import (
    ValidatedExtraction,
    ValidationFailure,
    score,
)


def _complete_rate_request() -> ValidatedExtraction:
    return ValidatedExtraction(
        intent="rate_request",
        origin_city="Chicago",
        origin_state="IL",
        dest_city="Dallas",
        dest_state="TX",
        equipment="dry_van",
        weight_lbs=42000,
    )


def test_validation_failure_forces_review_even_at_model_one() -> None:
    outcome = score(ValidationFailure(reasons=["invalid_origin_state:'X'"]), 1.0)
    assert outcome.route == "needs_review"
    assert outcome.score == 0.0
    assert "invalid_origin_state:'X'" in outcome.reasons


def test_complete_valid_is_processed() -> None:
    assert score(_complete_rate_request(), 0.9).route == "processed"
    # Even with no model self-report, a complete record clears the threshold.
    assert score(_complete_rate_request(), None).route == "processed"


def test_sparse_valid_routes_to_review() -> None:
    sparse = ValidatedExtraction(intent="rate_request", origin_state="IL")
    outcome = score(sparse, 0.5)
    assert outcome.route == "needs_review"
    assert any("low_confidence" in r for r in outcome.reasons)


def test_model_self_report_cannot_rescue_incomplete() -> None:
    # rate_request with NO fields: model claims 1.0 but can't cross the threshold.
    empty = ValidatedExtraction(intent="rate_request")
    outcome = score(empty, 1.0)
    assert outcome.route == "needs_review"
    assert outcome.score < 0.7


def test_non_route_intent_is_processed() -> None:
    # A clean negotiation needs only a valid intent.
    assert score(ValidatedExtraction(intent="negotiation"), None).route == "processed"
