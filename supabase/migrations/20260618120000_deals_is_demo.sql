-- Phase 10 demo: mark demo-seeded deals so they can never trigger a real send.
--
-- The showcase "load sample order" feature seeds deals through the SAME server-side
-- write path as real ingest, assigned to the (least-privilege, non-admin) demo reviewer
-- so RLS scopes them to that account. ``is_demo`` is the structural send-block: the
-- send service refuses any is_demo deal, so the published demo login has no path to a
-- real Gmail send — and an operator can't accidentally send a demo deal either.
--
-- Real ingested deals default to FALSE (NOT NULL default), so existing rows and the real
-- path are unchanged. No RLS change: visibility is governed by assigned_reviewer/admin as
-- before; this flag only gates the send service.

alter table public.deals
    add column is_demo boolean not null default false;
