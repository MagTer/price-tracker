---
type: quick-summary
slug: 260817-m4q-butikens-matt-pa-jfr-priset-och-mangdfal
date: 2026-08-17
status: complete
commits: [5f1f90a, 540be36]
released: v0.58.1
---

# Summary

Two display defects, found while answering a modelling question rather than by a bug report:
how should a product sold in exactly ONE size be tracked, when its store prints a kr/kg that
compares nothing? The modelling answer (`st` × 1) needed no code — `pricing.normalize_amount`
already refuses a gram reading against a `st` product as a CONFLICT, and
`validation.printed_measure` already vetoes the jämförpris cross-check on a measure mismatch,
so neither a bad autofill nor a false Fel & luckor row was ever possible. What made the answer
*read* as broken were the two defects below, and both are wrong independently of it.

## 1 — the store's printed jämförpris had no measure on it (5f1f90a)

`admin.html`'s links panel rendered `store_unit_price_sek` as `escapeHtml(value) + ' kr'`.
That column is by definition in the store's own measure, and it was the one cell in the
portal printing a figure without saying which unit it was in:

    Jfr-pris (kr/st)   6,81 kr      Butiken anger   63,20 kr

**`pricing.printed_measure`** — moved out of `validation._printed_measure`, not copied. The
two callers judge and label the SAME number: a second marker table would let a row rendered
"kr/kg" be judged as kr/st. They diverge only on None (unknown): validation keeps judging,
the UI stays silent. `store_unit_price_unit` rides both Gotcha-4 twins
(`service.get_links_for_product`, `admin._link_payload`), additive.

`unitKr()` is deliberately NOT used for the cell — it labels with the PRODUCT's unit and
would print ICA's kr/kg as "63,20 kr/st", a wrong measure stated with confidence. The cell
also stopped concatenating the raw number: it printed "63.2 kr" where every other money cell
prints "63,20 kr" — v0.54.0's history-table bug, in the one cell it missed.

## 2 — the amount survived a change of canonical unit (540be36)

Measured in a node vm running the actual script fragment against a stub document:

    OLD  after preview:      amount=0.27 step=any    (title "…270g" -> kg, 0,27)
         after Enhet -> st:  amount=0.27 step=1      <- 0,27 STYCK, pkgQuantity() = 0.27

    NEW  after Enhet -> st:  amount=(empty) step=1 label=270 g   (operator label kept)
         auto label:         label=500 g dirty=false -> (empty)  (auto label dropped)
         same unit no-op:    amount=270 entryUnit=g              (no-op when unchanged)

A hard stop, not a silent write — `step="1"` makes the browser refuse with "the two nearest
valid values are 0 and 1", v0.53.3's shape. What it cost is that the normal path for a
one-size product walked into a browser error. Both call sites now go through `pkgRekeyUnit`.

## Verification

- 895 passed against real Postgres (baseline 888). 7 new tests: 4 on `printed_measure`,
  2 real-Postgres on the links payload, 2 static gates (one per defect, minus one because
  the count includes the split).
- **Both static gates confirmed RED against the old markup** before being kept.
- Rendered behaviour measured in a node vm, both before and after — "the file parses" is not
  "the field cleared" (Gotcha 2), and neither defect is reachable by any runtime test: the
  drill-in never hits the API and the quick-add form errors in the browser.

## Rejected

- **Labelling Fel & luckor's mismatch rows with the measure too.** `unitKr(m.printed_…, m.unit)`
  there prints the product's unit on both numbers — but that row's whole premise is that the
  two figures are in the same measure (validation only reports a row when they are, or when
  the measure is unknown). Coherent as-is; widening the change was not asked for.
- **Making the modelling choice for the operator.** Quick-add still suggests `kg` from a
  "…270g" title. The app cannot know a product exists in one size only, and `Product.unit` is
  immutable (delete-and-recreate loses history), so the judgement stays human.
