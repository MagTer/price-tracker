---
phase: quick-260714-gbn
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/api/templates/admin.html
autonomous: true
requirements: [QD-1, QD-2, QD-3, QD-4, QD-5, QD-6]

must_haves:
  truths:
    - "Two links on one product (a 16-pack and a 24-pack) draw as TWO separate lines, never one merged series"
    - "In kr/unit mode a bold best-available line shows the cheapest kr/unit across links per date, forward-filled across unsampled dates"
    - "In absolute-price mode no best-available line is produced at all"
    - "A link with no package_quantity is absent from the best-available line and draws as a gap in its own kr/unit series — never as 0"
    - "That same link still appears normally in absolute-price mode, where it has a real price"
    - "The history table identifies which link each row belongs to (Package + kr/unit columns)"
    - "A never-checked link renders in_stock as ? , not as No"
  artifacts:
    - "src/api/templates/admin.html — groupHistoryByLink, buildBestAvailableSeries, buildChartData as PURE top-level functions"
    - "src/api/templates/admin.html — kr/unit vs absolute toggle inside #modal-price-history"
  key_links:
    - "groupHistoryByLink keys on product_store_id — this is the single line between 'per-link series' and the bug"
    - "buildBestAvailableSeries only ever carries a non-null unit_price_sek forward; a null must neither plot nor rank"
    - "buildChartData(rows, 'absolute') must return zero best-role datasets"
    - "The awk n==2 extraction + test -s guard is what makes the node gate capable of failing"
---

<objective>
The price-history modal was never updated by Phase 04.1. It still assumes the pre-04.1 model where a
product IS one SKU at one price. With a 16-pack and a 24-pack of toilet roll linked to one product, it
maps every row's absolute `price_sek` into ONE time-ordered series — so plotting a big box after a small
box reads as "the price is rising". The chart is confidently wrong, which is the worst kind of wrong.

`get_price_history` (src/domain/service.py) ALREADY returns one row per LINK carrying `product_store_id`,
`store_name`, `package_size`, `package_quantity`, `price_sek`, `offer_price_sek`, `store_unit_price_sek`,
`unit_price_sek` (the COMPUTED kr/unit) and `in_stock`. The frontend simply discards all of it.

**This is a frontend-only fix.** Do NOT touch Python. Do NOT change the API. The 214-test Python suite
must stay green and untouched. `ruff` is NOT a dependency of this project — do not call it.

Locked operator decisions this plan implements:
- **QD-1 Per-link series.** Group by `product_store_id`. One THIN line per link, labelled `{store_name} {package_size}`.
- **QD-2 Best-available line.** kr/unit mode ONLY: a BOLD line of the cheapest kr/unit per date across links,
  FORWARD-FILLED. Links have independent schedules (`check_frequency_hours` default 72, optional
  `check_weekday`), so they are sampled unevenly; without forward-fill the best line would spike and dip
  purely because a store was not checked that day — reintroducing the exact class of phantom movement this
  fix exists to remove, one level up.
- **QD-3 Toggle** kr/unit (DEFAULT) vs absolute. **In absolute mode there is NO best line** — cheapest
  absolute price across pack sizes is meaningless (a smaller box always wins), which is literally the bug.
- **QD-4 NULL quantity → no kr/unit.** Excluded from the best line; a GAP in its own series; NEVER coerced
  to 0. Still present in absolute mode.
- **QD-5 Table** gains a Package column and a kr/unit column, so two rows from the same store are
  self-identifying.
- **QD-6 `in_stock` may be null** (never-checked link) → render `?`, not `No`.

Purpose: the operator asks "is Lambi getting cheaper per roll?" and gets an answer that is true.
Output: a per-link, unit-aware price history modal whose logic is driven by a node harness that can fail.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
</execution_context>

<context>
@.planning/STATE.md
@src/api/templates/admin.html
@.planning/phases/04.1-package-data-moves-to-the-store-link/04.1-07-SUMMARY.md

Established patterns in this file — REUSE, do not reinvent:
- The template has **NO `<script>` tag**. It is a 3-part fragment split on `<!-- SECTION_SEPARATOR -->`,
  reassembled at `src/api/admin.py:1874` into parts[0]=HTML, parts[1]=CSS, parts[2]=JS. There are exactly
  2 separators (line 286 and line 429).
- Testable JS is a PURE, top-level function whose **closing brace sits at column 0** — the rest of the file
  calls `document.getElementById()` at top level and cannot be evaluated in node at all. `sortRows`
  (line ~828) is the reference implementation of this pattern.
- Interpolated values route through `escapeHtml` (line ~1084). Row actions use delegated `data-*` listeners,
  never inline `onclick` with an interpolated id.
- `unitPriceHeading(unit)` (line ~852) already returns `kr/st` / `kr/liter` / `kr/kg` / `kr/enhet` — reuse it.
- `showPriceHistory` is invoked from the delegated products-table handler at line ~1113, which already has
  the product object in hand, so `product.unit` can be threaded through with no plumbing.
- Chart.js is self-hosted on `/static` (D-30). No new dependency; no CDN.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Extract the history logic into three pure functions and prove the harness can fail</name>
  <files>src/api/templates/admin.html</files>

  <behavior>
    Driven in node over a fixture with link A (24-pack, kr/unit 5.83), link B (16-pack, kr/unit 7.49),
    link C (NULL package_quantity → no kr/unit at all, but a real absolute price), and dates where only
    SOME links were checked:

    - Test (a): `buildChartData(rows, 'unit')` yields THREE separate per-link datasets — not one merged series.
    - Test (b): the best-available line equals the min kr/unit across links on each date, and never dips to 0.
    - Test (c): link C is ABSENT from the best-available line — absent, not zero.
    - Test (d): forward-fill — on a date where only link B was checked, the best line still reflects link A's
      last known value; it does not jump to B's price merely because A was not sampled.
    - Test (e): `buildChartData(rows, 'absolute')` produces NO best-available dataset, and link C IS present.
  </behavior>

  <action>
Add three PURE, top-level functions to the JS fragment (parts[2], i.e. after the second separator), placed
directly above the existing `showPriceHistory`. Each takes plain data and returns plain data: no DOM, no
Chart.js, no globals, and each closing brace at column 0 — this is what makes them drivable in node.

`dayKey(row)` may be a small local helper, but the three below must be top-level and independently callable:

**1. `groupHistoryByLink(rows)`** — returns an array of link objects, one per distinct `product_store_id`,
each `{ product_store_id, store_name, package_size, package_quantity, label, byDate }` where `byDate` maps
`YYYY-MM-DD` → `{ price_sek, unit_price_sek }`. `label` is `store_name + ' ' + package_size` when
`package_size` is present, otherwise `store_name + ' (utan förpackningsmängd)'`. Do NOT assume input order —
the service returns `checked_at` DESC; sort internally ascending and, when one link has several observations
on one date, keep the latest. Grouping on `product_store_id` is the entire fix: it is the one line standing
between "per-link series" and the bug being fixed.

**2. `buildBestAvailableSeries(rows, dates)`** — returns an array aligned 1:1 with `dates` (ascending
`YYYY-MM-DD` strings). Walk the dates in order carrying a per-link "last known kr/unit"; a link's carried
value updates ONLY from an observation whose `unit_price_sek` is a finite number. On each date the value is
the MIN of the carried values across links; before any link has a known value, the entry is `null` — never 0.
A link with no `package_quantity` never produces a `unit_price_sek`, so it never enters the carry at all and
is thus structurally absent from this line (QD-4). Never write a fallback that turns a missing value into a
number: a null must never plot or rank as if it were free. The 04.1-07 sort gate exists precisely because a
naive coercion of a missing amount made an amount-less link look like the cheapest thing on the page.

**3. `buildChartData(rows, mode)`** — composes the two above and returns `{ labels, datasets }` ready to hand
to Chart.js. `labels` is the sorted unique date list. Each per-link dataset carries `role: 'link'` plus its
`product_store_id`, a distinct `borderColor` from a small palette constant cycled by index, a THIN
`borderWidth` (about 1.5), `spanGaps: false`, and `fill: false`. Its `data` is aligned to `labels`, using
`unit_price_sek` when `mode === 'unit'` and `price_sek` when `mode === 'absolute'`, with `null` on any date
that link was not checked — and `null`, never 0, wherever the value itself is missing (so a NULL-quantity link
is drawn as a gap in kr/unit mode, per QD-4). When `mode === 'unit'` and only then, append ONE dataset with
`role: 'best'`, a BOLD `borderWidth` (about 4), `spanGaps: false`, and the array from
`buildBestAvailableSeries`. When `mode === 'absolute'` the returned datasets must contain zero entries with
`role: 'best'` (QD-3) — cheapest absolute price across pack sizes is meaningless and rendering it would
re-commit the bug. `role` is an extra property Chart.js ignores; it exists so the harness can assert on
dataset identity rather than on brittle label strings.

Then write the node harness (in /tmp, NOT in the repo) that drives the REAL extracted functions — extract
them from the reassembled JS fragment, do not paste a copy into the harness, or the gate tests a duplicate
instead of the shipped code. Fixture rows, given in `checked_at` DESC order exactly as the API returns them:

| checked_at | product_store_id | store_name | package_size | package_quantity | price_sek | unit_price_sek |
|---|---|---|---|---|---|---|
| 2026-07-05T08:00:00Z | A | Willys | 24-pack | 24 | 130.08 | 5.42 |
| 2026-07-03T08:00:00Z | B | ICA | 16-pack | 16 | 119.84 | 7.49 |
| 2026-07-03T08:00:00Z | C | Apotea | null | null | 39.00 | null |
| 2026-07-01T08:00:00Z | A | Willys | 24-pack | 24 | 139.92 | 5.83 |
| 2026-07-01T08:00:00Z | B | ICA | 16-pack | 16 | 127.84 | 7.99 |
| 2026-07-01T08:00:00Z | C | Apotea | null | null | 42.00 | null |
| 2026-06-29T08:00:00Z | C | Apotea | null | null | 41.00 | null |

Expected `labels`: 2026-06-29, 2026-07-01, 2026-07-03, 2026-07-05.

Assert, with a non-zero exit on any failure:
- (a) `buildChartData(rows,'unit').datasets.filter(d => d.role === 'link')` has length 3, with the three
  distinct `product_store_id` values — one merged series is a FAIL.
- (b) the `role: 'best'` data equals `[null, 5.83, 5.83, 5.42]` and every entry is either `null` or a number
  greater than zero. On 2026-07-01 the min across A (5.83) and B (7.99) is 5.83.
- (c) link C never influences the best line: the first entry is `null` (on 2026-06-29 only C was checked, and
  C has no kr/unit) — a `0` there is the exact bug this gate exists to catch.
- (d) forward-fill: on 2026-07-03 only B (7.49) and C were checked, yet the best line reads 5.83 — A's last
  known value carried forward. A value of 7.49 there means the line moved because a store went unsampled,
  which is the phantom movement this whole fix removes.
- (e) `buildChartData(rows,'absolute').datasets` contains zero `role: 'best'` entries, and link C's dataset
  data equals `[41, 42, 39, null]` — C is present with real prices.
- Purity: the input array is unchanged after all calls.

**MUTATION CHECK — non-negotiable.** A gate that cannot fail is worthless, and this file has a history of
gates that passed while testing nothing. After the harness passes, temporarily edit
`buildBestAvailableSeries` in admin.html so a missing kr/unit is coerced to zero instead of skipped (the
classic falsy-fallback bug), re-run the harness, and confirm it FAILS on assertions (b)/(c). Then revert the
mutation and confirm it passes again. Record both outcomes in the summary. If the mutated version still
passes, the harness is not testing the shipped code — fix the harness before continuing.
  </action>

  <verify>
    <automated>awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js && test -s /tmp/admin.js && node --check /tmp/admin.js && for f in groupHistoryByLink buildBestAvailableSeries buildChartData; do awk "/^function $f\(/,/^}/" /tmp/admin.js; done > /tmp/ph-fns.js && test -s /tmp/ph-fns.js && test "$(grep -c '^function ' /tmp/ph-fns.js)" = "3" && cat /tmp/ph-fns.js /tmp/ph-assert.js > /tmp/ph-harness.js && node /tmp/ph-harness.js</automated>
    <automated>python -m pytest -q</automated>
  </verify>

  <done>
    - The awk extraction yields a NON-EMPTY /tmp/admin.js (the `test -s` guard is what makes an empty
      extraction fail instead of silently passing `node --check`, which exits 0 on empty input) and
      `node --check` reports valid syntax.
    - All three functions extract as top-level declarations (grep count is exactly 3) and the harness runs
      the REAL shipped code, not a copy.
    - Harness assertions (a)–(e) plus purity all pass.
    - The mutation (missing kr/unit coerced to 0) was applied, CAUGHT by the harness, and reverted.
    - `python -m pytest -q` → 214 passed. No Python file was modified.
  </done>
</task>

<task type="auto">
  <name>Task 2: Rebuild the modal on those functions — toggle, thin per-link lines, bold best line, self-identifying table</name>
  <files>src/api/templates/admin.html</files>

  <action>
Rewrite `showPriceHistory` (currently ~line 759) to consume the Task 1 functions. The offending two lines —
mapping every row into one flat absolute-price series — are deleted, not patched.

**Markup (parts[0], the HTML fragment — before the FIRST separator, inside `#modal-price-history` at line
~159):** add a two-button toggle above the canvas, buttons carrying `data-history-mode="unit"` and
`data-history-mode="absolute"`, with the unit button active by default (QD-3). Do not add a `<script>` tag —
this file has none by design.

**State + render (parts[2], the JS fragment):** follow the `productLinks` / `productLinksFor` pattern
established in 04.1-07. Cache the fetched rows and the product's `{ id, name, unit }` in module-level
variables and default the mode to `'unit'`. `showPriceHistory(productId, productName, productUnit)` fetches
`/products/{id}/prices?days=90` once and caches; a `renderPriceHistory()` function re-renders chart and table
from the cache, so toggling the mode does NOT refetch. Register the toggle on a DELEGATED listener at the
bottom of the file alongside the existing delegated handlers — never an inline `onclick` with interpolated
values. Thread the unit through by passing `product.unit` at the existing call site (line ~1113, which already
has the product object in hand).

**Chart:** destroy the previous chart instance as today, then feed it `buildChartData(rows, mode)` directly.
Set `spanGaps: false` so a missing observation is a visible gap rather than a line drawn straight through it.
Y-axis title: the result of the existing `unitPriceHeading(unit)` helper in unit mode (reuse it — do not write
a second unit-label function), and `Pris (kr)` in absolute mode. Keep `beginAtZero: false`. In absolute mode
the datasets simply contain no best-role entry, so nothing extra is needed to suppress it — the suppression
lives in `buildChartData` where the harness can see it.

**Table (QD-5, QD-6):** columns Date, Store, **Package**, Price, **kr/unit**, Offer, In Stock. Package renders
`package_size`, falling back to `package_quantity` + the product's unit, and an em dash when neither exists —
never a blank cell. kr/unit renders `unit_price_sek` to two decimals, and an em dash when it is null — never a
zero, and never a blank (a blank reads as free). In Stock renders Yes for true, No for false, and **`?` for
null** — a null means the link has never been checked, and answering "No" there is the same
confidently-wrong-answer bug that made MCP report "slut i lager" for every product since extraction. Every
interpolated value routes through `escapeHtml`.
  </action>

  <verify>
    <automated>awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js && test -s /tmp/admin.js && node --check /tmp/admin.js</automated>
    <automated>test "$(grep -c 'SECTION_SEPARATOR' src/api/templates/admin.html)" = "2" && python -c "import pathlib,sys; p=pathlib.Path('src/api/templates/admin.html').read_text(encoding='utf-8').split('<!-- SECTION_SEPARATOR -->'); assert len(p)==3 and all(s.strip() for s in p), 'fragment assembly broken'; print('3 non-empty fragments ok')"</automated>
    <automated>cat /tmp/ph-fns.js /tmp/ph-assert.js > /tmp/ph-harness.js && node /tmp/ph-harness.js</automated>
    <automated>python -m pytest -q</automated>
    <human-check>Open a product with a 16-pack and a 24-pack linked. The chart must show one thin line PER LINK (not one line), a bold best-per-unit line, and the kr/unit ⇄ absolute toggle must switch without refetching — with the bold line disappearing entirely in absolute mode.</human-check>
  </verify>

  <done>
    - The flattened one-series chart code is GONE; the chart is fed by `buildChartData`.
    - The template still splits into exactly 3 non-empty fragments (this is the invariant `admin.py:1874`
      depends on — a broken split renders a blank page even though the file parses).
    - Task 1's harness still passes against the shipped file after the rewrite.
    - Toggle is delegated, defaults to kr/unit, and re-renders from cache without a refetch.
    - Table has Package + kr/unit columns and renders `?` for a null `in_stock`.
    - `python -m pytest -q` → 214 passed. No Python file was modified.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| API JSON → DOM | Store names and package labels are operator-supplied strings interpolated into innerHTML |
| Data → operator's purchasing decision | The chart IS the product; a wrong line is a wrong purchase |

## STRIDE Threat Register

| Threat ID | Category | Component | Severity | Disposition | Mitigation Plan |
|-----------|----------|-----------|----------|-------------|-----------------|
| T-gbn-01 | Tampering (XSS) | history table innerHTML | high | mitigate | Every interpolated value routes through the existing `escapeHtml`; the mode toggle is a delegated `data-*` listener, never an inline `onclick` with interpolated values (the pattern removed in f6ec6c4) |
| T-gbn-02 | Information disclosure (misinformation) | best-available line | high | mitigate | Line is suppressed entirely in absolute mode (QD-3) and excludes NULL-quantity links (QD-4); both are asserted by the node harness, and the harness is mutation-checked |
| T-gbn-03 | Repudiation (silent gate) | node --check on empty extraction | high | mitigate | `test -s` guard + a grep count of exactly 3 extracted top-level functions; `node --check` exits 0 on empty input, which is how the last gate shipped vacuous |
| T-gbn-SC | Tampering | package installs | low | accept | No package installs. Chart.js stays self-hosted (D-30); `poetry.lock` and `package.json` untouched |
</threat_model>

<verification>
- Full Python suite green and untouched: `python -m pytest -q` → 214 passed. Zero Python files in the diff
  (`git diff --name-only` shows only `src/api/templates/admin.html`).
- JS fragment extracts non-empty and parses.
- Node harness drives the REAL extracted functions over the A/B/C fixture and passes (a)–(e).
- Mutation check performed and recorded: coercing the missing kr/unit to 0 makes the harness FAIL.
</verification>

<success_criteria>
- A product with a 16-pack and a 24-pack draws TWO thin lines, not one.
- kr/unit mode adds a BOLD forward-filled best-available line; absolute mode adds none.
- A NULL-quantity link is a gap in kr/unit mode, present in absolute mode, and never 0 anywhere.
- The table's Package and kr/unit columns make two same-store rows self-identifying; a never-checked link
  shows `?`.
- The harness has been proven able to FAIL.
</success_criteria>

<output>
Create `.planning/quick/260714-gbn-fix-price-history-view-per-link-series-b/260714-gbn-SUMMARY.md` when done.
Record the mutation-check result verbatim (both the caught failure and the post-revert pass).
</output>
