-- Phase 3 / migration 7: extraction output on email_messages.
--
-- Phase 3 stops at "validated record + intent + confidence on the email row"; deal
-- creation is Phase 4. So process-once is a single conditional UPDATE
-- (WHERE ingest_status='queued') around the extraction write — one delivery wins.
--
-- Routing (the precise permanent-vs-transient mapping):
--   success         -> ingest_status='processed'   (consumer returns 2xx)
--   content failure -> ingest_status='needs_review' (consumer returns 2xx; NOT retried)
--   transient infra -> consumer raises -> 5xx -> QStash retries -> DLQ
-- 'needs_review' is the human sink for won't-parse / invalid / injection / no-text-layer.

alter type public.email_ingest_status add value if not exists 'needs_review';

alter table public.email_messages
    add column extracted jsonb,       -- the ValidatedExtraction (null until extracted)
    add column review_reason text;    -- why a row landed in needs_review
