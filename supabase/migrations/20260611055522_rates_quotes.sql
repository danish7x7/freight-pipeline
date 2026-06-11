-- Phase 1 / migration 3: rates (append-only, effective-dated) + quotes.
--
-- rates is fully append-only (Model A): no effective_to. A version's validity
-- window is implicitly [effective_from, next_version.effective_from). The current
-- rate for a key is the greatest effective_from <= now(), with a deterministic
-- tiebreaker. New rates (incl. the Phase 4 fuel-surcharge cron and the computed
-- fallback) are always INSERTs — never UPDATEs. Enforced by forbid_mutation().

-- ---------------------------------------------------------------------------
-- Append-only enforcement
--
-- Fires for ALL roles (incl. admin/service) — stronger than RLS. A row-level
-- trigger does NOT fire on TRUNCATE, so a second statement-level BEFORE TRUNCATE
-- trigger is attached as well. Reused on audit_log in the next migration.
-- ---------------------------------------------------------------------------
create function public.forbid_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    raise exception '%.% is append-only; % is not allowed',
        tg_table_schema, tg_table_name, tg_op;
end;
$$;

-- ---------------------------------------------------------------------------
-- rates
-- ---------------------------------------------------------------------------
create table public.rates (
    id uuid primary key default gen_random_uuid(),
    origin_city text not null,
    origin_state text not null,
    dest_city text not null,
    dest_state text not null,
    equipment public.equipment_type not null,
    -- Nullable: NULL = lane/market rate (not carrier-specific). A carrier-specific
    -- row wins over the lane-generic row for the same key (resolved in Phase 4).
    carrier_id uuid references public.carriers (id),
    -- contracted | computed. Both live here; the "current contracted rate" lookup
    -- MUST filter source = 'contracted' so a materialized computed row can never
    -- masquerade as a contracted rate.
    source public.rate_source not null,
    amount_cents bigint not null check (amount_cents >= 0),
    currency text not null default 'USD',
    effective_from timestamptz not null default now(),
    -- Nullable: system/cron writes (fuel-surcharge job) have no human author.
    created_by uuid references public.users (id),
    created_at timestamptz not null default now()
);

-- Lookup index: equality on the lane key + equipment + carrier_id + source, then
-- ordered by effective_from DESC, created_at DESC. Supports both the
-- carrier-specific probe (carrier_id = X) and the lane-generic probe
-- (carrier_id IS NULL), and makes equal-effective_from ties deterministic.
-- Deliberately NOT unique on (key, effective_from): multiple rows may share an
-- effective_from; the tiebreaker resolves them.
create index rates_lookup_idx on public.rates (
    origin_state,
    origin_city,
    dest_state,
    dest_city,
    equipment,
    carrier_id,
    source,
    effective_from desc,
    created_at desc
);

create trigger rates_forbid_mutation
before update or delete on public.rates
for each row execute function public.forbid_mutation();

create trigger rates_forbid_truncate
before truncate on public.rates
for each statement execute function public.forbid_mutation();

-- ---------------------------------------------------------------------------
-- quotes
-- ---------------------------------------------------------------------------
create table public.quotes (
    id uuid primary key default gen_random_uuid(),
    deal_id uuid not null references public.deals (id),
    -- Pins the exact rate version used. The computed fallback materializes a
    -- source='computed' rates row first, then the quote pins it — so rate_id is
    -- always NOT NULL, including for generated quotes.
    rate_id uuid not null references public.rates (id),
    -- amount_cents and currency are snapshotted from the pinned rate at insert
    -- time (Phase 4), NOT from independent defaults — hence no default here.
    amount_cents bigint not null check (amount_cents >= 0),
    currency text not null,
    -- Denormalized convenience mirroring the pinned rate's source = 'computed'.
    is_computed boolean not null default false,
    created_by uuid references public.users (id),
    created_at timestamptz not null default now()
);
