-- Phase 2 / migration 6: ingest status on email_messages.
--
-- The durable "where is this message in ingestion?" marker. Needed because the Gmail
-- scopes are readonly + send (no `modify`): we cannot mark/label messages in Gmail, so
-- the DB is the only place to record that a message has been enqueued. Drives both the
-- claim (INSERT = claim) and the reconciliation sweep that re-enqueues rows stuck in
-- 'received' (a crash between the committed claim and the publish).
--
-- NOT NULL DEFAULT 'received' so the column backfills existing rows cleanly (the email
-- seeded in migration-6/seed.sql gets 'received' on db reset).

create type public.email_ingest_status as enum (
    'received',   -- claimed (row committed) but not yet published
    'queued',     -- published to the queue
    'processed',  -- consumer finished (set in Phase 3)
    'failed'      -- routed to DLQ / unrecoverable
);

alter table public.email_messages
    add column ingest_status public.email_ingest_status not null default 'received';

-- Supports the reconciliation sweep: find 'received' rows older than a threshold.
create index email_messages_ingest_status_idx
    on public.email_messages (ingest_status, created_at);
