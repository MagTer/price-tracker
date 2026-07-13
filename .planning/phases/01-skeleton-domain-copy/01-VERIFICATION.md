---
phase: 01-skeleton-domain-copy
verified: 2026-05-04T11:30:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining: []
  regressions: []
---

# Phase 1: Skeleton + Domain Copy Verification Report

**Phase Goal:** A standalone Python project with the domain logic copied verbatim, the data model squashed into a single tenant-scoped initial migration, and the existing test suite passing against rebased fixtures.
**Verified:** 2026-05-04T11:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (ROADMAP Phase 1 Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `pytest` runs the full ported suite (parser, service, scheduler, notifier, extractors) green against rebased fixtures | VERIFIED | `poetry run pytest -q` exits 0; `67 passed, 8 warnings in 0.46s`. Per-file counts match source: extractors=12, notifier=14, parser=14, scheduler=13, service=14 (sum 67). 8 warnings are pre-existing `RuntimeWarning` for unawaited `AsyncMock` coroutines in test_service — preserved verbatim from source per CLAUDE.md "don't fix known shortcomings during the port". |
| 2 | `alembic upgrade head` succeeds against fresh Postgres and produces tables `stores`, `products`, `product_stores`, `price_points`, `watches` with `tenant_id` UUID columns and the 5 seeded stores | VERIFIED | Live `docker compose up postgres -d` + `poetry run alembic upgrade head` (exit 0) against fresh `postgres:16-alpine`. `\dt` returned exactly 6 rows (5 domain + alembic_version). `SELECT slug FROM stores ORDER BY slug` returned `apotea\ndoz\nica\nmed24\nwillys`. `information_schema.columns` confirmed `products.tenant_id` and `watches.tenant_id` are `uuid NOT NULL`. `SELECT count(*) FROM information_schema.tables WHERE table_name IN ('contexts','tenants')` returned 0. `pg_constraint` lists exactly 4 FKs (none referencing contexts/tenants). `alembic check` reported "No new upgrade operations detected" — zero drift between migration and `Base.metadata`. |
| 3 | `poetry install` resolves all declared dependencies on Python 3.12 | VERIFIED | `poetry env info` reports `Python: 3.12.3`. `poetry check` exits 0 with `All set!`. `poetry.lock` present and in sync. pyproject.toml uses PEP 621 `[project]` table with `requires-python = ">=3.12,<4.0"`; runtime deps `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `httpx`, `pydantic`; dev deps `pytest`, `pytest-asyncio`. |
| 4 | `docker build` produces a runnable image from the `python:3.12-slim` Dockerfile | VERIFIED | `docker build -t price-tracker:phase1-verify .` re-run during verification: exit 0; image `price-tracker:phase1-verify 167MB` (44% headroom under D-11's 300 MB target). Existing `price-tracker:phase1 167MB` from Plan 05 also present. Multi-stage Dockerfile (builder + final), both `FROM python:3.12-slim`. |

**Score:** 4/4 truths verified

### Required Artifacts

All artifacts present and substantive at the canonical paths.

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | Poetry/PEP 621 + Python 3.12 dep set | VERIFIED | 30 LOC; PEP 621 `[project]` table; `requires-python = ">=3.12,<4.0"`; 5 runtime deps + 2 dev deps. No `aiosqlite` (D-04), no `fastapi-azure-auth`, no `pyjwt` (D-17), no Phase 2-4 deps. |
| `poetry.lock` | Generated lockfile in sync | VERIFIED | Present at repo root; `poetry install --no-root --with dev` reports "No dependencies to install or update". |
| `src/domain/tenant.py` | DEFAULT_TENANT_ID UUID constant (D-01) | VERIFIED | 4 LOC; declares `DEFAULT_TENANT_ID: uuid.UUID = uuid.UUID("f21b6620-c793-46e3-a354-dfcd9956b4a2")`. Docstring documents D-02/D-03. |
| `src/infra/db.py` | Base + _utc_now (D-15) | VERIFIED | 11 LOC; `class Base(DeclarativeBase): pass` and `def _utc_now() -> datetime: return datetime.now(UTC)`. |
| `src/infra/providers.py` | get_fetcher() Phase-2 stub | VERIFIED | 4 LOC; `raise NotImplementedError("Fetcher not configured. Phase 2 wires the httpx fetcher here.")`. Documented intentional stub at the D-15 boundary. |
| `src/domain/protocols/email.py` | IEmailService Protocol | VERIFIED | Verbatim port; `class IEmailService(Protocol)`, `EmailMessage`, `EmailResult` dataclasses present. |
| `src/domain/protocols/fetcher.py` | IFetcher Protocol | VERIFIED | Verbatim port; `class IFetcher(Protocol)` declared. |
| `src/domain/protocols/__init__.py` | Re-exports for both protocols | VERIFIED | Re-exports `EmailMessage`, `EmailResult`, `IEmailService`, `IFetcher`. |
| `src/domain/__init__.py` | Public re-exports of 5 symbols | VERIFIED | 30 LOC source-LOC parity; re-exports `PriceTrackerService`, `PriceParser`, `PriceExtractionResult`, `PriceNotifier`, `PriceCheckScheduler`. `get_price_tracker()` raises `NotImplementedError` — verbatim from source. |
| `src/domain/result.py` | PriceExtractionResult dataclass | VERIFIED | 19 LOC pure copy. |
| `src/domain/models.py` | ORM models with tenant_id, no price_tracker_ prefix | VERIFIED | 157 LOC (-4 vs source 161, expected delta from FK collapse during context_id→tenant_id rename). 5 `__tablename__` values: `stores`, `products`, `product_stores`, `price_points`, `watches`. `Product.tenant_id` and `PriceWatch.tenant_id` declared as `Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)` with no FK. `from infra.db import Base, _utc_now` present. `grep -c context_id` = 0; `grep -c price_tracker_` = 0. |
| `src/domain/parser.py` | PriceParser + LITELLM constant preserved | VERIFIED | 226 LOC source parity; `class PriceParser` defined; `LITELLM_API_BASE` preserved (Phase-2 swap deferred). |
| `src/domain/notifier.py` | PriceNotifier | VERIFIED | 333 LOC source parity; `class PriceNotifier` defined. |
| `src/domain/scheduler.py` | PriceCheckScheduler | VERIFIED | 487 LOC source parity; `class PriceCheckScheduler` defined; `grep -c context_id` = 0. |
| `src/domain/service.py` | PriceTrackerService with tenant_id | VERIFIED | 590 LOC source parity; `class PriceTrackerService` defined; `from infra.providers import get_fetcher` present; `grep -c context_id` = 0 (7 source occurrences propagated to tenant_id). |
| `src/domain/extractors/base.py` | PriceExtractor base | VERIFIED | 20 LOC source parity. |
| `src/domain/extractors/willys_api.py` | WillysApiExtractor | VERIFIED | 101 LOC source parity. |
| `src/domain/stores/__init__.py` | get_store_hints + 5 *_HINTS | VERIFIED | 66 LOC pure copy; `DOZ_HINTS` defined and registered. |
| `alembic.ini` | Alembic config wired for asyncpg | VERIFIED | `script_location = alembic`; `sqlalchemy.url = postgresql+asyncpg://...`. |
| `alembic/env.py` | Async env importing Base.metadata | VERIFIED | `from infra.db import Base`, `import domain.models  # registers ORM models`, `target_metadata = Base.metadata`. |
| `alembic/versions/0001_initial.py` | Single squashed initial migration | VERIFIED | 168 LOC; `revision = "0001_initial"`, `down_revision = None`. 5 `op.create_table` calls, 9 `op.create_index`, 1 `op.bulk_insert` (5 store rows). 11 `postgresql.UUID(as_uuid=True)`, 3 `postgresql.JSONB`. `grep -c price_tracker_` = 0; `grep -cE "tenants\|contexts.id"` = 0. |
| `tests/__init__.py` | Marker | VERIFIED | Present (1 LOC marker). |
| `tests/test_extractors.py` | 12 tests | VERIFIED | 12 test functions; imports rewritten to `from domain.*`; no `context_id`. |
| `tests/test_notifier.py` | 14 tests | VERIFIED | 14 test functions; imports `from domain.protocols.email import EmailMessage, EmailResult`. |
| `tests/test_parser.py` | 14 tests | VERIFIED | 14 test functions; `mock.patch("domain.stores.get_store_hints")` rewrite verified. |
| `tests/test_scheduler.py` | 13 tests | VERIFIED | 13 test functions; ORM kwargs use `tenant_id=`. |
| `tests/test_service.py` | 14 tests | VERIFIED | 14 test functions; ORM kwargs use `tenant_id=`. |
| `tests/conftest.py` | Minimal pytest scaffolding | NOT CREATED (intentional) | Plan 04 SUMMARY documents the executor decision: source repo had no conftest.py and each test file constructs its own MagicMock/AsyncMock fixtures inline; adding one would have been an unsolicited refactor (CLAUDE.md "don't add abstractions beyond what each phase requires"). pytest discovers and runs the suite green without it. See "Wording-lag note" below. |
| `Dockerfile` | Multi-stage python:3.12-slim build | VERIFIED | 54 LOC; two `FROM python:3.12-slim` stages (builder + final); CMD per D-11 literal. |
| `.dockerignore` | Build-context hygiene | VERIFIED | Present; excludes .git, .venv, .planning, tests, *.md. |
| `docker-compose.yml` | Postgres-only, no Traefik | VERIFIED | 26 LOC; postgres:16-alpine; named volume `price_tracker_pg_data`; healthcheck on `pg_isready`; no app service (D-12); the only "Traefik" string is a comment explaining the absence (D-13). |
| `.env.template` | DATABASE_URL + Phase-2/3/4 placeholders | VERIFIED | DATABASE_URL active; OPENROUTER_*, SMTP_*, ALLOWED_ENTRA_EMAIL, MCP_BEARER_TOKEN documented as commented placeholders. No fastapi-azure-auth/pyjwt/ALLOWED_ENTRA_OID (D-17). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `src/domain/notifier.py` | `src/domain/protocols/email.py` | `from domain.protocols.email import ...` | WIRED | Import present; tests import the same path. |
| `src/domain/scheduler.py` | `src/domain/protocols/__init__.py` | `from domain.protocols import IEmailService, IFetcher` | WIRED | Pytest exercises this import path successfully. |
| `src/domain/models.py` | `src/infra/db.py` | `from infra.db import Base, _utc_now` | WIRED | `alembic upgrade head` succeeds, proving `Base.metadata` resolves and includes price-tracker tables (autogen would otherwise emit drift). |
| `src/domain/service.py` | `src/infra/providers.py` | `from infra.providers import get_fetcher` | WIRED (stub) | Import resolves; runtime call raises `NotImplementedError` per D-15 boundary; pytest mocks at the fetcher boundary so this isn't hit. |
| `alembic/env.py` | `src/infra/db.py` + `src/domain/models.py` | `from infra.db import Base` + `import domain.models` | WIRED | Live `alembic upgrade head` produced exactly 5 expected tables — proves `Base.metadata` includes the registered models. `alembic check` confirms zero drift. |
| `alembic/versions/0001_initial.py` | `src/domain/models.py` | Schema hand-derived from final models.py state (D-07) | WIRED | `alembic check` returns "No new upgrade operations detected", proving the migration is a faithful rendering of `Base.metadata`. |
| `op.bulk_insert` in `0001_initial.py` | 5 store rows | Literal data inline in migration body | WIRED | `SELECT slug FROM stores ORDER BY slug` returned `apotea, doz, ica, med24, willys` after `alembic upgrade head`. |
| `Dockerfile` | `pyproject.toml` + `poetry.lock` | `COPY pyproject.toml poetry.lock ./` in builder stage | WIRED | Image builds and produces the `/app/.venv` containing all 5 runtime deps. |
| `Dockerfile` | `src/` | `COPY src ./src` in final stage | WIRED | Image final stage contains the source tree (verified by 167 MB image size + step 12 `COPY src ./src`). |

### Behavioral Spot-Checks (Step 7b)

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Pytest suite passes | `poetry run pytest -q` | `67 passed, 8 warnings in 0.46s` | PASS |
| Poetry env is Python 3.12 | `poetry env info | grep Python` | `Python: 3.12.3` | PASS |
| `poetry check` accepts manifest | `poetry check` | `All set!` | PASS |
| `alembic upgrade head` against fresh Postgres | `docker compose up postgres -d && poetry run alembic upgrade head` | exit 0; `Running upgrade  -> 0001_initial` | PASS |
| Schema produces exactly 6 tables | `\dt` | `alembic_version, price_points, product_stores, products, stores, watches` (6 rows) | PASS |
| 5 stores seeded with sorted slugs | `SELECT slug FROM stores ORDER BY slug` | `apotea, doz, ica, med24, willys` | PASS |
| tenant_id columns are uuid NOT NULL with no FK | `information_schema.columns + pg_constraint` | `products.tenant_id uuid NO`, `watches.tenant_id uuid NO`; FKs only to products/product_stores/stores; zero FKs to contexts/tenants | PASS |
| No phantom contexts/tenants table | `SELECT count(*) FROM information_schema.tables WHERE table_name IN ('contexts','tenants')` | `0` | PASS |
| Migration matches Base.metadata | `poetry run alembic check` | `No new upgrade operations detected` | PASS |
| `docker build` succeeds and produces 167 MB image | `docker build -t price-tracker:phase1-verify .` | exit 0; `167MB` | PASS |
| Phantom-dep imports resolve | `poetry run python -c "from domain.tenant import DEFAULT_TENANT_ID; from infra.db import Base, _utc_now; from infra.providers import get_fetcher; from domain.protocols import IEmailService, IFetcher; from domain.protocols.email import EmailMessage, EmailResult"` (verified during Plan 01-01) | exit 0 | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| DOMAIN-01 | 01-02 | Domain modules ported byte-equivalent from `modules/price_tracker/` to `src/domain/` | SATISFIED | All 6 listed modules (`models.py`, `service.py`, `scheduler.py`, `parser.py`, `notifier.py`, `result.py`) present with verified LOC parity vs source (the −4 LOC delta in `models.py` is the documented FK-collapse side-effect of context_id → tenant_id rename). Three documented transforms applied (D-16) and audited via grep gates: zero `from modules.price_tracker`, zero `from core.`, zero `context_id`, zero `price_tracker_`. |
| DOMAIN-02 | 01-02 | Extractors ported to `src/domain/extractors/` | SATISFIED | `extractors/__init__.py` (1 LOC), `extractors/base.py` (20 LOC), `extractors/willys_api.py` (101 LOC) present at canonical paths with byte-LOC parity. `class WillysApiExtractor` and `class PriceExtractor` both verified. |
| DOMAIN-03 | 01-02 | Stores helper ported to `src/domain/stores/` | SATISFIED | `stores/__init__.py` (66 LOC, pure copy). `get_store_hints` returns dict keyed by `{ica, willys, apotea, med24, doz}` (verified at runtime by Plan 02). |
| DB-01 | 01-03 | Three source migrations squashed into single `alembic/versions/0001_initial.py` | SATISFIED | Single `0001_initial.py` is the only file in `alembic/versions/`. The squash actually folds FOUR source migrations (initial + price_drop_threshold + unit_price_alerts + add_doz_store) — see Plan 03 SUMMARY note that REQUIREMENTS.md "three" is the pre-Doz wording from before the source repo added the 4th migration. Substantive intent (single hand-derived squash from final models.py state, D-07) satisfied. |
| DB-02 | 01-03 | Schema uses NOT-NULL `tenant_id` UUID column on tenant-scoped tables | SATISFIED | Live `information_schema.columns` query confirms `products.tenant_id uuid NO` and `watches.tenant_id uuid NO`. `pg_constraint` shows zero FKs to a `contexts` or `tenants` table. Free-floating UUID per D-03. |
| DB-03 | 01-03 | Initial migration seeds [default tenant row +] 5 stores with slug, store_type, base_url, parser_config | SATISFIED (with wording lag) | 5 stores seeded with verified slugs (apotea, doz, ica, med24, willys). Per the user's explicit verification note — REQUIREMENTS.md DB-03 "seeds a default tenant row" wording is a known IAP-pivot lag (D-03/D-10 dropped the tenants table). Treated as resolved-in-spec / pending REQUIREMENTS.md rewrite at the D-19 roadmap reassess; not a Phase 1 gap. |
| DB-04 | 01-03 | Tables renamed without `price_tracker_` prefix | SATISFIED | `\dt` lists `stores, products, product_stores, price_points, watches`. `grep -c price_tracker_ src/domain/models.py` = 0; `grep -c price_tracker_ alembic/versions/0001_initial.py` = 0 (after Plan 03's deviation #2 reworded forensic comments). |
| DB-05 | 01-03 | `alembic upgrade head` succeeds against fresh Postgres | SATISFIED | Verified live during this verification: `docker compose up postgres -d` (fresh `postgres:16-alpine`, `down -v` first to remove the named volume) → `poetry run alembic upgrade head` → exit 0 with `Running upgrade  -> 0001_initial`. |
| TEST-01 | 01-04 | Five source test files ported to `tests/` (~1,800 LOC total) | SATISFIED | All 5 test files present at canonical paths; `wc -l tests/test_*.py` total = 1,787 LOC (within the ~1,800 target). Per-file test counts (12 + 14 + 14 + 13 + 14 = 67) match source counts exactly per Plan 04 SUMMARY. |
| TEST-02 | 01-04 | Fixtures rebased — mocks for sessions/fetchers/email/LLM | SATISFIED (with wording lag) | `unittest.mock` MagicMock/AsyncMock used inline in every test file (counts: extractors=23, notifier=15, parser=19, scheduler=58, service=37). `MockEmailService` defined inline in `test_notifier.py` satisfies the `IEmailService` Protocol. Zero `aiosqlite`, zero `InMemoryAsyncSession`, zero `MockLLMClient` references — that wording in REQUIREMENTS.md predates the D-04 mocks-only decision. **Wording-lag exception:** TEST-02 literally says "in `conftest.py`", but Plan 04 SUMMARY documents the deliberate decision to NOT create one (source repo had none, no shared fixtures to put there, would be an unsolicited refactor per CLAUDE.md). The substantive intent — replacing platform-specific test doubles with new equivalents — is satisfied because there were no platform-specific test doubles in the source to begin with (mocks were inline in each test). Same kind of pre-IAP-pivot wording lag the user already flagged for DB-03. Pending REQUIREMENTS.md rewrite at the D-19 roadmap reassess. |
| TEST-03 | 01-04 | Full pytest suite green | SATISFIED | `poetry run pytest -q` reports `67 passed, 8 warnings in 0.46s`. Eight warnings are documented pre-existing AsyncMock unawaited-coroutine warnings, preserved verbatim from source. |
| DEPLOY-01 | 01-01 | `pyproject.toml` (poetry, Python 3.12) declares all required dependencies | SATISFIED (with documented dep-list shrinkage per D-04, D-17, plus Phase 2-4 deps deferred) | pyproject.toml present with PEP 621 layout, Python `>=3.12,<4.0`, runtime deps `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `httpx`, `pydantic`, dev deps `pytest`, `pytest-asyncio`. **Dep-list shrinkage:** REQUIREMENTS.md DEPLOY-01 lists `fastapi`, `uvicorn`, `aiosmtplib`, `cryptography`, `pyjwt`, `fastmcp`, `fastapi-azure-auth`, `aiosqlite` — all of these are intentionally absent in Phase 1: `aiosqlite` per D-04; `fastapi-azure-auth`, `pyjwt`, `cryptography` per D-17 (IAP topology shift); `fastapi`, `uvicorn`, `aiosmtplib`, `fastmcp` are Phase-2/3/4 deps that get added in their respective phases. This is the same wording-lag pattern the user flagged for DB-03 — the dep list reflects the pre-IAP-pivot extraction plan. Pending REQUIREMENTS.md rewrite at the D-19 roadmap reassess. |
| DEPLOY-02 | 01-05 | Dockerfile based on `python:3.12-slim` builds and runs the app | SATISFIED (build only — D-11) | `docker build -t price-tracker:phase1-verify .` exit 0; image 167 MB. Multi-stage Dockerfile, both stages `python:3.12-slim`. The "and runs the app" portion is intentionally NOT verified per D-11 (CMD points at `src.api.app:app` which Phase 2 implements; Phase 1 gate is `docker build`, not `docker run`). |

**Coverage summary:** 13/13 phase requirements satisfied. Three (DB-03, TEST-02, DEPLOY-01) carry wording-lag annotations from the IAP pivot — all flagged for resolution at the D-19 roadmap reassess scheduled between Phase 1 and Phase 2. Substantive intent verified for each.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/domain/__init__.py` | 27 | `raise NotImplementedError` in `get_price_tracker()` | Info | Verbatim from source per Plan 02 mapping; documented as "placeholder for dependency injection". Does not affect Phase 1 goal. |
| `src/infra/providers.py` | 2 | `raise NotImplementedError` in `get_fetcher()` | Info | Deliberate D-15 boundary stub; Phase 2 wires the httpx fetcher. Pytest mocks at this boundary so it isn't reached at test time. Documented in Plan 01-01 SUMMARY. |
| `src/domain/parser.py` | (LITELLM_API_BASE) | Source-repo constant points at `http://litellm:4000` | Info (deferred) | Preserved verbatim per Plan 02 (Phase 2 swaps for OpenRouter direct per REQ INFRA-03/04). Tests mock at the HTTP layer so the URL is never resolved against a real network. |

No blocking anti-patterns. No `TODO`/`FIXME`/`HACK`/`XXX`/`PLACEHOLDER` comments anywhere in `src/`, `tests/`, or `alembic/`.

### Human Verification Required

None. All four ROADMAP gates are programmatically verifiable and verified live during this verification run. No visual UI, no real-time behavior, no external service integration to spot-check at this phase boundary (those land in Phases 2-4).

### Gaps Summary

No gaps. All 4 ROADMAP success criteria verified by live evidence (not just SUMMARY claims). All 13 phase-scoped requirement IDs satisfied — three carry wording-lag annotations identical in shape to the user's pre-flagged DB-03 case (TEST-02 mentions `conftest.py` and platform-specific mocks; DEPLOY-01 lists Phase 2-4 deps and IAP-dropped auth libs). All wording-lag items flagged for the D-19 roadmap reassess between Phase 1 and Phase 2.

The verbatim-port doctrine is honored: 11 source modules ported with only the three documented transforms (D-16) — import rewrites, `context_id` → `tenant_id`, `price_tracker_` prefix drop. Three-way audit (models.py ↔ migration ↔ tests) all align (`alembic check` reports zero drift).

Phase 1 is shippable as-claimed. Phase 2 may proceed.

---

*Verified: 2026-05-04T11:30:00Z*
*Verifier: Claude (gsd-verifier)*
