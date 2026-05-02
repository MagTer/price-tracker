---
phase: 01-skeleton-domain-copy
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - pyproject.toml
  - .gitignore
  - src/domain/tenant.py
  - src/domain/protocols/__init__.py
  - src/domain/protocols/email.py
  - src/domain/protocols/fetcher.py
  - src/infra/__init__.py
  - src/infra/db.py
  - src/infra/providers.py
autonomous: true
requirements:
  - DEPLOY-01
user_setup: []
tags:
  - python
  - poetry
  - sqlalchemy
  - skeleton

must_haves:
  truths:
    - "src-layout package roots exist (src/domain, src/infra) so verbatim-ported domain modules in Plan 02 import cleanly (D-15, D-16)"
    - "DEFAULT_TENANT_ID constant lives in src/domain/tenant.py with the locked UUID f21b6620-c793-46e3-a354-dfcd9956b4a2 — no env var, no tenants table (D-01, D-02, D-03)"
    - "Base = DeclarativeBase() and _utc_now() helper live in src/infra/db.py and are importable as `from infra.db import Base, _utc_now` (D-15)"
    - "core.protocols.email and core.protocols.fetcher are ported into src/domain/protocols/ so notifier.py and scheduler.py keep their typing contracts (REQ DOMAIN-01)"
    - "src/infra/providers.py exports get_fetcher() that raises NotImplementedError so service.py imports resolve while Phase 2 wires httpx (D-15 boundary)"
    - "pyproject.toml declares the Phase 1 dep set on Python 3.12 and DROPS fastapi-azure-auth + pyjwt (D-17), keeps aiosqlite OUT (D-04)"
    - "poetry check exits 0 and poetry lock --no-update succeeds — pyproject.toml is internally consistent"
  artifacts:
    - path: "pyproject.toml"
      provides: "Poetry project metadata + Python 3.12 dep set"
      contains: "python = \"^3.12\""
    - path: "src/domain/tenant.py"
      provides: "DEFAULT_TENANT_ID UUID constant (D-01)"
      contains: "f21b6620-c793-46e3-a354-dfcd9956b4a2"
    - path: "src/infra/db.py"
      provides: "Base + _utc_now (D-15)"
      contains: "class Base(DeclarativeBase)"
    - path: "src/domain/protocols/email.py"
      provides: "EmailMessage, EmailResult, IEmailService dataclasses + Protocol"
      contains: "class IEmailService(Protocol)"
    - path: "src/domain/protocols/fetcher.py"
      provides: "IFetcher Protocol"
      contains: "class IFetcher(Protocol)"
    - path: "src/infra/providers.py"
      provides: "get_fetcher() stub for Phase 2 wiring"
      contains: "def get_fetcher"
  key_links:
    - from: "src/domain/notifier.py (Plan 02)"
      to: "src/domain/protocols/email.py"
      via: "from domain.protocols.email import EmailMessage, IEmailService"
      pattern: "domain\\.protocols\\.email"
    - from: "src/domain/scheduler.py (Plan 02)"
      to: "src/domain/protocols/__init__.py"
      via: "from domain.protocols import IEmailService, IFetcher"
      pattern: "domain\\.protocols"
    - from: "src/domain/models.py (Plan 02)"
      to: "src/infra/db.py"
      via: "from infra.db import Base, _utc_now"
      pattern: "infra\\.db"
    - from: "src/domain/service.py (Plan 02)"
      to: "src/infra/providers.py"
      via: "from infra.providers import get_fetcher"
      pattern: "infra\\.providers"
---

<objective>
Stand up the empty package skeleton, dependency manifest, and the small set of new files that make the verbatim domain port (Plan 02) compile without rewriting any internal logic.

Purpose: Plan 02 ports ~2,034 LOC verbatim from `ai-agent-platform`. That code imports from `core.db.models`, `core.protocols.email`, `core.protocols`, and `core.providers` — none of which exist in this repo. Rather than touching 11 ported files to invent new import targets, this plan creates the import targets first under the new layout so the port is a mechanical name swap (`core.X` → `infra.X` / `domain.protocols.X`).

Output: A buildable Python skeleton — `poetry install` resolves, package directories exist, and the four "phantom dep" modules (`infra/db.py`, `infra/providers.py`, `domain/protocols/email.py`, `domain/protocols/fetcher.py`) are in place.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
@EXTRACTION.md
@CLAUDE.md

<interfaces>
<!-- Phantom-dep targets that Plan 02 will import. Spec verbatim from source. -->

The four protocols + helpers ported here exactly preserve what the source modules already import:

```python
# src/domain/protocols/email.py — verbatim port of
# ai-agent-platform/services/agent/src/core/protocols/email.py
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass
class EmailMessage:
    to: list[str]
    subject: str
    html_body: str
    text_body: str | None = None
    reply_to: str | None = None

@dataclass
class EmailResult:
    success: bool
    message_id: str | None = None
    error: str | None = None

@runtime_checkable
class IEmailService(Protocol):
    async def send(self, message: EmailMessage) -> EmailResult: ...
    async def send_batch(self, messages: list[EmailMessage]) -> list[EmailResult]: ...
    def is_configured(self) -> bool: ...
```

```python
# src/domain/protocols/fetcher.py — verbatim port of
# ai-agent-platform/services/agent/src/core/protocols/fetcher.py
from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class IFetcher(Protocol):
    async def fetch(self, url: str) -> dict[str, Any]: ...
    async def search(self, query: str, k: int = 5, lang: str = "en") -> dict[str, Any]: ...
    async def research(self, query: str, k: int = 5, model: str = "gpt-3.5-turbo") -> dict[str, Any]: ...
    async def close(self) -> None: ...
```

```python
# src/domain/protocols/__init__.py — re-export
from domain.protocols.email import EmailMessage, EmailResult, IEmailService
from domain.protocols.fetcher import IFetcher

__all__ = ["EmailMessage", "EmailResult", "IEmailService", "IFetcher"]
```

```python
# src/infra/db.py — Base + _utc_now (D-15). Replaces source `core.db.models`.
from datetime import UTC, datetime
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

def _utc_now() -> datetime:
    return datetime.now(UTC)
```

```python
# src/infra/providers.py — Phase-2 placeholder so service.py imports resolve.
def get_fetcher():  # type: ignore[no-untyped-def]
    raise NotImplementedError(
        "Fetcher not configured. Phase 2 wires the httpx fetcher here."
    )
```

```python
# src/domain/tenant.py — D-01..D-03
import uuid

DEFAULT_TENANT_ID: uuid.UUID = uuid.UUID("f21b6620-c793-46e3-a354-dfcd9956b4a2")
"""Single-tenant constant. No env var (D-02). No tenants table (D-03)."""
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create pyproject.toml with Phase 1 dep set</name>
  <files>pyproject.toml, .gitignore</files>
  <read_first>
    - /home/magnus/dev/price-tracker/.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md (D-04, D-17 — drop aiosqlite + fastapi-azure-auth + pyjwt)
    - /home/magnus/dev/price-tracker/EXTRACTION.md §8 Phase 1 subsection (original dep list — for reference only; Phase 1 trims it)
    - /home/magnus/dev/price-tracker/CLAUDE.md (locked stack)
    - /home/magnus/dev/price-tracker/.gitignore (current state, before adding entries)
  </read_first>
  <action>
Create `pyproject.toml` at the repo root with **Poetry, Python 3.12, src-layout** (default per planner discretion). Dep set is the Phase-1 minimum that lets the verbatim domain port import cleanly + tests run + alembic operate.

Concrete pyproject.toml content:

```toml
[tool.poetry]
name = "price-tracker"
version = "0.1.0"
description = "Standalone Swedish grocery and pharmacy price tracker (extracted from ai-agent-platform)"
authors = ["Magnus Ternstrom <magnus.ternstrom@gmail.com>"]
readme = "README.md"
packages = [{ include = "domain", from = "src" }, { include = "infra", from = "src" }]

[tool.poetry.dependencies]
python = "^3.12"
sqlalchemy = { version = "^2.0", extras = ["asyncio"] }
alembic = "^1.13"
asyncpg = "^0.29"
httpx = "^0.27"
pydantic = "^2.7"

[tool.poetry.group.dev.dependencies]
pytest = "^8.2"
pytest-asyncio = "^0.23"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

Dep rationale (every line is intentional — do NOT add/remove without revisiting CONTEXT.md):
- `sqlalchemy[asyncio]` + `asyncpg` — needed by `models.py`, `service.py`, `scheduler.py` (REQ DOMAIN-01)
- `alembic` — REQ DB-01
- `httpx` — used directly by `parser.py` and `extractors/willys_api.py` (DOMAIN-01, DOMAIN-02)
- `pydantic` — Phase 2 schemas need it; cheap to declare now so the venv is warm
- `pytest`, `pytest-asyncio` — REQ TEST-03

Explicitly EXCLUDED in Phase 1 (each is locked by a decision):
- `aiosqlite` — dropped per D-04 (mocks-only test strategy)
- `fastapi`, `uvicorn` — Phase 2 (no FastAPI app yet per CONTEXT.md `<domain>` boundary)
- `aiosmtplib` — Phase 2 (`infra/email.py` lands then)
- `cryptography`, `pyjwt`, `fastapi-azure-auth` — dropped per D-17 (IAP topology shift)
- `fastmcp` — Phase 4

Then update `.gitignore` (do NOT overwrite — append). Add these entries if not already present (read current `.gitignore` first; the repo has 13 lines):
```
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Poetry
poetry.lock.bak

# Local env
.env
.env.local
*.db
```
  </action>
  <verify>
    <automated>poetry check 2>&amp;1 | grep -q "All set!" &amp;&amp; poetry lock --no-update 2>&amp;1 | tail -3 &amp;&amp; grep -c "^aiosqlite\|^fastapi-azure-auth\|^pyjwt" pyproject.toml | grep -q "^0$"</automated>
  </verify>
  <acceptance_criteria>
- `poetry check` exits 0 with "All set!" output
- `poetry lock --no-update` succeeds and produces `poetry.lock`
- `grep -c "aiosqlite" pyproject.toml` returns 0 (D-04)
- `grep -c "fastapi-azure-auth\|pyjwt" pyproject.toml` returns 0 (D-17)
- `grep -c "fastmcp\|aiosmtplib\|fastapi" pyproject.toml` returns 0 (Phase 2-4 deps not present yet)
- `grep -c "python = \"\^3.12\"" pyproject.toml` returns 1
- `grep -E "^sqlalchemy|^alembic|^asyncpg|^httpx|^pydantic|^pytest" pyproject.toml | wc -l` returns &gt;= 5
  </acceptance_criteria>
  <done>pyproject.toml + poetry.lock committed; deps locked to Phase 1 minimum; aiosqlite + auth libs deliberately absent.</done>
</task>

<task type="auto">
  <name>Task 2: Create package layout + phantom-dep target modules</name>
  <files>
    src/domain/tenant.py,
    src/domain/protocols/__init__.py,
    src/domain/protocols/email.py,
    src/domain/protocols/fetcher.py,
    src/infra/__init__.py,
    src/infra/db.py,
    src/infra/providers.py
  </files>
  <read_first>
    - /home/magnus/dev/ai-agent-platform/services/agent/src/core/protocols/email.py (verbatim port source)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/core/protocols/fetcher.py (verbatim port source)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/core/protocols/__init__.py (verify which symbols are re-exported)
    - /home/magnus/dev/price-tracker/.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md (D-01, D-15, D-16)
    - /home/magnus/dev/price-tracker/EXTRACTION.md §4 (target layout)
  </read_first>
  <action>
Create the package layout exactly per the `<interfaces>` block in `<context>` above. Every file body is specified there — copy them in.

File-by-file:

NOTE on `src/__init__.py` and `src/domain/__init__.py`: NEITHER is created by this plan to avoid a Wave-1 file-write race with Plan 02 (which owns `src/domain/__init__.py` as the verbatim port of the source `__init__.py`). `src/` does NOT need an `__init__.py` because pyproject.toml's `packages = [{include="domain", from="src"}, {include="infra", from="src"}]` declares `src/` as a path prefix, not a package. The `src/domain/` directory is created implicitly when this task writes `src/domain/tenant.py`.

1. **`src/domain/tenant.py`** — copy the body from `<interfaces>`. Single constant + docstring referencing D-01..D-03.

2. **`src/domain/protocols/__init__.py`** — copy from `<interfaces>` (re-exports email + fetcher symbols). This mirrors the source's `core/protocols/__init__.py` shape so `from domain.protocols import IEmailService, IFetcher` (used in scheduler.py) resolves. This file is owned by Plan 01 (Plan 02 does not touch it).

3. **`src/domain/protocols/email.py`** — VERBATIM port of `/home/magnus/dev/ai-agent-platform/services/agent/src/core/protocols/email.py`. Read the source, copy byte-for-byte (including docstrings, `__all__`, `from __future__ import annotations`).

4. **`src/domain/protocols/fetcher.py`** — VERBATIM port of `/home/magnus/dev/ai-agent-platform/services/agent/src/core/protocols/fetcher.py`. Same byte-for-byte rule.

5. **`src/infra/__init__.py`** — empty file. This IS needed because `infra` is declared as a package in pyproject.toml and the directory must contain an `__init__.py` for `from infra.db import Base` to resolve under the src-layout.

6. **`src/infra/db.py`** — copy from `<interfaces>`. This is the new home for `Base` and `_utc_now` (D-15). Use `from sqlalchemy.orm import DeclarativeBase` (SA 2.0 idiom; matches source `core.db.models.Base` semantics).

7. **`src/infra/providers.py`** — copy from `<interfaces>`. The single `get_fetcher()` raises `NotImplementedError`. This keeps `service.py:484`'s `fetcher = get_fetcher()` import-resolvable in Phase 1; Phase 2 will replace the body with a real httpx wrapper.

**Why these specific locations** (Claude's discretion choices, locked here):
- `Base` lives in `src/infra/db.py`, NOT `src/domain/_base.py` — keeps domain free of infra concerns and matches Phase 2 plan to land the async session factory next to it.
- src-layout (`from domain.X` not `from src.domain.X`) — matches modern Poetry convention; the `packages = [{include = "domain", from = "src"}, ...]` table makes both packages installable.
  </action>
  <verify>
    <automated>poetry run python -c "from domain.tenant import DEFAULT_TENANT_ID; from infra.db import Base, _utc_now; from domain.protocols import IEmailService, IFetcher; from domain.protocols.email import EmailMessage, EmailResult; from infra.providers import get_fetcher; assert str(DEFAULT_TENANT_ID) == 'f21b6620-c793-46e3-a354-dfcd9956b4a2', f'wrong UUID: {DEFAULT_TENANT_ID}'; print('OK')"</automated>
  </verify>
  <acceptance_criteria>
- All 9 files exist at paths listed in `<files>`
- `grep -c "f21b6620-c793-46e3-a354-dfcd9956b4a2" src/domain/tenant.py` returns 1
- `grep -c "DeclarativeBase" src/infra/db.py` returns &gt;= 1
- `grep -c "datetime.now(UTC)" src/infra/db.py` returns 1
- `grep -c "NotImplementedError" src/infra/providers.py` returns &gt;= 1
- `grep -c "class IEmailService(Protocol)" src/domain/protocols/email.py` returns 1
- `grep -c "class IFetcher(Protocol)" src/domain/protocols/fetcher.py` returns 1
- `grep -c "EmailMessage\|EmailResult\|IEmailService" src/domain/protocols/__init__.py` returns &gt;= 3
- `grep -c "IFetcher" src/domain/protocols/__init__.py` returns &gt;= 1
- `grep -rn "tenants" src/domain/tenant.py` returns 0 lines mentioning a tenants TABLE (D-03 — only the comment about why there is no table)
- The `<verify>` Python one-liner exits 0
  </acceptance_criteria>
  <done>Package layout in place. Phase 02's verbatim port can resolve all `from infra.db`, `from infra.providers`, `from domain.protocols`, `from domain.tenant` imports without further edits.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| n/a (Phase 1) | This plan ships pure code/skeleton — no network surface, no auth, no user input. Trust boundaries appear in Phase 2 (HTTP client → external sites) and Phase 3 (browser → admin UI). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-01 | Tampering | `src/domain/tenant.py` (`DEFAULT_TENANT_ID` constant) | mitigate | Constant is hardcoded (D-01) — accidentally rewriting it would silently orphan all data. Plan 03's migration seeds rows referencing the same UUID; Plan 04's tests assert it. Three-way cross-check is the audit. Severity: low. |
| T-01-02 | Supply Chain | `pyproject.toml` deps (sqlalchemy, asyncpg, httpx, pydantic, pytest, pytest-asyncio, alembic) | accept | All from PyPI, no `[tool.poetry.dependencies]` from git/path/url, no install scripts. `poetry.lock` committed (deterministic resolution). Pin-by-digest is OPTIONAL for v1 single-user app per CONTEXT.md `<security_threat_model>`. Severity: low. |
| T-01-03 | Information Disclosure | `.gitignore` | mitigate | Adds `.env` and `*.db` to `.gitignore` so credentials/data don't accidentally land in git. Severity: info. |

No high-severity threats in this plan.
</threat_model>

<verification>
After both tasks complete:

```bash
# Skeleton is buildable
poetry install --no-root --with dev
# All four phantom-dep targets are importable
poetry run python -c "from domain.tenant import DEFAULT_TENANT_ID; from infra.db import Base, _utc_now; from infra.providers import get_fetcher; from domain.protocols import IEmailService, IFetcher; from domain.protocols.email import EmailMessage, EmailResult; print('skeleton OK')"
# Locked decisions enforced
[ "$(grep -cE '^aiosqlite|^fastapi-azure-auth|^pyjwt|^fastapi |^uvicorn|^aiosmtplib|^fastmcp' pyproject.toml)" -eq 0 ] || (echo "FAIL: forbidden Phase-1 dep present"; exit 1)
echo "Skeleton verified."
```
</verification>

<success_criteria>
- `poetry install` resolves on Python 3.12 with the locked Phase-1 dep set (REQ DEPLOY-01)
- All 4 phantom-dep target modules importable from a fresh `python -c` invocation
- `DEFAULT_TENANT_ID` is the locked UUID from D-01 and lives in `src/domain/tenant.py`
- `Base` and `_utc_now` live in `src/infra/db.py` (D-15 location decision)
- No forbidden Phase 1 deps present in `pyproject.toml` (D-04, D-17)
- `.gitignore` covers `.env`, `*.db`, `__pycache__/`
</success_criteria>

<output>
After completion, create `.planning/phases/01-skeleton-domain-copy/01-01-SUMMARY.md` documenting:
- Final pyproject.toml dep list (with chosen version pins)
- Verification of D-04 (no aiosqlite) and D-17 (no auth libs)
- Confirmation that the four phantom-dep modules in `src/domain/protocols/`, `src/infra/db.py`, `src/infra/providers.py`, `src/domain/tenant.py` are in place and importable
- Any pip resolution surprises (e.g., asyncpg requiring extra system libs at install time)
</output>
