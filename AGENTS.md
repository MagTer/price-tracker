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

**Release history is `git log`.** The per-release index ("Since 04.1", v0.14–v0.66.0) was removed after its unique facts moved into the skills; read it with `git show 9d0b71e:AGENTS.md` (or `git show 88e99e9:CLAUDE.md`).

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
│   ├── tenant.py    # DEFAULT_TENANT_ID, the single-tenant constant; the client never echoes
│   │                # the tenant UUID and `require_default_tenant` is the ONE guard (v0.51.0)
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
                    # 0010_seed_lyko → 0011_package_precision → 0012_seed_jysk →
                    # 0013_store_weekdays_mon_fri → 0014_summary_sends
```

**Data model (7 tables):** `stores`, `products`, `product_stores`, `price_points`, `watches`, `check_attempts` (v0.35.0 — → check-attempts bullet), `summary_sends` (v0.62.0 — the buy-list mail's durable receipt, → buy-list-email bullet). Tenant-scoped tables carry a `tenant_id` UUID column.

- **`uq_product_store(product_id, store_id)` is DROPPED; `store_url` is globally unique** (`uq_product_stores_store_url`). One product can have several links at the same store (different pack sizes). The URL is the link's natural key. **An AST gate in `tests/test_static_gates.py` fails any query in `src/` that resolves a link by the `(product_id, store_id)` pair** — if that test fires, your query is built on the pre-04.1 model, not on a lint nit.

**Export/import is THE backup, and a backup is judged by what it leaves out (v0.26.0):** `GET /export` writes **every product in the tenant**. It used to inner-join *active watches*, so a tracker with products but no watch exported `products: []` under a **200 OK** — 15 prod products became a 205-byte file. A watch is one optional attribute of a product, never the condition for saving it. Format **1.1** carries `store_label` and keys each history row on `store_url` (the link's natural key — product+slug cannot address one of several links at one store, the pre-04.1 shape the AST gate forbids). `POST /import` restores products, links, watches **and price history**, deduped on `(link, checked_at)` so re-importing is idempotent; history used to be exported and then silently dropped. A 1.0 file still imports — its unaddressable history rows are skipped with a warning, never guessed at. The portal's button passes `history_days=0` = ALL history (the 30-day default silently truncated). `tests/test_export_import.py` is a real-Postgres round trip — export, wipe, import, compare — and these endpoints had **no test at all** before it, which is exactly how they rotted.

**`POST /import` enforces the invariants every other write path has (v0.50.0):** offer inversion, quantity bounds, no unit overwrite, and a 20 MB cap.

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
