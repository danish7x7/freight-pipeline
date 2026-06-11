-- Phase 1 seed data. Runs on `supabase db reset` after migrations.
--
-- Doubles as a load-time integration check of the full FK graph: a quote pinning a
-- contracted rate, an email linked to a deal, and reviewer-owned deals all inserting
-- cleanly proves the migrations compose. Also the Phase 5/10 demo dataset.
--
-- NOTE: seed users are FIXTURES, not credentials. The bare insert into auth.users
-- (id, email) satisfies FK + RLS but is NOT login-able. Phase 5 (Supabase Auth login)
-- needs encrypted_password, email_confirmed_at, aud, role='authenticated', and
-- instance_id — added then, or provisioned via the Auth admin API.

-- ---------------------------------------------------------------------------
-- Users (auth.users fixtures + app rows)
-- ---------------------------------------------------------------------------
insert into auth.users (id, email) values
    ('a1111111-1111-1111-1111-111111111111', 'admin@freight.local'),
    ('a2222222-2222-2222-2222-222222222222', 'reviewer1@freight.local'),
    ('a3333333-3333-3333-3333-333333333333', 'reviewer2@freight.local');

insert into public.users (id, email, role) values
    ('a1111111-1111-1111-1111-111111111111', 'admin@freight.local', 'admin'),
    ('a2222222-2222-2222-2222-222222222222', 'reviewer1@freight.local', 'reviewer'),
    ('a3333333-3333-3333-3333-333333333333', 'reviewer2@freight.local', 'reviewer');

-- ---------------------------------------------------------------------------
-- Carriers (exercises the MC eligibility gate: active vs blocked; not-found=unknown)
-- ---------------------------------------------------------------------------
insert into public.carriers (id, mc_number, name, status) values
    ('c1111111-1111-1111-1111-111111111111', 'MC123456', 'Acme Trucking', 'active'),
    ('c2222222-2222-2222-2222-222222222222', 'MC999999', 'Blocked Hauler', 'blocked');

-- ---------------------------------------------------------------------------
-- Rates. Lane Chicago,IL -> Dallas,TX / dry_van is set up so precedence competes:
--   * two contracted lane-generic versions with DISTINCT effective_from
--     (current = the newer one, deterministically),
--   * a carrier-specific contracted row on the SAME lane (wins for Acme),
--   * a computed row on the same lane (must be EXCLUDED by the source='contracted'
--     filter — never allowed to masquerade as contracted).
-- Plus a second lane (reefer) that the seed quote pins.
-- created_by = admin fixture.
-- ---------------------------------------------------------------------------
insert into public.rates
    (id, origin_city, origin_state, dest_city, dest_state, equipment,
     carrier_id, source, amount_cents, effective_from, created_by) values
    -- lane-generic v1 (older)
    ('e1111111-1111-1111-1111-111111111111', 'Chicago', 'IL', 'Dallas', 'TX',
     'dry_van', null, 'contracted', 120000, now() - interval '60 days',
     'a1111111-1111-1111-1111-111111111111'),
    -- lane-generic v2 (newer => current lane-generic)
    ('e2222222-2222-2222-2222-222222222222', 'Chicago', 'IL', 'Dallas', 'TX',
     'dry_van', null, 'contracted', 125000, now() - interval '10 days',
     'a1111111-1111-1111-1111-111111111111'),
    -- carrier-specific for Acme on the SAME lane (wins for Acme)
    ('e3333333-3333-3333-3333-333333333333', 'Chicago', 'IL', 'Dallas', 'TX',
     'dry_van', 'c1111111-1111-1111-1111-111111111111', 'contracted', 118000,
     now() - interval '5 days', 'a1111111-1111-1111-1111-111111111111'),
    -- computed on the SAME lane (must be excluded by source='contracted' lookup)
    ('e4444444-4444-4444-4444-444444444444', 'Chicago', 'IL', 'Dallas', 'TX',
     'dry_van', null, 'computed', 130000, now() - interval '1 day', null),
    -- second lane (reefer), pinned by the seed quote
    ('e5555555-5555-5555-5555-555555555555', 'Atlanta', 'GA', 'Miami', 'FL',
     'reefer', null, 'contracted', 95000, now() - interval '20 days',
     'a1111111-1111-1111-1111-111111111111');

-- ---------------------------------------------------------------------------
-- Deals (one per reviewer)
-- ---------------------------------------------------------------------------
insert into public.deals
    (id, state, assigned_reviewer, carrier_id,
     origin_city, origin_state, dest_city, dest_state, equipment) values
    ('d1111111-1111-1111-1111-111111111111', 'new_enquiry',
     'a2222222-2222-2222-2222-222222222222', null,
     'Chicago', 'IL', 'Dallas', 'TX', 'dry_van'),
    ('d2222222-2222-2222-2222-222222222222', 'quoted',
     'a3333333-3333-3333-3333-333333333333',
     'c1111111-1111-1111-1111-111111111111',
     'Atlanta', 'GA', 'Miami', 'FL', 'reefer');

-- ---------------------------------------------------------------------------
-- Email linked to deal A
-- ---------------------------------------------------------------------------
-- ingest_status = 'processed': this demo email is already extracted (intent +
-- confidence set), so it is coherent and the reconciliation sweep never touches it.
insert into public.email_messages
    (id, gmail_message_id, thread_id, deal_id, sender, subject, body,
     intent, confidence, received_at, ingest_status) values
    ('f1111111-1111-1111-1111-111111111111', 'seed-msg-0001', 'seed-thread-0001',
     'd1111111-1111-1111-1111-111111111111', 'broker@example.com',
     'Rate request: Chicago, IL -> Dallas, TX',
     'Need a dry van rate for 42,000 lbs, pickup Monday.',
     'rate_request', 0.92, now() - interval '2 days', 'processed');

-- ---------------------------------------------------------------------------
-- Quote on deal B, pinning the reefer contracted rate (amount copied from it)
-- ---------------------------------------------------------------------------
insert into public.quotes
    (id, deal_id, rate_id, amount_cents, currency, is_computed, created_by) values
    ('aaaa0000-0000-0000-0000-000000000001',
     'd2222222-2222-2222-2222-222222222222',
     'e5555555-5555-5555-5555-555555555555', 95000, 'USD', false,
     'a1111111-1111-1111-1111-111111111111');
