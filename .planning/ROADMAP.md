# Roadmap: Price Tracker (Extraction)

## Overview

Mechanical port of ~3,400 LOC of working price-tracker code from the `ai-agent-platform` monolith into this standalone repo. Goal is **byte-equivalent feature parity** behind a new MCP boundary, then unwiring the source repo. Five phases mirror EXTRACTION.md §8: skeleton + domain copy → service infra → admin UI + IAP header trust → MCP server → source-repo cleanup. Each phase has a verifiable gate; phases run sequentially because each gate is a precondition for the next phase's work (domain code before infra, infra before app shell, app shell before MCP plug-in, MCP live before source deletion is safe).

**Auth topology (locked 2026-05-02 in 01-CONTEXT.md, reassessed 2026-05-04; ingress hosting reassessed 2026-07-06 per D-20; ingress LIVE since 2026-07-09):** This repo does NOT terminate Entra OIDC. An upstream Identity-Aware Proxy (Traefik + oauth2-proxy v7.15.2 forwardAuth, operated within Dokploy's managed scope per D-20) terminates OIDC once at the edge and forwards `X-Auth-Request-Email` (email claim = `preferred_username`/UPN) to the app host `price.<domain>`. The MCP endpoint is served on the **same host** at `price.<domain>/mcp/` via a path-scoped, un-gated Traefik router (priority bypass, NOT a `mcp.<domain>` subdomain — D-29 supersedes D-18) and authenticates purely with `MCP_BEARER_TOKEN`. Phase 3 reads the header; Phase 4 wires the bearer-only `/mcp` route (route prepared in the home-server repo; only agent-platform registration remains).

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Skeleton + Domain Copy** - Repo scaffolding, domain modules ported verbatim, squashed initial migration, green test suite
- [x] **Phase 2: Service Infrastructure** - httpx/SMTP/OpenRouter/DB clients wired into a FastAPI app whose scheduler ticks and runs a real Willys price check
- [x] **Phase 3: Admin UI + IAP Header Trust** - Ported admin endpoints + HTML behind upstream IAP. App reads `X-Auth-Request-Email`, validates against `ALLOWED_ENTRA_EMAIL`, denies otherwise. No OIDC client in this repo; routed to `prices.<domain>` by the external edge proxy
- [ ] **Phase 4: MCP Server + Agent Wiring** - FastMCP server with 4 tools built and tested (bearer auth fail-closed); `price.<domain>/mcp/` path-route prepared in the home-server repo (D-29). Remaining gap is ONLY the agent-platform Hermes registration (`/platformadmin/mcp/` in ai-agent-platform)
- [ ] **Phase 04.1: Package data moves to the store link** (INSERTED) - `package_size` + `package_quantity` move `Product` -> `ProductStore`; `unit` stays on `Product`. Product = abstract good, link = concrete package listing. Unlocks "cheapest kr/unit across pack sizes and stores" as one product view
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

  - [x] 01-01-skeleton-PLAN.md — pyproject.toml + package layout + phantom-dep targets (Base, _utc_now, protocols, tenant constant, providers stub)

**Wave 2** *(blocked on Wave 1 completion)*

  - [x] 01-02-domain-port-PLAN.md — verbatim port of 11 domain modules with import rewrites + context_id->tenant_id + table prefix drop

**Wave 3** *(blocked on Wave 2 completion; plans within wave run in parallel)*

  - [x] 01-03-migration-PLAN.md — alembic init + squashed 0001_initial.py (5 tables + 5 seeded stores, derived from final models.py state)
  - [x] 01-04-tests-PLAN.md — port 5 test files with import rewrites + green pytest suite (no aiosqlite, mocks-only)

**Wave 4** *(blocked on Wave 3 completion)*

  - [x] 01-05-docker-PLAN.md — Dockerfile + postgres-only docker-compose + .env.template + end-to-end Phase 1 gate verification

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

### Phase 3: Admin UI + IAP Header Trust

**Goal**: Restore the operator workflow — create products, link to stores, view price history — behind the upstream Identity-Aware Proxy (terminating Entra OIDC outside this repo). The app reads `X-Auth-Request-Email`, validates against `ALLOWED_ENTRA_EMAIL`, and denies otherwise. Served on `prices.<domain>` by the external edge proxy.
**Depends on**: Phase 2
**Requirements**: API-02, API-03, API-04, AUTH-01, AUTH-02, AUTH-03, DEPLOY-03
**Success Criteria** (what must be TRUE):

  1. A request carrying `X-Auth-Request-Email: <ALLOWED_ENTRA_EMAIL>` lands on the admin UI; any other value (or missing header) returns HTTP 403 from a single FastAPI dependency that wraps the admin router
  2. Through the admin UI, the operator creates a product, links it to Willys, triggers a check, and sees a price history row appear
  3. App carries no `fastapi-azure-auth`, `authlib`, `pyjwt`, or session-cookie code; CSRF middleware is removed (the IAP + IAP-only-routable network is the trust boundary). Verifiable via `grep -rE "fastapi-azure-auth|authlib|pyjwt|csrf" pyproject.toml src/` returning zero matches
  4. `docker compose up` brings up postgres + app on the external `edge` docker network without publishing app ports to the host; the upstream proxy (separate repo) routes `prices.<domain>` → this app
  5. Admin endpoints use the seeded `DEFAULT_TENANT_ID` instead of `/me/context` / `context_id` lookups

**Plans**: TBD
**UI hint**: yes

### Phase 4: MCP Server + Agent Wiring

**Goal**: Expose the four price-tracker tools over MCP at `price.<domain>/mcp/` — a path-scoped Traefik router (explicit priority) that bypasses the Entra forwardAuth gate for `/mcp` only, on the same host as the portal (D-29 supersedes D-18's per-host subdomain plan). The MCP route authenticates purely with `MCP_BEARER_TOKEN` (fail-closed 503 without it). Register the server in the agent platform and confirm `priser` answers a real Swedish price query end-to-end.
**Depends on**: Phase 3
**Requirements**: MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06, MCP-07, AUTH-04
**Success Criteria** (what must be TRUE):

  1. The FastMCP server, mounted on the FastAPI app, exposes `check_price`, `find_deals`, `compare_stores`, and `list_products` and rejects calls without a valid `MCP_BEARER_TOKEN`
  2. The MCP endpoint is reachable at `price.<domain>/mcp/` (path route, not a subdomain — D-29) — verifiable from the agent-platform host with a bearer-only request that returns 200 and never sees an IAP redirect
  3. The agent platform's `/platformadmin/mcp/` registry has this server configured and discovers all 4 tools by name; `skills/general/price_tracker.md` frontmatter `tools:` lists the MCP-discovered tool names verified via the platform's test-connection flow
  4. An agent in the platform answers "Vad kostar Apotea omega-3?" by calling MCP tools against this server (no chat-tool fallback path)

**Plans**: TBD

### Phase 04.1: Package data moves to the store link (INSERTED)

**Goal**: Move `package_size` + `package_quantity` from `Product` to `ProductStore` so that the link (the store URL) owns the package data it actually describes. `unit` stays on `Product`. Product becomes the abstract good (name, brand, category, unit); the link becomes the concrete package listing. This makes the app's core question answerable — "cheapest kr/unit for Lambi across pack sizes and stores" becomes a single product view with all links sorted by unit price, where today three pack sizes are three unrelated products with no grouping key.
**Depends on**: Nothing in-repo (independent of Phase 4's agent-platform wiring, which is external). Sequenced before Phase 5 so the source-repo cleanup deletes against a settled model.
**Requirements**: New — captured in `.planning/SEED-package-data-moves-to-link.md` (decision, rejected `Variant` alternative, touch-point inventory)
**Success Criteria** (what must be TRUE):
  1. Creating a product asks for name / brand / category / **unit only** — no package fields
  2. Creating or editing a link asks for amount in the product's unit, with an auto-suggested editable package label (the v0.2.1 guided chain moves from the product dialog to the link dialog; no unit selector needed there)
  3. A product page shows all its links with package label, current price, and **kr/unit, sortable** — the toilet-paper question answered on one screen
  4. A scraper run against a real store page fills or verifies the link's `package_quantity` (a mismatch between page and stored quantity is signal, not noise)
  5. `alembic upgrade head` migrates a fresh DB cleanly and the full test suite is green
**Plans**: TBD

**Constraints:**
  - DB is **empty** — no data rescue needed. A clean new revision is fine; collapsing/resetting the revision chain is sanctioned (operator prefers fresh-start over compat in test-stage systems)
  - Do **not** reintroduce a middle `Variant` entity — considered and deliberately rejected as over-modeling for a home tool

Plans:

- [ ] TBD (run /gsd-plan-phase 04.1 to break down)

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
| 1. Skeleton + Domain Copy | 5/5 | Complete | 2026-05-04 |
| 2. Service Infrastructure | 1/1 | Complete | 2026-05-22 |
| 3. Admin UI + Entra Auth | 1/1 | Complete | 2026-05-22 |
| 4. MCP Server + Agent Wiring | 1/1 | Gaps Found | 2026-07-06 (retroactive verification) |
| 04.1. Package data moves to the store link (INSERTED) | 0/TBD | Not started | - |
| 5. Source-repo Cleanup | 0/TBD | Not started | - |
