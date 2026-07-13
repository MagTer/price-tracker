---
phase: 04-mcp-server-agent-wiring
verified: 2026-07-06T00:00:00Z
status: gaps_found
score: 2/4 must-haves verified (MCP-01..04 tool surface + AUTH-04 bearer auth fully verified; MCP-05 partial; MCP-06/MCP-07 not attempted)
overrides_applied: 0
retroactive: true
re_verification:
  previous_status: none
  previous_score: n/a
  gaps_closed: []
  gaps_remaining:
    - "No mcp.<domain> subdomain/ingress exists (Dokploy-managed ingress not built, pending Entra client registration)"
    - "Agent platform /platformadmin/mcp/ registry never configured for this server"
    - "skills/general/price_tracker.md tools: frontmatter never updated to MCP-discovered names"
    - "No agent has ever answered a real query via this MCP server"
  regressions: []
---

# Phase 4: MCP Server + Agent Wiring Verification Report

**Phase Goal:** Expose the four price-tracker tools over MCP on a dedicated `mcp.<domain>` subdomain; register the server in the agent platform; confirm `priser` answers a real Swedish price query end-to-end.
**Verified:** 2026-07-06 (retroactive)
**Status:** gaps_found
**Re-verification:** No — initial (retroactive) verification

## Goal Achievement

### Observable Truths (ROADMAP Phase 4 Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | FastMCP server exposes 4 tools, rejects calls without valid bearer | VERIFIED | `tests/test_mcp.py` (20 tests, all pass); `src/mcp_server/server.py` + `auth.py` |
| 2 | MCP endpoint reachable on `mcp.<domain>` subdomain, no IAP redirect | **NOT MET** | No subdomain/ingress exists. Confirmed via this session's discussion: Dokploy-managed Traefik+auth-middleware ingress is planned but not built, pending Entra client registration. `/mcp` is currently only reachable via whatever network path reaches this container directly. |
| 3 | Agent platform `/platformadmin/mcp/` registry configured, discovers 4 tools | **NOT MET** | Not attempted. ROADMAP.md's own Phase 4 description already stated "Agent platform registration pending end-to-end verification" prior to this backfill. |
| 4 | Agent answers "Vad kostar Apotea omega-3?" via MCP | **NOT MET** | Not attempted; depends on #3. |

**Score:** 1 of 4 truths fully verified (criterion 1). Criteria 2-4 are genuinely unmet, not documentation gaps — this is the correct, honest status.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| src/mcp_server/server.py | 4 MCP tools | PRESENT | check_price, find_deals, compare_stores, list_products |
| src/mcp_server/auth.py | Bearer auth middleware | PRESENT | BearerTokenMiddleware |
| mcp.\<domain\> ingress config | Subdomain routing | **ABSENT** | Not built — separate future work (Dokploy) |
| Agent platform MCP registry entry | Registration in ai-agent-platform | **ABSENT** | Not attempted (separate repo) |

## Gap Summary

**What's blocking full phase completion:**
1. Build the Dokploy-managed Traefik + auth-middleware ingress with `mcp.<domain>` routing (excluded from IAP per D-18), pending Entra client registration.
2. Register this MCP server in `ai-agent-platform`'s `/platformadmin/mcp/` registry.
3. Update `skills/general/price_tracker.md`'s `tools:` frontmatter to the MCP-discovered tool names.
4. Verify an agent answers a real Swedish price query end-to-end via MCP.

**Recommended next step:** Treat gap closure as its own scoped follow-up (likely requiring work in the separate `ai-agent-platform` repo and the not-yet-built Dokploy ingress) before starting Phase 5, since Phase 5's own stated dependency on this phase is not currently satisfied.

## Note on Retroactive Verification

This VERIFICATION.md was written 2026-07-06 as part of a backfill reconciling GSD's phase-tracking (which requires on-disk CONTEXT/PLAN/SUMMARY/VERIFICATION artifacts) with actual repo state. Unlike Phases 2 and 3's retroactive verification (which passed with flagged-but-accepted caveats), this phase's gaps are substantive enough — and depend on work outside this repo entirely — that `gaps_found` is the accurate status per explicit user decision on 2026-07-06.
