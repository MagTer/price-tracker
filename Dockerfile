# syntax=docker/dockerfile:1.7

# --- Builder stage ---
FROM python:3.12-slim AS builder

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
