# Phase 3: Admin UI + IAP Header Trust - Context

**Gathered:** 2026-07-06 (retroactive)
**Status:** Retroactively documented — implementation predates formal GSD phase tracking

<domain>
## Phase Boundary

Restore the operator workflow — create products, link to stores, view price history — behind the upstream Identity-Aware Proxy (terminating Entra OIDC outside this repo). The app reads `X-Auth-Request-Email`, validates against `ALLOWED_ENTRA_EMAIL`, and denies otherwise. Served on `prices.<domain>` by the external edge proxy.

**Note on retroactive documentation:** Implemented directly in commit `d92372a` (2026-05-04) alongside Phases 2 and 4, without the formal GSD pipeline. This CONTEXT.md and this phase's PLAN/SUMMARY/VERIFICATION were written 2026-07-06 to reconcile tracking with reality — see Phase 2's CONTEXT.md for the same disclosure, which applies identically here.

**Verifiable gates** (from ROADMAP.md Phase 3):
1. A request carrying `X-Auth-Request-Email: <ALLOWED_ENTRA_EMAIL>` lands on the admin UI; any other value (or missing header) returns HTTP 403 from a single FastAPI dependency that wraps the admin router
2. Through the admin UI, the operator creates a product, links it to Willys, triggers a check, and sees a price history row appear
3. App carries no `fastapi-azure-auth`, `authlib`, `pyjwt`, or session-cookie code; CSRF middleware is removed
4. `docker compose up` brings up postgres + app on the external `edge` docker network without publishing app ports to the host
5. Admin endpoints use the seeded `DEFAULT_TENANT_ID` instead of `/me/context` / `context_id` lookups

</domain>

<decisions>
## Implementation Decisions

### Auth model: IAP header trust
- `src/api/auth.py`'s `require_auth()` FastAPI dependency reads `X-Auth-Request-Email` (via `Header(None, alias=...)`), compares case-insensitively against `ALLOWED_ENTRA_EMAIL` env var, raises 403 on missing header, unconfigured env var, or mismatch.
- Applied globally to the admin router via `APIRouter(dependencies=[Depends(require_auth)])`, plus explicitly on individual mutating endpoints — no session cookies, no CSRF middleware (the IAP + private network is the trust boundary, per D-17/D-18 in `.planning/STATE.md`).
- Zero in-app OIDC client code — confirmed via `grep -rE "fastapi-azure-auth|authlib|pyjwt|csrf" pyproject.toml src/` returning no matches (re-verified 2026-07-06).

### Tenant scoping
- `DEFAULT_TENANT_ID` (from `src/domain/tenant.py`, established in Phase 1) is used directly by admin endpoints; no `/me/context` endpoint or `context_id` lookups remain (re-verified 2026-07-06 via grep — zero matches in `src/api/admin.py`).

### Admin dashboard template
- `src/api/templates/admin.html` was initially ported with only a minimal 11-line stub in commit `d92372a`, then substantially rebuilt (699 additional lines — sidebar/header layout, product/watch management, Chart.js price-history views) in a later uncommitted working-tree change that was only formally committed today (2026-07-06, commit `16245a4`) as part of reconciling this session's uncommitted Phase 3 work.

</decisions>

<code_context>
## Existing Code Insights

- `src/api/admin.py` (1,786 LOC) — ported from source `admin_price_tracker.py`, commit `d92372a`
- `src/api/auth.py`, `src/api/schemas.py` — commit `d92372a`
- `src/api/templates/admin.html` — stub in `d92372a`, fully built out in commit `16245a4` (2026-07-06)
- `docker-compose.yml` — app service joins external `edge` network, no published ports, from `d92372a`

</code_context>

<specifics>
## Specific Ideas

No specific requirements beyond ROADMAP.md's phase description — executed directly rather than planned in advance.

</specifics>

<deferred>
## Deferred Ideas

None captured — no discuss session took place for this phase.

</deferred>
