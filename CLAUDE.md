<!-- GSD:project-start source:PROJECT.md -->
## Project

**Price Tracker** — standalone Swedish grocery and pharmacy price tracker, originally extracted from the `ai-agent-platform` monolith at `/home/magnus/dev/ai-agent-platform`. Tracks prices at ICA, Willys, Apotea, Med24, Doz, Kronans Apotek, and Apohem; exposes its capabilities to the agent platform via an MCP server. Single-user (Magnus only); Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app.

**Status (2026-07-26): the extraction is done and this is a live product.** It is deployed in prod. Phase 04.1 — package data moved from `Product` to `ProductStore` — is built, verified, and deployed. Test suite: **472 passing** (that total includes 21 Postgres integration tests; with no DB reachable they skip cleanly and you get `451 passed, 21 skipped`).

**Deploy state (2026-07-26): prod runs `v0.26.0` — everything below is live and verified in prod.** Backup export checked against the live instance: 15 products / 38 links / 41 history rows, 38 KB, format 1.1. **This line goes stale the moment a tag is cut, so never trust it on its own** — `git tag | tail -1` is the newest tag and `ssh magnus@192.168.10.223 "sudo docker inspect price-tracker --format '{{.Config.Image}}'"` is what prod actually runs. Deploying is a one-line bump of the pinned tag in the home-server repo's `compose/dokploy-apps/price-tracker/docker-compose.yml` — that repo is not this repo, and the release is not live until that bump lands.

**Since 04.1 — what changed, and where the rule now lives.** This is an index, not the documentation: every durable rule sits in **Architecture** or **Gotchas** below, next to the code it governs. Do not grow this list into a changelog — `git log` is the changelog.

- **v0.20–v0.22:** `category` became a fixed ordered enum (→ category bullet); quick-add strips the brand out of the suggested name (→ Quick-add).
- **v0.14–v0.25.0 — anti-WAF:** shared politeness ledger with jitter, honest failure on a bot wall, scheduler auto-pause during adds, per-store circuit breaker (→ rate-limiter, fetcher and scheduler bullets). **ICA sits behind AWS WAF and its HTTP 202 is a JS challenge** — the un-built next levers are `curl_cffi` or a headless browser.
- **v0.25.1 — Willys quick-add:** the preview gained an API-first tier, because Willys is an SPA whose HTML has no price at all (→ Quick-add).
- **v0.25.2 / v0.25.3 — campaign prices:** the two store families were wrong in opposite directions and now agree (→ campaign-price bullet).
- **v0.26.0 — backup repaired:** the export had rotted into a backup of nothing (→ export/import bullet), and alembic was silencing app logging (→ Gotcha 5).

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
- Glossary: produkt · butik · länk · förpackning · mängd · **jfr-pris** (headings; the shelf-label word — value labels stay "kr/st"-style) · bevakning · erbjudande · i lager · Åtgärder.

**Comments:** explain WHY, not WHAT. The non-obvious invariants in `models.py` / `pricing.py` are commented on purpose — don't strip them.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

```
src/
├── domain/
│   ├── models.py  service.py  scheduler.py  parser.py  notifier.py  result.py
│   ├── pricing.py   # THE definition of kr/unit — see below. Never write a second one.
│   ├── schedule.py  # THE definition of check scheduling (store default → link override,
│   │                # next-check time). Scheduler AND admin API both resolve through it.
│   ├── categories.py # THE ordered category taxonomy (10 grocery sections in walk order) —
│   │                # normalize_category + category_sort_index. UI <select> injected from it.
│   ├── quickadd.py  # pure decision logic for quick-add (store match, package parse, product
│   │                # suggest, strip_brand_from_name) — docs/quick-add.md
│   ├── extractors/  (base.py, jsonld.py, willys_api.py)
│   └── stores/    (__init__.py)
├── api/
│   ├── app.py     # FastAPI factory + lifespan starts scheduler
│   ├── auth.py    # IAP header trust (X-Auth-Request-Email) + single-email gate
│   ├── admin.py   # portal + REST API (served at root, not /admin)
│   ├── schemas.py
│   └── templates/admin.html   # 3 fragments split on <!-- SECTION_SEPARATOR --> — see Gotchas.
│   │                          # The UI is ONE served page with four hash-routed pages
│   │                          # (#/produkter, #/erbjudanden, #/bevakningar, #/loggar) picked
│   │                          # from the sidebar; renderPage() in fragment 3 owns visibility +
│   │                          # active nav state. "Admin" is gone from all user-visible text (v0.7.1).
├── mcp_server/
│   └── server.py  # FastMCP, bearer auth, 4 tools
└── infra/
    ├── fetcher.py  # httpx; WAF walls fail fast + blocked flag (see bullet)
    ├── rate_limiter.py # THE per-store politeness ledger (reserve-a-slot + jitter), shared by scheduler + interactive
    ├── logbuffer.py # in-memory ring buffer of the app's own log records; portal Loggar page reads it via GET /logs (v0.18.0)
    ├── email.py    # Resend HTTP client
    ├── llm.py      # OpenRouter
    └── db.py       # async session factory
tests/
└── conftest.py (real-Postgres fixtures) + test_static_gates.py (AST invariant gates) + more
alembic/versions/   # 0001_initial (rewritten in place during 04.1 — see Gotchas) → 0002_store_label →
                    # 0003_seed_kronans → 0004_seed_apohem → 0005_store_schedule → 0006_normalize_categories
```

**Data model (5 tables):** `stores`, `products`, `product_stores`, `price_points`, `watches`. Tenant-scoped tables carry a `tenant_id` UUID column.

**The 04.1 shape — a product is abstract, a link is concrete:**

- **`package_size` + `package_quantity` live on `ProductStore`** (the link), not on `Product`. `unit` (st/liter/kg) stays on `Product`. The product is the abstract thing you compare; the link is the specific package a specific store sells.
- **`PricePoint.unit_price_sek` is GONE.** kr/unit is **computed on read** from `price / link.package_quantity`, exposed as the hybrid `PricePoint.computed_unit_price_sek` (Python + SQL modes). **The single definition lives in `src/domain/pricing.py`. Do not write a second one anywhere.** Correcting a link's amount retroactively fixes all its history — that's the point.
- **`PricePoint.store_unit_price_sek`** is the store's *printed* comparison price. It is displayed beside the computed value and **never sorted on** — stores print in different units, so sorting on it compares kr/kg against kr/st.
- **`ProductStore.scraped_package_quantity`** is what the page said, kept as *evidence*. Typed input is *intent*. Evidence autofills an empty field and **flags** a conflict; it never overwrites intent.
- **A campaign is ORDINARIE + OFFER, identically in every store (v0.25.2–v0.25.3):** `price_sek` is the ordinarie price, `offer_price_sek` is what you pay now, `offer_type="kampanj"`. Both extractors used to be wrong, in opposite directions. **Willys API:** `priceValue` is ordinarie and `savingsAmount` the per-unit discount, so the offer is `priceValue - savingsAmount` (verified 21.29 − 3.39 = 17.90 = `potentialPromotions[].price`); the old code ADDED the saving, inventing a "regular" price that appears nowhere in the API. **JSON-LD pharmacies:** the struck-through ordinarie hides in the offer's `priceSpecification` — `ListPrice` (Apohem) or `StrikethroughPrice` (Kronans), dict or list, http or https — and counts only when it is **higher** than `offers.price`; the old code kept the current price as `price_sek` and discarded ordinarie, so a pharmacy sale never surfaced in "Erbjudanden". A `UnitPriceSpecification` bearing only the jfr-pris is not an ordinarie. **kr/unit is unaffected by all of this** — it ranks on `effective_price_sek = coalesce(offer, price)`, which is the price paid either way.
- **`ProductStore.store_label` (v0.6.0)** is the link's optional display store name ("ICA Maxi Sandviken") for chains that price per physical butik — two ICA butiker are two links under the ONE chain-level `Store` row (whose slug drives the extractors; never add per-butik Store rows). `domain.models.link_store_name` (label wins, chain name is fallback) is the single display rule; every store-name emission (links, history, deals, emails, MCP) goes through it. Quick-add suggests the label from the URL's `/stores/<id>/` segment (`quickadd.KNOWN_STORE_LABELS`), and for butiker in `quickadd.SIBLING_STORE_GROUPS` it also offers to create the sister-butik links in the same confirm (v0.7.0) — siblings are created optimistically and left for the scheduler (v0.19.0 stopped fetching them inline: sister butiker share ONE `store_id`/host, so an inline check per sibling was the same-host burst that tripped ICA's WAF, and under the block-aware fetcher a rate-limited fetch is indistinguishable from an absent page, so it would wrongly delete valid siblings). Both tables are per-instance operator config since v0.8.0: env `QUICKADD_STORE_LABELS` / `QUICKADD_SIBLING_GROUPS` (JSON), in-repo defaults are Magnus's butiker, malformed JSON warns and falls back — the repo is public/MIT and a second instance sets its own.
- **The check schedule is a STORE property (v0.13.0):** `Store.check_weekdays` (JSONB list, 0=måndag — ICA `[0]`, Willys `[0, 4]`) + `Store.check_frequency_hours` (72 for the pharmacies), both modes morning-aligned (06–12). A link's own `check_weekdays`/`check_frequency_hours` are NULL in the normal state = inherit; setting either overrides WHOLESALE. Resolution + next-check time live in `domain/schedule.py` — one definition, used by the scheduler and the admin `/frequency` endpoint alike (its private copy was removed; do not reintroduce one). Quick-add and the add-link dialog ask nothing about scheduling; only the link-edit dialog offers the override.
- **`Product.category` is a fixed ordered enum (v0.20.0–v0.21.0):** the 10 grocery-section values and their in-store walk order live in `domain/categories.py` (THE list). `normalize_category()` coerces free text / an old LLM guess to a canonical value or None (keyword map; `"ost"`/`"fil"` ordered last so they don't swallow `"kosttillskott"`/`"filmjölk"`); `category_sort_index()` is the aisle-order sort key (ready for a future shopping-list sort; the product list still sorts by name). The UI `<select>` is server-injected from the list in `render_admin` (no second copy) and `test_static_gates.py` guards the `<!--CATEGORY_OPTIONS-->` placeholder. Server normalises on create/update/import. Free text is gone; "Kött, Chark & Deli" merged the old meat/chark/fish split.
- **The politeness ledger is SHARED and lives in `infra/rate_limiter.py` (v0.14.0):** `StoreRateLimiter` (process-wide singleton via `providers.get_rate_limiter`, injected into the scheduler) is THE per-store request throttle — reserve-a-slot, keyed by store id. The scheduler spaces its background checks 60 s apart (`RATE_LIMIT_DELAY`); the **interactive** fetches (quick-add preview, first check, manual re-check in `admin.py`) now go through the SAME ledger with a short interval and a `max_wait` cap (env `QUICKADD_RATE_LIMIT_DELAY`=5 s, `QUICKADD_MAX_WAIT`=10 s) so they can't burst a store's WAF while never stalling a human on a background reservation. **Both cadences carry jitter on top of the floor (v0.24.0)** — `RATE_LIMIT_JITTER`=30 s background, env `QUICKADD_RATE_LIMIT_JITTER`=3 s interactive — so a store isn't hit on a clockwork beat; jitter only extends the *next* slot, never the current caller's wait (`max_wait` still holds). Before the shared ledger, interactive fetches bypassed all throttling — that is what let quick-add + scheduler checks trip ICA into HTTP 202/403. **Do not reintroduce the scheduler's old private `_last_request_at` dict.**
- **The fetcher fails honestly on a bot wall (v0.14.0; hardened v0.25.0):** `WebFetcher.fetch` never treats a wall as a page (the old `raise_for_status()` let a 202 through as an empty page → "metadata extraction failed" instead of "blocked"). **WAF statuses (HTTP 202/403/429) FAIL FAST — no retry — and return `blocked=True`:** ICA's 202 is an AWS WAF JS challenge (`awsWafCookieDomainList`/`gokuProps`, needs a real browser to mint an `aws-waf-token`) that a few-second retry cannot solve and only reinforces the IP flag. Only *transient* failures (5xx, network error, empty body) get the short bounded retry (`_RETRY_DELAYS_SECONDS`); a hard 4xx (404/410) fails fast. The `blocked` flag propagates `fetcher → perform_price_check` (`PriceCheckOutcome.blocked`) `→ scheduler`. Chrome fingerprint (consistent UA + `sec-ch-ua` + `sec-fetch-*`, bump `chrome_major` when stale) reduces how often the challenge fires but can't beat the JS challenge or the TLS/JA3 layer — `curl_cffi` (TLS impersonation) / a headless browser are the un-built next levers if ICA keeps challenging.
- **The scheduler self-defends against store WAFs (v0.23.0 pause, v0.25.0 breaker):** (1) **Auto-pause** — the add flows (`/quick-add/preview`, `/quick-add`, `POST /products/{id}/stores`) call `scheduler.pause_for(...)`, silencing background checks for a window each add pushes forward (env `SCHEDULER_ADD_PAUSE_MINUTES`=60) so a burst of manual adds can't race the scheduler into a store's WAF; `paused_until` shows in `/scheduler/status` and the portal status box (PAUSAD — parse it with `parseUtc`, it's naive UTC). (2) **Per-store circuit breaker** — a `blocked` outcome cools the WHOLE store down (env `SCHEDULER_STORE_BLOCK_COOLDOWN_MINUTES`=30): its due links are skipped without fetching and deferred past the cooldown, so "poke all N ICA links during a challenge" becomes one probe then stand down. Both are in-memory (single-process); the breaker is scheduler-scoped — interactive fetches are NOT gated by it. A failed weekday-scheduled check still reschedules +24h (unchanged).
- **`uq_product_store(product_id, store_id)` is DROPPED; `store_url` is globally unique** (`uq_product_stores_store_url`). One product can have several links at the same store (different pack sizes). The URL is the link's natural key. **An AST gate in `tests/test_static_gates.py` fails any query in `src/` that resolves a link by the `(product_id, store_id)` pair** — if that test fires, your query is built on the pre-04.1 model, not on a lint nit.

**Export/import is THE backup, and a backup is judged by what it leaves out (v0.26.0):** `GET /export` writes **every product in the tenant**. It used to inner-join *active watches*, so a tracker with products but no watch exported `products: []` under a **200 OK** — 15 prod products became a 205-byte file. A watch is one optional attribute of a product, never the condition for saving it. Format **1.1** carries `store_label` and keys each history row on `store_url` (the link's natural key — product+slug cannot address one of several links at one store, the pre-04.1 shape the AST gate forbids). `POST /import` restores products, links, watches **and price history**, deduped on `(link, checked_at)` so re-importing is idempotent; history used to be exported and then silently dropped. A 1.0 file still imports — its unaddressable history rows are skipped with a warning, never guessed at. The portal's button passes `history_days=0` = ALL history (the 30-day default silently truncated). `tests/test_export_import.py` is a real-Postgres round trip — export, wipe, import, compare — and these endpoints had **no test at all** before it, which is exactly how they rotted.

**MCP surface:** `check_price`, `find_deals`, `compare_stores`, `list_products` — see EXTRACTION.md §5.

**Quick-add (v0.5.0):** `POST /quick-add/preview` + `POST /quick-add` create a product AND its store link from one pasted URL (store matched by hostname, package parsed from the title). **The preview's identity ladder mirrors the price-check ladder: store API → JSON-LD → LLM (v0.25.1).** For a store with a structured API — `domain.parser.get_api_extractor`, currently only Willys — it reads identity + package straight off the REST API (`WillysApiExtractor.extract_metadata`) and never fetches the page. That tier exists because **Willys is a client-rendered SPA: its product HTML carries no JSON-LD and no price**, so the HTML→LLM ladder saw an empty shell and returned a blank form. The four JSON-LD stores (the pharmacies) still take the JSON-LD path — verified 2026-07-26 that all five pharmacies serve a parseable Product node server-side. The suggested name is the abstract good — `strip_brand_from_name` drops a leading/trailing brand the source folded in (v0.22.0), so it doesn't double-print beside the brand column. Preview-then-confirm on purpose — the confirm step is where "ny produkt" vs "ny länk på befintlig produkt" gets decided, which keeps quick-add from rebuilding the pre-04.1 one-product-per-pack-size shape. Decision logic is pure functions in `domain/quickadd.py`; full design in `docs/quick-add.md`.

**Auth:** The portal + API are served at the app root (`price.<domain>/`); the `/admin` prefix was dropped (old `/admin` URLs 308-redirect to `/`) — D-28. The UI trusts the `X-Auth-Request-Email` header forwarded by the upstream ingress and validates it against `ALLOWED_ENTRA_EMAIL` (which must match the Entra **UPN**, not necessarily the gmail address). Entra ID enforcement itself is **live in production since 2026-07-09**: oauth2-proxy v7.15.2 + Traefik `forwardAuth` (`entra-auth@file`), email claim = `preferred_username`/UPN, managed in the home-server repo — not in this app. The MCP server is served at `price.<domain>/mcp/` (no `mcp.` subdomain — a path-scoped, un-gated Traefik router with explicit priority bypasses the Entra gate for `/mcp` only) and is protected by its own static bearer token; without `MCP_BEARER_TOKEN` the endpoint fails closed (503). See EXTRACTION.md §6 for background, though its original in-app-OIDC description was superseded by this IAP header-trust model, and D-18's `mcp.<domain>` subdomain plan was superseded by the `/mcp` path (D-29 in .planning/STATE.md).
<!-- GSD:architecture-end -->

## Releasing — shipping is part of the task, not a separate errand

**A fix that is not released is not delivered.** Prod runs a *pinned image tag*, so work that only reaches `main` changes nothing for the person using the app. Unreleased commits used to pile up for days (12 of them by v0.3.2 — a whole ruff/CI rebuild plus the price-history fix, all sitting on `main` doing nothing).

**Standing rule: when a change is user-visible or fixes a real problem, cut a release as the last step of the task — without being asked.** Do it in the same session as the fix.

Release = push a `v*` tag. That is the only trigger; `.github/workflows/release.yml` then builds and pushes `ghcr.io/magter/price-tracker:vX.Y.Z` (+ `:sha-<commit>`) for amd64. Nothing else in this repo deploys anything.

```bash
# 1. main is green and pushed. Never tag a dirty tree or an un-pushed commit —
#    the tag would point at something no one else can fetch.
poetry run ruff check src tests && poetry run ruff format --check src tests && poetry run pytest -q
git push origin main

# 2. The version in pyproject.toml IS the tag. Bump it, commit it, then tag that commit —
#    a tag whose code says a different version is a lie you will read back later.
#    (It said 0.1.0 while prod ran v0.3.2. Don't let that happen again.)
git tag vX.Y.Z && git push origin vX.Y.Z

# 3. Watch the build. A tag that fails to build is not a release.
gh run watch "$(gh run list --workflow=release.yml --limit=1 --json databaseId -q '.[0].databaseId')"
```

**Version choice** (single-user app, so this is deliberately simple):
- **patch** (`v0.3.2` → `v0.3.3`) — a bug fix, a corrected display, an internal cleanup.
- **minor** (`v0.3.x` → `v0.4.0`) — a new capability, a schema change, or anything that alters how the app is operated or deployed.
- A schema change means **a new alembic revision** (additive, on top of the chain — see Gotcha 3 for why a shipped revision is never rewritten in place). The deploy applies it via `alembic upgrade head`; verify with `alembic check`, not `alembic current`.

**Then tell Magnus the tag is built and needs the deploy bump.** Deploying is a one-line change to the pinned tag in the home-server repo's `compose/dokploy-apps/price-tracker/docker-compose.yml` (GitOps split: this repo builds, the platform repo is the deployment truth). **That repo is not this repo — do not edit it from here**, and the release is not live until that bump lands.

Docs-only, planning-only, or test-only commits do **not** earn a tag; they ride along with the next real one.

## Prod logs & observability

Prod runs on the home-server platform (Dokploy). Nothing runs locally — `docker ps` here is empty. To read live prod logs, SSH to the **Dokploy VM (`magnus@192.168.10.223`, VMID 200)** and read the container directly; `sudo` is required (magnus isn't in the docker group there):

```bash
# containers: `price-tracker` (app) and `price-tracker-postgres`
ssh magnus@192.168.10.223 "sudo docker logs --since 10m --timestamps price-tracker"
ssh magnus@192.168.10.223 "sudo docker logs --tail 200 -f price-tracker"     # follow
```

The image is `python:3.12-slim` — **no `curl`**; for in-container HTTP probing use `python -c` with `httpx` (already a dep), e.g. to hit OpenRouter with the live key without it leaving the box:
```bash
ssh magnus@192.168.10.223 'sudo docker exec price-tracker python -c "import os,httpx; ..."'
```

Notes:
- `home-server`'s `scripts/logs.sh` does the same SSH-to-Dokploy dance but its container allowlist is only `hermes`/`oauth2-proxy` — it does **not** know price-tracker; go direct with the commands above.
- Server-side apps do **not** ship to the `applogs`/logsink sink (that's for unreachable devices — phones, head units; ADR-011). price-tracker logs live only in Docker/journald on the Dokploy VM.
- The home-server topology: PVE `192.168.10.220`, ops LXC `.221`, AdGuard `.222`, **Dokploy `.223`**, dev `.224`.

## Gotchas

Traps that already cost time. Each one **passes silently** — that's why they're here.

1. **`ruff` exists — and `B008`/`S101` are ignored on purpose.** ruff 0.14.4 is a declared dev-dependency, with the config inherited from the source repo (`ai-agent-platform/services/agent`); Phase 1 rewrote `pyproject.toml` from scratch and dropped it, which is why the planning docs kept assuming a ruff that wasn't there. Run `poetry run ruff check src tests` and `poetry run ruff format src tests` (`ruff format` is black-compatible and **replaces** black — do not add black). **Never "fix" `B008`:** it is FastAPI's `Depends()`-in-default-argument pattern, and hoisting the call out of the default argument breaks dependency injection — and with it the auth guard. `S101` is ignored because `assert` is how tests assert. **The test suite is the truth, not the linter:** if an auto-fix turns a test red, the fix was wrong — revert it (`UP017` and `UP032` rewrite real logic, not just style).

2. **`src/api/templates/admin.html` has no `<script>` tag.** It is three fragments split on `<!-- SECTION_SEPARATOR -->` and reassembled in `admin.py` at request time (`admin.py` ~L1877). A gate that extracts the `<script>` body gets **zero bytes**, and `node --check` on empty input **exits 0** — so it passes no matter what you wrote. The working incantation:
   ```bash
   awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js \
     && test -s /tmp/admin.js && node --check /tmp/admin.js
   ```
   Keep the `test -s` guard. And remember: **"the file parses" is not "the page renders."** Verify rendering separately.

3. **Alembic: `0001_initial.py` was rewritten in place** during Phase 04.1. Alembic does not checksum migration bodies, so a database stamped `0001_initial` from BEFORE that rewrite compares equal to head, runs **nothing**, and exits **0** — the app then boots quietly against the **old** schema. **`alembic current` will not reveal this** — it prints the same revision either way. **Only `alembic check` does.** That trap was resolved by the 04.1 volume drop (`README.md` § "Schema reset (Phase 04.1)"). **Since v0.6.0 the chain is normal again:** `0002_store_label` is a real additive revision on top of `0001_initial`, and schema changes from here on are ordinary new revisions applied by `alembic upgrade head` — never rewrite a shipped revision in place again.

4. **TECH DEBT — there are two `get_price_history`.** `src/domain/service.py` has the rich, per-link one, and **only MCP calls it**. `src/api/admin.py` runs its **own duplicate query** for `GET /products/{id}/prices`, and **that** is the one the frontend hits. They have already drifted apart — it was the root cause of the price-history bug (package data never crossed the wire). If you touch price history via the API, you are editing `admin.py`, not `service.py`. Consider collapsing them.

5. **`alembic/env.py` must keep `disable_existing_loggers=False`.** `fileConfig()` defaults that flag to **True**, which sets `disabled = True` on every logger that already exists — including the app's own `domain`/`infra`/`api`. Nothing raises: the loggers simply stop emitting, so app logs and the portal's Loggar buffer go quiet. Invisible when alembic owns the process (the deploy runs it standalone), fatal in any process that ran the app first. It surfaced as two `test_logbuffer` failures the moment a DB-backed test file happened to sort before it — and the tests passed in isolation, which is what makes it a gotcha rather than a bug report.

6. **An endpoint with no test rots silently.** `/export` + `/import` had **zero** tests and drifted until the export backed up nothing while still returning 200 OK. Both are now covered by a real-Postgres round trip (`tests/test_export_import.py`). Before trusting any endpoint that no test names, check what it actually returns against real data — a green suite says nothing about code it never calls.

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
