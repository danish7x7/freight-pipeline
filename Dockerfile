# Shared image for the api and worker services. uv-managed, Python 3.12.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies + the project. Copy only what the build needs first so the
# dependency layer caches across source-only changes. README.md is referenced by
# pyproject's `readme` field, so it must be present at build time.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Put the project's venv on PATH so `uvicorn`/`python` resolve to it.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Default to the API; the worker service overrides this in docker-compose.yml.
CMD ["uvicorn", "freight.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
