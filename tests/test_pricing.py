"""Tests for domain.pricing — the single definition of unit price and package normalization."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Column, Numeric
from sqlalchemy.dialects import postgresql

from domain.pricing import (
    MAX_PACKAGE_QUANTITY,
    apply_scrape_to_link,
    normalize_amount,
    quantity_mismatch,
    scraped_quantity_from,
    unit_price_expr,
    unit_price_py,
)


@dataclass
class _Scrape:
    """A stand-in satisfying the ScrapedPackage protocol."""

    package_amount: Decimal | None = None
    package_unit: str | None = None
    pack_size: int | None = None


@dataclass
class _Link:
    """A stand-in satisfying the LinkLike protocol."""

    package_quantity: Decimal | None = None
    scraped_package_quantity: Decimal | None = None


# --- unit_price_py -----------------------------------------------------------------


def test_unit_price_py_divides_price_by_quantity_unrounded() -> None:
    result = unit_price_py(Decimal("139.90"), Decimal("24"))
    assert result == Decimal("139.90") / Decimal("24")
    # Not quantized here — rounding belongs at the presentation boundary.
    assert result != Decimal("5.83")


def test_unit_price_py_null_quantity_returns_none() -> None:
    assert unit_price_py(Decimal("59.90"), None) is None


def test_unit_price_py_zero_quantity_returns_none() -> None:
    assert unit_price_py(Decimal("59.90"), Decimal("0")) is None


def test_unit_price_py_null_price_returns_none() -> None:
    assert unit_price_py(None, Decimal("24")) is None


# --- unit_price_expr ---------------------------------------------------------------


def test_unit_price_expr_guards_division_with_nullif() -> None:
    price_col = Column("price_sek", Numeric(10, 2))
    qty_col = Column("package_quantity", Numeric(10, 2))
    sql = str(unit_price_expr(price_col, qty_col).compile(dialect=postgresql.dialect()))
    assert "nullif" in sql.lower()


# --- normalize_amount --------------------------------------------------------------


def test_normalize_amount_grams_to_kg() -> None:
    assert normalize_amount(Decimal("400"), "g", "kg") == Decimal("0.40")


def test_normalize_amount_millilitres_to_liter() -> None:
    assert normalize_amount(Decimal("500"), "ml", "liter") == Decimal("0.50")


def test_normalize_amount_pieces_stay_pieces() -> None:
    assert normalize_amount(Decimal("24"), "st", "st") == Decimal("24.00")


def test_normalize_amount_unit_conflict_returns_none() -> None:
    # The page reports grams, but the product is counted in pieces. That is a conflict,
    # not evidence — a garbage number here would corrupt every kr/unit comparison.
    assert normalize_amount(Decimal("400"), "g", "st") is None


def test_normalize_amount_unknown_entry_unit_returns_none() -> None:
    assert normalize_amount(Decimal("6"), "dl", "liter") is None


def test_normalize_amount_above_max_returns_none() -> None:
    hallucinated = MAX_PACKAGE_QUANTITY + Decimal("1")
    assert normalize_amount(hallucinated, "st", "st") is None
    assert normalize_amount(Decimal("1e30"), "st", "st") is None


def test_normalize_amount_zero_and_negative_return_none() -> None:
    assert normalize_amount(Decimal("0"), "st", "st") is None
    assert normalize_amount(Decimal("-24"), "st", "st") is None


# --- scraped_quantity_from ---------------------------------------------------------


def test_scraped_quantity_prefers_explicit_amount_and_unit() -> None:
    scrape = _Scrape(package_amount=Decimal("400"), package_unit="g", pack_size=99)
    assert scraped_quantity_from(scrape, "kg") == Decimal("0.40")


def test_scraped_quantity_pack_size_fallback_fires_for_piece_products() -> None:
    scrape = _Scrape(pack_size=24)
    assert scraped_quantity_from(scrape, "st") == Decimal("24.00")


def test_scraped_quantity_pack_size_fallback_ignored_for_non_piece_products() -> None:
    # "24 items" says nothing about a product measured in litres.
    scrape = _Scrape(pack_size=24)
    assert scraped_quantity_from(scrape, "liter") is None
    assert scraped_quantity_from(scrape, "kg") is None


def test_scraped_quantity_none_when_page_says_nothing() -> None:
    assert scraped_quantity_from(_Scrape(), "st") is None


# --- apply_scrape_to_link (D-07) ---------------------------------------------------


def test_apply_scrape_autofills_when_quantity_is_empty() -> None:
    link = _Link(package_quantity=None)
    message = apply_scrape_to_link(link, _Scrape(pack_size=24), "st")

    assert link.package_quantity == Decimal("24.00")
    assert link.scraped_package_quantity == Decimal("24.00")
    assert message is None


def test_apply_scrape_agreeing_page_changes_nothing() -> None:
    link = _Link(package_quantity=Decimal("24"))
    message = apply_scrape_to_link(link, _Scrape(pack_size=24), "st")

    assert link.package_quantity == Decimal("24")
    assert link.scraped_package_quantity == Decimal("24.00")
    assert message is None


def test_apply_scrape_never_overwrites_a_conflicting_quantity() -> None:
    link = _Link(package_quantity=Decimal("24"))
    message = apply_scrape_to_link(link, _Scrape(pack_size=12), "st")

    # The never-overwrite guarantee (D-07): the operator's typed value is intent.
    assert link.package_quantity == Decimal("24")
    assert link.scraped_package_quantity == Decimal("12.00")
    assert message is not None
    assert "12" in message
    assert "24" in message


def test_apply_scrape_unit_conflict_records_nothing_and_flags_nothing() -> None:
    link = _Link(package_quantity=Decimal("24"))
    scrape = _Scrape(package_amount=Decimal("400"), package_unit="g")
    message = apply_scrape_to_link(link, scrape, "st")

    assert link.package_quantity == Decimal("24")
    assert link.scraped_package_quantity is None
    assert message is None


# --- quantity_mismatch (D-09) ------------------------------------------------------


def test_quantity_mismatch_true_only_when_both_present_and_unequal() -> None:
    conflicting = _Link(package_quantity=Decimal("24"), scraped_package_quantity=Decimal("12"))
    agreeing = _Link(package_quantity=Decimal("24"), scraped_package_quantity=Decimal("24.00"))
    unscraped = _Link(package_quantity=Decimal("24"), scraped_package_quantity=None)
    unset = _Link(package_quantity=None, scraped_package_quantity=Decimal("24"))

    assert quantity_mismatch(conflicting) is True
    assert quantity_mismatch(agreeing) is False
    assert quantity_mismatch(unscraped) is False
    assert quantity_mismatch(unset) is False
