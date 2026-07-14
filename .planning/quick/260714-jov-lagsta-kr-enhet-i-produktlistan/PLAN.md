---
id: 260714-jov
slug: lagsta-kr-enhet-i-produktlistan
date: 2026-07-14
mode: quick
---

# Produktlistan: "Senaste pris" → "Lägsta kr/enhet"

## Problem

`buildProductsTable` (src/api/templates/admin.html) renders the product-list price cell as:

```js
const latest = (p.stores || []).find(s => s.price_sek != null);
```

Three defects, all silent:

1. **Not "latest", and not deterministic.** `.find()` takes the *first link in the array*. The
   array comes from `ps_stmt` in `src/api/admin.py` (~L230), which has **no `ORDER BY`** — so the
   order is Postgres' arbitrary row order. The column header ("Senaste pris") is a lie: it is
   neither the newest price nor a stable one.
2. **Not comparable.** Post-04.1 a product can have several links at the same store with different
   pack sizes. A 24-pack is always more expensive than a 12-pack, so an arbitrary link's absolute
   price answers no question the user has.
3. **Ignores offers.** It reads `price_sek`; the API already computes `unit_price_sek` from
   `_effective_price()` (offer wins). A product on rea showed its ordinary price.

## Decision

The product row answers **"what is the best buy right now, and where?"** — not "what did something
cost last".

Replace the column with **Lägsta kr/enhet**: the minimum of `unit_price_sek` across the product's
links (already computed server-side from the effective price and the *link's* quantity — D-03),
with the buy context as a secondary line.

## Scope

Frontend only. `unit_price_sek`, `store_name`, `package_size`, `package_quantity`,
`offer_price_sek`, `needs_amount` are all already in the `/products` payload. **No API change.**

## Tasks

1. **`bestUnitPrice(product)` helper** — min over links by `unit_price_sek`, nulls excluded.
   Deterministic tie-break (store name, then `product_store_id`) so equal prices never flap.
2. **Rewrite the price cell** in `buildProductsTable`:
   - primary: `1.79 kr/st` (unit suffix from `product.unit` via existing `unitPriceHeading()`)
   - secondary, muted: `Willys · 24-pack · 42.90 kr` (effective price = `offer_price_sek ?? price_sek`)
   - links with `needs_amount` cannot enter the minimum → a `badge-warning` count on the row, so
     the row does not lie by omission (D-02's rule, applied at product level)
   - fallback when NO link has an amount: lowest **effective** price + muted "kr/enhet saknas"
   - fallback when no link has a price at all: `—` (never a blank, never a 0)
3. **Header** `Senaste pris` → `Lägsta kr/enhet`.
4. **CSS** for the secondary line (`.best-link`), reusing `--text-muted`.

## Constraints

- **Never** use `store_unit_price_sek` here (D-05) — stores print in different units.
- No second unit-price definition (D-03): the frontend picks the minimum, it does not compute
  kr/enhet.
- Swedish in UI strings, English in code (language track, 2026-07-14).

## Verification

- `awk '/SECTION_SEPARATOR/{n++;next} n==2' src/api/templates/admin.html > /tmp/admin.js && test -s /tmp/admin.js && node --check /tmp/admin.js` (Gotcha 2 — keep the `test -s` guard)
- `poetry run ruff check src tests` + `poetry run ruff format --check src tests`
- `poetry run pytest` (214 / 202+12 skipped without DB)
- Render check: the page actually draws the new cell (parses ≠ renders).
