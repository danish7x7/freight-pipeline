"""Phase 6.5 — the adversarial containment run.

Sweeps the WHOLE adversarial corpus through the real `extract()` gate with a fully
"fooled" model (the worst case: the model is completely compromised by the injection
and emits the attacker's structured output with confidence 1.0). Proves the
DETERMINISTIC validation gate contains every injection regardless of model behaviour, on
BOTH vectors: the email body and the attachment-borne PDF (CLAUDE.md routes PDFs through
the same extraction + validation path).

Hermetic and NEVER skips — a containment proof must always execute in CI. The
consumer-level PDF routing + DB `needs_review` write is covered separately by
`test_pdf_intake.py`; here we prove the gate itself.

Per sample we assert the SPECIFIC gate dimension that must trip (`expected_failure`), so
weakening one dimension fails loudly instead of being masked by another.
"""

import inspect

import pytest
from fpdf import FPDF
from pydantic import BaseModel

from freight.extraction import ExtractionOutcome, extract
from freight.interfaces.types import LLMResult
from freight.synthetic import SyntheticEmail, generate_dataset

_ADVERSARIAL: list[SyntheticEmail] = [
    s for s in generate_dataset() if s.is_adversarial
]
_IDS = [s.message.gmail_message_id for s in _ADVERSARIAL]


class _FooledLLM:
    """A model fully compromised by the injection: returns the attacker's payload."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        # confidence 1.0 — a self-reported score must never bypass the gate.
        return LLMResult(data=self._payload, raw="{}", confidence=1.0)


def _text_pdf(body: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, body)
    return bytes(pdf.output())


@pytest.mark.parametrize("sample", _ADVERSARIAL, ids=_IDS)
async def test_injection_is_contained(sample: SyntheticEmail) -> None:
    assert sample.attack_payload is not None
    assert sample.expected_failure is not None

    if sample.attachment_text is not None:
        # PDF vector: render a real PDF and prove the injection text actually reaches
        # the model boundary via the text layer (same path as email body).
        from freight.pdf import extract_text

        surfaced = extract_text(_text_pdf(sample.attachment_text))
        assert surfaced is not None
        markers = [
            w for w in sample.attachment_text.split() if w.isupper() and len(w) >= 5
        ]
        assert markers, "PDF sample should carry an ALL-CAPS injection marker"
        assert any(m in surfaced for m in markers), "injection must reach extraction"
        content = surfaced
    else:
        content = sample.message.body

    outcome = await extract(
        _FooledLLM(sample.attack_payload), sample.message.subject, content
    )

    # Contained: rejected to review, nothing persisted as a valid record, and the
    # SPECIFIC expected gate dimension is what tripped.
    assert outcome.status == "needs_review"
    assert outcome.extracted is None
    assert outcome.review_reason is not None
    assert sample.expected_failure in outcome.review_reason


def test_extraction_has_no_send_channel() -> None:
    """The pipeline structurally cannot send — the model can never trigger an action.

    The only outbound path is the human-gated `POST /review/send` (proven by
    `test_send.py`). Here we prove extraction has no Gmail/send dependency at all.
    """
    params = set(inspect.signature(extract).parameters)
    assert params == {"llm", "subject", "body"}  # no gmail/sender/queue channel
    import freight.extraction as ext

    assert not hasattr(ext, "send")


async def test_clean_high_confidence_extraction_still_does_not_auto_send() -> None:
    """Even a valid, confidence-1.0 extraction yields only DATA, never a send.

    Models the case where the model resisted the injection and returned the true
    fields: the result is a pure `ExtractionOutcome`; auto-send remains impossible.
    """
    sample = next(s for s in _ADVERSARIAL if s.expected_intent == "rate_request")
    clean_payload = {"intent": sample.expected_intent, **sample.expected_fields}
    outcome = await extract(
        _FooledLLM(clean_payload), sample.message.subject, sample.message.body
    )
    assert isinstance(outcome, ExtractionOutcome)
    assert outcome.status == "processed"  # valid data, but still just data


def test_every_adversarial_sample_is_runnable_on_both_vectors() -> None:
    """Guard: the run can't silently stop covering a sample or a vector."""
    adv = [s for s in generate_dataset() if s.is_adversarial]
    assert len(adv) >= 6
    for s in adv:
        assert s.attack_payload is not None, s.message.gmail_message_id
        assert s.expected_failure is not None, s.message.gmail_message_id

    pdf = [s for s in adv if s.attachment_text is not None]
    body = [s for s in adv if s.attachment_text is None]
    assert pdf and body  # BOTH vectors represented
    assert all(s.message.attachment_refs for s in pdf)  # PDF samples carry attachments
