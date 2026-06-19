# LEARNING.md

What building this pipeline actually taught me — drawn from the decision log
([`DECISIONS.md`](DECISIONS.md)), not reconstructed after the fact. The system is a
small, deliberately right-sized thing: ingest freight order emails, extract fields
with an LLM, price them, and let a human approve the reply. The interesting lessons
were rarely about the happy path. They came from the seams — between local and
hosted, between the model and the gate, between what a number looks like and what it
can defend.

This is honest. The residual risks are still residual; I say so where they are.

---

## 1. The environment is the test

The single most repeated lesson: **a test only proves what its environment lets it
prove.** Twice the same mistake wore different clothes, and both times the fix was to
make the environment faithful rather than to make the assertion lenient.

**RLS, local vs hosted (the grant-layer divergence).** A hermetic test asserted that
reviewer A updating reviewer B's deal returns *zero rows* — RLS doing its job. Against
live Supabase, the identical statement instead raised `InsufficientPrivilege` (42501,
permission denied). That looked like a regression; it was the opposite. The migration
grants `authenticated` SELECT-only on `deals`, so hosted Postgres denies the UPDATE at
the **grant layer, before RLS is ever consulted** — a *stronger* deny. Locally it came
back as 0 rows only because the Supabase CLI bootstrap quietly runs a broad
`GRANT ALL ... TO anon, authenticated` that our migrations never asked for; that extra
grant lets the statement clear the grant layer and fall through to RLS (which, having
no UPDATE policy, filters it to nothing).

So **hosted was the faithful environment and local was the looser outlier** — the
reverse of the usual assumption. The right fix wasn't to pick one error to expect; it
was to assert the *security outcome* (the write is blocked) and accept either form,
backstopped by an admin re-read proving the row never changed so the either-or can't
pass vacuously. Then I made the intent explicit in the schema of truth: a migration
that `REVOKE`s writes from `anon`/`authenticated`, so both environments now deny at the
grant layer — defense in depth instead of relying on the *absence* of a grant.

**Green-by-skip in CI (the same lesson, scaled up).** About twenty integration tests —
the ones that actually prove RLS isolation, append-only audit, atomic finalize,
versioned rates — skip when the database isn't reachable. A database-less CI would
therefore *skip-pass exactly the proofs that matter* and report green. A bare Postgres
container wasn't enough either: those tests set roles, write `auth.users`, and lean on
the Supabase auth schema and the `anon`/`authenticated`/`service_role` roles that only
the Supabase image bootstraps. Hand-rolling that on plain Postgres would be
re-implementing Supabase internals — brittle and beside the point. So CI runs the
pinned Supabase CLI's `supabase start` (the same path as a local `db reset`), with a
fail-*loud* connectivity precheck so a failed start fails the job instead of silently
letting the integration tests skip.

The first faithful CI run immediately earned its keep: it caught an audit test that had
been passing locally **for the wrong reason** — it switched to `service_role` and
inserted into `audit_log`, which only worked because of that same loose local bootstrap
grant. The real app never switches Postgres roles; it writes as the connection owner. CI,
faithful to the migrations, correctly denied the fictional path. The fix was to delete
the fictional role-switch from the test, not to widen the audit write surface to make it
pass. A test that's green for the wrong reason is worse than a red one — it's a red one
wearing camouflage.

---

## 2. The errors you can't see

A theme that runs straight through the contract ("never swallow errors") and then got
demonstrated three times in production-shaped code. Every one of these was invisible
until something forced it into the open.

**The fenced-JSON parse-and-swallow.** The pinned model provider doesn't enforce a JSON
response format and returns valid JSON wrapped in a Markdown ```` ```json ```` fence.
The client called `json.loads` on the fenced string, got a `ValueError`, and **silently
returned an empty result.** The first live evaluation scored 0/14 with *zero logged
failures* — a perfect, quiet zero. In the deployed pipeline this wouldn't have crashed;
it would have routed *every* email to human review forever, looking like a model that
simply never extracted anything. The fix was small (strip the fence before decoding) but
the real fix was the `logger.warning` on every fall-back-to-empty branch. The bug wasn't
the fence; it was that a failure could happen without leaving a trace.

**The per-request engine leak.** Every route built a brand-new SQLAlchemy engine per
request — uncached, never disposed — so each call leaked a connection pool against the
Supabase pooler. At the system's actual volume (~80 emails/day, one at a time) this is
invisible. But it's *monotonic*: the deployed backend had been leaking on every request
since it shipped. The load test made a slow, mysterious failure fail *fast* — 50
concurrent users and two-thirds of requests 500'd with `remaining connection slots are
reserved`. The fix is the textbook one (a process-level singleton engine; a SQLAlchemy
Engine is *designed* to be long-lived, and per-request construction is the documented
anti-pattern), but I'd never have gone looking without a test that pushed past the
design volume.

The pattern across both: **a Phase 9 instrument surfaced a real production fault that
the happy path structurally could not show.** The eval found the swallow; the load test
found the leak. Neither was an artifact of the measuring tool. The same shape appears in
the frontend, too — a console query discarded its error object, so an ambiguous-embed
failure (`PGRST201`) rendered *identically to an empty review queue* and cost about an
hour to localize. A swallowed error makes a hard failure look like a benign-empty one.
That's the whole lesson, in three different languages.

---

## 3. Measure before you optimize

The rate engine is where this bit hardest. The original computed-rate fallback was
**route-blind**: it priced off a hardcoded flat-distance constant, so *every* dry-van
lane returned the same $2,200 regardless of where the freight was going. Chicago→Dallas
and San Jose→Dallas — nearly double the distance — came back identical. It was a
placeholder honestly labeled as one, but the collision only became real when I watched
it happen live, and it would have quietly poisoned any rate-accuracy number in the
evaluation.

The instructive part was *how* I refused to fix it. The tempting shortcut was a geodesic
(straight-line) distance fallback. But straight-line distance is **systematically wrong
for freight** — trucks drive roads, not great circles — so it would have quoted off a
plausible-looking but wrong number. Worse, since an unknown lane routes to human review
anyway, a guessed distance wouldn't even save a step; it would just hand the reviewer a
*misleading anchor*. So the engine prices off a small committed table of real road miles
(miles are geography — not versioned, not pinned — so a module, not a database table, is
right-sized), and an off-table lane returns nothing and goes to review. After the
rebuild, the same equipment over different distances finally produced different totals:
Chicago→Dallas (925 mi) and Atlanta→Miami (665 mi) no longer collide. A wrong number
that looks right is more dangerous than an honest gap.

The same discipline shows up as restraint elsewhere. There's a known efficiency
opportunity — a cheap pre-LLM sender filter so obvious non-freight mail doesn't spend a
model call — that I deliberately *haven't* built. Not because it's hard, but because it
risks dropping a legitimate order, and it's a tuning decision that should be made against
measured token cost and latency, not a hunch. The measurements exist now; the
optimization waits for a reason. Measure first, then cut — and only cut what the numbers
say you can.

---

## 4. Plumbing has opinions

The Supabase transaction pooler taught me that connection plumbing is not transparent.
The pooler (pgbouncer in transaction mode, on port 6543) rotates the underlying backend
connection between statements. psycopg3, helpfully, auto-promotes frequently-run
statements to server-side *prepared* statements after a few executions — but a prepared
statement created on one backend connection is gone by the time the next statement lands
on a different one. The result was a delayed, confusing failure:
`prepared statement "_pg3_0" does not exist`, and it only struck once real traffic
crossed psycopg3's auto-prepare threshold. The health check had been passing the whole
time precisely because its trivial `SELECT 1` never prepared anything — a green check
that proved less than it appeared to (see theme 1).

The fix was one line in the single engine factory — `prepare_threshold=None` to disable
psycopg3's server-side prepares — paired with the operational removal of `?pgbouncer=true`
from the connection URL. Both come from the *same* root cause, and keeping the fix in the
one factory (rather than smearing pooler flags across every connection string) meant it
covered every statement in the app at once. The lesson generalizes: when a managed
service sits between you and Postgres, its behavior is part of your runtime, and the
defaults of your driver and the defaults of the proxy can quietly disagree.

---

## 5. Two release channels (schema must lead code)

The most recent incident was also one of the cleanest. A migration adding an `is_demo`
column shipped in the same commit as the code that read it. The code auto-deployed to
Render on push; the schema is applied by a *separate, manual* `supabase db push`. For a
window, the deployed code referenced a column the live database didn't have yet, and the
demo endpoint returned a live 500: `column deals.is_demo does not exist`.

The root cause isn't a bug in either system — it's that **code-deploy and schema-push are
two independent release channels**, and a migration sitting in the repo is not a
migration applied to the hosted database. The carry-forward rule writes itself: **schema
must lead code.** Any new migration is pushed to live and verified clean *before* the code
that depends on it deploys. It's the deploy-ordering corollary of "migrations are the
source of truth" — the source of truth still has to be *applied*, out of band, and in the
right order.

---

## 6. Numbers you can defend

The README reports measured results, and the rule I held there was "no rounding you can't
defend." That turned into a few specific, deliberate choices:

- **Report the hard number, characterize the soft one.** Token cost per email is a pinned,
  measured figure. The dollar cost is *not* reported as a precise number, because the
  published provider rates span a ~25× range — so it's honestly characterized as
  "sub-cent" rather than dressed up with false precision off a wide spread.
- **Say "measured on a date," not "reproducible."** The provider is pinned by an alias that
  resolved to a particular host with server-default sampling, so the accuracy figure is
  measured-on-a-date, not bit-reproducible — and the README says exactly that, rather than
  implying a determinism the setup doesn't have.
- **Count the right thing.** The evaluation criteria were chosen so a *robust* model that
  ignores an injection and extracts the true fields counts as containment *succeeding*, not
  failing — and a genuine false-accept is "accepted *and* escaped," not mere category
  membership. An earlier, looser reading had reported false-accepts that weren't. The
  honest denominator (schema-modeled field slots only; fields the system never claims to
  model are graded classification-only) keeps the accuracy number from taking credit — or
  blame — for capabilities the system doesn't have.

The through-line: a number's job is to be *defensible*, not impressive. The canonical
field-accuracy figure beats the raw one because the gate canonicalizes values — and
reporting both, with the gap, shows the gate earning its keep rather than hiding it.

---

## What stays open (and why that's fine)

Honesty means the residuals stay residual. The deploy runs on free tiers, so there are
**no automated backups / no point-in-time restore** — documented as accepted-open for a
synthetic, re-seedable showcase, not papered over as DR that exists. PII column encryption
is scoped to an at-rest baseline on synthetic data; a real-PII deployment is a documented
delta. The pre-LLM sender filter is unbuilt by choice (theme 3). The attachment *write*
path — fetching and storing real inbox PDFs — is scoped as its own future task rather than
faked. A couple of advisor warnings are deferred as performance-only micro-optimizations on
a security-sensitive surface, logged so they're not re-litigated.

None of these is a surprise; each is a decision with a reason and a date. That's the
actual thing this project was practice in: not building something that looks finished, but
keeping an honest ledger of what's proven, what's deferred, and *why* — so the next
person (including the future me) doesn't have to re-derive it.
