# SEED: Package data moves from Product to ProductStore (link)

**Decided:** 2026-07-13 (operator + agent, home-server session — brought here
for a proper GSD phase in this repo: design → plan → build).
**Status:** Ready to pick up. **DB is empty — no data rescue needed.**

## The decision

`package_size` + `package_quantity` move from `Product` to `ProductStore`.
`unit` STAYS on `Product`. Product becomes the abstract good; the link is the
concrete package listing.

- **Product** = "Lambi toapapper" (name, brand, category, **unit** = the
  comparison unit: st/liter/kg — the property that makes variants comparable)
- **ProductStore (link)** = "Lambi 24-pack at Willys" (store_url,
  **package_quantity** = 24, **package_size** = "24-pack" display label,
  scheduling fields as today)
- **PricePoint** unchanged; `unit_price_sek` = price / link.package_quantity.

## Why (operator's insight, verified against the model)

1. **The link URL *is* the variant.** A store page points at "Lambi 24-pack",
   never at abstract "Lambi" — the package data describes what the URL points
   at, so they belong to the same entity.
2. **Today's model can't answer the app's core question.** Product-as-SKU means
   three pack sizes = three unrelated products; "cheapest kr/rulle for Lambi
   across sizes and stores" has no grouping key. After the move it is a single
   product view: all links sorted by unit price.
3. **The scraper already learns this per link.** `parser.py` extracts
   `pack_size` per store page — with quantity on the link the scraper can
   autofill/verify it instead of trusting hand entry on the product.

**Considered and rejected:** a middle `Variant` entity (Product → Variant →
per-store links) is the fully normalized model (the same 24-pack exists at
several stores) but is over-modeling for a home tool — quantity-per-link
duplicates one small number per store, and the scraper verifies it per page
anyway. Deliberate choice; do not resurrect Variant without a new reason.

## UI consequence (reshapes v0.2.1's guided chain — even simpler)

- **Product dialog:** unit becomes a dropdown of CANONICAL units only
  (st / liter / kg). No package fields at all.
- **Link dialog:** gains "Amount in package" + auto-generated editable label
  (the v0.2.1 chain from admin.html moves here) — and because the product
  already knows its unit, the link dialog needs NO unit selector: it renders
  the amount field in the product's unit (entry helpers ml/g may remain as a
  convenience, normalizing to the canonical unit as v0.2.1 already does).

## Touch points (inventory, not a plan — the GSD planner owns sequencing)

- `src/domain/models.py` — move the two columns
- `alembic/` — DB is EMPTY: a clean new revision is fine; collapsing/resetting
  the revision chain is also sanctioned if simpler (operator preference is
  fresh-start over compat in test-stage systems)
- `src/api/schemas.py` — ProductCreate/Update lose package fields;
  link create/update schemas gain them
- `src/domain/service.py` — create_product signature, link creation,
  unit-price calculation paths
- `src/api/admin.py` + `templates/admin.html` — dialogs per UI consequence
  above; product listing shows unit; link listing shows package + kr/unit
- `src/domain/parser.py` — extracted pack_size verifies/autofills the LINK's
  quantity (mismatch between page and stored quantity is signal, not noise)
- `src/mcp_server/` — no direct package_* references found (verify)
- `tests/test_api.py`, `tests/test_service.py` — carry package_* references

## Acceptance criteria (the session's definition of done)

1. Creating a product asks for name/brand/category/**unit only**.
2. Creating/editing a link asks for amount (+ label auto-suggestion) in the
   product's unit.
3. A product page shows all its links with package label, current price and
   **kr/unit, sortable** — the toilet-paper question answered on one screen.
4. Scraper run against a real store page fills/verifies link quantity.
5. Alembic migrates a fresh DB cleanly; full test suite green.
