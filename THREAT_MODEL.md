# THREAT_MODEL.md

Threat model for the freight order-email pipeline. Structured around the system's
**actual trust boundaries**: for each boundary we state the threat, the defense in place
(traced to its `DECISIONS.md` entry — defenses are not re-derived here), and the honest
residual risk. The centerpiece is the **injection defense** (§5), which is what makes this
system novel: an injection-aware, human-supervised logistics quoting pipeline.

## 1. Purpose & scope

The system ingests delivery orders and rate enquiries from email and PDF, extracts
structured fields with an LLM, looks up or computes a rate, and produces a
**human-reviewed** reply. The binding security invariants are in `CLAUDE.md`; this
document explains how the implementation upholds them and where the edges are.

**Scope.** This is right-sized for a **low-volume synthetic showcase**. Data is synthetic;
the deploy target is managed (Supabase / Upstash / Vercel / Hugging Face serverless). The
model assesses the application's own trust boundaries — not the physical/infra security of
those providers (assumed trusted, §7).

**Method.** Boundary-driven, not a forced STRIDE matrix. Each defense links to the
`DECISIONS.md` entry that established it, so the model stays faithful to what was actually
built and can be re-audited against the log.

## 2. System overview & data flow

Trust boundaries are marked `╪`. Untrusted input crosses left-to-right until it has
passed the validation gate; no outbound action occurs without a human.

```
  Gmail inbox                GitHub Actions cron
      │ (untrusted email/PDF)     │
      ▼                           ▼
  Poller ──claim/enqueue──► QStash ╪═► POST /ingest ─► consumer
   (idempotent)                    (6.1 signature)        │
                                                          ▼
  cron ╪═► /poll, /jobs/surcharge          PDF/attachment ─┐
        (6.2 CRON_SECRET)                  email body ──────┤
                                                          ▼
                              LLM (untrusted output) ╪═════╪═► VALIDATION GATE
                                                              (Phase 3 allowlist-reject;
                                                               §5 — the injection defense)
                                                          │ validated record only
                                                          ▼
                              rate engine (append-only, versioned, pinned) ─► quote
                                                          │
  Browser console ╪═► /review/*  ──reads via RLS / writes via service_role──┘
   (Phase 5 JWT + RBAC,            │
    6.3 CORS)                      ▼
                       HUMAN approves ╪═► Gmail send + atomic audit (Phase 5)
                       (the model proposes; a person disposes)
```

## 3. Assets & actors

**Assets.** (a) Quote/rate integrity — `rates` append-only, effective-dated; a `quote`
pins the exact `rate_id`. (b) The human-send gate — no automated/LLM-driven outbound.
(c) Reviewer data isolation (RLS). (d) Audit-trail integrity — append-only, tamper-evident.
(e) Secrets (Gmail refresh token, Supabase keys, QStash keys, `CRON_SECRET`). (f) PII
(sender / to_email / body / actor_email — synthetic today). (g) Availability + LLM cost.

**Actors / threats.**
- **External email/PDF injector** — crafts a message or attachment to make the system
  quote wrongly or auto-send. *The primary, novel threat (§5).*
- **Unauthenticated caller** of the public endpoints (`/ingest`, `/poll`,
  `/jobs/surcharge`, `/review/*`).
- **Forged-delivery sender** — replays/fakes a QStash push to `/ingest`.
- **Malicious / compromised reviewer** — tries to read others' deals, forge audit rows, or
  self-promote to admin.
- **DoS / cost-exhaustion** — floods the public API or drives unbounded LLM calls.

## 4. Trust boundaries

| # | Boundary | Threat | Defense | Source | Residual |
|---|----------|--------|---------|--------|----------|
| B1 | QStash → `POST /ingest` | Forged/replayed delivery drives ingestion | Verify `Upstash-Signature` over raw bytes BEFORE any parse/claim; fail-closed (missing/bad/expired/wrong-key/`sub`-mismatch → 401); official `qstash` SDK, no hand-rolled JWT | 6.1 | Live keys + `sub`/public-URL confirmed at Phase 8 (R7) |
| B2 | Cron → `/poll`, `/jobs/surcharge` | Anonymous trigger of ingestion / rate writes | Shared `CRON_SECRET` bearer, `hmac.compare_digest`, unconfigured-secret rejected before compare (no empty-equals-empty fail-open) | 6.2 | Secret wired both sides at Phase 8 (R7) |
| B3 | Browser console → `/review/*` | Unauthorized send/reject; cross-site abuse | Supabase **ES256/JWKS** JWT verify (exp + aud + iss); app role from `public.users`, never the token's role claim; **CORS** explicit-origin allowlist; CSRF N/A (bearer header, not cookie) | 6.3, Phase 5 | CORS/JWKS origins confirmed at Phase 8 |
| B4 | Public API surface | DoS; LLM cost-exhaustion | Fixed-window **rate limiter** before auth on every external POST; global **LLM-call budget guard** (trip = transient backpressure → retry); both fail-open | 6.4 | Proxy-IP caveat (R2) |
| B5 | **LLM output → engine/DB** | **Prompt injection** changes fields/intent or triggers a send | **The injection defense — §5** | Phase 3, 6.5 | Real-model accuracy is Phase 9 (R8) |
| B6 | Extracted fields → rate engine | SQLi / out-of-range / off-allowlist values reach the engine | Deterministic **allowlist-REJECT** gate (states, cities, equipment, weight, intent); reject → `needs_review`, never sanitize-and-keep | Phase 3 | — |
| B7 | Deal lifecycle | State skipping; quoting an ineligible carrier | State machine enforced in the service layer (no skips); **MC eligibility gate** before `quoted`/`contract_signed` → unknown/blocked = `on_hold` for a human | Phase 4 | — |
| B8 | Human → outbound send | Automated/duplicate send; missing audit | Send reachable ONLY via explicit reviewer action; claim pattern (`UNIQUE(quote_id)`) + audit atomic with state change | Phase 5 | At-least-once double-send window (R4) |
| B9 | DB / multi-tenant | Cross-reviewer read; forged/mutated invariant rows | **RLS** reviewer-owns-deal; invariant tables (`rates`/`quotes`/`deals`/`audit_log`) **server-side-write-only** (service_role sole writer); `audit_log` insert-only + tamper-evident triggers; `rates` append-only (UPDATE/DELETE/TRUNCATE all blocked); users cannot self-promote | Phase 1 | Real-PII delta (R3) |
| B10 | Secrets | Secret in code/git | Env vars / secret managers only; placeholders in `.env.example`; least-privilege Gmail scopes (`readonly` + `send`) | 6.x, Phase 2 | — |

## 5. The injection defense (centerpiece)

**The boundary (B5).** Every extracted field is **untrusted input**. The LLM emits
structured data only; it can never trigger an action. The flow is:

```
RawExtraction (permissive, UNTRUSTED LLM output)
   → deterministic gate (validation.validate)  ← the defense
   → ValidatedExtraction (the ONLY type the rate engine consumes)
```

**The defense is the gate, not the model's behavior** (Phase 3):
- **Allowlist-REJECT, not sanitize.** States (USPS allowlist), cities (name format, no
  newlines/injection punctuation), equipment (format-gate then bounded canonicalization),
  weight (numeric format + range), and `intent` (5-value allowlist). Anything off the
  allowlist/format/range → **reject → `needs_review`**. We never strip injection out of a
  field and keep the remainder.
- **Confidence is capped.** Composite confidence is deterministic-led; the model's
  self-reported score is weighted so it can never cross the threshold alone. Any validation
  failure forces `needs_review` regardless of a model-claimed "confidence 1.0".
- **The human gate (B8).** No `processed` outcome can send; the only outbound path is the
  reviewer-triggered `/review/send`. The model proposes; a person disposes.
- **Both vectors.** PDFs/attachments run the **same** extraction + validation path as email
  body text (Phase 3), so containment holds on the attachment vector too.

**Proven, not asserted — the 6.5 containment run** (`tests/test_containment.py`):
- A **fooled-model mock** returns the attacker's payload at confidence 1.0 (worst case: the
  model is fully compromised by the injection).
- The whole adversarial corpus is swept through the real `extract()` gate, on **both
  vectors** (email body 9–12; attachment PDF 13–14, rendered to a real PDF whose injection
  text is asserted to reach the model boundary).
- **Per-dimension assertions** — each sample trips a specific gate dimension
  (`invalid_intent`, `invalid_dest_city`, `invalid_origin_state`, `invalid_equipment`,
  `weight_out_of_range`), so weakening one dimension fails loudly instead of being masked.
- A **no-auto-send** structural test proves `extract()` has no Gmail/send channel at all.
- The run is hermetic and **never skips** — a containment proof must always execute in CI.

This is Phase 6's done-when: **injection emails (and PDFs) cannot drive a bad send.**

## 6. Residual risks

Tracked honestly. `Rn` ids are referenced from §4. Items marked *Phase n* are carry-forwards
already logged in `DECISIONS.md`, surfaced here so they are not buried in the change log.

- **R1 — Real-model adversarial accuracy is unmeasured here.** The 6.5 run proves the
  *gate* contains injection regardless of model output; it does **not** measure how often the
  real HF model is fooled. That is **Phase 9** (eval over the synthetic set: extraction /
  classification accuracy, injection containment). Per the fork, 6.5 used a deterministic
  fooled mock by design.
- **R2 — Rate-limiter proxy-IP caveat (6.4).** Behind the Phase 8 deploy proxy
  (Fly/Railway), `request.client.host` is the *proxy's* IP, so per-client limiting is coarse
  (per-proxy) until a trusted `X-Forwarded-For` / platform client-IP header is wired.
  Mitigations: the auth gates (B1–B3) are the primary control; the limiter is secondary and
  fail-open by design.
- **R3 — PII is at-rest baseline only; real-PII prod delta (Phase 6 kickoff).** Today:
  Supabase disk encryption at rest + TLS in transit. **No** column-level pgcrypto on
  sender/to_email/body/actor_email — data is synthetic, and pgcrypto there would break RLS
  joins, indexing, and audit snapshots. A real-PII production deployment must add field-level
  encryption (and revisit the audit snapshot design) — an explicit pre-prod gate, not a
  showcase task.
- **R4 — Send is at-least-once, not exactly-once (Phase 5).** If Gmail succeeds and the
  process crashes before `mark_sent` commits, a retry re-sends. The claim pattern prevents a
  duplicate *approval* from double-sending, but this crash window is real. README/eval must
  say *at-least-once*. Mitigation in place: every outbound carries an `X-Freight-Quote-Id`
  marker enabling a future mailbox-dedup check before re-send (the dedup itself is a later
  task).
- **R5 — `next` App-Router DoS (6.6).** The frontend `next@14.2.35` carries advisories
  fixable only by the `next@16.2.9` semver-major, which would risk breaking the React-18 App
  Router build. **Not bumped now.** Most advisories are **unreachable** in this console (no
  `middleware.ts`, no i18n, empty `next.config` — no rewrites/`remotePatterns`, no
  `next/image`, no `beforeInteractive`/CSP-nonce). The residual is a **generic RSC /
  App-Router DoS (availability)** on a low-volume, Supabase-auth-gated internal console,
  largely platform-mitigated on the Vercel target. **Carry-forward:** do the `next` 14→16
  (+ aligned `eslint-config-next`, retiring the `glob` override) at **Phase 8/10** with a
  real build/test pass. The `glob` CLI command-injection CVE and its parents are already
  fixed (6.6).
- **R6 — Cron is best-effort + can auto-disable (Phase 2.7).** GitHub enforces a 5-minute
  floor and may delay/skip runs; scheduled workflows auto-disable after 60 days of repo
  inactivity. Correctness is independent of cadence (idempotent claims + DB reconciliation
  sweep mean a delayed/dropped poll only adds latency, never loss or double-process), so this
  is an availability/operational note, not a correctness risk. Needs an operational keepalive
  at Phase 8.
- **R7 — Wiring carry-forwards (Phase 8).** Until set, the fail-closed posture holds (the
  endpoints 401 / reject): real QStash signing keys + `QSTASH_EXPECTED_URL` `sub` match
  (6.1); `CRON_SECRET` GitHub Secret on both sides (6.2); real `CORS_ALLOW_ORIGINS` and
  deployed JWKS/issuer (6.3); real Upstash `REDIS_URL` (6.4, limiter inert/fail-open until
  reachable). Also pin `HF_MODEL` + confirm the HF API shape (Phase 3/9), and wire Supabase
  Storage to replace the PDF placeholder reader.
- **R8 — Trust boundary is proven independent of the model; classification correctness is
  not a security property.** A model that *mis*classifies a legitimate email degrades quality
  (routes to `needs_review`), not safety — `needs_review` is the safe sink. Measured in
  Phase 9.

## 7. Assumptions & out of scope

**Assumptions.** Managed providers (Supabase, Upstash, Vercel, Hugging Face) and their
infrastructure are trusted; TLS protects data in transit; the Supabase `service_role` key
is kept secret (it is the sole writer of invariant tables — its compromise bypasses RLS and
the server-side-write-only model); the Gmail integration is single-inbox with a
least-privilege refresh token.

**Out of scope.** Provider physical/infrastructure security; a full penetration test or
real-model red-team (R1/R8 → Phase 9); and — per `CLAUDE.md` "Do not" — Kubernetes, a
service mesh, multi-region, or a self-hosted load balancer (over-engineering is a defect at
this volume). This document is a **threat model**, not a security audit; it reflects the
state of `DECISIONS.md` through Phase 6.6 and should be updated alongside it.
