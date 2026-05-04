# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-01)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 1 — Skeleton + Domain Copy

## Current Position

Phase: 1 of 5 (Skeleton + Domain Copy)
Plan: 5 of 5 in current phase
Status: Phase 1 complete (all 4 ROADMAP success criteria verified end-to-end on 2026-05-04)
Last activity: 2026-05-04 — Plan 01-05 complete: Docker scaffolding (167 MB image) + final phase verification; all 4 Phase 1 ROADMAP gates green simultaneously (pytest 67/67, alembic upgrade head against compose Postgres, poetry install on Python 3.12.3, docker build)

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: ~11 min
- Total execution time: ~54 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Skeleton + Domain Copy | 5/5 | ~54 min | ~11 min |

**Recent Trend:**
- Last 5 plans: 01-05 (~10 min, 2 tasks, 5 files), 01-04 (~10 min, 1 task, 6 files), 01-03 (~12 min, 2 tasks, 5 files), 01-02 (~12 min, 2 tasks, 11 files), 01-01 (~10 min, 2 tasks, 9 files)
- Trend: steady (~10-12 min/plan)

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

Last session: 2026-05-04
Stopped at: Phase 1 complete. All 4 ROADMAP gates green (pytest 67/67, alembic upgrade head against compose Postgres → 5 tables + 5 seeded stores, poetry install on Python 3.12.3, docker build → price-tracker:phase1 167 MB). Ready for Phase 2 (Service Infrastructure). Roadmap reassess (D-19) is the immediate next planning activity — Phase 3 (auth → IAP header trust) and Phase 4 (MCP routing) need rewriting before Phase 3 planning starts; flag at Phase 2 planning kickoff.
Resume file: (Phase 2 plan file — to be created during Phase 2 planning)
