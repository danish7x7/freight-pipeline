-- Phase 9-prereq / migration 12: pricing_components — the route-aware engine's
-- effective-dated, append-only pricing inputs (per-mile cost, margin, fuel surcharge,
-- deadhead, drayage base, accessorial fees).
--
-- WHY a new table, not overloaded onto lane-keyed `rates`: these inputs are policy/
-- equipment-scoped, NOT lane-scoped (a per-mile cost or a margin is not "a rate for
-- Chicago->Dallas"). `rates` stays the lane all-in contracted/computed table.
--
-- Append-only + server-side-write-only, mirroring rates/migration #10: the forbid_
-- mutation trigger blocks UPDATE/DELETE/TRUNCATE for ALL roles, and INSERT/UPDATE/DELETE
-- are revoked from anon/authenticated (the FastAPI service writes via service_role,
-- which bypasses RLS). Changing any amount later = INSERT a new effective-dated version;
-- a quote pins the version effective at quote time. NEVER an UPDATE.
--
-- USES the 'container' enum value added in migration 11 (drayage_base row) — safe here
-- because that ADD VALUE committed in its own prior migration/transaction.

create type public.pricing_component_type as enum (
    'per_mile_cost', 'margin', 'fuel_surcharge', 'deadhead', 'drayage_base', 'accessorial'
);

-- Closed allowlist of accessorial TYPES. The LLM flags which apply; the amount is never
-- model-proposed — it comes from the current effective-dated row of that type.
create type public.accessorial_type as enum (
    'detention', 'liftgate', 'appointment', 'chassis'
);

create table public.pricing_components (
    id uuid primary key default gen_random_uuid(),
    component_type public.pricing_component_type not null,
    -- per_mile_cost / drayage_base are equipment-scoped; margin/fuel_surcharge/deadhead
    -- are policy-wide (equipment NULL); accessorial is keyed by accessorial_type.
    equipment public.equipment_type,
    accessorial_type public.accessorial_type,
    -- Flats use value_cents (per_mile_cost = cents per mile, drayage_base/accessorial =
    -- flat cents); rate-of fields use value_bps (basis points): margin, fuel_surcharge,
    -- deadhead. Exactly one is set.
    value_cents bigint check (value_cents is null or value_cents >= 0),
    value_bps integer check (value_bps is null or value_bps >= 0),
    effective_from timestamptz not null default now(),
    -- Nullable: migration/system seeds + the (future) cron have no human author.
    created_by uuid references public.users (id),
    created_at timestamptz not null default now(),
    constraint pricing_components_one_value
        check ((value_cents is not null) <> (value_bps is not null)),
    constraint pricing_components_accessorial_type
        check ((component_type = 'accessorial') = (accessorial_type is not null))
);

-- Current-version lookup: equality on (type, equipment, accessorial_type) then newest
-- effective_from <= now(), created_at as the deterministic tiebreaker.
create index pricing_components_lookup_idx on public.pricing_components (
    component_type, equipment, accessorial_type, effective_from desc, created_at desc
);

create trigger pricing_components_forbid_mutation
before update or delete on public.pricing_components
for each row execute function public.forbid_mutation();

create trigger pricing_components_forbid_truncate
before truncate on public.pricing_components
for each statement execute function public.forbid_mutation();

-- Server-side-write-only (defense in depth: grant layer AND RLS), mirroring migration #10.
alter table public.pricing_components enable row level security;
grant select on public.pricing_components to authenticated;
revoke insert, update, delete on public.pricing_components from anon, authenticated;

-- Pricing inputs are reference data: read-all (like rates/carriers). private.* because
-- migration #10 relocated the RLS helpers out of the API-exposed public schema.
create policy pricing_components_select_all on public.pricing_components
for select to authenticated
using (true);

-- ---------------------------------------------------------------------------
-- Initial effective-dated versions (v1). created_by NULL: public.users is seeded
-- AFTER migrations, so no author row exists yet. effective_from defaults to now().
-- ---------------------------------------------------------------------------
insert into public.pricing_components
    (component_type, equipment, accessorial_type, value_cents, value_bps) values
    -- per-mile cost (cents/mile) by equipment (per-mile models only)
    ('per_mile_cost', 'dry_van',   null, 180, null),
    ('per_mile_cost', 'reefer',    null, 230, null),
    ('per_mile_cost', 'flatbed',   null, 210, null),
    ('per_mile_cost', 'step_deck', null, 240, null),
    ('per_mile_cost', 'power_only',null, 130, null),
    -- policy-wide rate-of components (basis points)
    ('margin',        null, null, null, 1500),  -- 15% margin on subtotal
    ('fuel_surcharge',null, null, null, 2000),  -- 20% FSC on subtotal (separate line)
    ('deadhead',      null, null, null, 1200),  -- 12% deadhead miles uplift (route-sensitive)
    -- drayage flat base (container only)
    ('drayage_base',  'container', null, 45000, null),  -- $450 flat
    -- accessorial flat fees (closed allowlist of types)
    ('accessorial', null, 'detention',   7500, null),  -- $75
    ('accessorial', null, 'liftgate',    5000, null),  -- $50
    ('accessorial', null, 'appointment', 3000, null),  -- $30
    ('accessorial', null, 'chassis',     4000, null);  -- $40
