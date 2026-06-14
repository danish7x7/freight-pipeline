# CLAUDE.md

Behavioral contract for this repo. Every rule here is binding. If a rule conflicts
with a request, surface the conflict instead of silently breaking the rule.

## What this is
A logistics order-email pipeline: ingest delivery orders and rate enquiries from
email and PDF, extract structured fields with an LLM, look up or compute a rate, and
produce a human-reviewed reply. Multi-step deals with negotiation, carrier
onboarding, and scheduling.

## Stack
- Backend: Python 3.12, FastAPI, Pydantic, SQLAlchemy.
- Data: Supabase (Postgres + Auth + RLS + Storage). Redis (Upstash) for cache,
  idempotency, rate limiting. Queue: Upstash QStash.
- LLM: Hugging Face serverless inference, behind an `LLMClient` interface.
- Frontend: Next.js + TypeScript + Tailwind + shadcn/ui (in `web/`).
- Schema + RLS are owned by `supabase/migrations/` SQL files — the single source of
  truth. The app reads the DB; it does not own migrations.

## Commands
- Install / sync env: `uv sync` ; frontend `cd web && npm install`
- Add a dependency: `uv add <pkg>` (dev: `uv add --dev <pkg>`)
- Run anything in the env: `uv run <cmd>`
- Local services: `docker compose up -d`
- New migration: `supabase migration new <name>` ; apply: `supabase db push`
- Test: `uv run pytest` ; lint/type: `uv run ruff check . && uv run mypy .`
- Frontend dev: `cd web && npm run dev`
Run lint, type-check, and tests before considering any task done. Read each gate's
output directly and check its exit code per command — never pipe lint/type-check to
`>/dev/null` and infer success from a later step (a masked failure then ships).

## Architecture invariants (do not violate)
- Build against interfaces: `LLMClient`, `GmailClient`, and the queue. Swap
  implementations by config, never by rewriting call sites.
- `rates` is append-only and effective-dated. Never UPDATE or overwrite a rate row;
  insert a new version. A `quote` pins the exact `rate_id` it used.
- Idempotency: every inbound email is keyed on `gmail_message_id` (Redis SET NX plus
  a DB unique constraint). Never process or send twice for the same id.
- RLS is on every table. Reviewers see only their assigned deals; admins see all.
  `audit_log` is insert-only — no UPDATE, no DELETE.
- The LLM never triggers a send. A human approves every outbound message. The model
  proposes; a person disposes.
- All extracted fields are untrusted input. Validate every field (format, ranges,
  allowlists) before it reaches the rate engine. This is the injection defense.
- Enforce the deal state machine: `new_enquiry → quoted → negotiating ⇄ quoted →
  rc_received → contract_signed → scheduled`, plus `rejected`/`on_hold`. No skipping.
- Carrier eligibility gate runs before `quoted` and `contract_signed`. Unknown or
  blocked MC number → park the deal in `on_hold` for a human.
- PDFs (RCs, contracts) go through the same extraction + validation path as email.
- No secrets in code or git. Env vars and secret managers only.

## Workflow rules for you (the agent)
- Read `PLAN.md` and `DECISIONS.md` at the start of every session.
- Work the current phase in `PLAN.md` top to bottom. Do not jump ahead. Build the
  spine (phases 0–5) before any hardening.
- One task at a time. State your plan for a task before writing code; wait for go.
- When a task is done: run lint/types/tests, then check it off in `PLAN.md`.
- Record every non-obvious decision and every dead-end in `DECISIONS.md` with the
  date, so it is not re-litigated later.

## Code conventions
- Type hints everywhere; Pydantic models for all external/boundary data.
- No business logic in FastAPI route handlers — keep it in service modules.
- A test accompanies every new module. Small, single-purpose functions.
- Conventional commit messages.

## Do not
- Do not add Kubernetes, a service mesh, multi-region, or a self-hosted load
  balancer. This system is right-sized for low volume; over-engineering is a defect.
- Do not let any LLM output directly trigger an irreversible action.
- Do not overwrite rate rows, weaken RLS, or commit secrets to satisfy a shortcut.
