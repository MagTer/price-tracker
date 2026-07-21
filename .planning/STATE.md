---
gsd_state_version: 1.0
milestone: v7.15.2
milestone_name: milestone
current_phase: 04.1
current_phase_name: Package data moves to the store link
status: executing
stopped_at: Phase 04.1 built + verified (human_needed) — autonomous stopped here as instructed
last_updated: "2026-07-21T00:00:00.000Z"
last_activity: 2026-07-21
last_activity_desc: "v0.11.0 UX overhaul from a purpose-first review Magnus approved wholesale: deals page is now a DECISION surface (jfr-pris + best-alternative comparison per deal via a cross-link latest-price query, Swedish offer_type fallback, clickable store link, seen-date) and the route's stale 24h window became 7d matching the service (Gotcha 4 drift — page was empty Tue-Sun); store names link out everywhere (UI had ZERO outbound links before); freshness line on Erbjudanden from /scheduler/status (new last_check_at/next_check_at bounds); watches show current lowest price/store + under-målet highlight and prefill the user's email; mobile-first pass (sidebar→top bar <768px, scrollable table containers, bottom-sheet modals) because the app doubles as the IN-STORE shopping list (Magnus webhandlar inte); headings say jfr-pris (shelf-label term, glossary updated); stat boxes link to their pages; weekly email deals rows link to store page + show jfr-pris. Suite: 347+12. Deploy bump: go straight to v0.11.1 (v0.11.1: Butiker column shows a distinct-store COUNT — the linked name list swallowed the table; names live behind Länkar). Prior: v0.10.0 UI-gap closure: product edit dialog (Redigera on the row; name/brand/category via previously UI-less PUT /products, unit LOCKED — removed from ProductUpdate, delete+recreate to change it, blank-name 400 guard added) and link-edit dialog now also edits cadence (Kontrolldag+frequency via previously UI-less PUT /product-stores/{id}/frequency, sent only when changed since the server reschedules next_check_at; cadence shown under last-checked in the links table; get_links_for_product payload gained check_frequency_hours/check_weekday). Remaining known UI gap: PUT /watches has no edit dialog (create/delete only). Suite: 334+12. Deploy bump pending (go straight to v0.10.0). Older: v0.9.0 weekday check schedule in quick-add (OFFER_WEEKDAYS Monday prefill for ICA/Willys), v0.8.x shareability + footer fix. .env.template still not updatable from here (permission-blocked)."
progress:
  total_phases: 6
  completed_phases: 5
  total_plans: 16
  completed_plans: 16
  percent: 83
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-04)

**Project code:** PT
**Core value:** After extraction, the agent platform's `priser` skill keeps working end-to-end via MCP-discovered tools served by this standalone repo, with all price-tracker code removed from the agent platform.
**Current focus:** Phase 04.1 (package data -> store link) is BUILT and verified: 214 tests green, no code gaps. Status human_needed — 4 operator items await Magnus (see 04.1-VERIFICATION.md + 04.1-08-SUMMARY.md § Operator checkpoint). MOST IMPORTANT: the deployed DB is still stamped at the OLD 0001, so `alembic upgrade head` silently applies NOTHING until the volume is dropped — see README.md § Schema reset (Phase 04.1).

## Current Position

Phase: 04.1 (Package data moves to the store link) — EXECUTING
Plan: 1 of 8
Status: Executing Phase 04.1
Last activity: 2026-07-21 — v0.8.0 shareability, on top of the v0.5.0–v0.7.1 quick-add/label/sibling/UI work: a friend wants their own instance, and the decision is SEPARATE INSTANCES over multi-tenant (service-layer reads have zero tenant filters; tenancy was deliberately dropped in D-02/D-03 — reversing it is a security audit, not a flag). To make the public repo genuinely reusable: butik config moved to env (QUICKADD_STORE_LABELS / QUICKADD_SIBLING_GROUPS, Magnus's butiker as in-repo defaults so his deploy needs no new vars), MIT LICENSE added, README "Run your own instance" section written. Erbjudanden also moved to the top of the menu and made the start page. GSD bypassed at Magnus's request. v0.8.1 followed immediately: the footer version was blank in prod — the image installs --no-root (no package metadata) AND never copied pyproject.toml, so both version sources failed silently; fixed by copying pyproject.toml into the image, verified by building the image locally and asserting the rendered footer. Deploy bump pending (go straight to v0.8.1). v0.9.0 then closed the quick-add cadence gap Magnus reported: the confirm form only offered an hour interval although QuickAddCreate had accepted check_weekday since v0.5.0 — now a weekday select (Swedish day names, interval field hidden when a day is fixed), prefilled Monday for ICA/Willys from quickadd.OFFER_WEEKDAYS because those chains publish weekly offers on Mondays and a weekly check on that day is the lightest schedule against their sites. Siblings already inherited the primary's cadence, so sister-butik links get the same Monday. The manual link form's numeric 0-6 weekday input became the same select. Deploy bump target is now v0.9.0.

Progress: [███████░░░] 70% (Phases 1-3 of 5 complete; Phase 4 partial — MCP server built and tested, agent-platform wiring pending)

## Performance Metrics

**Velocity:**

- Total plans completed: 5
- Average duration: ~11 min
- Total execution time: ~54 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Skeleton + Domain Copy | 5/5 | ~54 min | ~11 min |
| 2. Service Infrastructure | 1/1 | — | — |
| 3. Admin UI + IAP Header Trust | 1/1 | — | — |
| 4. MCP Server + Agent Wiring | 1/1 | — | — |

**Recent Trend:**

- Phases 2-4 completed in a single autonomous session
- New test coverage: 2 test files (~120 LOC) covering admin API and MCP tools

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Initialization: Single-user (Magnus-only) auth with `tenant_id` UUID column kept for future
- Initialization: Agent integration via MCP server (not REST), FastMCP library
- Initialization: OpenRouter direct (no LiteLLM proxy)
- Initialization: Squash 3 source migrations into one initial migration; drop `price_tracker_` table prefix
- Plan 01-01: Adapted pyproject.toml to Poetry 2.x PEP 621 `[project]` table (deprecation-warning fix); kept `[tool.poetry] packages = [...]` for src-layout — same dep set, same Phase 1 minimums (Rule 3 deviation)
- Plan 01-03: Removed redundant `uq_store_slug` named UniqueConstraint from squashed migration — the unique index `ix_stores_slug` produced by `mapped_column(unique=True, index=True)` already enforces slug uniqueness; the named constraint was reported as drift by `alembic check` (Rule 1 deviation, kept migration faithful to ORM metadata)
- Plan 01-03: Used Alembic async template (env.py uses `async_engine_from_config` against the `postgresql+asyncpg://` URL) — keeps alembic CLI URL identical to runtime URL, avoids dual sync/async driver config
- Plan 01-04: No `tests/conftest.py` created — source repo had none in `tests/`, each test file constructs its own MagicMock/AsyncMock fixtures inline. Adding a conftest would have been an unsolicited refactor (verbatim port doctrine).
- Plan 01-05: Bumped Dockerfile `POETRY_VERSION` from plan-spec 1.8.3 to 2.3.2 to match the project's PEP 621 `[project]` table (Plan 01-01 deviation continuation) — Poetry 1.8.3 rejected the manifest with "fields ['authors', 'description', 'name', 'version'] are required in package mode" (Rule 1 deviation, fix folded into Task 1 commit)
- Plan 01-05: Added `!.env.template` exception to `.gitignore` so the env-var-contract template can be committed (was matched by `.env.*` rule). Naming convention preserved per plan spec (Rule 3 deviation)
- 2026-05-04 D-19 reassess: Locked MCP subdomain (`mcp.<domain>`) over `/mcp` path because IAP auth-bypass is per-host. Locked IAP header trust (`X-Auth-Request-Email`) as the Phase 3 auth model — drops `fastapi-azure-auth`, `pyjwt`, `cryptography` from this repo permanently. Locked edge-proxy stack as out of the price-tracker extraction milestone (does NOT belong inside extraction milestone; hosting target corrected 2026-07-06 — see D-20 below).
- 2026-07-06 D-20 reassess: Corrected edge-proxy ingress hosting description from a standalone hand-built VM to Dokploy-managed ingress. Architecture unchanged — still Traefik + auth-middleware (oauth2-proxy-style header injection) terminating Entra OIDC and forwarding `X-Auth-Request-Email`; only the hosting/ownership model changed (Dokploy manages it, not a hand-built separate-repo stack). EDGE-01 remains out of the price-tracker extraction milestone's build scope; ingress is not yet built, pending Entra client registration.
- 2026-07-06 D-21: Retroactively backfilled CONTEXT/PLAN/SUMMARY/VERIFICATION for Phases 2-4 (quick task 260706-w69) to reconcile GSD phase-tracking with actual delivered code (all 3 phases were implemented directly in commit d92372a without the formal pipeline, so `.planning/phases/` had no directories for them and GSD's tracking recommended a fresh `/gsd-discuss-phase 2` against already-working code). Phase 2/3 verified passed with 1 flagged live-check caveat each (Willys live price check; live UI walkthrough) — accepted per user decision. Phase 4 verified gaps_found: agent-platform registration and `mcp.<domain>` ingress genuinely not done. Corrected ROADMAP.md checkbox/progress-table and REQUIREMENTS.md's MCP-05/INFRA-06 traceability entries accordingly. Note: **D-20 is reserved** (not yet committed) for the still-pending edge-proxy Dokploy reassess quick task (260706-tq5) — use **D-22** for the next new decision, not D-20.

- 2026-07-13 D-22: JSON-LD-extraktionssteg tillagt — kedjan är nu butiks-API → JSON-LD (schema.org Product/Offer ur rå HTML) → LLM-kaskad. Verifierat mot live-produktsidor hos alla fyra LLM-beroende butiker (ICA handlaprivatkund utan inloggning, Apotea, Med24, DOZ). WebFetcher returnerar nu rå `html` vid sidan av extraherad text. Motiv: exakta priser utan LLM-kostnad/hallucination, samma nätverksavtryck (Magnus 2026-07-13: manuell URL-inmatning är avsiktlig anti-block-policy — ingen produkt-discovery ska byggas). Commit 9337f3d.
- 2026-07-13 D-23: `PRICE_PARSER_MIN_CONFIDENCE` (default 0.6) acceptansgolv — LLM-extraktioner under golvet kasseras (price_sek=None → callers hoppar över lagring) i stället för att sparas. Stänger REL-05 (fallback-tröskel 0.0). Commit 9337f3d.
- 2026-07-13 D-24: MCP-endpointen failar stängt — utan `MCP_BEARER_TOKEN` svarar middlewaren 503 på allt (tidigare monterades appen oskyddad med bara en log-warning). hmac.compare_digest för tokenjämförelse. Motiv: containrar på delade `dokploy-network` når appen direkt förbi Traefik-gaten (ADR-009 i home-server accepterar den risken förutsatt att appar har egen auth). Commit 1815757.
- 2026-07-13 D-25: MCP-endpointens path fixad till `/mcp/` (fastmcp:s interna default `/mcp` dubblades av FastAPI-mounten till `/mcp/mcp/`); extern URL blir `https://mcp.<domain>/mcp/` per D-18. Commit 3d42ab8.
- 2026-07-13 D-26: Robusthetspaket — DB-medveten `/health` (SELECT 1, 503 vid nere), N+1 i list_products ersatt med batchade queries + row_number-window (verifierad mot riktig Postgres), import replace-läge i en transaktion, tenant/email-validering i create/update-endpoints. Commit c4e759b. Scheduler: per-item-sessioner i `_check_due_products` så rate-limit-sovningar aldrig håller DB-anslutning; schemauppdatering via explicit UPDATE på detachade rader. Commit 965d2b4.
- 2026-07-13 D-27: home-server-branch `feat/price-tracker-mcp-route` (pushad, INTE mergad — Magnus granskar): path-scopad Traefik-router `Host(mcp.DOMAIN) && PathPrefix(/mcp)` utan entra-middleware (ADR-009-undantag dokumenterat), env-passthrough för ALLOWED_ENTRA_EMAIL/MCP_BEARER_TOKEN/OPENROUTER_*/SMTP_* (composen skickade tidigare bara DATABASE_URL — admin-UI:t hade 403:at allt), image-pin bumpad till v0.2.0, mcp.falle.se i zonfilen. Operatörssteg i commit-meddelandet (bbd43c1): tagga v0.2.0, Cloudflare-DNS, Dokploy-env + SOPS.
- 2026-07-13 D-28: Portal + API flyttade från `/admin`-prefixet till roten (prefixet fanns bara för att OpenWebUI ägde `/` i källplattformen; Magnus beslut). Gamla `/admin`-URL:er 308-redirectar till `/`. Commit 4722f49 (+ home-server-kommentar 74eb6fa på MCP-branchen).
- 2026-07-13 D-29 (SUPERSEDES D-18): Ingen mcp-subdomän — MCP:n serveras på `https://price.<domain>/mcp/`. D-18 låste `mcp.<domain>` utifrån antagandet att IAP-bypass är per host; med Traefik forwardAuth är bypass per router, så en path-scopad ogatad router (`Host(price) && PathPrefix(/mcp)`, explicit priority=100 över den gatade Host-routern) räcker (Magnus beslut). Sparar DNS-post, cert och ett operatörssteg. home-server-branch dff244d; app-repots docstrings uppdaterade.
- 2026-07-13 D-30: Deploy-readiness för v0.2.0 — containern kör som uid 1001 (icke-root), libpq bortstädat, Chart.js self-hostad på `/static` (ingen CDN-dependens i portalen). Hela imagen röktestad lokalt mot dev-postgres: migrations seedar 5 butiker, /health db:true, auth-gate 403/200/403, /admin 308→/, /mcp/ 401 utan bearer + fullt MCP initialize-handskak med. Commit 8d7ad3c. Inga deploy-blockerare kvar från djupanalysen.
- 2026-07-13 D-32: E-postbackend bytt från SMTP/aiosmtplib till Resend HTTP-API (`ResendEmailService`, env `RESEND_API_KEY` + `EMAIL_FROM`) — Magnus har ingen SMTP-relay; källplattformen använde Resend. IEmailService-protokollet oförändrat. aiosmtplib borttagen ur pyproject. Ersätter EXTRACTION.md §2:s låsta "aiosmtplib (SMTP)"-val. App-commit 5e53bdc; home-server-composens env-passthrough uppdaterad (2274d0c); CLAUDE.md/REQUIREMENTS-footprint städad i denna commit. KVARSTÅR: `.env.template` (skrivskyddad för agenter via permissions) har ev. SMTP_*-rader — Magnus byter själv till RESEND_API_KEY/EMAIL_FROM.
- 2026-07-13 FAKTAKORRIGERING: Entra-ingressen ÄR byggd och i produktion sedan 2026-07-09/10 (home-server-repot: oauth2-proxy v7.15.2 + Traefik forwardAuth `entra-auth@file`, authResponseHeaders X-Auth-Request-User/Email, email-claim = preferred_username/UPN). Dokploy-composen för price-tracker (host `price.${DOMAIN}`, pinnad GHCR-tag, alembic-migrering i command, healthcheck) finns i home-server `compose/dokploy-apps/price-tracker/`. Detta gör tidigare "ingress not built"-skrivningar i STATE.md/CLAUDE.md/ROADMAP.md inaktuella — städning delegerad till Opus-kravställning. OBS: `ALLOWED_ENTRA_EMAIL` måste matcha UPN, inte nödvändigtvis gmail-adressen.
- 2026-07-13 D-31: Opus städ-/härdningssession (OPUS-HANDOFF.md uppgift 3–8, utan formell GSD-pipeline, atomiska commits + pytest-gate 113→117 gröna). (3) docs-faktakorrigering: admin.py-docstrings IAP-header-trust i st f Entra-roll + döda `admin:`-args borttagna, CLAUDE.md/ROADMAP/REQUIREMENTS uppdaterade till live-ingress + `/mcp`-path (D-28/D-29), docker-compose.yml märkt local-dev-only (commit 26f20fe). (4) README-runbok med env-var-kontrakt, säkerhetsmodell och release-flöde (commit a295e2f). (5) admin.py: path-param-typer `str|None`→`str`, 5 redundanta per-route `require_auth`-deps borttagna (commit 239f46d). (6) escapeHtml escapar nu citattecken + produkt-action-knappar flyttade till data-attribut + delegerad listener — History-knappen quote-säker (verifierad via node-harness), commit f6ec6c4. (7) IFetcher krympt till fetch()+close(), döda search/research-stubbar borttagna (commit 6ed9f4f). (8) Willys comparePrice strippar enhetssuffix (`kr/kg`,`kr/st`) före Decimal + tester (commit 119f2f2). Mid-task-ändring från Magnus: e-postbackend byter SMTP→Resend HTTP-API (implementeras av huvudsessionen på samma branch); README dokumenterar RESEND_API_KEY+EMAIL_FROM, inga SMTP_*. Kvarvarande SMTP/aiosmtplib-omnämnanden i CLAUDE.md-stacken + REQUIREMENTS INFRA-02/DEPLOY-01/04 lämnade orörda (tillhör huvudsessionens email-swap-beslut).

### Pending Todos

- Opus-kravställning (skrivs när Magnus ber om den): Dockerfile USER + stale kommentar + libpq-trim, /health med DB-ping, självhostad Chart.js, N+1 i list_products, tenant/email-validering i create-endpoints, import-replace i en transaktion, stale Entra-docstrings + inaktuella ingress-referenser i CLAUDE.md/ROADMAP/STATE, README-runbok, escapeHtml-quotes i admin.html.
- Hermes-registrering av MCP-servern (`/platformadmin/mcp/` i ai-agent-platform) — kvarstående Phase 4-gap, görs efter att MCP-routen finns i home-server.

### Blockers/Concerns

- Phases must run sequentially despite `parallelization=true` in config — each gate is a precondition for the next phase's work. Plans within a phase may parallelize.
- Email backend (SMTP via aiosmtplib vs AWS SES) — decide during Phase 2
- MCP subdomain (`mcp.<domain>`) is now LOCKED for Phase 4 (was: TBD); IAP per-host bypass is the rationale (D-18)
- **Edge-proxy / portal stack** is operated within Dokploy's managed scope, out of this repo's build scope (D-18, EDGE-01; hosting reassessed 2026-07-06 per D-20). Phases 3 + 4 ASSUME the IAP exists; if it does not exist when Phase 3 lands, Phase 3 still ships behind a "trust the header" dependency and the operator runs the app in a private network until the Dokploy-managed ingress is built (pending Entra client registration).
- **Phase 4 gap (2026-07-06 retroactive verification):** The MCP server itself works and is tested (4 tools, bearer auth), but the `mcp.<domain>` ingress (Dokploy-managed, not yet built) and agent-platform `/platformadmin/mcp/` registration (separate `ai-agent-platform` repo, never attempted) are NOT done. Phase 5 explicitly depends on "MCP must be live and `priser` verified end-to-end" — this precondition is unmet. Do not start Phase 5 until this gap is closed or the dependency is explicitly re-scoped. See `.planning/phases/04-mcp-server-agent-wiring/04-VERIFICATION.md` for the full gap summary.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260706-rso | Fix 4 pre-Phase-5 blockers: mcp/mcp_server package collision, doubled /v1 in OpenRouter URL, stale LiteLLM model aliases, fastmcp 1.0→2.x bump | 2026-07-06 | dd547bd | [260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-](./quick/260706-rso-fix-4-pre-phase-5-blockers-1-rename-src-/) |
| 260706-t3p | Fix CLAUDE.md stale mcp/ reference + propagate MCP sub-app lifespan into create_app() so the streamable-HTTP session manager actually starts | 2026-07-06 | 7a3127b | [260706-t3p-fix-2-issues-flagged-after-quick-task-26](./quick/260706-t3p-fix-2-issues-flagged-after-quick-task-26/) |
| 260706-tha | Fix 4 stale Entra OIDC references in CLAUDE.md to match the locked IAP header-trust auth model (X-Auth-Request-Email via Dokploy-managed Traefik+auth-middleware ingress, not yet built) | 2026-07-06 | d094d70 | [260706-tha-fix-4-stale-entra-oidc-references-in-cla](./quick/260706-tha-fix-4-stale-entra-oidc-references-in-cla/) |
| 260706-w69 | Backfill retroactive GSD phase artifacts for Phases 2-4 (implemented outside the formal pipeline); discovered and corrected Phase 4's optimistic "Complete" marking to gaps_found (agent-platform registration + mcp.<domain> ingress not done) | 2026-07-06 | d1ae100 | [260706-w69-backfill-retroactive-gsd-phase-artifacts](./quick/260706-w69-backfill-retroactive-gsd-phase-artifacts/) |
| 260706-tq5 | Reassess edge-proxy/ingress hosting (EDGE-01, D-18) across PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md: corrected from a standalone hand-built VM to Dokploy-managed ingress (architecture and IAP header-trust auth unchanged); recorded D-20 | 2026-07-06 | e8e208c | [260706-tq5-reassess-edge-proxy-plan-edge-01-d-18-ac](./quick/260706-tq5-reassess-edge-proxy-plan-edge-01-d-18-ac/) |
| 260714-hui | Ett språkspår: svenska. Försvenskade all användarsynlig text — ~91 UI-strängar i admin.html + de 40 användarvända HTTPException-detaljerna + `lang="sv"`. Skarven gick exakt mellan ärvt (portad Fas 3 = engelsk) och nyskrivet (e-post, MCP, 04.1 = redan svenska). Kodspråk förblir engelska (identifierare, DB-kolumner, loggar, commits). Skarv-grind: 17 träffar → 0 | 2026-07-14 | 0209006 | [260714-hui-ett-sprakspar-forsvenska-all-anvandarsyn](./quick/260714-hui-ett-sprakspar-forsvenska-all-anvandarsyn/) |
| 260714-gbn | Fix price-history view (post-04.1 bug found in prod): it plotted every link of a product as ONE absolute-price line, so a 16-pack followed by a 24-pack read as "the price is increasing". Now: per-link series + a bold forward-filled "cheapest kr/unit available" line + price/unit toggle + Package & kr/unit table columns. **Required an additive API change** — `PricePointResponse` was silently stripping `product_store_id`/`package_size`/`package_quantity` at the wire boundary, so the grouping key never reached the frontend (the admin route runs its own duplicate query, NOT `service.get_price_history`, which only MCP calls) | 2026-07-14 | c343862 | [260714-gbn-fix-price-history-view-per-link-series-b](./quick/260714-gbn-fix-price-history-view-per-link-series-b/) |
| 260714-x48 | Flödesanalysens elva åtgärder: LLM-berikning vid två triggers (första lyckade check → paketfält/D-07-autofyll; prisfall → erbjudandeklassning; enbart JSON-LD-källa, samma HTML), scheduler-retry-semantik (+1h backoff vid exception i egen session — stoppar 5-min-hamringsloopen; +24h i st f +7d för failad veckodagslänk), veckomail (trasiga restart-guarden borttagen, "Lägsta pris" = lägsta kr/enhet över senaste punkt per länk med märkt absolutpris-fallback), deals = senaste punkt per länk ≤7d (ersätter 24h-fönstret), MCP list_products får riktiga butiksnamn via batchad `get_store_names_by_product`, döda `service.check_price` raderad + EN `perform_price_check`-flöde (scheduler + admin delegerar, JSON-kontrakt pinnat av tester), check_price-docstring säger "senast observerade pris", målpris-fältet borta ur skapa-bevakning-formuläret (kr/enhet-mål styr; befintliga watches orörda), å/ä/ö i alla mailmallar, OPENROUTER_API_KEY-startvarning, JSON-LD name-sanity (0 tokenöverlapp → LLM-fallback). 252 tester (35 nya). **Släppt som v0.4.0** | 2026-07-15 | 4bcef78, 1475bb9, 145e226, 32ac24a | [260714-x48-flodesanalys-atgarder-llm-berikning-sche](./quick/260714-x48-flodesanalys-atgarder-llm-berikning-sche/) |
| 260714-jov | Produktlistans "Senaste pris" → "Lägsta kr/enhet". Kolumnen visade `stores.find(s => s.price_sek != null)` — första länken i en array från en query **utan `ORDER BY`**, alltså icke-deterministisk (inte "senaste"), ojämförbar mellan packstorlekar (24-pack slår alltid 12-pack absolut) och blind för rea (läste `price_sek`, inte erbjudandet). Nu: minimum av serverberäknat `unit_price_sek` (D-03) + vinnande länkens butik/förpackning/effektiva pris, badge för länkar utan mängd, fallback till lägsta absoluta pris märkt "kr/enhet saknas". `store_unit_price_sek` orörd (D-05). **Följdfix i samma task:** båda `admin.py`-vägarna saknade `ORDER BY` — `stores`-arrayen kom i Postgres godtyckliga radordning medan frontenden läste `stores[0]` som om positionen betydde något. Nu samma ordning som domäntjänsten redan ger (billigast kr/enhet först, utan mängd sist), sorterad på oavrundad Decimal, och de två byte-identiska dict-byggarna slogs ihop till en `_link_payload` (Gotcha 4-drift). 3 nya tester, verifierade röda mot gamla koden. **Släppt som v0.3.3** | 2026-07-14 | 998a9fd, 19118e6, b758503 | [260714-jov-lagsta-kr-enhet-i-produktlistan](./quick/260714-jov-lagsta-kr-enhet-i-produktlistan/) |

### Roadmap Evolution

- Phase 04.1 inserted after Phase 4: Package data moves to the store link — package_size + package_quantity move Product -> ProductStore; unit stays on Product. Runs before Phase 5 (source-repo cleanup). Brief: .planning/SEED-package-data-moves-to-link.md (URGENT)

## Deferred Items

Items acknowledged and carried forward (v2 / post-extraction backlog from REQUIREMENTS.md):

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Reliability | REL-01..05 (retry/backoff, raw_response, soft-delete, rate limiting, fallback threshold) | v2 backlog | Init |
| Extraction Quality | EXT-01..02 (structured extractors, dedup) | v2 backlog | Init |
| Notifications | NOTF-01 (Telegram/push) | v2 backlog | Init |
| Analytics | ANAL-01 (price trends/volatility) | v2 backlog | Init |
| i18n | I18N-01 (externalize sv-SE strings) | v2 backlog | Init |
| Edge proxy / portal | EDGE-01 (Traefik + oauth2-proxy + Homepage, operated within Dokploy's managed scope) | Out of milestone | 2026-05-04 (D-19 reassess); hosting corrected 2026-07-06 (D-20) |

## Session Continuity

**Resume file:** .planning/phases/04.1-package-data-moves-to-the-store-link/04.1-08-SUMMARY.md

Last session: 2026-07-14T00:49:29.769Z
Stopped at: Phase 04.1 built + verified (human_needed) — autonomous stopped here as instructed
Resume command: `/gsd-discuss-phase 2` (or continue `/gsd-autonomous` from current main thread)
