---
status: complete
quick_id: 260714-hui
date: 2026-07-14
---

# Quick 260714-hui — Ett språkspår: svenska

> **Not:** exekutorn skrev denna SUMMARY i sin worktree, men filen var ocommittad när
> orkestratorn tog bort worktree:n med `--force`. Den är rekonstruerad av orkestratorn från
> exekutorns returrapport, och samtliga grindar nedan är **omkörda av orkestratorn på `main`**
> — inga siffror är avskrivna på förtroende.

## Vad som gjordes

Appen var språkligt kluven, och skarven gick exakt mellan ärvt och nyskrivet: den portade Fas 3-koden
var engelsk, medan e-postmallen, MCP-verktygen och all ny 04.1-kod redan var svenska. Svenska var alltså
de facto standard på tre av fyra ytor. Skarven är nu borta.

**Commits** (cherry-pickade från worktree till `main`):

- `8b819fd` feat(ui): försvenska markup-fragmentet i admin.html
- `f94c5ec` feat(ui): försvenska skriptfragmentet i admin.html
- `16292af` feat(api): försvenska de 40 användarvända HTTPException-detaljerna
- `0209006` test(api): binda om 3 assertions till den svenska feltexten *(avvikelse — se nedan)*

## Grindar — körda, inte påstådda

| Grind | Före | Efter |
|---|---|---|
| Skarv-grep (engelska etiketter) | **17 träffar** | **0 träffar** |
| Skriptfragment (`test -s` + `node --check`) | 39 810 B, parsar | 39 970 B, parsar |
| Exakt 3 icke-tomma fragment | 3 | 3 |
| `GET /` renderar | 64 247 B, har "Latest Price" | **64 433 B, har "Åtgärder", noll engelska kvar** |
| `lang`-attribut | `en` | `sv` |
| å/ä/ö | HTML-entiteter (`F&#246;rpackning`) | literala tecken, UTF-8 |
| `pytest -q` | 214 gröna | **214 gröna** |

Render-grinden kördes **separat** från parse-grinden. Ett trefragmentsdokument kan parsa perfekt och
ändå rendera blankt — "filen parsar" är inte samma påstående som "sidan renderar".

## Wire-kontraktet — verifierat som mängder, inte radantal

Etiketter och `data-action` delar rader, så ett diff-radantal bevisar ingenting. Verifierat i stället att
mängderna är identiska:

- `<option value="...">` — `merge`, `replace`, `grocery`, `pharmacy`, `st`/`liter`/`kg`: **oförändrade**.
  Endast etiketten mellan taggarna översattes (`grocery` → "Livsmedel").
- `data-action`-nycklar: 93 attribut-tokens identiska.
- DB/JSON-nycklar oförändrade: `package_size` (11), `package_quantity` (11), `store_unit_price_sek` (1),
  `scraped_package_quantity` (1), `product_store_id` (11), `unit_price_sek` (11), `in_stock` (2).
- `PKG_UNITS`, `PKG_ENTRY_UNITS`, `escapeHtml`: byte-identiska.
- `admin.py`: endast `detail=`- och `lang=`-rader ändrade. De **21 interna 500-felen** och 502:an är kvar
  på engelska (utvecklarvända).
- `notifier.py` och `mcp_server/server.py`: **orörda** (redan svenska).

## Avvikelse: filscopet bröts, medvetet

**Orkestratorns premiss var fel.** Planen sa "inga tester asserterar på de engelska felsträngarna
(kontrollerat)". Det stämde inte — `tests/test_api.py:388`, `:415` och `:507` asserterar på
*responskroppens text* (`assert "positive" in detail`, `assert "already tracked" in detail`), alltså på
exakt den copy som uppgiften enligt mandat översätter. Sex tester gick röda.

Det ställde två plan-villkor mot varandra: "rör bara de två filerna" mot "214 gröna". Exekutorn bröt
filscopet, eftersom att backa översättningen hade omintetgjort hela uppgiftens syfte.

Fixen bevarar varje tests *avsikt* i stället för att bara stava om den:

- `:388` / `:507` asserterar nu att avvisningen **namnger det felande fältet** (`package_quantity` — ett
  oförändrat wire-namn) *och* säger varför (`positiv`). Den delen som bär betydelse är därmed
  språkneutral, och testet är strikt mer robust än att matcha ett engelskt adjektiv.
- `:415` → `"bevakas redan"`. **Driver-läckage-assertionerna (`:417`/`:418`) rördes inte** — de är
  testets säkerhetskärna, och 409:an är fortfarande en 409.

Lagt i en egen commit (`0209006`) så att den scope-brytande ändringen är lätt att granska eller backa
separat.

## Terminologi (fastställd, återanvänd befintlig)

produkt · butik · länk · förpackning · mängd · **kr/enhet** (exakt den form historikvyn redan använde) ·
bevakning · erbjudande · i lager · Åtgärder

Produktnamnet "Price Tracker" (logga, nav, `<title>`) översattes **inte** — ett namn är ingen
översättbar sträng.

## Kvar

Kodspråk förblir engelska enligt beslut: identifierare, DB-kolumner, loggar, docstrings, kommentarer,
commit-meddelanden. Endast användarsynlig text är svensk.
