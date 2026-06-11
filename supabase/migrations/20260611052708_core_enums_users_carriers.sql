-- Phase 1 / migration 1: core enums, users, carriers, and RLS admin helpers.
-- RLS itself is enabled in a later migration; this file only defines schema and
-- the helper functions that later policies depend on.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.user_role as enum ('reviewer', 'admin');

create type public.deal_state as enum (
    'new_enquiry',
    'quoted',
    'negotiating',
    'rc_received',
    'contract_signed',
    'scheduled',
    'rejected',
    'on_hold'
);

create type public.carrier_status as enum ('active', 'blocked', 'unknown');

create type public.email_intent as enum (
    'rate_request',
    'negotiation',
    'rc',
    'contract',
    'other'
);

create type public.rate_source as enum ('contracted', 'computed');

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

-- App users mirror auth.users 1:1 and carry the application role.
create table public.users (
    id uuid primary key references auth.users (id) on delete cascade,
    email text not null unique,
    role public.user_role not null default 'reviewer',
    created_at timestamptz not null default now()
);

create table public.carriers (
    id uuid primary key default gen_random_uuid(),
    mc_number text not null unique,
    name text not null,
    status public.carrier_status not null default 'unknown',
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- RLS admin helpers
--
-- SECURITY DEFINER so policies can read public.users without recursing through
-- that table's own RLS. STABLE because the result is fixed within a statement.
-- `set search_path = ''` plus fully schema-qualified references closes the
-- search-path escalation footgun and clears the "Function Search Path Mutable"
-- linter warning.
-- ---------------------------------------------------------------------------
create function public.current_user_role()
returns public.user_role
language sql
stable
security definer
set search_path = ''
as $$
    select u.role
    from public.users u
    where u.id = (select auth.uid());
$$;

create function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(public.current_user_role() = 'admin', false);
$$;
