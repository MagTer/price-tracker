# syntax=docker/dockerfile:1.7

# --- Builder stage ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_VIRTUALENVS_IN_PROJECT=1 \
    POETRY_NO_INTERACTION=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
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

# Runtime libs only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the venv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source + alembic config
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000

# Phase-1 CMD (D-11): uvicorn pointing at src.api.app:app — module not yet implemented (Phase 2)
# Build succeeds; running this image fails at import. Phase 2 implements api/app.py.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
