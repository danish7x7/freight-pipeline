"""Phase 9 load test — POST /ingest under load through the REAL signature gate.

Measures PIPELINE latency (HTTP receive → QStash signature verify → body parse →
extract via the MOCK LLM → finalize flip → DB commit) EXCLUDING HF model time. Each
request carries a UNIQUE pre-seeded ``gmail_message_id`` so ``finalize`` claims a real
'queued' row (a genuine conditional UPDATE), not an idempotency 200-dedup. Envelopes are
signed with ``sign_qstash`` and verified by the endpoint's own ``qstash.Receiver`` — the
fail-closed gate is measured, not bypassed.

Disposition: the default MockLLMClient returns ``{"intent": "rate_request"}`` with no
route, so the gate routes every message to ``needs_review`` (a single-row flip, no
deal/quote cascade) — clean to seed and tear down. The headline number is therefore
verify→parse→extract(mock)→finalize-flip→commit; the full-quote path adds a few inserts.

Run via ``scripts/run_loadtest.sh`` (starts uvicorn with the mock LLM + a signing key,
then locust headless). Env: ``QSTASH_CURRENT_SIGNING_KEY``, ``LOAD_INGEST_URL``,
``LOAD_POOL_SIZE``.
"""

import collections
import json
import os
from datetime import UTC, datetime
from typing import Any

from locust import HttpUser, between, events, task
from locust.exception import StopUser

from freight.config import get_settings
from freight.db import make_engine
from freight.db.repository import email_messages
from scripts.qstash_sign import sign_qstash

_PREFIX = "load-"
_POOL_SIZE = int(os.environ.get("LOAD_POOL_SIZE", "20000"))
_INGEST_URL = os.environ.get("LOAD_INGEST_URL", "http://localhost:8000/ingest")
_SIGNING_KEY = os.environ.get("QSTASH_CURRENT_SIGNING_KEY", "")

# Unique ids dispensed one-per-request. Locust runs on gevent greenlets (cooperative,
# single thread), so ``popleft`` between requests needs no extra lock.
_ids: collections.deque[str] = collections.deque()


@events.test_start.add_listener  # type: ignore[untyped-decorator]
def _seed(environment: Any, **_kwargs: Any) -> None:
    """Seed a pool of unique 'queued' email rows so every request claims real work."""
    engine = make_engine(get_settings().database_url)
    now = datetime.now(UTC)
    rows = [
        {
            "gmail_message_id": f"{_PREFIX}{i}",
            "thread_id": None,
            "sender": "loadtest@example.com",
            "subject": "Rate request: load",
            "body": "dry van rate please",
            "received_at": now,
            "ingest_status": "queued",
        }
        for i in range(_POOL_SIZE)
    ]
    with engine.begin() as conn:
        # Clean slate so a re-run can't trip the gmail_message_id unique constraint on
        # rows a prior run left behind (idempotent seeding).
        conn.execute(
            email_messages.delete().where(
                email_messages.c.gmail_message_id.like(f"{_PREFIX}%")
            )
        )
        for start in range(0, len(rows), 1000):
            conn.execute(email_messages.insert(), rows[start : start + 1000])
    engine.dispose()
    _ids.clear()
    _ids.extend(f"{_PREFIX}{i}" for i in range(_POOL_SIZE))
    print(f"[loadtest] seeded {_POOL_SIZE} queued rows")


@events.test_stop.add_listener  # type: ignore[untyped-decorator]
def _cleanup(environment: Any, **_kwargs: Any) -> None:
    """Delete the seeded load rows (needs_review path made no deals — single table)."""
    engine = make_engine(get_settings().database_url)
    with engine.begin() as conn:
        result = conn.execute(
            email_messages.delete().where(
                email_messages.c.gmail_message_id.like(f"{_PREFIX}%")
            )
        )
    engine.dispose()
    print(f"[loadtest] deleted {result.rowcount} seeded load rows")


class IngestUser(HttpUser):
    """One simulated QStash caller hammering the real /ingest gate."""

    wait_time = between(0, 0)

    @task
    def ingest(self) -> None:
        try:
            gid = _ids.popleft()
        except IndexError:
            raise StopUser() from None  # pool drained — every request did real work
        body = json.dumps({"id": gid, "payload": {}}).encode()
        signature = sign_qstash(body, key=_SIGNING_KEY, url=_INGEST_URL)
        with self.client.post(
            "/ingest",
            data=body,
            headers={
                "Upstash-Signature": signature,
                "Content-Type": "application/json",
            },
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}: {resp.text[:120]}")
