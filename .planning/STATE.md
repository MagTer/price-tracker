# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-04)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 4 — MCP Server + Agent Wiring has a verified gap (agent-platform registration + `mcp.<domain>` ingress not done); Phase 5 is blocked on this until resolved

## Current Position

Phase: 4 of 5 (MCP Server + Agent Wiring) — gaps found, blocking Phase 5
Plan: 1 of 1 in current phase (retroactively documented 2026-07-06)
Status: Phases 1-3 complete and verified (Phase 2/3 retroactively backfilled 2026-07-06 with 1 flagged live-check caveat each — see below). Phase 4's MCP server itself is built and tested (4 tools + bearer auth), but its `mcp.<domain>` ingress and agent-platform registration are NOT done — retroactive verification 2026-07-06 found `gaps_found` status. Phase 5 (Source-repo Cleanup) should not start until this is resolved, per ROADMAP.md's own stated Phase 5 dependency ("MCP must be live and `priser` verified end-to-end before deleting source paths").
Last activity: 2026-07-06 — Retroactively backfilled GSD phase artifacts (CONTEXT/PLAN/SUMMARY/VERIFICATION) for Phases 2-4 (quick task 260706-w69), since they were implemented directly (commit d92372a) without the formal discuss/plan/execute pipeline. Discovered Phase 4's agent-platform wiring was never done despite ROADMAP.md/STATE.md previously marking it Complete; corrected tracking to gaps_found.

Progress: [███████░░░] 70% (Phases 1-3 of 5 complete; Phase 4 partial — MCP server built and tested, agent-platform wiring pending)

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
- 2026-07-06 D-21: Retroactively backfilled CONTEXT/PLAN/SUMMARY/VERIFICATION for Phases 2-4 (quick task 260706-w69) to reconcile GSD phase-tracking with actual delivered code (all 3 phases were implemented directly in commit d92372a without the formal pipeline, so `.planning/phases/` had no directories for them and GSD's tracking recommended a fresh `/gsd-discuss-phase 2` against already-working code). Phase 2/3 verified passed with 1 flagged live-check caveat each (Willys live price check; live UI walkthrough) — accepted per user decision. Phase 4 verified gaps_found: agent-platform registration and `mcp.<domain>` ingress genuinely not done. Corrected ROADMAP.md checkbox/progress-table and REQUIREMENTS.md's MCP-05/INFRA-06 traceability entries accordingly. Note: **D-20 is reserved** (not yet committed) for the still-pending edge-proxy Dokploy reassess quick task (260706-tq5) — use **D-22** for the next new decision, not D-20.

### Pending Todos

None yet.

### Blockers/Concerns

- Phases must run sequentially despite `parallelization=true` in config — each gate is a precondition for the next phase's work. Plans within a phase may parallelize.
- Email backend (SMTP via aiosmtplib vs AWS SES) — decide during Phase 2
- MCP subdomain (`mcp.<domain>`) is now LOCKED for Phase 4 (was: TBD); IAP per-host bypass is the rationale (D-18)
- **Edge-proxy / portal stack** is deferred to a separate future GSD milestone or repo (D-18, EDGE-01). Phases 3 + 4 ASSUME the IAP exists; if it does not exist when Phase 3 lands, Phase 3 still ships behind a "trust the header" dependency and the operator runs the app in a private network until the edge stack is built.
- **Phase 4 gap (2026-07-06 retroactive verification):** The MCP server itself works and is tested (4 tools, bearer auth), but the `mcp.<domain>` ingress (Dokploy-managed, not yet built) and agent-platform `/platformadmin/mcp/` registration (separate `ai-agent-platform` repo, never attempted) are NOT done. Phase 5 explicitly depends on "MCP must be live and `priser` verified end-to-end" — this precondition is unmet. Do not start Phase 5 until this gap is closed or the dependency is explicitly re-scoped. See `.planning/phases/04-mcp-server-agent-wiring/04-VERIFICATION.md` for the full gap summary.
- **Pending, unexecuted quick task:** `.planning/quick/260706-tq5-reassess-edge-proxy-plan-edge-01-d-18-ac/` has a written PLAN.md (reassessing the edge-proxy plan to reflect Dokploy as the ingress-management platform) that was never executed — the plan reserves decision ID **D-20** for when it runs. Do not reuse D-20 for anything else.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260706-rso | Fix 4 pre-Phase-5 blockers: mcp/mcp_server package collision, doubled /v1 in OpenRouter URL, stale LiteLLM model aliases, fastmcp 1.0→2.x bump | 2026-07-06 | dd547bd | [260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-](./quick/260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-/) |
| 260706-t3p | Fix CLAUDE.md stale mcp/ reference + propagate MCP sub-app lifespan into create_app() so the streamable-HTTP session manager actually starts | 2026-07-06 | 7a3127b | [260706-t3p-fix-2-issues-flagged-after-quick-task-26](./quick/260706-t3p-fix-2-issues-flagged-after-quick-task-26/) |
| 260706-tha | Fix 4 stale Entra OIDC references in CLAUDE.md to match the locked IAP header-trust auth model (X-Auth-Request-Email via Dokploy-managed Traefik+auth-middleware ingress, not yet built) | 2026-07-06 | d094d70 | [260706-tha-fix-4-stale-entra-oidc-references-in-cla](./quick/260706-tha-fix-4-stale-entra-oidc-references-in-cla/) |
| 260706-w69 | Backfill retroactive GSD phase artifacts for Phases 2-4 (implemented outside the formal pipeline); discovered and corrected Phase 4's optimistic "Complete" marking to gaps_found (agent-platform registration + mcp.<domain> ingress not done) | 2026-07-06 | (pending) | [260706-w69-backfill-retroactive-gsd-phase-artifacts](./quick/260706-w69-backfill-retroactive-gsd-phase-artifacts/) |

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
