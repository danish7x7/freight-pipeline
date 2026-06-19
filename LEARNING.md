# LEARNING.md

Notes on what building this pipeline taught me, pulled from the decision log
([`DECISIONS.md`](DECISIONS.md)) rather than written from memory after the fact.

The system is small on purpose: take freight order emails, pull the fields out with
an LLM, price them, and have a human approve the reply before anything sends. Most of
what I learned didn't come from the happy path. It came from the places where two
parts of the system met and didn't agree — local vs hosted, the model vs the
validation gate, code deploys vs schema migrations.

I've tried to keep this honest about what's still open. Where something is deferred, I
say so.

## Get the data model and the rate engine right first

I designed the schema and the rate engine before building much around them, because
both are hard to change later. The rate engine is where being deliberate paid off.

The first version priced every dry-van lane the same. It used a hardcoded distance
constant (`_FLAT_MILES=800`), so Chicago→Dallas and a much shorter lane both came back
at $2,200. It was labeled as a placeholder, but it would have quietly wrecked any
rate-accuracy number in the eval, so it had to go.

The obvious shortcut was to compute straight-line distance with a geocoding library
and multiply by a circuity factor to approximate road miles. I looked into it and
decided against it. Straight-line distance is always short, and the error isn't even
consistent — a flat interstate run is close to straight-line, but anything routing
around mountains or water can be off by 40% or more. A flat multiplier gets the
average lane roughly right and specific lanes badly wrong, and you can't tell which
without the real road network. On top of that, an unknown lane already routes to human
review, so a guessed distance wouldn't save a step — it would just hand the reviewer a
number that looks authoritative and is wrong.

What I built instead: a small committed table of real road miles for the city pairs
the corpus actually uses. The accuracy gets paid for once at build time instead of
approximated forever at runtime. A lane that isn't in the table returns nothing and
goes to review, which is the behavior I want anyway. I kept it as a code module rather
than a database table because road miles are geography — they don't get versioned or
pinned the way rates do. After the rebuild, different distances finally produced
different prices.

The general lesson: don't optimize before you can measure. The flat rate looked fine
until I watched it price two different lanes identically. And don't reach for a fix
that produces a plausible wrong answer — an honest gap that routes to review is safer
than a confident bad number.

## Break the system into layers and keep the boundaries real

The pipeline is built as distinct layers — ingest, extract, validate, price, review,
send — and the rule is that each one only hands the next a validated thing. The
validation gate between extraction and pricing is the important one: the LLM's output
is untrusted, so nothing reaches the rate engine until it's passed an allowlist check
on every field. If a field is off the allowlist, the whole thing goes to review rather
than getting sanitized and used. That gate is what makes the injection defense work —
the model proposes, but it can't push a bad value through, and it can never trigger a
send.

Keeping the boundaries real also meant building against interfaces (the LLM client,
the Gmail client, the queue) so I could swap implementations by config instead of
rewriting call sites. That's what let the eval run a deliberately-fooled model mock
through the real gate to prove the gate contains injection regardless of what the model
does.

## Make sure the layers actually talk to each other — and check between them

The recurring failure was layers that looked connected but were passing the wrong
thing across the boundary. Three versions of the same bug:

**A swallowed JSON parse error.** The model returns valid JSON wrapped in a markdown
code fence. The client ran `json.loads` on the fenced string, hit a `ValueError`, and
silently returned an empty result. The first live eval scored 0/14 with nothing logged
— a clean zero that looked like a model that never extracted anything. In production it
would have routed every single email to review forever. The fix to the parse was one
line (strip the fence). The fix that mattered was logging a warning on every
fall-back-to-empty branch, so a failure can't happen silently again.

**A connection leak between the app and the database.** Every request built a fresh
SQLAlchemy engine that was never disposed, so each call leaked a connection against the
Supabase pooler. At real volume (~80 emails a day, one at a time) you'd never see it,
but it had been leaking on every request since deploy. A load test made it fail fast —
50 concurrent users, two-thirds of requests 500'd on exhausted connection slots. The
fix is the standard one (a single long-lived engine), but I only found it because the
load test pushed past the volume the system was designed for.

**A frontend query that dropped its error.** A console query discarded the error
object, so a real failure (`PGRST201`, an ambiguous join) rendered exactly like an
empty review queue. About an hour to track down.

Same lesson three times: a swallowed error turns a hard failure into something that
looks fine-but-empty, and the check between layers is what surfaces it. The eval and
the load test weren't just measuring — they were the checks that exposed faults the
happy path structurally can't show.

## The test only proves what its environment lets it prove

I hit this twice with the database, and both times the fix was to make the test
environment faithful instead of making the assertion looser.

A test checked that a reviewer can't update another reviewer's deal. Locally it came
back as zero rows (RLS filtering it out). Against hosted Supabase the same statement
raised a permission error (42501) instead — a stronger denial at the grant layer,
before RLS is even consulted. It looked like a regression and was actually the hosted
environment being stricter. The reason they differed: the Supabase CLI bootstrap adds a
broad `GRANT ALL` that my migrations never asked for, which let the local statement get
further than it should. The fix was to assert the outcome (the write is blocked) and
accept either form, backed by a re-read proving the row never changed — then add a
migration that revokes those writes explicitly, so both environments deny at the same
layer.

The bigger version of this was CI. About twenty integration tests — the ones that
prove RLS isolation, append-only audit, atomic finalize — skip when there's no
database. A database-less CI would skip exactly those and report green. So CI now spins
up the real Supabase stack and fails loudly if it can't, instead of letting the
important tests quietly skip. The first faithful run immediately caught a test that had
been passing locally for the wrong reason — it switched Postgres roles in a way the
real app never does, which only worked because of that same loose local grant. I
deleted the fake path rather than widening the real one. A test that's green for the
wrong reason is worse than one that's red.

## Deploy early to catch what only shows up live

A lot of these only surfaced because the thing was actually deployed and running, not
just passing tests.

The clearest one was a deploy-ordering bug. I shipped a migration (adding an `is_demo`
column) in the same commit as the code that read it. The code auto-deploys to Render on
push; the schema is applied separately by a manual `supabase db push`. For a window,
the live code referenced a column the database didn't have yet, and the endpoint
returned a 500. Neither system was broken — they're just two independent release
channels, and a migration sitting in the repo isn't a migration applied to the hosted
database. The rule now: schema leads code. Push the migration to live and verify it
before deploying the code that depends on it.

The connection pooler taught me something similar about trusting infrastructure to be
transparent. The Supabase transaction pooler rotates the backend connection between
statements, and psycopg3 auto-promotes frequent statements to prepared statements after
a few runs — but a prepared statement made on one connection is gone when the next
statement lands on a different one. It failed only once real traffic crossed that
threshold, and the health check had been passing the whole time because its trivial
`SELECT 1` never prepared anything. One line fixed it (`prepare_threshold=None` in the
single engine factory), but I wouldn't have predicted it from reading the code.

## Don't leak secrets, and don't fake the things you didn't do

Every secret lives in env vars or the provider console — nothing in code or git, with
placeholders in `.env.example` and the Gmail token scoped to read and send only. The
one place this got interesting: the demo feature was originally going to require an
admin login, which would have meant publishing admin credentials to let strangers try
it. I caught that before it shipped and redesigned it around a least-privilege reviewer
account plus a flag that structurally blocks demo deals from ever sending. (Checking
the live database also confirmed the seed file never ran against it, so no real
credential was ever exposed.)

The same instinct applies to how the numbers are reported. The rule was "no rounding
you can't defend." Token cost per email is a real measured figure, so it's reported as
one. The dollar cost isn't, because provider rates span a wide range, so it's described
as sub-cent rather than given false precision. The accuracy figure is "measured on a
date," not "reproducible," because the model isn't pinned tightly enough to claim
determinism it doesn't have. And the containment numbers count the right thing — a
model that ignores an injection and extracts the true fields counts as the gate
succeeding, and a real false-accept means a bad value actually escaped, not just that a
sample was adversarial.

## What's still open

Keeping an honest ledger means leaving the open things open. The deploy runs on free
tiers, so there are no automated backups — fine for a synthetic, re-seedable showcase,
but documented as accepted rather than pretended away. PII encryption is at-rest
baseline only on synthetic data; a real-PII deployment is a documented delta. The
pre-LLM sender filter that would skip model calls on obvious non-freight mail is
deliberately unbuilt — it risks dropping a legitimate order, and that's a tuning call I
want to make against measured cost, not a guess. A few advisory warnings are deferred
as performance-only items on a low-volume surface.

None of these are surprises. Each is a decision with a reason and a date, which is the
thing this project was really practice in: not making something look finished, but
keeping a clear record of what's proven, what's deferred, and why.