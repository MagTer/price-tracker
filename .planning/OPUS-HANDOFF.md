# Kravställning: kvarvarande städ- och härdningsarbete (för Opus)

**Skriven:** 2026-07-13 av Fable 5, efter djupanalys + autonom åtgärdssession.
**Kontext:** Läs `.planning/STATE.md` (Decisions D-22..D-27) för vad som redan är gjort.
**Arbetssätt:** Inget GSD-krav — men committa atomiskt per uppgift (conventional commits), kör `poetry run pytest` som gate efter varje uppgift (113 gröna vid handoff), och logga avslutade uppgifter i STATE.md. Rör INTE `.planning/quick/`-historik.

## Redan klart (gör INTE om)

- JSON-LD-extractor + kedjan API → JSON-LD → LLM, confidence-golv 0.6 (D-22/D-23)
- MCP fail-closed + constant-time token, path-fix `/mcp/` (D-24/D-25)
- DB-medveten /health, N+1-fix i list_products, import-transaktion, tenant/email-validering, scheduler per-item-sessioner (D-26)
- home-server-branchen `feat/price-tracker-mcp-route` (D-27) — Magnus granskar/deployar själv

## Uppgifter i prioritetsordning

### 1. Dockerfile-härdning
- Lägg till icke-root `USER` i final stage (skapa user `app`, chown `/app`).
- Ta bort den inaktuella kommentaren ovanför CMD ("module not yet implemented (Phase 2)... fails at import") — den har varit fel sedan commit 16245a4.
- Ta bort `libpq-dev` (builder) och `libpq5` (final) — asyncpg använder inte libpq. Verifiera med `docker build .` att bygget går igenom.

### 2. Självhostad Chart.js
- `src/api/admin.py:1636` laddar Chart.js 4.4.1 från cdn.jsdelivr.net på den autentiserade admin-sidan. Ladda ner UMD-filen till `src/api/static/chart.umd.min.js`, mounta StaticFiles på `/static` i `src/api/app.py`, byt script-src i dashboarden. Statiska filer behöver inte auth (publikt bibliotek).

### 3. Stale referenser i dokumentation och docstrings
- `CLAUDE.md` (price-tracker-repot): auth-avsnitten säger fortfarande att ingressen "not yet built, pending Entra client registration" — den är LIVE sedan 2026-07-09 (se STATE.md FAKTAKORRIGERING 2026-07-13). Uppdatera Architecture/Auth-styckena.
- `.planning/ROADMAP.md` + `REQUIREMENTS.md`: samma stale "ingress pending"-skrivningar; Phase 4-gapet är nu ENBART Hermes-registreringen (home-server-routen är förberedd på branch).
- `src/api/admin.py`: alla docstrings säger "Requires admin role via Entra ID authentication" — auth-modellen är IAP header-trust (X-Auth-Request-Email via oauth2-proxy). Sed-byt till "Requires IAP header auth (X-Auth-Request-Email)". Flera docstrings dokumenterar också parametrar som inte finns (`admin:`-args) — rensa.
- `docker-compose.yml` (price-tracker-repot): kommentaren säger "local development and deployment" — den är numera enbart lokal dev (deployment-sanningen är home-server-repot). Uppdatera kommentar + ta bort `ports: 5432` mot host om det inte behövs för lokal utveckling (behåll om Magnus vill kunna köra psql lokalt — fråga inte, behåll och kommentera "local dev only").

### 4. README-runbok
README.md är 2 rader. Skriv en riktig: vad tjänsten är, env-var-kontraktet (tabell: DATABASE_URL, ALLOWED_ENTRA_EMAIL [= Entra-UPN!], MCP_BEARER_TOKEN [fail-closed 503 utan], OPENROUTER_API_KEY, PRICE_PARSER_MODEL_CASCADE, PRICE_PARSER_MIN_CONFIDENCE, SMTP_*), lokal körning (compose + alembic upgrade head + uvicorn), test (poetry run pytest), release-flödet (git tag vX.Y.Z → GHCR → bumpa pin i home-server), säkerhetsmodellen (Entra-gate vid ingress, header-trust i appen, MCP bearer, extraktionskedjan API → JSON-LD → LLM).

### 5. Småfix i admin.py
- Path-parametrar felaktigt typade `str | None = None` (`get_product`, `unlink_product_from_store`, `get_price_history`, `delete_watch`) → ändra till `str`.
- Redundanta `dependencies=[Depends(require_auth)]` per route — routern har redan dependencyn; ta bort per-route-dubbletterna (behåll `admin_email: str = Depends(require_auth)` där värdet används).
- `get_product` delar fortfarande per-store latest-price-mönstret (N+1) — acceptabelt för en produkt, lämna.

### 6. escapeHtml-quotes i admin.html
`escapeHtml()` (rad ~685) escapar inte citattecken; `onclick="showPriceHistory('${p.id}', '${escapeHtml(p.name)}')"` (rad ~406) går att bryta med `'` i produktnamn. Antingen: utöka escapeHtml med `'`/`"`-ersättning, eller (bättre) byt onclick-attributen mot data-attribut + addEventListener. Verifiera att History-knappen fungerar efteråt (skapa produkt med `'` i namnet via API:t och ladda dashboarden).

### 7. IFetcher-protokollet
`src/domain/protocols/fetcher.py` kräver `search()`/`research()` som bara är döda stubbar i WebFetcher (arv från plattformen). Krymp protokollet till `fetch()` + `close()`, ta bort stubbarna, uppdatera ev. tester.

### 8. Willys comparePrice-parsning (bugg)
`src/domain/extractors/willys_api.py:_parse_response`: formatet `"33,29 kr/kg"` överlever inte rensningen (`/kg` blir kvar → Decimal kastar → unit_price tappas tyst). Strippa allt efter beloppet, t.ex. regex `([\d.,]+)`. Lägg till testfall i `tests/test_extractors.py` för `"33,29 kr/kg"` och `"12,50 kr/st"`.

## Ej i scope (beslut krävs av Magnus)

- Merge av home-server-branchen + deploy-stegen (secrets hanteras aldrig via chat)
- Merge `gsd/v1.0-milestone` → main + tag v0.2.0 (Fable har föreslagit; Magnus beslutar)
- Hermes-registrering av MCP-servern i ai-agent-platform
- Phase 5 (källkodsstädning i ai-agent-platform) — blockerad tills MCP är live end-to-end
