# ICA-nattprobe (engångsexperiment, natten 2→3 aug 2026)

**Frågan:** när byter ICA priserna — automatiskt vid midnatt, eller när någon "trycker
på knappen" på morgonen? Svaret avgör om price-trackerns ICA-fönster kan flyttas till
efter midnatt, så att måndagens erbjudanden ligger i appen till frukost i stället för
runt lunch.

**Bakgrund:** price-trackerns historik visar att Willys byter i fönstret 00:11–01:14
natten mot söndag (automatisk batch). För ICA ramade våra kollar in hela natten OCH
morgonen, så det gick inte att avgöra. Detta experiment stänger luckan.

## Upplägg

Cron (dev-maskinen, som står i **UTC**) kör `probe.py` fem gånger — svensk tid:

| Svensk tid       | UTC (cron)   | Roll |
|------------------|--------------|------|
| sön 2/8 22:30    | 20:30 2/8    | baslinje (gamla veckans priser) |
| mån 3/8 00:15    | 22:15 2/8    | direkt efter midnatt |
| mån 3/8 01:00    | 23:00 2/8    | |
| mån 3/8 03:00    | 01:00 3/8    | |
| mån 3/8 06:00    | 04:00 3/8    | innan trackerns eget fönster |

Varje körning hämtar 4 ICA-produktsidor (Bregott ×2 — bevisad kampanjflipper — Falukorv,
Köttbullar) med 25–45 s mellanrum, curl_cffi/Chrome-fingeravtryck, och läser priset ur
JSON-LD med appens egen extractor (`~/dev/price-tracker/src`). Avsiktligt minimalt:
proben delar publik IP med prod-trackern.

## Läsa av

```bash
cat ~/ica-natprobe/RESULTAT.md
```

Tabell + automatiskt utlåtande ("PRISBYTE UPPTÄCKT senast ..."). Rådata i
`results.jsonl`. Felkoder i cellerna (t.ex. `ERR 405`) är i sig intressanta —
det är ICA:s botvägg nattetid.

**Tolkning:** byter priserna redan i 00:15-körningen → midnatts-batch → flytta ICA-fönstret
i price-trackern (kräver att schemafönstret görs Europe/Stockholm-medvetet först — det
räknas i UTC idag). Byter de först mellan 06:00 och förmiddagen → behåll dagens fönster.

## Städning efteråt

Cron-raderna är datumstyrda (2–3 aug) men cron återkommer årligen; scriptet självspärrar
på fel datum. Ta bort permanent med:

```bash
crontab -l | grep -v ica-natprobe | crontab -
```

---

## Status: AVSLUTAT och städat 2026-08-14 · FLYTTAT IN I REPOT 2026-09-06

- **Svaret är hämtat:** prisbyte senast mån 00:15 → midnattsbatch. Se `RESULTAT.md`.
- **Slutsatsen är skördad in i repot** — den låg bara här i elva dagar. Den står nu i
  `~/dev/price-tracker/CLAUDE.md` (schedule-punkten, med förbehållen: n=1 produkt,
  rörelsen var en kampanj som TOG SLUT, och 03:00-körningens 404) plus som öppen post
  i `.planning/STATE.md`. **Fönstret är INTE flyttat** — det beslutet är inte taget.
- **Cron-raderna är borttagna** (5 st + kommentarsraden). Backup av den gamla crontaben
  togs innan de rensades.
- **`venv/` är raderad** (39 MB). Vill du köra proben igen:
  `python3 -m venv venv && ./venv/bin/pip install curl_cffi` — `probe.py` läser appens
  egen extractor från `~/dev/price-tracker/src`, så den behöver inget mer.
- **Rådata är kvar** (`results.jsonl`, `cron.log`, `RESULTAT.md`) därför att
  fönsterfrågan fortfarande är ÖPPEN och den nedskrivna vägen framåt är att köra proben
  bredare — inte att resonera vidare. Underlaget behövs alltså för nästa körning, inte
  bara som arkiv.
- Rättelse till "Tolkning" ovan: precis den förutsättning den ställer — att
  schemafönstret görs Europe/Stockholm-medvetet — är uppfylld sedan price-tracker
  v0.33.0. Det är alltså en värdeändring numera, inte ett ombygge.

### Flytten in i repot, 2026-09-06

Experimentet låg i `~/ica-natprobe/` på dev-VM:en, och både `CLAUDE.md` och
`.planning/STATE.md` pekade dit med ABSOLUT sökväg medan repot saknade egen kopia.
Dev-VM:en är dokumenterat disponibel och har sedan 2026-09-06 ett
`make rebuild-dev` i home-server-repot som ersätter hela disken — så en ombyggnad
hade raderat underlaget och lämnat två döda hänvisningar i ett repo som påstår att
underlaget finns. STATE.md sa att rådata "städas när frågan är avgjord", men frågan
är öppen: en ombyggnad hade avgjort den av misstag.

Filerna är byte-identiska med originalen utom två ändringar, båda gjorda för att
detta nu är versionshanterad källa och inte en katalog på en maskin:

- `probe.py` löste `sys.path` ur en hårdkodad absolut sökväg till en enskild klon;
  den räknas nu ut från filens egen plats, så "kör den bredare" inte kräver en
  redigering först.
- Status-punkten ovan sa att rådata ligger utanför repot. Den är rättad på plats.

Originalkatalogen är INTE raderad — den ligger kvar tills operatören bekräftat kopian.
