---
quick_id: 260714-iqe
wave: 1
autonomous: true
files_modified:
  - CLAUDE.md
---

# Quick 260714-iqe — Gör CLAUDE.md sann igen

## Objective

CLAUDE.md beskriver ett projekt som inte längre finns. Den är inte bara inaktuell — den är **aktivt
skadlig** för en ny session, eftersom dess `## Conventions` beordrar en agent att *inte* refaktorera och
*inte* fixa brister, vilket är precis tvärtemot vad Fas 04.1 gjorde. En ny session utan motvikt kommer
att backa modelländringen "för att återställa konventionen".

Ren dokumentationsuppgift. **Ingen kod, inga tester.** 214 gröna före = 214 gröna efter, orörda.

## Task 1 — skriv om CLAUDE.md

**files:** `CLAUDE.md`

**action:**

Behåll GSD:s HTML-markörer (`<!-- GSD:conventions-start ... -->` m.fl.) **exakt** som de är — de
avgränsar genererade sektioner. Skriv innehållet *mellan* dem.

### A. `## Conventions` — den farligaste sektionen. Skriv om helt.

Idag: *"byte-equivalent port"*, *"Don't fix known shortcomings"*, *"Don't add features, refactor, or
introduce abstractions"*. Ersätt med, i sak:

- **Port-doktrinen gällde extraktionsfaserna (1–3) och är AVSLUTAD.** Den fanns för att en mekanisk
  port ska vara diffbar mot källan. Den styr inte längre.
- **Detta är nu ett vanligt produktrepo.** Modelländringar, refaktorering och buggfixar är tillåtna och
  förväntade. Fas 04.1 var en avsiktlig modelländring; att backa den i konventionens namn vore fel.
- **Språk — ett spår (beslut 2026-07-14):** **svenska** för allt användarsynligt (UI-strängar, toasts,
  användarvända `HTTPException(detail=...)`, e-post, MCP-verktygens output). **Engelska** för allt
  utvecklarvänt: identifierare, DB-kolumner, JSON-nycklar/wire-kontrakt, loggar, docstrings,
  kommentarer, commit-meddelanden, och de interna 500-felen. Sidan deklarerar `lang="sv"`.
  Ordlista: produkt · butik · länk · förpackning · mängd · **kr/enhet** · bevakning · erbjudande ·
  i lager · Åtgärder.

### B. `## Technology Stack` — rätta ett faktafel

Raden *"DB: PostgreSQL via SQLAlchemy 2.0 (asyncio) + Alembic; **aiosqlite for tests**"* är **falsk**.
aiosqlite kan inte köra dessa modeller (de använder `postgresql.UUID` och `JSONB`). Testerna kör en
riktig Postgres-tier via `tests/conftest.py` mot slask-databasen `price_tracker_test`;
integrationstesterna **skippar rent (exit 0)** när ingen DB är nåbar.

### C. `## Architecture` — modellen och trädet

Datamodellen beskriver det **gamla** schemat. Efter 04.1 gäller:

- `package_size` + `package_quantity` ligger på **`ProductStore`** (länken), inte på `Product`.
  `unit` ligger kvar på `Product`. Produkt = den abstrakta varan; länken = den konkreta förpackningen.
- `PricePoint.unit_price_sek` är **borttagen**. Jämförpris **beräknas vid läsning**:
  `price_sek / link.package_quantity`. **Enda definitionen: `src/domain/pricing.py`. Skriv aldrig en andra.**
- Nya: `PricePoint.store_unit_price_sek` (butikens *tryckta* jämförpris — visas, **sorteras aldrig på**,
  eftersom butiker använder olika enheter) och `ProductStore.scraped_package_quantity` (sidans avläsning
  — autofyll vid tomt, **flagga** vid konflikt, **skriv aldrig över**).
- `uq_product_store(product_id, store_id)` är **borttagen**; **`store_url` är globalt unik**. En produkt
  kan ha flera länkar i samma butik (olika förpackningsstorlekar). En AST-grind i
  `tests/test_static_gates.py` fäller varje återinförd `(product_id, store_id)`-uppslagning.

Lägg till `src/domain/pricing.py` i arkitekturträdet, märkt som enda definitionen av jämförpris.

### D. Ny sektion: `## Gotchas` — fällor som kostade tid

Placera efter `## Architecture` (utanför GSD-markörerna, eller i en egen `<!-- GSD:gotchas -->`-fri
sektion — den har ingen genererad källa).

1. **`ruff` finns INTE i projektet** — inte i `pyproject.toml`, inte i `poetry.lock`, inte på PATH.
   Planeringsdokument antar felaktigt att det gör det. **Anropa det aldrig**; använd `python -m compileall`.
2. **`src/api/templates/admin.html` har ingen `<script>`-tagg.** Den är ett trefragmentsdokument delat på
   `<!-- SECTION_SEPARATOR -->`, hopsatt i `admin.py` vid request-tid. En grind som extraherar
   `<script>`-kroppen får **0 byte**, och `node --check` på tom indata **avslutar med 0** — den passerar
   alltså oavsett vad som skrivs. Korrekt:
   `awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js && test -s /tmp/admin.js && node --check /tmp/admin.js`
   Behåll `test -s`-vakten. Och: **"filen parsar" är inte samma påstående som "sidan renderar"** —
   verifiera renderingen separat.
3. **Alembic: `0001_initial.py` är omskriven IN PLACE** (ingen 0002). Alembic checksummar inte
   migrationskroppar, så en databas som redan är stämplad `0001_initial` jämförs lika med head, kör
   **ingenting**, och avslutar med **0**. Appen startar då tyst mot det **gamla** schemat.
   **`alembic current` avslöjar det inte** (den skriver samma sak) — **bara `alembic check` gör det.**
   Schemaändring kräver volymdropp; se `README.md` § "Schema reset (Phase 04.1)".
4. **TECH DEBT — två `get_price_history`:** `src/domain/service.py` har den rika versionen (per länk) som
   **endast MCP** anropar; `src/api/admin.py` kör en **egen duplicerad query** som frontend anropar. De har
   redan drivit isär — det var grundorsaken till historikbuggen (paketdata nådde aldrig fram över wire).
   Nästa som läser prishistorik via API:t går i samma fälla.

### E. Status (i `## Project`)

Extraktionen är i praktiken klar. Fas 04.1 är byggd, verifierad och **deployad i prod** (v0.3.2).
Kvar av ursprungsplanen: **Fas 4:s Hermes-registrering** i agent-plattformen, och **Fas 5** (radera
price-tracker-koden ur `ai-agent-platform` — alla 8 sökvägar finns kvar).
Testsvit: **214 gröna** (+12 DB-integrationstester som skippar utan Postgres).

**verify:**
- `git diff --stat` visar **endast** `CLAUDE.md` (plus ev. `.planning/`-artefakter)
- `poetry run pytest -q` → **214 passed**, orörda
- Varje faktapåstående i filen är **kontrollerat mot koden** (grep/läs) — inte gissat
- GSD-markörerna (`<!-- GSD:*-start/end -->`) är oförändrade och parvis intakta

**done:** CLAUDE.md beskriver repot som det faktiskt är, och en ny session kan inte längre läsa
port-doktrinen som ett mandat att backa Fas 04.1.

## Artifacts this task produces

- Omskriven `CLAUDE.md` (sektionerna Project, Technology Stack, Conventions, Architecture + ny Gotchas)
