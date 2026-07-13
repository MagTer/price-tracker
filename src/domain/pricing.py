"""Unit price and package normalization — the single definition (D-03/D-04/D-07/D-08/D-09).

Unit price is COMPUTED, never scraped: ``effective_price / link.package_quantity``.
It exists here exactly once, in two evaluation modes (Python and SQL), so "one definition
by construction" is structurally enforced rather than merely intended.

This module deliberately imports NOTHING from the ORM models module: models imports *this*, and
the reverse would be an import cycle. The two Protocols below give it the structural types it
needs without a hard dependency on the ORM or on the extraction-result dataclass.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy import ColumnElement, func

LOGGER = logging.getLogger(__name__)

# The Python twin of the JS PKG_UNITS table in src/api/templates/admin.html.
# entry unit -> (canonical unit, factor to canonical).
# Decimal factors, never floats: 0.001 is not representable in binary floating point, and a
# g/kg slip of 1000x would make one link look 1000x cheaper and win every comparison.
# tests/test_static_gates.py asserts this table and the JS one cannot drift apart.
PKG_UNITS: dict[str, tuple[str, Decimal]] = {
    "st": ("st", Decimal("1")),
    "ml": ("liter", Decimal("0.001")),
    "l": ("liter", Decimal("1")),
    "g": ("kg", Decimal("0.001")),
    "kg": ("kg", Decimal("1")),
}

# The three values Product.unit may hold — the comparison units that make per-store package
# listings comparable.
CANONICAL_UNITS: tuple[str, ...] = ("st", "liter", "kg")

# Upper bound of a Numeric(10, 2) column. A hallucinated 1e30 from the LLM must be rejected
# here and yield None — not surface as a DataError at flush inside the scheduler tick, where
# the per-product `except Exception` would swallow it and count it as a failed check.
MAX_PACKAGE_QUANTITY: Decimal = Decimal("99999999.99")

# Storage precision of package_quantity / scraped_package_quantity.
_QUANTUM = Decimal("0.01")


class ScrapedPackage(Protocol):
    """The package signals a page fetch yields (D-06/D-08).

    Structural typing keeps this module decoupled from domain.result;
    PriceExtractionResult satisfies it once 04.1-02 adds the fields.
    """

    package_amount: Decimal | None
    package_unit: str | None
    pack_size: int | None


class LinkLike(Protocol):
    """The two quantity columns on a ProductStore link (D-02/D-09)."""

    package_quantity: Decimal | None
    scraped_package_quantity: Decimal | None


def unit_price_py(price: Decimal | None, qty: Decimal | None) -> Decimal | None:
    """Python-side unit price. None when the link has no amount yet (D-02).

    Not quantized: rounding belongs at the presentation boundary. Rounding early and then
    comparing is how two "equal" unit prices start disagreeing.
    """
    if price is None or qty is None or qty == 0:
        return None
    return price / qty


def unit_price_expr(price_col: ColumnElement[Any], qty_col: ColumnElement[Any]) -> ColumnElement[Any]:
    """SQL-side unit price — the same definition as unit_price_py.

    NULLIF is the divide-by-zero guard. A NULL quantity yields NULL by SQL semantics, and
    Postgres sorts NULLs last on an ascending ORDER BY, so "needs amount" links sink to the
    bottom of a cheapest-first sort for free.
    """
    return price_col / func.nullif(qty_col, 0)


def normalize_amount(
    amount: Decimal | None, entry_unit: str | None, product_unit: str | None
) -> Decimal | None:
    """Convert an entry amount + entry unit into the product's canonical quantity.

    Returns None — never a garbage number — when the unit is unknown, when the entry unit's
    canonical form conflicts with the product's unit (a page reporting grams for a product
    whose unit is `st` is a CONFLICT, not evidence), or when the value falls outside what a
    Numeric(10, 2) column can faithfully hold.
    """
    if amount is None or entry_unit is None:
        return None

    spec = PKG_UNITS.get(entry_unit.strip().lower())
    if spec is None:
        LOGGER.info("Unknown package entry unit %r — ignoring the reading", entry_unit)
        return None
    canonical, factor = spec

    normalized_product_unit = product_unit.strip().lower() if product_unit else None
    if normalized_product_unit != canonical:
        LOGGER.info(
            "Package unit conflict: entry unit %r normalizes to %r but the product's unit is %r"
            " — ignoring the reading",
            entry_unit,
            canonical,
            product_unit,
        )
        return None

    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        LOGGER.info("Package amount %r is not a number — ignoring the reading", amount)
        return None
    if not value.is_finite():
        LOGGER.info("Package amount %r is not finite — ignoring the reading", amount)
        return None

    quantity = value * factor
    if quantity <= 0 or quantity > MAX_PACKAGE_QUANTITY:
        LOGGER.info("Package quantity %s is out of range — ignoring the reading", quantity)
        return None

    quantized = quantity.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized <= 0:
        # e.g. 1 g against a kg product: 0.001, which Numeric(10, 2) cannot hold.
        LOGGER.info("Package quantity %s rounds to zero at storage precision — ignoring", quantity)
        return None
    return quantized


def scraped_quantity_from(
    extraction: ScrapedPackage, product_unit: str | None
) -> Decimal | None:
    """The page's reading of the package quantity, in the product's canonical unit (D-06/D-08).

    Prefers the explicit amount + unit. Falls back to the title-derived item count (`16-p`),
    which is only meaningful when the product is counted in pieces.
    """
    quantity = normalize_amount(extraction.package_amount, extraction.package_unit, product_unit)
    if quantity is not None:
        return quantity

    if extraction.pack_size is None:
        return None
    if not product_unit or product_unit.strip().lower() != "st":
        # pack_size is an item count. For a 0.5-liter bottle or a 400 g bag it means nothing.
        return None
    return normalize_amount(Decimal(str(extraction.pack_size)), "st", product_unit)


def apply_scrape_to_link(
    link: LinkLike, extraction: ScrapedPackage, product_unit: str | None
) -> str | None:
    """Autofill when empty; flag on conflict; NEVER overwrite (D-07/D-09).

    This is the ONLY implementation of that rule — all three scrape write-paths call it.
    Returns a human-readable mismatch message, or None.

    The typed value is *intent*; the page is *evidence*. Evidence does not get to silently
    rewrite intent, now that kr/unit and the entire history view are computed from that number:
    one bad extraction would otherwise silently corrupt every comparison.
    """
    scraped = scraped_quantity_from(extraction, product_unit)

    # Always record the page's own reading, even when it is None (D-09). The mismatch flag is
    # DERIVED from the two columns, so it self-clears the moment either number is corrected.
    link.scraped_package_quantity = scraped

    if scraped is None:
        return None

    if link.package_quantity is None:
        link.package_quantity = scraped  # autofill when empty
        return None

    if link.package_quantity != scraped:
        return f"page says {scraped}, you have {link.package_quantity}"

    return None


def quantity_mismatch(link: LinkLike) -> bool:
    """The derived conflict flag (D-09). Never persisted — deriving it makes it self-clearing."""
    return (
        link.package_quantity is not None
        and link.scraped_package_quantity is not None
        and link.package_quantity != link.scraped_package_quantity
    )
