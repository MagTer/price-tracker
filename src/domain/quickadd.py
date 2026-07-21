"""Quick-add: the decision logic for creating a product + store link from one pasted URL.

Pure functions only — no HTTP, no DB, no LLM. The API layer (admin.py's
POST /quick-add/preview and POST /quick-add) does the fetching and persisting and calls
down into this module for every judgement call, so each rule here is testable without a
network or a database.

Design constraints this module enforces:

- **Store detection is coded, never guessed.** There are exactly five stores, each with a
  seeded ``base_url``; a pasted URL either hostname-matches one of them or quick-add refuses.
  A suffix match would be wrong, not just loose: ``www.ica.se`` (recipes) is not
  ``handlaprivatkund.ica.se`` (the shop the extractors understand).

- **Quick-add must not undo the 04.1 model.** A URL is a LINK (one package listing), and the
  product it belongs to may already exist — "Lambi 24-pack at Willys" pasted after
  "Lambi 8-pack at ICA" is a second link on ONE product, not a second product.
  ``suggest_existing_products`` exists so the preview can offer that choice; a quick-add that
  always created a new product would quietly rebuild the one-product-per-pack-size world the
  whole phase abolished.

- **The package guess is a suggestion, not a write.** Whatever is parsed here lands in an
  editable preview field; the persisted quantity still goes through the operator (intent) or
  the first scrape's D-07 autofill (evidence). This module never touches a link.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from urllib.parse import urlparse

from domain.pricing import PKG_UNITS

# Same token rule as the JSON-LD name-sanity check: 3+ alphanumerics, Swedish letters
# included. Shorter fragments ("3", "st", "p") appear in virtually every product title
# and would fake an overlap.
_NAME_TOKEN_RE = re.compile(r"[a-z0-9åäö]{3,}")

# Amount + unit as printed in a product title: "500 ml", "0,5 l", "1.5kg". Longest
# alternatives first — "ml" must win before "l", "kg" before "g". The \b keeps "3 lager"
# from reading as "3 l".
_AMOUNT_UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(ml|kg|l|g|st)\b", re.IGNORECASE)

# Item count in a title: "24-pack", "16 st", "8 rullar", "100 tabletter".
_PACK_RE = re.compile(
    r"(\d+)\s*[- ]?\s*(?:pack\b|st\b|rullar\b|tabletter\b|kapslar\b|påsar\b)",
    re.IGNORECASE,
)

# A butik-scoped path segment, as used by handlaprivatkund.ica.se:
#   https://handlaprivatkund.ica.se/stores/1003396/products/...
# The id after /stores/ IS the physical butik — two butiker sell the same product at
# different prices on two different URLs.
_STORE_SEGMENT_RE = re.compile(r"/stores/([^/]+)/")

# Butik id -> human name, for the label SUGGESTION only (the field stays editable, and an
# unknown id falls back to "<chain> <id>"). Single-user app: these are Magnus's stores.
KNOWN_STORE_LABELS: dict[str, str] = {
    "1003396": "ICA Maxi Sandviken",
    "1004503": "ICA Supermarket Björksätra",
}

# Butiker whose assortments overlap enough that a product added at one is (almost always)
# also sold at the others — quick-add offers to create the sibling links in the same
# confirm. Groups, not pairs, so a third butik is one edit here.
SIBLING_STORE_GROUPS: tuple[frozenset[str], ...] = (frozenset({"1003396", "1004503"}),)


class StoreLike(Protocol):
    """The two Store columns quick-add matching needs."""

    base_url: str


@dataclass
class PackageGuess:
    """A package suggestion parsed from a product NAME — preview prefill, never persisted."""

    amount: Decimal | None  # as printed ("500" for "500 ml")
    entry_unit: str | None  # the printed unit, a PKG_UNITS key
    pack_size: int | None  # item count from "24-pack" style patterns
    label: str | None  # human label for the link's package_size field


def _host(url: str) -> str | None:
    """Lowercased hostname with any port and leading ``www.`` stripped; None if unparsable."""
    try:
        netloc = urlparse(url).netloc
    except ValueError:
        return None
    host = netloc.split("@")[-1].split(":")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def match_store_by_url(url: str, stores: list) -> object | None:
    """The store whose seeded base_url hostname EXACTLY matches the pasted URL's hostname.

    Exact (www-insensitive) equality on purpose: matching by suffix would route
    ``www.ica.se`` recipe pages to the ``handlaprivatkund.ica.se`` extractors.
    """
    host = _host(url)
    if host is None:
        return None
    for store in stores:
        if _host(store.base_url) == host:
            return store
    return None


def parse_package_from_name(name: str | None) -> PackageGuess:
    """Best-effort package reading from a product title, with coded logic only.

    Prefers an explicit amount+unit ("500 ml") over an item count ("24-pack") — mirroring
    ``pricing.scraped_quantity_from``, which prefers the same signal from a scrape.
    """
    if not name:
        return PackageGuess(amount=None, entry_unit=None, pack_size=None, label=None)

    amount_match = _AMOUNT_UNIT_RE.search(name)
    pack_match = _PACK_RE.search(name)

    if amount_match and amount_match.group(2).lower() != "st":
        amount = Decimal(amount_match.group(1).replace(",", "."))
        unit = amount_match.group(2).lower()
        return PackageGuess(
            amount=amount,
            entry_unit=unit,
            pack_size=int(pack_match.group(1)) if pack_match else None,
            label=f"{amount_match.group(1)} {unit}",
        )

    if pack_match:
        count = int(pack_match.group(1))
        return PackageGuess(
            amount=Decimal(count),
            entry_unit="st",
            pack_size=count,
            label=f"{count}-pack",
        )

    return PackageGuess(amount=None, entry_unit=None, pack_size=None, label=None)


def derive_unit(entry_unit: str | None, pack_size: int | None) -> str | None:
    """The product's canonical comparison unit implied by a package reading.

    ml/l → liter, g/kg → kg, st → st (PKG_UNITS is the single conversion table). A bare
    item count with no unit implies pieces. None when there is no signal — the operator
    picks, exactly as in the manual flow.
    """
    if entry_unit:
        spec = PKG_UNITS.get(entry_unit.strip().lower())
        if spec:
            return spec[0]
    if pack_size:
        return "st"
    return None


def suggest_store_label(url: str, store_name: str) -> str | None:
    """A per-butik display label suggested from the URL's /stores/<id>/ segment.

    ICA prices per physical butik, so two butik URLs for one product are two links with
    different prices — and without a label both render as "ICA". Known butik ids map to
    their human names; an unknown id still yields a distinguishing "<chain> <id>". None
    when the URL has no butik segment (Willys, the pharmacies) — those chains price
    nationally and the chain name suffices.
    """
    match = _STORE_SEGMENT_RE.search(url)
    if not match:
        return None
    store_id = match.group(1)
    return KNOWN_STORE_LABELS.get(store_id, f"{store_name} {store_id}")


@dataclass
class SiblingLink:
    """A sister-butik link quick-add can create alongside the pasted one."""

    url: str
    store_label: str | None


def suggest_sibling_links(url: str, store_name: str) -> list[SiblingLink]:
    """The same product's URL at the OTHER butiker in the pasted butik's sibling group.

    On handlaprivatkund.ica.se the product slug and id are chain-wide and only the
    /stores/<id>/ segment is butik-specific — verified live 2026-07-21 by fetching swapped
    URLs (same product node, different price: 13.20 kr at Maxi vs 12.25 kr at Björksätra
    for the same potatissallad). A swap can still miss (assortment differs per butik), so
    the caller must treat these as CANDIDATES and verify each with a real fetch before
    keeping the link.
    """
    match = _STORE_SEGMENT_RE.search(url)
    if not match:
        return []
    store_id = match.group(1)
    for group in SIBLING_STORE_GROUPS:
        if store_id in group:
            return [
                SiblingLink(
                    url=url.replace(f"/stores/{store_id}/", f"/stores/{other}/", 1),
                    store_label=KNOWN_STORE_LABELS.get(other, f"{store_name} {other}"),
                )
                for other in sorted(group - {store_id})
            ]
    return []


def suggest_existing_products(
    name: str | None,
    candidates: list[dict],
    limit: int = 3,
) -> list[dict]:
    """Existing products the pasted URL might be a NEW LINK on, best match first.

    Token-overlap scoring against each candidate's name+brand. Deliberately simple: at
    single-user scale (tens of products) recall matters more than ranking finesse, and the
    result is a preview suggestion the operator confirms, never an automatic merge.

    Each candidate dict needs ``id``, ``name``; ``brand`` and ``unit`` ride along into the
    result untouched.
    """
    if not name:
        return []
    wanted = set(_NAME_TOKEN_RE.findall(name.lower()))
    if not wanted:
        return []

    scored = []
    for candidate in candidates:
        haystack = f"{candidate.get('name') or ''} {candidate.get('brand') or ''}".lower()
        overlap = wanted & set(_NAME_TOKEN_RE.findall(haystack))
        if overlap:
            scored.append((len(overlap), candidate))

    # Sort by score desc, then name for a stable order between renders.
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    return [dict(candidate, match_score=score) for score, candidate in scored[:limit]]
