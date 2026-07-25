"""THE definition of the product category taxonomy — a fixed, ORDERED list.

`category` used to be free text (the LLM was asked for "one or two Swedish words"), which
made it inconsistent enough — "Mejeri" vs "Mjölkprodukter" vs "Dryck" vs "Drycker" — to be
useless as a sort key. It is now a closed set of grocery sections whose ORDER is the order
the store is walked, so a shopping list can one day be sorted straight into aisle order
without backtracking. Index in PRODUCT_CATEGORIES == shelf order.

This module is the ONE place the set and its order live. The UI <select> (server-injected in
render_admin), the LLM prompt, server-side validation, and any category sort all resolve
through here. Do not write a second list.

The last entry, "Hushåll, Hygien & Apotek", deliberately spans household/hygiene goods AND
everything the pharmacy stores (Apotea, Med24, Doz, Kronans, Apohem) sell — receptfritt,
kosttillskott, vitaminer — since those are not a grocery aisle of their own.
"""

# Walk order == list order. Reordering is a pure code change here (that is the whole reason
# category stays a String column instead of a DB enum — a DB enum reorder is a painful migration).
PRODUCT_CATEGORIES: tuple[str, ...] = (
    "Bröd & Bageri",
    "Kött, Chark & Deli",
    "Mejeri & Ost",
    "Frukt & Grönt",
    "Fryst",
    "Skafferi",
    "Dryck",
    "Godis & Snacks",
    "Hushåll, Hygien & Apotek",
)

_CATEGORY_SET = frozenset(PRODUCT_CATEGORIES)

# Anything not in the set (a legacy free-text value, or None) sorts AFTER every real category,
# so uncategorised products fall to the bottom of an aisle-ordered list rather than the top.
_UNRANKED = len(PRODUCT_CATEGORIES)

# Best-effort keyword → canonical map for coercing the old free-text values (and a stray LLM
# guess that ignored the list). Lowercase substring match, FIRST hit wins, so ordering matters:
# "bröd" must precede "korv" or "korvbröd" lands in Chark instead of Bröd. Keep this the single
# authority — the 0006 migration inlines a frozen copy on purpose (a migration must not depend
# on live code), but new synonyms belong here first.
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("bröd", "Bröd & Bageri"),
    ("bageri", "Bröd & Bageri"),
    ("bulle", "Bröd & Bageri"),
    ("fralla", "Bröd & Bageri"),
    ("baguette", "Bröd & Bageri"),
    # Fresh meat, chark/deli, and fish are one cold-corner section in the store, so they are
    # one category. The label omits "fisk" (Magnus does not eat fish, so it never appears), but
    # the fish keywords stay mapped here — harmless, and robust if a fish product ever shows up.
    ("chark", "Kött, Chark & Deli"),
    ("deli", "Kött, Chark & Deli"),
    ("pålägg", "Kött, Chark & Deli"),
    ("korv", "Kött, Chark & Deli"),
    ("skinka", "Kött, Chark & Deli"),
    ("bacon", "Kött, Chark & Deli"),
    ("färdigmat", "Kött, Chark & Deli"),
    ("färdigrätt", "Kött, Chark & Deli"),
    ("kött", "Kött, Chark & Deli"),
    ("färs", "Kött, Chark & Deli"),
    ("fågel", "Kött, Chark & Deli"),
    ("kyckling", "Kött, Chark & Deli"),
    ("fläsk", "Kött, Chark & Deli"),
    ("biff", "Kött, Chark & Deli"),
    ("fisk", "Kött, Chark & Deli"),
    ("lax", "Kött, Chark & Deli"),
    ("skaldjur", "Kött, Chark & Deli"),
    ("räk", "Kött, Chark & Deli"),
    ("mejeri", "Mejeri & Ost"),
    ("mjölk", "Mejeri & Ost"),
    ("mjölkprodukt", "Mejeri & Ost"),
    ("yog", "Mejeri & Ost"),
    ("grädde", "Mejeri & Ost"),
    ("smör", "Mejeri & Ost"),
    ("kvarg", "Mejeri & Ost"),
    ("ägg", "Mejeri & Ost"),
    ("frukt", "Frukt & Grönt"),
    ("grönt", "Frukt & Grönt"),
    ("grönsak", "Frukt & Grönt"),
    ("sallad", "Frukt & Grönt"),
    ("banan", "Frukt & Grönt"),
    ("äpple", "Frukt & Grönt"),
    ("tomat", "Frukt & Grönt"),
    ("potatis", "Frukt & Grönt"),
    ("fryst", "Fryst"),
    ("frys", "Fryst"),
    ("glass", "Fryst"),
    ("skafferi", "Skafferi"),
    ("pasta", "Skafferi"),
    ("konserv", "Skafferi"),
    ("sås", "Skafferi"),
    ("mjöl", "Skafferi"),
    ("krydd", "Skafferi"),
    ("flingor", "Skafferi"),
    ("müsli", "Skafferi"),
    ("torrvaror", "Skafferi"),
    ("olja", "Skafferi"),
    ("socker", "Skafferi"),
    ("dryck", "Dryck"),
    ("läsk", "Dryck"),
    ("vatten", "Dryck"),
    ("juice", "Dryck"),
    ("saft", "Dryck"),
    ("kaffe", "Dryck"),
    ("godis", "Godis & Snacks"),
    ("snack", "Godis & Snacks"),
    ("chips", "Godis & Snacks"),
    ("choklad", "Godis & Snacks"),
    ("nötter", "Godis & Snacks"),
    ("kex", "Godis & Snacks"),
    ("hushåll", "Hushåll, Hygien & Apotek"),
    ("hygien", "Hushåll, Hygien & Apotek"),
    ("städ", "Hushåll, Hygien & Apotek"),
    ("tvätt", "Hushåll, Hygien & Apotek"),
    ("papper", "Hushåll, Hygien & Apotek"),
    ("toalett", "Hushåll, Hygien & Apotek"),
    ("schampo", "Hushåll, Hygien & Apotek"),
    ("tvål", "Hushåll, Hygien & Apotek"),
    ("tand", "Hushåll, Hygien & Apotek"),
    ("blöj", "Hushåll, Hygien & Apotek"),
    ("apotek", "Hushåll, Hygien & Apotek"),
    ("läkemedel", "Hushåll, Hygien & Apotek"),
    ("receptfritt", "Hushåll, Hygien & Apotek"),
    ("kosttillskott", "Hushåll, Hygien & Apotek"),
    ("tillskott", "Hushåll, Hygien & Apotek"),
    ("vitamin", "Hushåll, Hygien & Apotek"),
    ("medicin", "Hushåll, Hygien & Apotek"),
    ("plåster", "Hushåll, Hygien & Apotek"),
    # Dangerously short substrings kept LAST so a more specific keyword always wins first:
    # "ost" hides inside "kosttillskott" (→ Apotek), "fil" inside "filmjölk" (→ "mjölk" above)
    # and "oxfilé". Only a bare "ost"/"fil" that matched nothing else falls through to Mejeri.
    ("ost", "Mejeri & Ost"),
    ("fil", "Mejeri & Ost"),
)


def is_valid_category(value: str | None) -> bool:
    """True only for an exact canonical value. None/legacy/typo are not valid."""
    return value in _CATEGORY_SET


def normalize_category(value: str | None) -> str | None:
    """Coerce arbitrary input to a canonical category, or None.

    Exact match wins; otherwise the keyword map catches an old free-text value or a stray LLM
    guess. Anything unrecognised becomes None — an empty field the user re-picks beats a stale
    value the <select> cannot even display. Silent coercion (not a 400) on purpose: the only
    non-UI callers are quick-add's LLM path and the JSON import, and both would rather degrade
    to "uncategorised" than fail the whole create.
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if v in _CATEGORY_SET:
        return v
    lowered = v.lower()
    for keyword, category in _CATEGORY_KEYWORDS:
        if keyword in lowered:
            return category
    return None


def category_sort_index(value: str | None) -> int:
    """Aisle-walk position (0 = first section); unknown/None sort last. THE category sort key."""
    try:
        return PRODUCT_CATEGORIES.index(value)  # type: ignore[arg-type]
    except ValueError:
        return _UNRANKED
