# Decisions and open items

Why price-tracker is as it is, where the reason is a decision rather than a comment beside
the code. Moved here from the retired GSD directory `.planning/` by the information-layout
move (its plan, U4, deleted when done: `git show e7873e7:docs/planning/INFORMATION-LAYOUT.md`). The entries are VERBATIM — several are in
Swedish, as they were written; new entries are written in English.

**Everything else that was in `.planning/`** — PROJECT, REQUIREMENTS, ROADMAP, the 04.1 seed,
OPUS-HANDOFF, `phases/` and `quick/` (plans, summaries, verification reports, research) — is
history of how the extraction was run, and left the tree in the commit after `f78ab77`. Read
it there: `git ls-tree -r --name-only f78ab77 .planning` lists it,
`git show f78ab77:.planning/STATE.md` prints one file.

**Three numbering series collide, so a citation names its series.** `D-19`…`D-34` below are the project decision log. The phase-1 context (further down) has its own `D-01`…`D-19`, and phase 04.1 its own `D-01`…`D-15`. A citation of a low number says which: `01 D-07` is phase 1's ("hand-derive the migration"), `04.1 D-07` is phase 04.1's ("never overwrite") — the operator's choice of 2026-11-01. Every citation in a tracked file other than this one was classified on 2026-11-04 by reading the citing comment's subject against both lists; that is a record of that day and nothing gates it, so a new citation names its series by hand, and a bare low number is unclassified rather than the log's. A bare `D-20` or above means the log. `01 D-18` follows the log's own D-25 and D-29, which read phase 1's edge-stack decision as the `mcp.<domain>` plan, although the lock itself is recorded at the log's D-19 entry. The entries below are verbatim and keep their bare numbers.

## Open items

**The extraction is finished (closed out 2026-07-26).** One follow-up remains, and it is not in this repo:

- **MCP registration — open, but reframed.** The plan said "register the MCP server with Hermes at `/platformadmin/mcp/` in `ai-agent-platform`". That platform has been wound down; what runs in prod is the third-party gateway image `nousresearch/hermes-agent`, whose compose in the home-server repo carries **no MCP configuration** (checked 2026-07-26). Wiring price-tracker's MCP to *that* gateway is still wanted. Nothing here blocks it — the server is live and bearer-gated at `price.<domain>/mcp/`.

- **Phase 5 (delete the source code from `ai-agent-platform`) is closed as obsolete** — the repo no longer exists on the dev machine, so there is no source tree left to clean. Do not go looking for it.

### Pending todos (from `.planning/STATE.md`, verbatim)

- **MCP registration against the `nousresearch/hermes-agent` gateway** — see § GSD status. The
  only open item from the original plan.

- **ICA:s måndagsfönster — flytta det före frukost?** Öppen fråga med mätning bakom sig, och
  mätningen låg utanför repot i elva dagar innan den skrevs ned (2026-08-14): en engångsprobe
  natten 2–3 augusti 2026 (`docs/experiments/ica-night-probe/`) visar att ICA byter priser strax
  efter midnatt, inte på morgonen — Bregott Maxi 39,90 → 48,95 någon gång mellan söndag 22:30
  och måndag 00:15, sedan stilla till 06:00. Fönstret 06–12 kan alltså kollas så sent som
  klockan tolv, så måndagens erbjudanden kan saknas i köplistan vid frukost och veckomailet
  går 12:00 per konstruktion. **Inte genomfört, med flit:** en av fyra produkter rörde sig
  (n = 1 vecka), rörelsen var en kampanj som TOG SLUT (pris UPP) vilket inte bevisar att en ny
  kampanj dyker upp samtidigt, och en 03:00-körning fick 404 på en produkt som svarade fint
  06:00 — nattrafik kan möta en annan vägg. Vägen framåt är att köra proben bredare, inte att
  resonera vidare. Underlaget och förbehållen står i CLAUDE.md:s schedule-punkt [since the layout move: the `store-fetching` skill, Schedule]; rådata i
  `docs/experiments/ica-night-probe/results.jsonl` — **flyttat in i repot 2026-09-06**, för
  att det låg på en dev-VM som numera går att ersätta med ett kommando, och den här raden
  pekade dit med absolut sökväg. Det städas när fönsterfrågan är avgjord, inte av en
  ombyggnad.

- ~~The per-product drill-in as a routed PAGE instead of the modal~~ — **decided and shipped
  as something smaller, v0.56.0 (2026-08-13).** The page was refused on a cost the proposal
  never counted: `renderPage` toggles `.app-page` visibility, the hidden page leaves the flow,
  the document collapses and the scroll resets — so returning from a product page would drop
  the reader at the TOP of Att köpa, the one list swept top to bottom standing in a shop. The
  wins that were real (a pasteable URL, a working back button) turned out to be ONE mechanism
  and about a hundred lines: `#/<page>?produkt=<id>`, opened by pushing a history entry.
  Escape closes the read-only drill-ins too. The sidebar question the entry below used to ask
  is moot — there is no sidebar. Details in CLAUDE.md's history-modal bullet [since the layout move: the `portal-ui` skill], including why
  the page also does not buy a bigger graph below ~1500 px.

  Note for whoever revisits this: Prisutveckling's rows were listed as a third entry point
  into the drill-in. They never were one — only Att köpa's product name and Produkter's
  Historik open it. Adding it there is a separate, one-line question.
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

### Deferred items (from `.planning/STATE.md`, verbatim)

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

## Decision log, D-19…D-34 and the unnumbered decisions before them (from `.planning/STATE.md`, verbatim)

**The numbered decisions live HERE, in this list — not in PROJECT.md.** That GSD boilerplate
("Decisions are logged in PROJECT.md Key Decisions table") stood here until 2026-08-14 and was
false: PROJECT.md's table holds none of D-19…D-34, stopped being updated in 2026-07, and still
records the `mcp.<domain>` subdomain as "Locked" — a decision D-29 below supersedes. A reader
sent there for the decision log gets the opposite of the current one.

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

- 2026-07-26 D-33: **Två roller i stället för en tillåten adress (v0.29.0).** `ALLOWED_ENTRA_EMAIL` är nu **admin**; alla andra som passerar Entra-gaten blir **läsare** — varje GET öppen (inkl. `/export` och `/logs`, Magnus beslut), varje skrivande metod 403. Medlemskapet delegeras avsiktligt till Entra (tenant + `OAUTH2_PROXY_EMAIL_DOMAINS`); appen har ingen egen andra lista. Gaten är EN router-nivå-dependency (`require_admin_for_writes`) som nekar på HTTP-metod — deny-by-default, ingen per-route-markering att glömma — plus AST-grindar (AUTHZ-01/02) i `test_static_gates.py`. **Utlösare:** en inloggad familjemedlem fick vit sida med ordet "Found" — appens egen 403 kläddes om av ingressens `entra-auth-errors` (fångar 401-403, skriver bara om 401→302), verifierat i prod-loggen som `GET / … 403`. Konsekvens som överlever fixen: lägg aldrig användarbetydelse i en 403-detail (→ CLAUDE.md Gotcha 7 [since the layout move: AGENTS.md Gotcha 7]). Den riktiga källfixen — smalna middlewaren till enbart 401 — ligger i home-server-repot, inte här, och är INTE gjord.

- 2026-08-13 D-34: **Tre "öppna spakar" stängda på mätning i stället för på magkänsla.** Alla tre stod bara i CLAUDE.md, aldrig här, och lästes därför som obeslutade. Underlaget är `check_attempts` i prod, 2026-07-27 → 2026-08-13 (395 kontroller: 382 ok, 5 blocked, 8 fetch_failed). (1) **WAF-sidecaren byggs inte.** Alla 5 blockeringar är ICA, fyra av dem samma dag (2026-07-29, före v0.39.0:s stratifiering) och den femte klarnade på fem minuter genom brytarens egen cooldown. **Exakt en av fem låg på människovägen** — quick-add-previewen som utlöste spiken — och ingen sedan dess. En Playwright-sidecar för en händelse var fjortonde dag som självläker är fel pris. Mätningen från 2026-07-29 säger att token FUNGERAR, inte att den BEHÖVS; de fyra fakta den gav ligger kvar i CLAUDE.md [since the layout move: the `store-fetching` skill, the aws-waf-token bullet] om väggarna kommer tillbaka. (2) **curl_cffi är bevisad**, inte längre "promising" — och receptet för att mäta den var fel: `docker logs | grep 'Bot wall'` nollställs vid varje deploy, så en omdeployad dag svarar "0 väggar" och läses som bevis (hände under just den här verifieringen, mot en 20 minuter gammal container). `check_attempts.outcome='blocked'` är den beständiga mätningen. (3) **Döda länkar är hanterade** — `link_health` → Fel & luckor → radering för hand; enda prod-fallet var en ICA-länk som 404:ade fem morgnar och sedan togs bort. Automatisk pensionering är AVGJORT NEJ: varje tröskel raderar en länk under ett butiksavbrott, och URL:en är länkens identitet med historiken hängande i den. (Prod har 100 länkar, 0 inaktiva — inget har någonsin pensionerats i stället för raderats.)

## Phase 1 decisions, D-01…D-19 (from `.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md`, verbatim)

### Default tenant identity
- **D-01:** Hardcoded `DEFAULT_TENANT_ID: uuid.UUID = uuid.UUID("f21b6620-c793-46e3-a354-dfcd9956b4a2")` constant in a small module — proposed location `src/domain/tenant.py`. Single source of truth referenced by the migration seed, all domain queries that filter by `tenant_id`, and all tests that need to insert tenant-scoped rows.
- **D-02:** No `DEFAULT_TENANT_ID` env var. Single-user, single-deployment app — env-var indirection adds risk of mismatch between migration seed and runtime config without buying any flexibility we'll actually use.
- **D-03:** The seed migration inserts a row into a `tenants` table? **No.** There is no `tenants` table in the source schema. The source's `context_id` was an FK to the platform's `contexts` table, which we do not port. `tenant_id` becomes a free-floating UUID column with no FK — the constant IS the contract. Document this explicitly so a future reader doesn't add a phantom `tenants` table.

### Test database backend
- **D-04:** Drop `aiosqlite` from `pyproject.toml`. The five source test files (`test_parser.py`, `test_service.py`, `test_scheduler.py`, `test_notifier.py`, `test_extractors.py`) all use `unittest.mock` (`MagicMock` / `AsyncMock`) for sessions, fetchers, email, and LLM clients — there is no integration test that needs a real session in Phase 1.
- **D-05:** Schema fidelity is verified by `alembic upgrade head` against a real Postgres in `docker-compose.yml` (gate 2), not by the pytest suite. This avoids inventing sqlite type-compat shims for `postgresql.UUID` / `JSONB` / `gen_random_uuid()`.
- **D-06:** If a future phase needs real DB integration tests, use **testcontainers-python** with a Postgres image — keep prod parity, no sqlite drift.

### Squashed-migration shape
- **D-07:** Hand-derive `alembic/versions/0001_initial.py` from the final state of `src/domain/models.py`, NOT a literal concatenation of the 3 source migrations. The Alembic graph in this repo is empty; there is no historical sequence to preserve. One `upgrade()` body creates all 5 tables in their final shape (with the columns added by the two later source migrations baked in: percentage threshold, unit-price columns).
- **D-08:** Migration uses `postgresql.UUID(as_uuid=True)` and `postgresql.JSONB` directly. Postgres-only is a stated constraint (PROJECT.md).
- **D-09:** Migration body inserts 5 store rows (ICA, Willys, Apotea, Med24, Doz) with `slug`, `store_type`, `base_url`, `parser_config` populated from the source `stores/__init__.py` registry. Use `op.bulk_insert` with literal data — no fixture file indirection.
- **D-10:** No `tenants` table created (see D-03). The seed step seeds stores only.

### Dockerfile / compose scope
- **D-11:** `Dockerfile` based on `python:3.12-slim`. Multi-stage: builder installs poetry + deps; final stage copies installed venv + `src/`. CMD is `["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]` — points at a module that doesn't exist yet (Phase 2 creates it). Image builds cleanly; running it before Phase 2 will fail at import. That is acceptable — Phase 1 gate is `docker build`, not `docker run`.
- **D-12:** `docker-compose.yml` ships **postgres-only** in Phase 1. Service: `postgres:16-alpine` with healthcheck and named volume. No app service yet — added in Phase 2 when there's a real app to run.
- **D-13:** **No Traefik labels in this repo.** Routing/TLS/auth is the future edge-proxy stack's job (see project-level decision below). When Phase 2 adds the app service, it will join an external `edge` docker network for the proxy to reach it; it will NOT publish ports or carry Traefik labels itself.
- **D-14:** Phase 1 compose lets local dev do `docker compose up postgres` then `alembic upgrade head` from the host (or in CI) to verify gate 2.

### Import rebase during the "verbatim" port
- **D-15:** Source `models.py` imports `from core.db.models import Base, _utc_now`. Port creates a local `src/infra/db.py` (or `src/domain/_base.py` — planner picks) defining `Base = DeclarativeBase()` and a `_utc_now()` helper matching source semantics. Source intra-module imports `from modules.price_tracker.X` rewrite to `from domain.X` (or `from src.domain.X` — depends on package layout choice).
- **D-16:** Document the verbatim-port mapping table in the planner's PLAN.md — make it auditable that `models.py`, `service.py`, `scheduler.py`, `parser.py`, `notifier.py`, `result.py`, `extractors/*`, `stores/*` were copied with only import-path rewrites + the `context_id` → `tenant_id` column rename. If any other change is needed (e.g. SQLAlchemy 2.0 syntax adjustments), call it out explicitly per file.

### Project-level shifts captured during this discussion
- **D-17:** **Auth topology shifts to Identity-Aware Proxy (IAP).** Edge proxy (future stack) terminates Entra OIDC once and forwards `X-Auth-Request-Email` to backend apps. price-tracker reads this header and validates against `ALLOWED_ENTRA_EMAIL` env var. **Drops `fastapi-azure-auth` and `pyjwt` from `pyproject.toml`** in Phase 1.
- **D-18:** **Edge stack target: Traefik + oauth2-proxy + Homepage dashboard.** Lives in a separate future repo/milestone, NOT in price-tracker. Configuration of that stack is out of scope for this milestone.
- **D-19:** **Roadmap reassess** scheduled after Phase 1 completes, before Phase 3 starts. Phase 3 (Admin UI + Entra Auth) will be rewritten from "OIDC code flow" to "trust X-Auth-Request-Email header"; Phase 4 (MCP) will define how the MCP endpoint bypasses the IAP (likely separate `mcp.<domain>` subdomain that the proxy excludes from the auth middleware, since MCP uses its own bearer token).

## Phase 04.1 decisions, D-01…D-15 (from `.planning/phases/04.1-package-data-moves-to-the-store-link/04.1-CONTEXT.md`, verbatim)

### Link identity and uniqueness

- **D-01:** **Drop `uq_product_store(product_id, store_id)`; make `store_url` globally
  UNIQUE instead.** A link *is* a store page, so the URL is its natural key.
  **This is a blocker, not a nicety:** the existing constraint permits only one link per
  product per store, which forbids exactly the thing this phase exists to enable
  ("Lambi 24-pack at Willys" + "Lambi 8-pack at Willys"). Global uniqueness on `store_url`
  additionally prevents the same page being tracked twice or attached to two different
  products.
  - `(product_id, store_id)` → no constraint. Same URL twice → blocked.

- **D-02:** **`package_quantity` stays NULLABLE on the link** — so a link can be saved
  before its pack size is known. But it is **never silently blank**: the first successful
  scrape autofills it (D-07), and until then the product page renders the link as
  `kr/unit — needs amount`. Nullable in the schema, visibly flagged in the UI.

### Unit price — source of truth

- **D-03:** **Unit price is COMPUTED, never scraped:** `price_sek / link.package_quantity`.
  One definition by construction, so kr/unit is comparable across every store and pack
  size — which is the entire point of the phase.
  **Context the seed omits:** today `Product.package_quantity` is *never used in any
  arithmetic*. `PricePoint.unit_price_sek` is 100% LLM-parser output (`parser.py:261-266`
  scrapes the page's printed unit price, falling back to `price / pack_size`). Stores print
  unit prices with **incompatible definitions** (kr/rulle vs kr/pack vs kr/100g), which is
  why the scraped number cannot answer a cross-store comparison. **So this phase CHANGES
  the source of truth for unit price — it is not merely relocating a calculation.**

- **D-04:** **Compute on read; do not persist unit price.** `price_sek` is the observed
  fact; unit price is a *view* of it. Derive at display/compare time.
  → **`PricePoint.unit_price_sek` is DROPPED.** Correcting a link's quantity (24 → 12)
  retroactively fixes all history for free, and the stale-snapshot class of bug cannot
  exist.

- **D-05:** **Add `PricePoint.store_unit_price_sek`** — the store's *printed* unit price,
  as scraped, clearly labeled "what the store claims". Displayed **beside** the computed
  kr/unit, **never sorted on** (its definition varies per store). It is a raw signal for
  spotting mismatches, and feeds the quantity cross-check.

  Product page shape:

  | Link | Price | kr/rulle *(computed, sortable)* | Store says *(raw)* |
  |---|---|---|---|
  | Willys 24-p | 139.90 | 5.83 | 5.83/rulle |
  | ICA 8-p | 59.90 | 7.49 | 7.49/rulle |
  | Coop 24-p | 129.00 | 5.38 | 12.90/100g ← different unit |

### Scraper — role inverts from answer to evidence

- **D-06:** The scrape **stops being the output (unit price) and becomes the input
  (quantity)**. A page fetch yields two quantity signals: `parser.pack_size` from the title
  ("16-p"), and a back-derivation from the printed unit price (`price / store_unit_price`).
  This is what makes AC#4 ("scraper fills/verifies link quantity") real.

- **D-07:** **Autofill when empty; flag on conflict; NEVER overwrite.**
  - stored quantity is NULL → scrape fills it silently
  - stored quantity is set and the page disagrees → surface a visible flag on the link
    ("page says 12, you have 24"); leave the stored value alone
  - **Rationale:** the typed value is *intent*; the page is *evidence*. Evidence does not
    get to silently rewrite intent — especially now that kr/unit and the whole history view
    are computed from that number (D-03/D-04), so one bad LLM extraction would silently
    corrupt the comparison.

- **D-08:** **Extend the parser contract** so non-countable products can be verified too.
  `PriceExtractionResult` gains `package_amount: Decimal | None` and
  `package_unit: str | None` alongside the existing `pack_size: int | None`.
  **Why:** `pack_size` is an int ("items in a pack") — meaningful for `st` (24 rolls, 60
  capsules) but useless for a 0.5-liter bottle or a 400g bag, and groceries are full of
  liter/kg goods. Normalize `package_unit` → the product's canonical unit using the **same
  st/liter/kg factors the admin UI already has** (`PKG_UNITS` in `admin.html`, ml→liter,
  g→kg).

- **D-09:** **Store the page's reading in `ProductStore.scraped_package_quantity`**
  (nullable). Every check writes it. The UI flags the link whenever it differs from
  `package_quantity`. No state machine, no `resolved_at` bookkeeping — the flag
  **self-clears** the moment either number is corrected, and the last page reading stays
  inspectable.

### UI

- **D-10:** **Product dialog:** name / brand / category / **unit only**. No package fields.
  Unit is a dropdown of canonical units (st / liter / kg).

- **D-11:** **Link dialog gains the packaging chain** — "amount in package" + auto-suggested
  editable label. The v0.2.1 guided chain **moves** from the product dialog to the link
  dialog (commit `6c2347f`, `admin.html`). It needs **no unit selector**: the product
  already knows its unit, so the link renders the amount field *in the product's unit*.
  Entry helpers (ml/g) may remain, normalizing to the canonical unit as v0.2.1 already does.

- **D-12:** **Product page** lists all links with package label, current price, and
  **kr/unit — sortable**. This is the screen that answers the toilet-paper question.

### MCP

- **D-13:** **`compare_stores` returns one row per LINK, sorted by kr/unit** (store +
  package + price + kr/unit), replacing today's one-row-per-store table. The agent and the
  UI then tell the same story, and "cheapest per unit" becomes answerable over MCP — the
  `priser` skill is the platform's main consumer.

### Migration

- **D-14:** **Rewrite `0001_initial.py` in place** (single clean migration) rather than
  stacking a 0002. Sanctioned by the seed; the DB is empty, so there is nothing to rescue.

- **D-15:** **⚠ MANDATORY consequence of D-14 — the DB reset is a required, named step,
  not an afterthought.** The deployed v0.2.0 instance's DB is **already stamped at revision
  0001**. If 0001 is rewritten without resetting, `alembic upgrade head` sees `0001 == head`
  and **applies NOTHING** — the app then runs against the OLD schema. The plan MUST include:

  ```bash
  docker compose down -v          # drops the postgres volume (tables + alembic_version)
  docker compose up -d postgres
  alembic upgrade head            # rewritten 0001
  ```
