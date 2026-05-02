---
phase: 01-skeleton-domain-copy
plan: 01
subsystem: skeleton
tags:
  - python
  - poetry
  - sqlalchemy
  - skeleton
dependency-graph:
  requires: []
  provides:
    - "src.domain.tenant.DEFAULT_TENANT_ID (D-01)"
    - "src.infra.db.Base + _utc_now (D-15)"
    - "src.infra.providers.get_fetcher() stub"
    - "src.domain.protocols.{IEmailService, IFetcher, EmailMessage, EmailResult}"
  affects:
    - "01-02-domain-port (Wave 2): can mechanically rewrite core.X imports to domain.X / infra.X"
tech-stack:
  added:
    - "Poetry 2.x (PEP 621 [project] table)"
    - "SQLAlchemy 2.0 asyncio extras"
    - "asyncpg, httpx, pydantic, alembic"
    - "pytest 8.x + pytest-asyncio (asyncio_mode=auto)"
  patterns:
    - "src-layout: src/domain and src/infra declared as Poetry packages"
    - "Protocol-based dependency inversion preserved verbatim from source repo"
key-files:
  created:
    - "pyproject.toml (29 LOC)"
    - "src/domain/tenant.py (4 LOC)"
    - "src/domain/protocols/__init__.py (4 LOC)"
    - "src/domain/protocols/email.py (68 LOC, verbatim port)"
    - "src/domain/protocols/fetcher.py (69 LOC, verbatim port)"
    - "src/infra/__init__.py (0 LOC, package marker)"
    - "src/infra/db.py (11 LOC)"
    - "src/infra/providers.py (4 LOC)"
    - "poetry.lock (generated)"
  modified:
    - ".gitignore (appended Phase 1 Python/Poetry/local-env entries)"
decisions:
  - "Adapted plan to Poetry 2.x by moving project metadata into PEP 621 [project] table; deps use Poetry-style version-constraint strings (e.g. \"sqlalchemy[asyncio] (>=2.0,<3.0)\") so semantics match the original ^X.Y carets"
metrics:
  duration: "~10 min"
  completed: "2026-05-02"
  tasks: 2
  files: 9
---

# Phase 1 Plan 01: Skeleton + Phantom-Dep Targets Summary

Stood up the buildable Python skeleton (PEP 621 + Poetry 2.x manifest, src-layout package roots, Phase-1-minimum dep set) and the four phantom-dep modules (`infra/db.py`, `infra/providers.py`, `domain/protocols/email.py`, `domain/protocols/fetcher.py`) plus the locked tenant constant — so Plan 02's verbatim domain port becomes a mechanical name swap (`core.X` → `infra.X` / `domain.protocols.X`) with no logic edits to the ~2,034 LOC of ported code.

## What Was Built

| Artifact | LOC | Role |
|----------|-----|------|
| `pyproject.toml` | 29 | PEP 621 + Poetry, Python ^3.12, src-layout, Phase 1 minimum deps |
| `poetry.lock` | (gen) | Deterministic resolution of the dep set |
| `src/domain/tenant.py` | 4 | `DEFAULT_TENANT_ID` UUID constant (D-01..D-03) |
| `src/domain/protocols/email.py` | 68 | Verbatim port of source `core/protocols/email.py` |
| `src/domain/protocols/fetcher.py` | 69 | Verbatim port of source `core/protocols/fetcher.py` |
| `src/domain/protocols/__init__.py` | 4 | Re-exports `EmailMessage`, `EmailResult`, `IEmailService`, `IFetcher` |
| `src/infra/__init__.py` | 0 | Package marker required by src-layout |
| `src/infra/db.py` | 11 | `Base = DeclarativeBase()` + `_utc_now()` (D-15) |
| `src/infra/providers.py` | 4 | `get_fetcher()` stub raising `NotImplementedError` |
| `.gitignore` | (appended) | Python `__pycache__`, mypy/ruff/pytest caches, `*.db` |

## Final Phase-1 Dependency Set

Runtime deps (PEP 621 `[project].dependencies`):
- `sqlalchemy[asyncio] (>=2.0,<3.0)`
- `alembic (>=1.13,<2.0)`
- `asyncpg (>=0.29,<0.30)`
- `httpx (>=0.27,<0.28)`
- `pydantic (>=2.7,<3.0)`

Dev deps (`[tool.poetry.group.dev.dependencies]`):
- `pytest = "^8.2"` → resolved to 8.4.2
- `pytest-asyncio = "^0.23"` → resolved to 0.23.8

Locked Python: `>=3.12,<4.0`. Source deps resolved against Python 3.12.x venv at `/home/magnus/.cache/pypoetry/virtualenvs/price-tracker-RREIJDmU-py3.12`.

## Decision-Compliance Verification

- **D-04 (no aiosqlite):** `grep -c "aiosqlite" pyproject.toml` → `0`
- **D-17 (no auth libs):** `grep -cE "fastapi-azure-auth|pyjwt" pyproject.toml` → `0`
- **Phase 2-4 deps absent:** `grep -cE "fastmcp|aiosmtplib|^fastapi |^uvicorn" pyproject.toml` → `0`
- **D-01 (locked UUID):** `grep -c "f21b6620-c793-46e3-a354-dfcd9956b4a2" src/domain/tenant.py` → `1`; runtime check confirms `str(DEFAULT_TENANT_ID) == "f21b6620-c793-46e3-a354-dfcd9956b4a2"`
- **D-03 (no tenants table):** Only mention of "tenants" in `tenant.py` is the docstring `"""Single-tenant constant. No env var (D-02). No tenants table (D-03)."""` — exactly the explanatory comment the plan permits
- **D-15 (Base + _utc_now in `src/infra/db.py`):** confirmed via `grep -c "DeclarativeBase" src/infra/db.py` → 2; `grep -c "datetime.now(UTC)" src/infra/db.py` → 1

## Acceptance Criteria Met

Task 1 — pyproject.toml + .gitignore:
- [x] `poetry check` exits 0 with "All set!"
- [x] `poetry lock` succeeds and produces `poetry.lock`
- [x] No aiosqlite in pyproject.toml (D-04)
- [x] No `fastapi-azure-auth` / `pyjwt` in pyproject.toml (D-17)
- [x] No Phase 2-4 deps (`fastmcp`, `aiosmtplib`, `fastapi`, `uvicorn`)
- [x] Python pinned to `^3.12`
- [x] All 5 expected runtime deps + pytest present

Task 2 — Package layout + phantom-dep targets:
- [x] All 7 files exist at the paths in `<files>`
- [x] Locked UUID present in `tenant.py`
- [x] `DeclarativeBase` and `datetime.now(UTC)` present in `infra/db.py`
- [x] `NotImplementedError` raised by `infra/providers.get_fetcher()`
- [x] `IEmailService(Protocol)` and `IFetcher(Protocol)` declared
- [x] All 4 protocol symbols re-exported from `domain/protocols/__init__.py`
- [x] No `tenants` TABLE referenced in `tenant.py` (only the explanatory comment)
- [x] Verify one-liner exits 0:
      `from domain.tenant import DEFAULT_TENANT_ID; from infra.db import Base, _utc_now; from domain.protocols import IEmailService, IFetcher; from domain.protocols.email import EmailMessage, EmailResult; from infra.providers import get_fetcher; assert str(DEFAULT_TENANT_ID) == 'f21b6620-c793-46e3-a354-dfcd9956b4a2'; print('OK')`

Plan-level verification block:
- [x] `poetry install --with dev` resolves cleanly on Python 3.12
- [x] All 4 phantom-dep targets importable from a fresh `poetry run python -c` invocation
- [x] No forbidden Phase-1 dep present in `pyproject.toml`

## Commits

| Hash | Subject |
|------|---------|
| `7aaf2b3` | feat(01-01): add pyproject.toml with Phase 1 dep set |
| `e2b1b1e` | feat(01-01): create package layout + phantom-dep target modules |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Adapted pyproject.toml to Poetry 2.x PEP 621 layout**
- **Found during:** Task 1 verify step (`poetry check`)
- **Issue:** The plan supplied a Poetry-1.x-style `[tool.poetry]` block with `name`, `version`, `description`, `readme`, `authors`. Poetry 2.3.2 (the installed version) emits 5 deprecation warnings on those keys and the plan's acceptance criterion explicitly required `poetry check` to print `"All set!"` (warnings broke that).
- **Fix:** Moved project metadata into a `[project]` table per PEP 621. Translated `python = "^3.12"` to `requires-python = ">=3.12,<4.0"` and the deps list to PEP 508 strings with equivalent caret-range semantics (e.g. `"sqlalchemy[asyncio] (>=2.0,<3.0)"`). Kept `[tool.poetry] packages = [...]` so the src-layout package declarations Plan 02 relies on still work, and kept `[tool.poetry.group.dev.dependencies]` for pytest. Bumped `[build-system].requires` to `poetry-core>=2.0.0,<3.0.0`.
- **Files modified:** `pyproject.toml`
- **Result:** `poetry check` now prints `All set!` with exit 0 — the criterion as written passes. All Phase 1 dep-set rationale (D-04, D-17, no Phase 2-4 deps) is preserved unchanged. The same Poetry caret semantics are encoded in PEP 508 ranges.
- **Commit:** `7aaf2b3`

No other deviations. Both tasks executed exactly per the plan's `<action>` blocks.

## Notable Observations

- **`poetry install --no-root` is insufficient for the verify step.** `--no-root` skips the project install, so `from domain... from infra...` would fail. Re-ran `poetry install --with dev` (no `--no-root`) — that resolved the imports because the `packages = [{include="domain", from="src"}, {include="infra", from="src"}]` declaration installs both top-level packages into the venv site-packages. Worth flagging for Plan 05's docker build step (the Dockerfile likely needs to install with-root in the final stage too).
- **No pip resolution surprises.** `asyncpg` 0.29 wheels exist for Python 3.12 — no system-lib build required at install time. `cryptography` is no longer pulled in (D-17 win).
- **Poetry created a fresh venv** at `/home/magnus/.cache/pypoetry/virtualenvs/price-tracker-RREIJDmU-py3.12` — disposable, not in the repo, .gitignore covers it via `.venv/` (not used here but kept for the convention).
- **`grep -c` counts lines, not occurrences.** The plan's acceptance criterion `grep -c "EmailMessage\|EmailResult\|IEmailService" src/domain/protocols/__init__.py >= 3` returned 2 because all three symbols share a single import line. Verified per-symbol that each appears (each on 2 lines: import + `__all__`). Spirit of the criterion (all 3 symbols re-exported) is met; flagged for Plan 02's checker so it doesn't false-fail.

## Self-Check: PASSED

Files (all confirmed present):
- FOUND: pyproject.toml
- FOUND: poetry.lock
- FOUND: src/domain/tenant.py
- FOUND: src/domain/protocols/__init__.py
- FOUND: src/domain/protocols/email.py
- FOUND: src/domain/protocols/fetcher.py
- FOUND: src/infra/__init__.py
- FOUND: src/infra/db.py
- FOUND: src/infra/providers.py

Commits (both confirmed in `git log`):
- FOUND: 7aaf2b3
- FOUND: e2b1b1e
