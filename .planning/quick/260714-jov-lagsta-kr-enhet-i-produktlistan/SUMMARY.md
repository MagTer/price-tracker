---
id: 260714-jov
slug: lagsta-kr-enhet-i-produktlistan
date: 2026-07-14
status: complete
commit: 998a9fd
---

# Summary — Produktlistan: "Senaste pris" → "Lägsta kr/enhet"

## What was actually wrong

The reported symptom was "a product with several pack sizes shows a weird price". The cause was
worse than reported: the column was **not** "senaste pris" at all. `buildProductsTable` did
`stores.find(s => s.price_sek != null)` — the *first link in the array*, and the array comes from
`ps_stmt` in `src/api/admin.py` (~L230) which has **no `ORDER BY`**. The value was therefore
non-deterministic (Postgres row order), not merely stale. Two follow-on defects: it ignored
`offer_price_sek` (a product on rea displayed its ordinary price), and it carried no context —
no store, no package.

## What was built

`src/api/templates/admin.html` only. No API change: `unit_price_sek`, `store_name`,
`package_size`, `package_quantity`, `offer_price_sek` and `needs_amount` were already on the wire.

- `bestUnitPrice(product)` — minimum of `unit_price_sek` across links, nulls excluded, tie-broken
  on store name then `product_store_id` so equal prices cannot reorder between renders.
- `buildBestPriceCell(product, links)` — three honest states, never blank and never 0:
  1. cheapest kr/unit + `Butik · förpackning · effektivt pris` beneath it;
  2. no link has an amount → lowest *effective* price + "kr/enhet saknas";
  3. no link has a price → em dash.
  Links with `needs_amount` cannot enter the minimum, so the row shows an `N utan mängd` badge
  instead of quietly comparing fewer links than it lists stores.
- `effectivePrice()` (JS twin of `_effective_price`, display only) and `formatSek()` (öre always).
- Header `Senaste pris` → `Lägsta kr/enhet`; new `.best-link` muted style.

`store_unit_price_sek` is not consulted (D-05). No second kr/unit definition (D-03) — the
frontend selects a minimum, it does not compute unit prices.

## Verification

- `node --check` on the extracted `<script>` fragment, with the `test -s` guard (Gotcha 2).
- **Behavioural probe**: ran `buildBestPriceCell` in a `vm` sandbox over five fixtures (multi-pack
  with an offer, one link missing amount, no link with amount, no prices, no links) — all three
  states render as designed. Determinism check: 200 randomly shuffled link orders → **1** unique
  output (the old code would have produced several).
- **Render check** (parse ≠ render): called `price_tracker_dashboard()` and asserted the assembled
  70 KB page contains the new header, `buildBestPriceCell`, the `.best-link` CSS, a non-empty
  inline `<script>` body, and no surviving "Senaste pris".
- `ruff check` + `ruff format --check`: clean. `pytest`: **214 passed** (Postgres reachable).

## Notes for later

- The absent `ORDER BY` on `ps_stmt` (`admin.py` ~L230) is still there. It no longer affects this
  column, but any future consumer of `product.stores` inherits the same arbitrary ordering — the
  links list happens to sort client-side. Ordering it server-side (cheapest kr/unit, nulls last,
  as `service.get_product_links` already does) would remove the trap.
- The Erbjudanden table still renders absolute prices per link, which is correct there — a deal is
  about a specific package.
