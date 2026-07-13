# price-tracker

Standalone Swedish grocery and pharmacy price tracker. Tracks prices at **ICA,
Willys, Apotea, Med24 and Doz**, records price history, and alerts on drops via
email. It is single-user (Magnus only) and was extracted from the
`ai-agent-platform` monolith; it exposes its capabilities back to the agent
platform through an MCP server.

A background scheduler re-checks tracked product pages on a per-store cadence.
Product URLs are entered manually — there is deliberately no product discovery
or crawling (anti-block policy).

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
