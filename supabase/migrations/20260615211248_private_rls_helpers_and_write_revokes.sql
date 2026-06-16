-- Phase 8.1 / migration 10: lock down the schema-of-truth for the live deploy.
--
-- TWO halves, both validated against the local stack (rolled back) before push and
-- proven again by the live RLS suite. See DECISIONS 2026-06-15.
--
-- (1) WRITE-GRANT REVOKE. Make server-side-write-only EXPLICIT instead of relying on
--     the ABSENCE of a grant. The local Supabase CLI bootstrap issues a broad
--     `GRANT ALL ON ALL TABLES ... TO anon, authenticated` that migration 5 never
--     intended; it masked the missing write grant on the invariant-bearing tables so a
--     reviewer UPDATE was caught only by RLS (0 rows) instead of denied at the grant
--     layer. Hosted Supabase has no such broad grant and already denies at the grant
--     layer (42501) — the faithful behavior. We REVOKE the writes so BOTH environments
--     deny at the grant layer AND RLS (defense in depth).
--
-- (2) RLS HELPER RELOCATION to a non-exposed `private` schema. The four SECURITY
--     DEFINER helpers were flagged by the Supabase advisor as executable via
--     /rest/v1/rpc by anon/authenticated. The advisor's own remediation (REVOKE EXECUTE
--     FROM authenticated) BREAKS RLS: the querying role needs EXECUTE on functions
--     invoked inside its own policies (SECURITY DEFINER governs whose rights run the
--     BODY, not who may invoke). Proven locally — with EXECUTE revoked from
--     authenticated, `SELECT FROM deals` dies with "permission denied for function
--     can_access_deal". So instead we move the helpers into `private` (PostgREST serves
--     only public/graphql_public), removing the RPC surface for BOTH roles while RLS
--     keeps working: authenticated holds USAGE + EXECUTE in private, and policies stay
--     bound because SET SCHEMA / CREATE OR REPLACE preserve each function's OID.

-- ---------------------------------------------------------------------------
-- (1) Write-grant REVOKE on the server-side-write-only tables.
-- ---------------------------------------------------------------------------
revoke insert, update, delete on
    public.deals, public.quotes, public.audit_log,
    public.email_messages, public.attachments
from anon, authenticated;

-- ---------------------------------------------------------------------------
-- (2) Relocate the RLS helpers to a non-API-exposed schema.
-- ---------------------------------------------------------------------------
create schema if not exists private;

-- Move (OID preserved -> existing policies stay bound to these functions).
alter function public.current_user_role() set schema private;
alter function public.is_admin() set schema private;
alter function public.can_access_deal(uuid) set schema private;
alter function public.can_access_email(uuid) set schema private;

-- Repoint inter-helper body references to private.* (table refs stay in public).
-- CREATE OR REPLACE preserves the OID, so policy bindings are untouched.
-- current_user_role() needs no body change (it references only public.users).
create or replace function private.is_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(private.current_user_role() = 'admin', false);
$$;

create or replace function private.can_access_deal(d_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select private.is_admin() or exists (
        select 1
        from public.deals d
        where d.id = d_id
          and d.assigned_reviewer = (select auth.uid())
    );
$$;

create or replace function private.can_access_email(e_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select private.is_admin() or exists (
        select 1
        from public.email_messages m
        where m.id = e_id
          and m.deal_id is not null
          and private.can_access_deal(m.deal_id)
    );
$$;

-- Privileges. Strip the PUBLIC default grant + anon so neither can call them (the
-- private schema is already unexposed by PostgREST; this is belt-and-suspenders).
-- authenticated needs schema USAGE + EXECUTE to invoke them during RLS policy eval.
revoke execute on function
    private.current_user_role(), private.is_admin(),
    private.can_access_deal(uuid), private.can_access_email(uuid)
from public, anon;

grant usage on schema private to authenticated;
grant execute on function
    private.current_user_role(), private.is_admin(),
    private.can_access_deal(uuid), private.can_access_email(uuid)
to authenticated;
