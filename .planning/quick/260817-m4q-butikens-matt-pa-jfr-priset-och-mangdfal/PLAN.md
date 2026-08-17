---
type: quick
slug: 260817-m4q-butikens-matt-pa-jfr-priset-och-mangdfal
date: 2026-08-17
status: planned
---

# Butikens mått på jfr-priset, och mängdfältet som inte följer med enhetsbytet

Two display defects found while answering a modelling question: how should a product that
exists in exactly ONE size (Knorr middagskit lasagnette 270 g) be tracked, when its store
prints a kr/kg that says nothing? The answer is `st` × 1 — but both defects below are what
makes that answer read as broken, and both are wrong independently of it.

## Defect 1 — the store's printed jämförpris has no measure on it

`admin.html`'s links panel renders the "Butiken skriver" cell as
`escapeHtml(r.store_unit_price_sek) + ' kr'` — a bare number. That column is BY DEFINITION
in the store's own measure ("kr/rulle vs kr/pack vs kr/100g", per the comment on
`store_unit_price_sek` in models.py), and it is the one column in the portal that does not
say which. ICA's kr/kg therefore renders as "63,20 kr" beside our "34,90 kr" kr/st, two
numbers in different units stacked in one row with nothing distinguishing them.

`unitKr()` must NOT be used here: it labels with the PRODUCT's comparison unit, which would
state the wrong measure with confidence — worse than the current silence.

The measure is already recorded and already read: extractors stash a code in `raw_data`
(ICA `unit_price_unit`, Willys/Rusta `comparison_unit`) and `validation._printed_measure`
maps it to a `Product.unit` value. That is THE reader; widen its visibility rather than
write a second one (global CLAUDE.md, "One definition, one place").

**Approach**
- Move `_printed_measure` + its marker table into `domain/pricing.py` as public
  `printed_measure` / `PRINTED_MEASURE_MARKERS`. pricing.py owns unit semantics
  (`PKG_UNITS`, `CANONICAL_UNITS`) and imports nothing from models, so a plain dict argument
  fits; validation.py stays pure judgement and imports it.
- Carry `store_unit_price_unit` on the wire from BOTH Gotcha-4 twins —
  `service.get_links_for_product` (what the links panel actually reads, via
  `/products/{id}/links`) and `admin._link_payload` (the `/products` list) — so the two
  cannot drift. Both already hold the PricePoint, and `raw_data` rides the entity.
- Render `63,20 kr/kg` when the measure is known; keep the bare `63,20 kr` when it is not.
  Unknown is honest here: Willys without a comparison unit and the JSON-LD pharmacies
  record no code, and inventing one would be the exact false claim this fixes.

Additive wire fields only — a shipped contract is append-only.

## Defect 2 — the amount survives a change of canonical unit

`pkgInitChain(prefix, productUnit, amount, label)` re-keys the package fields to a product
unit. Two quick-add call sites read the CURRENT field values back and pass them through with
a possibly DIFFERENT unit:

- `qaUnitChanged` — the operator switches Enhet from the suggested `kg` to `st`.
- `qaProductChoiceChanged` — the operator picks "Ny länk på befintlig" and that product's
  unit differs from the suggestion.

Both are the normal path for a single-size product: the preview parses "270g" out of the
title, suggests `kg` and fills the amount with `0.27`. Switching to `st` leaves `0.27` in
the field, now meaning 0,27 **st**. `pkgFieldsChanged` then sets `step="1"` (a count is
whole) and the browser refuses to submit with "the two nearest valid values are 0 and 1" —
v0.53.3's exact shape: right rule, unreadable message, reads as an arbitrary GUI lock.

It is a hard stop, not a silent write, so nothing bad reaches the database. What it costs is
that the documented workflow for a one-size product ("byt Enhet → st, mängd → 1") walks
straight into a browser error.

**Approach**
- One `pkgRekeyUnit(prefix, productUnit)` both call sites route through: no-op when the
  canonical unit is unchanged (which also stops an unrelated re-render from snapping the
  entry-unit choice back to the first button), otherwise re-init with the amount DROPPED.
- The LABEL survives when the operator typed it: "270 g" is a truthful description of the
  package whatever unit the product compares in, and `package_size` is free text that never
  enters arithmetic. An auto-generated label goes with the amount that generated it, and
  `labelDirty` is carried across rather than re-derived from truthiness.
- The fix cannot be seen by a runtime test that never renders the page, so it gets a static
  gate over BOTH call sites — the "both halves" pattern the history-modal entry points use.

## Verification

- `ruff check` / `ruff format --check` / `pytest` against real Postgres
  (`docker run --rm -e POSTGRES_USER=price_tracker … postgres:16` + `TEST_DATABASE_URL`),
  baseline 888 passed.
- New tests: `printed_measure` per store code; both link payloads carry the field; static
  gate on the two quick-add call sites.
- Render-check `admin.html` in the Playwright Chromium with a stubbed `fetch` — the file
  parsing is not the page rendering (Gotcha 2), and defect 2 is invisible to every other
  check.

## Out of scope

- The modelling question itself (`st` × 1 vs `kg`) — that is an operator decision per
  product, documented in the answer, and needs no code.
- The history modal's own store-says column, if it has one — check, but do not widen the
  change beyond the links panel unless the same bare-number bug is there.
