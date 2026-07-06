---
quick_id: 260706-t3p
slug: fix-2-issues-flagged-after-quick-task-26
subsystem: mcp-server, api-app, docs
tags: [bugfix, docs, lifespan, mcp, fastmcp]
dependency-graph:
  requires: [260706-rso]
  provides: [mcp-lifespan-propagation]
  affects: [src/api/app.py, src/mcp_server/server.py, CLAUDE.md]
tech-stack:
  added: []
  patterns:
    - "Nested async context manager: mcp_http_app.lifespan(mcp_http_app) wraps existing scheduler/fetcher start+stop inside create_app()'s lifespan, since Starlette's app.mount() does not propagate a mounted sub-app's own lifespan"
key-files:
  created: []
  modified:
    - CLAUDE.md
    - src/mcp_server/server.py
    - src/api/app.py
decisions:
  - "get_mcp_app() now returns a (wrapped_app, http_app) 2-tuple in both the bearer-protected and unprotected branches, so callers can reach the raw http_app's .lifespan without constructing a second, divergent app instance via a second get_mcp_app() call"
  - "Manual lifespan smoke check kept as a one-off throwaway script (run then deleted) rather than a permanent pytest test, since entering the real lifespan starts a background scheduler task against a real Postgres DATABASE_URL that isn't available in dev/test — a judgment call to avoid nondeterministic latency in the committed suite"
metrics:
  duration: ~15 min
  completed: 2026-07-06
status: complete
---

# Quick Task 260706-t3p: Fix 2 issues flagged after quick task 260706-rso Summary

Fixed CLAUDE.md's stale `mcp/` architecture-tree entry and a real runtime bug where `create_app()`'s lifespan never entered the mounted MCP sub-app's own lifespan, meaning fastmcp's streamable-HTTP session manager never actually started when the real app booted (only masked because `TestClient(app)` in the existing suite never enters the app's lifespan context manager).

## What Was Built

**Task 1 — CLAUDE.md doc fix:** Corrected the Architecture section's directory tree from the stale `mcp/` (pre-260706-rso rename) to `mcp_server/`, matching the actual package layout. No other content in the section changed — the also-stale Entra OIDC references were left untouched per plan scope.

**Task 2 — `get_mcp_app()` tuple return:** `src/mcp_server/server.py`'s `get_mcp_app()` now returns `(wrapped_app, http_app)` in both branches (bearer-protected and unprotected). The raw `http_app` (fastmcp 2.x's `StarletteWithLifespan` instance from `mcp.http_app()`) is always returned as the second element so a caller can reach `.lifespan` regardless of whether bearer wrapping applied. Docstring updated to explain why the raw app must be exposed separately: `app.mount()` does not propagate a mounted sub-app's own lifespan.

**Task 3 — Lifespan propagation in `create_app()`:** `src/api/app.py` now unpacks `get_mcp_app()`'s tuple once at module scope (`mcp_app, mcp_http_app = get_mcp_app()`). The `lifespan()` async context manager wraps the entire existing scheduler/fetcher startup, the `yield`, and the scheduler/fetcher shutdown inside `async with mcp_http_app.lifespan(mcp_http_app):`, so the MCP session manager and the scheduler now start together before requests are served and stop together on shutdown. `app.mount("/mcp", ...)` now uses the pre-unpacked `mcp_app` instead of a second `get_mcp_app()` call.

**Task 4 — Regression + manual verification:** Full suite (`poetry run pytest -q`) stayed green: 87 passed, 0 failures, 0 errors — unchanged from the known-good baseline. A throwaway script (`create_app()` + `async with lifespan(app):`, asserting `app.state.scheduler is not None` and `scheduler._running is True`) confirmed the combined lifespan enters and exits cleanly with the scheduler running inside the block. The script was deleted after a successful run per plan instructions — it is not part of the committed suite.

## Deviations from Plan

None - plan executed exactly as written.

## Self-Check: PASSED

- FOUND: CLAUDE.md shows `mcp_server/` in Architecture tree, `mcp/` absent, Entra OIDC lines untouched
- FOUND: `src/mcp_server/server.py` `get_mcp_app()` returns 2-tuple in both branches, docstring updated
- FOUND: `src/api/app.py` module-scope unpack, nested `async with mcp_http_app.lifespan(mcp_http_app):`, `app.mount("/mcp", mcp_app)`, single `get_mcp_app()` call
- FOUND: commit 9e108a2 (docs fix)
- FOUND: commit 146aafe (get_mcp_app tuple)
- FOUND: commit 7a3127b (lifespan propagation)
- FOUND: `poetry run pytest -q` → 87 passed, 0 failures, 0 errors
- FOUND: throwaway lifespan smoke script ran successfully then was deleted (confirmed absent from working tree)
