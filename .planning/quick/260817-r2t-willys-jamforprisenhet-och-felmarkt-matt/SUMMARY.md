---
type: quick-summary
slug: 260817-r2t-willys-jamforprisenhet-och-felmarkt-matt
date: 2026-08-17
status: complete
released: v0.58.2
follows: 260817-m4q-butikens-matt-pa-jfr-priset-och-mangdfal
---

# Summary

Reported from prod on v0.58.1 with a screenshot: Fel & luckor showed a silent error on
Lasagnette Italian Dinnerkit at Willys — "butiken skriver 97,78 kr/st · vi räknar 26,40
kr/st · 73,0 %". Two independent defects behind one row, both measured rather than reasoned.

## The measurement

Prod DB, the three latest points for that product:

    ica    28.30  104.81  {"source":"ica_page","unit_price_unit":"fop.price.per.kg"}
    willys 26.40   97.78  {"source":"willys_api"}                    <- no measure

The ICA points carry a measure and are correctly vetoed. The Willys point carries none.
Probing the live Willys API for that product and five others (2026-08-17):

    comparePrice='97,78 kr'   comparePriceUnit='kg'    (Lasagnette)
    comparePrice='58,33 kr'   comparePriceUnit='kg'
    comparePrice='92,56 kr'   comparePriceUnit='l'
    ... 6/6 bare, measure always in the separate field

## 1 — Willys' measure was in a field the extractor never read

`_parse_response` pulled the measure out of `comparePrice` with a `/(\w+)` suffix regex.
Willys never puts it there. So `comparison_unit` has been None on **every Willys point ever
written**, and the validator has judged every Willys jämförpris with no measure veto —
harmless while `Product.unit` happened to be kg, wrong the first time it was not. The
extractor now reads `comparePriceUnit`, keeping the suffix as a fallback, and normalizes to
the printed "/kg" shape because a bare "kg" matches no marker in
`pricing.PRINTED_MEASURE_MARKERS` and would read as "no measure recorded".

The comment claiming `"33,29 kr/kg"` was a "format seen" from this endpoint was corrected in
place, not left standing: believing it is what cost the measure.

## 2 — Fel & luckor labelled the store's figure with the product's unit

`unitKr(m.printed_unit_price_sek, m.unit)` — so a kr/kg figure was drawn as "97,78 kr/st",
on the page whose entire job is to name what is wrong. **This was explicitly considered and
declined in the previous task** ("coherent as-is, the row's premise is that both figures are
in the same measure"). That reasoning was wrong: the premise holds only when a measure was
actually recorded, and it usually is not. `printed_unit` now rides the mismatch row and the
cell renders through `storeSaysKr`, which prints bare kronor on an unknown measure.

The static gate was extended to cover BOTH renderings of a store-printed figure — it covered
only the links panel, which is exactly how the second one shipped.

## Verification

- 900 passed against real Postgres (baseline 895). 5 new tests: 3 on the Willys measure
  field, 2 on the mismatch row's `printed_unit` (unknown stays unknown; a recorded measure
  vetoes the row entirely).
- The extended gate confirmed RED against the shipped v0.58.1 markup, quoting the exact
  offending expression.

## Note on the live row

Nothing is backfilled: the finding is derived from each link's LATEST point, so the row
clears itself on the next successful Willys check after deploy (Willys is checked Mondays
and Fridays). Until then it stays visible on v0.58.1's data.
