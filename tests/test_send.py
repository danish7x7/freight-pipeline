"""Human-gated send: exactly-once, duplicate→409, recovery, authz (integration)."""

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from freight.api.main import app
from freight.api.routes.review import get_review_deps
from freight.auth import Reviewer
from freight.auth.deps import require_reviewer
from freight.db import IngestRepository, RateKey, make_engine
from freight.mocks.gmail import MockGmailClient
from freight.sending import SendError, send_quote

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
REVIEWER1 = Reviewer(
    "a2222222-2222-2222-2222-222222222222", "r1@freight.local", "reviewer"
)
REVIEWER2 = Reviewer(
    "a3333333-3333-3333-3333-333333333333", "r2@freight.local", "reviewer"
)
ADMIN = Reviewer("a1111111-1111-1111-1111-111111111111", "admin@freight.local", "admin")
_CHI_DAL = RateKey("Chicago", "IL", "Dallas", "TX", "dry_van")


@pytest.fixture
def env() -> Iterator[tuple[Engine, IngestRepository, list[str], list[str]]]:
    engine = make_engine(os.environ.get("INGEST_TEST_DSN", DEFAULT_DSN))
    try:
        engine.connect().close()
    except OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    quote_ids: list[str] = []
    deal_ids: list[str] = []
    try:
        yield engine, IngestRepository(engine), quote_ids, deal_ids
    finally:
        with engine.begin() as conn:
            if quote_ids:
                conn.execute(
                    text("delete from sends where quote_id = any(:q)"), {"q": quote_ids}
                )
            conn.execute(
                text("delete from email_messages where gmail_message_id like :p"),
                {"p": "test-send-%"},
            )
            if quote_ids:
                conn.execute(
                    text("delete from quotes where id = any(:q)"), {"q": quote_ids}
                )
            if deal_ids:
                conn.execute(
                    text("delete from deals where id = any(:d)"), {"d": deal_ids}
                )
        engine.dispose()


def _setup(
    engine: Engine,
    repo: IngestRepository,
    quote_ids: list[str],
    deal_ids: list[str],
    *,
    reviewer_uid: str,
    state: str = "quoted",
) -> tuple[str, str]:
    rate = repo.current_contracted_rate(_CHI_DAL)
    assert rate is not None
    deal_id, quote_id = str(uuid.uuid4()), str(uuid.uuid4())
    gid = f"test-send-{uuid.uuid4()}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into deals (id, state, assigned_reviewer, origin_city,"
                " origin_state, dest_city, dest_state, equipment) values"
                " (:d, :s, :r, 'Chicago','IL','Dallas','TX','dry_van')"
            ),
            {"d": deal_id, "s": state, "r": reviewer_uid},
        )
        conn.execute(
            text(
                "insert into quotes (id, deal_id, rate_id, amount_cents, currency,"
                " is_computed) values (:q, :d, :rate, 125000, 'USD', false)"
            ),
            {"q": quote_id, "d": deal_id, "rate": rate.id},
        )
        conn.execute(
            text(
                "insert into email_messages (id, gmail_message_id, thread_id, deal_id,"
                " sender, subject, received_at, ingest_status) values"
                " (gen_random_uuid(), :g, :t, :d, 'broker@example.com', 'Rate request',"
                " now(), 'processed')"
            ),
            {"g": gid, "t": f"test-thread-{deal_id}", "d": deal_id},
        )
    deal_ids.append(deal_id)
    quote_ids.append(quote_id)
    return quote_id, deal_id


def test_send_once_marks_sent_and_audits(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, deal_id = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    gmail = MockGmailClient()

    result = send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="$950")

    assert len(gmail.sent) == 1
    assert result.gmail_message_id == "mock-sent-0001"
    assert gmail.sent[0].headers["X-Freight-Quote-Id"] == quote_id  # dedup marker
    with engine.connect() as conn:
        status, gid = conn.execute(
            text("select status, gmail_message_id from sends where quote_id = :q"),
            {"q": quote_id},
        ).one()
        actions = set(
            conn.execute(
                text("select action from audit_log where entity_id = :d"),
                {"d": deal_id},
            ).scalars()
        )
    assert status == "sent"
    assert gid == "mock-sent-0001"
    assert {"email.send.claimed", "email.sent"} <= actions


class _NoMsgIdGmail(MockGmailClient):
    """Fetch returns None (header absent) — threading degrades, send still completes."""

    def get_rfc_message_id(self, message_id: str) -> str | None:
        return None


class _RaisingMsgIdGmail(MockGmailClient):
    """Fetch raises — best-effort: caught → None, send must still complete."""

    def get_rfc_message_id(self, message_id: str) -> str | None:
        raise RuntimeError("gmail metadata fetch failed")


def test_send_sets_threading_fields_from_inbound(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, deal_id = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    with engine.connect() as conn:
        gid, thread_id = conn.execute(
            text(
                "select gmail_message_id, thread_id from email_messages"
                " where deal_id = :d"
            ),
            {"d": deal_id},
        ).one()
    gmail = MockGmailClient()

    send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="$950")

    sent = gmail.sent[0]
    # Recipient-side threading: In-Reply-To/References = the inbound RFC Message-ID,
    # NOT the Gmail API id (the bug we fixed).
    assert sent.in_reply_to == f"<{gid}@mail.gmail.com>"
    assert sent.in_reply_to != gid
    assert sent.thread_id == thread_id  # sender-side threading


def test_send_degrades_to_unthreaded_when_rfc_id_unavailable(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    # (a) fetch returns None → send completes, no In-Reply-To/References.
    quote_a, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    g_none = _NoMsgIdGmail()
    send_quote(repo, g_none, reviewer=REVIEWER1, quote_id=quote_a, body="x")
    assert len(g_none.sent) == 1  # send still completed
    assert g_none.sent[0].in_reply_to is None

    # (b) fetch raises → caught (best-effort) → None, send still completes.
    quote_b, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    g_raise = _RaisingMsgIdGmail()
    send_quote(repo, g_raise, reviewer=REVIEWER1, quote_id=quote_b, body="x")
    assert len(g_raise.sent) == 1  # never blocked the send
    assert g_raise.sent[0].in_reply_to is None


def test_duplicate_send_is_409_no_double_send(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    gmail = MockGmailClient()

    send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="$950")
    with pytest.raises(SendError) as exc:
        send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="$950")

    assert exc.value.status_code == 409
    assert len(gmail.sent) == 1  # no double-send


def test_claimed_but_unsent_resumes(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, deal_id = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    # Simulate a crash between claim and send: a committed 'claimed' row, no message id.
    with engine.begin() as conn:
        repo.claim_send(
            conn, quote_id=quote_id, deal_id=deal_id, to_email="broker@example.com",
            subject="Re: Rate request", body="$950", created_by=REVIEWER1.uid,
        )
    gmail = MockGmailClient()

    send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="$950")

    assert len(gmail.sent) == 1  # resumed, sent once
    with engine.connect() as conn:
        status = conn.execute(
            text("select status from sends where quote_id = :q"), {"q": quote_id}
        ).scalar_one()
    assert status == "sent"


def test_non_owner_is_403(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    gmail = MockGmailClient()
    with pytest.raises(SendError) as exc:
        send_quote(repo, gmail, reviewer=REVIEWER2, quote_id=quote_id, body="x")
    assert exc.value.status_code == 403
    assert len(gmail.sent) == 0


def test_admin_can_send_any_deal(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    gmail = MockGmailClient()
    send_quote(repo, gmail, reviewer=ADMIN, quote_id=quote_id, body="x")
    assert len(gmail.sent) == 1


def test_wrong_state_is_409(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, _ = _setup(
        engine, repo, q, d, reviewer_uid=REVIEWER1.uid, state="new_enquiry"
    )
    gmail = MockGmailClient()
    with pytest.raises(SendError) as exc:
        send_quote(repo, gmail, reviewer=REVIEWER1, quote_id=quote_id, body="x")
    assert exc.value.status_code == 409


def test_review_send_route_200(
    env: tuple[Engine, IngestRepository, list[str], list[str]],
) -> None:
    engine, repo, q, d = env
    quote_id, _ = _setup(engine, repo, q, d, reviewer_uid=REVIEWER1.uid)
    gmail = MockGmailClient()
    app.dependency_overrides[require_reviewer] = lambda: REVIEWER1
    app.dependency_overrides[get_review_deps] = lambda: (repo, gmail)
    try:
        response = TestClient(app).post(
            "/review/send", json={"quote_id": quote_id, "body": "$950"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["gmail_message_id"] == "mock-sent-0001"
