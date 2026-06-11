-- Phase 1 / migration 2: deals, email_messages, attachments (+ supporting enums).
-- RLS is enabled in a later migration. Many columns are intentionally nullable:
-- they are populated by ingestion (Phase 2) and extraction (Phase 3), not at insert.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

-- Equipment is an app-internal enum (DB-level backstop only). Canonicalization
-- of messy input happens in the Phase 3 Pydantic validation layer; a non-canonical
-- value should be normalized or rejected there, never first caught by this enum.
create type public.equipment_type as enum (
    'dry_van',
    'reefer',
    'flatbed',
    'step_deck',
    'power_only',
    'other'
);

-- File type drives Phase 3 OCR-vs-text routing. This is the file's format, NOT its
-- document role (rate confirmation / contract / order) — role is a classification
-- output that overlaps email_intent and is derived later, not stored here.
create type public.attachment_file_type as enum ('pdf', 'image', 'other');

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table public.deals (
    id uuid primary key default gen_random_uuid(),
    state public.deal_state not null default 'new_enquiry',
    -- Nullable: a new_enquiry can sit unassigned in the queue, and the carrier
    -- is unknown until onboarding + the MC eligibility gate (both before `quoted`).
    assigned_reviewer uuid references public.users (id),
    carrier_id uuid references public.carriers (id),
    -- Structured route (nullable until extracted) so deals join cleanly to the
    -- rates key and give Phase 3 typed allowlist targets instead of a parsed string.
    origin_city text,
    origin_state text,
    dest_city text,
    dest_state text,
    equipment public.equipment_type,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.email_messages (
    id uuid primary key default gen_random_uuid(),
    gmail_message_id text not null unique,
    thread_id text,
    deal_id uuid references public.deals (id),
    sender text not null,
    -- Nullable: subject-less and PDF-only (empty-body) orders are common in this
    -- domain and must not bounce at insert.
    subject text,
    body text,
    -- Populated by extraction (Phase 3).
    intent public.email_intent,
    confidence real,
    received_at timestamptz not null,
    created_at timestamptz not null default now()
);

create table public.attachments (
    id uuid primary key default gen_random_uuid(),
    email_message_id uuid not null references public.email_messages (id) on delete cascade,
    storage_path text not null,
    file_type public.attachment_file_type not null default 'other',
    mime_type text,
    created_at timestamptz not null default now()
);
