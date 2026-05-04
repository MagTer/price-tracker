---
phase: 01-skeleton-domain-copy
plan: 05
subsystem: infra
tags: [docker, postgres, alembic, poetry, deployment, phase-gate]

# Dependency graph
requires:
  - phase: 01-skeleton-domain-copy
    provides: pyproject.toml + poetry.lock (01-01), domain modules (01-02), alembic + 0001_initial (01-03), pytest suite (01-04)
provides:
  - Multi-stage Dockerfile (python:3.12-slim, 167 MB image)
  - Postgres-only docker-compose (price_tracker_pg_data named volume, 5432 published, healthcheck)
  - .env.template with DATABASE_URL active and Phase 2/3/4 placeholders documented
  - .dockerignore excluding build-context cruft
  - End-to-end verification of all 4 ROADMAP Phase 1 success criteria green simultaneously
affects: [02-service-infrastructure, 03-admin-ui-entra-auth, 04-mcp-server, 05-source-cleanup]

# Tech tracking
tech-stack:
  added: [Docker (BuildKit 1.7), docker-compose, postgres:16-alpine]
  patterns: [Multi-stage Docker (builder + final), Poetry-in-builder + venv-copy-to-final, host-side alembic against compose Postgres]

key-files:
  created:
    - Dockerfile
    - .dockerignore
    - docker-compose.yml
    - .env.template
  modified:
    - .gitignore (add !.env.template exception)

key-decisions:
  - "POETRY_VERSION bumped from plan-spec 1.8.3 to 2.3.2 to match project's PEP 621 pyproject.toml (Plan 01-01 deviation continuation)"
  - "Final image size 167 MB — well under D-11's 300 MB target; no further optimization needed"
  - ".env.template added to .gitignore exception list (!.env.template) since the .env.* rule would otherwise prevent committing it"

patterns-established:
  - "Multi-stage Docker pattern: builder installs Poetry + deps into /app/.venv (with build-essential + libpq-dev), final stage copies only the venv + source + alembic config (with libpq5 only). Keeps final image lean."
  - "Compose database stack pattern: postgres:16-alpine + named volume (price_tracker_pg_data) + healthcheck (pg_isready) + 5432 published to localhost. Used by Phase 1 host-side alembic; will be joined by app service in Phase 2 per D-12."
  - "Env-var contract pattern: active vars uncommented, future-phase vars commented with their phase tag and target file (e.g., 'Phase 2 wires these into src/infra/llm.py'). Single-source visibility without runtime consumption."

requirements-completed: [DEPLOY-02]

# Metrics
duration: ~10min
completed: 2026-05-04
---

# Phase 1 Plan 05: Docker + Phase 1 Closure Summary

**Multi-stage python:3.12-slim Dockerfile (167 MB), postgres-only compose stack, .env.template — all 4 ROADMAP Phase 1 gates verified green end-to-end.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-05-04T (after 85d3976)
- **Completed:** 2026-05-04
- **Tasks:** 2 (1 file-creation + 1 verification)
- **Files modified:** 5 (Dockerfile, .dockerignore, docker-compose.yml, .env.template, .gitignore)

## Accomplishments

- Dockerfile (multi-stage, python:3.12-slim, Poetry 2.3.2 in builder, libpq5 in final) builds clean to a 167 MB image (target <300 MB)
- docker-compose.yml with postgres:16-alpine + healthcheck + named volume `price_tracker_pg_data` + port 5432 published — D-12 honored (no app service), D-13 honored (no Traefik labels)
- `.env.template` documents `DATABASE_URL` actively + commented placeholders for Phase 2 (OPENROUTER_*, SMTP_*), Phase 3 (ALLOWED_ENTRA_EMAIL), Phase 4 (MCP_BEARER_TOKEN) — D-17 honored (no fastapi-azure-auth, pyjwt, ALLOWED_ENTRA_OID)
- All 4 ROADMAP Phase 1 success criteria verified back-to-back in a single end-to-end run (transcript below)

## Task Commits

Each task was committed atomically:

1. **Task 1: Write Dockerfile + .dockerignore + docker-compose.yml + .env.template** — `10af0ba` (feat)
2. **Task 2: End-to-end Phase 1 gate verification** — no code commit (verification-only; evidence captured in this SUMMARY)

**Plan metadata commit:** added in the final docs commit alongside SUMMARY.md, STATE.md, ROADMAP.md updates.

## Files Created/Modified

- `Dockerfile` — multi-stage build, python:3.12-slim base both stages, builder installs Poetry 2.3.2 + main deps into /app/.venv, final copies venv + src + alembic + alembic.ini, EXPOSE 8000, CMD `["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]` (D-11 literal)
- `.dockerignore` — excludes .git, .gsd, .planning, .claude, .venv, .env*, *.db, __pycache__, EXTRACTION.md, README.md, CLAUDE.md, tests/, *.md, Dockerfile, docker-compose.yml — keeps build context lean
- `docker-compose.yml` — postgres:16-alpine, container_name `price_tracker_postgres`, env user/password/db = `price_tracker`, port 5432:5432, named volume `price_tracker_pg_data`, healthcheck on `pg_isready` (5s interval, 3s timeout, 10 retries)
- `.env.template` — active `DATABASE_URL=postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker` + commented Phase-2/3/4 vars
- `.gitignore` — added `!.env.template` to .env.* exception list

## Phase 1 Gate Verification

Definitive end-of-phase audit. All 4 ROADMAP Phase 1 success criteria run back-to-back from the repo root, evidence captured here.

### Gate 1: pytest runs the full ported suite green

**Command:** `poetry run pytest -q`
**Exit code:** 0
**Evidence:** `67 passed, 8 warnings in 0.46s`
**Result:** PASS

(8 warnings are RuntimeWarning about unawaited coroutine in mocks — pre-existing, documented in 01-04 SUMMARY as accepted artifact of source repo's mock pattern under pytest-asyncio.)

### Gate 2: alembic upgrade head against fresh Postgres → 5 tables + 5 seeded stores

**Setup:** `docker compose down -v` → `docker compose up postgres -d` → wait for healthy (2 polls, ~4s)
**Command:** `DATABASE_URL=postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker poetry run alembic upgrade head`
**Exit code:** 0
**Evidence:**
- Migration ran: `Running upgrade  -> 0001_initial, initial schema (squashed: tables + threshold + unit-price alerts + doz seed)`
- `\dt` shows 6 tables: `alembic_version`, `price_points`, `product_stores`, `products`, `stores`, `watches` (5 domain tables + alembic bookkeeping — matches D-08, no `price_tracker_` prefix per D-09)
- Seeded slugs (sorted): `apotea, doz, ica, med24, willys` — all 5 stores from `src/domain/stores/__init__.py` registry
- `contexts` / `tenants` table count: 0 (D-03/D-10 honored — no phantom tenants table)

**Teardown:** `docker compose down -v` removed container, network, and volume (volume `price_tracker_pg_data` removed)
**Result:** PASS

### Gate 3: poetry install resolves on Python 3.12

**Commands:**
- `poetry env info | grep "Python"` → `Python: 3.12.3` / `Implementation: CPython`
- `poetry install --no-root --with dev` → `No dependencies to install or update`
- `poetry check` → `All set!`

**Exit code:** 0
**Evidence:** Lock file is in sync; pyproject.toml passes Poetry's strict validation; Python 3.12.3 is the active interpreter.
**Result:** PASS

### Gate 4: docker build produces a runnable image from python:3.12-slim

**Command:** `docker build -t price-tracker:phase1 .`
**Exit code:** 0
**Evidence:**
- Image: `price-tracker:phase1 167MB`
- 167 MB is well under the 300 MB D-11 target (44% headroom)
- Build steps 1-17 all completed; final layer is the `CMD ["uvicorn", "src.api.app:app", ...]` per D-11
- `docker run` of this image will fail at import (no `src/api/app.py` yet); accepted per D-11 — Phase 1 gate is `docker build`, not `docker run`. Phase 2 implements `src/api/app.py` and reconciles the package path (D-11 note in plan: either repackage as `api` or update CMD).

**Result:** PASS

### Summary line

**ALL 4 PHASE 1 GATES GREEN.**

| Gate | Criterion | Evidence | Status |
|------|-----------|----------|--------|
| 1 | pytest green | 67 passed, 8 warnings, 0.46s | PASS |
| 2 | alembic upgrade head against fresh Postgres + 5 tables + 5 seeded stores | 5 domain tables, slugs `apotea, doz, ica, med24, willys`, no `tenants` | PASS |
| 3 | poetry install on Python 3.12 | Python 3.12.3, lock in sync, `poetry check: All set!` | PASS |
| 4 | docker build from python:3.12-slim | `price-tracker:phase1` 167 MB, build exit 0 | PASS |

## Decisions Made

- **POETRY_VERSION 2.3.2 (not 1.8.3 as in plan-spec interfaces):** The plan's `<interfaces>` block pinned `POETRY_VERSION=1.8.3`, but the project's `pyproject.toml` was adapted to Poetry 2.x PEP 621 `[project]` table during Plan 01-01 (logged as Rule 3 deviation in that plan's SUMMARY). Poetry 1.8.3 in the builder rejected the manifest with `The fields ['authors', 'description', 'name', 'version'] are required in package mode` — a 1.x vs 2.x incompatibility. Bumped to 2.3.2 (matches both the host's Poetry version and the lock-file generator) and the build succeeded. Image size unaffected (167 MB).
- **`.env.template` git-ignore exception:** The repo's `.gitignore` excludes `.env.*` with `!.env.example` as the only exception. Since the plan mandates committing `.env.template` (not `.env.example`), added `!.env.template` to the exception list so the file can be tracked. Naming convention preserved per the plan spec.
- **D-11 CMD literal preserved:** `CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]` written verbatim per locked decision, even though `src.api.app` won't exist until Phase 2 and the project's `[tool.poetry] packages` doesn't include `api`. Phase 2 will reconcile (either repackage `api` from `src` or update CMD to `api.app:app`). Image BUILDS — that's the gate, not RUN.
- **Comment line `# D-13: No Traefik labels in this repo. ...` retained in docker-compose.yml:** The plan's `<interfaces>` block includes this exact comment line. The acceptance criterion `grep -ci "traefik" docker-compose.yml returns 0` would technically fail because the word "Traefik" appears in the comment. Kept the comment because (a) it is load-bearing documentation explaining the absence per D-13 and (b) the spec in `<interfaces>` is authoritative over the verify helper. No actual Traefik configuration exists.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Bumped Poetry version in Dockerfile from 1.8.3 to 2.3.2**

- **Found during:** Task 1 (initial `docker build`)
- **Issue:** Plan's `<interfaces>` block pinned `POETRY_VERSION=1.8.3`. Build failed at `RUN poetry install --only main --no-root --no-ansi` with `The Poetry configuration is invalid: The fields ['authors', 'description', 'name', 'version'] are required in package mode.` Root cause: the project's `pyproject.toml` uses the Poetry 2.x PEP 621 `[project]` table (Plan 01-01 deviation, logged in `.planning/STATE.md` Decisions). Poetry 1.8.3 is incompatible with that manifest shape; it expects fields under `[tool.poetry]`.
- **Fix:** Edited Dockerfile builder stage `POETRY_VERSION=1.8.3` → `POETRY_VERSION=2.3.2` (matches both the host Poetry version that generated `poetry.lock` and the manifest format).
- **Files modified:** Dockerfile
- **Verification:** Re-ran `docker build` — succeeded; image size 167 MB.
- **Committed in:** 10af0ba (Task 1 commit, fix folded in)

**2. [Rule 3 - Blocking] Added `!.env.template` exception to .gitignore**

- **Found during:** Task 1 (preparing to commit `.env.template`)
- **Issue:** Pre-existing `.gitignore` rule `.env.*` would have prevented `.env.template` from being staged. `git check-ignore .env.template` confirmed (exit 0). Plan requires the file be committed.
- **Fix:** Added `!.env.template` immediately after the existing `!.env.example` exception line.
- **Files modified:** .gitignore
- **Verification:** `git check-ignore .env.template` → exit 1 (no longer ignored); file successfully staged and committed.
- **Committed in:** 10af0ba (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 bug fix, 1 blocking)
**Impact on plan:** Both auto-fixes were prerequisites for the plan's stated success criteria. No scope creep — the project's actual Poetry version and the `.gitignore` rule predated this plan; the deviations only adapted the spec to the existing repo state. All locked decisions (D-11, D-12, D-13, D-17) honored verbatim.

## Issues Encountered

- The `<verify>` automated check criterion `grep -ci "traefik" docker-compose.yml returns 0` is in tension with the plan's `<interfaces>` block (which explicitly includes the comment `# D-13: No Traefik labels in this repo.`). Treated `<interfaces>` as authoritative; the comment is documentation explaining the *absence* of Traefik config, not Traefik config itself. Documented in Decisions Made above. Recorded for future planner: when the verify-helper grep contradicts the verbatim spec, prefer the spec.

## Next Phase Readiness

**Phase 1 is closed.** All 4 ROADMAP success criteria verified in a single end-to-end run today (2026-05-04). The repo is now a functioning Phase-1 baseline:

- pytest green (67 tests, mocks-only, no DB integration yet)
- alembic upgrade head against fresh Postgres produces the 5-table schema with 5 seeded stores
- poetry install + poetry check both clean on Python 3.12
- docker build produces a 167 MB image from python:3.12-slim

### Phase 2 prerequisites this delivery creates

- **Network address:** `localhost:5432` for host-side alembic / Phase 2 dev tooling. App service (added Phase 2 per D-12) will connect via the compose service name `postgres:5432` once the app joins the compose stack.
- **Volume name:** `price_tracker_pg_data` (named, persistent across `docker compose down`). Phase 2 should reuse the same name to preserve dev data.
- **Container name:** `price_tracker_postgres` for `docker exec` smoke tests (used by gate verification above).
- **Env var contract:** `.env.template` already lists every Phase-2 var the runtime will need (`OPENROUTER_*`, `SMTP_*`); Phase 2 just needs to uncomment them and set values, plus wire them into `src/infra/llm.py` and `src/infra/email.py`.
- **CMD reconciliation:** The Dockerfile CMD points at `src.api.app:app` (D-11 literal). Phase 2 must either (a) add `{include="api", from="src"}` to `pyproject.toml` so `from api.app import app` works at the package path, or (b) update the CMD to `["uvicorn", "api.app:app", ...]`. Either choice is fine; document in Phase 2 plan.

### Roadmap Reassess (D-19) due

Per D-19, a roadmap reassess is scheduled now that Phase 1 is closed and before Phase 3 starts. Phase 3 (Admin UI + Entra Auth) and Phase 4 (MCP) both need rewriting to absorb the IAP topology shift (D-17, D-18):

- Phase 3 success criterion 1 currently says "Logging in via Entra with the allowed `oid` lands on the admin UI" — must be rewritten to "App reads `X-Auth-Request-Email` header set by edge oauth2-proxy and validates against `ALLOWED_ENTRA_EMAIL` env var; missing/wrong header rejected."
- Phase 4 MCP-05 (mount path decision) is now constrained: subdomain wins over path because the proxy auth-bypass is per-host, not per-path.
- REQUIREMENTS.md AUTH-01..03 need rewriting (or splitting UI auth from MCP auth).
- A new edge-proxy/portal milestone (Traefik + oauth2-proxy + Homepage) belongs in a separate project/milestone — should be acknowledged in ROADMAP.md as out-of-scope for this milestone.

The reassess is a planner activity, not an executor activity — flag it in STATE.md "Blockers/Concerns" so it surfaces at the start of Phase 2 planning.

## Self-Check: PASSED

- FOUND: Dockerfile
- FOUND: .dockerignore
- FOUND: docker-compose.yml
- FOUND: .env.template
- FOUND: commit 10af0ba (Task 1)
- FOUND: Image `price-tracker:phase1` (167 MB) verified by `docker images`
- FOUND: 5 seeded store slugs in compose-managed Postgres (verified during gate run)
- All 4 ROADMAP gates verified green and documented above

---
*Phase: 01-skeleton-domain-copy*
*Completed: 2026-05-04*
