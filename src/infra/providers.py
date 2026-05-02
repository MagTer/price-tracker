def get_fetcher():  # type: ignore[no-untyped-def]
    raise NotImplementedError(
        "Fetcher not configured. Phase 2 wires the httpx fetcher here."
    )
