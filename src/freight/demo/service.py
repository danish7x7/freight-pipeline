"""Demo runner: seed a fixed sample and run the REAL extract→validate→rate path.

WHAT IS REAL (runs live on the deploy, same code as a real inbound email):
  - the server-side-write-only claim (``repo.claim_insert`` — the path the poller uses,
    NOT a backdoor insert);
  - the deterministic VALIDATION GATE (``extraction.validate`` via ``extract``) — the
    injection defense, run for real, NOT stubbed;
  - the rate engine and ``deals.finalize`` (deal/quote, MC gate, process-once flip);
  - RLS visibility (a NULL-reviewer deal is admin-visible, like a real ingested deal)
    and the human-approval send gate (the demo produces only a DRAFT — never a send;
    an LLM never triggers a send).

WHAT IS RECORDED (the only fiction):
  - the model's extraction OUTPUT. Instead of a live HF call per button press, a
    ``RecordedLLMClient`` returns a fixed value drawn from the labeled synthetic corpus
    (the single source of truth the eval uses). The injection sample feeds the gate
    exactly what a FULLY-FOOLED model would emit (the sample's ``attack_payload``) — the
    same construction as ``tests/test_containment.py`` — so showing the real gate reject
    it IS the canonical containment demo, deterministically and at no model cost.

This never touches ``/ingest`` and never bypasses a signature: it invokes the consumer's
building blocks server-side on fixed, server-controlled content. No boundary weakens.
"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from freight.db.repository import IngestRepository
from freight.deals import finalize, rate_key_from
from freight.extraction import extract
from freight.interfaces.types import InboundMessage, LLMResult
from freight.synthetic.emails import generate_dataset

SampleName = Literal["clean", "injection"]

_DEMO_SENDER = "demo@freight-pipeline.example"


class _SampleSpec(BaseModel):
    """A demo sample: the email content shown to the reviewer + the recorded output."""

    subject: str
    body: str
    recorded_output: dict[str, object]  # what the (recorded) model "extracted"
    blurb: str  # one line explaining what this sample demonstrates


def _build_samples() -> dict[SampleName, _SampleSpec]:
    """Derive the demo pair from the synthetic corpus (single source of truth).

    clean      = the labeled clean rate request; recorded output = its TRUE fields.
    injection  = the labeled instruction-override attack; recorded output = its
                 ``attack_payload`` (what a fully-fooled model would emit) so the REAL
                 gate rejects it — the canonical containment demo.
    """
    by_id = {s.message.gmail_message_id: s for s in generate_dataset()}
    clean = by_id["synthetic-0001"]
    injection = by_id["synthetic-0009"]
    assert injection.attack_payload is not None  # adversarial samples carry it

    return {
        "clean": _SampleSpec(
            subject=clean.message.subject,
            body=clean.message.body,
            recorded_output={"intent": clean.expected_intent, **clean.expected_fields},
            blurb="A clean rate request — extracted, validated, and priced to a draft.",
        ),
        "injection": _SampleSpec(
            subject=injection.message.subject,
            body=injection.message.body,
            recorded_output=dict(injection.attack_payload),
            blurb=(
                "A prompt-injection order. The recorded output is what a fully-fooled "
                "model would emit; the real validation gate rejects it and routes to "
                "review — no draft, no send."
            ),
        ),
    }


DEMO_SAMPLES: dict[SampleName, _SampleSpec] = _build_samples()


class RecordedLLMClient:
    """An ``LLMClient`` that returns a fixed, recorded result regardless of prompt.

    Records the MODEL OUTPUT only; the downstream validation gate runs for real on it.
    Mirrors the fooled-model mock in ``tests/test_containment.py``.
    """

    def __init__(self, data: dict[str, object]) -> None:
        # Confidence 0.95: a fooled model claims high confidence (worst case). The gate,
        # not the score, contains the injection — a ValidationFailure forces
        # needs_review regardless of any model-claimed confidence.
        self._result = LLMResult(data=data, raw=json.dumps(data), confidence=0.95)

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        return self._result


class DemoResult(BaseModel):
    """What the console renders after a run (real vs recorded is explicit in UI)."""

    sample: SampleName
    gmail_message_id: str
    status: str  # "processed" | "needs_review"
    intent: str | None
    review_reason: str | None
    deal_id: str | None
    deal_state: str | None
    quote_id: str | None
    blurb: str


def run_demo_sample(repo: IngestRepository, *, sample: SampleName) -> DemoResult:
    """Seed the sample and run the real pipeline on it; return the outcome for the UI.

    Mirrors ``IngestConsumer.handle`` (claim → extract → finalize) but with a recorded
    model output and returning the result. Uses a unique ``demo-<uuid>`` id so repeated
    presses never collide on the idempotency claim; the row is labeled (demo sender/id)
    so demo deals are identifiable and prunable.
    """
    spec = DEMO_SAMPLES[sample]
    gmail_message_id = f"demo-{uuid4().hex[:12]}"
    message = InboundMessage(
        gmail_message_id=gmail_message_id,
        thread_id=gmail_message_id,
        sender=_DEMO_SENDER,
        subject=spec.subject,
        body=spec.body,
        received_at=datetime.now(UTC),
        attachment_refs=[],
    )

    # Real server-side-write-only claim, then mark queued so finalize's flip_if_queued
    # claim (WHERE ingest_status='queued') wins — the poller's claim→queued path.
    repo.claim_insert(message)
    repo.set_ingest_status(gmail_message_id, "queued")

    # REAL gate on the recorded model output (extract runs validate()).
    llm = RecordedLLMClient(spec.recorded_output)
    outcome = asyncio.run(extract(llm, message.subject, message.body))

    contracted = None
    if outcome.status == "processed" and outcome.intent == "rate_request":
        key = rate_key_from(outcome.extracted or {})
        contracted = repo.current_contracted_rate(key)

    with repo.begin() as conn:
        result = finalize(
            conn,
            repo,
            gmail_message_id=gmail_message_id,
            outcome=outcome,
            contracted_rate=contracted,
        )

    return DemoResult(
        sample=sample,
        gmail_message_id=gmail_message_id,
        status=outcome.status,
        intent=outcome.intent,
        review_reason=outcome.review_reason,
        deal_id=result.deal_id,
        deal_state=result.deal_state,
        quote_id=result.quote_id,
        blurb=spec.blurb,
    )
