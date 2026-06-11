# DECISIONS.md
Append decisions and dead-ends here, newest first, with dates.

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
