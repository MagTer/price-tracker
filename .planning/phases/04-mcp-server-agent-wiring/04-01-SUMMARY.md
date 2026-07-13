---
phase: 04-mcp-server-agent-wiring
plan: 01
status: complete
completed: 2026-07-06
retroactive: true
retroactive_documented: 2026-07-06

provides:
  - FastMCP server (4 tools) mounted on the FastAPI app with bearer-token auth

requirements-completed: [MCP-01, MCP-02, MCP-03, MCP-04, AUTH-04]
requirements-partial: [MCP-05]
requirements-not-started: [MCP-06, MCP-07]

coverage:
  - id: MCP-01
    description: "check_price tool"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py"
        status: pass
    human_judgment: false
  - id: MCP-02
    description: "find_deals tool"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py"
        status: pass
    human_judgment: false
  - id: MCP-03
    description: "compare_stores tool"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py"
        status: pass
    human_judgment: false
  - id: MCP-04
    description: "list_products tool"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py"
        status: pass
    human_judgment: false
  - id: MCP-05
    description: "MCP mounted on FastAPI AND exposed via dedicated mcp.<domain> subdomain"
    verification:
      - kind: code-inspection
        ref: "src/api/app.py mounts /mcp; no subdomain/ingress exists"
        status: partial
    human_judgment: true
  - id: AUTH-04
    description: "Static MCP_BEARER_TOKEN validates MCP calls"
    verification:
      - kind: unit
        ref: "tests/test_mcp.py bearer auth tests"
        status: pass
    human_judgment: false
  - id: MCP-06
    description: "Agent platform /platformadmin/mcp/ registry configured"
    verification:
      - kind: none
        ref: "not attempted"
        status: fail
    human_judgment: true
  - id: MCP-07
    description: "Agent answers real Swedish query via MCP"
    verification:
      - kind: none
        ref: "not attempted - depends on MCP-06"
        status: fail
    human_judgment: true
---

# Phase 4: MCP Server + Agent Wiring Summary

**Retroactive summary** — written 2026-07-06. The MCP server was scaffolded in commit `d92372a` (2026-05-04) and substantially fixed today (package rename resolving a collision with the installed `mcp` SDK, fastmcp bumped from legacy 1.0 to 2.x, and its sub-app lifespan properly propagated — quick tasks 260706-rso and 260706-t3p).

**This phase's goal is only partially achieved.** The MCP server itself works and is tested; the agent-platform integration half of this phase's goal never happened.

## What Was Built

- `src/mcp_server/server.py` — 4 MCP tools (`check_price`, `find_deals`, `compare_stores`, `list_products`), FastMCP 2.x, streamable-HTTP transport
- `src/mcp_server/auth.py` — `BearerTokenMiddleware`, static `MCP_BEARER_TOKEN` validation
- `src/api/app.py` — mounts the MCP app at `/mcp`, correctly propagates its lifespan

## What Was NOT Built (genuine gaps, not oversights in this documentation)

- **No `mcp.<domain>` subdomain or ingress exists.** The Dokploy-managed Traefik + auth-middleware ingress that would route this subdomain and exclude it from IAP (per D-18) is not built yet, pending Entra client registration.
- **No agent-platform registration.** The separate `ai-agent-platform` repo's `/platformadmin/mcp/` registry was never configured to point at this server; `skills/general/price_tracker.md`'s `tools:` frontmatter was never updated.
- **No live agent query was ever answered via this MCP server.** This depends entirely on the registration above, which never happened.

## Verification Evidence (gathered 2026-07-06)

1. **4 tools + bearer auth** (criterion 1) — VERIFIED: `tests/test_mcp.py` (20 tests) exercises all 4 tools via `.fn()` calls (fastmcp 2.x's `@mcp.tool()` wrapper) plus bearer-middleware auth/rejection paths. All pass.
2. **Reachable on mcp.<domain> subdomain, IAP-bypassed** (criterion 2) — **NOT MET.** No subdomain or ingress exists at all right now.
3. **Agent platform registry configured, discovers tools** (criterion 3) — **NOT MET.** Never attempted.
4. **Agent answers real query via MCP** (criterion 4) — **NOT MET.** Depends on criterion 3.

## Deviations from "Plan"

This is the central finding of this retroactive backfill: ROADMAP.md and STATE.md previously marked this phase's checkbox as `[x]`/"Complete" (in a commit made earlier today, before this backfill's research surfaced the gap). That marking was optimistic — it reflected the MCP server being built, not the phase's full goal (which explicitly includes agent-platform wiring) being achieved. This SUMMARY and the accompanying VERIFICATION.md (`status: gaps_found`) correct that.

## Next Phase Readiness

**Phase 5 (Source-repo Cleanup) should NOT proceed yet.** ROADMAP.md's own Phase 5 dependency reads: "Phase 4 (MCP must be live and `priser` verified end-to-end before deleting source paths)" — a precondition this phase does not satisfy. Recommend: either (a) build the Dokploy ingress + complete agent-platform registration first, or (b) explicitly re-scope Phase 5's dependency if the user decides to proceed with source-repo cleanup independently of agent verification (not recommended without discussion, since Phase 5 deletes the chat-tool fallback that currently still works).
