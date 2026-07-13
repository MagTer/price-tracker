# Phase 4: MCP Server + Agent Wiring - Context

**Gathered:** 2026-07-06 (retroactive)
**Status:** Retroactively documented — implementation predates formal GSD phase tracking; goal only PARTIALLY achieved (see gaps below)

<domain>
## Phase Boundary

Expose the four price-tracker tools over MCP on a dedicated `mcp.<domain>` subdomain that the upstream IAP excludes from oauth2-proxy middleware (per-host bypass, not per-path — D-18). The MCP host authenticates purely with `MCP_BEARER_TOKEN`. Register the server in the agent platform and confirm `priser` answers a real Swedish price query end-to-end.

**Note on retroactive documentation:** The MCP server itself was implemented directly in commit `d92372a` (2026-05-04, scaffold) and substantially fixed/hardened today (2026-07-06, quick tasks 260706-rso and 260706-t3p: package rename, fastmcp 2.x upgrade, lifespan propagation). This CONTEXT.md and this phase's PLAN/SUMMARY/VERIFICATION were written 2026-07-06 to reconcile tracking with reality.

**Unlike Phases 2 and 3, this phase's goal is only partially achieved** — see `<decisions>` and `04-VERIFICATION.md` for the specific gaps. ROADMAP.md's own Phase 4 one-liner already said "Agent platform registration pending end-to-end verification" before this backfill; this documentation makes that gap formal and explicit rather than letting a `[x]` checkbox imply otherwise.

**Verifiable gates** (from ROADMAP.md Phase 4):
1. FastMCP server, mounted on FastAPI, exposes `check_price`/`find_deals`/`compare_stores`/`list_products`, rejects calls without valid `MCP_BEARER_TOKEN`
2. MCP endpoint reachable on `mcp.<domain>` (subdomain, not `/mcp` path) — verifiable from the agent-platform host with a bearer-only request that returns 200 and never sees an IAP redirect
3. Agent platform's `/platformadmin/mcp/` registry has this server configured and discovers all 4 tools by name
4. An agent in the platform answers "Vad kostar Apotea omega-3?" by calling MCP tools against this server

</domain>

<decisions>
## Implementation Decisions

### What's actually done (criterion 1)
- `src/mcp_server/server.py` (renamed from `src/mcp/` today, quick task 260706-rso, to resolve a package collision with the installed `mcp` PyPI SDK) exposes all 4 tools via `@mcp.tool()`.
- `src/mcp_server/auth.py`'s `BearerTokenMiddleware` validates `Authorization: Bearer <MCP_BEARER_TOKEN>`, rejecting with 401 otherwise.
- `get_mcp_app()` uses fastmcp 2.x's `http_app()` (modern streamable-HTTP transport, bumped from the legacy 1.0 SSE-only API today), and its lifespan is now properly propagated into `create_app()`'s own lifespan (quick task 260706-t3p) — the session manager actually starts/stops with the real app.
- All of this is mounted at `/mcp` on the SAME FastAPI app as the admin UI (`src/api/app.py`), not on a separate `mcp.<domain>` subdomain.

### What's NOT done (criteria 2-4) — genuine gaps, not documentation drift
- **No ingress/subdomain exists.** Per this session's earlier discussion with the user: the Traefik + auth-middleware ingress that would route `mcp.<domain>` to this app (and exclude that host from IAP per D-18) is planned to be managed via Dokploy, but is **not built yet** — pending the user registering an Entra client. Today, `/mcp` is only reachable via whatever path FastAPI itself exposes it on, on whatever host/network reaches this container directly (e.g., the `edge` docker network) — there is no subdomain, and no live proof the IAP-bypass-per-host behavior works, because the IAP itself doesn't exist yet.
- **Agent platform registration never happened.** ROADMAP.md's own Phase 4 line states "Agent platform registration pending end-to-end verification" — the `/platformadmin/mcp/` registry in the separate `ai-agent-platform` repo has not been configured to point at this server, and `skills/general/price_tracker.md`'s `tools:` frontmatter has not been updated to the MCP-discovered tool names.
- **No agent has ever answered a real query via this MCP server.** Criterion 4 depends on criterion 3; since registration never happened, this was never attempted.

### Why this matters for Phase 5
ROADMAP.md's Phase 5 explicitly depends on "Phase 4 (MCP must be live and `priser` verified end-to-end before deleting source paths)". That precondition is NOT met. Phase 5 (deleting price-tracker code from `ai-agent-platform`) should not proceed on the assumption that Phase 4 is done — doing so would risk deleting the chat-tool fallback path in the source repo before its MCP replacement is actually reachable by the agent platform.

</decisions>

<code_context>
## Existing Code Insights

- `src/mcp_server/{__init__.py,auth.py,server.py}` — renamed from `src/mcp/` today (260706-rso); fastmcp bumped to 2.x, lifespan fixed (260706-t3p)
- `src/api/app.py` — mounts the MCP app at `/mcp` on the same FastAPI process as the admin UI
- No corresponding changes exist anywhere in `/home/magnus/dev/ai-agent-platform` (the separate agent-platform repo) — not inspected this session (out of this repo's scope), but ROADMAP.md's own text already flags this as pending

</code_context>

<specifics>
## Specific Ideas

None — executed directly, no discuss session.

</specifics>

<deferred>
## Deferred Ideas

- Building the Dokploy-managed Traefik + auth-middleware ingress (mcp.<domain> + prices.<domain> routing) — separate future work, pending Entra client registration
- Registering this MCP server in the agent platform's /platformadmin/mcp/ registry and updating skills/general/price_tracker.md — blocked on the ingress existing first (agent platform needs a real reachable host to register against) or could be done against the current /mcp path + edge network as an interim step, at the user's discretion

</deferred>
