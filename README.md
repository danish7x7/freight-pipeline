# freight-pipeline

A logistics order-email pipeline: ingest delivery orders and rate enquiries from
email and PDF, extract structured fields with an LLM, look up or compute a rate, and
produce a **human-reviewed** reply. Injection-aware and human-supervised by design —
the model proposes, a person disposes.

> Status: early build. See [`PLAN.md`](PLAN.md) for the phased roadmap and
> [`DECISIONS.md`](DECISIONS.md) for the decision log. The behavioral contract for
> this repo lives in [`CLAUDE.md`](CLAUDE.md).

## Quickstart (local)

Requires [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                       # create the env from pyproject/uv.lock
cp .env.example .env          # then fill in real values (never commit .env)
docker compose up -d          # postgres, redis, api, worker
uv run pytest                 # run the test suite
uv run ruff check . && uv run mypy .
```

## Stack

Python 3.12 · FastAPI · Pydantic · SQLAlchemy · Supabase (Postgres + Auth + RLS +
Storage) · Redis (Upstash) · Upstash QStash · Hugging Face serverless inference ·
Next.js + TypeScript + Tailwind + shadcn/ui (`web/`).

More docs land in Phase 10: `ARCHITECTURE.md`, `THREAT_MODEL.md`, eval numbers.
