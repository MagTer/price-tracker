# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 1 — Skeleton + Domain Copy

## Current Position

Phase: 1 of 5 (Skeleton + Domain Copy)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-05-01 — Roadmap created from EXTRACTION.md §8; 5 phases mapped to 40 v1 requirements

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: —
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

Last session: 2026-05-02
Stopped at: Phase 1 context gathered (CONTEXT.md + DISCUSSION-LOG.md). Ready to run `/gsd-plan-phase 1`. Roadmap reassess scheduled after Phase 1 completes — auth topology shifted to portal-owned IAP (drops fastapi-azure-auth/pyjwt; rewrites Phase 3 + Phase 4).
Resume file: .planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
