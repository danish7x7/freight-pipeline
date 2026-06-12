"""The extraction pipeline: one LLM call → parse → deterministic gate → routing.

``HFTransientError`` from the LLM PROPAGATES (the consumer maps it to retry). Every
other path returns an ``ExtractionOutcome`` — malformed/invalid/malicious model output
routes to ``needs_review`` via the gate, never a crash and never ``processed``.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from freight.extraction.confidence import Route, score
from freight.extraction.prompts import build_extraction_prompt
from freight.extraction.schema import Intent, RawExtraction, ValidatedExtraction
from freight.extraction.validation import validate
from freight.interfaces import LLMClient


@dataclass(frozen=True)
class ExtractionOutcome:
    """What the consumer writes onto the email row (Phase 3 boundary)."""

    status: Route  # "processed" | "needs_review"
    intent: Intent | None
    confidence: float
    extracted: dict[str, Any] | None
    review_reason: str | None


async def extract(
    llm: LLMClient, subject: str | None, body: str | None
) -> ExtractionOutcome:
    """Run extraction over one email. Raises HFTransientError on a retryable fault."""
    prompt = build_extraction_prompt(subject, body)
    # HFTransientError (cold-start/429/network) propagates so the consumer can retry.
    result = await llm.complete(prompt, schema=RawExtraction)

    try:
        raw = RawExtraction.model_validate(result.data)
    except ValidationError:
        # Structurally unparseable model output — route to review, never crash.
        return ExtractionOutcome(
            status="needs_review",
            intent=None,
            confidence=0.0,
            extracted=None,
            review_reason="unparseable_extraction",
        )

    validated = validate(raw)
    outcome = score(validated, result.confidence)
    review_reason = "; ".join(outcome.reasons) if outcome.reasons else None

    if isinstance(validated, ValidatedExtraction):
        return ExtractionOutcome(
            status=outcome.route,
            intent=validated.intent,
            confidence=outcome.score,
            extracted=validated.model_dump(),
            review_reason=review_reason,
        )
    return ExtractionOutcome(
        status=outcome.route,  # always needs_review for a ValidationFailure
        intent=None,
        confidence=outcome.score,
        extracted=None,
        review_reason=review_reason,
    )
