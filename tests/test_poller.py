"""Poller integration tests (vs local Supabase + mock Gmail/queue).

Covers the three done-whens plus the two structural guarantees:
- exactly-once on first poll,
- re-poll enqueues zero (front-door dedupe) AND leaves the sweep empty,
- the sweep recovers a stuck 'received' row,
- the sweep runs even when list_messages() fails.

Skips when the local DB is unreachable. Uses a dedicated Redis db (flushed in
teardown) so SET NX keys don't leak across runs.
"""

import contextlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from freight.cache import RedisCache
from freight.db import IngestRepository, make_engine
from freight.ingestion import Poller
from freight.interfaces.types import InboundMessage, OutboundMessage
from freight.mocks.gmail import MockGmailClient
from freight.mocks.queue import InMemoryQueue
from freight.synthetic import generate_dataset

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
DEFAULT_REDIS = "redis://localhost:6379/15"
_CLEANUP = (
    "delete from email_messages "
    "where gmail_message_id like :a or gmail_message_id like :b"
)


@pytest.fixture
def deps() -> Iterator[tuple[IngestRepository, RedisCache]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    cache = RedisCache.from_url(os.environ.get("POLLER_TEST_REDIS", DEFAULT_REDIS))
    try:
        yield IngestRepository(engine), cache
    finally:
        with engine.begin() as conn:
            conn.execute(text(_CLEANUP), {"a": "synthetic-%", "b": "test-sweep-%"})
        engine.dispose()
        with contextlib.suppress(RedisError):
            cache._client.flushdb()


def _corpus_inbox() -> list[InboundMessage]:
    return [sample.message for sample in generate_dataset()]


async def test_first_poll_enqueues_each_once_and_sweep_empty(
    deps: tuple[IngestRepository, RedisCache],
) -> None:
    repo, cache = deps
    inbox = _corpus_inbox()
    queue = InMemoryQueue()
    poller = Poller(
        gmail=MockGmailClient(inbox=inbox), queue=queue, repo=repo, cache=cache
    )

    result = await poller.poll()

    expected = {m.gmail_message_id for m in inbox}
    assert result.enqueued == len(inbox)
    assert result.recovered == 0  # clean poll leaves nothing stuck
    published = [m.id for m in queue.published]
    assert set(published) == expected
    assert len(published) == len(expected)  # exactly once, no dupes
    for gmail_id in expected:
        record = repo.get_by_gmail_id(gmail_id)
        assert record is not None
        assert record.ingest_status == "queued"


async def test_repoll_enqueues_zero_and_sweep_empty(
    deps: tuple[IngestRepository, RedisCache],
) -> None:
    repo, cache = deps
    inbox = _corpus_inbox()
    gmail = MockGmailClient(inbox=inbox)
    await Poller(gmail=gmail, queue=InMemoryQueue(), repo=repo, cache=cache).poll()

    queue2 = InMemoryQueue()
    result = await Poller(gmail=gmail, queue=queue2, repo=repo, cache=cache).poll()

    assert result.enqueued == 0  # front-door dedupe
    assert result.recovered == 0  # and nothing for the sweep
    assert queue2.published == []


async def test_sweep_recovers_stuck_received_row(
    deps: tuple[IngestRepository, RedisCache],
) -> None:
    repo, cache = deps
    stuck_id = "test-sweep-0001"
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=stuck_id,
            thread_id="t",
            sender="x@y.z",
            subject="s",
            body="b",
            received_at=datetime.now(UTC),
        )
    )
    queue = InMemoryQueue()
    # Empty inbox this cycle; negative threshold so the just-claimed row qualifies.
    poller = Poller(
        gmail=MockGmailClient(inbox=[]),
        queue=queue,
        repo=repo,
        cache=cache,
        sweep_threshold=timedelta(seconds=-1),
    )

    result = await poller.poll()

    assert result.enqueued == 0
    assert stuck_id in [m.id for m in queue.published]
    record = repo.get_by_gmail_id(stuck_id)
    assert record is not None
    assert record.ingest_status == "queued"


class _FailingGmail:
    """GmailClient whose list_messages always fails (simulated outage)."""

    def list_messages(self) -> list[InboundMessage]:
        raise RuntimeError("gmail down")

    def get_message(self, message_id: str) -> InboundMessage:
        raise NotImplementedError

    def get_rfc_message_id(self, message_id: str) -> str | None:
        raise NotImplementedError

    def send(self, message: OutboundMessage) -> str:
        raise NotImplementedError


async def test_sweep_runs_when_list_messages_fails(
    deps: tuple[IngestRepository, RedisCache],
) -> None:
    repo, cache = deps
    stuck_id = "test-sweep-0002"
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=stuck_id,
            thread_id="t",
            sender="x@y.z",
            subject="s",
            body="b",
            received_at=datetime.now(UTC),
        )
    )
    queue = InMemoryQueue()
    poller = Poller(
        gmail=_FailingGmail(),
        queue=queue,
        repo=repo,
        cache=cache,
        sweep_threshold=timedelta(seconds=-1),
    )

    result = await poller.poll()

    assert result.enqueued == 0  # front door failed
    assert stuck_id in [m.id for m in queue.published]  # sweep still recovered it
