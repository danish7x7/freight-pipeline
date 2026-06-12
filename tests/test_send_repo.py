"""Send-claim + mark-sent + audit + authz reads (integration, rolled-back tx)."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.db import IngestRepository, make_engine

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
SEEDED_QUOTE = "aaaa0000-0000-0000-0000-000000000001"
SEEDED_DEAL_B = "d2222222-2222-2222-2222-222222222222"
REVIEWER2 = "a3333333-3333-3333-3333-333333333333"


@pytest.fixture
def engine_repo() -> Iterator[tuple[Engine, IngestRepository]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield engine, IngestRepository(engine)
    finally:
        engine.dispose()


def _claim_args() -> dict[str, str]:
    return {
        "quote_id": SEEDED_QUOTE,
        "deal_id": SEEDED_DEAL_B,
        "to_email": "broker@example.com",
        "subject": "re: your rate request",
        "body": "Our quote is $950.",
        "created_by": REVIEWER2,
    }


def test_claim_recover_mark_sent_and_audit(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    engine, repo = engine_repo
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            claim = repo.claim_send(conn, **_claim_args())
            assert claim.status == "claimed"
            assert claim.gmail_message_id is None

            # duplicate claim → same row, still claimed (recoverable, not sent)
            again = repo.claim_send(conn, **_claim_args())
            assert again.id == claim.id
            assert again.status == "claimed"

            repo.mark_sent(conn, send_id=claim.id, gmail_message_id="gmail-xyz")
            sent = repo.claim_send(conn, **_claim_args())
            assert sent.status == "sent"
            assert sent.gmail_message_id == "gmail-xyz"

            repo.insert_audit(
                conn,
                actor=REVIEWER2,
                actor_email="reviewer2@freight.local",
                action="email.sent",
                entity_type="deals",
                entity_id=SEEDED_DEAL_B,
                detail={"quote_id": SEEDED_QUOTE},
            )
            audited = conn.execute(
                text(
                    "select count(*) from audit_log "
                    "where actor::text = :a and action = 'email.sent'"
                ),
                {"a": REVIEWER2},
            ).scalar_one()
            assert audited == 1
        finally:
            trans.rollback()


def test_get_deal_and_quote_for_authz(
    engine_repo: tuple[Engine, IngestRepository],
) -> None:
    _, repo = engine_repo
    deal = repo.get_deal(SEEDED_DEAL_B)
    assert deal is not None
    assert deal.state == "quoted"
    assert deal.assigned_reviewer == REVIEWER2

    quote = repo.get_quote(SEEDED_QUOTE)
    assert quote is not None
    assert quote.deal_id == SEEDED_DEAL_B
    assert quote.amount_cents == 95000
