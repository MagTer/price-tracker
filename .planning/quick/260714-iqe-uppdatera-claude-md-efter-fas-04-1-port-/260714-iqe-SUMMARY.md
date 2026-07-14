---
quick_id: 260714-iqe
status: complete
date: 2026-07-14
commit: 0d3c002
files_modified:
  - CLAUDE.md
tests_before: 214 passed
tests_after: 214 passed
---

# Quick 260714-iqe — Gör CLAUDE.md sann igen

Skrev om `CLAUDE.md` så att den beskriver repot som det faktiskt är efter Fas 04.1. Den farligaste
raden — port-doktrinen i `## Conventions` — är borta. Docs only; ingen kod, inga tester rörda.

**Commit:** `0d3c002` — `docs(claude): make CLAUDE.md true again after Phase 04.1`

## Vad som ändrades

| Sektion | Ändring |
|---|---|
| `## Project` | Extraktionen är klar och deployad (v0.3.2). Kvar: Hermes-registrering + Fas 5. EXTRACTION.md nedgraderad till **historiskt dokument** — STATE.md/CLAUDE.md vinner vid konflikt. |
| `## Technology Stack` | Rättade den falska raden "aiosqlite for tests". |
| `## Conventions` | **Port-doktrinen explicit pensionerad.** Refaktorering/modelländring/buggfix är tillåtet. Fas 04.1 får inte backas i källrepots namn — källrepot ska raderas. + ett-språkspår-regeln. |
| `## Architecture` | Hela 04.1-modellen dokumenterad; `pricing.py` inlagd i trädet som enda definitionen av kr/enhet. |
| `## Gotchas` (ny) | 4 tysta fällor: ruff, admin.html-script-gaten, alembic-in-place, dubbla `get_price_history`. |

GSD-markörerna: alla 7 par oförändrade, parvis intakta, i ursprunglig ordning. `## Gotchas` ligger
utanför markörerna (har ingen genererad källa).

## Avvikelser — tre faktafel i planen, ej inskrivna

Planen kontrollerades mot koden innan något skrevs. Tre påståenden höll inte:

1. **`aiosqlite` FINNS kvar i `pyproject.toml`** (rad 30, `^0.22.1`). Planen sa att den var borta.
   *Sant* är att den är **oanvänd** — ingen kod importerar den, och `tests/conftest.py:9` säger
   uttryckligen att den inte kan ersätta Postgres (`postgresql.UUID` + `JSONB` kompilerar inte).
   **Skrev:** tester kör riktig Postgres; aiosqlite är en vestigial oanvänd dep (säker att ta bort).
   Att skriva "aiosqlite är borta" hade varit en ny osanning i samma fil.

2. **`ruff` FINNS i `poetry.lock`** (rad 752, 1721) — men bara som optional extra hos *andra* paket,
   vilket inte installerar något. Planens "inte i poetry.lock" är bokstavligen falskt.
   **Skrev:** inte en deklarerad dependency, inte installerad, inte på PATH — anropa aldrig.
   Formuleringen tål att någon greppar i låsfilen och hittar en träff.

3. **De 12 DB-testerna ligger INUTI de 214**, inte utöver. Postgres kör lokalt, så `214 passed`
   inkluderar dem. Utan DB blir det `202 passed, 12 skipped` (verifierat: `test_migration.py`
   samlar 12 tester).
   **Skrev:** "214 passing, varav 12 Postgres-integrationstester; utan DB → 202 passed, 12 skipped."

Inga radnummer skrevs in i CLAUDE.md utom en ungefärlig (`admin.py` ~L1877 för fragmentsammansättningen),
märkt som ungefärlig. Resten refererar till filer och symboler, som inte ruttnar.

## Verifierat mot koden (inte gissat)

| Påstående | Verifiering |
|---|---|
| `PricePoint.unit_price_sek` borta, `store_unit_price_sek` finns | `models.py` — endast `computed_unit_price_sek` (hybrid, L180 + SQL-expr), `store_unit_price_sek` L157 |
| `package_size`/`package_quantity` på `ProductStore`, `unit` på `Product` | `models.py` L101/L105 (ProductStore) vs L68 (Product) |
| `store_url` unik, `uq_product_store` borta | `UniqueConstraint("store_url", name="uq_product_stores_store_url")`; inga träffar på `uq_product_store` i `src/` eller `alembic/` — bara i tester som *asserterar frånvaron* |
| AST-grind mot pair-lookup | `tests/test_static_gates.py::test_no_link_lookup_by_product_store_pair` (+ en självtest som bevisar att detektorn fäller den gamla formen) |
| `src/domain/pricing.py` = enda definitionen | Filen finns; docstring deklarerar "the single definition", två evalueringslägen (Python + SQL) |
| `admin.html` saknar `<script>`, har SECTION_SEPARATOR | `grep -c "<script"` → **0**; 2 separatorer (rad 293, 446) → 3 fragment; `admin.py:1877` `.split("<!-- SECTION_SEPARATOR -->")` |
| Endast en migration | `alembic/versions/` innehåller bara `0001_initial.py` |
| Två `get_price_history` | `service.py:149` (anropas **endast** av `mcp_server/server.py:64`) och `admin.py:881` (rutten `/products/{product_id}/prices`, som är den `admin.html:923` faktiskt anropar) |
| `tests/conftest.py` finns, skippar utan DB | Finns; `pytest.skip(...)` vid onåbar DB; slaskdatabas `price_tracker_test` |
| README-sektionen existerar | `README.md:106` "Schema reset (Phase 04.1)" |

## Constraints

- Endast `CLAUDE.md` i diffen (`git diff --stat`: 1 file, +57/-21). Inga kod-, test- eller
  `.planning/`-filer rörda.
- `poetry run pytest -q` → **214 passed** före och efter. Identiskt.
- `ruff` anropades aldrig.
- SUMMARY/STATE inte committade (orkestratorns jobb).
