"""Structural interface for the veckoblad lookup, so domain/ stays free of infra imports."""

from __future__ import annotations

from typing import Protocol

from domain.ica_leaflet import LeafletOffer


class ILeafletLookup(Protocol):
    """A butik's currently advertised offers, keyed by the id a promotion joins on.

    Returning None means UNKNOWN — no leaflet configured for that butik, the store walled
    us, the fetch failed, or the page did not parse. It NEVER means "the butik advertises
    nothing": a caller that treated an empty answer as fact would mark every campaign as
    missing from a leaflet nobody read.
    """

    async def offers_for(self, store_url: str) -> dict[str, LeafletOffer] | None: ...
