---
quick_id: 260706-tha
slug: fix-4-stale-entra-oidc-references-in-cla
status: complete
key-files:
  modified:
    - CLAUDE.md
metrics:
  completed: 2026-07-06
---

# Quick Task 260706-tha: Fix 4 stale Entra OIDC references in CLAUDE.md Summary

Corrected CLAUDE.md's auth description from an in-app Entra OIDC client to the actual IAP header-trust model implemented in `src/api/auth.py`.

## What Was Done

CLAUDE.md previously described the app as terminating Entra OIDC itself (via `fastapi-azure-auth`/`authlib`), but `src/api/auth.py` actually implements IAP header-trust: it reads an `X-Auth-Request-Email` header forwarded by an upstream ingress and validates it case-insensitively against the `ALLOWED_ENTRA_EMAIL` env var, raising 403 on missing header, mismatch, or unconfigured env var — with zero in-app OIDC code.

Made exactly the 4 planned replacements in CLAUDE.md:

1. **Project intro paragraph** — now states Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app, instead of "with Entra ID auth."
2. **Technology Stack → Auth (UI) bullet** — now describes IAP header trust (`X-Auth-Request-Email` forwarded by the upstream ingress); no in-app OIDC client, instead of `fastapi-azure-auth` or `authlib`.
3. **Architecture directory tree comment on `auth.py`** — now reads "IAP header trust (X-Auth-Request-Email) + single-email gate" instead of "Entra OIDC + single-oid gate".
4. **Architecture section Auth paragraph** — now describes the Admin UI trusting `X-Auth-Request-Email` and validating against `ALLOWED_ENTRA_EMAIL`, attributes actual Entra ID enforcement to the Dokploy-managed upstream ingress (not yet built, pending Entra client registration), keeps the `/mcp` bearer-token sentence and EXTRACTION.md §6 reference, and notes that EXTRACTION.md §6's original in-app-OIDC description was superseded by this model per decisions D-18/D-19 in `.planning/STATE.md`.

No other content in CLAUDE.md was touched. EXTRACTION.md, `.planning/ROADMAP.md`, `.planning/REQUIREMENTS.md`, and `.planning/STATE.md` were not modified (they already correctly describe the IAP header-trust architecture).

## Deviations from Plan

None - plan executed exactly as written.

## Verification

Automated check passed:
```
test "$(grep -c 'fastapi-azure-auth' CLAUDE.md)" -eq 0 \
  && test "$(grep -c 'Entra OIDC' CLAUDE.md)" -eq 0 \
  && grep -qi 'X-Auth-Request-Email' CLAUDE.md \
  && grep -qi 'Dokploy' CLAUDE.md \
  && grep -qi 'ALLOWED_ENTRA_EMAIL' CLAUDE.md
```
Result: PASSED.

`git diff CLAUDE.md` confirmed only the 4 identified lines/paragraphs changed — no unrelated content modified.

## Commits

- `d094d70`: docs(260706-tha): fix 4 stale Entra OIDC references in CLAUDE.md

## Self-Check: PASSED

- FOUND: CLAUDE.md (4 spots updated, verified via grep)
- FOUND: commit d094d70 in git log
