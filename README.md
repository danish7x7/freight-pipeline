# freight-pipeline

A logistics order-email pipeline: ingest delivery orders and rate enquiries from
email and PDF, extract structured fields with an LLM, look up or compute a rate, and
produce a **human-reviewed** reply. Injection-aware and human-supervised by design —
the model proposes, a person disposes.

> Status: early build. See [`PLAN.md`](PLAN.md) for the phased roadmap and
> [`DECISIONS.md`](DECISIONS.md) for the decision log. The behavioral contract for
> this repo lives in [`CLAUDE.md`](CLAUDE.md).

## Quickstart (local)

Requires [`uv`](https://docs.astral.sh/uv/), Docker, and the
[Supabase CLI](https://supabase.com/docs/guides/cli). Supabase Postgres (local stack on
`:54322`) is the database of record; Docker Compose supplies only Redis; the API runs via
`uv run`.

```bash
uv sync                       # create the env from pyproject/uv.lock
cp .env.example .env          # then fill in real values (never commit .env)
supabase start                # Postgres + Auth + RLS + Storage (DB of record, :54322)
supabase db reset             # apply migrations + seed
docker compose up -d          # Redis (Supabase has none)
uv run uvicorn freight.api.main:app --reload   # API: /health, /ingest, /poll
uv run pytest                 # run the test suite
uv run ruff check . && uv run mypy .
```

## Stack

Python 3.12 · FastAPI · Pydantic · SQLAlchemy · Supabase (Postgres + Auth + RLS +
Storage) · Redis (Upstash) · Upstash QStash · Hugging Face serverless inference ·
Next.js + TypeScript + Tailwind + shadcn/ui (`web/`).

## Evaluation (measured)

Phase 9 ran the real `extract()` gate over a 14-sample labeled corpus (4 normal /
4 malformed / 6 adversarial), priced the route-aware engine against the live DB, and
load-tested the ingest path. Instruments: `scripts/eval_corpus.py`,
`scripts/eval_rates.py`, `scripts/locustfile.py`, `scripts/eval_llm_latency.py`.

### Extraction & classification

Measured 2026-06-18 on `Llama-3.3-70B-Instruct` via Hugging Face serverless (provider
`hyperbolic`, observed through `:cheapest` routing; server-default sampling — a
measured-on-a-date figure, not bit-reproducible).

- **Classification 13/14 (92.9%)** — normal 4/4, adversarial 6/6, malformed 3/4. The one
  miss is the no-text-layer PDF sample (empty body → `other`); it routes to human review,
  so the miss is safe. OCR is out of scope.
- **Field extraction 30/30 (100%) canonical** (the post-gate values that actually feed
  the rate engine) vs **26/30 (86.7%) raw** — the gap is the validation gate
  canonicalizing 4 model outputs (e.g. `"dry van"` → `dry_van`) that would otherwise
  miss. The gate adds accuracy, not just safety.
- **No fabricated fields.** One negotiation reply carried its lane verbatim from the email
  **subject** line — input-grounded extraction the corpus didn't label as expected, not
  invention (and it produced no draft).

### Injection containment (the system's novelty)

Every extracted field is untrusted and passes a deterministic allowlist-reject gate
before any rate logic; a human approves every send.

- **0 of 6 adversarial samples produced a draft containing attacker-controlled data** —
  the safety invariant. Real-model run: 6/6 contained, 0 escapes, backed by a
  model-independent fooled-model sweep (`tests/test_containment.py`) that holds even if
  the model is fully compromised.
- 3 of the 6 were truth-legit orders that merely wrapped an injection: the model ignored
  the injected text and quoted the **true** on-table lane (`escaped=[]`) — containment
  succeeding, not a false accept.

### Route-aware rate engine

Priced live against seeded effective-dated `pricing_components` — **synthetic,
operator-tunable** components (new effective-dated versions), **not** live market rates:

| lane (dry_van) | miles | linehaul | deadhead | margin | FSC | all-in |
|---|---|---|---|---|---|---|
| Chicago, IL → Dallas, TX | 925 | $1,665.00 | $199.80 | $279.72 | $372.96 | **$2,517.48** |
| Atlanta, GA → Miami, FL | 665 | $1,197.00 | $143.64 | $201.09 | $268.12 | **$1,809.85** |
| Newark, NJ → Boston, MA | 225 | $405.00 | $48.60 | $68.04 | $90.72 | **$612.36** |

Same equipment, three lanes, three totals — the old flat $2,200 is structurally dead.
Deadhead scales with distance (12% of linehaul); a `container` lane prices flat ($540.00)
to show the equipment-driven model switch (drayage vs per-mile).

### Load, latency & cost

Load test: locust → signed `POST /ingest` on a local stack with the **mock LLM**
(isolating pipeline latency from model time), through the real QStash signature gate, each
request a unique pre-seeded id so `finalize` does real work.

- **Pipeline latency p50 120 ms / p95 140 ms / p99 170 ms** — excludes model time; local
  stack, dominated by DB pooler round-trips.
- **~70 req/s sustained, 0 failures** — far beyond the ~80/day this system is sized for;
  the headroom means volume is never the constraint. At 40 concurrent users it degrades to
  backpressure (p50 410 ms) with **still 0 failures**.
- **Real-model latency median 3.63 s / p95 4.44 s** (live HF) — the model-dominated
  number, **separate** from the pipeline latency above; they are not the same thing.
- **319 tokens/email** (237 prompt + 82 completion, measured) — **sub-cent per email** at
  current Llama-3.3-70B serverless rates. The token count is the hard number; the dollar
  figure is a market variable.

### The eval did real work

Building these instruments surfaced and fixed **two latent production defects**: a
fenced-JSON parse-and-swallow in the HF client (valid responses were silently routed to
review) and an engine-per-request connection leak (every `/ingest` leaked a pool against
the Supabase pooler). Both are written up in [`DECISIONS.md`](DECISIONS.md).

---

Phase 10 adds the architecture diagram, a live demo, and the write-up. The security model
is in [`THREAT_MODEL.md`](THREAT_MODEL.md).
