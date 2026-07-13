# Price Tracker

## What This Is

Standalone Swedish grocery and pharmacy price tracker — extracted from the `ai-agent-platform` monolith at `/home/magnus/dev/ai-agent-platform`. Tracks prices at ICA, Willys, Apotea, Med24, and Doz; exposes its capabilities to the agent platform via an MCP server; runs behind an upstream Identity-Aware Proxy (separate edge-stack repo) that terminates Entra OIDC and forwards `X-Auth-Request-Email`. Single-user (Magnus only).

## Core Value

After extraction, the agent platform's `priser` skill keeps working end-to-end — but now via MCP-discovered tools served by this standalone repo, with the price tracker code fully removed from the agent platform.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — extraction phase begins from empty repo)

### Active

<!-- Current scope. Building toward these. -->

- [ ] Domain module (models, service, scheduler, parser, notifier, extractors, stores) ported byte-equivalent from source
- [ ] Alembic migrations squashed into single `0001_initial.py` against empty graph
- [ ] Multi-tenancy schema change: `context_id` FK → `tenant_id` UUID with seeded default tenant
- [ ] Test suite (parser, service, scheduler, notifier, extractors) ported with rebased fixtures
- [ ] Service infra: httpx fetcher, aiosmtplib email, OpenRouter LLM client, async DB session factory
- [ ] FastAPI app factory with lifespan that starts the price-check scheduler
- [ ] Admin UI ported (server-rendered HTML + vanilla JS, no CSRF middleware)
- [ ] IAP header trust (`X-Auth-Request-Email` validated against `ALLOWED_ENTRA_EMAIL`); no in-app OIDC client
- [ ] MCP server (FastMCP) with 4 tools: `check_price`, `find_deals`, `compare_stores`, `list_products`
- [ ] Static bearer-token auth on the MCP subdomain (`mcp.<domain>`, IAP-bypassed)
- [ ] Edge-proxy routing for `prices.<domain>` and `mcp.<domain>` (lives in separate edge-stack repo)
- [ ] Source repo cleanup: delete price_tracker module + chat tool + admin UI + protocol re-exports; update tools/models/skills config
- [ ] Source repo: Alembic downgrade migration that drops the 5 `price_tracker_*` tables
- [ ] Agent platform `priser` skill points at MCP-discovered tools (verified end-to-end)

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Multi-tenant beyond schema column — Single-user app; column kept for future, but no multi-tenant routing/UI
- Data migration from existing DB — Source DB has no production data; new repo starts empty
- LiteLLM proxy — Single app doesn't need a routing hub; OpenRouter direct only
- SPA framework (React/Vue) — Single user; vanilla JS + server-rendered HTML is sufficient
- Retry/backoff on extraction failures — Existing behavior preserved verbatim; backlog for post-extraction
- Telegram/push notifications — Email-only matches existing behavior; backlog
- Price analytics (trends, volatility, "is this a good deal") — Out of extraction scope; backlog
- i18n / English copy — Hardcoded `sv-SE` strings preserved verbatim; backlog
- Product deduplication — Existing behavior preserved
- Soft-delete / audit trail — Cascading deletes preserved as-is
- Rate limiting on manual `/check` endpoint — Existing behavior preserved
- Tightening fallback model confidence threshold (currently 0.0) — Existing behavior preserved
- Fixing the 4-of-5 LLM-dependent extractors — Only Willys has a structured API extractor; rest use LLM parsing as before
- New extractor implementations — Port what exists; don't add structured extractors for ICA/Apotea/Med24/Doz here

## Context

**Source repo:** `/home/magnus/dev/ai-agent-platform`
**Target repo:** `/home/magnus/dev/price-tracker` (this repo)
**Extraction brief:** [EXTRACTION.md](../EXTRACTION.md) — load-bearing source of truth; contains verified LOC counts, file paths, locked decisions, and the full phased plan
**Estimated scope:** ~3,400 LOC of working code being relocated; ~7–10 days of focused work
**Goal type:** Mechanical port + integration boundary swap (chat-tool → MCP server). Improvements come after extraction completes.

The `priser` skill in the agent platform routes through MCP today — this repo's job is to provide the MCP server it points at, then unwire the platform's internal price_tracker code.

## Constraints

- **Stack**: Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + FastMCP + httpx + aiosmtplib — Reuses ~3,400 LOC of working source code; deviations require touching ported logic
- **Auth (UI)**: Trusts `X-Auth-Request-Email` from upstream Identity-Aware Proxy (validates against `ALLOWED_ENTRA_EMAIL`) — OIDC is terminated once at the edge proxy; this app carries no OIDC client (D-17 in `.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md`)
- **Auth (MCP)**: Static bearer token in `MCP_BEARER_TOKEN` env var on a dedicated `mcp.<domain>` subdomain that the IAP middleware excludes — Service-to-service call from the agent platform; no header trust
- **TLS / routing**: External edge proxy (Traefik + oauth2-proxy in a separate future repo per D-18) — This repo carries NO Traefik labels; app joins an external `edge` docker network for the proxy to reach it
- **LLM provider**: OpenRouter direct at `https://openrouter.ai/api/v1` — OpenAI-compatible, replaces LiteLLM proxy with zero parser logic changes
- **Data**: Start empty — No migration from source DB; seed 5 stores (ICA, Willys, Apotea, Med24, Doz) in initial migration
- **Behavioral parity**: Byte-equivalent feature parity — No improvements during extraction; capture them as Phase 6 backlog
- **Hosting**: Joins external `edge` docker network owned by the future edge-proxy stack — `prices.<domain>` for admin UI, `mcp.<domain>` for MCP (subdomain locked over `/mcp` path because IAP auth-bypass is per-host)
- **Frontend**: Server-rendered HTML + vanilla JS + Chart.js — SPA framework not justified for single user

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Single-user (Magnus-only) auth | Simplest path; matches existing platform identity | — Pending |
| `tenant_id` UUID column kept (despite single-user) | Future-proofs multi-tenant without schema migration later | — Pending |
| Agent integration via MCP server (not REST) | Cleaner than service-to-service REST; platform already has per-context MCP infra at `/platformadmin/mcp/` | — Pending |
| OpenRouter direct (no LiteLLM proxy) | Single app doesn't need a routing hub; OpenAI-compatible base URL means parser changes only | — Pending |
| Server-rendered HTML + vanilla JS (port from existing) | Single user; SPA framework overhead not justified | — Pending |
| FastMCP library over raw MCP SDK | High-level wrapper; mounts on FastAPI cleanly | — Pending |
| Squash 3 source migrations into one initial migration | Empty target Alembic graph; no production data to migrate | — Pending |
| Drop `price_tracker_` table prefix | Single-purpose app — no naming conflicts | — Pending |
| Keep cascading deletes as-is | Behavioral parity during extraction; revisit later | — Pending |
| Email backend pluggable (SMTP via aiosmtplib OR AWS SES) | Decide concrete backend during Phase 2 implementation | — Pending |
| Mount path for MCP (`/mcp` on same app vs separate Traefik subdomain) | Decide during Phase 4 | Locked 2026-05-04 — dedicated `mcp.<domain>` subdomain (per-host IAP bypass cleaner than per-path) |
| Auth topology: in-app OIDC vs upstream IAP header trust | OIDC client + session cookies in this repo couples it tightly to Entra; IAP terminates OIDC once at the edge and forwards email header — multiple backend apps share one OIDC client | Locked 2026-05-02 (D-17) — IAP header trust; drops `fastapi-azure-auth` + `pyjwt` from this repo |
| Edge proxy / portal stack repo | Belongs out of the price-tracker extraction milestone; separate codebase, separate concerns (TLS, OIDC, routing, dashboard); ingress is operated within Dokploy's managed scope, not a standalone hosting target | Locked 2026-05-02 (D-18) — Traefik + oauth2-proxy + Homepage; reassessed 2026-07-06 (D-20) — managed within Dokploy's scope, not a hand-built stack on a standalone VM |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-04 — D-19 roadmap reassess after Phase 1 close: auth topology shifted to upstream IAP header trust (D-17); MCP subdomain locked over `/mcp` path (D-18); edge-proxy stack moved to separate future milestone*
*Last updated: 2026-07-06 — D-20 reassess: edge-proxy ingress hosting target corrected from a standalone hand-built VM to Dokploy-managed ingress (Traefik + auth-middleware architecture and D-18 lock unchanged; still out of the price-tracker extraction milestone's build scope)*
