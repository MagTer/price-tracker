# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-04)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 2 — Service Infrastructure (Phase 1 complete; D-19 roadmap reassess complete)

## Current Position

Phase: 5 of 5 (Source-repo Cleanup) — ready to start
Plan: 0 of TBD in current phase
Status: Phases 1-4 complete. Critical bugs fixed (Dockerfile CMD, admin.py type/import bugs). Admin dashboard template rebuilt. MCP server implemented with 4 tools + bearer auth. New API and MCP tests added. Ready for Phase 5 or end-to-end verification.
Last activity: 2026-07-06 — Completed quick task 260706-rso: fixed 4 pre-Phase-5 blockers (mcp/mcp_server package collision, doubled /v1 in OpenRouter URL, stale LiteLLM model aliases, fastmcp 1.0→2.x bump). Full test suite green (87 passed, 0 failures).

Progress: [████████░░] 80% (Phases 1-4 of 5 complete)

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: ~11 min
- Total execution time: ~54 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Skeleton + Domain Copy | 5/5 | ~54 min | ~11 min |
| 2. Service Infrastructure | 1/1 | — | — |
| 3. Admin UI + IAP Header Trust | 1/1 | — | — |
| 4. MCP Server + Agent Wiring | 1/1 | — | — |

**Recent Trend:**
- Phases 2-4 completed in a single autonomous session
- New test coverage: 2 test files (~120 LOC) covering admin API and MCP tools

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Initialization: Single-user (Magnus-only) auth with `tenant_id` UUID column kept for future
- Initialization: Agent integration via MCP server (not REST), FastMCP library
- Initialization: OpenRouter direct (no LiteLLM proxy)
- Initialization: Squash 3 source migrations into one initial migration; drop `price_tracker_` table prefix
- Plan 01-01: Adapted pyproject.toml to Poetry 2.x PEP 621 `[project]` table (deprecation-warning fix); kept `[tool.poetry] packages = [...]` for src-layout — same dep set, same Phase 1 minimums (Rule 3 deviation)
- Plan 01-03: Removed redundant `uq_store_slug` named UniqueConstraint from squashed migration — the unique index `ix_stores_slug` produced by `mapped_column(unique=True, index=True)` already enforces slug uniqueness; the named constraint was reported as drift by `alembic check` (Rule 1 deviation, kept migration faithful to ORM metadata)
- Plan 01-03: Used Alembic async template (env.py uses `async_engine_from_config` against the `postgresql+asyncpg://` URL) — keeps alembic CLI URL identical to runtime URL, avoids dual sync/async driver config
- Plan 01-04: No `tests/conftest.py` created — source repo had none in `tests/`, each test file constructs its own MagicMock/AsyncMock fixtures inline. Adding a conftest would have been an unsolicited refactor (verbatim port doctrine).
- Plan 01-05: Bumped Dockerfile `POETRY_VERSION` from plan-spec 1.8.3 to 2.3.2 to match the project's PEP 621 `[project]` table (Plan 01-01 deviation continuation) — Poetry 1.8.3 rejected the manifest with "fields ['authors', 'description', 'name', 'version'] are required in package mode" (Rule 1 deviation, fix folded into Task 1 commit)
- Plan 01-05: Added `!.env.template` exception to `.gitignore` so the env-var-contract template can be committed (was matched by `.env.*` rule). Naming convention preserved per plan spec (Rule 3 deviation)
- 2026-05-04 D-19 reassess: Locked MCP subdomain (`mcp.<domain>`) over `/mcp` path because IAP auth-bypass is per-host. Locked IAP header trust (`X-Auth-Request-Email`) as the Phase 3 auth model — drops `fastapi-azure-auth`, `pyjwt`, `cryptography` from this repo permanently. Locked edge-proxy stack as separate future milestone (does NOT belong inside extraction milestone).

### Pending Todos

None yet.

### Blockers/Concerns

- Phases must run sequentially despite `parallelization=true` in config — each gate is a precondition for the next phase's work. Plans within a phase may parallelize.
- Email backend (SMTP via aiosmtplib vs AWS SES) — decide during Phase 2
- MCP subdomain (`mcp.<domain>`) is now LOCKED for Phase 4 (was: TBD); IAP per-host bypass is the rationale (D-18)
- **Edge-proxy / portal stack** is deferred to a separate future GSD milestone or repo (D-18, EDGE-01). Phases 3 + 4 ASSUME the IAP exists; if it does not exist when Phase 3 lands, Phase 3 still ships behind a "trust the header" dependency and the operator runs the app in a private network until the edge stack is built.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260706-rso | Fix 4 pre-Phase-5 blockers: mcp/mcp_server package collision, doubled /v1 in OpenRouter URL, stale LiteLLM model aliases, fastmcp 1.0→2.x bump | 2026-07-06 | dd547bd | [260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-](./quick/260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-/) |

## Deferred Items

Items acknowledged and carried forward (v2 / post-extraction backlog from REQUIREMENTS.md):

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Reliability | REL-01..05 (retry/backoff, raw_response, soft-delete, rate limiting, fallback threshold) | v2 backlog | Init |
| Extraction Quality | EXT-01..02 (structured extractors, dedup) | v2 backlog | Init |
| Notifications | NOTF-01 (Telegram/push) | v2 backlog | Init |
| Analytics | ANAL-01 (price trends/volatility) | v2 backlog | Init |
| i18n | I18N-01 (externalize sv-SE strings) | v2 backlog | Init |
| Edge proxy / portal | EDGE-01 (Traefik + oauth2-proxy + Homepage on Flatcar — separate future milestone or repo) | Out of milestone | 2026-05-04 (D-19 reassess) |

## Session Continuity

Last session: 2026-05-04
Stopped at: Phase 1 verified + D-19 roadmap reassess complete. REQUIREMENTS.md AUTH-01..04, MCP-05, DEPLOY-01/03/04, DB-03, TEST-02 rewritten to match locked decisions (D-03/D-04/D-10/D-17/D-18). ROADMAP.md Phase 3 + Phase 4 sections rewritten. PROJECT.md Constraints + Key Decisions updated; EDGE-01 added to v2 backlog. Ready to enter Phase 2 (Service Infrastructure) discuss → plan → execute.
Resume command: `/gsd-discuss-phase 2` (or continue `/gsd-autonomous` from current main thread)
