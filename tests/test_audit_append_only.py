"""audit_log is insert-only — integration guard against the real write path.

`forbid_mutation()` is attached to `audit_log` as a row-level BEFORE UPDATE OR
DELETE trigger AND a statement-level BEFORE TRUNCATE trigger (a row trigger does
not fire on TRUNCATE). This test proves all three mutation paths raise.

These mutations are issued on the real application write path: a direct connection
as the ``postgres`` role (the ``DATABASE_URL`` user — table owner, bypasses RLS by
ownership), exactly how the app inserts audit rows (``repository.py`` plain insert,
no ``set role``). That is deliberate: it proves the *trigger* stops the real
privileged writer, not a role RLS would have filtered anyway. ``forbid_mutation()``
has no role check, so it fires for the owner too.

TRUNCATE is a distinct assertion. If UPDATE/DELETE raise but TRUNCATE slips
through, that is a real coverage gap in the trigger (the missing statement-level
trigger) — this test would fail loudly rather than paper over it.

Skips when the local supabase stack is not reachable; CI (Phase 8) opts in.
"""

import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import psycopg
else:
    psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# Distinct from seed rows; rolled back in the fixture so nothing persists.
SEED_ID = "ad17ad17-0000-0000-0000-000000000001"


@pytest.fixture
def conn() -> Iterator["psycopg.Connection"]:
    """A rolled-back postgres-role connection seeded with one audit_log row."""
    dsn = os.environ.get("RLS_TEST_DSN", DEFAULT_DSN)
    try:
        connection = psycopg.connect(dsn, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        cur = connection.cursor()
        # Write as the real app path: the postgres connection role (owner, bypasses
        # RLS by ownership), NOT a fictional `set role service_role` — the app never
        # switches role (repository.py plain insert). The owner bypasses RLS, so the
        # only thing that can stop a mutation is the trigger.
        # NULL actor = system actor (poll loop / surcharge cron), so no users row
        # is needed to give UPDATE/DELETE a target.
        cur.execute(
            "insert into public.audit_log (id, actor, action, entity_type)"
            " values (%s, null, 'test.seed', 'deals')",
            (SEED_ID,),
        )
        yield connection
    finally:
        # Roll back the whole transaction: the seed row never persists, and any
        # in-flight aborted subtransaction is discarded. No residue.
        connection.rollback()
        connection.close()


def _assert_append_only(exc: "psycopg.errors.RaiseException", op: str) -> None:
    """The raise must be forbid_mutation's P0001 naming the forbidden op."""
    # `raise exception` with no SQLSTATE → P0001 (RaiseException), not a generic
    # privilege/constraint error — proves the trigger fired, not RLS or a FK.
    assert exc.sqlstate == "P0001"
    message = exc.diag.message_primary or str(exc)
    assert "append-only" in message
    assert op in message  # tg_op: UPDATE / DELETE / TRUNCATE


def test_update_audit_log_raises(conn: "psycopg.Connection") -> None:
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.RaiseException) as excinfo, conn.transaction():
        cur.execute(
            "update public.audit_log set action='tampered' where id=%s", (SEED_ID,)
        )
    _assert_append_only(excinfo.value, "UPDATE")


def test_delete_audit_log_raises(conn: "psycopg.Connection") -> None:
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.RaiseException) as excinfo, conn.transaction():
        cur.execute("delete from public.audit_log where id=%s", (SEED_ID,))
    _assert_append_only(excinfo.value, "DELETE")


def test_truncate_audit_log_raises(conn: "psycopg.Connection") -> None:
    # Distinct assertion: a row-level BEFORE DELETE trigger does NOT fire on
    # TRUNCATE. Only the statement-level BEFORE TRUNCATE trigger catches this.
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.RaiseException) as excinfo, conn.transaction():
        cur.execute("truncate public.audit_log")
    _assert_append_only(excinfo.value, "TRUNCATE")
