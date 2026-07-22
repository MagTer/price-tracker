# price-tracker

Standalone Swedish grocery and pharmacy price tracker. Tracks prices at **ICA,
Willys, Apotea, Med24, Doz, Kronans Apotek and Apohem**, records price history, and alerts on drops via
email. It is single-user (Magnus only) and was extracted from the
`ai-agent-platform` monolith; it exposes its capabilities back to the agent
platform through an MCP server.

A background scheduler re-checks tracked product pages on a per-store cadence.
Product URLs are entered manually — there is deliberately no product discovery
or crawling (anti-block policy). **Quick-add** (⚡ Snabbtillägg, since v0.5.0)
still operates on one pasted URL, but infers the store, product name, package
and comparison unit from that single page and creates the product + link + first
price in one confirm step — see [docs/quick-add.md](./docs/quick-add.md).
Chains that price per physical butik (ICA) are handled with a per-link
**butiksetikett** ("ICA Maxi Sandviken"), so two butiker of one chain stay
distinguishable everywhere a store name is shown (v0.6.0).

## Architecture at a glance

- **FastAPI** app: server-rendered admin portal + JSON API at the root (`/`).
- **PostgreSQL** via async SQLAlchemy 2.0 + Alembic (5 tables:
  `stores`, `products`, `product_stores`, `price_points`, `watches`).
- **Price extraction chain** (per check, first hit wins):
  1. Store API (Willys public REST) where available.
  2. **JSON-LD** (`schema.org` Product/Offer parsed from raw HTML) — exact
     prices, no LLM cost.
  3. **LLM cascade** via OpenRouter as fallback. Extractions below
     `PRICE_PARSER_MIN_CONFIDENCE` are discarded (a gap beats a hallucinated
     price).
- **MCP server** (`fastmcp`) mounted at `/mcp/` exposing 4 tools:
  `check_price`, `find_deals`, `compare_stores`, `list_products`.

## Security model

- **Portal / API auth = IAP header trust.** This app does NOT terminate Entra
  OIDC. An upstream Traefik + oauth2-proxy `forwardAuth` ingress (live in
  production since 2026-07-09, managed in the home-server repo) authenticates
  the user once and forwards `X-Auth-Request-Email`. The app validates that
  header against `ALLOWED_ENTRA_EMAIL` and denies everything else.
  `ALLOWED_ENTRA_EMAIL` must match the Entra **UPN**, not necessarily a gmail
  address.
- **MCP auth = static bearer.** `/mcp/` is served on the same host
  (`price.<domain>/mcp/`) via a path-scoped Traefik router that bypasses the
  Entra gate, and is protected by `MCP_BEARER_TOKEN`. The endpoint is
  **fail-closed**: if `MCP_BEARER_TOKEN` is unset, every `/mcp` request returns
  503. Token comparison is constant-time.

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DATABASE_URL` | prod | `postgresql+asyncpg://price_tracker:price_tracker@localhost:5432/price_tracker` | Async Postgres URL. Also read by Alembic (overrides `alembic.ini`). |
| `ALLOWED_ENTRA_EMAIL` | yes | `""` (deny all) | Entra **UPN** allowed to use the portal; matched against `X-Auth-Request-Email`. |
| `MCP_BEARER_TOKEN` | for MCP | `""` | Static bearer for `/mcp/`. Unset ⇒ `/mcp` fails closed with 503. |
| `OPENROUTER_API_KEY` | for LLM fallback | `""` | OpenRouter key for the LLM extraction cascade. |
| `OPENROUTER_BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenRouter endpoint (OpenAI-compatible). |
| `OPENROUTER_HTTP_REFERER` | no | `""` | Optional OpenRouter attribution header. |
| `OPENROUTER_APP_TITLE` | no | `Price Tracker` | Optional OpenRouter attribution header. |
| `PRICE_PARSER_MODEL_CASCADE` | no | `meta-llama/llama-4-scout,anthropic/claude-haiku-4.5` | Comma-separated model fallback order. |
| `PRICE_PARSER_MIN_CONFIDENCE` | no | `0.6` | Acceptance floor; LLM extractions below this are discarded. |
| `RESEND_API_KEY` | for alerts | `""` | Resend API key. Watch-alert emails are sent via the Resend HTTP API. |
| `EMAIL_FROM` | for alerts | `""` | From address for alert emails. Must be on a Resend-verified domain. |
| `QUICKADD_STORE_LABELS` | no | the original operator's butiker | JSON object of ICA butik id → display name, e.g. `{"1003396": "ICA Maxi Sandviken"}`. Used by quick-add's butiksetikett suggestion. |
| `QUICKADD_SIBLING_GROUPS` | no | the original operator's butiker | JSON array of butik-id groups, e.g. `[["1003396", "1004503"]]`. Quick-add offers to create sister-butik links within a group. Malformed JSON in either variable logs a warning and falls back to the defaults. |

## Run your own instance

The repo is MIT-licensed and an instance is fully self-contained — the steps below are
everything a second deployment needs.

1. **Image:** pull `ghcr.io/magter/price-tracker:vX.Y.Z` (built by the release workflow),
   or build your own from the Dockerfile.
2. **Database:** point `DATABASE_URL` at an empty Postgres 16 database. The container's
   start command runs `alembic upgrade head`, which creates the schema and seeds the five
   supported stores (ICA, Willys, Apotea, Med24, Doz, Kronans Apotek, Apohem).
3. **Auth:** the app does NOT do login itself. It trusts the `X-Auth-Request-Email`
   header from your reverse proxy (any forward-auth setup works — oauth2-proxy, Authelia,
   Cloudflare Access…) and compares it against `ALLOWED_ENTRA_EMAIL` (despite the name:
   whatever email your IdP forwards). **Never expose the app without such a proxy** — the
   header is trusted as-is. `/mcp/` is instead protected by `MCP_BEARER_TOKEN` and fails
   closed (503) when unset.
4. **Keys:** get your own `OPENROUTER_API_KEY` (LLM extraction fallback — cheap; most
   checks use store APIs/JSON-LD and never touch the LLM) and, for email alerts,
   `RESEND_API_KEY` + `EMAIL_FROM` on a domain you have verified with Resend.
5. **Your stores:** set `QUICKADD_STORE_LABELS` / `QUICKADD_SIBLING_GROUPS` to *your* ICA
   butiker (the id is in the product-page URL after `/stores/`). Without them, quick-add
   suggests the defaults' labels — harmless, but they are someone else's stores.

Single-user by design: one instance per person, auth gates one email. Multi-tenancy is
deliberately out of scope (see `.planning/` for the decision history).

## Local development

```bash
# 1. Install dependencies
poetry install

# 2. Start Postgres (local-dev-only compose; production lives in home-server)
docker compose up -d postgres

# 3. Apply migrations
poetry run alembic upgrade head

# 4. Run the app (src layout — put src on PYTHONPATH)
PYTHONPATH=src poetry run uvicorn api.app:app --reload
```

The portal is then at http://localhost:8000/. Because there is no local
Entra ingress, send the auth header yourself, e.g.:

```bash
curl -H "X-Auth-Request-Email: $ALLOWED_ENTRA_EMAIL" http://localhost:8000/stores
```

`docker-compose.yml` in this repo is **local development only** — it publishes
Postgres on `5432` for host `psql` access. It is never deployed.

## Tests

```bash
poetry run pytest
```

Two tiers:

| Tier | Command | Needs Postgres |
|------|---------|----------------|
| fast (mocks) | `poetry run pytest -m "not integration"` | no |
| integration | `poetry run pytest -m integration` | yes |

`poetry run pytest` runs both. The integration tier (`tests/test_migration.py`) drops and
recreates a dedicated throwaway database called **`price_tracker_test`** — never the dev database,
whatever `DATABASE_URL` says — applies `alembic upgrade head` to it, and proves the schema claims
the mocks structurally cannot (a duplicate `store_url` is rejected; two links at one store both
persist; the kr/unit `ORDER BY` sinks a link with no amount to the bottom). **When no Postgres is
reachable those tests SKIP, they do not fail**, so `pytest` stays green on a laptop or a CI job
with no database. Point them elsewhere with `TEST_DATABASE_URL` if you want.

## Schema reset (Phase 04.1) — required once, by the operator

**Read this before the next deploy. Skipping it fails SILENTLY.**

Phase 04.1 moved the package data from `products` to `product_stores` and rewrote
`alembic/versions/0001_initial.py` **in place** (D-14) rather than stacking a `0002` — the database
was empty, so one clean migration beat a migration chain that only ever existed to patch a schema
nobody was using.

The consequence is a trap:

> `alembic_version` stores only a **revision id**. Alembic does **not** checksum migration bodies.
> A database already stamped `0001_initial` — **which the deployed instance is** — therefore
> compares equal to head, applies **nothing**, prints nothing, and **exits 0**. The app then starts
> against the **old** schema. Nothing warns you. The first symptom is a runtime error on the first
> query touching `product_stores.package_quantity`, long after the deploy looked successful.

Dropping the volume is the only defence. The deployment compose already runs `alembic upgrade head`
in its command, so **the operative step is the volume drop** — the migration then runs against an
empty database, as intended.

This targets the **deployed** stack: the home-server repo's
`compose/dokploy-apps/price-tracker/` (Dokploy-managed, pinned GHCR image). It is **not** this
repo's `docker-compose.yml`, which is local-dev-only.

```bash
# On the deployment host, in compose/dokploy-apps/price-tracker/ (home-server repo).

# 1. Stop the app and DROP THE POSTGRES VOLUME. This removes the tables AND the
#    alembic_version row that makes the rewritten 0001 look already-applied.
docker compose down -v

# 2. Bring Postgres back up (empty).
docker compose up -d postgres

# 3. Start the app: its command runs `alembic upgrade head`, which now executes the
#    rewritten 0001 against an empty database and re-seeds the five stores.
docker compose up -d

# 4. VERIFY — do not skip; the failure this guards against is silent.
docker compose exec app alembic current   # must print: 0001_initial (head)
docker compose exec app alembic check     # must print: No new upgrade operations detected.
```

`alembic current` printing `0001_initial (head)` on its own proves nothing (a stale DB prints the
same thing) — it is `alembic check` that proves the live schema matches the ORM. If `check` reports
operations, the volume was not actually dropped.

**This destroys all price history.** That is accepted, and it is the basis on which the in-place
rewrite was sanctioned: the database is empty. There is nothing to rescue.

**⚠ The export format changed — check before you reset.** An export file produced *before* Phase
04.1 will **not** round-trip through the new import: package data moved from the product rows to
the store-link rows, and the price-history rows renamed `unit_price_sek` → `store_unit_price_sek`.
If you are holding an old export you intend to restore, **say so before running the reset** — it
needs hand-editing first.

## Release / deployment

This repo's only deployment responsibility is building the container image;
the actual deployment lives in the home-server repo (GitOps split: app repo =
build, platform repo = deployment truth).

```bash
# Tag a version — this triggers .github/workflows/release.yml, which builds
# and pushes ghcr.io/magter/price-tracker:vX.Y.Z (+ :sha-<commit>) for amd64.
git tag vX.Y.Z && git push origin vX.Y.Z
```

Then bump the pinned image tag in the home-server repo's
`compose/dokploy-apps/price-tracker/docker-compose.yml`. The production compose
runs `alembic upgrade head` before starting uvicorn, has a DB-aware
`/health` check, and injects all secrets via SOPS/Dokploy.

## License

[MIT](./LICENSE).
