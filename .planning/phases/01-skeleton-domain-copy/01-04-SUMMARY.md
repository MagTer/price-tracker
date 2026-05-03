---
phase: 01-skeleton-domain-copy
plan: 04
subsystem: tests
tags:
  - python
  - pytest
  - port
  - verbatim
dependency-graph:
  requires:
    - "src.domain.* modules (from Plan 01-02)"
    - "src.domain.tenant.DEFAULT_TENANT_ID (from Plan 01-01)"
    - "pytest + pytest-asyncio in dev deps (from Plan 01-01)"
  provides:
    - "67-test green pytest suite covering parser, service, scheduler, notifier, extractors"
  affects:
    - "01-05-docker (Wave 4): pytest is gate 1 of Phase 1; this plan delivers it"
tech-stack:
  added: []
  patterns:
    - "unittest.mock (MagicMock / AsyncMock) for sessions, fetchers, email, LLM clients (D-04)"
    - "Schema fidelity verified by alembic upgrade head, NOT by pytest (D-05)"
    - "No tests/conftest.py — source repo had none; the 5 test files are self-contained"
key-files:
  created:
    - "tests/__init__.py (1 LOC, marker)"
    - "tests/test_extractors.py (220 LOC, 12 tests)"
    - "tests/test_notifier.py (387 LOC, 14 tests)"
    - "tests/test_parser.py (370 LOC, 14 tests)"
    - "tests/test_scheduler.py (414 LOC, 13 tests)"
    - "tests/test_service.py (348 LOC, 14 tests)"
  modified:
    - ".planning/STATE.md"
    - ".planning/ROADMAP.md"
decisions:
  - "No conftest.py created — source repo had no conftest in tests/, and each test file constructs its own MagicMock/AsyncMock fixtures inline. Adding a conftest would have been an unsolicited refactor (CLAUDE.md: don't add abstractions beyond what each phase requires)."
metrics:
  duration: "~10 min"
  completed: "2026-05-03"
  tasks: 1
  files: 6
---

# Phase 1 Plan 04: Test Verbatim Port Summary

Ported all 5 source test files from `ai-agent-platform/services/agent/src/modules/price_tracker/tests/` into local `tests/` with the four documented transforms (3 from Plan 02 plus the test-specific patch-target rewrite). Test count parity: 67/67 (12+14+14+13+14). `poetry run pytest -q` exits 0. Phase 1 gate 1 (`pytest` runs the full ported suite green) is verified.

## Acceptance Gate Output

```
$ poetry run pytest -q
67 passed, 8 warnings in 0.61s
```

The 8 warnings are `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` from a few `test_service.py` cases that pass an `AsyncMock` to `session.add()` (which is sync). These warnings exist verbatim in the source suite — they are a known consequence of the mock-only test strategy (D-04/D-05) and are NOT new regressions. Fixing them is explicitly out of scope per CLAUDE.md ("don't fix known shortcomings during the port").

## Test Count Parity (target vs source)

| File | Source tests | Target tests | Match |
|------|-------------:|-------------:|:-----:|
| test_extractors.py | 12 | 12 | ✓ |
| test_notifier.py | 14 | 14 | ✓ |
| test_parser.py | 14 | 14 | ✓ |
| test_scheduler.py | 13 | 13 | ✓ |
| test_service.py | 14 | 14 | ✓ |
| **Total** | **67** | **67** | ✓ |

## Transforms Applied

Per the 4-transform contract (3 from Plan 02 + the test-specific patch rewrite):

1. **Import-path rewrites** — `from modules.price_tracker.X` → `from domain.X` in every test file.
2. **`context_id` → `tenant_id`** — every kwarg / attribute / assertion rewritten; seeded with `DEFAULT_TENANT_ID` from `from domain.tenant import DEFAULT_TENANT_ID`.
3. **Table-prefix drop** — no raw SQL in source tests, so this transform was a no-op for test files.
4. **`mock.patch()` string targets** — every `mock.patch("modules.price_tracker.scheduler.X")` rewritten to `mock.patch("domain.scheduler.X")`. These are STRINGS, so import-tooling cannot catch them — manual string-pattern audit.

Verification:
- `grep -c 'modules.price_tracker' tests/*.py` → 0 leftover references in any file
- `grep -c 'context_id' tests/*.py` → 0 leftover references
- `grep -l aiosqlite tests/*.py` → no matches (D-04 honored)

## Acceptance Criteria

- [x] tests/__init__.py + 5 test_*.py files created (no conftest.py — see Decisions)
- [x] All `mock.patch("modules.price_tracker.X")` strings rewritten to `mock.patch("domain.X")`
- [x] All `context_id=` kwargs rewritten to `tenant_id=`; uses `DEFAULT_TENANT_ID`
- [x] No imports of `aiosqlite`, no real DB sessions instantiated
- [x] `poetry run pytest -q` exits 0
- [x] Test count in target matches source (per-file `grep -c "def test_"`)
- [x] No new comments added vs source files
- [x] SUMMARY.md created and committed

## Deviations

None. The conftest decision is a deliberate "don't refactor" call — see Decisions above.

## Phase 1 Progress After This Plan

- Gate 1 (`pytest` green): ✓ verified (67 passed)
- Gate 2 (`alembic upgrade head` green): ✓ verified by Plan 01-03
- Gate 3 (`poetry install` resolves): ✓ verified by Plan 01-01
- Gate 4 (`docker build` green): pending — closes in Plan 01-05
