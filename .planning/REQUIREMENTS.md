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
- [ ] **DB-03**: Initial migration seeds a default tenant row and the 5 stores (ICA, Willys, Apotea, Med24, Doz) with `slug`, `store_type`, `base_url`, `parser_config`
- [ ] **DB-04**: Tables renamed without the `price_tracker_` prefix: `stores`, `products`, `product_stores`, `price_points`, `watches`
- [ ] **DB-05**: `alembic upgrade head` succeeds against a fresh Postgres database

### Tests

- [ ] **TEST-01**: Five source test files (`test_parser.py`, `test_service.py`, `test_scheduler.py`, `test_notifier.py`, `test_extractors.py`, ~1,800 LOC total) ported to `tests/`
- [ ] **TEST-02**: Fixtures rebased: `MockLLMClient`, `InMemoryAsyncSession`, `IFetcher`/`IEmailService` mocks replaced with new equivalents in `conftest.py`
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

- [ ] **AUTH-01**: Entra OIDC code flow (via `fastapi-azure-auth` or `authlib`) protects the admin UI
- [ ] **AUTH-02**: Only the `oid` claim matching `ALLOWED_ENTRA_OID` env var is admitted; all others rejected
- [ ] **AUTH-03**: Session cookie is `SameSite=Strict`, `HttpOnly`, `Secure`
- [ ] **AUTH-04**: Static `MCP_BEARER_TOKEN` validates calls to the MCP endpoint

### MCP

- [ ] **MCP-01**: FastMCP server `src/mcp/server.py` exposes `check_price(product_name: str)` returning a markdown summary of prices across stores
- [ ] **MCP-02**: FastMCP server exposes `find_deals(store_type?: "grocery" | "pharmacy")` returning a list of current offers
- [ ] **MCP-03**: FastMCP server exposes `compare_stores(product_name: str)` returning a side-by-side price comparison
- [ ] **MCP-04**: FastMCP server exposes `list_products()` returning the inventory of tracked products
- [ ] **MCP-05**: MCP endpoint mounted on the FastAPI app (path or subdomain decided during the MCP phase)
- [ ] **MCP-06**: Agent platform's `/platformadmin/mcp/` server registry points at this server; `skills/general/price_tracker.md` `tools:` frontmatter updated to MCP-discovered tool names
- [ ] **MCP-07**: Agent in the platform answers a Swedish price query (e.g. "Vad kostar Apotea omega-3?") end-to-end via MCP

### Deployment

- [ ] **DEPLOY-01**: `pyproject.toml` (poetry, Python 3.12) declares all required dependencies (fastapi, uvicorn, sqlalchemy[asyncio], alembic, asyncpg, httpx, pydantic, aiosmtplib, cryptography, pyjwt, fastmcp, fastapi-azure-auth, pytest, pytest-asyncio, aiosqlite)
- [ ] **DEPLOY-02**: `Dockerfile` based on `python:3.12-slim` builds and runs the app
- [ ] **DEPLOY-03**: `docker-compose.yml` defines postgres + app with Traefik labels routing `prices.<domain>` to the app
- [ ] **DEPLOY-04**: `.env.template` documents all required env vars (OpenRouter, Entra, MCP token, SMTP)

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

Empty initially. Populated when roadmap is created.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (filled by roadmapper) | | |

**Coverage:**
- v1 requirements: 36 total
- Mapped to phases: 0 (pending roadmap)
- Unmapped: 36 ⚠️

---
*Requirements defined: 2026-05-01*
*Last updated: 2026-05-01 after initial definition from EXTRACTION.md*
