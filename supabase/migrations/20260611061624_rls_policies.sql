-- Phase 1 / migration 5: Row-Level Security on every table.
--
-- Pattern: invariant-bearing tables (rates, quotes, audit_log) and the deal state
-- machine (deals) are SERVER-SIDE-WRITE-ONLY — clients get read access via RLS, and
-- the service_role (which bypasses RLS) is the sole writer. This keeps the 1.3
-- invariants (pinned rate_id, snapshotted amount, append-only audit, tamper-evidence)
-- and the deal state machine enforced in one place (the FastAPI service), not spread
-- across client policies. Only carriers and users accept admin writes via JWT.
--
-- auth.uid() is wrapped as (select auth.uid()) so the planner caches it per-statement
-- instead of re-evaluating per row.

-- ---------------------------------------------------------------------------
-- Access helpers (SECURITY DEFINER so child-table policies don't nest RLS
-- subqueries against deals/email_messages; STABLE; search_path locked).
-- ---------------------------------------------------------------------------
create function public.can_access_deal(d_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.is_admin() or exists (
        select 1
        from public.deals d
        where d.id = d_id
          and d.assigned_reviewer = (select auth.uid())
    );
$$;

create function public.can_access_email(e_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select public.is_admin() or exists (
        select 1
        from public.email_messages m
        where m.id = e_id
          and m.deal_id is not null
          and public.can_access_deal(m.deal_id)
    );
$$;

-- ---------------------------------------------------------------------------
-- Table grants. RLS filters rows only AFTER the role holds the table privilege;
-- without these grants a denied query would raise "permission denied" rather than
-- return an RLS-filtered empty set. Writes not granted here are server-side only
-- (service_role holds its own privileges and bypasses RLS).
-- ---------------------------------------------------------------------------
grant select on
    public.users, public.carriers, public.deals, public.email_messages,
    public.attachments, public.rates, public.quotes, public.audit_log
to authenticated;

grant insert, update, delete on public.users to authenticated;     -- gated to admin
grant insert, update, delete on public.carriers to authenticated;  -- gated to admin
grant insert on public.rates to authenticated;                     -- gated to admin

-- ---------------------------------------------------------------------------
-- Enable RLS
-- ---------------------------------------------------------------------------
alter table public.users enable row level security;
alter table public.carriers enable row level security;
alter table public.deals enable row level security;
alter table public.email_messages enable row level security;
alter table public.attachments enable row level security;
alter table public.rates enable row level security;
alter table public.quotes enable row level security;
alter table public.audit_log enable row level security;

-- ---------------------------------------------------------------------------
-- users: self-or-admin read; admin-only writes (blocks reviewer self-promotion).
-- ---------------------------------------------------------------------------
create policy users_select_self_or_admin on public.users
for select to authenticated
using ((select auth.uid()) = id or public.is_admin());

create policy users_admin_write on public.users
for all to authenticated
using (public.is_admin())
with check (public.is_admin());

-- ---------------------------------------------------------------------------
-- carriers: read-all; admin writes.
-- ---------------------------------------------------------------------------
create policy carriers_select_all on public.carriers
for select to authenticated
using (true);

create policy carriers_admin_write on public.carriers
for all to authenticated
using (public.is_admin())
with check (public.is_admin());

-- ---------------------------------------------------------------------------
-- deals: owner-or-admin read. NO client writes — the deal state machine
-- (new_enquiry -> quoted -> ...) is enforced in the service layer, NOT by RLS.
-- RLS would happily let an owner UPDATE state to any value; the machine is guarded
-- in the FastAPI service (or a future trigger). Reviewers/admins mutate deals only
-- through the API (service_role), so no authenticated INSERT/UPDATE/DELETE policy.
-- ---------------------------------------------------------------------------
create policy deals_select_owner_or_admin on public.deals
for select to authenticated
using (public.can_access_deal(id));

-- ---------------------------------------------------------------------------
-- email_messages: admin or accessible-deal read (NULL deal_id => admin only).
-- Writes are server-side (ingestion/send via service_role).
-- ---------------------------------------------------------------------------
create policy email_messages_select on public.email_messages
for select to authenticated
using (public.is_admin() or (deal_id is not null and public.can_access_deal(deal_id)));

-- ---------------------------------------------------------------------------
-- attachments: visible iff the parent email is. Server-side writes only.
-- ---------------------------------------------------------------------------
create policy attachments_select on public.attachments
for select to authenticated
using (public.can_access_email(email_message_id));

-- ---------------------------------------------------------------------------
-- rates: read-all; admin INSERT. No UPDATE/DELETE policy (forbid_mutation already
-- blocks them at the DB for every role). Computed-fallback/cron writes go through
-- the service_role.
-- ---------------------------------------------------------------------------
create policy rates_select_all on public.rates
for select to authenticated
using (true);

create policy rates_admin_insert on public.rates
for insert to authenticated
with check (public.is_admin());

-- ---------------------------------------------------------------------------
-- quotes: accessible-deal read so the review UI can show them. NO client writes:
-- a quote pins rate_id, snapshots amount from that rate, sets is_computed, and emits
-- an audit row — all engine logic. A direct client INSERT would bypass every 1.3
-- invariant, so creation/edit goes through the service_role only.
-- ---------------------------------------------------------------------------
create policy quotes_select on public.quotes
for select to authenticated
using (public.can_access_deal(deal_id));

-- ---------------------------------------------------------------------------
-- audit_log: admin-only READ. NO client INSERT policy — WITH CHECK (true) would let
-- a reviewer forge rows (wrong actor, spoofed actor_email) and gut tamper-evidence.
-- All writes (poll loop, surcharge cron, reviewer-triggered backend actions) go
-- through the service_role, which bypasses RLS. If a reviewer-JWT insert path is ever
-- truly needed, constrain it to WITH CHECK (actor = (select auth.uid())) — never
-- another actor, never NULL/system.
-- ---------------------------------------------------------------------------
create policy audit_log_admin_select on public.audit_log
for select to authenticated
using (public.is_admin());
