# Phase 1: Skeleton + Domain Copy - Context

**Gathered:** 2026-05-02
**Status:** Ready for planning

<domain>
## Phase Boundary

A standalone Python project with the price-tracker domain logic ported from `ai-agent-platform`, the data model collapsed into a single tenant-scoped initial migration, and the existing test suite passing against rebased fixtures. Phase 1 produces a buildable repo whose tests are green and whose schema deploys against a fresh Postgres — no FastAPI app, no admin UI, no MCP, no Entra wiring yet. Those land in Phases 2-4.

**Verifiable gates** (from ROADMAP.md Phase 1):
1. `pytest` runs the full ported suite green against rebased fixtures
2. `alembic upgrade head` succeeds against fresh Postgres → 5 tables (`stores`, `products`, `product_stores`, `price_points`, `watches`) with `tenant_id` UUID columns and 5 seeded stores
3. `poetry install` resolves all declared dependencies on Python 3.12
4. `docker build` produces a runnable image from `python:3.12-slim`

</domain>

<decisions>
## Implementation Decisions

### Default tenant identity
- **D-01:** Hardcoded `DEFAULT_TENANT_ID: uuid.UUID = uuid.UUID("f21b6620-c793-46e3-a354-dfcd9956b4a2")` constant in a small module — proposed location `src/domain/tenant.py`. Single source of truth referenced by the migration seed, all domain queries that filter by `tenant_id`, and all tests that need to insert tenant-scoped rows.
- **D-02:** No `DEFAULT_TENANT_ID` env var. Single-user, single-deployment app — env-var indirection adds risk of mismatch between migration seed and runtime config without buying any flexibility we'll actually use.
- **D-03:** The seed migration inserts a row into a `tenants` table? **No.** There is no `tenants` table in the source schema. The source's `context_id` was an FK to the platform's `contexts` table, which we do not port. `tenant_id` becomes a free-floating UUID column with no FK — the constant IS the contract. Document this explicitly so a future reader doesn't add a phantom `tenants` table.

### Test database backend
- **D-04:** Drop `aiosqlite` from `pyproject.toml`. The five source test files (`test_parser.py`, `test_service.py`, `test_scheduler.py`, `test_notifier.py`, `test_extractors.py`) all use `unittest.mock` (`MagicMock` / `AsyncMock`) for sessions, fetchers, email, and LLM clients — there is no integration test that needs a real session in Phase 1.
- **D-05:** Schema fidelity is verified by `alembic upgrade head` against a real Postgres in `docker-compose.yml` (gate 2), not by the pytest suite. This avoids inventing sqlite type-compat shims for `postgresql.UUID` / `JSONB` / `gen_random_uuid()`.
- **D-06:** If a future phase needs real DB integration tests, use **testcontainers-python** with a Postgres image — keep prod parity, no sqlite drift.

### Squashed-migration shape
- **D-07:** Hand-derive `alembic/versions/0001_initial.py` from the final state of `src/domain/models.py`, NOT a literal concatenation of the 3 source migrations. The Alembic graph in this repo is empty; there is no historical sequence to preserve. One `upgrade()` body creates all 5 tables in their final shape (with the columns added by the two later source migrations baked in: percentage threshold, unit-price columns).
- **D-08:** Migration uses `postgresql.UUID(as_uuid=True)` and `postgresql.JSONB` directly. Postgres-only is a stated constraint (PROJECT.md).
- **D-09:** Migration body inserts 5 store rows (ICA, Willys, Apotea, Med24, Doz) with `slug`, `store_type`, `base_url`, `parser_config` populated from the source `stores/__init__.py` registry. Use `op.bulk_insert` with literal data — no fixture file indirection.
- **D-10:** No `tenants` table created (see D-03). The seed step seeds stores only.

### Dockerfile / compose scope
- **D-11:** `Dockerfile` based on `python:3.12-slim`. Multi-stage: builder installs poetry + deps; final stage copies installed venv + `src/`. CMD is `["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]` — points at a module that doesn't exist yet (Phase 2 creates it). Image builds cleanly; running it before Phase 2 will fail at import. That is acceptable — Phase 1 gate is `docker build`, not `docker run`.
- **D-12:** `docker-compose.yml` ships **postgres-only** in Phase 1. Service: `postgres:16-alpine` with healthcheck and named volume. No app service yet — added in Phase 2 when there's a real app to run.
- **D-13:** **No Traefik labels in this repo.** Routing/TLS/auth is the future edge-proxy stack's job (see project-level decision below). When Phase 2 adds the app service, it will join an external `edge` docker network for the proxy to reach it; it will NOT publish ports or carry Traefik labels itself.
- **D-14:** Phase 1 compose lets local dev do `docker compose up postgres` then `alembic upgrade head` from the host (or in CI) to verify gate 2.

### Import rebase during the "verbatim" port
- **D-15:** Source `models.py` imports `from core.db.models import Base, _utc_now`. Port creates a local `src/infra/db.py` (or `src/domain/_base.py` — planner picks) defining `Base = DeclarativeBase()` and a `_utc_now()` helper matching source semantics. Source intra-module imports `from modules.price_tracker.X` rewrite to `from domain.X` (or `from src.domain.X` — depends on package layout choice).
- **D-16:** Document the verbatim-port mapping table in the planner's PLAN.md — make it auditable that `models.py`, `service.py`, `scheduler.py`, `parser.py`, `notifier.py`, `result.py`, `extractors/*`, `stores/*` were copied with only import-path rewrites + the `context_id` → `tenant_id` column rename. If any other change is needed (e.g. SQLAlchemy 2.0 syntax adjustments), call it out explicitly per file.

### Project-level shifts captured during this discussion
- **D-17:** **Auth topology shifts to Identity-Aware Proxy (IAP).** Edge proxy (future stack) terminates Entra OIDC once and forwards `X-Auth-Request-Email` to backend apps. price-tracker reads this header and validates against `ALLOWED_ENTRA_EMAIL` env var. **Drops `fastapi-azure-auth` and `pyjwt` from `pyproject.toml`** in Phase 1.
- **D-18:** **Edge stack target: Traefik + oauth2-proxy + Homepage dashboard.** Lives in a separate future repo/milestone, NOT in price-tracker. Configuration of that stack is out of scope for this milestone.
- **D-19:** **Roadmap reassess** scheduled after Phase 1 completes, before Phase 3 starts. Phase 3 (Admin UI + Entra Auth) will be rewritten from "OIDC code flow" to "trust X-Auth-Request-Email header"; Phase 4 (MCP) will define how the MCP endpoint bypasses the IAP (likely separate `mcp.<domain>` subdomain that the proxy excludes from the auth middleware, since MCP uses its own bearer token).

### Claude's Discretion
- Concrete `pyproject.toml` dependency list and version pins (planner picks; Python 3.12 stack is locked, but minor versions are not).
- Whether `Base` / `_utc_now` live in `src/infra/db.py` or `src/domain/_base.py` (planner picks based on import-cycle considerations).
- Whether `src/` is a top-level package (`from src.domain...`) or src-layout with `domain` as the package (`from domain...`). Default to src-layout since that is the modern Poetry convention; planner can flip if it conflicts with source-import shape.
- conftest.py scope and fixture organisation (one `tests/conftest.py` vs per-area conftest).
- Dockerfile multi-stage details (which builder image, whether to use `poetry export` + pip vs poetry-in-final-stage).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Extraction plan (load-bearing source of truth)
- `EXTRACTION.md` — verified LOC counts, locked decisions, full 5-phase plan
- `EXTRACTION.md` §3 — exact source file paths and LOC for each ported file
- `EXTRACTION.md` §4 — target repo layout
- `EXTRACTION.md` §8 (Phase 1 subsection) — Phase 1 deliverables and gate
- `EXTRACTION.md` §9 — verified data model, table renames, multi-tenant column scope

### Planning artifacts
- `.planning/PROJECT.md` — Stack constraints (§Constraints), Key Decisions table
- `.planning/REQUIREMENTS.md` — v1 requirements DOMAIN-01..03, DB-01..05, TEST-01..03, DEPLOY-01..02 are in Phase 1 scope
- `.planning/ROADMAP.md` — Phase 1 success criteria

### Source code to port (relative to `/home/magnus/dev/ai-agent-platform/`)
- `services/agent/src/modules/price_tracker/` — entire directory (~2,034 LOC) ports verbatim into `src/domain/`
- `services/agent/src/modules/price_tracker/models.py` — note dependency on `core.db.models.Base` and `_utc_now` (rebase per D-15)
- `services/agent/src/modules/price_tracker/tests/` — 5 test files port into this repo's `tests/` (rebase fixtures per D-04)
- `services/agent/alembic/versions/20260115_add_price_tracker_tables.py` — main schema reference
- `services/agent/alembic/versions/20260115_add_price_drop_threshold.py` — adds `price_drop_threshold_pct` column (bake into squashed migration)
- `services/agent/alembic/versions/20260115_add_unit_price_alerts.py` — adds unit-price columns (bake into squashed migration)
- `services/agent/src/modules/price_tracker/stores/__init__.py` — store registry, source for migration seed data

### Out-of-scope-for-Phase-1 but worth knowing
- `services/agent/src/interfaces/http/admin_price_tracker.py` — ported in Phase 3, not Phase 1
- `services/agent/src/interfaces/http/templates/admin_price_tracker.html` — Phase 3
- `services/agent/src/core/tools/price_tracker.py` — replaced by MCP server in Phase 4

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Source `models.py`** uses SQLAlchemy 2.0 `Mapped[T]` / `mapped_column()` style — modern, no rewrite needed.
- **Source `models.py`** already uses `uuid.UUID` for `context_id` (FK to a UUID PK) — column rename to `tenant_id` is name-only, no type change.
- **Source `stores/__init__.py`** (66 LOC) is the canonical seed-data source for the 5 stores in the migration.
- **Source test files** use `unittest.mock` (`MagicMock`, `AsyncMock`, `Mock`) — no custom test-double base classes to port.

### Established Patterns
- **`postgresql.UUID(as_uuid=True)` + `JSONB` + `gen_random_uuid()`** are used directly in source migration — Postgres-only, locked by D-08.
- **Cascading deletes** (`Product → ProductStore[]`, `ProductStore → PricePoint[]`, `Product → Watch[]`) preserved as-is per PROJECT.md "behavioral parity" constraint.
- **Source intra-module imports** use absolute paths from `modules.price_tracker.X` — every file needs path rewriting during port.

### Integration Points
- **`Base` and `_utc_now`** must come from a new module in this repo (D-15). All ported domain modules import them.
- **`tenant_id`** column requires a default value — the seeded `DEFAULT_TENANT_ID` constant (D-01).
- **Alembic** environment must be wired to import `Base` so autogenerate sees the models — `alembic/env.py` configuration is part of Phase 1 scope.

</code_context>

<specifics>
## Specific Ideas

- The chosen `DEFAULT_TENANT_ID` is `f21b6620-c793-46e3-a354-dfcd9956b4a2` (generated 2026-05-02). Stable for the life of the repo.
- Postgres image: `postgres:16-alpine` (small, current).
- Dockerfile target image size: aim for <300 MB. Multi-stage to exclude build tools.

</specifics>

<deferred>
## Deferred Ideas

### Edge proxy + portal stack (new milestone after price-tracker v1)
- New project/repo for the edge proxy: **Traefik + oauth2-proxy + Homepage dashboard**
- Hosts on Flatcar Container Linux inside a Proxmox VM on a single mini-PC
- Owns: TLS termination, Entra OIDC, routing to backend apps, landing-page tiles
- Backend apps (price-tracker, future ai-agent-platform consolidation, etc.) join a shared `edge` docker network and trust `X-Auth-Request-Email` headers
- Subdomain strategy (`prices.<domain>`, `mcp.<domain>`, `agent.<domain>`) with wildcard cert via Let's Encrypt DNS-01
- Should be its own GSD milestone — does NOT belong inside the extraction milestone

### Phase 3 rewrite (after roadmap reassess)
- Replace OIDC code flow plan with: read `X-Auth-Request-Email` header, validate against `ALLOWED_ENTRA_EMAIL` env var, deny otherwise
- Drops auth-related deps from `pyproject.toml` (`fastapi-azure-auth`, `pyjwt`) — already actioned in Phase 1 (D-17)
- Session cookie / CSRF concerns largely vanish (proxy owns the session)
- AUTH-01..03 in REQUIREMENTS.md need to be rewritten or split (UI auth separate from MCP auth)

### Phase 4 adjustment (after roadmap reassess)
- MCP endpoint must bypass the IAP — likely separate `mcp.<domain>` subdomain that the edge proxy excludes from oauth2-proxy middleware
- AUTH-04 (static bearer token) unchanged
- MCP-05 (mount path) decision now constrained: subdomain wins over `/mcp` path because the proxy auth-bypass is per-host, not per-path (cleaner Traefik config)

### Roadmap reassess action items (when scheduled, after Phase 1)
- Update PROJECT.md Key Decisions: add IAP/portal architecture row
- Update REQUIREMENTS.md: rewrite AUTH-01..03; update INFRA/DEPLOY items that reference Traefik labels in this repo
- Update ROADMAP.md Phase 3 and Phase 4 success criteria
- Decide whether the portal/edge-proxy stack becomes a v1.1 milestone or a separate project entirely

### Reviewed Todos (not folded)
None — no pending todos matched Phase 1 scope.

</deferred>

---

*Phase: 1-Skeleton + Domain Copy*
*Context gathered: 2026-05-02*
