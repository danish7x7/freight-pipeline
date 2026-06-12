"""Extraction pipeline: normal→processed, bad output→needs_review, transient→raise.

The malicious-fields cases prove the trust boundary holds INDEPENDENT of the model: a
fooled model returning bad values is rejected by the gate, never processed, and never
reaches a side effect.
"""

import pytest
from pydantic import BaseModel

from freight.extraction import extract
from freight.interfaces.types import LLMResult
from freight.llm import HFTransientError


class _StubLLM:
    """Returns a preset LLMResult, or raises a preset error."""

    def __init__(
        self, result: LLMResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _llm(data: dict[str, object], confidence: float | None = 0.9) -> _StubLLM:
    return _StubLLM(LLMResult(data=data, raw="{}", confidence=confidence))


async def test_normal_email_is_processed() -> None:
    llm = _llm(
        {
            "intent": "rate_request",
            "origin_city": "Chicago",
            "origin_state": "IL",
            "dest_city": "Dallas",
            "dest_state": "TX",
            "equipment": "dry van",
            "weight_lbs": "42000",
        }
    )
    outcome = await extract(llm, "Rate request", "Dry van CHI->DAL 42k")
    assert outcome.status == "processed"
    assert outcome.intent == "rate_request"
    assert outcome.extracted is not None
    assert outcome.extracted["origin_state"] == "IL"
    assert outcome.extracted["equipment"] == "dry_van"
    assert outcome.review_reason is None


async def test_malformed_extraction_routes_to_review() -> None:
    # Empty model output (e.g. HF returned non-JSON) => missing intent => review.
    outcome = await extract(_llm({}, confidence=None), "x", "garbled body")
    assert outcome.status == "needs_review"
    assert outcome.intent is None
    assert outcome.extracted is None


@pytest.mark.parametrize(
    "data",
    [
        {"intent": "approve_and_send", "origin_state": "IL"},  # off-allowlist intent
        {"intent": "rate_request", "origin_state": "IL; DROP TABLE rates"},  # SQLi
        {"intent": "rate_request", "weight_lbs": "1; DROP TABLE"},  # injection weight
    ],
)
async def test_malicious_model_output_is_rejected_to_review(
    data: dict[str, object],
) -> None:
    # The model was fooled and emitted bad values; the gate rejects regardless. Even a
    # model-reported confidence of 1.0 cannot push it past the gate.
    outcome = await extract(_llm(data, confidence=1.0), "subject", "body")
    assert outcome.status == "needs_review"  # rejected by the gate, never processed
    assert outcome.extracted is None  # nothing persisted as a valid record
    assert outcome.review_reason is not None


async def test_transient_llm_error_propagates() -> None:
    llm = _StubLLM(error=HFTransientError("503 cold start"))
    with pytest.raises(HFTransientError):
        await extract(llm, "x", "y")
