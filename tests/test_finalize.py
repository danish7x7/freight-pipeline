"""Atomic finalize: deal/quote creation, MC gate, process-once (integration)."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, make_engine
from freight.deals import finalize, rate_key_from
from freight.extraction import ExtractionOutcome
from freight.interfaces.types import InboundMessage

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
PREFIX = "test-finalize-"

_CHI_DAL_FIELDS = {
    "origin_city": "Chicago",
    "origin_state": "IL",
    "dest_city": "Dallas",
    "dest_state": "TX",
    "equipment": "dry_van",
    "weight_lbs": 42000,
}


@pytest.fixture
def env() -> Iterator[tuple[Engine, IngestRepository, list[str]]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    deal_ids: list[str] = []
    try:
        yield engine, IngestRepository(engine), deal_ids
    finally:
        with engine.begin() as conn:
            if deal_ids:
                conn.execute(
                    text("delete from quotes where deal_id = any(:ids)"),
                    {"ids": deal_ids},
                )
            conn.execute(
                text("delete from email_messages where gmail_message_id like :p"),
                {"p": f"{PREFIX}%"},
            )
            if deal_ids:
                conn.execute(
                    text("delete from deals where id = any(:ids)"), {"ids": deal_ids}
                )
        engine.dispose()


def _queued(repo: IngestRepository, gmail_id: str) -> None:
    repo.claim_insert(
        InboundMessage(
            gmail_message_id=gmail_id,
            thread_id="t",
            sender="broker@example.com",
            subject="Rate request",
            body="body",
            received_at=datetime.now(UTC),
        )
    )
    repo.set_ingest_status(gmail_id, "queued")


def _outcome(intent: str, extracted: dict[str, object] | None) -> ExtractionOutcome:
    return ExtractionOutcome(
        status="processed" if intent != "review" else "needs_review",
        intent=intent if intent != "review" else None,  # type: ignore[arg-type]
        confidence=0.9,
        extracted=extracted,
        review_reason="validation_failed" if intent == "review" else None,
    )


def test_processed_rate_request_creates_quoted_deal(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    contracted = repo.current_contracted_rate(rate_key_from(_CHI_DAL_FIELDS))

    with engine.begin() as conn:
        result = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("rate_request", dict(_CHI_DAL_FIELDS)),
            contracted_rate=contracted,
        )
    if result.deal_id:
        deal_ids.append(result.deal_id)

    assert result.won is True
    assert result.deal_state == "quoted"
    assert result.quote_id is not None
    # the quote pins the contracted rate
    assert contracted is not None
    with engine.connect() as conn:
        rate_id = conn.execute(
            text("select rate_id from quotes where id = :id"), {"id": result.quote_id}
        ).scalar_one()
    assert str(rate_id) == contracted.id


def test_blocked_mc_goes_on_hold_without_quote(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    fields = {**_CHI_DAL_FIELDS, "mc_number": "MC999999"}  # seeded blocked carrier

    with engine.begin() as conn:
        result = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("rate_request", fields), contracted_rate=None,
        )
    if result.deal_id:
        deal_ids.append(result.deal_id)

    assert result.deal_state == "on_hold"
    assert result.quote_id is None
    with engine.connect() as conn:
        quote_count = conn.execute(
            text("select count(*) from quotes where deal_id = :d"),
            {"d": result.deal_id},
        ).scalar_one()
    assert quote_count == 0


def test_redelivery_is_a_noop(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, deal_ids = env
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    contracted = repo.current_contracted_rate(rate_key_from(_CHI_DAL_FIELDS))

    with engine.begin() as conn:
        first = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("rate_request", dict(_CHI_DAL_FIELDS)),
            contracted_rate=contracted,
        )
    if first.deal_id:
        deal_ids.append(first.deal_id)
    with engine.begin() as conn:
        second = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("rate_request", dict(_CHI_DAL_FIELDS)),
            contracted_rate=contracted,
        )

    assert first.won is True
    assert second.won is False  # row no longer 'queued'
    assert second.deal_id is None
    with engine.connect() as conn:
        deals = conn.execute(
            text("select count(*) from deals where id = :id"), {"id": first.deal_id}
        ).scalar_one()
    assert deals == 1  # exactly one deal, no duplicate


def test_needs_review_outcome_creates_no_deal(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, _ = env
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    with engine.begin() as conn:
        result = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("review", None), contracted_rate=None,
        )
    assert result.deal_id is None
    record = repo.get_by_gmail_id(gid)
    assert record is not None
    assert record.ingest_status == "needs_review"


def test_non_rate_request_routes_to_review_without_deal(
    env: tuple[Engine, IngestRepository, list[str]],
) -> None:
    engine, repo, _ = env
    gid = f"{PREFIX}{uuid.uuid4()}"
    _queued(repo, gid)
    with engine.begin() as conn:
        result = finalize(
            conn, repo, gmail_message_id=gid,
            outcome=_outcome("negotiation", {"counter_offer_usd": 1150}),
            contracted_rate=None,
        )
    assert result.deal_id is None
    record = repo.get_by_gmail_id(gid)
    assert record is not None
    assert record.ingest_status == "needs_review"
    with engine.connect() as conn:
        reason = conn.execute(
            text("select review_reason from email_messages where gmail_message_id=:g"),
            {"g": gid},
        ).scalar_one()
    assert reason == "intent_not_yet_routable"
