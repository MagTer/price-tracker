---
quick_id: 260706-tha
slug: fix-4-stale-entra-oidc-references-in-cla
type: execute
autonomous: true
files_modified:
  - CLAUDE.md
must_haves:
  truths:
    - "CLAUDE.md no longer claims this app terminates Entra OIDC in-app (fastapi-azure-auth/authlib) — it describes IAP header-trust (X-Auth-Request-Email), matching src/api/auth.py exactly"
    - "CLAUDE.md attributes Entra ID enforcement to the upstream Traefik + auth-middleware ingress, noting it is managed via Dokploy and not yet built (pending Entra client registration)"
    - "No other content in CLAUDE.md changed; EXTRACTION.md, .planning/ROADMAP.md, .planning/REQUIREMENTS.md, .planning/STATE.md untouched"
  artifacts:
    - CLAUDE.md
  key_links:
    - "CLAUDE.md's Auth descriptions -> src/api/auth.py (require_auth reads X-Auth-Request-Email, validates against ALLOWED_ENTRA_EMAIL, 403 on mismatch)"
---

<objective>
Fix 4 stale Entra OIDC references in CLAUDE.md. The file currently describes an in-app Entra OIDC client (fastapi-azure-auth/authlib code flow), but the actual, already-implemented auth model in src/api/auth.py is IAP header-trust: the app reads an `X-Auth-Request-Email` header forwarded by an upstream ingress and validates it against `ALLOWED_ENTRA_EMAIL`, with zero in-app OIDC code. This was locked by decisions D-18/D-19 (see .planning/STATE.md) and is already correctly described in .planning/ROADMAP.md and .planning/REQUIREMENTS.md — only CLAUDE.md drifted.

New detail to incorporate: the upstream Traefik + auth-middleware ingress stack (the thing that actually terminates Entra OIDC and forwards the header) will be operated within Dokploy's scope — Dokploy is the deployment platform managing it, not a separately hand-built edge-stack repo. That ingress config is not built yet; it's pending Entra client registration.

Do NOT touch EXTRACTION.md, .planning/ROADMAP.md, .planning/REQUIREMENTS.md, or .planning/STATE.md — those already correctly describe the IAP header-trust architecture. Do NOT change anything else in CLAUDE.md beyond the 4 spots below.
</objective>

<context>
@src/api/auth.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Fix 4 stale Entra OIDC references in CLAUDE.md</name>
  <files>CLAUDE.md</files>
  <action>
Make exactly these 4 replacements in CLAUDE.md. Verify each new sentence is consistent with src/api/auth.py's actual behavior (reads `X-Auth-Request-Email` header via `Header(None, alias="X-Auth-Request-Email")`, compares case-insensitively against `ALLOWED_ENTRA_EMAIL` env var, raises 403 on missing/mismatch/unconfigured) before writing.

1. Line 4 (Project section intro paragraph): change
   "...runs alongside the agent platform behind the same Traefik proxy. Single-user (Magnus only) with Entra ID auth."
   to convey: single-user (Magnus only); Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app.

2. Line 25 (Technology Stack, "Auth (UI)" bullet): change
   "- **Auth (UI):** `fastapi-azure-auth` or `authlib` for Entra OIDC"
   to describe IAP header trust: reads `X-Auth-Request-Email` forwarded by the upstream Traefik + auth-middleware ingress (managed via Dokploy); no in-app OIDC client.

3. Line 57 (Architecture directory tree comment on auth.py): change
   "│   ├── auth.py    # Entra OIDC + single-oid gate"
   to describe what auth.py actually does: IAP header trust (X-Auth-Request-Email) + single-email gate.

4. Line 77 (Architecture section, Auth paragraph): change
   "**Auth:** Admin UI uses Entra OIDC with single-`oid` gate; `/mcp` endpoint uses static bearer token. See EXTRACTION.md §6."
   to describe: Admin UI trusts `X-Auth-Request-Email` forwarded by the upstream Traefik + auth-middleware ingress and validates it against `ALLOWED_ENTRA_EMAIL`; Entra ID enforcement itself happens at that ingress layer (managed via Dokploy — not yet built, pending Entra client registration), not in this app. Keep the `/mcp` endpoint bearer-token sentence and the EXTRACTION.md §6 reference, but note EXTRACTION.md §6's original in-app-OIDC description was superseded by the IAP header-trust model (D-18/D-19 in .planning/STATE.md).

Do not modify any other line, section, or file.
  </action>
  <verify>
    <automated>test "$(grep -c 'fastapi-azure-auth' CLAUDE.md)" -eq 0 && test "$(grep -c 'Entra OIDC' CLAUDE.md)" -eq 0 && grep -qi 'X-Auth-Request-Email' CLAUDE.md && grep -qi 'Dokploy' CLAUDE.md && grep -qi 'ALLOWED_ENTRA_EMAIL' CLAUDE.md</automated>
  </verify>
  <done>CLAUDE.md's 4 stale Entra-OIDC spots now describe IAP header-trust (X-Auth-Request-Email / ALLOWED_ENTRA_EMAIL) with Entra enforcement attributed to the Dokploy-managed upstream ingress, matching src/api/auth.py; no other content changed.</done>
</task>

</tasks>

<verification>
- `grep -c "fastapi-azure-auth" CLAUDE.md` returns 0.
- `grep -c "Entra OIDC" CLAUDE.md` returns 0 (no remaining claims of in-app OIDC).
- CLAUDE.md mentions `X-Auth-Request-Email`, `ALLOWED_ENTRA_EMAIL`, and `Dokploy`.
- `git diff CLAUDE.md` touches only the 4 identified lines/paragraphs — no unrelated changes.
</verification>

<success_criteria>
- CLAUDE.md accurately describes the IAP header-trust auth model implemented in src/api/auth.py.
- Entra ID enforcement is correctly attributed to the upstream Traefik + auth-middleware ingress, managed via Dokploy, not yet built.
- No other file touched.
</success_criteria>

<output>
Create `.planning/quick/260706-tha-fix-4-stale-entra-oidc-references-in-cla/260706-tha-SUMMARY.md` when done, with `status: complete` in its frontmatter.
</output>
