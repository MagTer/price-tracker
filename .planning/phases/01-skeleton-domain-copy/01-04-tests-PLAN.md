---
phase: 01-skeleton-domain-copy
plan: 04
type: execute
wave: 3
depends_on: [01-02]
files_modified:
  - tests/__init__.py
  - tests/conftest.py
  - tests/test_parser.py
  - tests/test_service.py
  - tests/test_scheduler.py
  - tests/test_notifier.py
  - tests/test_extractors.py
autonomous: true
requirements:
  - TEST-01
  - TEST-02
  - TEST-03
user_setup: []
tags:
  - pytest
  - testing
  - port

must_haves:
  truths:
    - "All 5 source test files (test_parser, test_service, test_scheduler, test_notifier, test_extractors — ~1,787 LOC) ported into tests/ with imports rewritten (REQ TEST-01)"
    - "test_notifier.py replaces `from core.protocols.email` with `from domain.protocols.email` and the inline MockEmailService class still satisfies IEmailService Protocol (REQ TEST-02)"
    - "test_parser.py patch target rewrites from `modules.price_tracker.stores.get_store_hints` to `domain.stores.get_store_hints` (TEST-02)"
    - "All ORM constructions in test_service.py and test_scheduler.py use `tenant_id=` keyword (column rename propagation from Plan 02)"
    - "No fixtures reference aiosqlite, InMemoryAsyncSession, MockLLMClient, or IFetcher/IEmailService platform mocks — all per-test mocks use unittest.mock (D-04, D-05, D-06 — testcontainers reserved for future integration phases, not Phase 1; REQ TEST-02)"
    - "tests/conftest.py exists but contains only minimal shared scaffolding (project sys.path injection if needed; no fixture explosion) — source repo had NO conftest under tests/, so we keep it slim per planner discretion"
    - "`poetry run pytest` runs the full ported suite green (REQ TEST-03, ROADMAP gate 1) — exit code 0, all collected tests pass"
  artifacts:
    - path: "tests/conftest.py"
      provides: "Minimal pytest scaffolding (event_loop policy if needed, sys.path glue for src-layout)"
      contains: "import"
    - path: "tests/test_parser.py"
      provides: "Parser tests (PriceExtractionResult dataclass, prompt building, LLM cascade, API-first extractor)"
      contains: "def test_"
    - path: "tests/test_service.py"
      provides: "Service tests (CRUD, mock session)"
      contains: "TestPriceTrackerService"
    - path: "tests/test_scheduler.py"
      provides: "Scheduler tests (status, single-product check, alerts, weekly summary)"
      contains: "TestCheckSingleProduct"
    - path: "tests/test_notifier.py"
      provides: "Notifier tests (email composition + send)"
      contains: "TestPriceNotifier"
    - path: "tests/test_extractors.py"
      provides: "Willys API extractor tests (URL parsing, response parsing, HTTP mocking)"
      contains: "TestExtract"
  key_links:
    - from: "tests/test_*.py"
      to: "src/domain/*.py"
      via: "from domain.X import Y (rewrite of source `from modules.price_tracker.X`)"
      pattern: "domain\\."
    - from: "tests/test_notifier.py"
      to: "src/domain/protocols/email.py"
      via: "from domain.protocols.email import EmailMessage, EmailResult"
      pattern: "domain\\.protocols\\.email"
    - from: "tests/test_parser.py"
      to: "src/domain/stores/__init__.py (patched)"
      via: "patch('domain.stores.get_store_hints')"
      pattern: "patch\\(['\\\"]domain\\.stores"
---

<objective>
Port the 5 source test files into `tests/` and make them green against the ported domain modules. The tests already use `unittest.mock` heavily (no platform-specific test doubles to recreate per CONTEXT.md `<code_context>.Reusable Assets`), so this is primarily an import-rewrite operation plus column-rename propagation.

Purpose: REQ TEST-01, TEST-02, TEST-03. Ship gate 1 of Phase 1 — `pytest` runs the full ported suite green.

Output:
- 5 test files in `tests/` (~1,787 LOC total) with imports rewritten and `context_id` -> `tenant_id` propagated
- `tests/__init__.py` (1-line marker)
- `tests/conftest.py` (minimal — sys.path glue if needed for src-layout, no global fixtures)
- A green `pytest` run (exit 0, all tests pass)

Why no aiosqlite, no InMemoryAsyncSession (D-04, D-05): every test in the source already uses `MagicMock`/`AsyncMock` for sessions, fetchers, email, and LLM clients. There is NO test in Phase 1 scope that needs a real DB session. Schema fidelity is verified by `alembic upgrade head` in Plan 03 — not by pytest. This avoids inventing sqlite type-compat shims for `postgresql.UUID`/`JSONB`.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
@.planning/phases/01-skeleton-domain-copy/01-02-domain-port-PLAN.md
@CLAUDE.md

<interfaces>
Source -> target import rewrites for test files (extends Plan 02's mapping):

| Source import in tests | Rewritten import |
|---|---|
| `from modules.price_tracker.parser import PriceExtractionResult, PriceParser` | `from domain.parser import PriceExtractionResult, PriceParser` |
| `from modules.price_tracker.service import PriceTrackerService` | `from domain.service import PriceTrackerService` |
| `from modules.price_tracker.scheduler import PriceCheckScheduler` | `from domain.scheduler import PriceCheckScheduler` |
| `from modules.price_tracker.notifier import PriceNotifier` | `from domain.notifier import PriceNotifier` |
| `from modules.price_tracker.result import PriceExtractionResult` | `from domain.result import PriceExtractionResult` |
| `from modules.price_tracker.extractors.willys_api import WillysApiExtractor` | `from domain.extractors.willys_api import WillysApiExtractor` |
| `from core.protocols.email import EmailMessage, EmailResult` (test_notifier) | `from domain.protocols.email import EmailMessage, EmailResult` |

Patch-string rewrites (mock.patch references inside tests):

| Source patch target | Rewritten patch target |
|---|---|
| `patch("modules.price_tracker.stores.get_store_hints")` (test_parser line ~111) | `patch("domain.stores.get_store_hints")` |
| Any `patch("modules.price_tracker.X.Y")` in any other test | `patch("domain.X.Y")` |
| Any `patch("core.X.Y")` if present | `patch("domain.protocols.X.Y")` (rare; verify with grep before rewriting) |

Symbol-level rewrites for ORM construction (column rename propagation from Plan 02):

| Source code shape (in tests) | Rewritten shape |
|---|---|
| `Product(context_id=...)` keyword arg | `Product(tenant_id=...)` |
| `PriceWatch(context_id=...)` keyword arg | `PriceWatch(tenant_id=...)` |
| `Product.context_id` attribute access | `Product.tenant_id` |
| `PriceWatch.context_id` attribute access | `PriceWatch.tenant_id` |
| `mock_product.context_id = ...` | `mock_product.tenant_id = ...` |

Source test files at `/home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/`:

```
__init__.py           1 LOC  (marker only)
test_extractors.py  228 LOC
test_notifier.py    403 LOC  (defines local MockEmailService class)
test_parser.py      384 LOC  (uses mock.patch on stores.get_store_hints)
test_scheduler.py   416 LOC  (heavy ORM construction with kwargs)
test_service.py     356 LOC  (heavy ORM construction with kwargs)
TOTAL              1788 LOC
```

NO source `conftest.py` exists — fixtures are inline per test file. Decision (planner discretion): create a minimal `tests/conftest.py` that does nothing more than guarantee the `src/` path is importable when pytest is invoked from the repo root. Poetry's `packages = [{include="domain", from="src"}, {include="infra", from="src"}]` should handle this when running `poetry run pytest`, so conftest.py may end up empty. Keep it minimal — do NOT speculatively add fixtures.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Port the 5 test files with import + symbol rewrites</name>
  <files>tests/__init__.py, tests/test_parser.py, tests/test_service.py, tests/test_scheduler.py, tests/test_notifier.py, tests/test_extractors.py</files>
  <read_first>
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/__init__.py (1 LOC, marker)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_parser.py (384 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_service.py (356 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_scheduler.py (416 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_notifier.py (403 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_extractors.py (228 LOC)
    - src/domain/__init__.py (Plan 02 — confirms public symbols)
    - src/domain/protocols/email.py (Plan 01 — confirms EmailMessage, EmailResult, IEmailService)
  </read_first>
  <action>
Port each test file as a near-verbatim copy with these mechanical rewrites applied uniformly:

1. **Import rewrites** — apply the table from `<interfaces>`. After porting, the only `from` imports referencing project code MUST start with `from domain.` (or stdlib/third-party).

2. **Patch-string rewrites** — search each ported file for `patch(` and rewrite the string argument:
   - `"modules.price_tracker.X"` -> `"domain.X"` (e.g., `"modules.price_tracker.stores.get_store_hints"` -> `"domain.stores.get_store_hints"`)
   - Any `"core.X"` patch target — rewrite to `"domain.protocols.X"` if it exists (verify with grep first; the only known case is `core.protocols.email` symbols which are usually imported, not patched)

3. **Symbol rewrites** — `context_id` -> `tenant_id` everywhere in test bodies. After porting, `grep -c context_id tests/` MUST return 0. Use a careful sed-like replacement: target `context_id` as a whole word (Python identifiers are not substrings — there's no risk of breaking unrelated names like `something_context_id_else` in this codebase).

4. **DO NOT add new fixtures, helpers, or test cases.** Verbatim port = same tests doing the same things, just against the renamed module paths.

File-by-file:

- **`tests/__init__.py`** — copy source verbatim (1 LOC, just a marker docstring).

- **`tests/test_extractors.py`** (228 LOC) — single import rewrite needed:
  - `from modules.price_tracker.extractors.willys_api import WillysApiExtractor` -> `from domain.extractors.willys_api import WillysApiExtractor`
  - `from modules.price_tracker.result import PriceExtractionResult` -> `from domain.result import PriceExtractionResult`
  - No `context_id` references expected.

- **`tests/test_notifier.py`** (403 LOC) — rewrites:
  - `from core.protocols.email import EmailMessage, EmailResult` -> `from domain.protocols.email import EmailMessage, EmailResult`
  - `from modules.price_tracker.notifier import PriceNotifier` -> `from domain.notifier import PriceNotifier`
  - The local `class MockEmailService:` defined inside this file already implements the IEmailService Protocol shape (`send`, `send_batch`, `is_configured`) — it should pass `isinstance(mock, IEmailService)` runtime checks because the protocol is `@runtime_checkable`. No changes to the mock class needed.
  - Check for `context_id` references — there shouldn't be any (notifier has no ORM); verify with `grep -c context_id tests/test_notifier.py` after porting (must be 0).

- **`tests/test_parser.py`** (384 LOC) — rewrites:
  - `from modules.price_tracker.parser import PriceExtractionResult, PriceParser` -> `from domain.parser import PriceExtractionResult, PriceParser`
  - `patch("modules.price_tracker.stores.get_store_hints")` -> `patch("domain.stores.get_store_hints")` (line ~111 in source)
  - The HTTP-call patches inside `_extract_with_model` tests target `httpx.AsyncClient` directly (not the project module) — those don't need rewriting.
  - No `context_id` references expected.

- **`tests/test_scheduler.py`** (416 LOC) — rewrites:
  - `from modules.price_tracker.result import PriceExtractionResult` -> `from domain.result import PriceExtractionResult`
  - `from modules.price_tracker.scheduler import PriceCheckScheduler` -> `from domain.scheduler import PriceCheckScheduler`
  - The `_make_product_store(...)`, `_make_watch(...)` helper functions construct ORM instances — if they pass `context_id=...`, rewrite kwargs to `tenant_id=...`. Same for any direct `Product(context_id=...)` / `PriceWatch(context_id=...)` constructions in test bodies.
  - Run `grep -nc context_id tests/test_scheduler.py` AFTER porting; result MUST be 0.
  - Patch targets: any `patch("modules.price_tracker.X")` -> `patch("domain.X")`.

- **`tests/test_service.py`** (356 LOC) — rewrites:
  - `from modules.price_tracker.service import PriceTrackerService` -> `from domain.service import PriceTrackerService`
  - The `mock_session_factory` and `mock_session` fixtures create `MagicMock` / `AsyncMock` instances — no rewrites needed.
  - Test bodies likely call `service.create_product(context_id=..., name=...)` style — these become `tenant_id=...` because the underlying `PriceTrackerService` API was renamed in Plan 02 (the service layer parameter was `context_id: uuid.UUID`, now `tenant_id: uuid.UUID`).
  - Run `grep -nc context_id tests/test_service.py` AFTER porting; result MUST be 0.
  - Patch targets: any `patch("modules.price_tracker.X")` -> `patch("domain.X")`.

After all 5 files ported, the test suite should be runnable. DO NOT run pytest in this task (Task 2 does the gate run after conftest is in place).
  </action>
  <verify>
    <automated>test "$(grep -rn 'from modules.price_tracker' tests/ | wc -l)" = "0" &amp;&amp; test "$(grep -rn 'from core\.' tests/ | wc -l)" = "0" &amp;&amp; test "$(grep -rn context_id tests/ | wc -l)" = "0" &amp;&amp; test "$(grep -rn 'patch(\"modules.price_tracker' tests/ | wc -l)" = "0" &amp;&amp; poetry run python -c "import ast; [ast.parse(open(f).read()) for f in ['tests/test_parser.py','tests/test_service.py','tests/test_scheduler.py','tests/test_notifier.py','tests/test_extractors.py']]; print('all 5 test files parse')"</automated>
  </verify>
  <acceptance_criteria>
- All 6 files exist (5 test files + `tests/__init__.py`)
- `grep -rn "from modules.price_tracker" tests/ | wc -l` returns 0
- `grep -rn "from core\." tests/ | wc -l` returns 0
- `grep -rn context_id tests/ | wc -l` returns 0 (column rename fully propagated)
- `grep -rn 'patch("modules.price_tracker' tests/ | wc -l` returns 0
- `grep -rn 'patch("core\.' tests/ | wc -l` returns 0
- `grep -c "from domain" tests/test_parser.py` returns 1 or more
- `grep -c "from domain" tests/test_service.py` returns 1 or more
- `grep -c "from domain" tests/test_scheduler.py` returns 1 or more
- `grep -c "from domain" tests/test_notifier.py` returns 1 or more
- `grep -c "from domain" tests/test_extractors.py` returns 1 or more
- `grep -c "from domain.protocols.email" tests/test_notifier.py` returns 1
- `grep -c 'patch("domain.stores.get_store_hints")' tests/test_parser.py` returns 1
- All 5 test files parse as valid Python (verify command runs `ast.parse` on each)
- LOC roughly preserved (allow +/-2 per file for whitespace from rewrites): `wc -l tests/test_extractors.py` ~228, etc.
  </acceptance_criteria>
  <done>5 test files ported with imports + patch strings + ORM kwargs rewritten. Files parse as Python. Ready for Task 2 to add the conftest and run pytest.</done>
</task>

<task type="auto">
  <name>Task 2: Add minimal conftest.py and run pytest gate</name>
  <files>tests/conftest.py</files>
  <read_first>
    - tests/test_parser.py (Task 1 — confirm imports resolve)
    - tests/test_service.py (Task 1)
    - tests/test_scheduler.py (Task 1)
    - tests/test_notifier.py (Task 1)
    - tests/test_extractors.py (Task 1)
    - pyproject.toml (Plan 01 — confirms `pytest-asyncio` present and `asyncio_mode = "auto"` configured)
  </read_first>
  <action>
Create `tests/conftest.py` with the minimum content needed to make pytest run cleanly. Start empty:

```python
"""Test scaffolding for price-tracker.

Source repo had no conftest.py — fixtures are defined per-file inline using unittest.mock.
Per D-04/D-05, no real DB sessions are used in Phase 1 tests; mocks-only.
"""
```

Then run `poetry run pytest -q` from the repo root. Three possible outcomes:

1. **All tests pass green** — done. Commit conftest.py and proceed to verification.

2. **Import errors** (e.g., "ModuleNotFoundError: No module named 'domain'") — this means Poetry's `packages = [{include="domain", from="src"}, ...]` did not register the packages on sys.path. Fix by adding to conftest.py:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
   ```
   Then re-run `poetry run pytest -q`. If still failing, the issue is a missing `poetry install --no-root` to register the packages. Run that, then `poetry install` to also install the local project, then re-run pytest.

3. **Test failures** (not import errors) — diagnose per failure:
   - `AttributeError: ... has no attribute 'context_id'` — Task 1 missed a `context_id` reference. Find with `grep -rn context_id tests/`, fix, re-run.
   - `ModuleNotFoundError: No module named 'modules'` or `'core'` — Task 1 missed an import rewrite. Find with `grep -rn "from modules\\|from core" tests/`, fix, re-run.
   - `ImportError: cannot import name 'X'` from `domain.Y` — Plan 02 dropped a public symbol. Cross-reference Plan 02's port mapping table and fix at the domain layer (NOT the test) — domain MUST preserve public surface byte-equivalent.
   - `RuntimeError: Fetcher not configured. Phase 2 wires the httpx fetcher here.` — a test is exercising `service.manual_check_price` without mocking the fetcher. Source test should already mock at this boundary; verify the mock chain wasn't dropped during port. If source test legitimately calls real `get_fetcher()`, that's a bug in the source we inherit — escalate via SUMMARY rather than papering over.
   - Real test logic failures — DO NOT modify tests. Reverse-trace the failure to a domain-port mistake (Plan 02 likely deviated from byte-equivalence). Fix domain code; re-run.

Iterate fix -> rerun -> fix until pytest is green. Final acceptance: `poetry run pytest -q` exits 0 with the same number of passing tests as the source suite.

Compute and record in SUMMARY:
- Source test count: run `grep -c "def test_\|async def test_" /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_*.py | awk -F: '{s+=$2} END {print s}'` (informational)
- Ported test count: `grep -c "def test_\|async def test_" tests/test_*.py | awk -F: '{s+=$2} END {print s}'` — must equal source count
- Pytest pass count: parse `poetry run pytest -q` output — must equal ported test count
  </action>
  <verify>
    <automated>poetry run pytest -q 2>&amp;1 | tee /tmp/pytest-gate.log; PASS=$(grep -oE "[0-9]+ passed" /tmp/pytest-gate.log | head -1 | grep -oE "[0-9]+"); SRC_COUNT=$(grep -c "def test_\|async def test_" /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_*.py 2&gt;/dev/null | awk -F: '{s+=$2} END {print s}'); PORT_COUNT=$(grep -c "def test_\|async def test_" tests/test_*.py | awk -F: '{s+=$2} END {print s}'); echo "src=$SRC_COUNT ported=$PORT_COUNT passed=$PASS"; test "$PORT_COUNT" = "$SRC_COUNT" &amp;&amp; test "$PASS" = "$PORT_COUNT" &amp;&amp; ! grep -q "failed\|error" /tmp/pytest-gate.log &amp;&amp; echo "PYTEST GATE PASS"</automated>
  </verify>
  <acceptance_criteria>
- `tests/conftest.py` exists
- `poetry run pytest -q` exit code is 0
- `grep -oE "[0-9]+ passed" /tmp/pytest-gate.log` matches the count of `def test_` / `async def test_` declarations across `tests/test_*.py`
- `grep -c "failed\|error" /tmp/pytest-gate.log` returns 0 (no failures, no errors — only passes and skips allowed)
- The verify command prints `PYTEST GATE PASS`
- Source test count vs ported test count: equal (no tests dropped during port)
  </acceptance_criteria>
  <done>Full ported pytest suite green. ROADMAP gate 1 (`pytest` runs the full ported suite green against rebased fixtures) is satisfied. SUMMARY records the source/ported/passed test counts and any iteration history.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| n/a (Phase 1) | Tests run inside the developer/CI sandbox. No network, no DB, no real services touched (all mocked per D-04/D-05). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-12 | Tampering | `context_id` -> `tenant_id` symbol propagation in tests | mitigate | Acceptance criteria asserts `grep -c context_id tests/` returns 0. Any miss surfaces as an `AttributeError` at test time. Severity: low. |
| T-01-13 | Information Disclosure | Test fixtures (mocked email addresses, sample product data) | accept | Source tests use placeholder strings (`test@example.com`, `Toalettpapper 24-pack`). No real PII. Verbatim port preserves these. Severity: info. |
| T-01-14 | Denial of Service | parser.py LITELLM_API_BASE call inside test_parser HTTP-mock paths | accept | Source tests mock `httpx.AsyncClient` at the call site, so the LITELLM_API_BASE constant is never resolved against a real network. Verbatim port preserves the mocking discipline. Severity: info. |

No high-severity threats in this plan.
</threat_model>

<verification>
After both tasks complete:

```bash
# Imports clean
test "$(grep -rn 'from modules.price_tracker\|from core\.' tests/ | wc -l)" = "0" || (echo FAIL: stale imports in tests; exit 1)
# Column rename fully propagated
test "$(grep -rn context_id tests/ | wc -l)" = "0" || (echo FAIL: stale context_id in tests; exit 1)
# Patch strings rewritten
test "$(grep -rn 'patch(\"modules\\|patch(\"core' tests/ | wc -l)" = "0" || (echo FAIL: stale patch target; exit 1)
# Pytest green
poetry run pytest -q
# Test count parity
SRC=$(grep -c "def test_\\|async def test_" /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/tests/test_*.py | awk -F: '{s+=$2} END {print s}')
PORT=$(grep -c "def test_\\|async def test_" tests/test_*.py | awk -F: '{s+=$2} END {print s}')
test "$SRC" = "$PORT" || (echo "FAIL: test count drift src=$SRC ported=$PORT"; exit 1)
echo "tests verified."
```
</verification>

<success_criteria>
- 5 source test files ported (REQ TEST-01); 1788 LOC accounted for
- All `MockEmailService`, `MagicMock`, `AsyncMock` patterns preserved (REQ TEST-02 — no platform-specific test doubles ported because none existed in source)
- `poetry run pytest` exits 0 with all ported tests passing (REQ TEST-03, ROADMAP gate 1)
- Test count parity: source test count == ported test count
- No `context_id`, `from modules.`, `from core.`, or `patch("modules.` / `patch("core.` references remain in `tests/`
- `tests/conftest.py` is minimal (no speculative fixtures)
</success_criteria>

<output>
After completion, create `.planning/phases/01-skeleton-domain-copy/01-04-SUMMARY.md` containing:
- Source test count vs ported test count vs pytest passed count (all three must match)
- Final `tests/conftest.py` contents (likely just docstring + optional sys.path glue)
- Iteration history if any test failures surfaced port mistakes (which file, which mistake, how fixed)
- Confirmation that no aiosqlite, no InMemoryAsyncSession, no platform-specific mocks were introduced (D-04 holds)
</output>
