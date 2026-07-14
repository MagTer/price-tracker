---
phase: quick-260714-hui
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/api/templates/admin.html
  - src/api/admin.py
autonomous: true
requirements: [I18N-01-partial]

must_haves:
  truths:
    - "Every user-visible string in the portal is Swedish; no view mixes languages."
    - "The 40 user-facing HTTPException details render as Swedish toasts."
    - "The page still renders: GET / returns assembled HTML containing the new Swedish strings."
    - "The 214-test suite is still green; the JS fragment still parses."
  artifacts:
    - src/api/templates/admin.html
    - src/api/admin.py
  key_links:
    - "admin.html stays a 3-fragment document split on <!-- SECTION_SEPARATOR --> (consumed by price_tracker_dashboard)."
    - "Wire contract untouched: DB columns, JSON keys, API field names, option value= attributes."
---

<objective>
The app is linguistically split, and the seam runs exactly between inherited and newly written
code: the ported Phase-3 UI is English, while the email template, the MCP tools, and all the
04.1 code are Swedish. Swedish is already the de facto standard on three of four surfaces.

This plan picks **one track: Swedish** and removes the seam. It does not build a bridge over it
(no i18n framework, no string catalogue — that stays I18N-01 in the v2 backlog).

Purpose: a portal where one table cannot say "Åtgärder" next to "Latest Price".
Output: `admin.html` and `admin.py` fully Swedish on every user-visible surface, wire contract
byte-identical.
</objective>

<context>
@.planning/STATE.md
@src/api/templates/admin.html
@src/api/admin.py
</context>

## Ordlista (binding — reuse, do not invent synonyms)

Every term below already exists in the Swedish code (`notifier.py`, `mcp_server/server.py`, the
04.1 UI). Reuse the exact form. A third variant of an existing term is a failed result.

| English | Swedish | Already used in |
|---|---|---|
| product | produkt | notifier, MCP |
| store | butik | MCP, 04.1 UI |
| ProductStore / link | länk / länka | 04.1 UI |
| package / packaging | förpackning | MCP, 04.1 UI |
| quantity / amount | mängd | 04.1 UI ("saknar mängd") |
| unit price | **kr/enhet** (heading), jämförpris (prose) | history view, MCP ("Jämförspris") |
| watch | bevakning | new — fixed here |
| offer / deal | erbjudande | MCP, notifier |
| in stock | i lager | MCP, 04.1 UI |
| yes / no | Ja / Nej | 04.1 history table |

`unitPriceHeading()` already emits `kr/enhet` — do **not** introduce a competing heading.

## Rör INTE (wire contract + developer surface)

The executor must leave these byte-identical. They are code, not UI:

- Identifiers, function/variable/class names.
- DB columns: `package_quantity`, `package_size`, `store_unit_price_sek`,
  `scraped_package_quantity`, `check_frequency_hours`, `check_weekday`, `tenant_id`.
- JSON keys, API field names, `name="..."` form-field names, and `<option value="...">`
  **values** (`merge`, `replace`, `grocery`, `pharmacy`, `st`, `liter`, `kg`) — only the option
  *labels* change.
- `logger.*` / `console.error` messages, docstrings, code comments.
- The 21 internal 500s and the 1 502 (generic, developer-facing).
- `"message": "Import completed"` in the `/import` response body — a wire field; the toast
  builds its own text from `data.summary` and never reads it.
- `src/domain/notifier.py` and `src/mcp_server/server.py` — already Swedish.
- The proper noun **Price Tracker** (sidebar logo, nav item, page title, breadcrumb "Admin").
  A product name is not a string to translate.
- `PKG_UNITS` / `PKG_ENTRY_UNITS` keys and factors — `tests/test_static_gates.py` asserts this
  table agrees with `domain/pricing.py`. A g/kg slip makes one link look 1000x cheaper.

<tasks>

<task type="auto">
  <name>Task 1: Försvenska the markup fragment of admin.html (lines 1-291)</name>
  <files>src/api/templates/admin.html</files>
  <action>
Translate every user-visible string in the FIRST fragment (everything above the first
`<!-- SECTION_SEPARATOR -->` on line 293). Per the Ordlista. Do not touch the two comments that
explain D-11/D-02 — they are developer prose and stay as they are.

Stat labels: Products, Active Watches, Deals Today, Scheduler → Produkter, Aktiva bevakningar,
Erbjudanden idag, Schemaläggare.

Card titles: Products → Produkter; Current Deals → Aktuella erbjudanden; Price Watches →
Prisbevakningar. Loading placeholders ("Loading products...", "Loading deals...") → "Laddar
produkter…", "Laddar erbjudanden…". Search placeholder → "Sök produkter…".

Header buttons: "+ Add Product" → "+ Ny produkt"; Export → Exportera; Import → Importera;
"+ Add Watch" → "+ Ny bevakning".

Deal filter option LABELS (values stay `""`/`grocery`/`pharmacy`): All stores → Alla butiker;
Grocery → Livsmedel; Pharmacy → Apotek.

Modal headings and forms:
- Create Product: heading → Ny produkt; labels Name */Brand/Category/Unit → Namn */Varumärke/
  Kategori/Enhet; the empty unit option label "— none —" → "— ingen —" (value stays empty);
  submit → Skapa produkt; the dismiss button → Avbryt.
- Link Store: heading → Länka till butik; Store * → Butik *; Product URL * → Produkt-URL *;
  "Check Frequency (hours)" → "Kontrollfrekvens (timmar)"; "Check Weekday (0=Mon, 6=Sun,
  empty=frequency)" → "Kontrollveckodag (0=mån, 6=sön, tomt=frekvens)"; "Amount in package" →
  "Mängd i förpackningen"; "Quantity label" → "Mängdetikett"; placeholders "e.g. 24" → "t.ex. 24"
  and "auto — e.g. 24-pack, 500 ml" → "auto — t.ex. 24-pack, 500 ml"; submit → Länka butik;
  dismiss → Avbryt.
- Price History: heading → Prishistorik. The two mode buttons are already Swedish — leave them.
- Product Links: heading → Länkar.
- Edit Packaging: heading → Redigera förpackning; same two packaging labels + placeholders as the
  link dialog (identical wording — the two dialogs run the same chain); submit → Spara
  förpackning; dismiss → Avbryt.
- Create Watch: heading → Ny prisbevakning; Product * → Produkt *; Email * → E-post *; "Target
  Price (SEK)" → "Målpris (kr)"; "Alert on any offer" → "Larma vid varje erbjudande"; "Price Drop
  Threshold (%)" → "Prisfallströskel (%)"; "Unit Price Target (SEK)" → "Mål för jämförpris (kr)";
  submit → Skapa bevakning; dismiss → Avbryt.
- Import: heading → Importera data; Mode → Läge; option labels Merge/Replace → Sammanfoga/Ersätt
  (values `merge`/`replace` unchanged); "JSON File *" → "JSON-fil *"; submit → Importera;
  dismiss → Avbryt.

The `st (styck)` / `liter` / `kg` option labels are already correct — leave them.

Keep å/ä/ö as literal UTF-8 characters (the file is UTF-8 and the rendered doc declares
`charset=UTF-8`). Do not introduce new numeric entities. Leave the existing `&times;`, `&mdash;`
and emoji entities alone.
  </action>
  <verify>
    <automated>test "$(grep -c 'SECTION_SEPARATOR' src/api/templates/admin.html)" -eq 2 && poetry run python -c "
parts = open('src/api/templates/admin.html', encoding='utf-8').read().split('<!-- SECTION_SEPARATOR -->')
assert len(parts) == 3, f'expected 3 fragments, got {len(parts)}'
assert all(p.strip() for p in parts), 'a fragment is empty — the page would render blank'
head = parts[0]
for s in ['Produkter', 'Aktiva bevakningar', 'Ny produkt', 'Länka till butik', 'Prishistorik', 'Ny prisbevakning', 'Importera data', 'Avbryt']:
    assert s in head, f'missing Swedish string: {s}'
for s in ['Add Product', 'Cancel', 'Brand', 'Category', 'Import Data', 'Link to Store', 'Price Watches']:
    assert s not in head, f'English string still present: {s}'
print('fragment 1 OK')
"</automated>
  </verify>
  <done>Fragment 1 has no English UI strings, the file still splits into exactly 3 non-empty fragments, and every listed Swedish string is present.</done>
</task>

<task type="auto">
  <name>Task 2: Försvenska the script fragment of admin.html (lines 447-1382)</name>
  <files>src/api/templates/admin.html</files>
  <action>
Translate every user-visible string in the THIRD fragment (everything below the second
`<!-- SECTION_SEPARATOR -->` on line 446). This fragment is injected into a `<script>` block at
request time, so a syntax slip here blanks the whole page — the verify gate parses it.

Rendered table headings (this is where the seam is loudest — `buildLinksTable` currently prints
"Åtgärder" one column away from an English header):
- products table: Name/Brand/Category/Stores/Latest Price/Actions → Namn/Varumärke/Kategori/
  Butiker/Senaste pris/Åtgärder.
- deals table: Product/Store/Regular/Offer/Discount/Type → Produkt/Butik/Ordinarie/Erbjudande/
  Rabatt/Typ.
- watches table: Product/Email/Target/Alert on Offer/Actions → Produkt/E-post/Mål/Larma vid
  erbjudande/Åtgärder.
- links table (`buildLinksTable`): the final English header becomes Åtgärder. The other six
  headers are already Swedish — leave them, including the `unitPriceHeading()` interpolation.

Row buttons — products table: Links/Link/History/Delete → Länkar/Länka/Historik/Ta bort. Watches
table: the destructive button → Ta bort. Links table: Packaging/Check now/Unlink → Förpackning/
Kontrollera nu/Avlänka. The `data-action` / `data-link-action` attribute VALUES stay English —
they are the dispatch keys read by the delegated listeners.

Cell values: the watches table's Yes/No → Ja/Nej (the history table already prints Ja/Nej — match
it exactly). Scheduler stat ON/OFF → PÅ/AV.

Empty states: "No products yet. Add one to get started." → "Inga produkter än. Lägg till en för
att komma igång."; "No active deals right now." → "Inga aktiva erbjudanden just nu."; "No price
watches set up yet." → "Inga prisbevakningar än."; "No price history yet." → "Ingen prishistorik
än."; the links empty state → "Inga länkar än. En produkt utan länkar är helt okej — använd
<strong>Länka</strong>-knappen på produktraden för att lägga till en butikssida." (the emphasized
word must match the renamed button, or the instruction points at a button that no longer exists).

Failure states: the four "Failed to load X." strings → "Kunde inte ladda produkter/erbjudanden/
bevakningar/historik/länkar."; "Loading links..." → "Laddar länkar…".

Toasts and confirms (all 19):
- "Product created" → "Produkt skapad"; "Product deleted" → "Produkt borttagen"; "Store linked" →
  "Butik länkad"; "Packaging saved" → "Förpackning sparad"; "Link removed" → "Länk borttagen";
  "Watch created" → "Bevakning skapad"; "Watch deleted" → "Bevakning borttagen".
- "Checking price..." → "Kontrollerar pris…"; the checked toasts → "Kontrollerad" and
  "Kontrollerad — " + the mismatch text; "Check failed: " → "Kontrollen misslyckades: ".
- "Export downloaded" → "Export nedladdad"; "Export failed: " → "Exporten misslyckades: ";
  the import summary toast → `Import klar: ${...} skapade, ${...} bevakningar` (keep the two
  interpolations and their `data.summary` keys); "Import failed: " → "Importen misslyckades: ".
- The generic error prefix "Error: " (8 occurrences) → "Fel: ".
- confirm() dialogs: the product one → "Ta bort produkten och all dess data?"; the unlink one →
  "Ta bort butikslänken och dess prishistorik?"; the watch one → "Ta bort bevakningen?".

`pkgSetEntryUnit` writes the amount label at runtime: both the bare form and the
`(${unit})`-suffixed form → "Mängd i förpackningen" / `Mängd i förpackningen (${unit})`. This must
read identically to the static labels in Task 1, since it overwrites them.

Normalize the existing numeric entities for Swedish letters to literal characters:
`F&#246;rpackning` → Förpackning (two places), `s&#228;ger` → säger, `m&#228;ngd` → mängd. Leave
`&mdash;`, `&#8212;`, `&#39;`, `&amp;` and the emoji entities exactly as they are — the em dashes
are the "never a blank cell" guard from 04.1-07 and `escapeHtml`'s replacement table is security
code, not text.

Do NOT touch: `console.error('Stats load failed', e)` (developer-facing), the
`price-tracker-export.json` download filename, the `role: 'link'` / `role: 'best'` dataset keys,
the already-Swedish chart strings ("Billigast tillgänglig", "Pris (kr)", "utan
förpackningsmängd"), the `localeCompare(..., 'sv')` locale, or any of the PKG_UNITS table.
  </action>
  <verify>
    <automated>awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin_hui.js && test -s /tmp/admin_hui.js && node --check /tmp/admin_hui.js && echo "JS parses ($(wc -c < /tmp/admin_hui.js) bytes)" && test "$(grep -icE '>(Add|Delete|Cancel|Store|Price|History|Actions|Brand|Category|Export|Import)<' src/api/templates/admin.html || true)" -eq 0 && echo "SEAM GATE: 0 hits (was 17)"</automated>
  </verify>
  <done>The extracted script fragment is non-empty and parses under `node --check`; the seam gate returns 0 hits (baseline: 17). No English UI string survives in any rendered table, button, toast, confirm, or empty state.</done>
</task>

<task type="auto">
  <name>Task 3: Försvenska the 40 user-facing HTTPException details in admin.py</name>
  <files>src/api/admin.py</files>
  <action>
Translate the detail text of the 40 user-facing HTTPExceptions — 28x400, 3x403, 8x404, 1x409.
They surface verbatim in the frontend toast (`showToast('Fel: ' + err.message)`), so they are UI.
Leave the 21 internal 500s and the single 502 alone: generic, developer-facing.

Field identifiers stay verbatim inside the message — the operator needs to know which field the
API rejected, and the name IS the wire contract:

- "Invalid tenant_id format" / store_id / product_id / product_store_id / watch_id →
  "Ogiltigt format på tenant_id" (and so on, one per identifier).
- "Invalid product ID" → "Ogiltigt produkt-ID". "Invalid email address" → "Ogiltig e-postadress".
- The three 403s → "Åtkomst nekad: du kan bara agera i din egen kontext" / "...bara se produkter i
  din egen kontext" / "...bara se bevakningar i din egen kontext".
- 404s: "Product not found" → "Produkten hittades inte"; "Product-store link not found" →
  "Produkt–butikslänken hittades inte"; "Price watch not found" → "Prisbevakningen hittades inte".
- The 409: "This store URL is already tracked — each link has its own URL" → "Den här
  butiks-URL:en bevakas redan — varje länk har sin egen URL". (`tests/test_api.py:393` asserts
  this is a curated 409 and not a 500 leaking a driver message — it asserts the status, not the
  text. Keep it a 409.)
- Validation 400s: "check_frequency_hours must be between 72 and 240 (inclusive)" →
  "check_frequency_hours måste vara mellan 72 och 240 (inklusive)"; "check_weekday must be between
  0 (Monday) and 6 (Sunday)" → "check_weekday måste vara mellan 0 (måndag) och 6 (söndag)";
  "check_frequency_hours is required" → "check_frequency_hours krävs"; "package_quantity must be
  positive" → "package_quantity måste vara positiv".
- Import/export 400s: "mode must be 'merge' or 'replace'" → "mode måste vara 'merge' eller
  'replace'" (the two quoted literals are wire values — keep them English); the f-string "Invalid
  JSON: {e}" → "Ogiltig JSON: {e}" (keep the interpolation); "Unsupported export version:
  {version}" → "Versionen av exporten stöds inte: {version}"; "Invalid products format" →
  "Ogiltigt format på products".

Also flip the document language in `price_tracker_dashboard`'s HTML shell: `lang="en"` →
`lang="sv"`. The page is Swedish now; the attribute is what screen readers and hyphenation read,
so leaving it English is simply wrong. That is the ONLY chrome change — the sidebar logo, the nav
item, the `<title>`, the breadcrumb and the footer all say "Price Tracker", which is a product
name, and "Admin", which is the same word in Swedish. They stay.

Do not touch docstrings, `LOGGER.*` calls, or the `"message": "Import completed"` value in the
import response body (a wire field the toast never reads).
  </action>
  <verify>
    <automated>poetry run python -m compileall -q src/api/admin.py && poetry run python -c "
import re
src = open('src/api/admin.py', encoding='utf-8').read()
sv = 0
for m in re.finditer(r'HTTPException\(\s*(?:status_code=)?(\d{3})\s*,\s*(?:detail=)?(.+?)\)\s*(?:from|\n)', src, re.S):
    code, detail = m.group(1), ' '.join(m.group(2).split())
    if code in ('400', '403', '404', '409'):
        sv += 1
        assert not re.search(r'\b(Invalid|not found|Access denied|must be|is required|already tracked|Unsupported)\b', detail), f'English detail survives ({code}): {detail[:80]}'
assert sv == 40, f'expected 40 user-facing details, found {sv}'
assert 'lang=\"sv\"' in src and 'lang=\"en\"' not in src, 'document lang not flipped to sv'
print(f'{sv} user-facing details are Swedish; lang=sv')
"</automated>
  </verify>
  <done>All 40 user-facing HTTPException details are Swedish, the 21 500s and the 502 are untouched, `admin.py` compiles, and the document declares `lang="sv"`.</done>
</task>

</tasks>

<verification>
Run all four gates together. Each one was confirmed to *discriminate* against the current file
before the work started — a gate that cannot fail is not a gate:

1. **Seam gate** (baseline: 17 hits → must be 0). This is the whole point of the task:
   `test "$(grep -icE '>(Add|Delete|Cancel|Store|Price|History|Actions|Brand|Category|Export|Import)<' src/api/templates/admin.html || true)" -eq 0`

2. **Script fragment parses** (baseline: 39,810 bytes, parses). `admin.html` has NO `<script>`
   tag — it is a three-fragment document split on `<!-- SECTION_SEPARATOR -->` and assembled in
   `admin.py`. Extracting a `<script>` body from it yields 0 bytes, and `node --check` on empty
   input exits 0, so that gate passes no matter what is written. Keep the `test -s` guard:
   `awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin_hui.js && test -s /tmp/admin_hui.js && node --check /tmp/admin_hui.js`

3. **The page actually renders** (baseline: 64,247 bytes, contains "Latest Price", lacks
   "Åtgärder"). "The file parses" and "the page renders" are different claims — the document is
   stitched from three fragments at request time. Call the renderer and assert on its output:
   ```
   poetry run python -c "
   import asyncio
   from api.admin import price_tracker_dashboard
   html = asyncio.run(price_tracker_dashboard(admin_email='magnus@example.com'))
   assert len(html) > 50000, f'render suspiciously small: {len(html)}'
   for s in ['Åtgärder', 'Produkter', 'Ny produkt', 'Senaste pris', 'Erbjudanden idag', 'Prisbevakningar', 'Ta bort', 'kr/enhet', 'lang=\"sv\"']:
       assert s in html, f'missing from rendered page: {s}'
   for s in ['Latest Price', 'Add Product', 'Actions', 'Check now', 'In Stock']:
       assert s not in html, f'English survives in rendered page: {s}'
   print(f'RENDER OK: {len(html)} bytes, Swedish')
   "
   ```

4. **Suite still green — run it, do not assume.** No test asserts on the English error strings
   (checked: `tests/test_static_gates.py:29` and `tests/test_api.py:393` reference them only in
   assertion *messages* and comments), but that is a prediction, not a result:
   `poetry run pytest -q` → 214 passed.

`ruff` is NOT in this project — never invoke it. `python -m compileall` is the Python syntax check.
</verification>

<success_criteria>
- Seam gate: 0 hits (was 17).
- Script fragment: non-empty, parses under `node --check`.
- `admin.html` still splits into exactly 3 non-empty fragments.
- `GET /` renders ~64 KB of HTML carrying the new Swedish strings and none of the English ones.
- `poetry run pytest -q`: 214 passed.
- Wire contract byte-identical: no DB column, JSON key, API field name, form-field `name`, or
  `<option value>` changed.
- No view mixes languages. One track: Swedish.
</success_criteria>

<output>
Quick task — commit both files in one atomic commit:
`feat(ui): ett språkspår — försvenska all användarsynlig text i portalen`
</output>
