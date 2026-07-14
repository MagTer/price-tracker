---
phase: quick-260714-gbn
plan: 01
subsystem: ui
tags: [bugfix, admin-ui, unit-price, chart, harness-verified, mutation-verified, api-contract]
status: complete

requires:
  - "04.1 (package_size + package_quantity live on ProductStore, not Product)"
provides:
  - "groupHistoryByLink / buildBestAvailableSeries / buildChartData — pure, node-drivable price-history logic"
  - "per-link price-history modal with a kr/unit ⇄ absolute toggle"
  - "PricePointResponse.product_store_id / package_size / package_quantity — the link identity on the wire"
affects:
  - "any future consumer of GET /products/{id}/prices (three new fields, purely additive)"

tech-stack:
  added: []
  patterns:
    - "a pure function extracted for a node gate must carry NO top-level dependencies — the awk extraction pulls the function and nothing else, so a shared palette const or dayKey helper would vanish and the harness would ReferenceError"
    - "response_model is a FILTER: a field the service returns but the schema does not declare is silently stripped at the boundary"

key-files:
  created: []
  modified:
    - src/api/templates/admin.html
    - src/api/schemas.py
    - src/api/admin.py

decisions:
  - "Grouped on product_store_id, not store_slug. store_slug was the only key the API used to expose, and it merges a 16-pack and a 24-pack at the SAME store back into one line — the bug, re-shipped."
  - "The best-available line is suppressed inside buildChartData (where the harness can see it), not in the render path (where it could not)."
  - "The palette and the day-key helper live INSIDE the functions, not at module top level, because the gate extracts the three functions and nothing else."

metrics:
  duration: ~40 min
  tasks: 3
  files: 3
  tests_added: 0
  completed: 2026-07-14
---

# Quick Task 260714-gbn: Price History — Per-Link Series Summary

The price-history modal no longer claims a product is one SKU. A 16-pack and a 24-pack now draw
as **two lines**, with a bold forward-filled best-per-unit line above them. **Python suite: 214
passed, unchanged.**

## The plan's premise was wrong, and it mattered

The plan (and CLAUDE.md's brief) asserted this was a pure frontend fix:

> `get_price_history` ALREADY returns one row per LINK carrying `product_store_id`... **The
> frontend simply discards all of it.**

The *service method* does. **The endpoint the frontend calls does not.** They are two separate
implementations that happen to share a name:

- `src/domain/service.py:149` — rich rows, all the fields. **Only MCP calls this** (`server.py:64`).
- `src/api/admin.py:881` — a **duplicate query**, returning `response_model=list[PricePointResponse]`.

`response_model` is a filter. `PricePointResponse` declared neither `product_store_id` nor the
package fields, so Pydantic **stripped them at the response boundary**:

```
Fields the fix REQUIRES:
   product_store_id:   ABSENT -- stripped at the response boundary
   package_size:       ABSENT -- stripped at the response boundary
   package_quantity:   ABSENT -- stripped at the response boundary
```

The grouping key did not cross the wire. `groupHistoryByLink` keyed on `product_store_id` would
have keyed every row on `undefined` and merged all links into one series — **the original bug,
with a green harness sitting on top of it**, because the harness feeds a hand-written fixture and
would never have noticed. I stopped instead of working around it.

The only frontend-only substitute key was `store_slug`, which merges two pack sizes **at the same
store** into one line. That is precisely the confidently-wrong chart this task exists to kill, and
QD-5 ("two rows from the same store are self-identifying") says same-store multi-pack is expected.

## Deviations from Plan

### 1. [Rule 4 — architectural; EXPLICITLY APPROVED by the orchestrator] The Python boundary was amended

- **Found during:** pre-flight, before any edit.
- **Issue:** the hard boundary said "FRONTEND ONLY / DO NOT touch Python", but QD-1 (group by
  `product_store_id`) and QD-5 (Package column) are **impossible** frontend-only — the fields are
  stripped by `response_model`.
- **Escalated, not worked around.** The orchestrator verified the finding independently and
  authorized a narrow, additive Python change: *"The hard boundary is hereby amended."*
- **Fix (additive only — no field removed, no type narrowed, no value's meaning changed):**
  - `src/api/schemas.py` — `PricePointResponse` gains `product_store_id: str`,
    `package_size: str | None`, `package_quantity: float | None`
  - `src/api/admin.py` (~line 927) — populates them from `product_store`, which was **already in
    the SELECT tuple and in scope**: no query change, no extra round-trip.
- **Kept in its own atomic commit** (`47c73c0`) so it can be reverted independently of the frontend.
- **Suite confirmed, not assumed:** 214 passed. `tests/test_api.py` (51 passed) only asserts on
  `unit_price_sek` / `store_unit_price_sek` / `in_stock`, so the additive fields did not disturb it.

### 2. [Factual correction to QD-6 — accepted by the orchestrator] The `?` is defensive, not a live fix

`PricePoint.in_stock` is `Mapped[bool] = mapped_column(Boolean, default=True)` — **not nullable** —
and a never-checked link contributes **zero rows** to this endpoint (no price point exists). A null
`in_stock` therefore **cannot reach this payload**. The `?` rendering is implemented anyway (it costs
nothing and is correct if the column ever goes nullable), but **it fixes no live bug here**. The
"slut i lager for everything" bug lives on the MCP path (`compare_stores` → the service method), not
on this one.

### 3. [Discretion] Palette and day-key helper moved inside the functions

The plan allowed `dayKey(row)` as "a small local helper". It had to be **local in the literal sense**:
the gate extracts `^function <name>(` … `^}` and nothing else, so a top-level `const HISTORY_COLORS`
or a top-level `dayKey` would not be extracted and the harness would `ReferenceError` on the shipped
code. Both live inside the functions. This is a general property of this gate worth remembering.

No other deviations.

## The mutation check — recorded verbatim, as required

The harness passed first try, with the best series coming out **exactly** `[null, 5.83, 5.83, 5.42]`
as the plan predicted. I did not tune the expectation to match the output.

Then I introduced the falsy-fallback bug into `buildBestAvailableSeries` **in the shipped file**
(`carried.set(link.product_store_id, obs.unit_price_sek || 0)`) and re-ran:

```
(b) best-available line = min kr/unit per date, forward-filled, never 0
  FAIL best series
         expected: [null,5.83,5.83,5.42]
         actual:   [0,0,0,0]
  FAIL every entry is null or a number > 0 — never 0
         a 0 here is a NULL kr/unit coerced to free
(c) the NULL-quantity link never influences the best line
  FAIL first entry is null, not 0
         expected: null
         actual:   0
(d) forward-fill — the line must not move because a store went unsampled
  FAIL 2026-07-03 carries A's 5.83, not B's 7.49
         expected: 5.83
         actual:   0

HARNESS FAILED — 4 assertion(s)
>>> harness exit code: 1
```

**MUTATION CAUGHT.** The best line collapses to `[0,0,0,0]`: the amount-less Apotea link, coerced to
free, wins "cheapest" **on every single date** — on the screen whose entire job is to name the
cheapest thing. It looks completely fine on inspection. It has to be driven to be caught.

Reverted, and re-run: **`HARNESS PASSED — all assertions green`** (exit 0).

## Why the gate is capable of failing at all

`src/api/templates/admin.html` has **no `<script>` tag** — it is a 3-part fragment split on
`<!-- SECTION_SEPARATOR -->` and reassembled at `admin.py`. A previous plan's gate grepped for a
script tag, got **0 bytes**, and `node --check` **exits 0 on empty input** — so it passed regardless
of what JavaScript was written. Both guards from the plan are kept, unsimplified:

```
awk '/SECTION_SEPARATOR/{n++;next} n==2' ... > /tmp/admin.js
test -s /tmp/admin.js                    -> 39,810 bytes (non-empty)
node --check /tmp/admin.js                -> js syntax ok
grep -c '^function ' /tmp/ph-fns.js       -> 3   (all three extracted as top-level)
```

The harness concatenates the **extracted** functions with the assertions — it drives the shipped
code, never a pasted copy.

## What the modal now shows

| Datum | Butik | Förpackning | Pris | kr/{unit} | Erbjudande | I lager |

- **One THIN line per LINK** (`role: 'link'`), labelled `{store_name} {package_size}`, keyed on
  `product_store_id`. Three links → three lines. A merged series is a test failure.
- **Bold best-available line** (`role: 'best'`), **kr/unit mode only** — the cheapest kr/unit across
  links per date, **forward-filled**. Links run on independent schedules
  (`check_frequency_hours`, `check_weekday`), so without the carry the line would spike and dip purely
  because a store went unsampled that day — reintroducing the exact phantom movement this fix removes,
  one level up.
- **Absolute mode draws NO best line.** Cheapest absolute price across pack sizes is meaningless — a
  smaller box always wins — so rendering it would restate the bug as a recommendation. The suppression
  lives in `buildChartData`, where the harness asserts on it.
- **A NULL-quantity link** is a **gap** in kr/unit mode, **present with real prices** in absolute mode,
  and **never 0 anywhere**. `spanGaps: false`, so an unchecked date is a visible gap rather than a line
  drawn straight through it.
- **Table:** Package + kr/unit make two same-store rows self-identifying. A missing kr/unit is an **em
  dash** — never a 0 and never a blank (a blank reads as free and sorts like zero, 04.1-07).
- **Toggle** defaults to kr/unit, is a **delegated `data-history-mode` listener**, and re-renders from
  the cached rows — **no refetch**.

## Threat Mitigations Applied

| Threat ID | Mitigation | Verification |
|---|---|---|
| T-gbn-01 (XSS) | Every interpolated value in the history table routes through `escapeHtml`; the mode toggle is a delegated `data-*` listener, never inline `onclick` with interpolated values. | `grep -E "onclick=[\"'][^\"']*\$\{"` over the whole file → **no matches**. |
| T-gbn-02 (misinformation) | Best line suppressed entirely in absolute mode (QD-3) and structurally excludes NULL-quantity links (QD-4) — a null never enters the carry. | Asserted by the harness, and the harness is **mutation-verified** (above). |
| T-gbn-03 (silent gate) | `test -s` guard + `grep -c '^function '` == 3. | Extraction → 39,810 bytes, 3 functions. The mutation run proves the gate can fail. |
| T-gbn-SC | No package installs. Chart.js stays self-hosted (D-30). | `poetry.lock` / `package.json` untouched. |

## Known Stubs

None.

## Follow-up flagged (NOT fixed — out of scope, this is the plan's Option B)

**`src/api/admin.py:881` duplicates the service's price-history query.** Two implementations of
"price history" that can drift apart — and they already had: the service's rich version is consumed
only by MCP, while the admin route's thinner version silently dropped the link identity, which is the
entire root cause of this task. Worth a future task to collapse the route onto
`service.get_price_history()`. Not attempted here: it changes `checked_at` from `str` to `datetime`
on the wire, which is a real refactor and the wrong size for a quick task.

## Commits

| Task | Commit | Description |
|---|---|---|
| 0 (approved deviation) | `47c73c0` | `feat(260714-gbn)`: carry the link identity on the price-history wire |
| 1 | `9fa048c` | `feat(260714-gbn)`: per-link price-history logic as three pure functions |
| 2 | `c343862` | `feat(260714-gbn)`: rebuild the price-history modal on per-link series |

## Verification Run

- JS extraction: **39,810 bytes**, non-empty (`test -s`), `node --check` → **js syntax ok**
- Top-level function extraction: **exactly 3** (`grep -c '^function '`)
- Node harness over the A/B/C fixture → **HARNESS PASSED**, all of (a)–(e) + purity:
  - (a) 3 link datasets, distinct `product_store_id` — not one merged series
  - (b) best series `[null, 5.83, 5.83, 5.42]`, every entry null or > 0
  - (c) first entry **null, not 0** — link C never influences the best line
  - (d) 2026-07-03 reads **5.83** (A's carried value), not B's 7.49
  - (e) absolute mode → **zero** `role: 'best'` datasets; link C present with `[41, 42, 39, null]`
  - purity: input array unchanged
- **Mutation check: CAUGHT** (`[0,0,0,0]`, exit 1) → reverted → **passes again** (exit 0)
- Template still splits into **3 non-empty fragments** (the invariant `admin.py` reassembly depends on)
- **Page actually renders:** `GET /` → **200, 64,255 bytes**, containing `price-history-modes`, both
  `data-history-mode` buttons, and all five new functions; **zero** top-level `addEventListener`
  targets missing from the markup (a missing id would throw and kill all JS)
- Old flattened code **gone**: `label: 'Price (SEK)'` and `history.map(h => h.price_sek)` → 0 occurrences
- `pytest -q` (FULL suite) → **214 passed** — unchanged
- `tests/test_api.py` → **51 passed** — the additive response fields disturbed nothing
- No `ruff` invoked (not a project dependency)

**No database was created, dropped, reset, or migrated. No deployment was touched.**

## Human check still outstanding

Open a product with a 16-pack and a 24-pack linked (ideally **two links at the same store**, which is
the case `store_slug` grouping would have silently merged). Confirm: one thin line **per link**, a bold
best-per-unit line, and the kr/unit ⇄ absolute toggle switching **without a refetch**, with the bold
line **disappearing entirely** in absolute mode.

## Self-Check: PASSED

- `src/api/templates/admin.html`, `src/api/schemas.py`, `src/api/admin.py` all exist and the page renders (HTTP 200)
- All three commit hashes resolve in `git log`: `47c73c0`, `9fa048c`, `c343862`
- Full suite green (214 passed)
</content>
