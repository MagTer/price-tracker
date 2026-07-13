# Phase 1: Skeleton + Domain Copy - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-02
**Phase:** 1-Skeleton + Domain Copy
**Areas discussed:** Default tenant identity, Test DB backend, Squashed-migration shape, Dockerfile/compose scope, Auth topology (project-level), Edge proxy stack (project-level), Roadmap reassess timing

---

## Default tenant identity

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded UUID constant in code | Single module-level constant referenced everywhere | ✓ (Claude discretion) |
| Env-var `DEFAULT_TENANT_ID` | Configurable per environment | |
| Derived (UUID5 of email) | Reproducible without storing | |

**User's choice:** "No opinion" — Claude's discretion.
**Notes:** Hardcoded chosen because single-user / single-deployment makes env-var indirection a risk (mismatch between migration seed and runtime) without buying real flexibility. Generated stable UUID `f21b6620-c793-46e3-a354-dfcd9956b4a2`.

---

## Test DB backend

| Option | Description | Selected |
|--------|-------------|----------|
| sqlite + aiosqlite with type compat shims | Cheap; needs UUID/JSONB compilers | |
| Postgres in docker | Slow; matches prod | |
| Hybrid (mock for unit, postgres for integration) | Tests use mocks; alembic gate uses real postgres | ✓ (Claude discretion) |

**User's choice:** "No opinion" — Claude's discretion.
**Notes:** Source tests already use `unittest.mock` heavily — no real session needed. Drop `aiosqlite` from deps. Schema verified by `alembic upgrade head` against postgres (gate 2). If integration tests appear in a future phase, use testcontainers-python for fidelity.

---

## Squashed-migration shape

| Option | Description | Selected |
|--------|-------------|----------|
| Hand-derive from final `models.py` | Clean, idiomatic, no historical cruft | ✓ (Claude discretion) |
| Concatenate 3 source migrations' upgrade() bodies | Verbose, preserves historical sequence | |

**User's choice:** "No opinion" — Claude's discretion.
**Notes:** Empty Alembic graph + no production data = no reason to preserve history. Bake the columns added by the two later source migrations (price_drop_threshold_pct, unit-price columns) directly into the table definitions.

---

## Dockerfile / compose scope

| Option | Description | Selected |
|--------|-------------|----------|
| Postgres-only compose | Cleanest; defers app to Phase 2 | ✓ |
| Postgres + app stub on internal network | Both services; no Traefik labels | |
| Defer compose entirely to Phase 2 | Dockerfile only in Phase 1 | |

**User's choice:** Postgres-only compose.
**Notes:** Driven by the IAP shift below — routing/Traefik labels live in the future edge-proxy stack, not in this repo. Phase 1 compose just enables `alembic upgrade head` against a real postgres for gate 2.

---

## Auth topology (project-level shift)

| Option | Description | Selected |
|--------|-------------|----------|
| Portal owns auth (IAP pattern) | Edge proxy validates Entra OIDC once; apps trust X-Auth-Request-Email | ✓ |
| Each app does its own OIDC | Current EXTRACTION.md plan; price-tracker keeps fastapi-azure-auth | |
| Decide later, keep both options open | Ship Phase 1 unchanged | |

**User's choice:** Portal owns auth (IAP pattern).
**Notes:** Single user, multiple personal apps over time → IAP scales linearly. Drops `fastapi-azure-auth` and `pyjwt` from price-tracker. Phase 3 simplifies dramatically. Phase 4 MCP routing now constrained — likely subdomain bypass.

---

## Edge proxy + portal stack (project-level shift)

| Option | Description | Selected |
|--------|-------------|----------|
| Traefik + oauth2-proxy + Homepage dashboard | Most-documented IAP combo | ✓ |
| Caddy + caddy-security + custom HTML | Simpler config; fewer moving parts | |
| Authentik (full IdP) | Heavier; gives 2FA + audit + app catalog | |
| Decide later — just answer auth question now | Lock topology, defer tooling | |

**User's choice:** Traefik + oauth2-proxy + Homepage dashboard.
**Notes:** Hosts on Flatcar inside Proxmox VM on a single mini-PC. Stack lives in a separate future project/milestone, NOT inside the price-tracker extraction.

---

## Roadmap reassess timing

| Option | Description | Selected |
|--------|-------------|----------|
| After Phase 1 completes, before Phase 3 | Phase 1/2 unaffected by auth shift; reassess before Phase 3 | ✓ |
| Now — reassess immediately | Heaviest; no chance of stale decisions in Phase 1/2 plans | |
| After v1 ships, as part of v1.1 | Defer entirely; build v1 per current plan | |

**User's choice:** After Phase 1 completes, before Phase 3.
**Notes:** Phase 1 (skeleton) and Phase 2 (infra) are auth-agnostic. Reassess after Phase 1 to rewrite Phase 3 (OIDC code flow → header trust) and Phase 4 (MCP bypass), and to add a portal/proxy milestone or new project.

---

## Claude's Discretion

- Default tenant identity (D-01..03): hardcoded constant in `src/domain/tenant.py`, no `tenants` table, value `f21b6620-c793-46e3-a354-dfcd9956b4a2`.
- Test DB backend (D-04..06): drop aiosqlite, mocks for unit, alembic-gate covers schema.
- Squashed-migration shape (D-07..10): hand-derived clean migration, postgres-only types, bulk_insert seed for stores, no tenants table.
- Concrete pyproject pins, package layout (src vs flat), conftest organisation, Dockerfile multi-stage details — left to planner.

## Deferred Ideas

- Edge proxy + portal stack as separate milestone/project (Traefik + oauth2-proxy + Homepage on Flatcar/Proxmox)
- Phase 3 rewrite to header-based auth (after roadmap reassess)
- Phase 4 MCP subdomain bypass design (after roadmap reassess)
- Roadmap reassess action items: PROJECT.md decisions update, REQUIREMENTS.md AUTH-* rewrite, ROADMAP.md Phase 3/4 success criteria update, decision on portal-as-milestone vs separate project
