-- Phase 5 / migration 10: outbound sends (the send-claim idempotency backstop).
--
-- One send per approved quote: UNIQUE(quote_id) makes a duplicate approval fail the
-- claim INSERT (no double-send). A 'claimed' row with null gmail_message_id is a
-- crash-between-claim-and-send and is recoverable (resume the send). Server-side-write
-- only (the FastAPI backend under service_role); reviewers READ via RLS.
create type public.send_status as enum ('claimed', 'sent', 'failed');

create table public.sends (
    id uuid primary key default gen_random_uuid(),
    quote_id uuid not null unique references public.quotes (id),
    deal_id uuid not null references public.deals (id),
    to_email text not null,
    subject text not null,
    body text not null,
    status public.send_status not null default 'claimed',
    gmail_message_id text,
    created_by uuid references public.users (id),
    created_at timestamptz not null default now(),
    sent_at timestamptz
);

alter table public.sends enable row level security;

grant select on public.sends to authenticated;

create policy sends_select on public.sends
for select to authenticated
using (public.can_access_deal(deal_id));
