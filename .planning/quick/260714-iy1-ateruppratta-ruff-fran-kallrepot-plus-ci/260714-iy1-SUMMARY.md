---
quick_id: 260714-iy1
status: complete
tasks_completed: 4
tasks_total: 4
tests_before: 214 passed
tests_after: 214 passed
files_modified:
  - pyproject.toml
  - poetry.lock
  - src/api/admin.py
  - src/mcp_server/server.py
  - tests/**
  - .github/workflows/ci.yml
  - CLAUDE.md
commits:
  - f945528 chore: restore ruff config inherited from source repo
  - 6274f0d style: ruff format + auto-fix across src/ and tests/
  - 4ac5025 ci: add CI that actually runs the tests
  - 61c10d6 docs: CLAUDE.md no longer lies about the toolchain
---

# Quick 260714-iy1 — Återupprätta ruff + CI

Ruff 0.14.4 restored from the source repo's config, `src/` + `tests/` formatted and
lint-clean, a real test-CI added (with Postgres, so the integration tests actually run),
and CLAUDE.md corrected. Suite unchanged at **214 passed**.

## What was done

| Task | Outcome |
| --- | --- |
| 1 — inherit ruff config | `ruff = "0.14.4"` + `[tool.ruff]` from source; `target-version` py311 → py312, first-party packages ours. `poetry check` green. |
| 2 — format + auto-fix | `ruff format` 16 files; 15 auto-fixes; 22 residual E501 fixed by hand. `ruff check` + `ruff format --check` both clean. |
| 3 — CI | New `.github/workflows/ci.yml`: ruff check → ruff format --check → pytest on push/PR, against a Postgres 16 service. `release.yml` untouched. |
| 4 — CLAUDE.md | Gotcha #1 ("ruff is not in this project — never invoke it") replaced with the truth; stack section lists ruff + CI. |

## Measured vs. predicted

The orchestrator's baseline held almost exactly — no dramatic divergence:

| | Predicted | Measured |
| --- | --- | --- |
| Total findings | 46 | **45** |
| Auto-fixable | 14 | **13** (15 applied incl. cascading) |
| E501 / F401 / UP017 / UP032 / UP041 | 32 / 3 / 2 / 1 / 1 | identical |
| I001 | 7 | **6** |
| Security (S/bandit) | 0 | **0** |
| `ruff format` | 16 files | **16 files** |

Only I001 differed (6 vs 7). Same rule shape otherwise; not worth stopping over.

## The one interesting finding

**The 32 E501s were self-inflicted by the port, not inherent.** The formatter cleared only 10 of
them; 20 remained inside `_get_admin_nav_css()` — a triple-quoted CSS literal, where `# noqa` is
impossible (a comment inside a string literal is just CSS text).

The tempting move was a per-file E501 ignore for `admin.py`. That would have been slack: it is a
1,689-LOC file of real Python, and blanket-ignoring E501 there would let genuinely long code lines
through forever.

Checking the source repo settled it: its equivalent (`interfaces/http/admin_shared.py`) has **zero**
lines over 100 chars, because it writes CSS one declaration per line. Our port had collapsed those
rules onto single lines — *that* is why the source passes E501 and we didn't. So the fix was to
restore the source's formatting, consistent with the "you restore, you don't invent" framing. CSS is
whitespace-insensitive, so rendering is unchanged; verified by rendering the dashboard (66,183 chars,
61 balanced brace pairs, no unsubstituted placeholders).

## Deviations from plan

1. **`poetry lock` was required before `poetry install`** — `poetry check` fails on a stale lock by
   design. Expected ordering, not a real deviation; noted for completeness.
2. **CI got an extra step not in the plan** (see below) — an addition, not a departure.

Nothing else. `B008` and `S101` were left ignored, as mandated. No fix turned a test red, so nothing
had to be reverted.

## The skip trap in CI (worth knowing)

The plan allowed integration tests to skip in CI if wiring Postgres proved awkward. It wasn't awkward
— but wiring it exposed a sharper problem: **`conftest.py` skips the 12 integration tests *cleanly*
when Postgres is unreachable, so the suite still exits 0.** A CI with a misconfigured DB would go
green while silently testing nothing — exactly the failure class this task exists to close.

So CI asserts the skip did not happen, and fails the build if it did. Verified both directions
locally:

- Postgres up → `12 passed` → guard passes
- Postgres unreachable → `12 skipped` → guard **fails the build** (as intended)

## Decisions

- **`ruff format` replaces black.** The source had black separately; `ruff format` is
  black-compatible, so one tool instead of two. Black was deliberately not pulled in.
- **mypy deliberately deferred.** The source repo has it (with strict flags), but type-checking
  ~10,800 ported lines is its own task, not a rider on this one. Recorded in CLAUDE.md.

## Known gaps (deferred, not silent)

- **`alembic/` is outside the linted scope.** `ruff check src tests` is what the plan specified and
  what CI runs; `alembic/` carries **21 further findings** (mostly E501). Left alone deliberately —
  the plan forbade touching `0001_initial.py`'s semantics. Bringing `alembic/` under lint is a
  follow-up.
- **mypy** — see above.

## Verification

```
poetry run ruff check src tests          -> All checks passed!
poetry run ruff format --check src tests -> 44 files already formatted
poetry run pytest -q                     -> 214 passed        (before: 214 passed)
poetry check                             -> All set!
python -c "yaml.safe_load(ci.yml)"       -> valid
git diff --diff-filter=D                 -> no deletions
```

Admin dashboard rendered end-to-end after both the f-string (UP032) rewrite and the CSS reflow — all
placeholders substituted, braces balanced.

## Self-Check: PASSED

- `pyproject.toml` `[tool.ruff]` present, ruff 0.14.4 installed — FOUND
- `.github/workflows/ci.yml` — FOUND, valid YAML, runs pytest
- `.github/workflows/release.yml` — unmodified
- CLAUDE.md stale ruff claim — 0 hits
- Commits f945528, 6274f0d, 4ac5025, 61c10d6 — all FOUND
