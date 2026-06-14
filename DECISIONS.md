# DECISIONS.md
Append decisions and dead-ends here, newest first, with dates.

## 2026-06-14 — Phase 7 triage: local-now vs deploy-time (front-load local)
**The split (recorded so Phase 7 doesn't quietly become half of Phase 8).** Buildable +
testable LOCALLY now: structured JSON logs + correlation id (7.1); health/readiness +
retries-with-backoff + DLQ replay (7.2); Prometheus metrics instrumentation + `/metrics`
(7.3); `RECOVERY.md` runbook (7.4, backed by 7.2). Genuinely DEPLOY-TIME (Phase 8):
Grafana Cloud dashboard, Sentry DSN wiring, Supabase backups toggle, uptime monitor —
for each, the instrumentation/seam is local; only the external destination is wired at
deploy. **The done-when splits too:** "trace one email end to end" is the LOCAL gate
(correlation-id logs); "the dashboard is live" is the DEPLOY gate (Phase 8). Ordering
front-loads the local tasks 7.1 → 7.4.

## 2026-06-14 — Phase 7.1: structured JSON logs + correlation id (ingest -> send)
**Dependency-free.** A small `logging.Formatter` (`JsonFormatter`) emits one JSON object
per record; a `contextvars.ContextVar` (`correlation_id`) + a `logging.Filter` stamp the
id onto every record. No structlog/json-logger dep — right-sized. `configure_logging` is
idempotent (re-installs a single named handler) and called from the API app factory and
the worker entrypoint (replacing the old `basicConfig`).

**Correlation id = the originating `gmail_message_id`** — the same key that idempotently
identifies one inbound email — so one email traces end to end by grepping one id.
`bind_correlation_id` is a context manager that resets on exit (no leakage across messages
or async tasks; contextvars are per-task). Bound at three seams: the consumer `handle()`
(covers extract → rate → finalize), the poller `_publish()` (both front-door and the
reconciliation sweep), and `send_quote`.

**The send threads the REAL end-to-end id (no degradation).** `send_quote` already resolves
`email = repo.get_deal_email(deal.id)` BEFORE the send, so the originating
`email.gmail_message_id` is in scope at zero extra cost — bound there, the human send logs
under the SAME id ingest used. (The `gmail.send` return is the new OUTBOUND message id,
logged as a field, never the correlation key.) So "trace one email end to end" is genuine,
not "ingest→finalize only".

**Seam noted honestly:** `reject_deal` does NOT fetch the inbound email (it sends nothing),
so it is NOT yet bound to a correlation id — its log lines thread under no id (or could bind
`deal_id` later). Reject is a terminal side-branch, not on the ingest→send path, so this is
acceptable; recorded here rather than left as a silent gap. If reject-path tracing is wanted,
bind `deal_id` (or fetch the email) in a follow-up.

**Tested (hermetic):** `tests/test_logging.py` — valid JSON + required keys; id present when
bound / null when not; contextvar resets (no leakage across blocks); extras merged; exception
captured; one bound block threads a single id. Smoke-verified the real stdout JSON. Full suite
224 passed.

## 2026-06-14 — Phase 6.8: close-out (verify-and-record; Phase 6 closed)
**Not a rebuild — verification.** No code changed. The two done-when gates were verified
by scan/test, and the PLAN Phase 6 boxes ticked honestly.

**"No secret in the repo" — scanned, not asserted.** No gitleaks/trufflehog installed, so:
(1) structural — `.env` / `web/.env.local` are **untracked + gitignored** and were **never
committed** (`git log --all -- .env` empty); only `*.example` files are tracked.
(2) `.env.example` + `web/.env.local.example` are **placeholders-only** (`replace-me`,
`your-project`, localhost). (3) full-history patch scan (`git log -p --all`) for high-signal
formats (PEM, JWT `eyJ…`, `service_role` JWTs, `AKIA`, `ghp_`, `sig_`, `sk-`, `AIza`) →
**zero real secrets**; every `service_role` hit is the Postgres ROLE NAME in docs/SQL/code,
not a key. (4) tracked-source scan for non-placeholder KEY/TOKEN/SECRET/PASSWORD assignments
→ none. **Called out as intentional, not leaks:** `postgres:postgres@localhost` (local-dev
DSN default) and the seed `freight-demo-pw` (demo password, explicitly "never production").

**"Injection can't drive a bad send" — cited, not re-proven.** `tests/test_containment.py`
green (9 passed): the 6.5 fooled-model sweep over both vectors (email + PDF) with
per-dimension assertions, plus the no-auto-send structural test (extract() has no send
channel). This is the evidence for Phase 6's done-when. Supporting gates re-confirmed green
(sig/cron/cors/limiter/llm-guard = 49 passed; full suite 217). Gmail scopes confirmed
`gmail.readonly` + `gmail.send`.

**PLAN boxes — honest ticks.** Five `[x]` (secrets+audit 6.6, signatures+scopes 6.1/Phase 2,
limiter 6.4, audit-append-only+containment 6.0/6.5, THREAT_MODEL 6.7). One **`[~]` partial,
not a silent tick**: "Encrypt PII columns / TLS / CSRF" — PII column encryption was
**de-scoped to at-rest baseline** (synthetic data; pgcrypto would break RLS/joins/audit; real-
PII prod delta = THREAT_MODEL R3), TLS is in transit, CSRF is **N/A** on the bearer model
(6.3). Ticking it `[x]` would misrepresent the log.

**Phase 6 is closed.** Next is Phase 7 (observability + reliability). Open carry-forwards
into Phase 8 wiring are tracked as THREAT_MODEL R2/R5/R7 + the existing per-task DECISIONS.

## 2026-06-14 — Phase 6.7: THREAT_MODEL.md (boundary-driven, traced to this log)
**Structure.** The model is organized around the system's ACTUAL trust boundaries (B1–B10),
not a fresh/STRIDE-forced model: each boundary states threat → defense → residual, and every
defense is **traced to its DECISIONS entry** (6.1 QStash sig, 6.2 CRON_SECRET, 6.3 CORS/JWT,
6.4 limiter, Phase 1 RLS, Phase 4 state machine + MC gate, Phase 5 send gate) so the doc stays
faithful to what was built and is re-auditable against this log. Not re-derived; cited.

**Centerpiece.** §5 is the injection defense: allowlist-REJECT gate + capped confidence +
human gate, on BOTH vectors, **proven by the 6.5 containment run** (fooled-model, per-dimension
assertions, no-auto-send). Framed as the system's novelty.

**Residuals are explicit (R1–R8), not buried.** Per the 6.7 ask, the three named residuals are
surfaced as first-class items: R5 the `next` 14→16 App-Router-DoS carry-forward (with the
unreachable-advisory reasoning), R2 the rate-limiter proxy-IP caveat, R3 the PII at-rest baseline
/ real-PII prod delta. Plus R1 real-model accuracy = Phase 9, R4 send at-least-once double-send
window, R6 best-effort cron, R7 the Phase 8 wiring carry-forwards, R8 misclassification is a
quality not safety property.

**Maintenance.** Doc-only task (no code; lint/types/tests still green at 217). THREAT_MODEL.md
states it reflects DECISIONS through 6.6 and must be updated alongside this log.

## 2026-06-14 — Phase 6.6: dependency audit (pip-audit + npm audit)
**Backend — clean.** `pip-audit` added as a dev dep (`uv add --dev pip-audit`), so the
scan is reproducible (`uv run pip-audit`) for Phase 8 CI. Result: **no known
vulnerabilities** across all locked deps. The only "skip" is `freight-pipeline` itself
(our unpublished package, not on PyPI) — expected, not a finding.

**Frontend — the 5 Phase-5 carry-forward vulns, two clusters.** `npm audit` resolved per
finding (NOT a blanket `--force`, which would pull `next@16.2.9`, a build-breaking major):

| # | Package | Sev | What | Disposition |
|---|---------|-----|------|-------------|
| 1 | `glob` 10.3.10 | high (7.5) | CLI `-c/--cmd` command injection, GHSA-5j98-mcp5-4vw2 (range 10.2.0–10.4.5) | **FIXED** via `overrides: {glob: 10.5.0}` |
| 2 | `@next/eslint-plugin-next` | high | only flagged: depends on vulnerable glob | **FIXED** (clears with #1) |
| 3 | `eslint-config-next` | high | only flagged: depends on #2 | **FIXED** (clears with #1) |
| 4 | `next` 14.2.35 | high | 13 advisories (RSC/Image/middleware/i18n/WS) | **NOT exploitable here; Phase 8/10 carry-forward** |
| 5 | `postcss` 8.4.31 *bundled in next* | mod | XSS in CSS stringify, GHSA-qx2v-qp2m-jg93 | **build-time only; clears when #4 is bumped** |

**#1–3 (cleanly fixed).** `overrides: {glob: "10.5.0"}` patches the actual CVE: 10.5.0 is
just above the vulnerable range and stays in glob's v10 major (lowest breakage risk vs.
glob 11; npm can't comment-key an override, so this rationale lives here). All dev-only
tooling, and the eslint plugin uses glob as a LIBRARY, not the vulnerable CLI — but it is
cleanly patchable, so it's patched. Verified post-override: `npm audit` 5→2, and
`npm run lint && build && typecheck` all pass (no regression). glob also de-dupes to
10.5.0 under eslint's rimraf.

**#4–5 (not cleanly fixable — per-advisory judgment, not silent acceptance).** The only
fix npm offers is `next@16.2.9`, a 14→16 **semver-major** that risks breaking the React-18
App Router build — Phase 8 (deploy) / Phase 10 (console polish) work, and exactly the
"don't break the build" line. NOT bumped now. Non-exploitability is grounded in what the
console ACTUALLY uses (verified): **no `middleware.ts`, no i18n, empty `next.config` (no
rewrites / no `remotePatterns`), no `next/image`, no `beforeInteractive` / CSP-nonce.**
That makes the Image-Optimizer DoS (GHSA-9g9p/3x4c/h64f), middleware-proxy cache-poisoning
& bypass (GHSA-3g8h/36qx/ggv3), rewrite smuggling, i18n bypass, WebSocket SSRF (GHSA-c4j6),
and nonce/`beforeInteractive` XSS (GHSA-ffhc/gx5p) advisories **unreachable** in this
console. Residual = generic **RSC/App-Router DoS (availability)** on a low-volume,
Supabase-auth-gated INTERNAL console, largely platform-mitigated on the Vercel deploy
target. Bundled-postcss XSS (#5) is **build-time** CSS stringify over our own TRUSTED CSS —
no untrusted CSS input.

**Carry-forward (tracked, not buried):** the `next` 14→16 upgrade (+ aligned
`eslint-config-next@16`, which also retires the glob override) lands at Phase 8/10 with a
real build/test pass. This App-Router-DoS residual is ALSO recorded in `THREAT_MODEL.md`
(6.7) as a tracked residual risk, not only in this table.

## 2026-06-13 — Phase 6.5: adversarial containment run (both vectors)
**The run.** `tests/test_containment.py` sweeps the WHOLE adversarial corpus through the
real `extract()` gate with a fully **fooled model** (`_FooledLLM` returns the attacker's
structured payload at confidence 1.0 — the worst case: the model is completely
compromised by the injection). It proves the DETERMINISTIC validation gate contains every
injection regardless of model behaviour. Hermetic and **never skips** (a containment proof
must always execute in CI) — it drives `extract()` directly rather than the DB-end-to-end
consumer; the consumer's PDF routing + DB `needs_review` write stays covered by
`test_pdf_intake.py::test_pdf_embedded_injection_is_rejected`.

**Both vectors, as required.** Email-body (samples 9-12) AND attachment-borne PDF
(samples 13-14). For the PDF samples the run renders a REAL text PDF from the corpus's new
`attachment_text`, asserts `extract_text` surfaces the injection marker (proving the
attack actually reaches the model boundary through the text layer), then drives the fooled
payload through the SAME `extract()` path CLAUDE.md mandates. This closes the stale Phase 1
carry-forward (the PDF *samples* existed since the corpus extension, but nothing exercised
them; the note at the top of `synthetic/emails.py` is refreshed).

**Per-dimension assertions (not uniform).** Each adversarial sample carries an
`attack_payload` whose ONLY gate-violating field targets a distinct dimension, plus an
`expected_failure` reason-prefix the run asserts appears in `review_reason`. So weakening
one gate dimension fails LOUDLY instead of being masked by another sample's rejection.
Coverage: `invalid_intent` (body 9 + PDF 14 — intent gate on both vectors),
`invalid_dest_city` (10, newline), `invalid_origin_state` (11), `invalid_equipment`
(12, spoofed tool-call), `weight_out_of_range` (13). Confidence 1.0 never bypasses.

**No-auto-send invariant.** A structural test asserts `extract()`'s signature is exactly
`(llm, subject, body)` — no Gmail/sender/queue channel — and the module exposes no `send`,
so the model can never trigger an action. A second test feeds a CLEAN valid payload at
confidence 1.0 and asserts the result is still just an `ExtractionOutcome` (`processed`
data), never a send. The only outbound path remains the human-gated `/review/send`
(proven by `test_send.py`). This is Phase 6's done-when: injection can't drive a bad send.

**Corpus carries the attack ground truth.** `SyntheticEmail` gained `attack_payload`,
`expected_failure`, `attachment_text` (adversarial-only, optional → back-compatible;
`test_synthetic.py` unchanged). A completeness guard asserts every adversarial sample is
runnable and both vectors stay represented, so a future corpus edit can't silently drop a
sample or a vector. These labels also feed the Phase 9 real-model run (fork: 6.5 + corpus
run merge).

**Scope (unchanged forks).** Deterministic fooled-model mock, not a real model — this
proves the GATE, not model accuracy (real-model accuracy is Phase 9). The fooled mock
lives in the test, not shipped `src` (keeps attack-simulation out of the package); the
reusable artifact is the labeled corpus.

## 2026-06-13 — Phase 6.4: rate limiter (public API) + global LLM-call guard
**One fail-open primitive.** `RateLimiter` (`freight.security.rate_limit`) is a fixed-
window counter over Redis (`INCR`; arm `EXPIRE` on the first hit of a window). It is
SECONDARY to the auth gates and FAIL-OPEN: any `RedisError` → `allow` returns True
(proceed), short 1s timeouts so an outage fails open FAST — same discipline as the
idempotency/rate caches, and exactly the decided fork. `limit<=0` disables. Both the
HTTP limiter and the LLM guard share this one primitive (the primitive stays FastAPI-free
so the guard can import it).

**HTTP limiting runs BEFORE auth.** `RateLimit(scope)` (`freight.security.http_rate_limit`)
is a route-level dependency keyed `rl:{scope}:{client_ip}`; 429 over the per-minute cap
(`public_rate_limit_per_minute`, default 120). Applied to every externally reachable POST
route: `/ingest`, `/poll`, `/jobs/surcharge`, `/review/send`, `/review/reject`. FastAPI
inserts route-level `dependencies=[]` at the FRONT of the dependant list, so the limiter
is evaluated before the signature/cron-secret/JWT gates — a flood is cheap-rejected before
any crypto/DB work. This ordering does NOT contradict "limiter secondary to auth": that's
about the fail-open priority (Redis-down ⇒ auth still gates), which holds. `get_rate_limiter`
is an `@lru_cache` singleton (the counter must persist across requests); overridden in tests.

**Global LLM-call guard = transient backpressure.** `GuardedLLMClient`
(`freight.security.llm_guard`) decorates ANY `LLMClient` and is wired in
`build_llm_client`, so EVERY call site is guarded with no call-site change (honors the
build-against-interfaces invariant). Global budget key `llm:calls`, `llm_calls_per_minute`
(default 60). Over budget → raise `LLMRateLimitError`, which propagates out of the consumer
exactly like `HFTransientError` (uncaught by the /ingest route's `except IngestError` → 5xx
→ QStash retries → DLQ on exhaustion). Retrying is correct — the budget refills — UNLIKE a
content failure, which routes to `needs_review`. FAIL-OPEN on Redis-down (delegate to the
model). **Tradeoff:** under a sustained flood, legit messages burn QStash retries and may
DLQ (replayable at Phase 7); accepted backpressure for a low-volume showcase.

**Disable switch.** `rate_limit_enabled` (default True) gates both: false ⇒ `build_llm_client`
returns the bare backend and `RateLimit` is a no-op. The factory backend-SELECTION tests set
it false to isolate which impl is chosen from the guard wrapper; a separate test asserts the
guard wraps when enabled.

**Test (hermetic, no real Redis).** `tests/test_rate_limit.py` (dict-backed FakeRedis):
`allow` permits up to N then blocks, arms expiry once, fails open when Redis raises, `limit<=0`
disables; the HTTP dep 429s over the limit (shared limiter instance so the counter persists),
fails open on outage, and no-ops when disabled. `tests/test_llm_guard.py`: delegates under
budget, raises `LLMRateLimitError` over budget WITHOUT calling the inner model, fails open on
outage. Existing route tests are unaffected — Redis-absent ⇒ the limiter fails open.

**Phase 8 carry-forwards (NOT done now):**
- Behind the deploy proxy (Fly/Railway) `request.client.host` is the PROXY ip — wire a trusted
  `X-Forwarded-For` / platform client-IP header so per-client limiting is real, not per-proxy.
- Set the real Upstash `REDIS_URL` (the limiter is inert/fail-open until a reachable Redis).
- Tune `public_rate_limit_per_minute` / `llm_calls_per_minute` against measured Phase 9 volume.

## 2026-06-13 — Phase 6.3: CORS locked to an explicit origin allowlist
**The lockdown.** A Starlette `CORSMiddleware` is attached in `create_app()` via one
seam, `configure_cors(app, settings)` (`freight.security.cors`) — never inline in the
app body or handlers, same discipline as `cron_auth`/`qstash_verifier`. Origins come
from `CORS_ALLOW_ORIGINS` (comma-separated, `cors_origins_list()` strips/drops empties),
NEVER `["*"]`. Default `http://localhost:3000` (Next dev); empty => no origin allowed
(fail-closed), consistent with 6.1/6.2. `allow_methods=["POST"]`,
`allow_headers=["Authorization","Content-Type"]`.

**`allow_credentials=False` — deliberate.** The console authenticates with an explicit
`Authorization: Bearer <JWT>` header (`web/lib/api.ts` `authedPost`), not cookies, and
never sends `credentials:'include'`. So credentialed CORS is never needed; false is the
tighter setting and sidesteps the browser's wildcard+credentials rejection rule. The
bearer header rides through fine via `allow_headers` (credentials govern cookies/TLS
client certs, not request headers).

**Scope.** Only `/review/send` + `/review/reject` are browser-facing. `/ingest` (QStash)
and `/poll` / `/jobs/surcharge` (cron curls) are server-to-server with no browser
`Origin`, so a global allowlist is harmless to them.

**CSRF — assessed, intentionally NOT adding token machinery.** PLAN's "CSRF on
state-changing routes" line is bundled under the PII/TLS bullet; the DECISIONS task
breakdown scopes 6.3 to CORS. Classic CSRF needs ambient credentials a cross-site
request auto-attaches (cookie/session). This API has none: auth is a bearer header that
JS must set explicitly and that a cross-site form/img/navigation cannot forge, and there
are no auth cookies. So there is no live CSRF exposure to defend; a CSRF token would be
dead weight on a bearer model. If cookie-based sessions are ever introduced, revisit.

**Test (hermetic).** `tests/test_cors.py` exercises `configure_cors` on a throwaway app
with explicit settings (independent of the env/settings singleton): allowed origin →
preflight + actual response echo ACAO; unlisted origin → no ACAO grant; empty allowlist
→ fail-closed (no ACAO); `allow-credentials` never advertised.

**Phase 8 carry-forward (NOT done now):** set `CORS_ALLOW_ORIGINS` to the deployed
console origin (the Vercel URL) in the backend env — the factory swaps it with no code
change. Until set, only `localhost:3000` is allowed (dev default), which is the correct
fail-closed-ish posture for a not-yet-deployed console.

## 2026-06-12 — Phase 6.2: CRON_SECRET bearer on /poll + /jobs/surcharge
**The gate.** Both cron-triggered endpoints (which trigger ingestion / rate writes)
now require `Authorization: Bearer <CRON_SECRET>`. Auth lives in one dependency,
`require_cron_secret` (`freight.security.cron_auth`), applied via
`@router.post(..., dependencies=[Depends(require_cron_secret)])` on each route — never
inline in the handlers, never mixed with poll/surcharge logic. Reuses the 6.1 seam
pattern.

**Single secret, env-only.** `CRON_SECRET` guards both endpoints (replacing the old
per-endpoint `POLL_TOKEN`/`SURCHARGE_TOKEN`). Added to Settings (default `""`) and
`.env.example`. Header parsed properly: missing header / non-`bearer` scheme / empty
token → 401. Compare is `hmac.compare_digest`, never `==`.

**The fail-open trap, closed explicitly.** `hmac.compare_digest("", "")` is `True`, so
an unset secret + an empty bearer would otherwise pass. The dependency rejects an empty
configured secret (401) BEFORE running any compare, and `logger.warning`s that
CRON_SECRET is unconfigured (consistent with 6.1 fail-closed logging). The compare is
never run against an empty configured secret.

**Workflows.** `poll-inbox.yml` and `fuel-surcharge.yml` now send
`Authorization: Bearer ${{ secrets.CRON_SECRET }}` and the old `POLL_TOKEN`/
`SURCHARGE_TOKEN` env refs are dropped. No secret value is in code or git.

**Test (hermetic).** `tests/test_cron_auth.py` stubs the downstream poll/surcharge work
(no DB/Gmail/Redis) and parametrizes BOTH endpoints: correct→200; wrong→401; missing
header→401; malformed (wrong scheme / no token / empty token / no scheme)→401; and the
unconfigured-secret guard (CRON_SECRET="" + empty/any bearer)→401, proving
empty-equals-empty can't fail open. The pre-existing `/poll` and `/jobs/surcharge` route
tests were updated to send the bearer.

**Phase 8 carry-forward (NOT done now):** set the `CRON_SECRET` GitHub Secret AND the
matching backend env value at wiring, and REMOVE the old `POLL_TOKEN`/`SURCHARGE_TOKEN`
repo secrets. Until `CRON_SECRET` is set on both sides the cron workflows will 401 —
that is the fail-closed posture working as intended; the crons do no real work pre-Phase
8 anyway (they're inert until `POLL_ENDPOINT`/`SURCHARGE_ENDPOINT` are provided).

## 2026-06-12 — Phase 6.1: /ingest verifies the QStash Upstash-Signature
**The auth boundary is not hand-rolled.** Verification delegates to the official
`qstash` SDK (`qstash==3.4.0`, `Receiver`, PyJWT HS256 under the hood) — no bespoke
JWT/HMAC. PyPI name confirmed from the installed source as `qstash` (the older
`upstash-qstash` is superseded); `Receiver(current_signing_key, next_signing_key)`,
`receiver.verify(*, signature, body: str, url=None, clock_tolerance=0)` raising
`qstash.errors.SignatureError`. The Receiver itself tries current→next key (rotation).

**The seam.** `freight.security.qstash_verifier`: a `QStashVerifier` Protocol stated in
**raw bytes** (`verify(*, body: bytes, signature: str) -> None`, raise = reject) +
`SDKQStashVerifier` (decodes utf-8 only at the SDK boundary, since the body-hash claim
is over the exact raw bytes — any re-serialization breaks the hash) + `build_qstash_
verifier(settings)`. Phase 8 swaps real keys/URL behind the factory without touching
the route. Injected via `Depends(get_qstash_verifier)`.

**Ordering (the invariant that matters).** Route dependency chain
`require_qstash_signature` (read `Upstash-Signature` header + `await request.body()`,
verify) → `parse_verified_message` (`model_validate_json` ONLY after verify) → handler.
Because the message arrives via `Depends`, FastAPI does no auto body-parse — so the
signature check over raw bytes strictly precedes the JSON parse, the `gmail_message_id`
idempotency claim, and all Redis/DB/enqueue work (which live inside `consumer.handle`).

**Fail-closed, and don't hide bugs.** Missing header → 401; `SignatureError`
(bad/expired/wrong-key/sub-mismatch) → 401; ANY other verifier exception → still 401
but logged with the exception type, so a misconfiguration can't masquerade as routine
auth-failure noise (Phase 7 will structure these logs). The verifier can never fall
through to the handler.

**Keys + expected URL.** `QSTASH_CURRENT_SIGNING_KEY` / `QSTASH_NEXT_SIGNING_KEY` /
`QSTASH_EXPECTED_URL` (the signed `sub` claim) — env-only, placeholders in
`.env.example`, empty locally. Empty expected-URL ⇒ `sub` not matched (claim still
required present); set to the public /ingest URL in real deploys.

**Test proves it for real (hermetic, no DB).** `tests/test_ingest_signature.py` mints
a genuine HS256 token locally (claims `iss=Upstash, sub, exp, nbf, body=urlsafe_b64(
sha256(raw)).rstrip("=")`, matching the SDK source) and runs the REAL `SDKQStashVerifier`
with test keys — nothing stubbed; the consumer is a no-op override so 200 means only
"gate passed". Cases: valid→200; tampered body→401; missing header→401; wrong-key→401;
expired→401; **sub-mismatch→401** (proves the expected-URL binding actually rejects).
`tests/test_qstash_verifier.py` unit-tests the seam (incl. next-key rotation). The
pre-existing `test_consumer.py` route test was updated to sign its bodies.

**Phase 8 carry-forwards (NOT done now — this is a local slice):**
- Confirm the SDK API against live QStash docs (pinned to source-read of 3.4.0 here).
- Real `QSTASH_CURRENT/NEXT_SIGNING_KEY` from the QStash console (GitHub/host secrets).
- `sub`/public-URL match: set `QSTASH_EXPECTED_URL` to the real deployed /ingest URL,
  accounting for any deploy proxy that rewrites the host before the app sees it.
- Confirm QStash's actually-delivered header name (`Upstash-Signature`) and claim set
  match the SDK's expectations against a live delivery.

## 2026-06-12 — Phase 6 kickoff: four security forks resolved (option 1)
**Decision:** Phase 6 (security hardening) starts with these forks locked, all
right-sized for a low-volume synthetic showcase:
- **Cron auth** (`/poll`, `/jobs/surcharge`): shared-secret `CRON_SECRET` bearer,
  constant-time compare (GitHub Secrets + backend env only). Not GitHub OIDC.
  `/ingest` uses QStash `Upstash-Signature` (separate mechanism, separate source).
- **PII**: at-rest baseline (Supabase disk encryption) + TLS in transit. No
  column-level pgcrypto — data is synthetic; pgcrypto on sender/to_email/body/
  actor_email would break RLS joins, indexing, and audit snapshots. Real-PII prod
  delta noted in THREAT_MODEL.md.
- **Rate limiter**: fail-open on Redis unavailable (consistent with cache
  discipline; auth gates are the primary access control, limiter is secondary).
- **Adversarial containment run**: deterministic 'fooled-model' mock — proves the
  *validation gate* contains injection regardless of model behavior. Real-model
  accuracy is Phase 9 (corpus run merged with 6.5).

Task breakdown: 6.0 confirm append-only + secret audit → 6.1 /ingest QStash sig →
6.2 cron CRON_SECRET → 6.3 CORS lockdown → 6.4 rate limiter + LLM guard → 6.5
containment run → 6.6 pip-audit + npm audit → 6.7 THREAT_MODEL.md → 6.8 close out.

**Why:** Recorded now (not at 6.8 close-out) because the build session is being
`/clear`-ed at the 6.0 boundary to shed stale phase 0–5 context. The on-disk
record must carry the resolved forks so a fresh session doesn't re-litigate them.

**Trade-off:** Decisions logged before the work they govern is complete; if a fork
proves wrong mid-phase, amend with a follow-up entry rather than editing this one.

## 2026-06-12 — Phase 5: review console + human-gated send (spine complete)
**The human gate.** The Gmail send is reached ONLY via an explicit reviewer action
(`POST /review/send`) — never the pipeline. The model proposed a quote in Phase 4; a
person disposes here.

**Send is AT-LEAST-ONCE, not exactly-once (record honestly; do NOT overclaim).** The
claim pattern (UNIQUE(quote_id) `sends` row + audit, committed) prevents a duplicate
*approval* from double-sending. But there is a real double-send window: if Gmail
succeeds and the process crashes BEFORE TX-B (`mark_sent`) commits, the row stays
`claimed` and a retry re-sends. So the guarantee is **at-least-once delivery with
no-duplicate-on-duplicate-approval**. README/eval must say at-least-once, never
"exactly-once". MITIGATION IN PLACE: every outbound carries an `X-Freight-Quote-Id`
marker header (via `OutboundMessage.headers`) so a FUTURE dedup can check the mailbox
for that marker before re-sending and close the window. The dedup check itself is a
later task.

**Send flow (dual-write done right).** authz reads (quote→deal; owned-by-reviewer or
admin; state 'quoted') → TX-A: `claim_send` + audit `email.send.claimed` (atomic;
already-`sent` → 409) → Gmail send AFTER the claim commits → TX-B: `mark_sent` + audit
`email.sent`. A Gmail failure leaves `claimed` (502, recoverable); a crashed `claimed`
row resumes on retry. `sends` is server-side-write-only (reviewers READ via RLS).

**Console↔backend boundary.** The console READS the queue directly from Supabase via RLS
(reviewer JWT scopes to their deals); all send/reject WRITES go through the FastAPI
backend (the only sender), which verifies the JWT and acts under the service role —
preserving the Phase 1 server-side-write-only model.

**Auth: ES256/JWKS SUPERSEDES the fork-3 HS256 choice.** Local (and current) Supabase
signs access tokens with asymmetric ES256 keys, so the backend verifies against the
project JWKS (URL derived from `SUPABASE_URL`), validating exp + aud='authenticated' +
iss. App role (reviewer vs admin) is read from `public.users`, never the token's
'authenticated' role claim. (Phase 8 carry-forward: verify the deployed project's JWKS +
issuer at wiring.)

**Seed users are now login-able** (the Phase 1 carry-forward closed): `seed.sql` writes
full `auth.users` rows (bcrypt `encrypted_password`, confirmed, email provider) + matching
`auth.identities`. DEV/DEMO password `freight-demo-pw` — never production.

**Verified end to end (real token):** `POST /review/send` with a real Supabase JWT →
200, `sends`→'sent', audit `email.send.claimed`+`email.sent`. Frontend builds/lints/
typechecks; browser click-through is manual.

**Carry-forwards:** `web` `npm install` flags 5 audit vulns (transitive) — Phase 6
`npm audit`. The send dedup-via-marker and `deals.accepted_quote_id` population are
later tasks.

## 2026-06-12 — Phase 4: state machine, MC gate, rate engine, atomic finalize
**State machine + resume.** Pure `advance(state, event, *, held_from=None)` enforces
`new_enquiry → quoted → negotiating ⇄ quoted → rc_received → contract_signed → scheduled`
(+ rejected/on_hold); skips raise. `on_hold` carries no history, so resume requires a
stored `held_from` (the active state held from); a deal moved to on_hold records it
(`deals.held_from`). No/invalid held_from → TransitionError.

**MC eligibility gate.** No MC on a rate enquiry → eligible (proceed); MC active →
eligible; MC blocked / table-unknown / not-found → on_hold (no engine, no quote — the
gate runs before `quoted`, so a deal that fails it can't be quoted). Re-enforced before
contract_signed (later phase). `mc_number` was added to the extraction schema; a
malformed MC is DROPPED to None (not a hard reject) since the carriers table is the
allowlist and the gate maps unknown → on_hold.

**Rate engine.** Contracted lookup pins the current contracted version (Model A: filter
source='contracted', carrier precedence, effective_from/created_at tiebreaker); a miss
materializes a `source='computed'` row via the transparent placeholder formula and pins
it, `is_computed=true`. The quote snapshots amount/currency from the pinned rate.
`quote_for` takes the PRE-FETCHED contracted rate — no in-tx lookup.

**Atomic finalize (the heart of Phase 4).** The consumer (transport) opens
`repo.begin()`, runs the pre-tx cached contracted lookup (Redis OUT of the tx), and calls
`deals.finalize(conn, ...)`; the service layer owns the dispatch/gate/quote orchestration;
the repo is dumb and conn-scoped (`flip_if_queued`, `create_deal`, `link_email`,
`advance_deal`). One transaction does the process-once flip + deal + computed-rate +
quote, so redelivery no-ops and a crash can't split the flip from deal creation.
**Limitation:** the pre-tx contracted lookup uses carrier_id=None (lane-generic) — the
carrier is resolved by the in-tx gate, so carrier-specific rate precedence for
rate_request quotes is deferred (precedence still holds for the standalone lookup).

**Intent dispatch (deal scope).** Only `processed` `rate_request` creates a deal+quote.
Other processed intents (negotiation/rc/contract/other) → `needs_review`
('intent_not_yet_routable'), NO deal — thread-linking is later-phase, and a silent
`processed` flip would make a dropped email look handled. The Phase 3 extract-before-claim
tradeoff stands (a rare concurrent redelivery can incur a duplicate LLM call; only one
write lands; the LLM has no side effects).

**Cache invalidation coupling.** Only CONTRACTED-version inserts invalidate the rate cache
(the surcharge job, any admin contracted insert). The engine's `source='computed'`
materialization must NOT invalidate — computed rows are excluded from the cached
contracted lookup, so they can't stale it.

**Surcharge job.** Re-versions each current contracted lane by a delta — always an INSERT
(rates append-only; forbid_mutation blocks overwrites). Verified: append (+1 row, prior
version intact), new version becomes current.

**Validator fix.** The equipment format gate now allows `_` so the canonical `dry_van`
passes (injection punctuation like `;` still rejected before canonicalization).

## 2026-06-11 — Phase 3 extraction: trust boundary, confidence, routing, PDF
**Trust boundary (the injection defense).** Flow is `RawExtraction` (permissive,
UNTRUSTED LLM output) → deterministic gate (`validation.validate`) → `ValidatedExtraction`
(the only type the rate engine consumes). The LLM only emits structured data; it can
never trigger an action. The gate is the defense, NOT the model's behavior — and it is
**allowlist-REJECT, not sanitize**: states (USPS allowlist), equipment (format-gate then
keyword-canonicalize; injection punctuation rejected *before* canon), weight (numeric
format + range), cities (name format), and `intent` (5-value allowlist — intent is an
allowlisted untrusted field). Anything off the allowlist/format/range → reject → review.
We never strip injection out of a field and keep the remainder.

**Confidence.** Composite is deterministic-led: `0.8 × completeness + 0.2 × model`, the
model capped at weight 0.2 so a self-reported score can never cross the 0.7 threshold
alone. **Any validation failure forces `needs_review` regardless of the model score** —
an injected "confidence 1.0" cannot skip the gate.

**Phase 3↔4 boundary.** Phase 3 stops at "validated record + intent + confidence written
on the email row" (`extracted` jsonb, `intent`, `confidence`, `ingest_status`); deal
creation/linking is Phase 4. Kept the boundary light so process-once is a single atomic
write, not a multi-row idempotency problem.

**Process-once.** A conditional UPDATE `WHERE ingest_status='queued'` — the delivery that
flips the row wins and writes; 0 rows → already processed → ack and skip. **Accepted
tradeoff:** extraction runs BEFORE the claiming UPDATE (no intermediate 'processing'
state), so a rare concurrent redelivery can incur a duplicate LLM call. We accept the
wasted call over the complexity of a processing-claim; correctness is unaffected (only
one write lands, and the LLM has no side effects).

**Status mapping (precise permanent-vs-transient form).** `processed` and `needs_review`
both → the consumer returns 2xx (it SUCCEEDED at routing; QStash must not retry). Only
TRANSIENT faults (`HFTransientError`: 503 cold-start / 429 / network; DB unreachable)
raise → 5xx → QStash retries → DLQ on exhaustion. **Content failures (won't-parse /
invalid / injection / no-text-layer) go to `needs_review` (the human sink), NOT the
DLQ** — retrying them never helps. This refines Phase 2's loose "content-poison → DLQ".

**One structured LLM call.** A single `complete` over the superset schema returns intent
+ all fields together (fewer HF calls → less cold-start/429 exposure, one transient
failure point, one validation pass). HF slice (`HFLLMClient`) targets the
OpenAI-compatible chat-completions surface; `HFTransientError` is the retry taxonomy;
malformed model JSON → low-confidence `LLMResult` (no crash) → review.

**PDF intake.** Text-layer only via `pypdf` (no OCR). Storage is an injectable
`StorageReader` (fixture in tests; `UnconfiguredStorageReader` placeholder pre-Phase 8).
A PDF attachment takes PRIORITY over the email body; no text layer → `needs_review`
(`review_reason='no_text_layer'`), never a crash. PDF text runs the SAME extract +
validation path, so containment holds on the attachment vector too.

**Forward carry-forwards:**
- **Phase 8:** verify the HF API shape against current HF docs and PIN `HF_MODEL` (the ⚠️
  comments in `llm/hf.py`); wire Supabase Storage to replace `UnconfiguredStorageReader`
  (and the QStash slice strings).
- **Phase 9:** a pinned `HF_MODEL` is required for a reproducible extraction/eval run.
- **Future:** scanned-PDF OCR — deferred; currently degrades cleanly to `needs_review`.

## 2026-06-11 — Phase 2 close-out: local topology + ingestion summary
**Local topology (choice a).** Supabase Postgres (local stack `:54322`) is the DATABASE
of record — it also provides Auth + RLS + Storage. Docker Compose is reduced to
**Redis only** (Supabase has no Redis). The api/worker run via `uv run` in dev — matching
how the tests and the poller already run — so there is no cross-stack container
networking to misconfigure. The Phase 0 compose `postgres` service (5432, schema-less)
and `pgdata` volume are removed; the `api`/`worker` compose services are dropped too. The
`Dockerfile` is retained for the Phase 8 cloud deploy of the backend. (Alternative b —
keep api/worker in compose pointing at `host.docker.internal:54322` with
`extra_hosts: host-gateway` — was rejected as needless networking at this volume.)

**Ingestion model (the spine of Phase 2).**
- **Idempotency:** the DB unique constraint on `email_messages.gmail_message_id` is
  authoritative; Redis `SET NX` is a fail-open, evictable pre-check (an outage forces the
  slow DB path, never loss). The poller's per-message order is: pre-check → committed
  `claim_insert` (the claim) → publish id-only thin payload → set `queued`. A crash
  between the committed claim and the publish leaves a `received` row that the **DB
  reconciliation sweep** re-enqueues (bypassing `SET NX`, runs even if Gmail listing
  fails). Sweep threshold (5 min) > worst-case poll runtime + cron interval.
- **Publish-once vs process-once:** the poll enqueues each id once across runs, but QStash
  is at-least-once and redelivers; the real guarantee is **process-once at the consume
  boundary**, implemented in Phase 3 (carry-forward: conditional UPDATE on
  `ingest_status`). Phase 2's consumer does no writes, so double delivery is harmless.
- **DLQ scope:** envelope-poison → DLQ is proven locally on the mock (`LocalDispatcher`
  retries N+1 then dead-letters; `/ingest` maps a raise → 5xx). Content-poison is deferred
  to Phase 3; the QStash-cloud DLQ half is proven at Phase 8.
- **Gmail:** single-inbox, refresh-token OAuth (one runtime secret, no token table),
  scopes least-privilege (`gmail.readonly` + `gmail.send`).

## 2026-06-11 — Phase 2.7: poll cron is best-effort; correctness independent of cadence
The GitHub Actions cron (`poll-inbox.yml`) curls a deployed `POST /poll` on the always-on
backend (poller lives in the backend; CI just pings). **Cadence is `*/5`, not `*/2`:**
GitHub enforces a 5-minute MINIMUM on scheduled workflows and even that is best-effort
(10-30 min delays, skipped runs under load) — PLAN's "~2 min" is aspirational here.
`workflow_dispatch` is kept for precise/manual triggering. **Correctness is independent
of cadence:** idempotent claims + the DB reconciliation sweep mean a delayed or dropped
poll only adds latency, never loss or double-process — so no external scheduler is
warranted. The cron job is guarded to skip cleanly when `POLL_ENDPOINT` is unset (inert
until Phase 8).

**Carry-forward — /poll auth (Phase 6 GATE).** Same class as `/ingest`: `/poll` triggers
ingestion and is currently UNAUTHENTICATED. A shared-secret / OIDC check must land before
the Phase 8 deploy.

**Phase 8 operational carry-forwards (would silently break the live poll; NOT caught by
"workflow validates"):**
- **60-day auto-disable.** GitHub auto-disables scheduled workflows after 60 days of repo
  inactivity. A quiet showcase repo stops polling silently (email notice only). Needs an
  operational reminder / keepalive.
- **Default-branch only.** Scheduled workflows trigger only from the DEFAULT branch — the
  cron won't run from a feature branch. Relevant when wiring the live target at Phase 8.

## 2026-06-11 — Phase 2.5: ingestion consumer + /ingest route; three carry-forwards
The consumer (`IngestConsumer.handle`) does ONLY its Phase 2 job: re-fetch the committed
row by id and validate the envelope (row exists + non-empty sender), else raise
`IngestError`. Success is an ack stub marked "Phase 3: extraction extends here". It does
NO writes in Phase 2, so a double delivery is observationally harmless. The `/ingest`
route is a sync `def` (threadpool, shares the sync repo), runs the async `handle` via
`asyncio.run`, and maps a raise → 5xx so QStash retries on status (same failure trigger
as the local dispatcher's retry-on-exception); a poison message exhausts retries → DLQ.

**Carry-forward 1 — process-once (Phase 3).** When extraction adds write-side work, the
consumer enforces CLAUDE.md "never process twice" with a compare-and-set:
`UPDATE email_messages SET ingest_status='processed' WHERE gmail_message_id=:id AND
ingest_status='queued' RETURNING ...`. The delivery that flips the row wins and does the
work; 0 rows flipped → ack and skip. The `processed`/`failed` enum states stay reserved
until then. (Use this conditional-UPDATE approach; don't re-litigate.)

**Carry-forward 2 — /ingest signature verification (Phase 6 GATE).** The route is
UNAUTHENTICATED. Upstash-Signature verification is owned by PLAN Phase 6 and MUST land
before the Phase 8 deploy — an unauthenticated public ingest endpoint must not reach
production. Phase 8 cannot precede this.

**Carry-forward 3 — permanent-vs-transient error routing (Phase 3).** Once
content-validation failures exist, permanent "will-never-succeed" failures can
fast-fail/non-retry instead of burning N+1 attempts. Not needed while everything maps to
5xx.

## 2026-06-10 — Phase 1 close-out: RLS test is an opt-in integration test
The RLS regression guard (`tests/test_rls.py`) connects to the local supabase DB
(`psycopg`, DSN env-overridable via `RLS_TEST_DSN`) and **skips** when the stack isn't
reachable, so `pytest` stays green on a machine without it up. Marked
`@pytest.mark.integration` (marker registered in `pyproject` to avoid unknown-marker
warnings). Phase 8 CI opts in by running it against a live local DB. Two mypy/ruff
gotchas worth remembering: (1) `psycopg` is pulled via `pytest.importorskip` at
runtime but imported under `TYPE_CHECKING` so mypy resolves the real stubs; (2) with
those stubs, `cur.fetchone()` is `tuple | None`, so a `_scalar()` helper asserts
non-None before indexing (strict mypy).

## 2026-06-10 — Phase 1: seed are fixtures (not credentials); test/seed split
**Seed users are FIXTURES, not credentials.** `supabase/seed.sql` inserts
`auth.users (id, email)` only — enough to satisfy FKs and exercise RLS, but NOT
login-able. Phase 5 (Supabase Auth login) will need `encrypted_password`,
`email_confirmed_at`, `aud`, `role='authenticated'`, and `instance_id` — added then or
provisioned via the Auth admin API. Flagged so the demo login doesn't silently bounce.

**Richer seed for demo + load-check; hermetic fixtures for the security proof.** The
seed (2 deals + 1 email + 1 quote) doubles as a load-time integration check of the FK
graph and as Phase 5/10 demo data. But the 1.7 pytest RLS test must NOT read seed
deals — it creates its own reviewer A/B + a deal each in a rolled-back transaction and
asserts, exactly like the 1.5 inline proof. Coupling a security test to demo data is
brittle: adding a demo deal later would flip "A sees 1 deal" to 2 and turn a passing
test red for a non-security reason (or a seed change could mask a real regression).

**Synthetic corpus.** `generate_dataset()` is deterministic and reuses the
`InboundMessage` boundary type the mock `GmailClient` serves. Adversarial samples carry
the TRUE intent/fields so Phase 6/9 can prove injection containment (the attack must
not change the real classification/extraction). Carry-forward (Phase 3): the corpus
covers email-body injection only — add attachment-borne (PDF) injection samples when
PDF intake lands, since CLAUDE.md routes PDFs through the same extraction path.

## 2026-06-10 — Phase 1: RLS — invariant-bearing tables are server-side-write-only
**The pattern.** Tables that carry invariants — `rates` (append-only, versioned),
`quotes` (pins rate_id, snapshots amount, sets is_computed, emits audit), `audit_log`
(append-only, tamper-evident) — and `deals` (the state machine) are
**server-side-write-only**: RLS grants clients READ access, and the `service_role`
(bypasses RLS) is the sole writer. A direct client write would bypass the 1.3
invariants or the state machine, so creation/edit goes through the FastAPI service.
Only `carriers` and `users` accept admin writes via JWT. Apply this consistently to
any future invariant-bearing table.

**deals writes (decision on the open question).** No authenticated INSERT/UPDATE/DELETE
policy on `deals`. RLS does **not** enforce the `new_enquiry → quoted → …` machine — an
owner could otherwise UPDATE `state` to any value. The machine is guarded in the
service layer (or a future trigger), in one place. The writing surface for deals is the
FastAPI backend via `service_role`; reviewers/admins mutate deals only through the API.

**quotes / audit_log writes.** Both server-side only. Rejected a permissive
`WITH CHECK (true)` audit INSERT policy: it would let a reviewer forge rows (wrong
`actor`, spoofed `actor_email`) and gut tamper-evidence. If a reviewer-JWT audit insert
is ever needed, constrain to `WITH CHECK (actor = (select auth.uid()))` — never another
actor, never NULL/system. `service_role` already covers poll loop, surcharge cron, and
reviewer-triggered backend writes, so the "must not silently fail" carry-forward holds
without a client policy.

**users self-promotion is blocked.** `users` SELECT is self-or-admin; writes are
admin-only (`is_admin()` in USING + WITH CHECK). A reviewer's
`UPDATE users SET role='admin'` on their own row affects 0 rows. Verified.

**Mechanics.** Two SECURITY DEFINER helpers (`can_access_deal`, `can_access_email`;
STABLE, `search_path=''`) keep child-table policies from nesting RLS subqueries.
`auth.uid()` is wrapped `(select auth.uid())` for per-statement planner caching.
Explicit `GRANT SELECT` to `authenticated` on all 8 tables ensures a denied query
returns an RLS-filtered empty set, not a bare "permission denied" that could mask a
missing grant as a passing test (confirmed via `has_table_privilege`).

## 2026-06-10 — Phase 1: audit_log insert-only + users are deactivated, not deleted
**Users are deactivated, never hard-deleted.** `audit_log.actor` → `users(id)` with
`ON DELETE NO ACTION` (explicit, not the default spelling). A user with audit rows
therefore cannot be hard-deleted — which is intended, since the audit trail must
outlive the user. `ON DELETE` is written explicitly so nobody later "fixes" it to
CASCADE/SET NULL: both would trip `forbid_mutation` (the cascade delete / the set-null
update) and fail with a cryptic raise instead of a clean FK error. Deactivation (a
status/flag on the user, added when auth lands) is the supported path.

**`actor_email` is a denormalized snapshot.** Taken at insert time alongside the
`actor` FK so each audit row is self-describing and immutable even if the `users` row
later changes. Serves the table's core purpose: permanent who-did-what.

**`actor` nullable = system.** Poll loop and surcharge cron write audit rows with no
human actor; NULL actor = system. Preferred over a sentinel user row, which would need
a real `auth.users` entry and invite "log in as system." `entity_type` stays free text
holding canonical table names (no enum). Insert-only enforced by `forbid_mutation`
(row UPDATE/DELETE trigger + statement TRUNCATE trigger); verified all three raise.

**Carry-forward to RLS (migration 5):** `audit_log` needs an INSERT path, not just
admin-SELECT — reviewer actions, poll loop, and surcharge cron all insert. Either the
inserting role bypasses RLS (service role) or a permissive INSERT policy is added.
"Admin-only" means read, not write; inserts must not silently fail when RLS lands.

## 2026-06-10 — Phase 1: rates append-only (Model A) + quotes
**Model A — fully append-only `rates`, no `effective_to`.** A version's validity
window is implicitly `[effective_from, next_version.effective_from)`. The current rate
for a key is the greatest `effective_from <= now()`. New rates (fuel-surcharge cron,
computed fallback) are always INSERTs. Rejected retaining `effective_to` even under a
set-once rule: it would be a second, redundant supersession mechanism and the
"close out the old row" path is an UPDATE the append-only trigger forbids. Expiry-
without-successor isn't needed yet (Phase 4 computed fallback covers "no current
contracted rate"); add a tombstone row later if ever required.

**Computed quotes materialize a `rates` row.** `quotes.rate_id` is NOT NULL, including
for generated quotes: the formula fallback INSERTs a `source='computed'` row into
`rates` and the quote pins it. **Consequence baked in now:** the "current contracted
rate for a key" lookup MUST filter `source='contracted'`, so a previously-computed row
can never masquerade as contracted. `rates` holds both contracted and computed rows,
distinguished by `source`; `quotes.is_computed` stays as a denormalized convenience
mirroring the pinned rate's source.

**Deterministic tiebreaker.** Current-rate lookup orders by
`effective_from DESC, created_at DESC`; the composite `rates_lookup_idx` includes the
tiebreaker so equal `effective_from` is never a coin flip. Deliberately NO unique on
`(key, effective_from)` — multiple rows may share an `effective_from`.

**Carrier precedence = most specific, then most recent.** Prefer the carrier-specific
row (`carrier_id = X`), fall back to lane-generic (`carrier_id IS NULL`). `carrier_id`
sits in the index equality prefix so both the specific probe and the IS NULL probe are
supported. (Query logic lands in Phase 4; only the index is shaped now.)

**Append-only enforced against UPDATE/DELETE *and* TRUNCATE.** `forbid_mutation()`
(`set search_path = ''`) is attached as a row-level `BEFORE UPDATE OR DELETE` trigger
AND a statement-level `BEFORE TRUNCATE` trigger (a row trigger does not fire on
TRUNCATE). Fires for all roles incl. admin/service — stronger than RLS. Verified:
UPDATE, DELETE, and `TRUNCATE ... CASCADE` all raise; the row survives.

**`quotes` snapshots from the pinned rate.** `amount_cents` and `currency` have no
column defaults so they must be copied from the pinned rate at insert (Phase 4), not
silently defaulted. No `quotes.status` enum — deal state tracks acceptance. A nullable
`deals.accepted_quote_id` (which quote was signed against) will be added in a later
migration; not now.

## 2026-06-10 — Phase 1 data layer: deals/email/attachments schema choices
**Structured route on `deals` (not a freeform string).** `deals` carries
`origin_city`, `origin_state`, `dest_city`, `dest_state`, `equipment` instead of a
single `route` text column. Why: (1) joins cleanly to the `rates` key
`(origin, destination, equipment, …)` in Phase 4; (2) gives Phase 3 validation typed
allowlist targets (state codes, equipment enum) rather than regex-parsing a string —
serves the injection-defense invariant. All route columns are nullable (unknown until
extracted).

**`equipment` is an enum (`equipment_type`).** Consistent with the other app-internal
enums. It is the **DB-level backstop only** — canonicalization of messy input
("reefer", "refrigerated", "53' reefer" → `reefer`) happens in the Phase 3 Pydantic
validation layer *before* a value reaches the column; a non-canonical value should
never first be caught by the enum. **Tradeoff:** new equipment types require an
`ALTER TYPE ... ADD VALUE` migration. If the taxonomy ever churns, the fallback is a
reference table (`equipment_types`) with an FK instead of an enum.

**`attachments.file_type` = file format, not document role.** Values `pdf|image|other`
drive Phase 3 OCR-vs-text routing. Document role (rate confirmation / contract / order)
overlaps `email_intent` and is a classification output — derived later, not stored on
this column.

**Nullability driven by pipeline timing.** Columns populated by ingestion (Phase 2) or
extraction (Phase 3) are nullable, not NOT NULL: `email_messages.subject`/`body`
(PDF-only and subject-less orders are common and must not bounce at insert),
`deal_id`/`intent`/`confidence`; `deals.assigned_reviewer`/`carrier_id` (a new_enquiry
sits unassigned with no known carrier until the MC gate). `gmail_message_id` stays
NOT NULL + unique (idempotency); `sender`/`received_at` stay NOT NULL (present at ingest).

## 2026-06-10 — Phase 0 foundations: layout and interface seam
**Decisions:**
- **`src/` layout** (`src/freight/`), built by hatchling. Keeps the installed
  package separate from the repo root so tests run against the install, not CWD.
- **Swap-by-config seam.** Interfaces live in `freight.interfaces` (Protocols +
  Pydantic DTOs); implementations are chosen in `freight.factories` from
  `Settings.*_backend`. Call sites depend only on the Protocols. Real backends
  (`hf`/`gmail`/`qstash`) raise `NotImplementedError` naming their phase until built.
- **`Queue` has no `consume()`/`subscribe()`.** The real queue is Upstash QStash,
  which is push-based (HTTP delivery). A pull model would not map onto it, so the
  consumption side is a `Handler = Callable[[QueueMessage], Awaitable[None]]` the
  transport invokes. Documented in `interfaces/queue.py` so it isn't re-added later.
- **`LLMClient.complete` always returns `LLMResult`**, never raw text — keeps a
  consistent structured wrapper (`data`/`raw`/`confidence`) at the boundary.
- **`.env` optional in docker-compose** (`required: false`); service-to-service URLs
  are set inline so `docker compose up` works before any `.env` exists.

**Dead-ends / gotchas:**
- pydantic-mypy `init_typed = true` rejects passing a plain `str` where an enum field
  (`AppEnv`) is expected, even though Pydantic coerces it at runtime. Construct with
  the enum or omit the field; don't pass the bare string.
- Starlette's `TestClient` warns it wants `httpx2` instead of `httpx`. Harmless today;
  revisit if/when the test suite needs it silenced or the dep is pinned.

**Verification:** `uv run ruff check .`, `uv run mypy .`, `uv run pytest` (10 passed),
and `docker compose up` (postgres/redis/api healthy, `/health` → 200, worker logs
startup) all pass.

## 2026-06-10 — Toolchain: uv instead of conda + pip
**Decision:** Use `uv` as the Python toolchain for the backend, replacing the
conda + `pip install -e` workflow originally written in `CLAUDE.md`. Canonical
commands are now `uv sync`, `uv add <pkg>` / `uv add --dev <pkg>`, and
`uv run <cmd>` (e.g. `uv run pytest`, `uv run ruff check .`, `uv run mypy .`).
`CLAUDE.md` Commands section updated to match.
**Why:** Single, fast resolver/locker; reproducible env via `uv.lock`; no separate
conda activation step. Chosen explicitly during Phase 0 setup.
**Trade-off:** Diverges from the original conda assumption; anyone cloning needs
`uv` installed. Frontend tooling (npm) is unchanged.
