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

## Follow-up, done in the same task (commit 19118e6)

The missing `ORDER BY` was not left as a note — it was fixed.

**Both** `admin.py` link queries (`GET /products` ~L230 and `GET /products/{id}` ~L409) lacked an
`ORDER BY`, so every `stores` array the API emitted was in Postgres' arbitrary row order while the
frontend read `stores[0]` as if position meant something. They now emit the same order the domain
service already emits (`service.get_links_for_product` → `unit_price_expr(...).asc().nulls_last()`):
cheapest kr/unit first, amount-less links last, ties broken on store name then link id.

Sorted on the **unrounded** `Decimal` from `pricing.unit_price_py`, not on the rounded float in the
payload — rounding first makes two genuinely different prices tie and swap places for nothing.

Both routes also built the link dict byte-for-byte identically; that duplication collapsed into one
`_link_payload()`. Two copies of a wire contract drift, and this one has form (Gotcha 4).

**3 new tests** (`TestStoresArrayIsRanked`) feed the links in the worst possible order — dearest
first, the amount-less one at the front — and require the response to fix it, including a case where
a rea on the small pack must reorder it ahead of the big one. Verified they **fail against the
pre-fix code** (3 failed) and pass after. Suite: **217 passed**.

## Released

`v0.3.3` — tag pushed, `release.yml` green, image `ghcr.io/magter/price-tracker:v0.3.3`
(+ `sha-b758503`, `latest`) in GHCR. `pyproject.toml`'s version said `0.1.0` while prod ran
`v0.3.2`; it now tracks the tag. CLAUDE.md gained a **Releasing** section making the tag the
standing last step of any user-visible change — 12 commits had accumulated unreleased behind
v0.3.2, including the whole ruff/CI rebuild.

**Not live until Magnus bumps the pinned tag** in the home-server repo's
`compose/dokploy-apps/price-tracker/docker-compose.yml`.

## Notes for later

- The Erbjudanden table still renders absolute prices per link, which is correct there — a deal is
  about a specific package.
