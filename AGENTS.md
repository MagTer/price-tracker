# price-tracker — for agents

Read on every turn: only what binds every task. Knowledge for one kind of task is a project skill (below); why a thing is as it is lives next to its code or in `docs/`.

## Project

**Price Tracker** — standalone Swedish grocery and pharmacy price tracker, originally extracted from the `ai-agent-platform` monolith at `/home/magnus/dev/ai-agent-platform`. Tracks prices at ICA, Willys, Apotea, Med24, Doz, Kronans Apotek, Apohem, Rusta, Clas Ohlson, Lyko, and JYSK; exposes its capabilities to the agent platform via an MCP server. Single-**owner**: one admin (Magnus) who may change anything, and read-only access for everyone else the Entra gate authenticates (v0.29.0 — → Auth bullet); one tenant of data either way. Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app.

**Status (2026-08-13): the extraction is done and this is a live product.** It is deployed in prod. Phase 04.1 — package data moved from `Product` to `ProductStore` — is built, verified, and deployed. Test suite: no count is written here, because a count rots exactly like a pinned version does — the authority is the last green CI run (`gh run view "$(gh run list --workflow=ci.yml --limit=1 --json databaseId -q '.[0].databaseId')" --log | grep passed`), which prints both figures. No local Postgres is needed to run the integration tier — `docker run --rm -e POSTGRES_USER=price_tracker -e POSTGRES_PASSWORD=price_tracker -e POSTGRES_DB=price_tracker -p 55432:5432 postgres:16` plus `TEST_DATABASE_URL` pointed at it runs all of it, and that is how a schema change should be verified before it is tagged.

**Deploy state: DO NOT state it from this file — read it, every time, with the one command below.** No pinned version is recorded here on purpose. A written-down version is wrong the moment Magnus bumps, and a stale one has already been repeated back to him as fact ("prod runs v0.32.1" when prod was four releases further on, at v0.36.0) — an assertion about someone else's system that costs them trust in every other claim in the same message. **Magnus bumps the pin himself, directly in the GitHub web UI** (commits read "Update price-tracker image to version X"), so the deployment truth is the home-server repo's file, not this machine and not a memory of it:

```bash
# WHAT PROD IS PINNED TO — the authority. No SSH, no clone, works from anywhere.
# The pin is a digest under a comment naming its tag, in the shared base, so it moves the lab
# and production together. (Until 2026-10-02 it was compose/dokploy-apps/…, now gone: 404.)
gh api repos/MagTer/home-server/contents/clusters/base/price-tracker/app.yaml \
  --jq '.content' | tr -d '\n' | base64 -d | grep -A1 '# ghcr.io/magter/price-tracker:'
# The newest tag HERE — the gap between the two is what is undeployed. `--sort=v:refname` is
# NOT optional: git tag sorts LEXICALLY, so v0.9.0 sorts after every v0.1x–v0.5x tag and a
# bare `git tag | tail -1` answers v0.9.0 no matter how far the repo has come. That is the
# stale-version error this whole paragraph exists to prevent, handed to you by its own recipe.
git tag --sort=v:refname | tail -1
```

`ssh magnus@192.168.10.221 "sudo kubectl --kubeconfig /etc/talos-prod/kubeconfig -n price-tracker get deploy price-tracker -o jsonpath='{.spec.template.spec.containers[0].image}'"` (from the ops LXC, which alone reaches the cluster) answers what the container is actually RUNNING, which is the check worth making when a deploy is suspected to have not landed. Backup export checked against the live instance (on v0.26.0): 15 products / 38 links / 41 history rows, 38 KB, format 1.1. Deploying is the pin bump in the home-server repo's `clusters/base/price-tracker/app.yaml` (→ Releasing; it was `compose/dokploy-apps/price-tracker/docker-compose.yml` until 2026-10-02) — that repo is not this repo, do not edit it from here, and the release is not live until the bump lands.

**The extraction is finished (closed out 2026-07-26).** One follow-up remains, and it is not in this repo:

- **MCP registration — open, but reframed.** The plan said "register the MCP server with Hermes at `/platformadmin/mcp/` in `ai-agent-platform`". That platform has been wound down; what runs in prod is the third-party gateway image `nousresearch/hermes-agent`, whose compose in the home-server repo carries **no MCP configuration** (checked 2026-07-26). Wiring price-tracker's MCP to *that* gateway is still wanted. Nothing here blocks it — the server is live and bearer-gated at `price.<domain>/mcp/`.

- **Phase 5 (delete the source code from `ai-agent-platform`) is closed as obsolete** — the repo no longer exists on the dev machine, so there is no source tree left to clean. Do not go looking for it.

**Historical context:** [EXTRACTION.md](./EXTRACTION.md) describes the original 5-phase port. It is a **historical document, not current law** — where it conflicts with this file or with `.planning/STATE.md`, those win.

**Planning artifacts:** `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`.

## Project skills

Knowledge for one kind of task lives in a project skill under `.claude/skills/` (linked for
other clients at `.agents/skills/`), loaded when the task matches its description:

| Skill | Load it when |
|---|---|
| `store-extractors` | adding a store, fixing an extractor, campaign/offer parsing, quick-add |
| `store-fetching` | a store walls or blocks checks; ledger, fetcher, breaker, schedule, scheduler, `check_attempts` |
| `price-judgements` | a price, kr/enhet, verdict, statistic, the buy-list mail or an MCP tool changes |
| `portal-ui` | `admin.html` / `shell.*` changes, or a UI change must be shown to work |

`README.md` is for people: what the app is, how to run, deploy and operate it.

## Verification

Run before and after every change; CI runs the same three on every push:

```bash
poetry run ruff check src tests && poetry run ruff format --check src tests && poetry run pytest -q
```

Without a reachable Postgres the integration tier skips cleanly. To run it, start the
throwaway container from the Status paragraph above and point the suite at it:
`TEST_DATABASE_URL=postgresql+asyncpg://price_tracker:price_tracker@localhost:55432/price_tracker`.
**Known flake:** `tests/test_scheduler_leadership.py::TestPostgresLeaderLock::test_one_holder_at_a_time_and_takeover_when_the_holder_dies`
has failed in a full run and passed in isolation (observed 2026-10-19 and 2026-10-31) —
rerun it alone before treating it as a regression.

## Technology Stack

Changing a stack component is a real decision — discuss it first. Everything below is what the code actually uses.

- **Language/runtime:** Python 3.12

- **Web:** FastAPI + uvicorn

- **DB:** PostgreSQL via SQLAlchemy 2.0 (asyncio) + Alembic. **Tests run against real Postgres, not SQLite** — the models use `postgresql.UUID` and `JSONB`, which aiosqlite cannot compile. `tests/conftest.py` drops and recreates a throwaway `price_tracker_test` database; if no DB is reachable, the integration tier skips cleanly. (`aiosqlite` was dropped from `pyproject.toml` in v0.51.0 — do not re-add it to make a test run without Postgres; it cannot compile these models.)

- **HTTP client:** **curl_cffi** for store fetches (Chrome TLS/JA3 + h2 impersonation; env `FETCHER_TRANSPORT=httpx` flips back), httpx everywhere else (OpenRouter, Resend) and as the fallback — with **HTTP/2 enabled** (`httpx[http2]` → `h2`). See the fetcher and transport bullets: the fingerprint is a bot-detection concern, not a performance choice

- **Email:** Resend HTTP API (`https://api.resend.com/emails`) — same provider as the source platform; no SMTP (D-32)

- **LLM:** **none since v0.66.0.** OpenRouter was the extraction fallback, enrichment and quick-add identity step until prod evidence showed it priced only dead products, wrongly (→ structured-ladder bullet). `tests/test_static_gates.py` fails on any model endpoint in `src/`; re-adding one is a stack decision that needs a measurement first. `OPENROUTER_API_KEY` is no longer read.

- **MCP:** `fastmcp` (mounts on FastAPI)

- **Auth (UI):** IAP header trust — reads `X-Auth-Request-Email` forwarded by the upstream Traefik + auth-middleware ingress (managed via Dokploy); no in-app OIDC client. **Or, with `AUTH_SOURCE=cf-access` (v0.63.0), a verified Cloudflare Access JWT** via `joserfc` (→ Auth bullet)

- **Auth (MCP):** static bearer token (`MCP_BEARER_TOKEN`)

- **Frontend:** server-rendered HTML + vanilla JS + Chart.js (ported from source — no SPA framework)

- **Tests:** pytest, pytest-asyncio

- **Lint/format:** ruff 0.14.4 (config inherited from the source repo). `ruff format` replaces black — do not add black. mypy is **not** carried over yet (the source had it; typing 10,800 ported lines is its own task).

- **CI:** `.github/workflows/ci.yml` — ruff check → ruff format --check → pytest, on every push and PR, against a Postgres 16 service so the integration tests actually run. (`release.yml` only builds the image on a version tag.)

- **Packaging:** poetry

- **Container:** `python:3.12-slim` Docker image; docker-compose with Traefik labels

## Conventions

**The port doctrine is over. Read this before you "restore" anything.**

Phases 1–3 were a byte-equivalent port from the monolith, and the rule then was: don't refactor, don't fix known shortcomings, don't add abstractions — so the result stayed diffable against the source. **That rule expired when the port landed. It no longer governs this repo.**

This is now an ordinary product repo:

- **Refactoring, model changes, and bug fixes are allowed and expected.** Don't ask "does this match the source?" — ask "is this right?"

- **Phase 04.1 was a deliberate, verified, deployed model change** (package data moved to the store link). It is not drift from the port. **Reverting it in the name of "the source repo did it differently" would be a regression** — and there is nothing left to compare against anyway: the source repo is GONE (the platform was wound down; Phase 5 closed as obsolete 2026-07-26, verified by absence). "The source did X" is not an argument that can be checked, let alone won.

**Language — one track (decided 2026-07-14):**

- **Swedish** for everything a user sees: UI strings, toasts, user-facing `HTTPException(detail=...)`, email, MCP tool output. The page declares `lang="sv"`.

- **English** for everything a developer sees: identifiers, DB columns, JSON keys / wire contracts, logs, docstrings, comments, commit messages, and internal 500-level errors.

- Glossary: produkt · butik · länk · förpackning · mängd · **jfr-pris** (headings; the shelf-label word — value labels stay "kr/st"-style) · bevakning · erbjudande · i lager · Åtgärder.

**Comments:** explain WHY, not WHAT. The non-obvious invariants in `models.py` / `pricing.py` are commented on purpose — don't strip them.

## Architecture

```
src/
├── domain/
│   ├── models.py  service.py  scheduler.py  parser.py  notifier.py  result.py
│   ├── pricing.py   # THE definition of kr/unit — see below. Never write a second one.
│   │                # Also THE reader of the measure a store PRINTS its jfr-pris in
│   │                # (printed_measure) — validation vetoes with it, the links panel
│   │                # labels with it; one number, one answer about its unit.
│   ├── schedule.py  # THE definition of check scheduling (store default → link override,
│   │                # next-check time). Scheduler AND admin API both resolve through it.
│   ├── categories.py # THE ordered category taxonomy (10 grocery sections in walk order) —
│   │                # normalize_category + category_sort_index. UI <select> injected from it.
│   ├── stats.py     # THE definition of the statistics view (index-free cross-product
│   │                # comparison, matched-basket store ranking) — docs in the module
│   ├── link_health.py # THE judgement of a broken link (repeated non-blocked failures
│   │                #  in check_attempts) — feeds the Trasig länk facet + links-panel badge
│   ├── validation.py # THE data-quality judgement (tier regressions + jämförpris
│   │                # contradictions) — feeds Statistik's Datakvalitet + the buy-list mail
│   ├── deals.py     # THE deal query + verdict (billigast vs the product's cheapest other
│   │                # link) AND timing (vs the product's own observed floor) — /deals,
│   │                # MCP find_deals and the buy-list mail all resolve here. `observed_spans`
│   │                # is THE floor/high walk and is public: /watches uses it too (v0.53.0)
│   ├── blad.py      # THE butiksblad offer analysis (pasted Willys offline-offer URL →
│   │                # store's numbers + ranked candidates) — discovery is human by decision
│   ├── quickadd.py  # pure decision logic for quick-add (store match, package parse, product
│   │                # suggest, strip_brand_from_name) — docs/quick-add.md
│   ├── extractors/  (base.py — protocols, the shared hydration-JSON scanner, the shared
│   │                 LD-JSON block regex and format_sek —, jsonld.py, willys_api.py,
│   │                 rusta.py, clasohlson.py, lyko.py, ica.py, jysk.py)
│   ├── protocols/   # structural interfaces (IFetcher, IRateLimiter, IBlockRegistry,
│   │                # ICheckAttemptLog, IEmailService) — keep domain/ free of infra imports
│   ├── tenant.py    # DEFAULT_TENANT_ID, the single-tenant constant
├── api/
│   ├── app.py     # FastAPI factory + lifespan starts scheduler
│   ├── auth.py    # IAP header trust (X-Auth-Request-Email) + single-email gate
│   ├── admin.py   # portal + REST API (served at root, not /admin)
│   ├── schemas.py
│   ├── templates/shell.html + shell.css  # the served shell (document, sidebar, header,
│   │                          # mobile chrome) and the design-system CSS (v0.51.0 — were
│   │                          # Python string literals in admin.py)
│   └── templates/admin.html   # 3 fragments split on <!-- SECTION_SEPARATOR --> — see Gotchas.
│   │                          # The UI is ONE served page with SIX hash-routed pages, in two
│   │                          # sidebar groups (v0.47.0): Veckan = #/kopa, #/bevakningar,
│   │                          # #/utveckling; Underhåll = #/produkter, #/luckor, #/loggar.
│   │                          # renderPage() in fragment 3 owns visibility + active nav state;
│   │                          # #/erbjudanden and #/statistik still resolve (aliases).
│   │                          # "Admin" is gone from all user-visible text (v0.7.1).
│   │                          # Deal grouping + product filtering are CLIENT-side (v0.34.0) —
│   │                          # see the portal-UI bullet; the endpoints kept their contracts.
├── mcp_server/
│   ├── server.py  # FastMCP, 4 tools
│   └── auth.py    # the bearer middleware (hmac.compare_digest, fail-closed)
└── infra/
    ├── providers.py # process-wide singletons (get_fetcher/get_rate_limiter/
    │               # get_block_registry/get_check_log)
    ├── fetcher.py  # curl_cffi by default, httpx behind FETCHER_TRANSPORT and as the
    │               # fallback; WAF walls fail fast + blocked flag (see bullet)
    ├── rate_limiter.py # THE per-store politeness ledger (reserve-a-slot + jitter), shared by scheduler + interactive
    ├── store_block.py # THE per-store circuit breaker (escalating cooldown), shared by scheduler + interactive
    ├── check_log.py # THE writer of check_attempts — own transaction, never raises (see bullet)
    ├── logbuffer.py # in-memory ring buffer of the app's own log records; portal Loggar page reads it via GET /logs (v0.18.0)
    ├── email.py    # Resend HTTP client
    └── db.py       # async session factory
tests/
└── conftest.py (real-Postgres fixtures) + test_static_gates.py (AST invariant gates) + more
alembic/versions/   # 0001_initial (rewritten in place during 04.1 — see Gotchas) → 0002_store_label →
                    # 0003_seed_kronans → 0004_seed_apohem → 0005_store_schedule → 0006_normalize_categories →
                    # 0007_seed_rusta → 0008_seed_clasohlson → 0009_check_attempts →
                    # 0010_seed_lyko → 0011_package_precision → 0012_seed_jysk
```

**Data model (7 tables):** `stores`, `products`, `product_stores`, `price_points`, `watches`, `check_attempts` (v0.35.0 — → check-attempts bullet), `summary_sends` (v0.62.0 — the buy-list mail's durable receipt, → buy-list-email bullet). Tenant-scoped tables carry a `tenant_id` UUID column.

- **`uq_product_store(product_id, store_id)` is DROPPED; `store_url` is globally unique** (`uq_product_stores_store_url`). One product can have several links at the same store (different pack sizes). The URL is the link's natural key. **An AST gate in `tests/test_static_gates.py` fails any query in `src/` that resolves a link by the `(product_id, store_id)` pair** — if that test fires, your query is built on the pre-04.1 model, not on a lint nit.

**Export/import is THE backup, and a backup is judged by what it leaves out (v0.26.0):** `GET /export` writes **every product in the tenant**. It used to inner-join *active watches*, so a tracker with products but no watch exported `products: []` under a **200 OK** — 15 prod products became a 205-byte file. A watch is one optional attribute of a product, never the condition for saving it. Format **1.1** carries `store_label` and keys each history row on `store_url` (the link's natural key — product+slug cannot address one of several links at one store, the pre-04.1 shape the AST gate forbids). `POST /import` restores products, links, watches **and price history**, deduped on `(link, checked_at)` so re-importing is idempotent; history used to be exported and then silently dropped. A 1.0 file still imports — its unaddressable history rows are skipped with a warning, never guessed at. The portal's button passes `history_days=0` = ALL history (the 30-day default silently truncated). `tests/test_export_import.py` is a real-Postgres round trip — export, wipe, import, compare — and these endpoints had **no test at all** before it, which is exactly how they rotted.

### Auth

**Auth:** The portal + API are served at the app root (`price.<domain>/`); the `/admin` prefix was dropped (old `/admin` URLs 308-redirect to `/`) — D-28. The UI trusts the `X-Auth-Request-Email` header forwarded by the upstream ingress and validates it against `ALLOWED_ENTRA_EMAIL` (which must match the Entra **UPN**, not necessarily the gmail address).

**Behind Cloudflare Access the app verifies a JWT instead of trusting a header (v0.63.0, `AUTH_SOURCE=cf-access`).** home-server moves this app to a Kubernetes cluster behind Cloudflare Access (its STATEFUL-APPS-DESIGN D6), where no oauth2-proxy exists. `api/cf_access.py` verifies `Cf-Access-Jwt-Assertion`: RS256 only, a key from `https://<CF_ACCESS_TEAM_DOMAIN>/cdn-cgi/access/certs` (cached an hour, an unknown kid refetches at most every 30 s, a failed refresh keeps the old keys, nothing cached ⇒ 403), `aud` containing `CF_ACCESS_AUD`, `iss` the team, exp/nbf with 60 s leeway. **Exactly one source is believed**: in `cf-access` mode `X-Auth-Request-Email` is ignored, and in the default mode an Access token names nobody. Three measured facts shaped it (home-server lab, 2026-09-30): a client-supplied `Cf-Access-Authenticated-User-Email` does not reach the origin, a forged assertion is replaced by Cloudflare's own — and **a service-token request carries a VALID assertion with no `email`** (it has `common_name`), so such a caller is refused rather than made a reader. A real lab assertion was verified against Cloudflare's live keys, and the same token was refused with a wrong AUD, a wrong team and a tampered signature. `joserfc` was already in the lock via authlib; it is declared directly so the auth path does not rest on fastmcp's dependency tree. `tests/test_cf_access.py` holds the cases; three of four canaries turned it red, and the fourth (HS256 admitted in `ALGORITHMS`) stayed green because joserfc refuses an RSA key as an HMAC secret on its own, so that outcome has two locks. The refusal reason is logged, never returned.

**Header trust has an optional second factor since v0.51.0:** when `INGRESS_SHARED_SECRET` is set, every request must also carry it in `X-Ingress-Auth` (compared constant-time BEFORE the email header is read) — closing the hole where anything that can reach the container port directly (a neighbour on the docker network; the reachability mcp_server/auth.py documents) could claim to be the admin. Unset = old behavior; enabling is a two-sided move (env here, `customRequestHeaders` on the Traefik router in home-server).

**Two roles since v0.29.0 — admin writes, everyone else reads.** `ALLOWED_ENTRA_EMAIL` is now the **admin**, not the only allowed user: anyone the upstream Entra gate authenticates is a **reader**. THE gate is `auth.require_admin_for_writes`, mounted **once** as the `APIRouter(dependencies=[...])` in `admin.py` — it authenticates every caller and refuses any method outside `SAFE_METHODS` (`GET`/`HEAD`/`OPTIONS`) from a non-admin. **Deny-by-default and keyed on the HTTP method, not on a per-route marker**, because a decorator you have to remember on 14 write endpoints is a hole waiting for the 15th; `test_static_gates.py` (AUTHZ-01/02) fails the build if the dependency is dropped or a route is registered off a second router. Every read is a GET here, so no exception list exists — and that is by decision: `/export` (a full DB dump) and `/logs` are readable by every reader. `get_principal` is the ONE identity point (`require_auth` resolves through it); a test that overrides only `require_auth` leaves the write gate live and 403s every POST. The UI mirrors the role rather than defining it: `render_admin` stamps `<body class="role-admin|role-reader">`, CSS hides `.admin-only`, and the JS builds row actions off `IS_ADMIN` — the server is still the gate, this only stops offering buttons that can only fail. Before the split, a household member who logged in correctly got a **403 from this app**, which the ingress then dressed as a white page reading "Found" (→ Gotcha 7). Entra ID enforcement itself is **live in production since 2026-07-09**: oauth2-proxy v7.15.2 + Traefik `forwardAuth` (`entra-auth@file`), email claim = `preferred_username`/UPN, managed in the home-server repo — not in this app. The MCP server is served at `price.<domain>/mcp/` (no `mcp.` subdomain — a path-scoped, un-gated Traefik router with explicit priority bypasses the Entra gate for `/mcp` only) and is protected by its own static bearer token; without `MCP_BEARER_TOKEN` the endpoint fails closed (503). See EXTRACTION.md §6 for background, though its original in-app-OIDC description was superseded by this IAP header-trust model, and D-18's `mcp.<domain>` subdomain plan was superseded by the `/mcp` path (D-29 in .planning/STATE.md).

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

**Then tell Magnus the tag is built and needs the deploy bump** — and say which version is pending against what is pinned *right now*, read with the command in the Deploy state paragraph, never recalled. Deploying is a change to the pinned digest and its tag comment in the home-server repo's `clusters/base/price-tracker/app.yaml`, which **Magnus makes himself in the GitHub web UI** (GitOps split: this repo builds, the platform repo is the deployment truth). **That repo is not this repo — do not edit it from here**, and the release is not live until that bump lands.

Docs-only, planning-only, or test-only commits do **not** earn a tag; they ride along with the next real one.

## Prod logs & observability

Prod runs on the home-server platform's Kubernetes cluster `prod` (Deployment `price-tracker`, StatefulSet `price-tracker-postgres`, namespace `price-tracker`); the Dokploy VM it ran on was retired 2026-10-02, and nothing runs locally. Corrected 2026-10-06: this section described Dokploy until then.

**Field logs.** The pod's stdout is collected into the platform's `logs-infra` store (14 d), readable from the dev seat with `telemetry-query` (its skill has the rules: `check` first, always a `_time:` filter, exit 1 is unknown, not empty):

```bash
telemetry-query check
telemetry-query logs logs-infra '_time:1h kubernetes.container_name:=price-tracker level:WARNING' --limit 50
telemetry-query logs logs-infra '_time:24h kubernetes.container_name:=price-tracker logger:="uvicorn.access" -_msg:~"/health"'
```

Every console line is one JSON object (`infra/logbuffer.py`, `JsonLineFormatter`, since 2026-10-06), and the collector turns its keys into fields: `level` (Python's level name), `logger`, the message as `_msg`, a traceback in `exc`, and `time` as the row's timestamp. uvicorn's own loggers are routed through the same handler, so the access log is `logger:="uvicorn.access"`. Before this, every line was text and the store and Grafana read its level as "unknown". Still text, so still level-less: the `alembic upgrade head` that runs before uvicorn in the container, and Python `warnings`.

Live, from the ops LXC (the dev seat cannot reach the cluster's API): `sudo kubectl --kubeconfig /etc/talos-prod/kubeconfig -n price-tracker logs deploy/price-tracker --since=10m`.

The image is `python:3.12-slim` — **no `curl`**; for in-container HTTP probing use `python -c` with `httpx` (already a dep).

## Gotchas

Traps that already cost time. Each one **passes silently** — that's why they're here.

1. **`ruff` exists — and `B008`/`S101` are ignored on purpose.** ruff 0.14.4 is a declared dev-dependency, with the config inherited from the source repo (`ai-agent-platform/services/agent`); Phase 1 rewrote `pyproject.toml` from scratch and dropped it, which is why the planning docs kept assuming a ruff that wasn't there. Run `poetry run ruff check src tests` and `poetry run ruff format src tests` (`ruff format` is black-compatible and **replaces** black — do not add black). **Never "fix" `B008`:** it is FastAPI's `Depends()`-in-default-argument pattern, and hoisting the call out of the default argument breaks dependency injection — and with it the auth guard. `S101` is ignored because `assert` is how tests assert. **The test suite is the truth, not the linter:** if an auto-fix turns a test red, the fix was wrong — revert it (`UP017` and `UP032` rewrite real logic, not just style).

2. **`src/api/templates/admin.html` has no `<script>` tag.** It is three fragments split on `<!-- SECTION_SEPARATOR -->`, so a gate that extracts the `<script>` body gets **zero bytes** and `node --check` on empty input **exits 0** — it passes no matter what you wrote. The working incantation, and how to actually render and click the page, are in the `portal-ui` skill. **"The file parses" is not "the page renders."**

3. **Alembic: `0001_initial.py` was rewritten in place** during Phase 04.1. Alembic does not checksum migration bodies, so a database stamped `0001_initial` from BEFORE that rewrite compares equal to head, runs **nothing**, and exits **0** — the app then boots quietly against the **old** schema. **`alembic current` will not reveal this** — it prints the same revision either way. **Only `alembic check` does.** That trap was resolved by the 04.1 volume drop (`README.md` § "Schema reset (Phase 04.1)"). **Since v0.6.0 the chain is normal again:** `0002_store_label` is a real additive revision on top of `0001_initial`, and schema changes from here on are ordinary new revisions applied by `alembic upgrade head` — never rewrite a shipped revision in place again.

4. **RESOLVED (v0.51.0) — the two `get_price_history` are one.** THE query and row shape live in `domain.service.price_history_rows`; the service method wraps it with the MCP error contract (failure → empty list) and the `/products/{id}/prices` route with the HTTP one (400/500). Before the collapse the copies had drifted twice (package data missing on the wire; a truthiness test rendering a legitimate 0.00 as null). The general lesson stands: a second hand-written copy of a domain query WILL drift — `latest_point_per_link` in `domain/models.py` closed the same pattern for the "latest point per link" subquery (five copies, two semantics).

5. **A handler on the app loggers is what silences `docker logs` — `logbuffer.install()` must keep calling `ensure_console_logging()` first (v0.28.1).** Nothing configures the ROOT logger (uvicorn only configures its own `uvicorn*` loggers), so root has no handlers and Python falls back to `logging.lastResort`, which prints WARNING+ to stderr. But `Logger.callHandlers` uses `lastResort` **only when it finds zero handlers anywhere up the chain** — so the instant v0.18.0's ring buffer attached to `domain`/`infra`/`api`, a handler WAS found, the fallback was skipped, and every app record terminated in memory. **Nothing raised and the portal's Loggar page kept working**, which is exactly why it went unnoticed: seven days of prod logs contained no app line at all, only uvicorn access logs. `alembic current`-style checks won't show it either — the only test is to look for a non-uvicorn line in `docker logs`. Note the ordering trap: adding a handler to any app logger *before* root has one re-creates this. Covered by `TestConsoleLoggingSurvivesTheRingBuffer` in `tests/test_logbuffer.py`.

6. **`alembic/env.py` must keep `disable_existing_loggers=False`.** `fileConfig()` defaults that flag to **True**, which sets `disabled = True` on every logger that already exists — including the app's own `domain`/`infra`/`api`. Nothing raises: the loggers simply stop emitting, so app logs and the portal's Loggar buffer go quiet. Invisible when alembic owns the process (the deploy runs it standalone), fatal in any process that ran the app first. It surfaced as two `test_logbuffer` failures the moment a DB-backed test file happened to sort before it — and the tests passed in isolation, which is what makes it a gotcha rather than a bug report.

7. **The ingress replaces the BODY of every 401–403 this app returns, and a 403 keeps its status.** The `entra-auth-errors` Traefik middleware (home-server, `bootstrap-dokploy.sh`) catches `401-403` from the *whole chain* — the forwardAuth's own 401 **and** any 403 the app itself produced — and serves the body of `oauth2-proxy`'s `/oauth2/sign_in?rd=…` instead. Its `statusRewrites` maps **only 401→302**, so a 403 comes back as **403 with a redirect's body**: `<a href="…">Found</a>.` The browser does not follow a 403, so it renders a white page with the word **"Found"** on it — which is what a non-admin user saw before v0.29.0, and reads like a broken app rather than "access denied". Consequences that outlive that fix: **never put user-facing meaning in a 403 detail** — a JSON API 403 reaches the frontend as that HTML, which is why `apiPost/apiPut/apiDelete` in `admin.html` substitute their own Swedish message on 403 instead of echoing the response body. Narrowing the middleware to 401 only would fix it at the source, but that is the **home-server** repo, not this one.

8. **An endpoint with no test rots silently.** `/export` + `/import` had **zero** tests and drifted until the export backed up nothing while still returning 200 OK. Both are now covered by a real-Postgres round trip (`tests/test_export_import.py`). Before trusting any endpoint that no test names, check what it actually returns against real data — a green suite says nothing about code it never calls.

## Release index (leaves in the next commit)

**Since 04.1 — what changed, and where the rule now lives.** This is an index, not the documentation: every durable rule sits in **Architecture** or **Gotchas** below, next to the code it governs. Do not grow this list into a changelog — `git log` is the changelog.

- **v0.20–v0.22:** `category` became a fixed ordered enum (→ category bullet); quick-add strips the brand out of the suggested name (→ Quick-add).

- **v0.14–v0.25.0 — anti-WAF:** shared politeness ledger with jitter, honest failure on a bot wall, scheduler auto-pause during adds, per-store circuit breaker (→ rate-limiter, fetcher and scheduler bullets). **ICA sits behind AWS WAF and its HTTP 202 is a JS challenge** — `curl_cffi` shipped in v0.30.0 and is now the measured answer (→ transport bullet); the headless-browser lever was proven to work on 2026-07-29 and **decided against on 2026-08-13**, because the walls it would open stopped happening (→ waf-token bullet).

- **v0.28.0 — the breaker became everyone's, and HTTP/2:** the circuit breaker moved out of the scheduler into `infra/store_block.py` and now gates every caller, its cooldown escalates, and the fetcher speaks h2 (→ store-block, fetcher and Willys-API bullets).

- **v0.28.2 — breaker callers agree, and the Willys API speaks Chrome too:** the scheduler now resets breaker strikes on the same "reached the store" predicate as the interactive paths, and the Willys REST call rides the shared fetcher client and raises `StoreBlockedError` on a wall instead of degrading to "product missing" (→ store-block and Willys-API bullets).

- **v0.30.0 — curl_cffi is the fetch transport:** Chrome's TLS and h2 fingerprint, behind an env switch so the benefit can actually be measured rather than assumed (→ transport bullet).

- **v0.30.1 — the XHR repaint stopped wearing navigation headers:** `fetch_json` removes `sec-fetch-user`/`upgrade-insecure-requests` and claims the product page as Referer, and the bot-wall log line names the transport so the A/B is a grep (→ Willys-API and transport bullets).

- **v0.31.0 — Rusta, via a new store-HTML ladder tier:** the extraction ladder gained a page-state tier between store-API and JSON-LD, because Rusta's JSON-LD hides the ordinarie at a rea and the printed jämförpris (→ Rusta bullet + Quick-add).

- **v0.32.0 — Clas Ohlson, second store-HTML extractor:** same JSON-LD blind spot as Rusta, but the riches live in BEM price markup instead of hydration state; the extractor composes the shared JsonLdExtractor and refuses on any disagreement (→ Clas Ohlson bullet).

- **v0.32.1 — the LLM can no longer invent an inverted offer:** an offer is what you PAY, lower than `price_sek` by definition, but a model reading "2 för 130 kr" reported the bundle price as the offer (prod: price 69.46, "offer" 130.00 — and `effective_price = coalesce(offer, price)` re-priced the link to 130 in every ranking). `offer >= price` was refused wholesale (price + type + details) in BOTH the enrichment merge and `_extract_with_model` — both paths are gone since v0.66.0, and every structured extractor keeps the same refusal.

- **v0.66.0 — no language model prices anything, and a product the store says is gone ends the check.** Found 2026-10-19 on Fel & luckor: two tier regressions were two links whose product no longer existed (Willys API 404 for the code; a JYSK article gone from its ProductGroup), and the LLM fallback had priced both for weeks — 8 points, all wrong, all reported "ok". The fallback, the JSON-LD enrichment pass and quick-add's LLM identity step are removed, and `ProductGoneError` ends the ladder with a reason that reaches Fel & luckor (→ structured-ladder bullet). The 8 points, the two dead links and the NORA product were deleted from prod the same day (the deleted rows are saved as JSON in `~/price-tracker-backups/2026-10-19-llm-cleanup.json` on the dev box, outside the repo).

- **v0.64.0 — one scheduler per database, and none where the installation says so.** `SCHEDULER_ENABLED` + a Postgres advisory lock (→ Scheduler-leadership bullet). Found while moving to Kubernetes: a lab copy of production's data would have checked every store beside production from the same IP.

- **v0.63.0 — behind Cloudflare Access the identity is a verified JWT, not a header.** `AUTH_SOURCE=cf-access` + `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD`; the default stays Dokploy's header, so this release changes nothing where it is deployed today (→ Auth bullet). Also carries the OpenRouter `provider` block (cheapest ZDR endpoint at fp8 or better), committed 2026-09-23 and unreleased until now.

- **v0.62.1 — priset i grafen var det man INTE betalade.** Reported from the portal 2026-09-18: a product on campaign at 90,00 kr drew a flat 129,00 in the history modal's "Absolut pris" mode — ringed as an erbjudande, two centimetres under a summary strip reading 0,68 kr/st (= 90/132). kr/enhet has always been computed from coalesce(offer, price) server-side; absolute mode read `price_sek`, the ordinarie, so ONE chart answered in two currencies of truth. The fix is a wire field the domain owns (`effective_price_sek`), not a client-side coalesce (→ history-modal and links-panel bullets). **The same disagreement was found in the links panel** while verifying the first one — same ordinarie beside the same offer-derived kr/enhet, on a table with no erbjudande column at all — and both are fixed here; the JS `effectivePrice()` twin is gone with them.

- **v0.62.0 — inköpslistan minns i databasen att den redan gått ut.** Reported the same evening the v0.61.0 deploy landed: bumping the pin produced a second buy-list mail. The dedup was in-memory, so every restart forgot it — a known trade-off from when the mail was Monday-only, whose premise v0.59.0 (mån+fre) and the deploy cadence had quietly voided. `summary_sends` (alembic 0014) keys on the local check day as its PRIMARY KEY, so the duplicate is impossible by construction, and the row doubles as the answer to "did Monday's mail go out, and what did it carry" (→ buy-list-email bullet).

- **v0.61.0 — butikens eget veckoblad svarar, och kopplingen är exakt.** v0.60.0's `presentationMode` hint was a correlation and it missed the very row that prompted it (the rapsolja is DEFAULT). The answer was already in the data it recorded: a promotion's `retailerPromotionId` first segment IS the id of the offer on the butik's own erbjudandesida, so leaflet membership is an exact join. `domain/ica_leaflet.py` (parse + join + per-butik URL config) and `infra/ica_leaflet.py` (one fetch per butik per local day, on the shared ledger and breaker keyed `host:www.ica.se`) write the verdict into `raw_data` AT CHECK TIME, and `result.channel_note` composes the ONE sentence the mail, the portal row and MCP all print (→ leaflet bullet). Verified against the two real pages: 187 + 48 offers parsed, 6/6 joins reproduced.

- **v0.60.0 — ett erbjudande i e-handeln är inte alltid ett erbjudande i hyllan.** Reported from the shop floor 2026-09-04: a buy-list row did not exist at the till and the staff said it was online only — the first time in the product's life that an ICA e-handel price has failed to match the butik. The state's promotion carries flags we had been throwing away (`presentationMode`, `retailerPromotionId`), now recorded, with MUTE_STYLE surfaced as "kan gälla endast e-handeln" in the mail, the portal row and MCP — marked, never hidden, because the price is real and it is the till that may disagree. **The flag is known-incomplete and would NOT have caught the row that prompted it** (it is DEFAULT); the exact signal is the promotion id's leaflet join, recorded in the same release and not yet acted on (→ ICA and online-only bullets).

- **v0.59.0 — mailet slutade lita på ett schema bara två butiker följde.** Rusta's rea was observed on a Saturday, was over by the Sunday, and led Monday's buy list anyway — flagged "sett lördag", honest and dead. Measured on prod 2026-08-27: at Monday 12:00 a weekday-mode link's observation was 3–5 h old, an interval-mode link's averaged 12–46 h and was over 48 h on **20 of 56 link-Mondays (36 %)** — 72 h and 7 days are coprime, so the check walks around the week. Two halves: **every store checks mån+fre** (alembic 0013), and **the mail's days are now DERIVED from the schedule** (`schedule.summary_weekdays` + `summary_slot`) instead of hardcoded to Monday beside it (→ schedule and buy-list-email bullets). ICA doubles to 136 checks/week; the measurement to watch is `check_attempts.outcome = 'blocked'`.

- **v0.58.2 — Willys jämförprisenhet låg i ett fält vi aldrig läste.** Reported from prod on the release before it: a Willys row in Fel & luckor claiming a 73 % error with both numbers correct, drawn as "97,78 kr/st" for a figure printed in kr/kg. Two defects, one row — the extractor parsed the measure out of `comparePrice` (which never carries it) instead of `comparePriceUnit` (which always does), so **every Willys point ever written carried no measure** and the validator judged them all unvetoed; and Fel & luckor labelled the store's figure with the PRODUCT's unit, the one rendering v0.58.1's gate did not cover (→ Willys-API and faults-page bullets).

- **v0.58.1 — måttet på butikens jfr-pris, och mängden som överlevde ett enhetsbyte.** Two silent-in-different-ways display defects, both surfaced by one question: how do you track a product sold in exactly ONE size (a Knorr middagskit 270 g), where `st` × 1 is the right comparison unit and the store's own kr/kg is pure noise? The modelling answer needed no code — but (1) "Butiken anger" printed the store's figure with no measure at all, the one cell in the portal that did (→ pricing/printed-measure bullet), and (2) quick-add's mängd survived a change of canonical unit and then failed to submit with the browser's own cryptic step message (→ pkgRekeyUnit bullet).

- **v0.58.0 — Prisutveckling fick vägen in i drill-in:en, och en sida slutade hämta om sig själv.** The product name in "Per produkt" opens the price-history modal, the same gesture and the same `#/<page>?produkt=<id>` address Att köpa uses (→ history-modal bullet, which also carries the new both-halves gate over every entry point). The refetch fix rides with it because the click is what exposed it: `renderPage` ran its per-page loads on every hashchange, and the drill-in changes the hash without leaving the page.

- **v0.57.0 — JYSK, femte store-HTML-extractorn, och den första butiken vars JSON-LD döljer själva PRODUKTEN.** Two independent silent failures, not one: a multi-variant product publishes `ProductGroup` (the generic Product walk never descends into `hasVariant`, so the page fell to the LLM), and the price it publishes is what one piece costs TODAY, never the ordinarie (→ JYSK bullet). The LD-JSON block regex, which had grown two copies, moved to `extractors/base.py` as `LDJSON_BLOCK_RE`.

- **v0.56.2 — tre döda lägen, och den sista oöversatta maskintexten.** The tier pair `ica_page → jsonld` became admin-only (→ faults-page bullet), "URL:en bevakas redan" leads to the product's links panel, and Loggar states that it holds EVENTS while state lives on Fel & luckor. Plus a focus bug v0.56.1 shipped, visible only in the reader role (→ a11y bullet).

- **v0.56.1 — dialogerna säger vad de är, och toasten kapar inte sin efterträdare.** From a UX review (2026-08-14) that separated bugs from taste: `showToast` never cleared its timer, so the second toast inherited the FIRST one's deadline — and 10 dialogs were anonymous divs with 46 labels that named nothing (→ a11y bullet).

- **v0.56.0 — drill-in:en fick en adress, inte en egen sida.** `#/<page>?produkt=<id>`, back stänger den, Escape stänger de läsbara dialogerna. The routed page the design review wanted was refused on a measurement about scroll, not on taste (→ history-modal bullet).

- **v0.55.0 — Pristillfällen: den enda dom appen fäller om det förflutna.** `GET /products/{id}/offers` + a table in the history modal — every campaign in the window with the verdict it earned AT THE TIME (→ occasions bullet). The judgement already existed inside `stats.py`, which walked each offer against `best_excluding` and then kept only four counters per butik; now the walk lives in `product_offer_occasions` and the comparison in **`was_cheapest`**, shared with that counter so the rows and the number summarising them cannot disagree. **Inactive links are INCLUDED here** (decided 2026-08-13) — the rest of stats.py filters them because "billigast" must not name an unreachable shelf, but a campaign that really ran really ran; the row is flagged "borttagen länk" rather than hidden. The alternative carries **when it was last seen**, because carry-forward over weekly checks can be six days old. The observation table folded into a `<details>` under it.

- **v0.54.0 — prishistorik-modalen svarar innan grafen.** A visual pass adopting the design review's drill-in *look* without its data model (→ history-modal bullet): a summary strip under the title (billigast nu · förändring · observationer · a span bar for the modal's own window), campaign points drawn as **hollow rings** (a dip alone never said it was a kampanj), a dashed mean line replacing the "billigast tillgänglig" line that traced the minimum across links — by construction whichever coloured line sat lowest, so it added nothing while being the darkest mark on the canvas — and the legend moved out of Chart.js into the header beside the mode switch, still click-to-hide. **The proposal's smooth per-butik curves were refused:** its mock generates dense synthetic series, while real data is ~12 points per link at three different cadences, so the stepped line with visible dots stays — a dot is measured, the run after it is carried, and a smooth line between two Mondays draws prices that were never observed. Also the money bug this pass found: the history table was the one place in the portal that concatenated raw numbers instead of `kr()`/`unitKr()` and printed "79.9 kr".

- **v0.53.3 — 0,024 kg gick inte att spara, 24 g gick bra: `step` sattes två gånger.** v0.45.0 widened `package_quantity` to Numeric(12, 4), dropped `_QUANTUM` to 0.0001 and put `step="any"` on the three package-amount inputs — but left the line in `pkgFieldsChanged` that assigns `amountEl.step = '0.01'`, and it runs on every dialog open (via `pkgSetEntryUnit`). So the attribute was right in the file and wrong in the browser: the field reported `stepMismatch` ("the two nearest valid values are 0.02 and 0.03") and the form refused to submit, while the SAME amount entered as 24 g went through because 24 is a multiple of 0,01 — which is why it read as an arbitrary GUI lock rather than a validation rule. **No server code was involved and no runtime test could see it** (nothing reaches the API), so the fix ships with a static gate over BOTH places — markup attribute and JS assignment — verified to fail on the old line. `st` keeps `step='1'`: a count is whole.

- **v0.53.2 — måndagsmailet slutade jämföra butiker med varandra.** The buy list's third column was "0,45 kr/kg billigare än ICA Maxi 1 kg" and is now the **span bar** (→ weekly-email bullet): being cheaper than the next link says nothing about whether either price is good, so the sentence dressed an unknown as a win. The bar is built from tables and percentage widths because Outlook has no `position`. Same change the portal made in v0.53.1, now on the surface that is actually read standing in the aisle. Also: "har varit X **kr** billigare" became "har varit X **kr/kg** billigare" in both — the difference of two kr/enhet figures is not kronor off the package.

- **v0.53.1 — förenkling, second pass: four things the rows stopped saying twice.** (1) **A BEST row's verdict sentence is gone** — "31 % billigare per kg än Willys 500 g" was the loudest line on every row of a list where being best is the entry condition; the margin still ORDERS the list (→ deals bullet), the heading still says what the list is, and the span bar answers the question the reader arrives with. WORSE and UNKNOWN keep their sentence — nothing else on those rows carries it. The **butik moved into the meta line** so it survives the sentence it used to close: that link is the way OUT to the store. (2) **The villkor (`offer_details`) moved from the row's bottom to beside the product name** — it is a CONDITION on the price printed to its right, and the bottom of the row is the last place a swept row is read. It wraps; it is never shortened to a badge. (3) **The product row's badge moved to the left, beside the name** (→ portal-UI bullet). (4) **"Luckor" left Produkter's chips and Fel & luckor's counters stopped linking there** (→ faults-page bullet).

- **v0.53.0 — a bevakning that cannot fire looks exactly like one that is waiting.** From a design review whose theme was simplification (handoff kept out of the repo — → design-handoff bullet): Bevakningar is grouped by what the reader can DO about each row, and the third group is the one that did not exist as a concept before — **"Larmar inte som det är satt"**, a unit-price target below everything the product has been observed at inside the floor window. It is silent by definition, so nothing else in the interface would ever bring it up. `/watches` therefore carries the product's observed span, and it is **`domain/deals.observed_spans` — the buy list's own floor query, made public rather than copied** (→ watches-page bullet). Prisutveckling's "Lägst–högst" cell became the same span bar Att köpa draws, keeping the two numbers as its end labels. Both refuse to draw on one observation or a price that has not moved, and the watch bar refuses an **absolute** target outright: `target_price_sek` is a package price and the span is kr/enhet.

- **v0.52.0 — "Erbjudanden utanför nätet": the blad lookup and the manual note became ONE card.** They always did the same job — prices the tracker cannot fetch itself — but lived apart: the analysis answered on screen and the answer had to be carried by hand into a collapsed `<details>` three clicks inside the link dialog, which is a place nobody finds (reported: "jag hittar den inte"). Now two tabs on Att köpa. **Notera pris själv** has its own product/link pickers, so a Facebook förbokning needs no detour through Produkter → row → Redigera → scroll; **the bridge** gives every blad candidate a "Notera på \<butik\> \<förpackning\>" button that switches tab and prefills price, ordinarie and terms from the store's own numbers — and deliberately does NOT save: deciding that the offer IS this product stays the human's call. **The button only ever offers links at the offer's OWN store** (`notable_links`, new on the candidate): a Willys blad price written onto an ICA link forges the observation, and a manual point is indistinguishable from a fetched one once written (no path deletes a single point) — no such link says so instead of guessing. `postManualPrice` is now ONE function both entry points share (the Gotcha-4 shape, in JS). The URL field: Enter analyses (it is not in a `<form>`, so paste+Enter was a silent no-op), FastAPI's `{"detail": …}` is unwrapped to Swedish, and the two URLs a reader reaches for first — the listing page (Akamai sensor) and the iPaper viewer (images, no codes) — get targeted answers without spending a ledger slot.

- **v0.51.0 — granskningens skörd: refactors, testluckor och opt-in-hemligheten.** A deep audit (2026-08-05) drove three releases. This one: the shell/design-CSS moved out of admin.py into `templates/shell.html`/`shell.css`; Gotcha 4 closed (`price_history_rows` — one query, two error contracts) and `latest_point_per_link` in models.py replaced five hand-written latest-point subqueries (row_number semantics, deterministic on ties); price helpers (`effective_price`/`rounded_unit_price`/`as_float`) consolidated into pricing.py; dead code deleted (`record_price`, `get_stores`, aiosqlite); the client no longer echoes the tenant UUID (optional in ProductCreate/watches, `require_default_tenant` is the ONE guard); **`INGRESS_SHARED_SECRET`** (opt-in, → Auth bullet) closes the direct-container-access hole in header trust; `/blad/analyze` got endpoint tests, two static gates that could not fail were repaired, `EXPECTED_SOURCE` is gated against the extractor registries, and `/stats` + `/scheduler/status` wire shapes are pinned. Chart.js is `defer`; loadDeals/loadStatistics carry request-sequence tokens (last RESPONSE no longer wins).

- **v0.50.0 — en deal bedöms mot hyllor som finns.** `in_stock` rides DealRow/DealResponse (out-of-stock links excluded from `best_alt`, sold-out deals MARKED everywhere, never hidden); `link_health`'s broken links likewise excluded from `best_alt` (a dead February URL must not veto an August campaign — the floor still counts both); the floor requires ≥2 observations (one data point may not claim "bra läge"); the span bar rides the DEAL row (`highest_unit_price_sek`, same 84-day walk as the floor — the bar and `timing` can no longer contradict each other, and `/stats` is no longer needed for it); a manual floor is flagged `floor_is_manual` and the weekly email KEEPS such poor rows (a one-off förbokning must not silence twelve weeks of campaigns); MCP `find_deals` renders verdict/margin/timing/villkor/lager and lost its top-20 cap; `/import` enforces the invariants every other write path has (offer inversion, quantity bounds, no unit overwrite, 20 MB cap); a manual price cannot be future-dated; stats.py filters `is_active`; Willys keeps its jämförpris measure (`comparison_unit`) and parses group separators.

- **v0.49.1 — granskningens P0-buggar.** ICA:s `_MULTI_BUY_RE` kräver valutamarkör ("3 för 2" parsades som 0,67 kr och blev golv i 84 dagar) + a ≥20 %-of-ordinarie sanity floor; the weekly email renders `offer_details` (the promise v0.41.2 made); an empty Monday no longer stamps the summary as sent (a walled morning re-evaluates all day); the price-history route's truthiness test no longer nulls a 0.00; three silent portal errors fixed (unguarded `loadStores` blanked the whole app on one 500, `loadExport` saved a 500 body AS the backup, the span bar claimed "en observation" when `/stats` failed).

- **v0.49.0 — a price a human saw is still an observation: manuell prisnotering.** `POST /product-stores/{id}/prices` + a collapsed section in the link dialog (→ manual-price bullet). ICA Björksätra advertises förbokning campaigns on Facebook (32-p toalettpapper 109 kr against a 162 kr shelf = 3,41 kr/st, 25 % under the cheapest tracked link) and that source is as unscrapable as the butiksblad listing — so discovery stays human while the RESULT becomes an ordinary price point on the link that already exists, marked `source: "manual"`. It feeds v0.48.0's floor, which is the whole point.

- **v0.48.0 — cheapest today is not the same as worth buying: the deal gained a SECOND judgement.** `timing` beside `verdict` (→ deals bullet) — the price against the product's own cheapest OBSERVED kr/enhet, because the verdict only ever compared links to each other AT THIS MOMENT and said nothing about the moment itself. Found in prod: Bryggkaffe "2 för 130 kr" led "Sex saker är billigast just nu" at 144,44 kr/kg while the same coffee had been 110,00 kr/kg at Willys nine days earlier. A poor moment is DEMOTED to its own collapsed section on Att köpa and dropped from the Monday email — never hidden, because it is still the cheapest shelf we know of.

- **v0.47.0 — the portal is "3a": six pages in two groups, and every page answers ONE question.** A UI-only rebuild against a design handoff ("3a") that was delivered as a separate package and is deliberately NOT in this repo — no endpoint, schema or domain change came out of it, so the code below is the whole record. Erbjudanden became **Att köpa**, ranked on the margin over the product's next-best link as a PERCENTAGE inside the verdict sentence (`savings_per_unit_sek / best_alt_unit_price_sek`, never `discount_percent`), with a price-span bar answering the other question (right *moment*, not right *store*) and a korgremsa whose store pills ARE the filter. Statistik became **Prisutveckling** (three key figures, per product, store comparison as bars). **Fel & luckor is the one new page** and the whole point of the redesign (→ faults-page bullet). The sidebar carries live counters and the scheduler line; the global stat row is gone. Phone: dark title bar, four-tab bottom menu with a "Mer" sheet, cards instead of tables. → portal-UI bullet.

- **v0.46.0 — bladerbjudanden: manuell upptäckt, automatisk analys.** `domain/blad.py` + `POST /blad/analyze` + an admin-only box on Erbjudanden: paste a Willys butiksblad offer URL and get the store's own numbers (numeric campaign price, ordinarie, jämförpris with colon decimals and +pant, Willys Plus/max-köp conditions) plus tracked products ranked by token overlap with a per-unit diff when units agree. The listing API was spiked (2026-08-03) and is session-bound behind Akamai Bot Manager's JS sensor — a real-browser problem, so discovery stays human; the per-offer endpoint (`/axfood/rest/v1/offline-product/{storeId}/{code}`) is public and sessionless. Store id via env `WILLYS_OFFLINE_STORE_ID` (default 2211, Willys Sandviken Linggatan). Rides the shared ledger (`host:www.willys.se`) and breaker; nothing is persisted — a blad offer is week-scoped and can never be a link. **Since v0.52.0 the analysis sits beside the manual note in one card and each candidate carries `notable_links`** (the product's links at the offer's own store) so the reader can turn a judged offer into an ordinary price point in one click — the offer itself is still never persisted.

- **v0.45.1 — the portal page is served `Cache-Control: no-cache`:** without the header a browser keeps a heuristically-cached copy of the WHOLE UI across deploys — right after v0.45.0 a cached page silently enforced the old `step="0.01"` validation and read as "the fix didn't work". no-cache (revalidate, not no-store) suffices; the response carries no validator, so revalidation always fetches fresh.

- **v0.45.0 — package amounts got the precision they always needed:** `package_quantity`/`scraped_package_quantity` widened to Numeric(12, 4) (alembic 0011), `pricing._QUANTUM` to 0.0001, and the three package-amount inputs to `step="any"` — Numeric(10, 2) in the kg scale was 10-gram resolution, so a 24 g sachet could only ever be 0.02 kg (the Dippmix error the v0.44.0 validator found could not even be CORRECTED: the portal refused 0.024 and Postgres would have rounded it back anyway).

- **v0.44.0 — the validator: does the tracker still SEE what the stores publish?** `domain/validation.py` (→ validation bullet) alarms on the CLASS of silent miss the jättefranska bug was: tier regressions (a store-extractor degraded to JSON-LD — checks green, campaigns dark) and jämförpris contradictions (printed vs computed kr/enhet). Surfaces as a Datakvalitet section on Statistik and a ride-along block in the Monday email; found a real prod error on its first survey.

- **v0.43.0 — ICA, fourth store-HTML extractor:** ICA's JSON-LD never carries a campaign or a jämförpris — both live only in the `__QUERY_INITIAL_STATE__` hydration state, so every ICA deal was structurally invisible (found live 2026-08-03: JätteFranska "2 för 45 kr" at ICA Maxi while we recorded plain 27.30). An ICA promotion has NO price field — the per-unit offer is parsed from the store's own label under the v0.41.2 doctrine (→ ICA bullet).

- **v0.41.3 — the review's small truths:** "kan inte jämföras" never claims "enda länken" (best_alt is also null when siblings merely lack a mängd — email + portal), a defensively-fed WORSE row says "dyrare än" instead of the UNKNOWN wording, a watched product without a price renders — not "None kr", `extraction_source`/`offer_type`/`offer_details` clamp to their columns instead of failing the write, the Statistik coverage denominator counts linkless products, `#stats-container` joined the never-scroll-sideways rule and the staleness caveat re-enters inline on the phone, and `test_static_gates` now gates EVERY `getElementById` literal.

- **v0.41.2 — the review fixes:** a failed weekly send is no longer counted as sent (retries while the local Monday lasts — → weekly-email bullet); the Willys savings-fallback is refused outright when any promotion has `qualifyingCount > 1`, and the portal now renders `offer_details` (→ campaign-price bullet); an inactive link can no longer win as a deal's `best_alt` (→ deals bullet); Lyko recognizes `SoldOut`/`Discontinued`, not only `OutOfStock` (→ Lyko bullet).

- **v0.41.1 — a Willys multi-buy no longer invents a per-unit price:** `savingsAmount` is the TOTAL saving over the promotion's qualifying quantity, so `priceValue − savingsAmount` recorded 31.10 for a "2 för 99" coffee whose shelf price is 49.50/st; the offer now comes from `potentialPromotions[].price.value`, with the store's `cartLabel` as `offer_details` (→ campaign-price bullet).

- **v0.41.0 — the weekly email answers "vad ska jag köpa, var?", and the deal verdict moved into the domain:** `domain/deals.py` is now THE deal query + classification (best/worse/unknown against the product's cheapest other link) — `/deals`, MCP `find_deals` and the weekly email all resolve through it, and the portal's `classifyDeal` merely READS the wire's `verdict`/`savings_per_unit_sek`. The Monday email became a buy list grouped per butik, goes to `SUMMARY_EMAIL` (fallback `ALLOWED_ENTRA_EMAIL`) instead of being derived from watch rows, and fires after the SWEDISH förmiddag window instead of at 14:00 naive UTC (→ deals + weekly-email bullets).

- **v0.40.0 — the product list answers "vilka länkar är döda?":** a Trasig länk status facet + a links-panel badge, derived per read from `check_attempts` (→ portal-UI bullet; `domain/link_health.py` is THE judgement — last 3 non-blocked attempts all failed, walls invisible in both directions).

- **v0.39.0 — the förmiddag window is spread, not drawn:** stratified next-check slots per store weekday, breaker deferrals staggered instead of identical, and a failed weekday check retries next MORNING instead of +24h clock-time drift (→ schedule + scheduler bullets).

- **v0.38.0 — the product list answers "vad jämför jag inte?":** a täckning facet on CHAIN level, with the own-brand exception that keeps it usable (→ portal-UI bullet).

- **v0.37.0 — Lyko, third store-HTML extractor:** same Avensia Nitro platform as Rusta, but its `comparisonPrices` is a LIST of other prices rather than a jämförpris, and only two of its four types are an ordinarie the store charged (→ Lyko bullet).

- **v0.36.0 — Statistik, a page of cross-product questions:** per-product trend, store comparison on a MATCHED basket, offer quality (how often a campaign is actually cheapest), and coverage counts that link into the product facets — computed by `domain/stats.py`, read-only by construction (→ statistics bullet).

- **v0.35.0 — the tracker records what the stores REFUSE, not just what they say:** a new `check_attempts` row per outgoing check (outcome, ladder tier, duration), because a failed or walled check wrote nothing at all and the only trace was a log buffer that resets on restart (→ check-attempts bullet).

- **v0.34.0 — the portal answers "vad ska jag köpa?" instead of listing everything:** Erbjudanden groups on whether an offer is actually the cheapest way to buy the product and ranks on the kr/unit margin (recency is a column now, not the order); the product row lost three of its five buttons; the product list filters in the browser on four facets (→ portal-UI bullet).

- **v0.33.0 — the förmiddag window became Swedish:** `schedule.py` computes the 06–12 window and the weekday in Europe/Stockholm and converts back to naive UTC, instead of applying the civil rule directly on UTC (which silently meant 08–14 Swedish summer time and judged "måndag" by a clock whose Monday starts 02:00 Swedish) (→ schedule bullet).

- **v0.29.3 — 405 is a wall too:** ICA refuses us with HTTP 405 as well as 202, and the fetcher was reading it as a legitimate page error (→ fetcher bullet).

- **v0.29.2 — a block is the store's state, not the link's:** the link that discovers a wall is deferred by the breaker instead of +24h, and a wall no longer counts as a check (→ scheduler bullet).

- **v0.29.1 — the block cooldown is 5 minutes, not 30:** an ICA wall was measured lifting within 17 minutes for the identical client and IP, so the base was sized for the wrong failure mode (→ store-block and fetcher bullets).

- **v0.29.0 — read-only for everyone else:** the app stopped being single-user; `ALLOWED_ENTRA_EMAIL` became the admin and every other Entra identity became a reader (→ Auth bullet + Gotcha 7).

- **v0.25.1 — Willys quick-add:** the preview gained an API-first tier, because Willys is an SPA whose HTML has no price at all (→ Quick-add).

- **v0.25.2 / v0.25.3 — campaign prices:** the two store families were wrong in opposite directions and now agree (→ campaign-price bullet).

- **v0.26.0 — backup repaired:** the export had rotted into a backup of nothing (→ export/import bullet), and alembic was silencing app logging (→ Gotcha 5).

- **v0.27.1 — the LLM cascade now says which caller ran it.** `extract_price` and `enrich_with_llm` share `_run_llm_cascade`, which logged `"Price extracted with <model>"` for both. So a *successful* JSON-LD check followed by routine enrichment logged `Price extracted via JSON-LD` → `Trying deepseek…` → `Price extracted with deepseek`, which reads exactly like "the structured parse failed and fell back to an LLM". It sent a debugging session after a store block that had not happened. Enrichment now logs `Enrichment answered with <model>`. **Enrichment can never change the price** — `price_sek` is not in `_ENRICHABLE_FIELDS` and only `None` fields are filled — so that sequence is normal and the JSON-LD price stands. **And it can no longer invent an inverted offer (v0.32.1):** an enriched or LLM-extracted `offer_price_sek >= price_sek` is refused together with its type/details, because a model reading a multi-buy ("2 för 130 kr") reports the bundle price as the per-unit offer. **All of this is history since v0.66.0** — the cascade, enrichment and these log lines were removed (→ structured-ladder bullet).

- **v0.27.0 — watches became editable:** `PUT /watches/{id}` had no dialog behind it, and its `if value is not None` guards made every target a one-way door — settable, never removable. The endpoint now keys on `model_fields_set`, so a **sent null clears** while an absent key is still left alone; the dialog sends every field on save. Both targets are exposed, because the scheduler alerts on `target_price_sek` AND `unit_price_target_sek` and showing only one leaves an invisible trigger on a watch restored from a backup.
