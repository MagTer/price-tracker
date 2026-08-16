# syntax=docker/dockerfile:1.7

# --- Builder stage ---
FROM python:3.12-slim AS builder

# POETRY_VIRTUALENVS_IN_PROJECT below is load-bearing and must NOT be dropped in
# favour of the repo's poetry.toml, which states the same thing: that file is not
# copied into this stage (only pyproject.toml and poetry.lock are — see below), so
# poetry cannot read it here. Without the variable poetry installs into its cache
# and `COPY --from=builder /app/.venv` finds nothing. The failure is loud — the
# build dies on that COPY — but the cause is three lines away from the symptom.
# POETRY_VERSION is the hub of a three-way pin and this comment is the only place
# that names all of it. Bump ALL THREE together, or the one you miss resolves a
# different poetry than the other two:
#   here (the image)                    .github/workflows/ci.yml (env POETRY_VERSION)
#   home-server scripts/bootstrap-dev.sh §6 (uv tool install, the dev seat)
# The seat is in another repo on purpose — it provisions the machine, not the app —
# so nothing here can verify it; that is why it is written down rather than checked.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

# build-essential kept for any dependency that lacks a wheel; libpq is NOT
# needed — the app talks Postgres via asyncpg, which doesn't use libpq.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Copy dependency manifests first for cache efficiency
COPY pyproject.toml poetry.lock ./

# Install runtime deps only (skip dev group)
RUN poetry install --only main --no-root --no-ansi

# --- Final stage ---
FROM python:3.12-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

RUN useradd --system --uid 1001 --create-home app

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source + alembic config. pyproject.toml is REQUIRED at runtime: the package is
# installed --no-root, so the sidebar-footer version (_read_app_version) has no installed
# metadata to read and falls back to parsing /app/pyproject.toml — without this COPY the
# footer silently shows no version at all.
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini pyproject.toml ./

USER app

EXPOSE 8000

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
