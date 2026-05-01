# Price Tracker Extraction Brief

This document is the handoff for extracting the `price_tracker` feature out of the `ai-agent-platform` monolith into this standalone repo.

It is written to be self-sufficient — a fresh Claude Code session reading only this file should have everything needed to start `/plan`.

**Source repo:** `/home/magnus/dev/ai-agent-platform`
**Target repo:** `/home/magnus/dev/price-tracker` (this repo)
**Date:** 2026-05-01
**Decided by:** Magnus

---

## 1. Goal

Move the price tracker feature from the AI agent platform monolith into a standalone web app that:
- Tracks Swedish grocery and pharmacy prices (ICA, Willys, Apotea, Med24, Doz)
- Exposes its capabilities to the agent platform via an **MCP server** (consumed through the platform's existing per-context MCP infrastructure at `/platformadmin/mcp/`)
- Has its own admin UI for managing products and watches
- Sends email alerts on price drops and weekly summaries
- Is hosted alongside the agent platform behind the same Traefik proxy

After extraction, the price tracker code is fully removed from the agent platform. The platform retains only the `priser` skill, which routes to MCP-discovered tools from this new server.

---

## 2. Locked-in decisions

| Decision | Choice | Rationale |
|---|---|---|
| Multi-tenancy | Single-user (Magnus only) | Simplest auth path; future-proof column kept |
| Auth | Entra ID OIDC, single allowed `oid` | Matches existing platform auth |
| TLS / routing | Traefik (existing instance) | Already in place |
| Agent integration | MCP server with bearer token | Cleaner than service-to-service REST |
| Data migration | None — start with empty DB | Existing DB has no production data |
| LLM provider | OpenRouter direct (no LiteLLM proxy) | Single app doesn't need a routing hub |
| Stack | Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + FastMCP | Reuses ~3,400 LOC of working code |
| Frontend | Server-rendered HTML + vanilla JS (port from existing) | Single user; SPA framework not justified |
| Email backend | SMTP via `aiosmtplib` (or AWS SES if preferred) | Pluggable — pick during Phase 2 |

---

## 3. Source code inventory (verified line counts)

All paths are relative to `/home/magnus/dev/ai-agent-platform/`.

### Module to copy verbatim into `src/domain/`
| File | LOC |
|---|---|
| `services/agent/src/modules/price_tracker/__init__.py` | 30 |
| `services/agent/src/modules/price_tracker/models.py` | 161 |
| `services/agent/src/modules/price_tracker/service.py` | 590 |
| `services/agent/src/modules/price_tracker/scheduler.py` | 487 |
| `services/agent/src/modules/price_tracker/parser.py` | 226 |
| `services/agent/src/modules/price_tracker/notifier.py` | 333 |
| `services/agent/src/modules/price_tracker/result.py` | 19 |
| `services/agent/src/modules/price_tracker/extractors/base.py` | 20 |
| `services/agent/src/modules/price_tracker/extractors/willys_api.py` | 101 |
| `services/agent/src/modules/price_tracker/extractors/__init__.py` | 1 |
| `services/agent/src/modules/price_tracker/stores/__init__.py` | 66 |
| **Module subtotal** | **~2,034** |

### Tests to copy into `tests/`
Located under `services/agent/src/modules/price_tracker/tests/`:
- `test_parser.py`
- `test_service.py`
- `test_scheduler.py`
- `test_notifier.py`
- `test_extractors.py`

~1,800 LOC total. Fixture rebasing required (replace platform's `MockLLMClient`, `InMemoryAsyncSession`, `IFetcher`/`IEmailService` mocks with new equivalents).

### Admin UI to port into `src/api/`
| File | LOC | Notes |
|---|---|---|
| `services/agent/src/interfaces/http/admin_price_tracker.py` | 1,689 | 14+ FastAPI endpoints. Strip CSRF, replace Entra integration. |
| `services/agent/src/interfaces/http/templates/admin_price_tracker.html` | 953 | Vanilla JS + modals + Chart.js. Update API base path. |
| `services/agent/src/interfaces/http/templates/price_tracker_dashboard.html` | (verify) | Second template — check whether still used. |
| `services/agent/src/interfaces/http/schemas/price_tracker.py` | 135 | Pydantic request/response models. Copy as-is. |

### Migrations to rebase
Under `services/agent/alembic/versions/`:
- `20260115_add_price_tracker_tables.py` — main schema + seed stores
- `20260115_add_price_drop_threshold.py` — adds percentage threshold column
- `20260115_add_unit_price_alerts.py` — adds unit price comparison columns

Squash into a single initial migration in this repo's empty Alembic graph. Replace `context_id` FK to `contexts` table with a non-null `tenant_id` UUID column (default to a single seeded tenant — keeps the door open if you ever go multi-tenant).

### NOT copied (reference only)
- `services/agent/src/core/tools/price_tracker.py` (251 LOC) — the chat tool. Replaced by MCP server in this repo.
- `services/agent/src/core/protocols/price_tracker.py` (82 LOC) — internal protocol; no longer needed.
- `services/agent/src/orchestrator/price_tracker.py` (20 LOC) — re-exports; not needed.
- `skills/general/price_tracker.md` (56 LOC) — stays in agent platform; updated to reference MCP-discovered tools.

---

## 4. Target repo layout

```
price-tracker/
├── pyproject.toml              # poetry, Python 3.12
├── poetry.lock
├── Dockerfile
├── docker-compose.yml          # postgres + app, Traefik labels
├── .env.template
├── alembic.ini
├── alembic/
│   └── versions/
│       └── 0001_initial.py     # squashed from 3 source migrations
├── src/
│   ├── domain/                 # was modules/price_tracker/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── service.py
│   │   ├── scheduler.py
│   │   ├── parser.py
│   │   ├── notifier.py
│   │   ├── result.py
│   │   ├── extractors/
│   │   │   ├── base.py
│   │   │   └── willys_api.py
│   │   └── stores/
│   │       └── __init__.py
│   ├── api/
│   │   ├── app.py              # FastAPI factory + lifespan (starts scheduler)
│   │   ├── auth.py             # Entra OIDC + single-user gate
│   │   ├── admin.py            # ported admin endpoints
│   │   ├── schemas.py
│   │   └── templates/
│   │       └── admin.html
│   ├── mcp/
│   │   └── server.py           # FastMCP, bearer auth, 4 tools
│   └── infra/
│       ├── fetcher.py          # httpx async wrapper (replaces IFetcher)
│       ├── email.py            # aiosmtplib (replaces IEmailService)
│       ├── llm.py              # OpenRouter client (replaces LiteLLM proxy)
│       └── db.py               # async session factory
└── tests/
    ├── conftest.py             # rebased fixtures
    ├── test_parser.py
    ├── test_service.py
    ├── test_scheduler.py
    ├── test_notifier.py
    └── test_extractors.py
```

---

## 5. MCP server surface

The MCP server exposes 4 tools that mirror the chat tool's current actions. Naming should match closely so the agent platform's `priser` skill needs minimal frontmatter changes.

| MCP tool | Args | Returns |
|---|---|---|
| `check_price` | `product_name: str` | Markdown summary of prices across stores |
| `find_deals` | `store_type?: "grocery" \| "pharmacy"` | List of current offers |
| `compare_stores` | `product_name: str` | Side-by-side price comparison |
| `list_products` | — | Inventory of tracked products |

**Auth:** static bearer token in `MCP_BEARER_TOKEN` env var. Set the same value in the agent platform's `/platformadmin/mcp/` server config.

**Mount:** `/mcp` path on the same FastAPI app, OR a separate Traefik subdomain. Decide during Phase 4.

**Library:** [`fastmcp`](https://gofastmcp.com/) (the high-level wrapper around the official MCP Python SDK). Mounts on FastAPI cleanly.

---

## 6. Auth model

### Admin UI (browser, single user)
- OIDC code flow against Entra ID using `fastapi-azure-auth` or `authlib`
- Validate `oid` claim against `ALLOWED_ENTRA_OID` env var — reject all others
- Issue `SameSite=Strict`, `HttpOnly`, `Secure` session cookie
- No CSRF middleware needed (SameSite=Strict + bearer-only mutating endpoints when accessed by MCP)

### MCP endpoint (service-to-service)
- Static bearer token (`MCP_BEARER_TOKEN`)
- No Entra — agent calls this as a service, not as a person
- Token rotation: env var change + restart

---

## 7. OpenRouter integration

Replaces the LiteLLM proxy entirely. OpenRouter is OpenAI-compatible at `https://openrouter.ai/api/v1`, so `parser.py` needs zero logic changes — only the base URL, auth header, and model IDs change.

### Required env vars
```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=https://prices.<your-domain>
OPENROUTER_APP_TITLE=Price Tracker
PRICE_PARSER_MODEL=meta-llama/llama-4-scout
PRICE_PARSER_FALLBACK_MODEL=anthropic/claude-haiku-4.5
PRICE_PARSER_MODEL_CASCADE=${PRICE_PARSER_MODEL},${PRICE_PARSER_FALLBACK_MODEL}
```

### Headers OpenRouter expects
```
Authorization: Bearer $OPENROUTER_API_KEY
HTTP-Referer: $OPENROUTER_HTTP_REFERER
X-Title: $OPENROUTER_APP_TITLE
```

### Parser changes
The cascade logic in `parser.py` already reads `PRICE_PARSER_MODEL_CASCADE`. Keep it. The only place that needs touching is wherever the LiteLLM URL is hardcoded — replace with `OPENROUTER_BASE_URL`. Confidence thresholds (0.70 main / 0.0 fallback) stay as-is.

---

## 8. Phased execution plan

Each phase ends with a verifiable gate. Don't proceed to the next phase until the gate is green.

### Phase 1 — Skeleton + domain copy (2 days)
- `poetry init`, Python 3.12, dependencies: `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `httpx`, `pydantic`, `aiosmtplib`, `cryptography`, `pyjwt`, `fastmcp`, `fastapi-azure-auth`, `pytest`, `pytest-asyncio`, `aiosqlite`
- Dockerfile (base: `python:3.12-slim`)
- `docker-compose.yml` with postgres + app + Traefik labels
- Copy `modules/price_tracker/` → `src/domain/` verbatim
- Squash 3 alembic migrations into `alembic/versions/0001_initial.py`
- Replace `context_id` FK with `tenant_id` UUID column (default seeded tenant)
- Copy 5 test files → `tests/`, rebase fixtures
- **Gate:** `pytest` green; `alembic upgrade head` succeeds against fresh Postgres

### Phase 2 — Service infra (1–2 days)
- `src/infra/fetcher.py`: ~30-line httpx async client with timeouts (replaces `IFetcher`)
- `src/infra/email.py`: aiosmtplib SMTP client (replaces `IEmailService`)
- `src/infra/llm.py`: OpenRouter HTTP client (replaces LiteLLM proxy)
- `src/infra/db.py`: async session factory with engine
- `src/api/app.py`: FastAPI factory with lifespan that starts the scheduler
- **Gate:** App starts, scheduler ticks, manual price check on a Willys URL succeeds end-to-end

### Phase 3 — Admin UI (2–3 days)
- Port `admin_price_tracker.py` endpoints to `src/api/admin.py`:
  - Drop CSRF middleware references
  - Replace Entra session integration with new `auth.py`
  - Drop `/me/context` and replace `context_id` lookups with the seeded tenant
- Port `admin_price_tracker.html` to `src/api/templates/admin.html`:
  - Update API base path
  - Remove CSRF token plumbing in JS fetch calls
- `src/api/auth.py`: OIDC code flow + single-user `oid` check
- Traefik label for `prices.<your-domain>` → port 80 of the app
- **Gate:** You can log in via Entra, create a product, link to Willys, see price history populate

### Phase 4 — MCP server (1–2 days)
- `src/mcp/server.py` with FastMCP
- 4 tools matching Section 5
- Bearer auth middleware (validates `MCP_BEARER_TOKEN`)
- Mount on FastAPI app at `/mcp`
- Configure the agent platform's MCP server entry at `/platformadmin/mcp/` to point here
- Update `skills/general/price_tracker.md` in the agent platform — set `tools:` frontmatter to the MCP-discovered tool names (run the platform's MCP test-connection flow first to see the exact namespacing)
- **Gate:** Agent in the platform can answer "Vad kostar Apotea omega-3?" by calling the MCP server

### Phase 5 — Agent platform cleanup (~half day)
Branch in `ai-agent-platform`, delete:
- `services/agent/src/modules/price_tracker/` (entire dir, including tests)
- `services/agent/src/orchestrator/price_tracker.py`
- `services/agent/src/interfaces/http/admin_price_tracker.py`
- `services/agent/src/interfaces/http/templates/admin_price_tracker.html`
- `services/agent/src/interfaces/http/templates/price_tracker_dashboard.html`
- `services/agent/src/core/tools/price_tracker.py`
- `services/agent/src/core/protocols/price_tracker.py`
- `services/agent/src/interfaces/http/schemas/price_tracker.py`

Edit:
- `services/agent/config/tools.yaml` — remove `price_tracker` tool entry
- `services/agent/config/models.yaml` — remove `price_tracker` and `price_tracker_fallback` aliases
- `services/agent/src/interfaces/http/admin_shared.py` — remove "Price Tracker" nav item
- `services/agent/src/interfaces/http/app.py` — remove `admin_price_tracker_router` registration
- `services/agent/src/orchestrator/startup.py` — remove `PriceCheckScheduler` initialization and `set_price_tracker()` call
- `skills/general/price_tracker.md` — already updated in Phase 4

Add:
- New Alembic downgrade migration that drops the 5 `price_tracker_*` tables

**Gate:** `stack check` green; agent platform deploys; `priser` skill still works (now via MCP)

---

## 9. Verified data model

All tables prefixed with `price_tracker_` in source repo. Drop the prefix in the new repo (single-purpose app, no naming conflicts).

| New table name | Source name | Purpose | Multi-tenant |
|---|---|---|---|
| `stores` | `price_tracker_stores` | Retailer registry (seeded) | No (shared) |
| `products` | `price_tracker_products` | Items to track | Yes (`tenant_id`) |
| `product_stores` | `price_tracker_product_stores` | Product↔store links with check schedule | Implicit |
| `price_points` | `price_tracker_price_points` | Historical price observations | Implicit |
| `watches` | `price_tracker_watches` | User-defined alerts | Yes (`tenant_id`) |

Key relationships (cascading deletes preserved as-is):
- `Product` → `ProductStore[]` (cascade delete)
- `ProductStore` → `PricePoint[]` (cascade delete)
- `Product` → `Watch[]` (cascade delete)

Seed in initial migration: 5 stores (ICA, Willys, Apotea, Med24, Doz) with their `slug`, `store_type`, `base_url`, and `parser_config`.

---

## 10. Known shortcomings (do NOT fix during extraction)

These are deliberately out of scope for the initial extraction. Capture as backlog for post-extraction Phase 6:

- **LLM-dependent for 4/5 stores** — only Willys has a structured API extractor
- **No retry/backoff** on extraction failures
- **Email-only notifications** — no Telegram/push despite agent platform having Telegram
- **No price analytics** — no trends, volatility, or "is this actually a good deal" intelligence
- **Hardcoded Swedish** — store hints, prompts, email copy all `sv-SE`
- **No deduplication** — same product entered twice creates two rows
- **`raw_response` JSONB nullable** — failed extractions lose context
- **Cascading deletes wipe history** — no soft-delete or audit trail
- **No rate limiting on manual `/check`** endpoint
- **Fallback model confidence threshold is 0.0** — accepts anything

Goal of extraction: **byte-equivalent feature parity in a standalone repo.** Improvements come after.

---

## 11. Cross-references for the planning agent

If the planning session needs to verify a claim or peek at the source, here are the load-bearing files:

```bash
# Module structure
ls /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/

# Admin endpoints (the big one — 1689 LOC)
/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/admin_price_tracker.py

# Migrations
/home/magnus/dev/ai-agent-platform/services/agent/alembic/versions/20260115_add_price_tracker_*.py

# Skill (stays in source repo, frontmatter updated in Phase 4)
/home/magnus/dev/ai-agent-platform/skills/general/price_tracker.md

# Existing MCP server admin page (for understanding the consumption side)
/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/admin_mcp.py
/home/magnus/dev/ai-agent-platform/services/agent/src/interfaces/http/templates/admin_mcp.html
```

---

## 12. Estimate

~7–10 days of focused work end-to-end. Phase 1 (green tests in new repo) is the load-bearing milestone — once that lands, the rest is mechanical.

---

## 13. Next step

Run `/plan` for **Phase 1 only** (skeleton + domain copy + green tests). Don't pre-plan Phase 2+ until Phase 1 reveals any surprises with the fixture rebase.
