-- Phase 1 / migration 4: audit_log (insert-only).
--
-- Permanent, immutable who-did-what. Insert-only is enforced by the same
-- forbid_mutation() used on rates (row trigger for UPDATE/DELETE + statement
-- trigger for TRUNCATE). RLS (admin-read + an INSERT path) lands in migration 5.

create table public.audit_log (
    id uuid primary key default gen_random_uuid(),
    -- Nullable: NULL = system actor (poll loop, surcharge cron). ON DELETE is
    -- explicitly NO ACTION: a user with audit rows cannot be hard-deleted (users
    -- are deactivated, not deleted). CASCADE/SET NULL would trip forbid_mutation
    -- with a cryptic raise; NO ACTION fails cleanly at the FK level. Do not
    -- "fix" this to CASCADE.
    actor uuid references public.users (id) on delete no action,
    -- Denormalized snapshot taken at insert time so each row is self-describing
    -- and survives any later change to the users row.
    actor_email text,
    action text not null,
    -- Free text holding canonical table names (e.g. 'deals'); no enum.
    entity_type text not null,
    entity_id uuid,
    detail jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

-- "History of entity X" lookups.
create index audit_log_entity_idx
on public.audit_log (entity_type, entity_id, created_at desc);

create trigger audit_log_forbid_mutation
before update or delete on public.audit_log
for each row execute function public.forbid_mutation();

create trigger audit_log_forbid_truncate
before truncate on public.audit_log
for each statement execute function public.forbid_mutation();
