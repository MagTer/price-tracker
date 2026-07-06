<!-- GSD:project-start source:PROJECT.md -->
## Project

**Price Tracker** — standalone Swedish grocery and pharmacy price tracker, extracted from the `ai-agent-platform` monolith at `/home/magnus/dev/ai-agent-platform`. Tracks prices at ICA, Willys, Apotea, Med24, and Doz; exposes its capabilities to the agent platform via an MCP server; runs alongside the agent platform behind the same Traefik proxy. Single-user (Magnus only); Entra ID is enforced at the upstream Traefik + auth-middleware ingress (managed via Dokploy), not inside this app.

**Core Value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.

**Source of truth for the extraction plan:** [EXTRACTION.md](./EXTRACTION.md) — verified LOC counts, file paths, locked decisions, and the full 5-phase plan. Read it before making structural decisions.

**Planning artifacts:** `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:PROJECT.md -->
## Technology Stack

Stack is **locked** by EXTRACTION.md §2 — deviations require touching ported logic and should be discussed before changing.

- **Language/runtime:** Python 3.12
- **Web:** FastAPI + uvicorn
- **DB:** PostgreSQL via SQLAlchemy 2.0 (asyncio) + Alembic; aiosqlite for tests
- **HTTP client:** httpx (async)
- **Email:** aiosmtplib (SMTP) — AWS SES is the alternative if needed
- **LLM:** OpenRouter direct at `https://openrouter.ai/api/v1` (OpenAI-compatible) — no LiteLLM proxy
- **MCP:** `fastmcp` (mounts on FastAPI)
- **Auth (UI):** IAP header trust — reads `X-Auth-Request-Email` forwarded by the upstream Traefik + auth-middleware ingress (managed via Dokploy); no in-app OIDC client
- **Auth (MCP):** static bearer token (`MCP_BEARER_TOKEN`)
- **Frontend:** server-rendered HTML + vanilla JS + Chart.js (ported from source — no SPA framework)
- **Tests:** pytest, pytest-asyncio
- **Packaging:** poetry
- **Container:** `python:3.12-slim` Docker image; docker-compose with Traefik labels
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. The extraction is a **byte-equivalent port** — preserve the source repo's conventions unless explicitly asked to change them. Source repo: `/home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/`.

Key constraints during extraction:
- **Don't fix known shortcomings** during the port (see EXTRACTION.md §10) — they are intentionally backlog for post-extraction.
- **Don't add features, refactor, or introduce abstractions** beyond what each phase requires.
- **Comments:** keep what's in the source; don't add new ones unless the WHY is non-obvious.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Target layout (from EXTRACTION.md §4):

```
src/
├── domain/        # ported verbatim from modules/price_tracker/
│   ├── models.py  service.py  scheduler.py  parser.py  notifier.py  result.py
│   ├── extractors/  (base.py, willys_api.py)
│   └── stores/    (__init__.py)
├── api/
│   ├── app.py     # FastAPI factory + lifespan starts scheduler
│   ├── auth.py    # IAP header trust (X-Auth-Request-Email) + single-email gate
│   ├── admin.py   # ported from admin_price_tracker.py (1,689 LOC, 14+ endpoints)
│   ├── schemas.py
│   └── templates/admin.html
├── mcp_server/
│   └── server.py  # FastMCP, bearer auth, 4 tools
└── infra/
    ├── fetcher.py  # httpx (replaces IFetcher)
    ├── email.py    # aiosmtplib (replaces IEmailService)
    ├── llm.py      # OpenRouter (replaces LiteLLM proxy)
    └── db.py       # async session factory
tests/
└── conftest.py + 5 test files (rebased fixtures from source)
alembic/versions/0001_initial.py  # squashed from 3 source migrations
```

**Data model (5 tables, prefix dropped):** `stores`, `products`, `product_stores`, `price_points`, `watches`. Tenant-scoped tables get `tenant_id` UUID column (replaces source's `context_id` FK).

**MCP surface:** `check_price`, `find_deals`, `compare_stores`, `list_products` — see EXTRACTION.md §5.

**Auth:** Admin UI trusts the `X-Auth-Request-Email` header forwarded by the upstream Traefik + auth-middleware ingress and validates it against `ALLOWED_ENTRA_EMAIL`; Entra ID enforcement itself happens at that ingress layer (managed via Dokploy — not yet built, pending Entra client registration), not in this app. `/mcp` endpoint uses static bearer token. See EXTRACTION.md §6 for background, though its original in-app-OIDC description was superseded by this IAP header-trust model (D-18/D-19 in .planning/STATE.md).
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, or `.github/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` — do not edit manually.
<!-- GSD:profile-end -->
