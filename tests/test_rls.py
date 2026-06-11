"""RLS regression guard (hermetic, integration).

Creates its own reviewers A/B (+ an admin C) and a deal each inside a single
rolled-back transaction, then asserts cross-reviewer isolation and the
escalation/forgery denials. Deliberately does NOT read seed deals: fresh fixtures
with fixed UUIDs distinct from any seeded user keep A's unscoped "sees exactly 1"
true regardless of demo data.

Skips when the local supabase stack is not reachable; CI (Phase 8) opts in.
"""

import json
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import psycopg
else:
    psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# Fixed UUIDs, distinct from seed users (a1.., a2.., a3..) and seed deals (d1.., d2..).
A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
ADMIN = "33333333-3333-3333-3333-333333333333"
A_DEAL = "aaaa1111-0000-0000-0000-000000000001"
B_DEAL = "bbbb2222-0000-0000-0000-000000000002"
NULL_EMAIL = "cccc3333-0000-0000-0000-000000000003"


def _claims(sub: str) -> str:
    return json.dumps({"sub": sub})


def _scalar(cur: "psycopg.Cursor") -> Any:
    """Return the first column of the single result row (assert it exists)."""
    row = cur.fetchone()
    assert row is not None
    return row[0]


@pytest.fixture
def conn() -> Iterator["psycopg.Connection"]:
    dsn = os.environ.get("RLS_TEST_DSN", DEFAULT_DSN)
    try:
        connection = psycopg.connect(dsn, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"local supabase db not reachable: {exc}")
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _become(cur: "psycopg.Cursor", sub: str) -> None:
    """Switch to the authenticated role acting as user ``sub``."""
    cur.execute("reset role")
    cur.execute("set local role authenticated")
    cur.execute("select set_config('request.jwt.claims', %s, true)", (_claims(sub),))


def test_rls_isolation_and_escalation(conn: "psycopg.Connection") -> None:
    cur = conn.cursor()

    # --- setup as superuser (bypasses RLS) ---
    cur.execute(
        "insert into auth.users (id, email) values (%s,%s),(%s,%s),(%s,%s)",
        (A, "rls-a@test", B, "rls-b@test", ADMIN, "rls-admin@test"),
    )
    cur.execute(
        "insert into public.users (id, email, role) values"
        " (%s,%s,'reviewer'),(%s,%s,'reviewer'),(%s,%s,'admin')",
        (A, "rls-a@test", B, "rls-b@test", ADMIN, "rls-admin@test"),
    )
    cur.execute(
        "insert into public.deals (id, state, assigned_reviewer) values"
        " (%s,'new_enquiry',%s),(%s,'new_enquiry',%s)",
        (A_DEAL, A, B_DEAL, B),
    )
    # An email with NULL deal_id must be visible to admin only.
    cur.execute(
        "insert into public.email_messages"
        " (id, gmail_message_id, sender, received_at) values (%s,%s,%s, now())",
        (NULL_EMAIL, "rls-null-msg", "broker@example.com"),
    )
    # A contracted rate for the quote-forgery attempt to reference.
    cur.execute(
        "insert into public.rates"
        " (origin_city,origin_state,dest_city,dest_state,equipment,source,amount_cents)"
        " values ('Chicago','IL','Dallas','TX','dry_van','contracted',125000)"
        " returning id"
    )
    rate_id = _scalar(cur)

    # ===================== reviewer A =====================
    _become(cur, A)

    # [done-when] zero leakage: unscoped count is exactly A's one deal.
    cur.execute("select count(*) from public.deals")
    assert _scalar(cur) == 1
    cur.execute("select id from public.deals")
    assert str(_scalar(cur)) == A_DEAL

    # A cannot UPDATE B's deal (deals are server-side-write-only: 0 rows).
    cur.execute("update public.deals set state='rejected' where id=%s", (B_DEAL,))
    assert cur.rowcount == 0

    # A cannot see a NULL-deal_id email (NULL => admin only).
    cur.execute("select count(*) from public.email_messages where id=%s", (NULL_EMAIL,))
    assert _scalar(cur) == 0

    # A cannot forge a quote (server-side-write-only).
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        conn.transaction(),
    ):
        cur.execute(
            "insert into public.quotes"
            " (deal_id, rate_id, amount_cents, currency) values (%s,%s,%s,'USD')",
            (A_DEAL, rate_id, 125000),
        )

    # A cannot forge an audit row.
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        conn.transaction(),
    ):
        cur.execute(
            "insert into public.audit_log (actor, action, entity_type)"
            " values (%s,'deal.tamper','deals')",
            (A,),
        )

    # A cannot self-promote (no UPDATE policy grants it: 0 rows).
    cur.execute("update public.users set role='admin' where id=%s", (A,))
    assert cur.rowcount == 0
    cur.execute("select count(*) from public.audit_log")
    assert _scalar(cur) == 0  # admin-only read

    # ===================== admin =====================
    _become(cur, ADMIN)

    # Scoped to the two created deals so seed/demo data can't perturb the count.
    cur.execute(
        "select count(*) from public.deals where id in (%s,%s)", (A_DEAL, B_DEAL)
    )
    assert _scalar(cur) == 2

    # Admin can see the NULL-deal_id email.
    cur.execute("select count(*) from public.email_messages where id=%s", (NULL_EMAIL,))
    assert _scalar(cur) == 1

    cur.execute("reset role")
    # fixture rolls back — no rows persist.
