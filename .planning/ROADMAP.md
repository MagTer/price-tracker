# Roadmap: Price Tracker (Extraction)

## Overview

Mechanical port of ~3,400 LOC of working price-tracker code from the `ai-agent-platform` monolith into this standalone repo. Goal is **byte-equivalent feature parity** behind a new MCP boundary, then unwiring the source repo. Five phases mirror EXTRACTION.md §8: skeleton + domain copy → service infra → admin UI + Entra auth → MCP server → source-repo cleanup. Each phase has a verifiable gate; phases run sequentially because each gate is a precondition for the next phase's work (domain code before infra, infra before app shell, app shell before MCP plug-in, MCP live before source deletion is safe).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Skeleton + Domain Copy** - Repo scaffolding, domain modules ported verbatim, squashed initial migration, green test suite
- [ ] **Phase 2: Service Infrastructure** - httpx/SMTP/OpenRouter/DB clients wired into a FastAPI app whose scheduler ticks and runs a real Willys price check
- [ ] **Phase 3: Admin UI + Entra Auth** - Ported admin endpoints + HTML behind Entra OIDC single-`oid` gate, routed by Traefik to `prices.<domain>`
- [ ] **Phase 4: MCP Server + Agent Wiring** - FastMCP server with 4 tools, bearer auth, registered in the agent platform, `priser` skill answers Swedish queries via MCP
- [ ] **Phase 5: Source-repo Cleanup** - Delete price-tracker code from `ai-agent-platform`, drop `price_tracker_*` tables, verify platform deploys with `priser` still working

## Phase Details

### Phase 1: Skeleton + Domain Copy
**Goal**: A standalone Python project with the domain logic copied verbatim, the data model squashed into a single tenant-scoped initial migration, and the existing test suite passing against rebased fixtures.
**Depends on**: Nothing (first phase)
**Requirements**: DOMAIN-01, DOMAIN-02, DOMAIN-03, DB-01, DB-02, DB-03, DB-04, DB-05, TEST-01, TEST-02, TEST-03, DEPLOY-01, DEPLOY-02
**Success Criteria** (what must be TRUE):
  1. `pytest` runs the full ported suite (parser, service, scheduler, notifier, extractors) green against rebased fixtures
  2. `alembic upgrade head` succeeds against a fresh Postgres and produces tables `stores`, `products`, `product_stores`, `price_points`, `watches` with `tenant_id` UUID columns and the 5 seeded stores
  3. `poetry install` resolves all declared dependencies on Python 3.12
  4. `docker build` produces a runnable image from the `python:3.12-slim` Dockerfile
**Plans**: 5 plans across 4 waves

**Wave 1**
  - [ ] 01-01-skeleton-PLAN.md — pyproject.toml + package layout + phantom-dep targets (Base, _utc_now, protocols, tenant constant, providers stub)

**Wave 2** *(blocked on Wave 1 completion)*
  - [ ] 01-02-domain-port-PLAN.md — verbatim port of 11 domain modules with import rewrites + context_id->tenant_id + table prefix drop

**Wave 3** *(blocked on Wave 2 completion; plans within wave run in parallel)*
  - [ ] 01-03-migration-PLAN.md — alembic init + squashed 0001_initial.py (5 tables + 5 seeded stores, derived from final models.py state)
  - [ ] 01-04-tests-PLAN.md — port 5 test files with import rewrites + green pytest suite (no aiosqlite, mocks-only)

**Wave 4** *(blocked on Wave 3 completion)*
  - [ ] 01-05-docker-PLAN.md — Dockerfile + postgres-only docker-compose + .env.template + end-to-end Phase 1 gate verification

**Cross-cutting constraints** (truths shared across multiple plans):
  - `DEFAULT_TENANT_ID = uuid.UUID("f21b6620-c793-46e3-a354-dfcd9956b4a2")` (D-01) — referenced by tenant.py, migration seed step, and tests
  - `Base = DeclarativeBase()` + `_utc_now()` live in `src/infra/db.py` (D-15) — imported by models.py, migration env, and tests
  - No `tenants` table created or seeded (D-03, D-10) — enforced in migration and verified by gate-2 query
  - Verbatim port mapping table (D-16) — produced by Plan 02, audited by checker

### Phase 2: Service Infrastructure
**Goal**: Replace the source repo's interfaces (`IFetcher`, `IEmailService`, `LiteLLM proxy`, in-memory session) with concrete infra clients, wire them into a FastAPI app whose lifespan starts the scheduler, and prove the whole loop works against a real Willys URL.
**Depends on**: Phase 1
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06, API-01, DEPLOY-04
**Success Criteria** (what must be TRUE):
  1. App boots via `uvicorn` with FastAPI lifespan starting the price-check scheduler (scheduler ticks observable in logs)
  2. A manual price check on a real Willys product URL persists a `PricePoint` row end-to-end via the new fetcher, parser, and DB session
  3. Parser calls hit OpenRouter at `https://openrouter.ai/api/v1` with `Authorization: Bearer`, `HTTP-Referer`, and `X-Title` headers and the cascade model env vars are honoured
  4. `.env.template` lists every env var required by the running app (OpenRouter, SMTP, DB)
**Plans**: TBD

### Phase 3: Admin UI + Entra Auth
**Goal**: Restore the operator workflow — create products, link to stores, view price history — behind Entra OIDC with a single allowed `oid`, served on `prices.<domain>` via the existing Traefik proxy.
**Depends on**: Phase 2
**Requirements**: API-02, API-03, API-04, AUTH-01, AUTH-02, AUTH-03, DEPLOY-03
**Success Criteria** (what must be TRUE):
  1. Logging in via Entra with the allowed `oid` lands on the admin UI; any other `oid` is rejected
  2. Through the admin UI, a user creates a product, links it to Willys, triggers a check, and sees a price history row appear
  3. Session cookie issued by the auth flow has `SameSite=Strict`, `HttpOnly`, and `Secure` attributes
  4. `docker compose up` brings up postgres + app and Traefik routes `prices.<domain>` to the app on the agent-platform network
  5. Admin endpoints have CSRF middleware references removed and use the seeded `tenant_id` instead of `/me/context` / `context_id` lookups
**Plans**: TBD
**UI hint**: yes

### Phase 4: MCP Server + Agent Wiring
**Goal**: Expose the four price-tracker tools over MCP behind a static bearer token, register the server in the agent platform, and confirm the `priser` skill answers a real Swedish price query end-to-end.
**Depends on**: Phase 3
**Requirements**: MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06, MCP-07, AUTH-04
**Success Criteria** (what must be TRUE):
  1. The FastMCP server, mounted on the FastAPI app, exposes `check_price`, `find_deals`, `compare_stores`, and `list_products` and rejects calls without a valid `MCP_BEARER_TOKEN`
  2. The agent platform's `/platformadmin/mcp/` registry has this server configured and discovers all 4 tools by name
  3. `skills/general/price_tracker.md` frontmatter `tools:` lists the MCP-discovered tool names verified via the platform's test-connection flow
  4. An agent in the platform answers "Vad kostar Apotea omega-3?" by calling MCP tools against this server (no chat-tool fallback path)
**Plans**: TBD

### Phase 5: Source-repo Cleanup
**Goal**: Excise every line of price-tracker code from `ai-agent-platform`, drop the 5 `price_tracker_*` tables via a downgrade migration, and confirm the platform still deploys clean with `priser` routing through MCP.
**Depends on**: Phase 4 (MCP must be live and `priser` verified end-to-end before deleting source paths)
**Requirements**: CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-04
**Success Criteria** (what must be TRUE):
  1. All 8 source paths listed in CLEAN-01 are deleted from `ai-agent-platform` and the 5 config files in CLEAN-02 no longer reference price tracker
  2. A new Alembic downgrade migration in `ai-agent-platform` drops `price_tracker_stores`, `price_tracker_products`, `price_tracker_product_stores`, `price_tracker_price_points`, `price_tracker_watches`
  3. `stack check` is green in `ai-agent-platform` and the platform deploys without `PriceCheckScheduler` initialization
  4. After cleanup, the `priser` skill in the agent platform still answers a Swedish price query (now via MCP into this repo)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute strictly in numeric order: 1 → 2 → 3 → 4 → 5. Each phase's gate is a precondition for the next phase's work, so phases cannot parallelize. Plans within a phase MAY parallelize per `parallelization=true` in config.

**Branching:**
`branching_strategy=milestone` — all 5 phases run on a single milestone branch.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Skeleton + Domain Copy | 0/5 | Not started | - |
| 2. Service Infrastructure | 0/TBD | Not started | - |
| 3. Admin UI + Entra Auth | 0/TBD | Not started | - |
| 4. MCP Server + Agent Wiring | 0/TBD | Not started | - |
| 5. Source-repo Cleanup | 0/TBD | Not started | - |
