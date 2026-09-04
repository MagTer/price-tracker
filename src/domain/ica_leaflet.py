"""ICA's veckoblad — the butik's OWN advertised campaigns, and THE join to a promotion.

The tracker reads ICA's e-handel, and for a year that was a faithful mirror of the butik.
On 2026-09-04 it was not: a buy-list row (Arla Köket smör- & rapsolja 7,5dl, ICA Maxi,
"30 kr/st") was driven to, did not exist at the till, and the staff said it was online
only. v0.60.0 recorded the promotion's own ``presentationMode`` on the strength of a
correlation — and that flag is DEFAULT on exactly that row, so it would have stayed
silent about the one campaign we KNOW was e-handel only.

The signal that does answer it is an EXACT join, not a name match. A promotion's
``retailerPromotionId`` is two segments, and the FIRST is the id of the corresponding
offer on the butik's own erbjudandesida::

    promotion 5004133591-1787037115  ->  weeklyOffers[].id 5004133591
                                         ("Makaroner, Svenska pastaformer",
                                          "5 för 50 kr", valid to 2026-09-06)

Verified 6/6 on 2026-09-04 across both Sandviken butiker (187 offers at Maxi, 48 at
Björksätra): the one tracked offer present in a veckoblad joined, and the five absent
ones did not — including the rapsolja. **The id PREFIX is not the signal** and must never
be used as one: both butiker's veckoblad carry ``200…``-prefixed offers of their own
(2005267735 at Maxi, 2005258771 at Björksätra), so membership in the fetched set is the
whole test.

What the leaflet answers is "did the butik ADVERTISE this campaign", which is not the
same question as "will the till charge it" — an unadvertised shelf price is a real thing.
So absence is reported as absence from the veckoblad and nothing stronger; the wording
each surface prints says exactly that.

This module is pure: parsing, the join key and the per-butik URL table. The fetch (which
needs the shared ledger and the circuit breaker, because www.ica.se is a different host
than handlaprivatkund.ica.se but the same WAF family) lives in ``infra/ica_leaflet.py``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from domain.quickadd import butik_id_from_url
from domain.result import PriceExtractionResult

if TYPE_CHECKING:
    # Imported for typing only: domain/protocols/leaflet.py names LeafletOffer from THIS
    # module, so a runtime import here would close the cycle. `from __future__ import
    # annotations` makes the signature below a string, so nothing needs it at run time.
    from domain.protocols.leaflet import ILeafletLookup

logger = logging.getLogger(__name__)

# --- Per-instance butik config -----------------------------------------------------------
#
# A butik's leaflet address is OPERATOR data, exactly like QUICKADD_STORE_LABELS: the repo
# is public and another instance shops elsewhere. The slug is NOT derivable from the
# handlaprivatkund URL we track ("maxi-ica-stormarknad-sandviken" vs "ica-supermarket-
# bjorksatra" — different chain words, and the format is ICA's to change), so at two
# butiker a table beats discovery. Malformed JSON warns and falls back rather than
# silently turning the cross-check off.

_DEFAULT_LEAFLET_URLS: dict[str, str] = {
    "1003396": "https://www.ica.se/erbjudanden/maxi-ica-stormarknad-sandviken-1003396/",
    "1004503": "https://www.ica.se/erbjudanden/ica-supermarket-bjorksatra-1004503/",
}

# The one host these URLs may point at. An operator typo that sent leaflet fetches to
# another domain would be a silent SSRF-ish surprise in a scheduled job, and this table is
# the only place a URL enters the outgoing path — so it is validated where it is loaded.
_LEAFLET_HOST_PREFIX = "https://www.ica.se/"


def load_leaflet_urls(raw: str | None) -> dict[str, str]:
    """Parse ICA_LEAFLET_URLS: a JSON object of butik id -> erbjudandesida URL.

    Example: ``{"1003396": "https://www.ica.se/erbjudanden/maxi-ica-...-1003396/"}``.
    Entries that are not https://www.ica.se/ URLs are DROPPED with a warning rather than
    fetched: this table feeds a scheduled outgoing request, so a typo must fail closed.
    """
    if not raw or not raw.strip():
        return dict(_DEFAULT_LEAFLET_URLS)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object of butik id -> URL")
    except (ValueError, TypeError) as e:
        logger.warning("Malformed ICA_LEAFLET_URLS (%s) - using built-in defaults", e)
        return dict(_DEFAULT_LEAFLET_URLS)

    urls: dict[str, str] = {}
    for key, value in data.items():
        url = str(value)
        if not url.startswith(_LEAFLET_HOST_PREFIX):
            logger.warning(
                "ICA_LEAFLET_URLS entry %r does not point at %s - ignoring it",
                key,
                _LEAFLET_HOST_PREFIX,
            )
            continue
        urls[str(key)] = url
    return urls


LEAFLET_URLS: dict[str, str] = load_leaflet_urls(os.getenv("ICA_LEAFLET_URLS"))


def leaflet_url_for(store_url: str) -> str | None:
    """The erbjudandesida for the butik a tracked ICA link belongs to, or None.

    None means "no leaflet is configured for this butik", which is UNKNOWN and not "no
    offers" — a butik nobody configured must never have its campaigns marked as missing
    from a veckoblad we did not read.
    """
    butik_id = butik_id_from_url(store_url)
    if butik_id is None:
        return None
    return LEAFLET_URLS.get(butik_id)


def promotion_leaflet_id(retailer_promotion_id: Any) -> str | None:
    """The leaflet offer id inside a promotion's ``retailerPromotionId``, or None.

    The value is "<leaflet offer id>-<something ICA owns>"; only the first segment joins.
    A value with no separator, or an empty/odd one, yields None — UNKNOWN, so the caller
    records no verdict rather than judging on half an id.
    """
    if not isinstance(retailer_promotion_id, str):
        return None
    head = retailer_promotion_id.split("-", 1)[0].strip()
    return head if head.isdigit() else None


@dataclass(frozen=True)
class LeafletOffer:
    """One offer from a butik's veckoblad, as the page publishes it."""

    offer_id: str
    name: str
    brand: str
    mechanic: str
    valid_to: date | None
    # The butik's own channel flags for this offer. store_ind False is a genuinely
    # e-handel-only leaflet offer; online_ind False is the butiksunika kind e-handeln can
    # never show us at all (2 of Maxi's 187 on 2026-09-04).
    store_ind: bool | None
    online_ind: bool | None


# The page is server-rendered and the offers live in a JS object literal, not in JSON.
_INITIAL_DATA_MARKER = "window.__INITIAL_DATA__ = "

# JS values that are legal in that literal and not in JSON. They are patched AT THE EXACT
# POSITION the decoder stops, never by a sweeping regex: "undefined" inside a string value
# (a product name, a disclaimer) parses fine as part of that string and must not be
# rewritten. A literal we do not know about ends the decode as UNKNOWN.
_JS_LITERALS: tuple[tuple[str, str], ...] = (
    ("undefined", "null"),
    ("new Map([])", "null"),
)
# A bound so a pathological page cannot spin here. The live pages needed ~30 patches.
_MAX_PATCHES = 2000


def _decode_initial_data(html: str) -> Any | None:
    """The ``window.__INITIAL_DATA__`` object, or None when it cannot be read."""
    start = html.find(_INITIAL_DATA_MARKER)
    if start < 0:
        return None
    text = html[start + len(_INITIAL_DATA_MARKER) :]
    decoder = json.JSONDecoder()
    for _ in range(_MAX_PATCHES):
        try:
            obj, _end = decoder.raw_decode(text)
            return obj
        except json.JSONDecodeError as e:
            for literal, replacement in _JS_LITERALS:
                if text.startswith(literal, e.pos):
                    text = text[: e.pos] + replacement + text[e.pos + len(literal) :]
                    break
            else:
                logger.warning(
                    "ICA leaflet: __INITIAL_DATA__ stopped parsing at %d (%s)", e.pos, e.msg
                )
                return None
    logger.warning("ICA leaflet: gave up after %d literal patches", _MAX_PATCHES)
    return None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_leaflet(html: str) -> dict[str, LeafletOffer] | None:
    """Every offer in a butik's veckoblad, keyed by the id a promotion joins on.

    None means UNKNOWN — the page did not parse, carried no offers array, or carried an
    EMPTY one. **The empty case is deliberately not an empty result**: a butik always
    advertises something (187 and 48 on the day this was measured), so an empty list is
    far more likely a shape change or a page that never hydrated than a week with no
    campaigns — and returning {} would mark every one of that butik's offers as absent
    from a veckoblad we never actually read. Every failure here degrades to "we do not
    know", never to a verdict.
    """
    data = _decode_initial_data(html)
    if not isinstance(data, dict):
        return None
    offers = data.get("offers")
    weekly = offers.get("weeklyOffers") if isinstance(offers, dict) else None
    if not isinstance(weekly, list) or not weekly:
        logger.warning("ICA leaflet: no weeklyOffers array on the page")
        return None

    parsed: dict[str, LeafletOffer] = {}
    for raw in weekly:
        if not isinstance(raw, dict):
            continue
        offer_id = raw.get("id")
        if not isinstance(offer_id, str) or not offer_id:
            continue
        details = raw.get("details")
        details = details if isinstance(details, dict) else {}
        stores = raw.get("stores")
        store = stores[0] if isinstance(stores, list) and stores else {}
        store = store if isinstance(store, dict) else {}
        parsed[offer_id] = LeafletOffer(
            offer_id=offer_id,
            name=str(details.get("name") or ""),
            brand=str(details.get("brand") or ""),
            mechanic=str(details.get("mechanicInfo") or ""),
            valid_to=_as_date(raw.get("validTo")),
            store_ind=_as_bool(store.get("storeInd")),
            online_ind=_as_bool(store.get("onlineInd")),
        )
    return parsed or None


async def annotate_extraction(
    extraction: PriceExtractionResult,
    store_url: str,
    lookup: ILeafletLookup | None,
) -> None:
    """Stamp the veckoblad verdict into an extraction's raw_response, in place.

    Called from the check flow because the answer belongs to the OBSERVATION: the mail,
    the portal and MCP then read one stored verdict instead of each fetching a leaflet at
    read time, and a row keeps saying what was true the morning it was recorded.

    Writes nothing at all unless there is something to say — no offer, no promotion id, no
    configured butik or an unreadable leaflet all leave the keys ABSENT, which every
    reader treats as unknown. **Never raises**: a note on a price check must not be able to
    take the price check down (the check_attempts rule).
    """
    if lookup is None or extraction.offer_price_sek is None:
        return
    raw = extraction.raw_response
    if not isinstance(raw, dict):
        return
    offer_id = promotion_leaflet_id(raw.get("offer_retailer_promotion_id"))
    if offer_id is None:
        return
    try:
        offers = await lookup.offers_for(store_url)
    except Exception:
        logger.exception("ICA leaflet: lookup for %s raised - recording no verdict", store_url)
        return
    if offers is None:
        return

    match = offers.get(offer_id)
    raw["offer_in_leaflet"] = match is not None
    if match is not None:
        raw["offer_leaflet_valid_to"] = match.valid_to.isoformat() if match.valid_to else None
        # The butik's OWN channel flag on the advertised offer — the only statement in this
        # whole chain that comes from the store rather than from our inference.
        raw["offer_leaflet_store_ind"] = match.store_ind
    else:
        logger.info(
            "ICA promotion %s is not among the butik's %d advertised offers", offer_id, len(offers)
        )
