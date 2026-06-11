# PLAN.md — Master Build Plan

The executable spine for the order-email pipeline. This is the source of truth:
work top to bottom, check tasks as they land, and record decisions and dead-ends in
`DECISIONS.md` so they don't get re-litigated next session.

## How to use this
- Phases are ordered by dependency, not calendar. Roughly: phases 0–5 are the spine
  (get one email flowing end to end), 6–10 are hardening, deploy, and showcase.
- **Build the spine before the layers.** Do not start security hardening or
  observability until a synthetic email flows ingest → extract → rate → review →
  send locally.
- Each phase has a **done-when** gate. Don't advance until it's true.
- Deep detail lives in the companion docs: `order_pipeline_build_plan.md` (runbook),
  `cloud_deployment_zero_cost.md` (deploy), `production_stack_blueprint.md` (layers).

---

## Phase 0 — Foundations
- [~] Create repo under `~/projects/freight-pipeline`, `git init`, public on GitHub.
      (repo + `git init` done; **public-on-GitHub is manual — see checklist**)
- [x] `CLAUDE.md` (the contract) and `DECISIONS.md` (the log) at root.
- [x] Docker Compose skeleton: postgres, redis, api, worker (local dev mirror).
- [x] Python 3.12 env via **uv** + `pyproject.toml`; ruff, mypy, pytest configured.
      (toolchain switched conda→uv — see DECISIONS 2026-06-10)
- [ ] Sign up (no card): Supabase, Upstash, Hugging Face, Vercel; confirm repo public.
      (**manual — see checklist**)
- [x] Define the `LLMClient`, `GmailClient`, and queue interfaces (mocks first).
- **Done when:** `docker compose up` runs and the test suite executes (even if empty).
      ✅ verified 2026-06-10: services healthy, `/health`→200, `uv run pytest` 10 passed.

## Phase 1 — Data layer
- [x] Supabase schema migration: `users`, `carriers`, `deals`, `email_messages`,
      `attachments`, `rates`, `quotes`, `audit_log` with enums and FKs.
- [x] Unique constraints: `carriers.mc_number`, `email_messages.gmail_message_id`.
- [x] `rates` as append-only, effective-dated. (Model A: `effective_from` only —
      `effective_to` dropped; validity from next version. See DECISIONS 2026-06-10.)
- [x] RLS policies: reviewer-owns-deal reads; admin-all; carriers/rates read-all
      write-admin; `audit_log` insert-only, admin-read. (Invariant-bearing tables
      server-side-write-only — see DECISIONS 2026-06-10. Deny-side proven.)
- [x] Seed script + **synthetic email generator** with labeled ground truth
      (normal, malformed, and adversarial/injection emails). (12 samples, 4/4/4.)
- **Done when:** RLS denies a cross-reviewer query in a test; seed data loads.
      ✅ verified 2026-06-10: `tests/test_rls.py` (hermetic, cross-reviewer + 4 deny
      assertions) passes against the local stack; `supabase db reset` loads seed clean.

## Phase 2 — Ingestion + queue
- [x] Gmail OAuth (least-privilege: readonly + send). (Single-inbox refresh token =
      one runtime secret, no token table. Scopes asserted least-privilege.)
- [x] Poll loop behind `GmailClient`; mock implementation serves synthetic emails.
      (Poller + DB reconciliation sweep; mock serves the 12-sample corpus.)
- [x] Publish each new message to the queue (Upstash QStash) with retries + DLQ.
      (QStashQueue slice; retry/DLQ via the separate LocalDispatcher; cloud DLQ @ Phase 8.)
- [x] Idempotency: claim by `gmail_message_id` (Redis SET NX + DB unique backstop).
      (DB unique authoritative; Redis fail-open pre-check; sweep recovers stuck rows.)
- [x] GitHub Actions cron for the inbox poll. (`*/5` — GitHub's floor; curls `/poll`;
      inert until Phase 8. Correctness independent of cadence. See DECISIONS.)
- **Done when:** a synthetic email enqueues exactly once; a poisoned one hits the DLQ.
      ✅ verified 2026-06-11 locally: `test_poller.py` (exactly-once + re-poll-zero +
      sweep recovery), `test_consumer.py` (envelope-poison → DLQ). QStash-cloud half @ Phase 8.

## Phase 3 — Extraction
- [ ] `LLMClient` against HF serverless inference; structured/JSON decoding.
- [ ] Classify intent (rate_request / negotiation / rc / contract / other).
- [ ] Extract fields under the Pydantic schema; score confidence.
- [ ] Validate every field (route format, numeric ranges, allowlists) — untrusted in.
- [ ] **PDF intake:** attachments → text/OCR → same extraction + validation path.
- [ ] Low-confidence or invalid → flag for review / DLQ.
- **Done when:** a raw email and an RC PDF both produce a validated structured record.

## Phase 4 — State machine + rate engine
- [ ] Deal state machine: `new_enquiry → quoted → negotiating ⇄ quoted →
      rc_received → contract_signed → scheduled`, plus `rejected`/`on_hold`.
- [ ] Carrier/MC eligibility gate before `quoted` and `contract_signed`
      (unknown/blocked → `on_hold` for human).
- [ ] Rate engine: contracted-route lookup vs internal formula fallback;
      flag generated quotes; quote pins the exact `rate_id`.
- [ ] Redis cache for hot routes + invalidate on new rate version.
- [ ] Fuel-surcharge update job (GitHub Actions cron) writing new rate versions.
- **Done when:** operated route returns a contracted rate; new route returns a
      flagged computed rate; a surcharge update creates a version, not an overwrite.

## Phase 5 — Review UI + send (spine completes)
- [ ] Next.js + TS + Tailwind + shadcn/ui console; deploy preview on Vercel.
- [ ] Supabase Auth login; RBAC (reviewer vs admin).
- [ ] Review queue: model proposal + confidence + rate version; edit/approve/reject.
- [ ] Idempotent send via Gmail; write audit-log row in the same transaction.
- **Done when:** log in, review a draft, edit, send — reply goes out exactly once
      with an audit record. **End-to-end spine is now working.**

## Phase 6 — Security hardening
- [ ] Secrets to env/managers; remove any from code; `pip-audit` + `npm audit`.
- [ ] Encrypt PII columns; enforce TLS; CSRF on state-changing routes.
- [ ] Verify webhook/queue signatures; confirm OAuth scopes are minimal.
- [ ] Rate limiting (Upstash) on public API + LLM-call guard.
- [ ] Make `audit_log` append-only; run the adversarial set, confirm containment.
- [ ] Write `THREAT_MODEL.md`.
- **Done when:** injection emails can't drive a bad send; no secret in the repo.

## Phase 7 — Observability + reliability
- [ ] Sentry on frontend + backend.
- [ ] Structured JSON logs with a correlation ID threaded ingest → send.
- [ ] Prometheus metrics → Grafana Cloud dashboard (queue depth, latency,
      acceptance rate, DLQ size).
- [ ] Health-check endpoints; retries with backoff; confirm DLQ replay works.
- [ ] Supabase backups on; uptime monitor (Better Stack / UptimeRobot).
- [ ] Write `RECOVERY.md` (DLQ replay, restore, key rotation).
- **Done when:** the dashboard is live and you can trace one email end to end.

## Phase 8 — Deployment
- [ ] Deploy backend container (Fly.io/Railway), always-on; register QStash target.
- [ ] Deploy Next.js console on Vercel with secrets wired.
- [ ] Connection strings in GitHub Secrets + provider secret stores.
- [ ] CI/CD: lint/type/test/build/deploy on push; branch protection; PR previews.
- **Done when:** a synthetic email flows through the *cloud* path and a reply sends.

## Phase 9 — Evaluation + load test
- [ ] Eval script over the synthetic set: extraction accuracy, classification
      accuracy, acceptance proxy, injection containment.
- [ ] `k6`/`locust` load test well past 80/day; record latency under load.
- [ ] Put the **real measured numbers** in the README (no rounding you can't defend).
- **Done when:** the eval report exists with honest figures.

## Phase 10 — Showcase
- [ ] README: problem, architecture diagram, decisions, eval numbers, threat model.
- [ ] `ARCHITECTURE.md`, `DECISIONS.md` (ADRs), `LEARNING.md` polished.
- [ ] Live demo with a "load sample order email" button (no Gmail needed to try it).
- [ ] 2–3 min demo video.
- [ ] Write-up for LinkedIn + portfolio, leading with the novelty
      (injection-aware, human-supervised logistics quoting).
- **Done when:** a stranger can open the demo, understand the problem, and see it work.

---

## Sequencing reminder
The fastest path to something demoable is phases 0→5 with mocks where needed. Resist
jumping ahead to security or observability before the spine flows. When you fall
behind, consult the de-scoping ladder in `order_pipeline_build_plan.md` — cut from
the bottom, never the queue, versioned rates, human gate, or injection validation.
