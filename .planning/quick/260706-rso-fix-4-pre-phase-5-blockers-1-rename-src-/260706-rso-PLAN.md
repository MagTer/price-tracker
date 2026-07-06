---
quick_id: 260706-rso
slug: fix-4-pre-phase-5-blockers-1-rename-src-
type: execute
autonomous: true
files_modified:
  - src/mcp_server/__init__.py
  - src/mcp_server/auth.py
  - src/mcp_server/server.py
  - src/api/app.py
  - src/domain/parser.py
  - pyproject.toml
  - poetry.lock
  - tests/test_mcp.py
  - tests/test_parser.py
must_haves:
  truths:
    - "import mcp_server.server and import mcp_server.auth succeed without colliding with the installed mcp 1.12.4 PyPI SDK that fastmcp depends on"
    - "OpenRouter chat-completion requests hit https://openrouter.ai/api/v1/chat/completions (no doubled /v1 segment)"
    - "PriceParser's default model cascade uses real OpenRouter model IDs (meta-llama/llama-4-scout, anthropic/claude-haiku-4.5) with matching confidence thresholds (0.70 / 0.0)"
    - "the /mcp mount is built from fastmcp 2.x's http_app() (modern streamable-HTTP transport), not the legacy sse_app() shim, and remains wrapped in BearerTokenMiddleware"
    - "poetry run pytest -q collects and passes all 7 test files with 0 failures and 0 errors"
  artifacts:
    - src/mcp_server/__init__.py
    - src/mcp_server/auth.py
    - src/mcp_server/server.py
    - poetry.lock (regenerated for fastmcp >=2.0,<3.0)
  key_links:
    - "src/api/app.py -> src/mcp_server/server.py (get_mcp_app import)"
    - "src/mcp_server/server.py -> src/mcp_server/auth.py (BearerTokenMiddleware import)"
    - "src/domain/parser.py -> src/infra/llm.py (OPENROUTER_BASE_URL)"
    - "tests/test_mcp.py -> src/mcp_server/server.py (tool .fn access post fastmcp-2.x bump)"
---

<objective>
Fix 4 verified, narrowly-scoped bugs blocking Phase 5 (source-repo cleanup): (1) a package-name collision between the local `src/mcp/` package and the installed `mcp` PyPI SDK that fastmcp depends on, (2) a doubled `/v1` segment in the OpenRouter chat-completions URL, (3) stale LiteLLM-proxy model aliases in the price-parser's model cascade and confidence thresholds, (4) fastmcp pinned to the legacy 1.0 SSE-only API instead of the real 2.x streamable-HTTP API.

Purpose: Phase 5 assumes a working, tested MCP server and OpenRouter-backed parser. Today, 2 of 7 test files fail to even collect (confirmed: `ModuleNotFoundError: No module named 'mcp.auth'` in tests/test_mcp.py, and a circular-import `cannot import name 'Server' from partially initialized module 'mcp.server'` in tests/test_api.py — both caused by the same package-name collision), and the parser would call a broken URL against real placeholder model names that don't exist on OpenRouter.

Output: `src/mcp_server/` package (renamed from `src/mcp/`), corrected OpenRouter URL and model config in `src/domain/parser.py`, `fastmcp` bumped to `^2.x` with `src/mcp_server/server.py` using the modern `http_app()` API, and a fully green `poetry run pytest -q` across all 7 test files.

Do NOT fix anything else beyond these 4 bugs and their directly-required test fallout (verified by hands-on testing below — not speculative).
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@EXTRACTION.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Rename src/mcp/ to src/mcp_server/ and fix all four import sites (bug 1)</name>
  <files>src/mcp_server/__init__.py, src/mcp_server/auth.py, src/mcp_server/server.py, src/api/app.py, pyproject.toml, tests/test_mcp.py</files>
  <action>
Fix the package-name collision. Root cause (verified by running the current test suite): `src/mcp/` shadows the installed `mcp` 1.12.4 PyPI SDK on `sys.path`, and fastmcp's own internals do `from mcp.server import Server`, so importing the local `mcp` package first breaks fastmcp's import with a partially-initialized-module circular import (or, depending on path order, a plain `ModuleNotFoundError: No module named 'mcp.auth'`). Both tests/test_mcp.py and tests/test_api.py fail collection for this same reason (test_api.py imports `create_app` from src/api/app.py, which imports `mcp.server`).

Move the directory: relocate src/mcp/__init__.py, src/mcp/auth.py, and src/mcp/server.py to src/mcp_server/ with identical contents (no other changes to auth.py). Note: src/mcp/__init__.py is git-tracked while auth.py and server.py are currently untracked in this working tree — use a plain filesystem move (not `git mv`, which expects all source paths to already be tracked).

Inside the moved src/mcp_server/server.py, change the import line `from mcp.auth import BearerTokenMiddleware, MCP_BEARER_TOKEN` to import from `mcp_server.auth` instead.

In src/api/app.py, change `from mcp.server import get_mcp_app` to import from `mcp_server.server` instead.

In pyproject.toml's `[tool.poetry] packages` list, change the entry that currently reads `{ include = "mcp", from = "src" }` to reference `mcp_server` instead.

In tests/test_mcp.py, change both module-level imports (currently `from mcp.auth import BearerTokenMiddleware` and `from mcp.server import check_price, compare_stores, find_deals, list_products`) to import from `mcp_server.auth` / `mcp_server.server`, and update all five `@patch("mcp.server._get_service")` decorators to target `mcp_server.server._get_service` instead.

Do not modify tests/test_api.py directly — it only imports `create_app`, and inherits this fix transitively once src/api/app.py's import is corrected.
  </action>
  <verify>
    <automated>test ! -d src/mcp && test -f src/mcp_server/server.py && grep -q "from mcp_server.auth import" src/mcp_server/server.py && grep -q "from mcp_server.server import get_mcp_app" src/api/app.py && grep -q 'include = "mcp_server"' pyproject.toml && test "$(grep -c 'mcp_server.server._get_service' tests/test_mcp.py)" -eq 5 && poetry run pytest tests/test_mcp.py tests/test_api.py -q</automated>
  </verify>
  <done>src/mcp/ no longer exists; src/mcp_server/{__init__.py,auth.py,server.py} exist with the corrected internal import; src/api/app.py and pyproject.toml reference mcp_server; tests/test_mcp.py imports and patches target mcp_server; `poetry run pytest tests/test_mcp.py tests/test_api.py -q` collects and passes (still using the currently-installed fastmcp 1.0 — the version bump is Task 4).</done>
</task>

<task type="auto">
  <name>Task 2: Fix doubled /v1 segment in the OpenRouter chat-completions URL (bug 2)</name>
  <files>src/domain/parser.py</files>
  <action>
In `PriceParser._extract_with_model`, the `client.post(...)` call builds its URL as an f-string that appends `/v1/chat/completions` to `OPENROUTER_BASE_URL`. `OPENROUTER_BASE_URL` (defined in src/infra/llm.py) already defaults to `https://openrouter.ai/api/v1` — the `/v1` is baked into the base URL — so the current code produces `https://openrouter.ai/api/v1/v1/chat/completions`, a 404 against the real OpenRouter API.

Change the f-string so it appends only `/chat/completions` to `OPENROUTER_BASE_URL` (drop the extra `/v1` segment), matching EXTRACTION.md §7's documented integration: "OpenRouter is OpenAI-compatible at `https://openrouter.ai/api/v1`, so `parser.py` needs zero logic changes — only the base URL, auth header, and model IDs change."
  </action>
  <verify>
    <automated>test "$(grep -c '/v1/chat/completions' src/domain/parser.py)" -eq 0 && grep -q 'OPENROUTER_BASE_URL}/chat/completions' src/domain/parser.py && poetry run pytest tests/test_parser.py -q</automated>
  </verify>
  <done>src/domain/parser.py posts to `{OPENROUTER_BASE_URL}/chat/completions` with no doubled `/v1` segment; tests/test_parser.py still passes (no existing test asserts the URL string, so this is a pure regression-safety check).</done>
</task>

<task type="auto">
  <name>Task 3: Replace stale LiteLLM-proxy model aliases with real OpenRouter model IDs (bug 3)</name>
  <files>src/domain/parser.py, tests/test_parser.py</files>
  <action>
Per EXTRACTION.md §7 (`PRICE_PARSER_MODEL=meta-llama/llama-4-scout`, `PRICE_PARSER_FALLBACK_MODEL=anthropic/claude-haiku-4.5`), replace the placeholder LiteLLM-proxy aliases in `PriceParser` with real OpenRouter model IDs.

Change the `PRICE_PARSER_MODEL_CASCADE` env-var default from the old two-alias placeholder string to a comma-joined string of the two real model IDs above, main model first, fallback second (this is the same value EXTRACTION.md documents as `${PRICE_PARSER_MODEL},${PRICE_PARSER_FALLBACK_MODEL}`).

Re-key `CONFIDENCE_THRESHOLDS` from the two old placeholder-alias keys to the two new model-id strings as keys, preserving the exact same values unchanged: 0.70 for the main model, 0.0 for the fallback (so it keeps its accept-anything last-resort behavior, per EXTRACTION.md: "Confidence thresholds (0.70 main / 0.0 fallback) stay as-is").

This model-id change has one required test fallout: tests/test_parser.py's `test_extract_price_uses_primary_model_first` asserts that the literal substring of the *old* placeholder alias appears in the first cascade call's arguments. Since the cascade now genuinely calls a different model string first, update that one assertion to check for a substring of the new main-model id (`llama-4-scout`) instead. Leave the two other direct `_extract_with_model(prompt, ...)` calls elsewhere in the test file (which pass old-style literal strings directly as an arbitrary `model` parameter, bypassing the cascade entirely) and the `Exception("price_tracker error")` test message untouched — they are unaffected by the cascade default change since `_extract_with_model` accepts any string.
  </action>
  <verify>
    <automated>grep -q "meta-llama/llama-4-scout,anthropic/claude-haiku-4.5" src/domain/parser.py && test "$(grep -c 'price_tracker' src/domain/parser.py)" -eq 0 && grep -q "llama-4-scout" tests/test_parser.py && poetry run pytest tests/test_parser.py -q</automated>
  </verify>
  <done>PriceParser.MODEL_CASCADE defaults to the two real OpenRouter model IDs (meta-llama/llama-4-scout, anthropic/claude-haiku-4.5); CONFIDENCE_THRESHOLDS is keyed by those same IDs with unchanged 0.70/0.0 values; tests/test_parser.py passes with the updated cascade-order assertion.</done>
</task>

<task type="auto">
  <name>Task 4: Bump fastmcp to ^2.x and modernize server.py's ASGI app construction (bug 4)</name>
  <files>pyproject.toml, src/mcp_server/server.py, tests/test_mcp.py</files>
  <action>
In pyproject.toml, change the dependency constraint currently pinning fastmcp to `>=1.0,<2.0` to `>=2.0,<3.0`.

In src/mcp_server/server.py's `get_mcp_app()`, remove the `try: mcp.sse_app() except AttributeError: getattr(mcp, "app", mcp)` fallback shim entirely and call `mcp.http_app()` directly with no arguments. This has been verified directly (pip-installed fastmcp 2.14.7 in isolation and inspected its API): `FastMCP.http_app()` exists and returns a `StarletteWithLifespan` ASGI app using the modern streamable-HTTP transport by default (`transport="http"` is the default parameter value and is fastmcp 2.x's recommended transport) — there is no `sse_app` method on 2.x. Keep the existing `BearerTokenMiddleware` wrapping logic unchanged: wrap whatever `mcp.http_app()` returns exactly as the old code wrapped whatever the shim returned, so bearer-auth protection on the `/mcp` mount is preserved unmodified.

IMPORTANT verified fastmcp 2.x behavior change that affects tests/test_mcp.py: `@mcp.tool()` no longer returns the original async function — it returns a non-callable tool-wrapper object whose original coroutine function is reachable via a `.fn` attribute (confirmed by direct testing: calling the decorated function raises `TypeError: '...' object is not callable`, while calling `.fn(...)` on it invokes the original coroutine and still resolves module-level names like `_get_service` through the module's normal global namespace, so mocking/patching continues to work unchanged). This means tests/test_mcp.py's five direct calls to the imported tool objects (the two `check_price(...)` calls, and the single `find_deals(...)`, `compare_stores(...)`, and `list_products()` calls) will break once fastmcp 2.x is actually installed. Update all five call sites in tests/test_mcp.py to invoke `.fn(...)` on the imported tool object instead (e.g. `await check_price.fn("omega-3")`). Do not change the five `@patch("mcp_server.server._get_service")` decorators — they need no adjustment.

Do NOT run `poetry install` or `poetry lock` in this task. fastmcp 2.x is not yet installed in this environment, so `.fn` access and `http_app()` cannot be exercised at runtime until the final gate task actually installs the new version — this task only makes the source edits, verified statically.
  </action>
  <verify>
    <automated>grep -q "fastmcp (>=2.0,<3.0)" pyproject.toml && test "$(grep -c 'sse_app' src/mcp_server/server.py)" -eq 0 && grep -q "mcp.http_app()" src/mcp_server/server.py && test "$(grep -c '\.fn(' tests/test_mcp.py)" -eq 5</automated>
  </verify>
  <done>pyproject.toml pins fastmcp to `>=2.0,<3.0`; src/mcp_server/server.py's `get_mcp_app()` calls `mcp.http_app()` with no fallback shim and still wraps the result in `BearerTokenMiddleware`; tests/test_mcp.py's five tool invocations use `.fn(...)`. Full dynamic verification happens in Task 5 after the real install.</done>
</task>

<task type="auto">
  <name>Task 5: poetry lock + install + full pytest gate (mandatory final task)</name>
  <files>poetry.lock</files>
  <action>
Regenerate the lock file for the fastmcp version bump and install the updated dependency tree: run `poetry lock` followed by `poetry install`.

Then run the full test suite: `poetry run pytest -q`. All 7 test files — tests/test_api.py, tests/test_extractors.py, tests/test_mcp.py, tests/test_notifier.py, tests/test_parser.py, tests/test_scheduler.py, tests/test_service.py — must collect and pass with 0 failures and 0 errors. Each of Tasks 1-4 was independently verified above (statically, or dynamically where the fastmcp version didn't matter yet); this task is where the fastmcp 2.x install and the runtime `.fn()` / `http_app()` behavior get exercised together for the first time. If anything fails here, diagnose against the specific task that introduced the affected file — do not silently patch around a real regression, and do not expand scope beyond what's needed to make the 4 targeted bug fixes internally consistent.
  </action>
  <verify>
    <automated>poetry lock && poetry install && poetry run pytest -q tests/test_api.py tests/test_extractors.py tests/test_mcp.py tests/test_notifier.py tests/test_parser.py tests/test_scheduler.py tests/test_service.py</automated>
  </verify>
  <done>poetry.lock reflects fastmcp `>=2.0,<3.0` and its resolved transitive dependencies; `poetry run pytest -q` exits 0 with all 7 test files passing, 0 failures, 0 errors.</done>
</task>

</tasks>

<verification>
- `poetry run pytest -q` exits 0 across all 7 test files (tests/test_api.py, tests/test_extractors.py, tests/test_mcp.py, tests/test_notifier.py, tests/test_parser.py, tests/test_scheduler.py, tests/test_service.py) — 0 failures, 0 errors.
- `grep -rc "from mcp\." src/ tests/` returns 0 (no remaining references to the old `mcp` package import path; only `mcp_server` and the third-party `fastmcp`/`mcp` SDK imports remain).
- `grep -c "/v1/chat/completions" src/domain/parser.py` returns 0 (no doubled `/v1`).
- `grep -c "price_tracker" src/domain/parser.py` returns 0 (no leftover LiteLLM-proxy aliases).
- `grep -c "sse_app" src/mcp_server/server.py` returns 0 (legacy shim fully removed).
- `poetry show fastmcp` reports a 2.x version.
</verification>

<success_criteria>
- src/mcp/ renamed to src/mcp_server/; all four import sites (server.py, app.py, pyproject.toml, test_mcp.py) updated; no more package collision with the installed mcp SDK.
- OpenRouter URL in parser.py has no doubled /v1 segment.
- MODEL_CASCADE and CONFIDENCE_THRESHOLDS in parser.py use the real OpenRouter model IDs from EXTRACTION.md §7 (meta-llama/llama-4-scout, anthropic/claude-haiku-4.5) with unchanged 0.70/0.0 threshold values.
- fastmcp bumped to ^2.x; server.py uses http_app() with no legacy shim; bearer-auth wrapping on /mcp is preserved.
- `poetry lock && poetry install && poetry run pytest -q` all succeed — 7/7 test files green, 0 failures.
</success_criteria>

<output>
Create `.planning/quick/260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-/260706-rso-SUMMARY.md` when done, with `status: complete` in its frontmatter.
</output>