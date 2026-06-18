# RECOVERY.md

Operational runbook for the freight order-email pipeline: how to detect a problem and how
to recover from the failure modes the system is designed around — DLQ replay, a stuck send,
restore from backup, and key/secret rotation.

Each procedure is tagged:
- **[local]** — works against the local stack today and/or is proven by a test.
- **[deploy — Phase 8]** — defined here, but needs the live providers (QStash console,
  Supabase backups, GitHub Secrets) to actually run.

This reflects `DECISIONS.md` through Phase 7.3 and should be updated alongside it. Recovery
rests on invariants enforced elsewhere (process-once claim, append-only `rates`/`audit_log`,
the human send gate) — see `CLAUDE.md` and `THREAT_MODEL.md`.

---

## 1. Detection — read these before acting

The observability built in 7.1–7.3 surfaces every failure mode below.

- **Readiness — `GET /ready`** (distinct from `/health` liveness):
  - `not_ready` (HTTP 503) — Postgres (the hard dep) is unreachable: the instance can't
    claim/finalize or serve `/review`. It should be out of rotation.
  - `degraded` (HTTP 200) — Redis is down but the process keeps serving (fail-open:
    idempotency pre-check, cache, rate limiter all degrade gracefully). Degraded ≠ down.
- **Metrics — `GET /metrics`** (Prometheus text):
  - `freight_ingest_backlog` climbing — inbound emails stuck in `received`/`queued` (the
    poller isn't publishing, or the consumer isn't draining).
  - `freight_dlq_size > 0` — messages have exhausted retries (§3).
  - `freight_sends_claimed_not_sent > 0` — a send is stuck mid-flight (§4).
- **Logs — structured JSON, one `correlation_id` per email.** The id is the
  `gmail_message_id`; grep it to trace one email end to end (ingest → extract → rate →
  finalize, and the human send):
  ```
  grep '"correlation_id": "<gmail_message_id>"' <logs>
  ```

---

## 2. What lands where (so you recover in the right place)

- **Transient fault** (`HFTransientError`: HF cold-start/429/network; DB unreachable) →
  `consumer.handle` raises → `/ingest` returns 5xx → QStash retries with bounded backoff →
  **DLQ** on exhaustion. Recover via §3 (replay).
- **Content failure** (won't-parse / invalid / injection / no-text-layer PDF) → routed to
  **`needs_review`**, a human sink — it is **NOT** in the DLQ and replaying it never helps.
  Recover via the review console, not §3.
- **Send interrupted** → row stuck `claimed` (§4).

This split is the Phase 3 error taxonomy; do not retry a `needs_review` item as if it were a
DLQ item.

---

## 3. DLQ replay

**Why replay is safe.** Replay re-delivers each dead-lettered message through the **same
handler** — `/ingest → consumer.handle → finalize → flip_if_queued`. `flip_if_queued` is the
process-once claim (conditional `UPDATE … WHERE ingest_status='queued'`): a still-`queued`
(transiently-failed) message processes exactly once; an already-`processed` message flips 0
rows and **no-ops**. Replay is controlled re-delivery — **it cannot reintroduce
double-process.** A message that fails again is re-dead-lettered (bounded; no infinite loop).

**Procedure — [local]** (proven by `tests/test_dlq_replay.py`):
- The local dispatcher (`LocalDispatcher`) holds dead-letters in `dead_letter`. Call
  `await dispatcher.replay()` → re-delivers each via the same bounded-retry path; returns
  `ReplayResult(replayed, re_dead_lettered)`.

**Procedure — [deploy — Phase 8]:**
1. In the QStash console (or API), inspect the DLQ for the target destination (`/ingest`).
2. Re-publish the dead-lettered message(s) to `/ingest`. They carry the same id, so the
   re-delivery rides the **same `flip_if_queued` claim** — no separate idempotency needed.
3. A message that is genuinely poison (a real bug, not a transient blip) will re-dead-letter;
   fix the cause before replaying again.

**Verify recovery:** `freight_dlq_size` (and `freight_ingest_backlog`) return toward 0, and
the message's `correlation_id` log line shows `ingest processed`.

---

## 4. Stuck `claimed`-not-sent send (the at-least-once window)

**Signal:** `freight_sends_claimed_not_sent > 0`, and a `sends` row sits in `claimed`.

**Cause.** The send is a dual-write: TX-A claims (`UNIQUE(quote_id)` row + `email.send.claimed`
audit, committed) → Gmail send → TX-B `mark_sent` + `email.sent` audit. If the process crashes
**after** Gmail succeeds but **before** TX-B commits, the row stays `claimed`.

**Recovery — [local]:** the reviewer re-invokes `POST /review/send` for that quote. The claim
returns the existing `claimed` row (not a new claim) and the flow resumes at the Gmail send —
the same idempotent path.

**Honest risk — at-least-once, not exactly-once.** If Gmail had already delivered before the
crash, the resume **re-sends** (a duplicate outbound). Every outbound carries an
`X-Freight-Quote-Id` header so a future mailbox-dedup can check for the marker before
re-sending and close the window — **that dedup is not yet built.** Until then, on a stuck
`claimed` row, check the mailbox for the `X-Freight-Quote-Id` marker before retrying. See
`THREAT_MODEL.md` **R4**.

A send that failed at the Gmail step (never delivered) is the safe case — the row is `claimed`,
the retry simply sends.

---

## 5. Restore from backup — [deploy: ACCEPTED-OPEN on Free tier]

**Tier reality (honest, not aspirational).** The **Supabase Free tier provides NO automated
backups — there is no restore point.** The restore gate is therefore **ACCEPTED-OPEN** for this
synthetic showcase deployment: data is synthetic and re-seedable, so a total DB loss is a
re-seed, not a data-loss incident. A **production** deployment would require **Supabase Pro**
(scheduled daily backups / PITR) to close this gate. Documented honestly rather than claiming a
DR capability that does not exist on the deployed tier.

**Procedure (applies once on Pro, where a restore point exists):**
1. In the Supabase console, restore the project to a snapshot or point-in-time.
2. Restore is consistent for the invariant-bearing tables because they are **append-only and
   never mutated**: `rates` (effective-dated versions), `audit_log` (insert-only,
   tamper-evident). A restore can lose recent rows but cannot produce a half-mutated invariant
   row.
3. After restore, re-run the reconciliation path: rows left in `received`/`queued` are
   re-published by the poller's sweep on the next `/poll`; in-flight `claimed` sends are
   handled per §4. Confirm via `/ready` (ready) and `/metrics` (backlog draining).

---

## 6. Key & secret rotation — [deploy — Phase 8]

All secrets are env-only (see `.env.example`); nothing in code or git. Rotate in the provider
console / GitHub Secrets and the matching backend env together. Fail-closed posture means a
brief mismatch rejects requests rather than leaking — that is the intended behavior.

- **`QSTASH_CURRENT_SIGNING_KEY` / `QSTASH_NEXT_SIGNING_KEY`** — the `/ingest` verifier tries
  current → next, so rotation is zero-downtime (6.1): put the new key in `NEXT`, deploy, let
  QStash start signing with it (verified by `NEXT`), then promote new→`CURRENT` and stage the
  following key as `NEXT`. A mismatch → 401 (fail-closed), QStash retries → DLQ (replayable
  per §3).
- **`CRON_SECRET`** — guards `/poll` + `/jobs/surcharge` (6.2). Rotate the **GitHub Secret and
  the backend env together.** During any gap the endpoints 401 (fail-closed); the crons do no
  real work pre-wiring, so a missed cycle only adds latency (idempotent claims + sweep, not
  loss). Never set an empty secret — the dependency rejects an unconfigured secret before any
  compare.
- **`GMAIL_REFRESH_TOKEN`** — single-inbox OAuth (scopes `gmail.readonly` + `gmail.send`).
  Re-consent for the inbox, replace the token in env, redeploy. Sends fail until replaced
  (visible as send errors / a stuck `claimed` row per §4).
- **`SUPABASE_SERVICE_ROLE_KEY`** — the sole writer of invariant tables (bypasses RLS); its
  compromise is high-impact (`THREAT_MODEL.md` assumptions). Rotate in Supabase, update env,
  redeploy. **`SUPABASE_ANON_KEY`** — also update the frontend env (`NEXT_PUBLIC_…`).
- **`HF_TOKEN`** — rotate in the HF console, update env. Extraction fails transiently until
  replaced → 5xx → retry/DLQ (replayable per §3), never a bad send.
- **`APP_SECRET`** — rotate and redeploy.

---

## 7. Cross-references

- `DECISIONS.md` — 7.1 (correlation-id logging), 7.2 (readiness, backoff, DLQ replay), 7.3
  (metrics), 6.1/6.2 (QStash sig / cron secret), Phase 5 (send claim pattern).
- `THREAT_MODEL.md` — residuals R2 (limiter proxy-IP), R4 (at-least-once send), R5 (`next`
  DoS), R7 (Phase 8 wiring carry-forwards).
- `PLAN.md` — Phase 7 (this work) and Phase 8 (deploy: backups, Grafana, Sentry, uptime).
