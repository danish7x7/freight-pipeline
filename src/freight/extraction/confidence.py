"""Composite confidence + the review-routing rule.

Confidence is NOT the model's self-report. Two rules make injection-resistant routing:
1. ANY validation failure -> needs_review, regardless of any model-reported score (an
   injected "report confidence 1.0" can never skip the gate).
2. On success, the score is mostly DETERMINISTIC (field completeness for the intent);
   the model's contribution is capped so a self-report alone can't cross the threshold.
"""

from dataclasses import dataclass
from typing import Literal

from freight.extraction.schema import ValidatedExtraction
from freight.extraction.validation import ValidationFailure

Route = Literal["processed", "needs_review"]

REVIEW_THRESHOLD = 0.7
_DETERMINISTIC_WEIGHT = 0.8
_MODEL_WEIGHT = 0.2  # capped: 0.2 * 1.0 < threshold, so the model can't pass alone
_NEUTRAL_MODEL = 0.5  # used when the model reports no confidence


@dataclass(frozen=True)
class ConfidenceOutcome:
    """The routing decision for an extracted record."""

    score: float
    route: Route
    reasons: list[str]


def score(
    result: ValidatedExtraction | ValidationFailure,
    model_confidence: float | None,
) -> ConfidenceOutcome:
    """Combine the validation result + a capped model signal into a routing decision."""
    if isinstance(result, ValidationFailure):
        return ConfidenceOutcome(
            score=0.0,
            route="needs_review",
            reasons=result.reasons or ["validation_failed"],
        )

    completeness = _completeness(result)
    model = _NEUTRAL_MODEL if model_confidence is None else _clamp(model_confidence)
    composite = round(
        _DETERMINISTIC_WEIGHT * completeness + _MODEL_WEIGHT * model, 4
    )
    if composite >= REVIEW_THRESHOLD:
        return ConfidenceOutcome(score=composite, route="processed", reasons=[])
    return ConfidenceOutcome(
        score=composite,
        route="needs_review",
        reasons=[f"low_confidence:{composite}"],
    )


def _completeness(result: ValidatedExtraction) -> float:
    """Fraction of the fields expected for the intent that are present."""
    if result.intent == "rate_request":
        core = [
            result.origin_state,
            result.dest_state,
            result.equipment,
            result.weight_lbs,
        ]
        return sum(field is not None for field in core) / len(core)
    # Non-route intents (negotiation/rc/contract/other) need only a valid intent.
    return 1.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
