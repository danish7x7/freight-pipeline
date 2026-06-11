# DECISIONS.md
Append decisions and dead-ends here, newest first, with dates.

## 2026-06-10 — Phase 0 foundations: layout and interface seam
**Decisions:**
- **`src/` layout** (`src/freight/`), built by hatchling. Keeps the installed
  package separate from the repo root so tests run against the install, not CWD.
- **Swap-by-config seam.** Interfaces live in `freight.interfaces` (Protocols +
  Pydantic DTOs); implementations are chosen in `freight.factories` from
  `Settings.*_backend`. Call sites depend only on the Protocols. Real backends
  (`hf`/`gmail`/`qstash`) raise `NotImplementedError` naming their phase until built.
- **`Queue` has no `consume()`/`subscribe()`.** The real queue is Upstash QStash,
  which is push-based (HTTP delivery). A pull model would not map onto it, so the
  consumption side is a `Handler = Callable[[QueueMessage], Awaitable[None]]` the
  transport invokes. Documented in `interfaces/queue.py` so it isn't re-added later.
- **`LLMClient.complete` always returns `LLMResult`**, never raw text — keeps a
  consistent structured wrapper (`data`/`raw`/`confidence`) at the boundary.
- **`.env` optional in docker-compose** (`required: false`); service-to-service URLs
  are set inline so `docker compose up` works before any `.env` exists.

**Dead-ends / gotchas:**
- pydantic-mypy `init_typed = true` rejects passing a plain `str` where an enum field
  (`AppEnv`) is expected, even though Pydantic coerces it at runtime. Construct with
  the enum or omit the field; don't pass the bare string.
- Starlette's `TestClient` warns it wants `httpx2` instead of `httpx`. Harmless today;
  revisit if/when the test suite needs it silenced or the dep is pinned.

**Verification:** `uv run ruff check .`, `uv run mypy .`, `uv run pytest` (10 passed),
and `docker compose up` (postgres/redis/api healthy, `/health` → 200, worker logs
startup) all pass.

## 2026-06-10 — Toolchain: uv instead of conda + pip
**Decision:** Use `uv` as the Python toolchain for the backend, replacing the
conda + `pip install -e` workflow originally written in `CLAUDE.md`. Canonical
commands are now `uv sync`, `uv add <pkg>` / `uv add --dev <pkg>`, and
`uv run <cmd>` (e.g. `uv run pytest`, `uv run ruff check .`, `uv run mypy .`).
`CLAUDE.md` Commands section updated to match.
**Why:** Single, fast resolver/locker; reproducible env via `uv.lock`; no separate
conda activation step. Chosen explicitly during Phase 0 setup.
**Trade-off:** Diverges from the original conda assumption; anyone cloning needs
`uv` installed. Frontend tooling (npm) is unchanged.
