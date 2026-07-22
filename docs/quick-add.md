# Snabbtillägg — quick-add from one pasted URL

*Added in v0.5.0 (2026-07-20). Feature design + operating notes.*

## What it is

Adding a tracked item used to take two dialogs: create the product (name, brand,
category, unit), then link it to a store (pick store, paste URL, packaging). Quick-add
collapses that to **paste one product URL → confirm a prefilled form**. The server infers
everything it can; the human confirms and edits. Two endpoints, one modal:

```
POST /quick-add/preview   {url}         → suggestion bundle, NO writes
POST /quick-add           {url, ...}    → product (new or existing) + link + first check
```

UI entry point: **⚡ Snabbtillägg** on the product toolbar (`admin.html`, prefix `qa-`).

## How the preview infers each field

| Field | Mechanism | LLM involved? |
|---|---|---|
| Store | Hostname match against the 5 seeded `Store.base_url` values | Never |
| Butik label (v0.6.0) | The URL's `/stores/<id>/` segment — ICA prices per physical butik. Known ids (`quickadd.KNOWN_STORE_LABELS`) map to names ("ICA Maxi Sandviken"); unknown ids fall back to "ICA \<id\>"; no segment → no label (nationally priced chains) | Never |
| Sister-butik links (v0.7.0) | Butiker in the same `quickadd.SIBLING_STORE_GROUPS` group: the sibling URL is the pasted URL with `/stores/<id>/` swapped (product slug + id are chain-wide on handlaprivatkund.ica.se — verified live 2026-07-21: same potatissallad, 13.20 kr at Maxi vs 12.25 kr at Björksätra). Offered as a pre-checked opt-out in the confirm step | Never |
| Check schedule (v0.13.0) | None — the link INHERITS the store's schedule (`stores.check_weekdays` / `check_frequency_hours`, resolved by `domain/schedule.py`): ICA måndagar, Willys mån+fre, apoteken var 72:e timme, allt förmiddag. The confirm shows a read-only info line; the per-link override lives in the link-edit dialog. (v0.9.0's `OFFER_WEEKDAYS` prefill was this idea materialized per link — superseded) | Never |
| Name, brand | schema.org JSON-LD `Product` node in the page (`JsonLdExtractor.extract_product_metadata`) | No — exact and free on ICA/Apotea/Med24/DOZ/Kronans/Apohem |
| Price (display only) | Same JSON-LD offer | No |
| Package (amount/unit/label) | Regex over the product title: `500 ml`, `24-pack`, `8 rullar` … (`quickadd.parse_package_from_name`) | No |
| Comparison unit (`st`/`liter`/`kg`) | Derived from the package unit via `PKG_UNITS` (`quickadd.derive_unit`) | No |
| Everything above, when the page has no usable JSON-LD (typically Willys) | `PriceParser.extract_product_metadata` — one cheap cascade call (llama-4-scout → haiku), same acceptance floor as price extraction | Yes, fallback only |
| Existing-product suggestions | Token overlap (3+ chars, åäö included) between the extracted name and existing product names+brands (`quickadd.suggest_existing_products`) | Never |

All decision logic lives in **`src/domain/quickadd.py`** as pure functions — no HTTP, no
DB, no LLM — so every rule is unit-testable in isolation (`tests/test_quickadd.py`).

## Design decisions (and why)

1. **Preview-then-confirm, not one-shot.** Two reasons:
   - The JSON-LD extractor's carousel guard (name-overlap sanity check) *cannot run* when
     there is no tracked name yet — the human eyeballing the preview is that guard.
   - The "new product vs. new link" decision (below) needs a human choice.
   Nothing is persisted by a preview; every inferred value lands in an editable field.

2. **Quick-add must not undo the 04.1 model.** A URL is a *link* (one package listing at
   one store). "Lambi 24-pack at Willys" pasted after "Lambi 8-pack at ICA" is a second
   link on ONE product. The preview therefore suggests matching existing products, and the
   confirm accepts `product_id` ("ny länk på befintlig produkt") as an alternative to the
   identity fields. A quick-add that always created a new product would quietly rebuild
   the one-product-per-pack-size world Phase 04.1 abolished.

3. **Store matching is exact hostname equality (www-insensitive), never suffix.**
   `www.ica.se` (recipes) must not match `handlaprivatkund.ica.se` (the shop the
   extractors understand). Unknown host → 422 listing the known stores.

4. **No second write path.** The confirm composes the *same* service methods as the manual
   flow (`create_product`, `link_product_store`) and the *same* check flow
   (`perform_price_check`). The first check also drives the D-07 package autofill, so a
   link confirmed without a quantity usually comes back from the confirm call with one —
   harvested from the page, conflict-flagged, never overwriting typed intent.

5. **Creation is the task; the first check is best-effort.** A fetch or extraction failure
   after a successful create returns **201** with `check.success=false` — the link exists
   and the scheduler retries later, exactly like a manually created link. A duplicate URL
   is caught *before* the product is created (plus an `IntegrityError` backstop that
   deletes the just-created product), so a failed quick-add leaves no orphans.

6. **Preview price is display only.** The recorded first price always comes from
   `perform_price_check` after confirm — quick-add cannot introduce a second price
   authority.

7. **A sibling link is a candidate, not a fact (v0.7.0).** The `/stores/<id>/` swap almost
   always resolves to the same product at the sister butik, but assortments differ — so
   each accepted sibling runs its own first check, and a link whose page cannot be fetched
   is **removed again** (`removed_not_found`) rather than left to fail on every scheduler
   tick. `no_price` keeps the link: the page exists, extraction retries like any link.
   The primary link is the task; sibling outcomes (`created` / `already_tracked` /
   `removed_not_found` / `error`) are reported per entry and never fail the request.

## Failure modes

| Situation | Behavior |
|---|---|
| URL not http(s) | 400 |
| Hostname matches no store | 422, message names the stores |
| URL already tracked | Preview short-circuits **before** fetching (no page load, no LLM); confirm returns 409 |
| Page fetch fails | Preview 502; confirm 201 with `check.success=false` |
| No JSON-LD and LLM below confidence floor | Preview returns `name: null` — UI says "fyll i namnet själv"; the flow still works, you just type the name |
| LLM hallucinates package quantity | It's a prefilled *editable* field; and `normalize_amount` rejects out-of-range values, unit conflicts, and non-numbers |

## Extension points

- **New store:** seed its `base_url` (migration) — store matching needs nothing else.
  Add JSON-LD support for free if the store embeds schema.org; otherwise the LLM fallback
  covers it.
- **New ICA butik (v0.8.0 — env config, no code change):** set `QUICKADD_STORE_LABELS`
  (JSON object, butik id → display name) and `QUICKADD_SIBLING_GROUPS` (JSON array of
  id groups) on the instance — the butik id is in the product URL after `/stores/`.
  Unknown ids already work (they suggest "ICA \<id\>"); unset vars use the in-repo
  defaults; malformed JSON logs a warning and uses the defaults. The saved label lives on
  the LINK (`ProductStore.store_label`), editable in the Förpackning dialog; see
  `domain.models.link_store_name` for the display rule.
- **A chain changes its offer day (or a new chain has one):** update the Store row's
  `check_weekdays` (JSONB list, 0=Monday — Willys is `[0, 4]`), seeded in migration
  `0005_store_schedule`. Every inheriting link follows automatically; the scheduler
  checks listed days förmiddag (06–12 spread) and retries a FAILED weekday check after
  24 h instead of waiting a week. Resolution rule: `domain/schedule.py` — the ONE
  definition, used by scheduler and admin API alike.
- **Better title parsing:** extend the regexes in `quickadd.parse_package_from_name`
  (currently `ml|kg|l|g|st` amounts and `pack|st|rullar|tabletter|kapslar|påsar` counts).
- **MCP:** quick-add is deliberately UI-only for now. If the agent platform should add
  products, expose a thin MCP tool over the same two endpoints — do not reimplement.
