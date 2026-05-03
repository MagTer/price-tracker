# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 1 — Skeleton + Domain Copy

## Current Position

Phase: 1 of 5 (Skeleton + Domain Copy)
Plan: 2 of 5 in current phase
Status: In progress (Wave 2 complete; Wave 3 ready)
Last activity: 2026-05-03 — Plan 01-02 complete (11 domain modules ported verbatim)

Progress: [████░░░░░░] 40%

## Performance Metrics

**Velocity:**
- Total plans completed: 2
- Average duration: ~11 min
- Total execution time: ~22 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Skeleton + Domain Copy | 2/5 | ~22 min | ~11 min |

**Recent Trend:**
- Last 5 plans: 01-02 (~12 min, 2 tasks, 11 files), 01-01 (~10 min, 2 tasks, 9 files)
- Trend: —

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

### Pending Todos

None yet.

### Blockers/Concerns

- Phases must run sequentially despite `parallelization=true` in config — each gate is a precondition for the next phase's work. Plans within a phase may parallelize.
- Email backend (SMTP via aiosmtplib vs AWS SES) — decide during Phase 2
- MCP mount path now constrained by IAP shift — likely separate `mcp.<domain>` subdomain (proxy auth-bypass is per-host); confirm during Phase 4
- **Roadmap reassess required after Phase 1** — Phase 3 (Entra OIDC code flow) and Phase 4 (MCP routing) both need rewriting to absorb the portal-owned IAP architecture decided in Phase 1 discuss. AUTH-01..03 in REQUIREMENTS.md will be rewritten; new edge-proxy/portal milestone (or separate project) will be added.

## Deferred Items

Items acknowledged and carried forward (v2 / post-extraction backlog from REQUIREMENTS.md):

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Reliability | REL-01..05 (retry/backoff, raw_response, soft-delete, rate limiting, fallback threshold) | v2 backlog | Init |
| Extraction Quality | EXT-01..02 (structured extractors, dedup) | v2 backlog | Init |
| Notifications | NOTF-01 (Telegram/push) | v2 backlog | Init |
| Analytics | ANAL-01 (price trends/volatility) | v2 backlog | Init |
| i18n | I18N-01 (externalize sv-SE strings) | v2 backlog | Init |

## Session Continuity

Last session: 2026-05-03
Stopped at: Plan 01-02 complete. Wave 2 done. Wave 3 (01-03-migration + 01-04-tests, sequential) is next — squashed Alembic 0001_initial.py from final models.py shape, then port 5 test files. Roadmap reassess still scheduled after Phase 1 completes (auth topology shift; REQUIREMENTS.md DB-03 wording lag).
Resume file: .planning/phases/01-skeleton-domain-copy/01-03-migration-PLAN.md
