"""The category taxonomy — pure logic, no DB. See src/domain/categories.py."""

from domain.categories import (
    PRODUCT_CATEGORIES,
    category_sort_index,
    is_valid_category,
    normalize_category,
)


def test_taxonomy_is_the_ten_agreed_sections_in_walk_order() -> None:
    # Order IS the contract — it is the aisle-walk order a future shopping list sorts by.
    assert PRODUCT_CATEGORIES == (
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


def test_exact_canonical_value_passes_through() -> None:
    for category in PRODUCT_CATEGORIES:
        assert normalize_category(category) == category
        assert is_valid_category(category)


def test_empty_and_none_normalise_to_none() -> None:
    assert normalize_category(None) is None
    assert normalize_category("") is None
    assert normalize_category("   ") is None
    assert not is_valid_category(None)
    assert not is_valid_category("Mejeri")  # a near-miss is not canonical


def test_legacy_free_text_folds_into_the_right_section() -> None:
    # The old LLM guesses the migration and quick-add have to absorb.
    assert normalize_category("toalettpapper") == "Hushåll, Hygien & Apotek"
    assert normalize_category("kosttillskott") == "Hushåll, Hygien & Apotek"
    assert normalize_category("tandkräm") == "Hushåll, Hygien & Apotek"
    assert normalize_category("mjölk") == "Mejeri & Ost"
    assert normalize_category("Drycker") == "Dryck"
    assert normalize_category("färsk lax") == "Kött, Chark & Deli"
    assert normalize_category("falukorv") == "Kött, Chark & Deli"
    assert normalize_category("köttfärs") == "Kött, Chark & Deli"
    assert normalize_category("glass") == "Fryst"


def test_short_substrings_do_not_swallow_more_specific_words() -> None:
    # "ost" hides inside "kosttillskott" and "fil" inside "filmjölk"/"oxfilé": the specific
    # keyword must win, which is why "ost"/"fil" are ordered last.
    assert normalize_category("kosttillskott") == "Hushåll, Hygien & Apotek"
    assert normalize_category("filmjölk") == "Mejeri & Ost"
    assert normalize_category("oxfilé") == "Mejeri & Ost"  # bare "fil" fallback, no better match
    # A bare "fil" (filmjölk, dairy) still resolves to Mejeri via the trailing fallback.
    assert normalize_category("fil") == "Mejeri & Ost"
    assert normalize_category("ost") == "Mejeri & Ost"
    # "mjöl" (flour) must reach Skafferi, not be swallowed by "mjölk" — order handles both.
    assert normalize_category("mjöl") == "Skafferi"
    assert normalize_category("mjölk") == "Mejeri & Ost"


def test_korvbrod_lands_in_bread_not_chark() -> None:
    # The ordering trap the keyword list is built around: "korvbröd" contains both "bröd"
    # (Bröd & Bageri) and "korv" (Kött, Chark & Deli); "bröd" must win.
    assert normalize_category("korvbröd") == "Bröd & Bageri"


def test_unrecognised_value_becomes_none() -> None:
    assert normalize_category("blahonga") is None


def test_sort_index_is_walk_order_and_unknown_sorts_last() -> None:
    assert category_sort_index("Bröd & Bageri") == 0
    assert category_sort_index("Hushåll, Hygien & Apotek") == len(PRODUCT_CATEGORIES) - 1
    # Every real category sorts before None / an unknown value.
    assert category_sort_index(None) == len(PRODUCT_CATEGORIES)
    assert category_sort_index("blahonga") == len(PRODUCT_CATEGORIES)
    ordered = sorted(["Dryck", None, "Bröd & Bageri", "Fryst"], key=category_sort_index)
    assert ordered == ["Bröd & Bageri", "Fryst", "Dryck", None]
