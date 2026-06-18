#!/usr/bin/env bash
# Phase 9 pipeline load test. Starts uvicorn with the MOCK LLM + a local signing key,
# runs locust headless against the REAL fail-closed /ingest gate, then stops uvicorn.
# The signing key here is a LOCAL TEST key (not a secret) — it only has to match between
# the app's verifier and the locust signer so the real gate accepts the load envelopes.
set -euo pipefail

export LLM_BACKEND=mock
export GMAIL_BACKEND=mock
export QUEUE_BACKEND=memory
export RATE_LIMIT_ENABLED=false   # measure the pipeline, not the 6.4 limiter
export DATABASE_URL="${INGEST_TEST_DSN:-postgresql://postgres:postgres@127.0.0.1:54322/postgres}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export QSTASH_CURRENT_SIGNING_KEY="${QSTASH_CURRENT_SIGNING_KEY:-load-test-current-key}"
export QSTASH_NEXT_SIGNING_KEY="${QSTASH_NEXT_SIGNING_KEY:-load-test-next-key}"
export QSTASH_EXPECTED_URL=""      # sub claim present but not matched (load convenience)
export LOAD_INGEST_URL="http://127.0.0.1:8000/ingest"
export LOAD_POOL_SIZE="${LOAD_POOL_SIZE:-20000}"

USERS="${USERS:-50}"
RUN_TIME="${RUN_TIME:-30s}"

uv run uvicorn freight.api.main:app --host 127.0.0.1 --port 8000 --log-level warning &
UVICORN_PID=$!
trap 'kill "${UVICORN_PID}" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf http://127.0.0.1:8000/health >/dev/null && break
  sleep 0.5
done

uv run locust -f scripts/locustfile.py --headless \
  -u "${USERS}" -r "${USERS}" -t "${RUN_TIME}" \
  -H http://127.0.0.1:8000 --only-summary
