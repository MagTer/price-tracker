<!-- GSD:project-start source:PROJECT.md -->
## Project

**Price Tracker** — standalone Swedish grocery and pharmacy price tracker, originally extracted from the `ai-agent-platform` monolith at `/home/magnus/dev/ai-agent-platform`. Tracks prices at ICA, Willys, Apotea, Med24, and Doz; exposes its capabilities to the agent platform via an MCP server. Single-user (Magnus only); Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app.

**Status (2026-07-14): the extraction is done and this is a live product.** It is deployed in prod (latest tag `v0.3.2`). Phase 04.1 — package data moved from `Product` to `ProductStore` — is built, verified, and deployed. Test suite: **214 passing** (that total includes 12 Postgres integration tests; with no DB reachable they skip cleanly and you get `202 passed, 12 skipped`).

Remaining from the original extraction plan:
- **Phase 4 tail:** register the MCP server with Hermes (`/platformadmin/mcp/`) in the agent platform.
- **Phase 5:** delete the price-tracker code from `ai-agent-platform` — `services/agent/src/modules/price_tracker/` and friends still exist there.

**Historical context:** [EXTRACTION.md](./EXTRACTION.md) describes the original 5-phase port. It is a **historical document, not current law** — where it conflicts with this file or with `.planning/STATE.md`, those win.

**Planning artifacts:** `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:PROJECT.md -->
## Technology Stack

Changing a stack component is a real decision — discuss it first. Everything below is what the code actually uses.

- **Language/runtime:** Python 3.12
- **Web:** FastAPI + uvicorn
- **DB:** PostgreSQL via SQLAlchemy 2.0 (asyncio) + Alembic. **Tests run against real Postgres, not SQLite** — the models use `postgresql.UUID` and `JSONB`, which aiosqlite cannot compile. `tests/conftest.py` drops and recreates a throwaway `price_tracker_test` database; if no DB is reachable, the integration tier skips cleanly. (`aiosqlite` is still declared in `pyproject.toml` but no code imports it — vestigial, safe to remove.)
- **HTTP client:** httpx (async)
- **Email:** Resend HTTP API (`https://api.resend.com/emails`) — same provider as the source platform; no SMTP (D-32)
- **LLM:** OpenRouter direct at `https://openrouter.ai/api/v1` (OpenAI-compatible) — no LiteLLM proxy
- **MCP:** `fastmcp` (mounts on FastAPI)
- **Auth (UI):** IAP header trust — reads `X-Auth-Request-Email` forwarded by the upstream Traefik + auth-middleware ingress (managed via Dokploy); no in-app OIDC client
- **Auth (MCP):** static bearer token (`MCP_BEARER_TOKEN`)
- **Frontend:** server-rendered HTML + vanilla JS + Chart.js (ported from source — no SPA framework)
- **Tests:** pytest, pytest-asyncio
- **Lint/format:** ruff 0.14.4 (config inherited from the source repo). `ruff format` replaces black — do not add black. mypy is **not** carried over yet (the source had it; typing 10,800 ported lines is its own task).
- **CI:** `.github/workflows/ci.yml` — ruff check → ruff format --check → pytest, on every push and PR, against a Postgres 16 service so the integration tests actually run. (`release.yml` only builds the image on a version tag.)
- **Packaging:** poetry
- **Container:** `python:3.12-slim` Docker image; docker-compose with Traefik labels
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

**The port doctrine is over. Read this before you "restore" anything.**

Phases 1–3 were a byte-equivalent port from the monolith, and the rule then was: don't refactor, don't fix known shortcomings, don't add abstractions — so the result stayed diffable against the source. **That rule expired when the port landed. It no longer governs this repo.**

This is now an ordinary product repo:
- **Refactoring, model changes, and bug fixes are allowed and expected.** Don't ask "does this match the source?" — ask "is this right?"
- **Phase 04.1 was a deliberate, verified, deployed model change** (package data moved to the store link). It is not drift from the port. **Reverting it in the name of "the source repo did it differently" would be a regression** — the source repo is being deleted (Phase 5).

**Language — one track (decided 2026-07-14):**
- **Swedish** for everything a user sees: UI strings, toasts, user-facing `HTTPException(detail=...)`, email, MCP tool output. The page declares `lang="sv"`.
- **English** for everything a developer sees: identifiers, DB columns, JSON keys / wire contracts, logs, docstrings, comments, commit messages, and internal 500-level errors.
- Glossary: produkt · butik · länk · förpackning · mängd · **kr/enhet** · bevakning · erbjudande · i lager · Åtgärder.

**Comments:** explain WHY, not WHAT. The non-obvious invariants in `models.py` / `pricing.py` are commented on purpose — don't strip them.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

```
src/
├── domain/
│   ├── models.py  service.py  scheduler.py  parser.py  notifier.py  result.py
│   ├── pricing.py   # THE definition of kr/unit — see below. Never write a second one.
│   ├── extractors/  (base.py, willys_api.py)
│   └── stores/    (__init__.py)
├── api/
│   ├── app.py     # FastAPI factory + lifespan starts scheduler
│   ├── auth.py    # IAP header trust (X-Auth-Request-Email) + single-email gate
│   ├── admin.py   # portal + REST API (served at root, not /admin)
│   ├── schemas.py
│   └── templates/admin.html   # 3 fragments split on <!-- SECTION_SEPARATOR --> — see Gotchas
├── mcp_server/
│   └── server.py  # FastMCP, bearer auth, 4 tools
└── infra/
    ├── fetcher.py  # httpx
    ├── email.py    # Resend HTTP client
    ├── llm.py      # OpenRouter
    └── db.py       # async session factory
tests/
└── conftest.py (real-Postgres fixtures) + test_static_gates.py (AST invariant gates) + 9 more
alembic/versions/0001_initial.py   # the only migration — rewritten in place, see Gotchas
```

**Data model (5 tables):** `stores`, `products`, `product_stores`, `price_points`, `watches`. Tenant-scoped tables carry a `tenant_id` UUID column.

**The 04.1 shape — a product is abstract, a link is concrete:**

- **`package_size` + `package_quantity` live on `ProductStore`** (the link), not on `Product`. `unit` (st/liter/kg) stays on `Product`. The product is the abstract thing you compare; the link is the specific package a specific store sells.
- **`PricePoint.unit_price_sek` is GONE.** kr/unit is **computed on read** from `price / link.package_quantity`, exposed as the hybrid `PricePoint.computed_unit_price_sek` (Python + SQL modes). **The single definition lives in `src/domain/pricing.py`. Do not write a second one anywhere.** Correcting a link's amount retroactively fixes all its history — that's the point.
- **`PricePoint.store_unit_price_sek`** is the store's *printed* comparison price. It is displayed beside the computed value and **never sorted on** — stores print in different units, so sorting on it compares kr/kg against kr/st.
- **`ProductStore.scraped_package_quantity`** is what the page said, kept as *evidence*. Typed input is *intent*. Evidence autofills an empty field and **flags** a conflict; it never overwrites intent.
- **`uq_product_store(product_id, store_id)` is DROPPED; `store_url` is globally unique** (`uq_product_stores_store_url`). One product can have several links at the same store (different pack sizes). The URL is the link's natural key. **An AST gate in `tests/test_static_gates.py` fails any query in `src/` that resolves a link by the `(product_id, store_id)` pair** — if that test fires, your query is built on the pre-04.1 model, not on a lint nit.

**MCP surface:** `check_price`, `find_deals`, `compare_stores`, `list_products` — see EXTRACTION.md §5.

**Auth:** The portal + API are served at the app root (`price.<domain>/`); the `/admin` prefix was dropped (old `/admin` URLs 308-redirect to `/`) — D-28. The UI trusts the `X-Auth-Request-Email` header forwarded by the upstream ingress and validates it against `ALLOWED_ENTRA_EMAIL` (which must match the Entra **UPN**, not necessarily the gmail address). Entra ID enforcement itself is **live in production since 2026-07-09**: oauth2-proxy v7.15.2 + Traefik `forwardAuth` (`entra-auth@file`), email claim = `preferred_username`/UPN, managed in the home-server repo — not in this app. The MCP server is served at `price.<domain>/mcp/` (no `mcp.` subdomain — a path-scoped, un-gated Traefik router with explicit priority bypasses the Entra gate for `/mcp` only) and is protected by its own static bearer token; without `MCP_BEARER_TOKEN` the endpoint fails closed (503). See EXTRACTION.md §6 for background, though its original in-app-OIDC description was superseded by this IAP header-trust model, and D-18's `mcp.<domain>` subdomain plan was superseded by the `/mcp` path (D-29 in .planning/STATE.md).
<!-- GSD:architecture-end -->

## Gotchas

Traps that already cost time. Each one **passes silently** — that's why they're here.

1. **`ruff` exists — and `B008`/`S101` are ignored on purpose.** ruff 0.14.4 is a declared dev-dependency, with the config inherited from the source repo (`ai-agent-platform/services/agent`); Phase 1 rewrote `pyproject.toml` from scratch and dropped it, which is why the planning docs kept assuming a ruff that wasn't there. Run `poetry run ruff check src tests` and `poetry run ruff format src tests` (`ruff format` is black-compatible and **replaces** black — do not add black). **Never "fix" `B008`:** it is FastAPI's `Depends()`-in-default-argument pattern, and hoisting the call out of the default argument breaks dependency injection — and with it the auth guard. `S101` is ignored because `assert` is how tests assert. **The test suite is the truth, not the linter:** if an auto-fix turns a test red, the fix was wrong — revert it (`UP017` and `UP032` rewrite real logic, not just style).

2. **`src/api/templates/admin.html` has no `<script>` tag.** It is three fragments split on `<!-- SECTION_SEPARATOR -->` and reassembled in `admin.py` at request time (`admin.py` ~L1877). A gate that extracts the `<script>` body gets **zero bytes**, and `node --check` on empty input **exits 0** — so it passes no matter what you wrote. The working incantation:
   ```bash
   awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js \
     && test -s /tmp/admin.js && node --check /tmp/admin.js
   ```
   Keep the `test -s` guard. And remember: **"the file parses" is not "the page renders."** Verify rendering separately.

3. **Alembic: `0001_initial.py` was rewritten in place** (there is no `0002`). Alembic does not checksum migration bodies, so a database already stamped `0001_initial` compares equal to head, runs **nothing**, and exits **0**. The app then boots quietly against the **old** schema. **`alembic current` will not reveal this** — it prints the same revision either way. **Only `alembic check` does.** A schema change requires dropping the volume; see `README.md` § "Schema reset (Phase 04.1)".

4. **TECH DEBT — there are two `get_price_history`.** `src/domain/service.py` has the rich, per-link one, and **only MCP calls it**. `src/api/admin.py` runs its **own duplicate query** for `GET /products/{id}/prices`, and **that** is the one the frontend hits. They have already drifted apart — it was the root cause of the price-history bug (package data never crossed the wire). If you touch price history via the API, you are editing `admin.py`, not `service.py`. Consider collapsing them.

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` — do not edit manually.
<!-- GSD:profile-end -->
