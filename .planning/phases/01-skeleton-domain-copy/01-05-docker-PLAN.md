---
phase: 01-skeleton-domain-copy
plan: 05
type: execute
wave: 4
depends_on: [01-03, 01-04]
files_modified:
  - Dockerfile
  - .dockerignore
  - docker-compose.yml
  - .env.template
autonomous: true
requirements:
  - DEPLOY-02
user_setup: []
tags:
  - docker
  - postgres
  - deployment

must_haves:
  truths:
    - "`docker build .` produces a runnable image based on python:3.12-slim, multi-stage, < 300 MB target (REQ DEPLOY-02, ROADMAP gate 4, D-11)"
    - "Image CMD is [\"uvicorn\", \"src.api.app:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"] — points at a module that doesn't exist yet (Phase 2 creates it). docker run will fail at import; that's accepted because gate is build, not run (D-11)"
    - "`docker-compose.yml` ships postgres-only in Phase 1 (D-12). No app service. No Traefik labels (D-13). postgres:16-alpine with healthcheck and named volume."
    - "`docker compose up postgres -d` then `poetry run alembic upgrade head` from the host succeeds (D-14, regression of Plan 03's gate 2 against the compose-managed Postgres)"
    - "`.env.template` documents the Phase-1 env vars needed locally (DATABASE_URL); placeholder for Phase-2 vars (OPENROUTER_*, SMTP_*) noted as 'Phase 2'"
    - "Final phase verification: all 4 ROADMAP gates green in one go (pytest, alembic upgrade head, poetry install, docker build)"
  artifacts:
    - path: "Dockerfile"
      provides: "Multi-stage Python 3.12 build (REQ DEPLOY-02)"
      contains: "python:3.12-slim"
    - path: "docker-compose.yml"
      provides: "Postgres-only compose for local dev (D-12)"
      contains: "postgres:16-alpine"
    - path: ".env.template"
      provides: "Documented env-var contract"
      contains: "DATABASE_URL"
    - path: ".dockerignore"
      provides: "Build-context hygiene (excludes .venv, __pycache__, .planning, EXTRACTION.md, etc.)"
      contains: ".venv"
  key_links:
    - from: "Dockerfile"
      to: "pyproject.toml + poetry.lock"
      via: "COPY pyproject.toml poetry.lock ./ in builder stage"
      pattern: "COPY pyproject\\.toml"
    - from: "Dockerfile"
      to: "src/"
      via: "COPY src ./src in final stage"
      pattern: "COPY src"
    - from: "docker-compose.yml postgres service"
      to: "alembic upgrade head from host"
      via: "exposes 5432 on localhost"
      pattern: "5432:5432"
---

<objective>
Ship the deployment scaffolding for Phase 1 — the Dockerfile that builds, the postgres-only compose stack for local dev, the env-var contract — and run the final phase verification that confirms all 4 ROADMAP success criteria are green simultaneously.

Purpose:
- REQ DEPLOY-02: Dockerfile builds a runnable image from python:3.12-slim
- ROADMAP gate 4: `docker build` succeeds
- ROADMAP gates 1-3: re-verified end-to-end against the docker-compose Postgres so Phase 1 closure is unambiguous
- D-11..D-14: enforced explicitly (CMD points at not-yet-existent module, postgres-only compose, no Traefik labels in this repo, host-side alembic verification)

Output:
- `Dockerfile` (multi-stage, python:3.12-slim, < 300 MB, builds clean)
- `.dockerignore` (excludes build context cruft)
- `docker-compose.yml` (postgres:16-alpine + healthcheck + named volume)
- `.env.template` (DATABASE_URL + Phase-2 placeholder env vars)
- A green run of all 4 ROADMAP gates recorded in 01-05-SUMMARY.md
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
@.planning/phases/01-skeleton-domain-copy/01-01-skeleton-PLAN.md
@.planning/phases/01-skeleton-domain-copy/01-03-migration-PLAN.md
@.planning/phases/01-skeleton-domain-copy/01-04-tests-PLAN.md
@EXTRACTION.md

<interfaces>
Dockerfile shape (multi-stage, planner-discretion choice: poetry-in-builder + pip-install-from-wheel-in-final to keep final image lean):

```dockerfile
# syntax=docker/dockerfile:1.7

# --- Builder stage ---
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
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
    PATH="/app/.venv/bin:$PATH"

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
CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

NOTE on `src.api.app:app` — D-11 says this module won't exist until Phase 2. The image BUILDS successfully (image size includes only the venv + source); RUNNING the image with that CMD fails at import (no `src/api/app.py` yet). That is an accepted Phase-1 outcome.

NOTE on `src.api.app` vs `domain.app` (src-layout collision): pyproject.toml's `packages = [{include="domain", from="src"}, {include="infra", from="src"}]` registers `domain` and `infra` as top-level packages — NOT `src.X`. The CMD as written (`src.api.app:app`) treats `src` as a Python path. To make this work cleanly Phase 2 will either (a) add `{include="api", from="src"}` to pyproject.toml so `from api.app import app` works, or (b) modify the CMD to `["uvicorn", "api.app:app", ...]` once api lands. For Phase 1, write the CMD AS-IS per D-11 (`src.api.app:app`) — the literal string in the locked decision. Phase 2 reconciles.

**Alternative simpler reading of D-11**: D-11's literal text is `["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]`. Use that exact CMD. Document in SUMMARY that Phase 2 may need to either (a) re-package as `api` not `src.api`, or (b) update CMD. Either way, Phase 1 gate is `docker build`, not `docker run`.

`.dockerignore` shape:

```
.git
.gitignore
.gsd
.gsd-id
.bg-shell
.claude
.planning
.venv
.env
.env.local
*.db
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.idea
.vscode
EXTRACTION.md
README.md
CLAUDE.md
tests
*.md
Dockerfile
docker-compose.yml
```

Note: `tests/` is excluded from the image — Phase-1 test execution is host-side via `poetry run pytest`. If a future phase adds in-image testing, revisit.

`docker-compose.yml` shape (postgres-only per D-12):

```yaml
# Phase 1: Postgres-only stack for local development.
# D-12: No app service yet (Phase 2 adds it).
# D-13: No Traefik labels in this repo. Routing/TLS owned by future edge-proxy stack.

services:
  postgres:
    image: postgres:16-alpine
    container_name: price_tracker_postgres
    environment:
      POSTGRES_USER: price_tracker
      POSTGRES_PASSWORD: price_tracker
      POSTGRES_DB: price_tracker
    ports:
      - "5432:5432"
    volumes:
      - price_tracker_pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U price_tracker -d price_tracker"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  price_tracker_pg_data:
    name: price_tracker_pg_data
```

`.env.template` shape:

```
# Phase 1 — local dev DB connection
# Used by alembic.ini and (Phase 2+) by the runtime SQLAlchemy engine.
DATABASE_URL=postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker

# --- Phase 2 placeholders (declared here for visibility; not consumed yet) ---
# OpenRouter (REQ INFRA-03, INFRA-04) — Phase 2 wires these into src/infra/llm.py
# OPENROUTER_API_KEY=
# OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
# OPENROUTER_HTTP_REFERER=https://prices.example.com
# OPENROUTER_APP_TITLE=Price Tracker
# PRICE_PARSER_MODEL=meta-llama/llama-4-scout
# PRICE_PARSER_FALLBACK_MODEL=anthropic/claude-haiku-4.5
# PRICE_PARSER_MODEL_CASCADE=${PRICE_PARSER_MODEL},${PRICE_PARSER_FALLBACK_MODEL}

# SMTP (REQ INFRA-02) — Phase 2 wires these into src/infra/email.py
# SMTP_HOST=
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASSWORD=
# SMTP_FROM=

# IAP (post-roadmap-reassess; Phase 3) — header trust, no Entra libs in this repo (D-17, D-18)
# ALLOWED_ENTRA_EMAIL=

# MCP (Phase 4)
# MCP_BEARER_TOKEN=
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Write Dockerfile + .dockerignore + docker-compose.yml + .env.template</name>
  <files>Dockerfile, .dockerignore, docker-compose.yml, .env.template</files>
  <read_first>
    - pyproject.toml (Plan 01 — confirms Poetry version-compatible scripts and the dep set)
    - alembic.ini (Plan 03 — confirms the file exists at repo root for COPY)
    - alembic/ (Plan 03 — confirms the directory exists for COPY)
    - src/ (Plan 02 — confirms the directory exists for COPY)
    - /home/magnus/dev/price-tracker/.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md (D-11..D-14, D-17, D-18)
    - /home/magnus/dev/price-tracker/EXTRACTION.md §7 (env vars list — for the .env.template Phase-2 placeholders)
  </read_first>
  <action>
Write all 4 files exactly per the `<interfaces>` block contents above. No deviations except as noted.

1. **`Dockerfile`** — copy verbatim from `<interfaces>`. Multi-stage (`builder` -> `final`), python:3.12-slim base, poetry-in-builder + venv-copy-to-final pattern. CMD is the literal D-11 string: `["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]`.

2. **`.dockerignore`** — copy from `<interfaces>`. Excludes `.git`, `.venv`, `.planning`, `tests`, `*.md`, etc. Keeps build context small (target < 5 MB build context, < 300 MB final image).

3. **`docker-compose.yml`** — copy from `<interfaces>`. Postgres-only. Named volume `price_tracker_pg_data`. Healthcheck on `pg_isready`. Port 5432 published to host (so the host-side `alembic upgrade head` from Plan 03 connects via `localhost:5432`).
   - DO NOT add an app service (D-12).
   - DO NOT add Traefik labels (D-13).
   - DO NOT add an external `edge` network here (D-18 — that's a future repo's concern).

4. **`.env.template`** — copy from `<interfaces>`. Active line: `DATABASE_URL=...`. Commented Phase-2 placeholders for visibility. NO `ALLOWED_ENTRA_OID`, `FASTAPI_AZURE_*`, or `JWT_*` vars (D-17 — auth libs dropped).

After writing, build the image and confirm size:

```bash
docker build -t price-tracker:phase1 .
docker images price-tracker:phase1 --format "{{.Repository}}:{{.Tag}} {{.Size}}"
```

Image size target: < 300 MB (D-11 / `<specifics>`). If the image is over, the most likely culprits are:
- Missing `--no-install-recommends` on apt-get (already set; double-check)
- Forgot to `rm -rf /var/lib/apt/lists/*` in either stage (already in template)
- Build tools (`build-essential`) leaking into final stage (template uses multi-stage to prevent this)

If still over, document the exact size in SUMMARY and accept; D-11 says target, not hard cap.
  </action>
  <verify>
    <automated>test -f Dockerfile &amp;&amp; test -f .dockerignore &amp;&amp; test -f docker-compose.yml &amp;&amp; test -f .env.template &amp;&amp; grep -q "python:3.12-slim" Dockerfile &amp;&amp; grep -q "AS builder" Dockerfile &amp;&amp; grep -q "AS final" Dockerfile &amp;&amp; grep -q '"src.api.app:app"' Dockerfile &amp;&amp; grep -q "postgres:16-alpine" docker-compose.yml &amp;&amp; ! grep -qi "traefik" docker-compose.yml &amp;&amp; grep -q "DATABASE_URL=postgresql+asyncpg" .env.template &amp;&amp; ! grep -q "FASTAPI_AZURE\|ALLOWED_ENTRA_OID\|^JWT_" .env.template &amp;&amp; docker build -t price-tracker:phase1 . &amp;&amp; SIZE=$(docker images price-tracker:phase1 --format "{{.Size}}"); echo "image size: $SIZE"</automated>
  </verify>
  <acceptance_criteria>
- All 4 files exist
- `grep -c "python:3.12-slim" Dockerfile` returns 2 (builder + final stages)
- `grep -c "AS builder\|AS final" Dockerfile` returns 2 (multi-stage confirmed)
- `grep -c '"src.api.app:app"' Dockerfile` returns 1 (D-11 literal CMD)
- `grep -c "postgres:16-alpine" docker-compose.yml` returns 1 (D-12)
- `grep -ci "traefik" docker-compose.yml` returns 0 (D-13)
- `grep -ci "fastapi-azure\|pyjwt" Dockerfile docker-compose.yml .env.template` returns 0 (D-17)
- `grep -c "5432:5432" docker-compose.yml` returns 1 (host alembic can connect)
- `grep -c "healthcheck:\|pg_isready" docker-compose.yml` returns 2 or more
- `grep -c "DATABASE_URL=postgresql+asyncpg" .env.template` returns 1
- `grep -c "OPENROUTER_API_KEY\|MCP_BEARER_TOKEN\|SMTP_HOST\|ALLOWED_ENTRA_EMAIL" .env.template` returns 4 (Phase-2/3/4 placeholders documented as comments)
- `docker build -t price-tracker:phase1 .` exits 0
- Final image exists: `docker images price-tracker:phase1 --format "{{.Repository}}"` returns "price-tracker"
- Image size < 350 MB (allow 50 MB headroom over the 300 MB D-11 target; document actual size in SUMMARY)
  </acceptance_criteria>
  <done>Dockerfile + compose + env template in place. `docker build` succeeds. Phase 1 gate 4 (REQ DEPLOY-02, ROADMAP gate 4) is satisfied.</done>
</task>

<task type="auto">
  <name>Task 2: End-to-end Phase 1 gate verification (all 4 ROADMAP criteria)</name>
  <files></files>
  <read_first>
    - .planning/ROADMAP.md (Phase 1 success criteria — the 4 gates)
    - .planning/phases/01-skeleton-domain-copy/01-01-SUMMARY.md (Plan 01 — confirm pyproject + skeleton landed)
    - .planning/phases/01-skeleton-domain-copy/01-02-SUMMARY.md (Plan 02 — confirm domain port + mapping table)
    - .planning/phases/01-skeleton-domain-copy/01-03-SUMMARY.md (Plan 03 — confirm migration + seed)
    - .planning/phases/01-skeleton-domain-copy/01-04-SUMMARY.md (Plan 04 — confirm pytest green)
  </read_first>
  <action>
Run all 4 ROADMAP Phase 1 gates back-to-back from the repo root and capture each gate's pass/fail evidence into `01-05-SUMMARY.md`. This is the definitive end-of-phase audit:

```bash
set -e
echo "=== GATE 1: pytest ==="
poetry run pytest -q

echo "=== GATE 3: poetry install (Python 3.12) ==="
poetry env info | grep "Python"
poetry install --no-root --with dev
poetry check

echo "=== GATE 2: alembic upgrade head against fresh Postgres (via docker-compose) ==="
# Tear down any leftover state
docker compose down -v 2>/dev/null || true
# Bring up postgres-only stack (D-12)
docker compose up postgres -d
# Wait for healthcheck
until [ "$(docker inspect -f '{{.State.Health.Status}}' price_tracker_postgres)" = "healthy" ]; do
  echo "waiting for postgres healthy..."
  sleep 2
done
# Run alembic from the host (D-14)
poetry run alembic upgrade head
# Assert tables
docker exec price_tracker_postgres psql -U price_tracker -d price_tracker -c "\dt"
# Assert seeded stores (sorted alphabetically by slug)
SLUGS=$(docker exec price_tracker_postgres psql -U price_tracker -d price_tracker -tAc "SELECT slug FROM stores ORDER BY slug")
echo "seeded slugs: $SLUGS"
test "$SLUGS" = "$(printf 'apotea\ndoz\nica\nmed24\nwillys')" || (echo "GATE 2 FAIL: store seed mismatch"; exit 1)
# Assert no contexts/tenants table
NO_CTX=$(docker exec price_tracker_postgres psql -U price_tracker -d price_tracker -tAc "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('contexts','tenants')")
test "$NO_CTX" = "0" || (echo "GATE 2 FAIL: contexts/tenants table found"; exit 1)
# Tear down
docker compose down -v

echo "=== GATE 4: docker build (re-confirm Task 1) ==="
docker build -t price-tracker:phase1 .
docker images price-tracker:phase1 --format "{{.Repository}}:{{.Tag}} {{.Size}}"

echo "=== ALL 4 PHASE 1 GATES GREEN ==="
```

Capture the full transcript (stdout + stderr) into `01-05-SUMMARY.md` under a section `## Phase 1 Gate Verification`. Each gate gets:
- Gate number + name (matches ROADMAP.md Phase 1 Success Criteria 1-4)
- Command run
- Exit code
- Key evidence (e.g., "X tests passed", "5 tables created", "image size 287 MB")
- PASS / FAIL

If ANY gate fails, do NOT mark the plan done — escalate via SUMMARY with the failing gate, the error, and a hypothesis (probably a regression in Plans 01-04 that needs revisiting).
  </action>
  <verify>
    <automated>set -e; poetry run pytest -q &gt; /tmp/g1.log 2&gt;&amp;1; G1=$?; poetry install --no-root --with dev &gt; /tmp/g3.log 2&gt;&amp;1; poetry check &gt;&gt; /tmp/g3.log 2&gt;&amp;1; G3=$?; docker compose down -v 2&gt;/dev/null || true; docker compose up postgres -d &gt; /tmp/g2.log 2&gt;&amp;1; for i in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Health.Status}}' price_tracker_postgres 2&gt;/dev/null)" = "healthy" ] &amp;&amp; break; sleep 2; done; poetry run alembic upgrade head &gt;&gt; /tmp/g2.log 2&gt;&amp;1; SLUGS=$(docker exec price_tracker_postgres psql -U price_tracker -d price_tracker -tAc "SELECT slug FROM stores ORDER BY slug"); test "$SLUGS" = "$(printf 'apotea\ndoz\nica\nmed24\nwillys')"; G2=$?; docker compose down -v &gt;&gt; /tmp/g2.log 2&gt;&amp;1; docker build -t price-tracker:phase1 . &gt; /tmp/g4.log 2&gt;&amp;1; G4=$?; echo "G1=$G1 G2=$G2 G3=$G3 G4=$G4"; test $G1 -eq 0 -a $G2 -eq 0 -a $G3 -eq 0 -a $G4 -eq 0 &amp;&amp; echo "ALL 4 PHASE 1 GATES GREEN"</automated>
  </verify>
  <acceptance_criteria>
- Gate 1 (pytest): exits 0; all ported tests pass
- Gate 2 (alembic upgrade head against compose-managed Postgres): exits 0; 5 tables created; 5 stores seeded with sorted slugs `apotea, doz, ica, med24, willys`; no `contexts` or `tenants` table
- Gate 3 (poetry install): exits 0 on Python 3.12; `poetry check` reports "All set!"
- Gate 4 (docker build): exits 0; `price-tracker:phase1` image present; size < 350 MB (target 300 MB per `<specifics>`)
- The verify command prints `ALL 4 PHASE 1 GATES GREEN`
- `01-05-SUMMARY.md` includes the gate transcript with PASS/FAIL per gate
  </acceptance_criteria>
  <done>All 4 ROADMAP Phase 1 success criteria verified end-to-end against the docker-compose Postgres + the built image. Phase 1 is closed; Phase 2 (Service Infrastructure) can start with a clean baseline.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Docker daemon -> Docker Hub | Pulls `python:3.12-slim` and `postgres:16-alpine` from Docker Hub during build/compose-up. Trust assumption: Docker Hub serves untampered images for these official tags. |
| Host -> docker-compose Postgres (port 5432) | Postgres exposed on `localhost:5432` for the host-side alembic command. In Phase 1 this is local dev only — no production posture. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-15 | Tampering / Supply Chain | `python:3.12-slim` base image (Docker Hub) | accept | Pin-by-digest is OPTIONAL for v1 single-user app per CONTEXT.md `<security_threat_model>`. Tag `python:3.12-slim` is an official image; supply-chain risk is mitigated by Docker Hub's signing infrastructure. Severity: low. Document accepted risk in SUMMARY; revisit if/when prod deploy lands. |
| T-01-16 | Tampering / Supply Chain | `postgres:16-alpine` base image | accept | Same as T-01-15. Official image. Severity: low. |
| T-01-17 | Information Disclosure | docker-compose.yml hardcodes `POSTGRES_PASSWORD=price_tracker` | accept | Local-dev-only. `.env.template` documents the runtime override pattern for Phase 2. Compose file is committed to git; if it later gets prod responsibility, the password MUST move to a secret manager. Severity: low (Phase 1 dev), high (if reused for prod — flagged for Phase 2 review). |
| T-01-18 | Tampering | `.dockerignore` excludes secrets-bearing files (`.env`, `*.db`) | mitigate | `.dockerignore` lists `.env`, `.env.local`, `*.db` so they cannot leak into image layers. Severity: low. |
| T-01-19 | Information Disclosure | image build logs may print pip/poetry install output | accept | Standard Docker build behavior; no secrets pass through poetry install. Severity: info. |

No high-severity threats in this plan (the password concern is conditional on a Phase-2 misuse).
</threat_model>

<verification>
The Task 2 automated command IS the verification — it runs all 4 gates back-to-back. The summary line `ALL 4 PHASE 1 GATES GREEN` confirms the phase is shippable.

Re-run on demand:

```bash
# Re-verify Phase 1 closure at any time
poetry run pytest -q
poetry install --no-root --with dev && poetry check
docker compose down -v && docker compose up postgres -d
until [ "$(docker inspect -f '{{.State.Health.Status}}' price_tracker_postgres)" = "healthy" ]; do sleep 2; done
poetry run alembic upgrade head
docker exec price_tracker_postgres psql -U price_tracker -d price_tracker -c "SELECT slug FROM stores ORDER BY slug;"
docker compose down -v
docker build -t price-tracker:phase1 .
```
</verification>

<success_criteria>
- `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.template` exist (REQ DEPLOY-02)
- `docker build .` exits 0 and produces a runnable image (ROADMAP gate 4)
- `docker compose up postgres` followed by host-side `alembic upgrade head` succeeds (ROADMAP gate 2 re-confirmed against the compose-managed Postgres)
- All 4 ROADMAP Phase 1 success criteria green in a single end-to-end run
- No Traefik labels in this repo (D-13)
- No app service in `docker-compose.yml` (D-12)
- No fastapi-azure-auth, pyjwt, ALLOWED_ENTRA_OID env vars anywhere (D-17)
- `01-05-SUMMARY.md` contains the verified gate transcript
</success_criteria>

<output>
After completion, create `.planning/phases/01-skeleton-domain-copy/01-05-SUMMARY.md` containing:
- The full Phase 1 gate verification transcript (4 gates, command + evidence + PASS/FAIL each)
- Final image size with comparison to the 300 MB target (D-11)
- Confirmation that D-11..D-14 are honored verbatim (CMD literal, postgres-only compose, no Traefik, host-side alembic works)
- Confirmation that D-17 holds (no auth libs in Dockerfile, no Entra env vars in .env.template)
- Notes on Phase 2 prerequisites the docker setup creates (network address `localhost:5432`, volume name `price_tracker_pg_data`)
- A bulleted "Phase 1 closed" statement listing each ROADMAP success criterion with its evidence pointer
- A reminder that D-19 schedules a Roadmap Reassess after Phase 1 closes — Phases 3 and 4 need rewriting to absorb the IAP topology shift (D-17, D-18) before planning continues
</output>
