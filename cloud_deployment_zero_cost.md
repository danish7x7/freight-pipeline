# cloud_deployment_zero_cost.md

Zero-cost cloud deploy runbook for the freight order-email pipeline (Phase 8). This is the
companion deploy doc referenced by `PLAN.md`. It reflects the host corrections in
`DECISIONS.md` 2026-06-15. Architecture invariants live in `CLAUDE.md`; recovery procedures
in `RECOVERY.md`; residual risks in `THREAT_MODEL.md`.

> **Host correction (2026-06-15).** Backend is **Render free tier** (Fly.io & Railway both
> dropped their free tiers in 2026), deployed from the retained `Dockerfile`. **DB stays
> Supabase, queue/cache stays Upstash; do NOT create a Render Postgres** (Render's free
> Postgres is deleted after 30 days). Supabase issued **legacy** anon/service_role keys, so
> the Phase 5 JWKS/ES256 auth needs no change.

---

## 1. Topology — what runs where (all free tier)

| Component | Host | Notes |
|-----------|------|-------|
| FastAPI backend (api) | **Render** web service, Docker | The only thing Render hosts. Single service — no separate worker. |
| Postgres + Auth + RLS + Storage | **Supabase** | Schema/RLS source of truth = `supabase/migrations/`. |
| Redis (cache / idempotency / rate limit) | **Upstash** | Fail-open everywhere; backend degrades, not down, if absent. |
| Queue (push) | **Upstash QStash** | Pushes to the backend `/ingest`; DLQ on retry exhaustion. |
| LLM | **Hugging Face** Inference Providers | OpenAI-compatible chat-completions via `router.huggingface.co`. |
| Next.js review console | **Vercel** | Reads Supabase via RLS; writes go through the backend. |
| Inbox poll + fuel-surcharge crons | **GitHub Actions** | `*/5` poll curls `/poll`; surcharge curls `/jobs/surcharge`. |

**No always-on worker.** The poller runs in-process when the cron curls `POST /poll`; the
consumer runs in-process when QStash pushes `POST /ingest`. Both are routes on the one web
service. The docker-compose `worker` was local-only.

**Cold-start-on-idle is accepted.** Render free web services sleep after ~15 min idle and take
~30–60s to wake. The backend is cron/queue-driven, not user-facing, and the `*/5` poll cron
keeps it warm during active hours. QStash retries absorb a wake on `/ingest`.

---

## 2. Deploy order (maps to the Phase 8 task list)

8.1 migrations → 8.2 HF confirm → 8.3 backend deploy → 8.4 QStash target → 8.5 frontend
deploy → 8.6 CORS → 8.7 GitHub Secrets + crons → 8.8 end-to-end cloud test.

### 8.1 — Apply migrations to live Supabase + verify RLS deny-side
1. `supabase login` then `supabase link --project-ref <ref>` (interactive; needs DB password).
2. `supabase db push --dry-run` → read the pending migration list, then `supabase db push`.
3. `supabase migration list` → confirm local == remote.
4. **No seed to live** — `seed.sql` is demo/fixtures, not schema-of-truth (`db push` skips it).
5. Verify RLS deny-side against live: export the **session-mode** pooler DSN (port 5432 —
   IPv4-only; the transaction pooler can't `set local role`) as `RLS_TEST_DSN`, then
   `uv run pytest tests/test_rls.py -m integration -v`. The test inserts fixtures and rolls
   back — nothing persists.

### 8.2 — Confirm HF live API + pin the model
- HF serverless folded into **Inference Providers** (OpenAI-compatible
  `https://router.huggingface.co/v1/chat/completions`). `HF_BASE_URL` already points at the
  router. Confirm `HFLLMClient`'s endpoint/shape against the current API and confirm the pinned
  `HF_MODEL` returns a valid structured extraction on a real prompt.

### 8.3 — Deploy backend to Render
- New **Web Service** → from the repo → **Docker** runtime (uses the repo `Dockerfile`). Free
  plan. Health check path **`/health`** (liveness; `/ready` is the readiness probe).
- **Port:** the `Dockerfile` CMD binds `--port 8000`. Set Render env **`PORT=8000`** (or change
  the CMD to `--port ${PORT:-8000}`) so Render routes to the right port.
- Set all backend env vars (§3). `LLM_BACKEND=hf`, `GMAIL_BACKEND=gmail`, `QUEUE_BACKEND=qstash`.
- **Replace `UnconfiguredStorageReader`** with the Supabase Storage reader (Phase 3 carry-forward)
  so PDF attachments resolve.
- After first deploy, note the public URL (`https://<svc>.onrender.com`); it feeds 8.4/8.7.

### 8.4 — Register QStash target
- In the QStash console, point the destination at `https://<svc>.onrender.com/ingest`.
- Set `QSTASH_DESTINATION_URL` and `QSTASH_EXPECTED_URL` to that `/ingest` URL (the signed
  `sub` claim), and `QSTASH_CURRENT_SIGNING_KEY` / `QSTASH_NEXT_SIGNING_KEY` from the console.
- Confirm the live delivery header is `Upstash-Signature` and a real delivery verifies (6.1).

### 8.5 — Deploy frontend to Vercel
- Import `web/` to Vercel. Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  (legacy anon key), `NEXT_PUBLIC_API_BASE_URL=https://<svc>.onrender.com`.
- Note the Vercel origin (`https://<app>.vercel.app`) for 8.6.

### 8.6 — CORS
- Set backend `CORS_ALLOW_ORIGINS=https://<app>.vercel.app` on Render (6.3). The factory swaps
  it with no code change; until set, only `localhost:3000` is allowed (fail-closed-ish).

### 8.7 — GitHub Secrets + crons
- `CRON_SECRET`: set on **both** GitHub repo Secrets **and** Render env — they must match (6.2).
- `POLL_ENDPOINT` / `SURCHARGE_ENDPOINT` (workflow env): the Render `/poll` and `/jobs/surcharge`
  URLs. **Remove** the old `POLL_TOKEN` / `SURCHARGE_TOKEN` repo secrets.
- Crons run only from the **default branch**; GitHub auto-disables schedules after 60 days of
  repo inactivity (2.7) — set a keepalive reminder.
- CI/CD: lint/type/test/build on push; branch protection; Vercel PR previews.

### 8.8 — End-to-end cloud test + Phase 7 deploy-half
- Drive a synthetic email through the **cloud** path: poll → `/ingest` (QStash, signature
  verified) → extract → rate → review console → human send. Confirm a reply goes out with an
  audit row, and a poisoned message lands in the QStash DLQ (replay per `RECOVERY.md` §3).
- Wire the Phase 7 deploy-half: Sentry DSN (frontend + backend), Grafana Cloud dashboard
  scraping `/metrics`, **Supabase backups ON** (restore gate, `RECOVERY.md` §5), uptime monitor
  on the live URL.
- Verify the **deployed** project's JWKS URL + issuer (Phase 5 carry-forward).

---

## 3. Env var reference — which value goes where

**Render (backend web service):**

| Var | Value | Source |
|-----|-------|--------|
| `APP_ENV` | `production` | — |
| `APP_SECRET` | `openssl rand -hex 32` | generate |
| `PORT` | `8000` | matches Dockerfile CMD |
| `CRON_SECRET` | shared secret | **must match** the GitHub Secret |
| `CORS_ALLOW_ORIGINS` | `https://<app>.vercel.app` | 8.5 output |
| `LLM_BACKEND` / `GMAIL_BACKEND` / `QUEUE_BACKEND` | `hf` / `gmail` / `qstash` | — |
| `SUPABASE_URL` | `https://<ref>.supabase.co` | Supabase |
| `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | legacy keys | Supabase |
| `DATABASE_URL` | session-pooler URL | Supabase (IPv4) |
| `REDIS_URL` | Upstash Redis URL | Upstash |
| `QSTASH_TOKEN` / `QSTASH_URL` | from console | Upstash QStash |
| `QSTASH_DESTINATION_URL` / `QSTASH_EXPECTED_URL` | `…onrender.com/ingest` | 8.3 output |
| `QSTASH_CURRENT_SIGNING_KEY` / `QSTASH_NEXT_SIGNING_KEY` | from console | Upstash QStash |
| `HF_TOKEN` / `HF_MODEL` / `HF_BASE_URL` | token, pinned model, `https://router.huggingface.co` | HF |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REDIRECT_URI` / `GMAIL_REFRESH_TOKEN` | OAuth | Google |

**Vercel (frontend):** `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`,
`NEXT_PUBLIC_API_BASE_URL` (= the Render URL).

**GitHub Secrets:** `CRON_SECRET` (matches Render); workflow env `POLL_ENDPOINT`,
`SURCHARGE_ENDPOINT` (Render URLs). Remove `POLL_TOKEN` / `SURCHARGE_TOKEN`.

> No secret values in this file or any committed file — env/secret stores only (`CLAUDE.md`).

---

## 4. Free-tier gotchas (don't get surprised)

- **Render free Postgres is deleted after 30 days** — we don't use it; DB is Supabase.
- **Render sleep ~15 min idle**, ~30–60s wake — accepted (queue/cron-driven). The `*/5` poll
  keeps it warm; QStash retries absorb a cold `/ingest`.
- **Rate limiter behind Render's proxy** sees the proxy IP, so per-client limiting is coarse
  until a trusted forwarded-IP header is wired (`THREAT_MODEL.md` R2).
- **GitHub Actions crons:** default-branch only; auto-disabled after 60 days idle; `*/5` floor
  and best-effort timing (2.7). Correctness is independent of cadence (idempotent claims + sweep).
- **Supabase connection:** IPv4-only hosts (WSL2) must use the **session-mode pooler** (5432),
  not the IPv6 direct host, and not the transaction pooler for `set role` operations.
