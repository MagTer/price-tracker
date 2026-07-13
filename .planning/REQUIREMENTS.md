# Requirements: Price Tracker (Extraction)

**Defined:** 2026-05-01
**Core Value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.

## v1 Requirements

Requirements for the extraction milestone. Goal is **byte-equivalent feature parity** in a standalone repo — improvements come after.

### Domain

- [ ] **DOMAIN-01**: All domain modules (`models.py`, `service.py`, `scheduler.py`, `parser.py`, `notifier.py`, `result.py`) ported byte-equivalent from source `modules/price_tracker/` to `src/domain/`
- [ ] **DOMAIN-02**: Extractors (`extractors/base.py`, `extractors/willys_api.py`) ported to `src/domain/extractors/`
- [ ] **DOMAIN-03**: Stores helper (`stores/__init__.py`) ported to `src/domain/stores/`

### Database

- [ ] **DB-01**: Three source migrations squashed into a single `alembic/versions/0001_initial.py` against the empty target Alembic graph
- [ ] **DB-02**: Schema uses non-null `tenant_id` UUID column on tenant-scoped tables (replaces source's `context_id` FK to the `contexts` table)
- [ ] **DB-03**: Initial migration seeds the 5 stores (ICA, Willys, Apotea, Med24, Doz) with `slug`, `store_type`, `base_url`, `parser_config`. **No `tenants` table is created or seeded** — `tenant_id` is a free-floating UUID column (`DEFAULT_TENANT_ID` constant in `src/domain/tenant.py` is the contract; D-03 / D-10 in `01-CONTEXT.md`)
- [ ] **DB-04**: Tables renamed without the `price_tracker_` prefix: `stores`, `products`, `product_stores`, `price_points`, `watches`
- [ ] **DB-05**: `alembic upgrade head` succeeds against a fresh Postgres database

### Tests

- [ ] **TEST-01**: Five source test files (`test_parser.py`, `test_service.py`, `test_scheduler.py`, `test_notifier.py`, `test_extractors.py`, ~1,800 LOC total) ported to `tests/`
- [ ] **TEST-02**: Each test file constructs its own `unittest.mock` (`MagicMock` / `AsyncMock`) doubles for sessions, fetcher, email, and LLM clients inline — **no `tests/conftest.py`** (source had none; D-04 in `01-CONTEXT.md` drops `aiosqlite` and any in-memory async session shim)
- [ ] **TEST-03**: Full `pytest` suite green

### Infrastructure

- [ ] **INFRA-01**: HTTP client `src/infra/fetcher.py` (httpx async wrapper with timeouts) replaces source's `IFetcher` interface
- [ ] **INFRA-02**: Email client `src/infra/email.py` (aiosmtplib SMTP) replaces source's `IEmailService` interface
- [ ] **INFRA-03**: LLM client `src/infra/llm.py` calls OpenRouter directly (OpenAI-compatible base URL `https://openrouter.ai/api/v1`); replaces LiteLLM proxy
- [ ] **INFRA-04**: OpenRouter request headers include `Authorization: Bearer`, `HTTP-Referer`, and `X-Title`
- [ ] **INFRA-05**: Async DB session factory `src/infra/db.py` with configured engine
- [ ] **INFRA-06**: Manual price check on a Willys URL succeeds end-to-end via the new infra

### API

- [ ] **API-01**: FastAPI app factory `src/api/app.py` starts the price-check scheduler via lifespan
- [ ] **API-02**: All 14+ admin endpoints from source's `admin_price_tracker.py` (1,689 LOC) ported to `src/api/admin.py`, CSRF middleware references dropped, `/me/context` and `context_id` lookups replaced with the seeded tenant
- [ ] **API-03**: Admin HTML template `src/api/templates/admin.html` ported from source (953 LOC) with updated API base path and CSRF token plumbing removed from JS fetch calls
- [ ] **API-04**: Pydantic request/response schemas ported from source `schemas/price_tracker.py` (135 LOC) as-is to `src/api/schemas.py`

### Authentication

- [ ] **AUTH-01**: Admin UI trusts the `X-Auth-Request-Email` header forwarded by the upstream Identity-Aware Proxy (IAP). Entra OIDC is terminated at the proxy — price-tracker does NOT carry `fastapi-azure-auth`, `authlib`, `pyjwt`, or any OIDC client (D-17 in `01-CONTEXT.md`)
- [ ] **AUTH-02**: Only the email matching `ALLOWED_ENTRA_EMAIL` env var is admitted; all other emails (and missing-header requests) get HTTP 403. Implemented as a small FastAPI dependency, not as middleware reading session cookies
- [ ] **AUTH-03**: Sessions are owned by the upstream IAP (oauth2-proxy). price-tracker does not issue, parse, or set session cookies — it is a stateless backend behind the proxy. CSRF middleware is removed because the proxy + IAP-only-routable network is the trust boundary
- [ ] **AUTH-04**: Static `MCP_BEARER_TOKEN` validates calls to the MCP endpoint. Per D-18, the MCP subdomain is excluded from the IAP middleware so MCP traffic can authenticate purely with this bearer

### MCP

- [ ] **MCP-01**: FastMCP server `src/mcp/server.py` exposes `check_price(product_name: str)` returning a markdown summary of prices across stores
- [ ] **MCP-02**: FastMCP server exposes `find_deals(store_type?: "grocery" | "pharmacy")` returning a list of current offers
- [ ] **MCP-03**: FastMCP server exposes `compare_stores(product_name: str)` returning a side-by-side price comparison
- [ ] **MCP-04**: FastMCP server exposes `list_products()` returning the inventory of tracked products
- [ ] **MCP-05**: MCP endpoint mounted on the FastAPI app at `/mcp/` and exposed at `price.<domain>/mcp/` via a path-scoped, un-gated Traefik router with explicit priority over the Entra-gated host router (D-29 in STATE.md supersedes D-18's `mcp.<domain>` subdomain plan — with Traefik forwardAuth the auth-bypass is per-router, so a path route on the same host suffices and saves a DNS record + cert). The `/mcp` route is prepared in the home-server repo
- [ ] **MCP-06**: Agent platform's `/platformadmin/mcp/` server registry points at this server; `skills/general/price_tracker.md` `tools:` frontmatter updated to MCP-discovered tool names
- [ ] **MCP-07**: Agent in the platform answers a Swedish price query (e.g. "Vad kostar Apotea omega-3?") end-to-end via MCP

### Deployment

- [ ] **DEPLOY-01**: `pyproject.toml` (poetry, Python 3.12) declares all required dependencies. Phase 1 minimum: sqlalchemy[asyncio], alembic, asyncpg, pydantic, pytest, pytest-asyncio. Phase 2 adds: fastapi, uvicorn, httpx, aiosmtplib, fastmcp. **Excluded** (per D-04 / D-17 in `01-CONTEXT.md`): `aiosqlite` (mocks-only tests), `fastapi-azure-auth`, `pyjwt`, `cryptography` (IAP terminates OIDC at the proxy)
- [ ] **DEPLOY-02**: `Dockerfile` based on `python:3.12-slim` builds and runs the app
- [ ] **DEPLOY-03**: `docker-compose.yml` defines postgres + app. The app service joins an external `edge` docker network so the upstream proxy (separate repo per D-18) can reach it. **No Traefik labels in this repo** (D-13 in `01-CONTEXT.md`) — routing/TLS/auth live in the edge-proxy stack. App container does NOT publish ports to the host
- [ ] **DEPLOY-04**: `.env.template` documents all required env vars (DATABASE_URL, OpenRouter API key + model cascade, `ALLOWED_ENTRA_EMAIL`, `MCP_BEARER_TOKEN`, SMTP credentials). No OIDC client secrets — IAP owns those

### Source-repo Cleanup

- [ ] **CLEAN-01**: Delete from `ai-agent-platform`: `services/agent/src/modules/price_tracker/` (entire dir, including tests), `services/agent/src/orchestrator/price_tracker.py`, `services/agent/src/interfaces/http/admin_price_tracker.py`, `services/agent/src/interfaces/http/templates/admin_price_tracker.html`, `services/agent/src/interfaces/http/templates/price_tracker_dashboard.html`, `services/agent/src/core/tools/price_tracker.py`, `services/agent/src/core/protocols/price_tracker.py`, `services/agent/src/interfaces/http/schemas/price_tracker.py`
- [ ] **CLEAN-02**: Edit in `ai-agent-platform`: remove `price_tracker` tool entry from `services/agent/config/tools.yaml`; remove `price_tracker` and `price_tracker_fallback` aliases from `services/agent/config/models.yaml`; remove "Price Tracker" nav item from `services/agent/src/interfaces/http/admin_shared.py`; remove `admin_price_tracker_router` registration from `services/agent/src/interfaces/http/app.py`; remove `PriceCheckScheduler` initialization and `set_price_tracker()` call from `services/agent/src/orchestrator/startup.py`
- [ ] **CLEAN-03**: New Alembic downgrade migration in `ai-agent-platform` drops the 5 `price_tracker_*` tables
- [ ] **CLEAN-04**: `stack check` green in `ai-agent-platform`; the platform deploys; `priser` skill still works (now via MCP)

## v2 Requirements

Deferred to post-extraction backlog. Captured for visibility, not in current roadmap.

### Reliability & Observability

- **REL-01**: Retry/backoff on extraction failures
- **REL-02**: Persist `raw_response` JSONB on failed extractions (currently nullable, lost on failure)
- **REL-03**: Soft-delete or audit trail (cascading deletes currently wipe history)
- **REL-04**: Rate limiting on manual `/check` endpoint
- **REL-05**: Tighten fallback model confidence threshold above 0.0

### Extraction Quality

- **EXT-01**: Structured API extractors for ICA, Apotea, Med24, Doz (currently LLM-dependent)
- **EXT-02**: Product deduplication

### Notifications & Analytics

- **NOTF-01**: Telegram or push notifications (currently email-only)
- **ANAL-01**: Price trend / volatility / "is this a good deal" analytics

### Internationalization

- **I18N-01**: Externalize hardcoded `sv-SE` strings (store hints, prompts, email copy)

### Edge Proxy / Portal Stack (separate milestone or repo)

- **EDGE-01**: Traefik + oauth2-proxy + Homepage dashboard ingress, operated within Dokploy's managed scope (not a standalone hand-built stack). **LIVE in production since 2026-07-09** (oauth2-proxy v7.15.2 + Traefik forwardAuth `entra-auth@file`). Owns: TLS termination (Let's Encrypt DNS-01 wildcard), Entra OIDC, routing to backend apps, landing-page tiles. Backend apps (price-tracker, future ai-agent-platform consolidation, etc.) join a shared `edge` docker network and trust `X-Auth-Request-Email` headers (D-18 in `01-CONTEXT.md`; hosting reassessed 2026-07-06 per D-20). Host strategy: `price.<domain>` (portal + API at root, MCP at `/mcp/` via a path-scoped un-gated router — no `mcp.<domain>` subdomain, D-29). **Does NOT belong inside the price-tracker extraction milestone** — ingress is managed via Dokploy, not built as part of this repo.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Multi-tenant routing/UI | Single-user app; `tenant_id` column kept for future, but no routing or UI for it |
| Data migration from source DB | Source DB has no production data; new repo starts empty |
| LiteLLM proxy | Single app; OpenAI-compatible OpenRouter direct is sufficient |
| SPA framework (React/Vue) | Single user; vanilla JS + server-rendered HTML preserved from source |
| New extractor implementations during extraction | Port what exists; quality improvements are v2 |
| Behavioral changes during extraction | Byte-equivalent parity is the goal; improvements are v2 |
| Tightening cascading deletes | Existing behavior preserved during extraction |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DOMAIN-01 | Phase 1 | Complete |
| DOMAIN-02 | Phase 1 | Complete |
| DOMAIN-03 | Phase 1 | Complete |
| DB-01 | Phase 1 | Complete |
| DB-02 | Phase 1 | Complete |
| DB-03 | Phase 1 | Complete |
| DB-04 | Phase 1 | Complete |
| DB-05 | Phase 1 | Complete |
| TEST-01 | Phase 1 | Complete |
| TEST-02 | Phase 1 | Complete |
| TEST-03 | Phase 1 | Complete |
| DEPLOY-01 | Phase 1 | Complete |
| DEPLOY-02 | Phase 1 | Complete |
| INFRA-01 | Phase 2 | Complete |
| INFRA-02 | Phase 2 | Complete |
| INFRA-03 | Phase 2 | Complete |
| INFRA-04 | Phase 2 | Complete |
| INFRA-05 | Phase 2 | Complete |
| INFRA-06 | Phase 2 | Partial |
| API-01 | Phase 2 | Complete |
| DEPLOY-04 | Phase 2 | Complete |
| API-02 | Phase 3 | Complete |
| API-03 | Phase 3 | Complete |
| API-04 | Phase 3 | Complete |
| AUTH-01 | Phase 3 | Complete |
| AUTH-02 | Phase 3 | Complete |
| AUTH-03 | Phase 3 | Complete |
| DEPLOY-03 | Phase 3 | Complete |
| MCP-01 | Phase 4 | Complete |
| MCP-02 | Phase 4 | Complete |
| MCP-03 | Phase 4 | Complete |
| MCP-04 | Phase 4 | Complete |
| MCP-05 | Phase 4 | Partial |
| MCP-06 | Phase 4 | Pending |
| MCP-07 | Phase 4 | Pending |
| AUTH-04 | Phase 4 | Complete |
| CLEAN-01 | Phase 5 | Pending |
| CLEAN-02 | Phase 5 | Pending |
| CLEAN-03 | Phase 5 | Pending |
| CLEAN-04 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 40 total
- Mapped to phases: 40 (100%)
- Unmapped: 0

**Per-phase counts:**
- Phase 1 (Skeleton + Domain Copy): 13 requirements
- Phase 2 (Service Infrastructure): 8 requirements
- Phase 3 (Admin UI + Entra Auth): 7 requirements
- Phase 4 (MCP Server + Agent Wiring): 8 requirements
- Phase 5 (Source-repo Cleanup): 4 requirements

---
*Requirements defined: 2026-05-01*
*Last updated: 2026-05-04 — D-19 roadmap reassess after Phase 1 close: AUTH-01..04 rewritten for IAP header trust (was Entra OIDC code flow); DEPLOY-01/03/04 updated to drop in-repo Traefik/OIDC libs; MCP-05 pinned to subdomain; DB-03 / TEST-02 wording aligned with locked decisions; EDGE-01 added to v2 backlog as separate milestone*
*Last updated: 2026-07-06 — D-20 reassess: EDGE-01 hosting corrected from a standalone hand-built VM to Dokploy-managed ingress (Traefik + oauth2-proxy architecture unchanged); still out of the price-tracker extraction milestone*
*2026-07-06 D-21: Retroactive backfill of Phases 2-4 GSD tracking artifacts surfaced that MCP-05 ("mounted on FastAPI AND exposed via dedicated mcp.<domain> subdomain") and INFRA-06 ("manual Willys price check succeeds end-to-end") were marked Complete without independent live verification. Both changed to Partial — the code-level half of each is done and tested, but the subdomain-ingress half (MCP-05) and live-network half (INFRA-06) are not proven. MCP-06/MCP-07 were already correctly Pending.*
