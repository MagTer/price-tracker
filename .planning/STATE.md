---
gsd_state_version: 1.0
milestone: extraction
milestone_name: Extract price-tracker from ai-agent-platform
current_phase: none
current_phase_name: "GSD retired — see § GSD status"
status: retired
stopped_at: "GSD wound down 2026-07-26. Every phase is closed; ONE external follow-up remains (Hermes MCP registration). Do not resume the pipeline — CLAUDE.md is the living document."
last_updated: "2026-07-26T00:00:00.000Z"
last_activity: 2026-07-29
last_activity_desc: "v0.42.0: the Erbjudanden row's product name became a button into the price-history modal (rule documented in CLAUDE.md's portal-UI bullet). Otherwise: GSD retired. This file is a decision log + open-items list, not a live pipeline. Product history since v0.13.0 lives in CLAUDE.md and git log — the old 3 000-character running commentary was deleted here on 2026-07-26 because it had become a list of stale 'deploy bump pending' claims contradicting the frontmatter. Latest decision: D-33 (admin/reader split, v0.29.0)."
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 16
  completed_plans: 16
  percent: 100
---

# Project State

> **GSD is retired for this repo (2026-07-26).** This file is no longer a live pipeline —
> it is (a) the **decision log** (D-19…D-32, referenced by name from CLAUDE.md) and (b) the
> **open-items list** below. Everything about how the product currently works lives in
> **CLAUDE.md**, which is maintained. Do not "resume" a phase from here, and do not mirror
> product changes into this file — that duplication is exactly what made the old
> `last_activity_desc` a 3 000-character contradiction of itself.

## GSD status — what is actually open

**Closed:** Phases 1–3 (skeleton, services, portal+IAP), Phase 4's build (MCP server: 4 tools,
bearer auth, live at `price.<domain>/mcp/`), Phase 04.1 (package data → store link; built,
verified, deployed — the old "deployed DB still stamped at old 0001" warning was resolved by
the volume drop), and **Phase 5**.

**Phase 5 closed as obsolete (2026-07-26):** it was "delete the price-tracker code from
`ai-agent-platform`". That repo no longer exists on the dev machine (`/home/magnus/dev` holds
no copy) — the platform was wound down, so there is no source tree left to clean. Verified by
absence, not by deletion.

**The one genuinely open item — MCP registration, reframed:**
The original Phase 4 tail was "register the MCP server with Hermes at `/platformadmin/mcp/`
in `ai-agent-platform`". That endpoint belonged to the retired platform. What runs in prod
today is the third-party gateway image `nousresearch/hermes-agent` (container `hermes` on the
Dokploy VM), and its compose in the home-server repo has **no MCP configuration at all**
(checked 2026-07-26). Magnus still wants price-tracker's MCP wired to it, so the task stands —
but against the third-party gateway, which is a different integration from the one the
original plan described. Nothing in this repo blocks it: the server is live and bearer-gated.

**Milestone verdict:** the extraction is complete. The product is standalone, deployed, and
maintained through ordinary releases (see CLAUDE.md § Releasing), not through GSD phases.

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

- 2026-07-26 D-33: **Två roller i stället för en tillåten adress (v0.29.0).** `ALLOWED_ENTRA_EMAIL` är nu **admin**; alla andra som passerar Entra-gaten blir **läsare** — varje GET öppen (inkl. `/export` och `/logs`, Magnus beslut), varje skrivande metod 403. Medlemskapet delegeras avsiktligt till Entra (tenant + `OAUTH2_PROXY_EMAIL_DOMAINS`); appen har ingen egen andra lista. Gaten är EN router-nivå-dependency (`require_admin_for_writes`) som nekar på HTTP-metod — deny-by-default, ingen per-route-markering att glömma — plus AST-grindar (AUTHZ-01/02) i `test_static_gates.py`. **Utlösare:** en inloggad familjemedlem fick vit sida med ordet "Found" — appens egen 403 kläddes om av ingressens `entra-auth-errors` (fångar 401-403, skriver bara om 401→302), verifierat i prod-loggen som `GET / … 403`. Konsekvens som överlever fixen: lägg aldrig användarbetydelse i en 403-detail (→ CLAUDE.md Gotcha 7). Den riktiga källfixen — smalna middlewaren till enbart 401 — ligger i home-server-repot, inte här, och är INTE gjord.

### Pending Todos

- **MCP registration against the `nousresearch/hermes-agent` gateway** — see § GSD status. The
  only open item in the whole plan.
- ~~`.env.template` still carries `SMTP_*` rows instead of `RESEND_API_KEY`/`EMAIL_FROM`~~ —
  **closed 2026-07-26.** It did, plus three dead `PRICE_PARSER_*` rows and a stale
  `mcp.<domain>` comment; it also lacked all 8 of the QUICKADD/SCHEDULER knobs. Rewritten
  against the code's actual env contract. **The Read deny on `.env.*` is real and stays** —
  the way through is `git show HEAD:.env.template` to read and a shell heredoc to write, so
  the global secrets guard never has to be loosened for a committed template.
- ~~UI gap: watches can be created and deleted, but not edited~~ — **closed in v0.27.0.**
  The dialog exists, and the endpoint behind it can now CLEAR a target (its `is not None`
  guards meant a target price could be set but never removed).

**Closed since this list was written:** the "Opus-kravställning" backlog (Dockerfile USER,
libpq trim, `/health` DB-ping, self-hosted Chart.js, N+1 in `list_products`, tenant/email
validation, import-replace in one transaction, stale Entra docstrings, README runbook,
escapeHtml quotes) was delivered across D-26, D-30 and D-31.

### Blockers/Concerns

**None open.** Kept for the record, with why each is dead:

- ~~Phases must run sequentially~~ — GSD retired; there are no phases left to sequence.
- ~~Email backend (SMTP vs SES) — decide during Phase 2~~ — decided: **Resend HTTP API**
  (D-32). `aiosmtplib` is gone from `pyproject.toml`.
- ~~MCP subdomain `mcp.<domain>` LOCKED (D-18)~~ — **superseded by D-29**: no subdomain; the
  MCP is served at `price.<domain>/mcp/` behind a path-scoped un-gated Traefik router. This
  entry contradicted a decision recorded 40 lines above it in this same file.
- ~~Edge-proxy / portal stack pending Entra client registration~~ — the ingress has been
  **live in production since 2026-07-09/10** (oauth2-proxy v7.15.2 + Traefik `forwardAuth`);
  see the 2026-07-13 FAKTAKORRIGERING entry above.
- ~~Phase 4 gap: ingress + agent-platform registration~~ — the ingress question died with
  D-29; the registration item is reframed and moved to Pending Todos above.

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

Several of these were quietly **built** while the table still called them deferred — corrected
2026-07-26 by checking the code, not the label.

| Category | Item | Status | Note |
|----------|------|--------|------|
| Reliability | REL-01 retry/backoff | **DONE** | Bounded retry on transient failures in `infra/fetcher.py`; scheduler +1h backoff on exception; a failed weekday check retries next MORNING (v0.39.0 `next_morning_retry` — superseded the old +24h clock drift) |
| Reliability | REL-04 rate limiting | **DONE** | `infra/rate_limiter.py` — one shared per-store ledger with jitter, used by scheduler AND interactive fetches |
| Reliability | REL-05 fallback threshold | **DONE** | `PRICE_PARSER_MIN_CONFIDENCE` acceptance floor (D-23) |
| Reliability | REL-02/03 (raw_response, soft-delete) | v2 backlog | `raw_data` column exists and is populated; soft-delete never needed at single-user scale |
| Extraction Quality | EXT-01 structured extractors | **DONE** | `WillysApiExtractor` (store REST API) + `JsonLdExtractor` (schema.org), ahead of the LLM cascade (D-22) |
| Extraction Quality | EXT-02 dedup | v2 backlog | — |
| Notifications | NOTF-01 (Telegram/push) | v2 backlog | Email via Resend covers the need today |
| Analytics | ANAL-01 (price trends/volatility) | **DONE** (v0.36.0) | `domain/stats.py` + the Prisutveckling page: per-product trends, matched-basket store comparison, offer quality — read-only by construction |
| i18n | I18N-01 (externalize sv-SE strings) | **won't do** | One language track, decided 2026-07-14: Swedish for users, English for developers. Externalizing is pointless for a single-user Swedish app |
| Edge proxy / portal | EDGE-01 (Traefik + oauth2-proxy) | **DONE / out of repo** | Live in prod since 2026-07-09; owned by the home-server repo, never this one |

## Session Continuity

**There is nothing to resume.** GSD is retired for this repo; the old resume pointer
(`/gsd-discuss-phase 2`, aimed at a phase completed months ago) would have sent a fresh
session to re-plan working, deployed code.

Start a session by reading **CLAUDE.md**. Ordinary work goes through ordinary commits and a
release tag (CLAUDE.md § Releasing); `.planning/phases/` and `.planning/quick/` are kept as an
**archive** of how the extraction was run, not as a queue.
