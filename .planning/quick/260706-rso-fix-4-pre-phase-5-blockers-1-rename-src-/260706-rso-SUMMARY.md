---
quick_id: 260706-rso
subsystem: mcp
tags: [fastmcp, mcp, openrouter, pytest, poetry, dependency-resolution]

provides:
  - src/mcp_server/ package (renamed from src/mcp/, no more collision with the installed mcp PyPI SDK)
  - Corrected OpenRouter chat-completions URL in src/domain/parser.py
  - Real OpenRouter model IDs in PriceParser's MODEL_CASCADE / CONFIDENCE_THRESHOLDS
  - fastmcp bumped to 2.x (resolved 2.12.5) with get_mcp_app() using the modern http_app() API
  - Fully green poetry run pytest -q across all 7 test files (87 passed, 0 failures, 0 errors)
affects: [phase-5-source-repo-cleanup]

tech-stack:
  added: []
  patterns:
    - "fastmcp 2.x @mcp.tool() decorated functions expose the original coroutine via .fn — call .fn(...) directly in tests, not the decorated object"

key-files:
  created:
    - src/mcp_server/__init__.py
    - src/mcp_server/auth.py
    - src/mcp_server/server.py
  modified:
    - src/api/app.py
    - src/domain/parser.py
    - pyproject.toml
    - poetry.lock
    - tests/test_mcp.py
    - tests/test_parser.py
    - tests/test_api.py

key-decisions:
  - "Bumped httpx from >=0.27,<0.28 to >=0.28,<0.29 (Rule 3 blocking-issue fix) because fastmcp >=2.0,<3.0 requires httpx>=0.28.1 or fastapi>=0.115.12, and the repo's fastapi pin (0.111) doesn't satisfy the fastapi branch"
  - "Fixed 3 latent test_api.py bugs (Rule 1) unmasked once the mcp/mcp_server collision stopped blocking collection: @patch on api.admin.get_price_tracker_service never took effect against FastAPI's Depends binding (switched to dependency_overrides), and test_scheduler_status assumed lifespan-set app.state.scheduler that a non-context-manager TestClient never populates (set it explicitly to None)"

requirements-completed: []

coverage:
  - id: D1
    description: "src/mcp/ renamed to src/mcp_server/, resolving the package collision with the installed mcp PyPI SDK"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py (20 tests)"
        status: pass
    human_judgment: false
  - id: D2
    description: "OpenRouter chat-completions URL no longer has a doubled /v1 segment"
    verification:
      - kind: unit
        ref: "tests/test_parser.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "PriceParser MODEL_CASCADE / CONFIDENCE_THRESHOLDS use real OpenRouter model IDs (meta-llama/llama-4-scout, anthropic/claude-haiku-4.5) with unchanged 0.70/0.0 thresholds"
    verification:
      - kind: unit
        ref: "tests/test_parser.py::TestPriceParser::test_extract_price_uses_primary_model_first"
        status: pass
    human_judgment: false
  - id: D4
    description: "fastmcp bumped to ^2.x (resolved 2.12.5); get_mcp_app() uses http_app() with no legacy sse_app shim, bearer-auth wrapping preserved"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py (5 .fn() tool invocations)"
        status: pass
    human_judgment: false
  - id: D5
    description: "poetry lock && poetry install && poetry run pytest -q succeed with 0 failures, 0 errors across all 7 test files"
    verification:
      - kind: unit
        ref: "poetry run pytest -q (87 passed)"
        status: pass
    human_judgment: false

duration: 30min
completed: 2026-07-06
status: complete
---

# Quick Task 260706-rso: Fix 4 pre-Phase-5 blockers Summary

**Renamed src/mcp/ to src/mcp_server/ to fix a fastmcp SDK package collision, fixed a doubled /v1 in the OpenRouter URL, swapped stale LiteLLM-proxy model aliases for real OpenRouter model IDs, and bumped fastmcp to 2.x (resolved 2.12.5) with the modern http_app() API — all 7 test files now pass (87 passed, 0 failures, 0 errors).**

## Performance

- **Duration:** ~30 min
- **Completed:** 2026-07-06
- **Tasks:** 5/5 completed
- **Files modified:** 11 (3 new: src/mcp_server/__init__.py, auth.py, server.py; 8 modified: src/api/app.py, src/domain/parser.py, pyproject.toml, poetry.lock, tests/test_mcp.py, tests/test_parser.py, tests/test_api.py, and the removal of src/mcp/)

## Accomplishments

- Fixed the package-name collision between `src/mcp/` and the installed `mcp` PyPI SDK that fastmcp depends on, by renaming to `src/mcp_server/` and updating all four import sites (server.py's internal auth import, api/app.py, pyproject.toml's packages list, tests/test_mcp.py's imports and patch targets)
- Fixed the doubled `/v1` segment in the OpenRouter chat-completions URL in `src/domain/parser.py`
- Replaced placeholder LiteLLM-proxy model aliases (`price_tracker`, `price_tracker_fallback`) with real OpenRouter model IDs (`meta-llama/llama-4-scout`, `anthropic/claude-haiku-4.5`), keeping the 0.70/0.0 confidence thresholds unchanged
- Bumped fastmcp to `^2.x` (resolved 2.12.5), replaced the legacy `sse_app()`/getattr fallback shim in `get_mcp_app()` with a direct `mcp.http_app()` call, and updated all five direct tool invocations in tests/test_mcp.py to use `.fn(...)` (fastmcp 2.x's `@mcp.tool()` no longer returns a callable)
- `poetry lock && poetry install && poetry run pytest -q` all succeed — 87 tests passed, 0 failures, 0 errors across all 7 test files

## Task Commits

1. **Task 1: Rename src/mcp/ to src/mcp_server/ and fix all four import sites (bug 1)** - `04a1479` (fix)
2. **Deviation: fix broken dependency-override mocking in test_api.py** - `cfc1806` (fix)
3. **Task 2: Fix doubled /v1 segment in the OpenRouter chat-completions URL (bug 2)** - `2565534` (fix)
4. **Task 3: Replace stale LiteLLM-proxy model aliases with real OpenRouter model IDs (bug 3)** - `77c0f44` (fix)
5. **Task 4: Bump fastmcp to ^2.x and modernize server.py's ASGI app construction (bug 4)** - `936f9ca` (fix)
6. **Task 5: poetry lock + install + full pytest gate** - `dd547bd` (fix)

_Note: Task 1's commit is followed by a separate deviation commit (cfc1806) fixing pre-existing test_api.py bugs unmasked once collection succeeded — see Deviations below._

## Files Created/Modified

- `src/mcp_server/__init__.py` - Empty package marker (moved from src/mcp/)
- `src/mcp_server/auth.py` - BearerTokenMiddleware, unchanged content (moved from src/mcp/)
- `src/mcp_server/server.py` - FastMCP server + 4 tools; internal import now `from mcp_server.auth import ...`; `get_mcp_app()` now calls `mcp.http_app()` directly with no legacy shim
- `src/api/app.py` - Import updated to `from mcp_server.server import get_mcp_app`
- `src/domain/parser.py` - Fixed doubled `/v1` in chat-completions URL; MODEL_CASCADE/CONFIDENCE_THRESHOLDS now use real OpenRouter model IDs
- `pyproject.toml` - `packages` list references `mcp_server`; fastmcp bumped to `>=2.0,<3.0`; httpx bumped to `>=0.28,<0.29`
- `poetry.lock` - Regenerated for the fastmcp 2.x + httpx 0.28 dependency tree (resolved fastmcp 2.12.5)
- `tests/test_mcp.py` - Imports/patches target `mcp_server`; five tool invocations use `.fn(...)`
- `tests/test_parser.py` - Updated cascade-order assertion to check for `llama-4-scout` instead of the old `price_tracker` alias
- `tests/test_api.py` - Two tests switched from non-functional `@patch` to `app.dependency_overrides`; `test_scheduler_status` sets `app.state.scheduler = None` explicitly (deviation fix, see below)

## Decisions Made

- Bumped httpx from `>=0.27,<0.28` to `>=0.28,<0.29` rather than bumping fastapi, since fastmcp's dependency constraint is satisfiable via either httpx>=0.28.1 or fastapi>=0.115.12 — the httpx bump is the smaller, more isolated change and resolved to the newest fastmcp (2.12.5)
- Fixed the three latent test_api.py bugs as a Rule 1 deviation (see below) rather than leaving them, since the plan's own must_haves and Task 5's final gate explicitly require `poetry run pytest -q` to pass 0 failures / 0 errors across all 7 files

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed broken `@patch` mocking pattern in test_create_product / test_delete_product**
- **Found during:** Task 1 verification (running `poetry run pytest tests/test_mcp.py tests/test_api.py -q` after the collision fix, for the first time ever since collection was previously blocked)
- **Issue:** `@patch("api.admin.get_price_tracker_service")` has no effect because FastAPI's `Depends(get_price_tracker_service)` captures the function object at route-declaration time, not by module-attribute lookup — so both tests were silently attempting a real (unavailable) Postgres connection and failing with 500s
- **Fix:** Switched both tests to `client.app.dependency_overrides[get_price_tracker_service] = lambda: mock_service`, matching the `get_db` override pattern already established in the same fixture
- **Files modified:** tests/test_api.py
- **Commit:** `cfc1806`

**2. [Rule 1 - Bug] Fixed test_scheduler_status assuming lifespan-populated app.state.scheduler**
- **Found during:** Task 1 verification (same run as above)
- **Issue:** `scheduler_status` reads `request.app.state.scheduler`, which is only ever set by `create_app()`'s lifespan context manager — but the `client` fixture returns a plain `TestClient(app)` without entering it as a context manager, so lifespan never runs and the attribute access raised `AttributeError`
- **Fix:** Set `client.app.state.scheduler = None` explicitly at the start of the test, which the endpoint already handles gracefully (`if scheduler is None: return {"running": False, ...}`)
- **Files modified:** tests/test_api.py
- **Commit:** `cfc1806`

**3. [Rule 3 - Blocking] Bumped httpx from 0.27 to 0.28 to resolve fastmcp 2.x dependency conflict**
- **Found during:** Task 5 (`poetry lock`)
- **Issue:** `poetry lock` failed — fastmcp `>=2.0,<3.0` requires `httpx>=0.28.1` or `fastapi>=0.115.12`, and the repo pinned httpx to `>=0.27,<0.28` and fastapi to `>=0.111,<0.112`, satisfying neither branch
- **Fix:** Bumped the httpx constraint to `>=0.28,<0.29` in pyproject.toml (left fastapi unchanged), then re-ran `poetry lock` successfully, resolving fastmcp to 2.12.5
- **Files modified:** pyproject.toml, poetry.lock
- **Commit:** `dd547bd`

---

**Total deviations:** 3 auto-fixed (2 Rule 1 bug fixes in test code, 1 Rule 3 blocking dependency-resolution fix)
**Impact on plan:** All three were necessary to satisfy this plan's own declared must-have ("poetry run pytest -q collects and passes all 7 test files with 0 failures and 0 errors") and Task 5's mandatory final gate. No production code behavior was changed beyond the 4 explicitly targeted bugs; the httpx version bump only widens an existing dependency range to satisfy fastmcp's transitive requirement. No scope creep beyond what was required to make the plan's own verification pass.

## Issues Encountered

None beyond the deviations documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 5 (Source-repo Cleanup) is now unblocked: the MCP server imports cleanly, OpenRouter integration points at the correct URL and real model IDs, and fastmcp is on the modern 2.x streamable-HTTP API
- Full test suite is green (87 passed, 0 failures, 0 errors) — safe baseline for Phase 5 work
- No blockers identified

---
*Quick task: 260706-rso*
*Completed: 2026-07-06*

## Self-Check: PASSED

All created files verified present on disk (src/mcp_server/__init__.py, auth.py, server.py, this SUMMARY.md). All 6 task commit hashes verified present in git log (04a1479, cfc1806, 2565534, 77c0f44, 936f9ca, dd547bd).
