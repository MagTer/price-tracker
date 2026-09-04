"""Shared result types for price and product-metadata extraction."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class StoreBlockedError(Exception):
    """A store's bot wall answered instead of data — the HOST is blocking us, not one URL.

    Raised by extractors whose HTTP call hits a WAF (HTTP 202/403/429), because returning
    None there is indistinguishable from "product not found": the ladder would fall through
    to the LLM on an empty page, the check would end as a routine "no_price", and the callers
    would record a SUCCESS against the circuit breaker — resetting the escalation for a store
    that is actively walling us. An exception is the only return channel that survives the
    whole extraction ladder without threading a flag through every result type.
    """


@dataclass
class ProductMetadata:
    """What a product PAGE says the product is — used by quick-add, never by price checks.

    Quick-add needs identity fields (name, brand, category) that PriceExtractionResult
    deliberately does not carry: a price check already knows what product it is checking.
    `price_sek` here is preview display only — the recorded first price always comes from
    the normal perform_price_check flow, so quick-add cannot become a second write path.
    """

    name: str | None
    brand: str | None
    category: str | None
    price_sek: Decimal | None
    package_amount: Decimal | None
    package_unit: str | None
    pack_size: int | None
    confidence: float
    source: str  # "jsonld" | "llm" | "willys_api"
    in_stock: bool | None = None  # availability when the source states it (store APIs do)


@dataclass
class PriceExtractionResult:
    """Result from price extraction."""

    price_sek: Decimal | None
    store_unit_price_sek: (
        Decimal | None
    )  # The store's PRINTED comparison price — never computed (D-05)
    offer_price_sek: Decimal | None
    offer_type: str | None  # "stammispris", "extrapris", "kampanj", etc.
    offer_details: str | None  # "Kop 2 betala for 1"
    in_stock: bool
    confidence: float
    pack_size: int | None  # Number of items in pack (e.g., 16 for "16-p")
    package_amount: Decimal | None  # Amount of product in the package as printed (0.5, 400, 24)
    package_unit: str | None  # Unit that amount is in: "st", "ml", "l", "g", "kg"
    raw_response: dict[str, str | float | bool | None]


def extraction_source(result: PriceExtractionResult | None) -> str:
    """WHICH ladder tier produced this extraction — THE reader of ``raw_response["source"]``.

    Every extractor stamps its own marker ("willys_api", "rusta_page", "clasohlson_page",
    "lyko_page", "jsonld"); the LLM path stamps "llm:<model>". The value is deliberately raw
    rather than bucketed into a tier: which MODEL answered is what makes LLM spend readable,
    and grouping is a display decision.

    It exists because the same `isinstance(raw, dict)` dance had been written three times —
    the scheduler's per-tier counters, the JSON-LD enrichment trigger in perform_price_check,
    and the check log — over a loosely-typed JSONB dict where a typo returns None and simply
    reads as "the LLM answered". One definition, like pricing and schedule.

    An ENRICHED JSON-LD result still reports "jsonld": enrich_with_llm keeps the base
    raw_response precisely so a routine enrichment cannot masquerade as an LLM extraction.
    """
    if result is None:
        return "unknown"
    raw = result.raw_response
    if not isinstance(raw, dict):
        return "unknown"
    source = raw.get("source")
    return source if isinstance(source, str) and source else "unknown"


# ICA's own presentation flag on a promotion. MUTE_STYLE is the value that has never yet
# coincided with an offer in the store's printed veckoblad (measured below).
_ICA_MUTED_PRESENTATION = "MUTE_STYLE"


def offer_online_only(raw_data: Any) -> bool | None:
    """Whether the store presented this offer as one its shelf may not carry.

    THE reader of ``raw_data["offer_presentation_mode"]`` — the ICA extractor records the
    flag verbatim off the promotion whose price it recorded, and every consumer (the buy
    list, the portal row, MCP) resolves the judgement here rather than testing the string,
    so the mail and the page cannot come to disagree about one campaign.

    **True is a HYPOTHESIS with evidence behind it, not a statement by ICA — and it is
    known to be INCOMPLETE.** Measured 2026-09-04 against both Sandviken butiker's public
    erbjudandesidor (187 + 48 offers, each carrying its own ``storeInd``/``onlineInd``):
    of the six offers the tracker held that day, the four flagged ``MUTE_STYLE`` were
    absent from the store's veckoblad and the one present in it was ``DEFAULT``. A second
    signal agrees — over all 26 ICA offers ever recorded, 22 lived 0-7 days (the
    veckoblad's own cadence) and the four long-runners (11-31 days) are exactly those four.

    **What this flag does NOT catch, established the same day (corrected in place):** the
    trip that produced the whole investigation — a buy-list row that did not exist at the
    till, staff said online only — was Arla Köket smör- & rapsolja 7,5dl at ICA Maxi, and
    its promotion is ``DEFAULT``. So MUTE_STYLE implies "absent from the veckoblad" on the
    evidence so far, but the converse is false in BOTH directions that matter: a DEFAULT
    campaign can be online-only too, and ``False`` never means "verified in the store".
    Consumers MARK a True and say nothing otherwise — an unmarked row is not a promise.

    The sharper signal is recorded beside this one and not yet acted on:
    ``offer_retailer_promotion_id``'s first segment IS the id of the corresponding offer
    on the butik's erbjudandesida (``offers.weeklyOffers[].id``), so leaflet membership is
    an EXACT join rather than a name match — 6/6 on that day's sample, including the
    rapsolja the flag misses. Reading it needs one fetch per butik per check day, which is
    a decision about a new external source, not a rename.

    None means UNKNOWN and is the normal answer nearly everywhere: every other store
    records no such flag, and so does every ICA point written before v0.60.0 — the field
    cannot be backfilled, since a promotion that has ended is gone from the page. An
    absent flag is not evidence of a store offer, which is why False and None are
    deliberately NOT collapsed into one falsy value here.

    To falsify: pull www.ica.se/erbjudanden/<butik>/, read ``offers.weeklyOffers[]`` from
    ``window.__INITIAL_DATA__`` and look for a MUTE_STYLE offer among them.
    """
    if not isinstance(raw_data, dict):
        return None
    mode = raw_data.get("offer_presentation_mode")
    if not isinstance(mode, str) or not mode:
        return None
    return mode.upper() == _ICA_MUTED_PRESENTATION


def offer_in_leaflet(raw_data: Any) -> bool | None:
    """Whether this campaign was among the butik's ADVERTISED offers when we checked.

    THE reader of ``raw_data["offer_in_leaflet"]``, written at check time by
    ``ica_leaflet.annotate_extraction`` from an exact id join (v0.61.0). Three states, and
    the None is the important one: no leaflet configured for that butik, a walled or
    unparseable erbjudandesida, a store that has no such thing — all mean we did not read
    it, and none of them may render as "not advertised".

    True does not promise the till charges it either. The veckoblad is what the butik
    ADVERTISES, and an unadvertised shelf price is a real thing — which is why the note
    below says "finns i veckobladet" rather than anything about the till.
    """
    if not isinstance(raw_data, dict):
        return None
    value = raw_data.get("offer_in_leaflet")
    return value if isinstance(value, bool) else None


def leaflet_valid_to(raw_data: Any) -> str | None:
    """The date the butik itself put on the advertised offer (ISO), or None."""
    if not isinstance(raw_data, dict):
        return None
    value = raw_data.get("offer_leaflet_valid_to")
    return value if isinstance(value, str) and value else None


def leaflet_store_ind(raw_data: Any) -> bool | None:
    """The butik's own "gäller i butiken" flag on the advertised offer, or None."""
    if not isinstance(raw_data, dict):
        return None
    value = raw_data.get("offer_leaflet_store_ind")
    return value if isinstance(value, bool) else None


def channel_note(
    *,
    in_leaflet: bool | None,
    valid_to: str | None = None,
    store_ind: bool | None = None,
    online_only: bool | None = None,
) -> str | None:
    """THE one sentence about where an offer applies — or None when we have nothing to say.

    Composed in one place because it has three surfaces (the buy-list mail, the portal row
    and MCP) and they may not drift: it is exactly the setup Gotcha 4 describes, and the
    sentence is the whole point of the feature. Swedish, like every other string that
    reaches a reader (deals.py's own "erbjudande" fallback sets the precedent).

    It takes the VALUES rather than a row or a raw_data dict so the deal row can derive it
    as a property: a stored note would let a hand-built row carry the flags with no
    sentence, which is precisely how the notifier's own fixtures went quiet the first time
    this was written.

    Precedence, strongest evidence first:

    1. The butik advertised it and said it does NOT apply in the shop (``storeInd`` false)
       — the only statement here that comes from the store rather than from us.
    2. It is not among the advertised offers at all. That is a fact about the veckoblad,
       so the sentence says veckoblad and only then names the likely reason.
    3. It IS advertised — say so, with the date the butik itself put on it. Positive, and
       the reason a reader can trust the silence on every other row.
    4. Nothing was read from a leaflet: fall back to v0.60.0's ``presentationMode`` hint,
       which is weaker (it misses DEFAULT online-only campaigns, the rapsolja case) but
       better than nothing. Everything else says nothing at all.
    """
    if in_leaflet is True:
        if store_ind is False:
            return "butiken anger att erbjudandet inte gäller i butiken"
        if valid_to:
            return f"finns i butikens veckoblad t.o.m. {_swedish_day(valid_to)}"
        return "finns i butikens veckoblad"
    if in_leaflet is False:
        return "finns inte i butikens veckoblad — kan gälla endast e-handeln"
    if online_only:
        return "kan gälla endast e-handeln"
    return None


def offer_channel_note(raw_data: Any) -> str | None:
    """``channel_note`` read straight off a stored point's raw_data."""
    return channel_note(
        in_leaflet=offer_in_leaflet(raw_data),
        valid_to=leaflet_valid_to(raw_data),
        store_ind=leaflet_store_ind(raw_data),
        online_only=offer_online_only(raw_data),
    )


def _swedish_day(iso_date: str) -> str:
    """ "2026-09-06" as "6/9" — the way a date is written on a Swedish shelf label."""
    try:
        parts = iso_date.split("-")
        return f"{int(parts[2])}/{int(parts[1])}"
    except (IndexError, ValueError):
        return iso_date
