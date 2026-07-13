# Phase 2: Service Infrastructure - Context

**Gathered:** 2026-07-06 (retroactive)
**Status:** Retroactively documented — implementation predates formal GSD phase tracking

<domain>
## Phase Boundary

Replace the source repo's interfaces (`IFetcher`, `IEmailService`, `LiteLLM proxy`, in-memory session) with concrete infra clients, wire them into a FastAPI app whose lifespan starts the scheduler, and prove the whole loop works against a real Willys URL.

**Note on retroactive documentation:** This phase was implemented directly in a single autonomous session (commit `d92372a`, 2026-05-04) alongside Phases 3 and 4, without going through the formal `/gsd-discuss-phase` → `/gsd-plan-phase` → `/gsd-execute-phase` pipeline. This CONTEXT.md, along with this phase's PLAN.md, SUMMARY.md, and VERIFICATION.md, were written retroactively on 2026-07-06 to reconcile GSD's phase-tracking state (which requires on-disk plan/summary/verification artifacts to recognize a phase as complete) with the actual delivered code. Decisions below are reconstructed from the actual code, `.planning/STATE.md`'s decision log, and `.planning/ROADMAP.md`'s phase description — not from a live discussion transcript, since none exists for this phase.

**Verifiable gates** (from ROADMAP.md Phase 2):
1. App boots via `uvicorn` with FastAPI lifespan starting the price-check scheduler (scheduler ticks observable in logs)
2. A manual price check on a real Willys product URL persists a `PricePoint` row end-to-end via the new fetcher, parser, and DB session
3. Parser calls hit OpenRouter at `https://openrouter.ai/api/v1` with `Authorization: Bearer`, `HTTP-Referer`, and `X-Title` headers and the cascade model env vars are honoured
4. `.env.template` lists every env var required by the running app (OpenRouter, SMTP, DB)

</domain>

<decisions>
## Implementation Decisions

### Infra client shape
- Singleton-per-process provider pattern (`src/infra/providers.py`): `get_fetcher()` and `get_email_service()` lazily construct and cache a single `WebFetcher`/`SmtpEmailService` instance per process, reused across the scheduler and any request handlers.
- `WebFetcher` (`src/infra/fetcher.py`) is a thin `httpx.AsyncClient` wrapper with browser-like headers (Swedish `Accept-Language`), a 15s/5s-connect timeout, and a stdlib `HTMLParser`-based text extractor that strips `script`/`style`/`nav`/`footer`/`header` tags and truncates to 12000 chars — no external HTML-parsing dependency needed.
- `SmtpEmailService` (`src/infra/email.py`) wraps `aiosmtplib`; `is_configured()` gates whether the scheduler even constructs a `PriceNotifier` (see `src/api/app.py`'s lifespan) — unconfigured SMTP means notifications are silently skipped rather than erroring.
- `src/infra/llm.py` builds a module-level `OPENROUTER_HEADERS` dict from env vars at import time (`OPENROUTER_API_KEY`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE`), conditionally adding each header only if the corresponding env var is set.

### FastAPI app factory + lifespan
- `src/api/app.py`'s `create_app()` factory constructs the `PriceCheckScheduler` inside an `@asynccontextmanager` lifespan, passing it the shared fetcher/email-service singletons and the async session factory from `src/infra/db.py`. `app.state.scheduler` is exposed for the `/health` endpoint.
- The MCP sub-app (Phase 4) mounts at `/mcp` inside the same `create_app()` — Phase 2 and Phase 4 share this one app factory rather than running as separate processes.

### OpenRouter integration (EXTRACTION.md §7)
- `parser.py`'s `_extract_with_model` posts to `{OPENROUTER_BASE_URL}/chat/completions` (base URL already includes `/api/v1`) — **this exact endpoint had a doubled `/v1` bug that was live until today's quick task 260706-rso fixed it** (see Deviations note in SUMMARY.md).
- `MODEL_CASCADE` and `CONFIDENCE_THRESHOLDS` are keyed by real OpenRouter model IDs (`meta-llama/llama-4-scout` main, `anthropic/claude-haiku-4.5` fallback, 0.70/0.0 thresholds) — **also only correct as of today's fix**; the code that actually landed in the May 4 commit used placeholder LiteLLM-proxy aliases that would have 400'd against the real OpenRouter API.

</decisions>

<code_context>
## Existing Code Insights

- `src/infra/fetcher.py`, `src/infra/email.py`, `src/infra/llm.py`, `src/infra/db.py`, `src/infra/providers.py` — all created in commit `d92372a` (2026-05-04)
- `src/api/app.py` — FastAPI factory + lifespan, also from `d92372a`
- `src/domain/parser.py` — pre-existed from Phase 1's domain port, but its OpenRouter URL and model-cascade defaults were buggy until quick task 260706-rso (2026-07-06) fixed them

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond ROADMAP.md's phase description — this was executed directly rather than planned in advance.

</specifics>

<deferred>
## Deferred Ideas

None captured — no discuss session took place for this phase.

</deferred>
