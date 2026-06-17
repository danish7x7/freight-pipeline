-- Phase 9-prereq / migration 13: quote_components — the per-quote pinned breakdown.
--
-- A quote pins its primary rate via quotes.rate_id (the contracted row, or the
-- materialized computed all-in anchor) — UNCHANGED. quote_components additionally pins
-- EACH pricing input the computed quote applied (linehaul/per-mile, deadhead, margin,
-- fuel surcharge, drayage base, each accessorial) to the exact pricing_components
-- version effective at quote time, with the line amount it contributed. So a later
-- pricing change (a new effective-dated pricing_components row) never alters an existing
-- quote — its pinned versions and line amounts are frozen here.
--
-- Append-only (a pinned breakdown never changes) + server-side-write-only, mirroring
-- pricing_components/rates. deal_id is denormalized (like sends.deal_id) so the RLS
-- read policy is a direct can_access_deal() without a join — and so the Phase 10
-- reviewer breakdown view (deferred) can scope cleanly.

create table public.quote_components (
    id uuid primary key default gen_random_uuid(),
    quote_id uuid not null references public.quotes (id),
    deal_id uuid not null references public.deals (id),
    pricing_component_id uuid not null references public.pricing_components (id),
    -- The line's role in the breakdown: 'linehaul', 'deadhead', 'margin',
    -- 'fuel_surcharge', 'drayage_base', or 'accessorial:<type>'.
    role text not null,
    line_amount_cents bigint not null check (line_amount_cents >= 0),
    created_at timestamptz not null default now()
);

create index quote_components_quote_idx on public.quote_components (quote_id);

create trigger quote_components_forbid_mutation
before update or delete on public.quote_components
for each row execute function public.forbid_mutation();

create trigger quote_components_forbid_truncate
before truncate on public.quote_components
for each statement execute function public.forbid_mutation();

alter table public.quote_components enable row level security;
grant select on public.quote_components to authenticated;
revoke insert, update, delete on public.quote_components from anon, authenticated;

-- Accessible-deal read (like quotes). private.* per migration #10's helper relocation.
create policy quote_components_select on public.quote_components
for select to authenticated
using (private.can_access_deal(deal_id));
