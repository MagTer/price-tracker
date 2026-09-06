#!/usr/bin/env python
"""ICA-nattprobe: NÄR byter ICA priserna — midnatt (batch) eller på morgonen (manuellt)?

Körs av cron natten söndag 2 aug → måndag 3 aug 2026 (se README.md). Varje körning
hämtar 4 produktsidor, läser priset ur JSON-LD med appens egen extractor, appendar till
results.jsonl och skriver om RESULTAT.md — så facit går att läsa med `cat`, utan Claude
och utan att minnas frågeställningen.

Självspärr: kör bara 2026-08-02/03 UTC (cron-datumfält återkommer årligen), utom med
--force för test. Delar publik IP med prod — 4 URL:er × 5 körningar med 25–45 s mellanrum
är avsiktligt minimalt.
"""

import asyncio
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Resolved from this file, not hardcoded. The probe lived outside the repo until
# 2026-09-06 and carried an absolute path to one machine's clone; now that it is
# committed here, "re-run it wider" must not require editing it first.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from curl_cffi.requests import AsyncSession  # noqa: E402

from domain.extractors.jsonld import JsonLdExtractor  # noqa: E402

BASE = Path(__file__).resolve().parent
RESULTS = BASE / "results.jsonl"
REPORT = BASE / "RESULTAT.md"

ALLOWED_UTC_DATES = {"2026-08-02", "2026-08-03"}

URLS = [
    (
        "Bregott Maxi",
        "https://handlaprivatkund.ica.se/stores/1003396/products/sm%C3%B6r-raps-mellan-57-500g-bregott/2129120",
    ),
    (
        "Bregott Björksätra",
        "https://handlaprivatkund.ica.se/stores/1004503/products/sm%C3%B6r-raps-mellan-57-500g-bregott/2129120",
    ),
    (
        "Falukorv Maxi",
        "https://handlaprivatkund.ica.se/stores/1003396/products/falukorv-klassikern-800g-scan/1024181",
    ),
    (
        "Köttbullar Björksätra",
        "https://handlaprivatkund.ica.se/stores/1004503/products/k%C3%B6ttbullar-mamma-scans-1kg-scan/2024404",
    ),
]


def svensk(dt: datetime) -> str:
    """UTC → svensk sommartid (experimentet ligger helt i augusti = CEST)."""
    return (dt + timedelta(hours=2)).strftime("%a %d/%m %H:%M")


async def probe_once(run_id: str) -> list[dict]:
    extractor = JsonLdExtractor()
    rows = []
    async with AsyncSession(
        impersonate="chrome",
        timeout=20,
        allow_redirects=True,
        headers={"Accept-Language": "sv-SE,sv;q=0.9,en-US;q=0.8,en;q=0.7"},
    ) as session:
        for i, (label, url) in enumerate(URLS):
            if i:
                await asyncio.sleep(random.uniform(25, 45))
            row = {
                "run": run_id,
                "at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "label": label,
            }
            try:
                response = await session.get(url)
                row["status"] = response.status_code
                if response.status_code == 200:
                    result = extractor.extract_from_html(response.text)
                    if result is not None:
                        row["price"] = float(result.price_sek) if result.price_sek else None
                        row["offer"] = (
                            float(result.offer_price_sek) if result.offer_price_sek else None
                        )
                        row["offer_details"] = result.offer_details
                    else:
                        row["error"] = "no JSON-LD product"
            except Exception as e:
                row["error"] = str(e)[:120]
            rows.append(row)
    return rows


def effective(row: dict) -> str:
    if "price" in row and row.get("price") is not None:
        eff = row["offer"] if row.get("offer") is not None else row["price"]
        return f"{eff:.2f}"
    return f"ERR {row.get('status', '?')}: {row.get('error', 'blocked?')}"


def write_report() -> None:
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()]
    runs: dict[str, dict[str, dict]] = {}
    for row in rows:
        runs.setdefault(row["run"], {})[row["label"]] = row

    labels = [label for label, _ in URLS]
    lines = [
        "# ICA-nattprobe — resultat",
        "",
        "**Frågan:** byter ICA priserna vid midnatt (automatisk batch) eller på",
        "morgonen (manuellt)? Om midnatt kan price-trackerns ICA-fönster flyttas till",
        "efter midnatt så måndagens erbjudanden finns i appen till frukost.",
        "",
        "Varje rad är en probekörning (svensk tid). Cellerna är EFFEKTIVT pris",
        "(kampanjpris om det finns, annars ordinarie). Priset byts i intervallet",
        "mellan raden där värdet ändras och raden före.",
        "",
        "| Körning (svensk tid) | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    ordered = sorted(runs.items())
    for run_id, by_label in ordered:
        first = next(iter(by_label.values()))
        when = svensk(datetime.fromisoformat(first["at_utc"]))
        cells = [effective(by_label[label]) if label in by_label else "—" for label in labels]
        lines.append(f"| {when} ({run_id}) | " + " | ".join(cells) + " |")

    # Auto-utlåtande: första körning vars priser skiljer sig från baslinjen.
    if len(ordered) >= 2:
        baseline = {
            label: effective(row) for label, row in ordered[0][1].items() if "price" in row
        }
        verdict = None
        for run_id, by_label in ordered[1:]:
            changed = [
                f"{label}: {baseline[label]} → {effective(row)}"
                for label, row in by_label.items()
                if label in baseline and "price" in row and effective(row) != baseline[label]
            ]
            if changed:
                when = svensk(datetime.fromisoformat(next(iter(by_label.values()))["at_utc"]))
                verdict = f"**PRISBYTE UPPTÄCKT** senast {when}: " + "; ".join(changed)
                break
        lines += ["", verdict or "*Inget prisbyte upptäckt ännu (jämfört med baslinjen).*"]
    lines += [
        "",
        "Rådata: `results.jsonl`. Upplägg och städning: `README.md`.",
        "",
    ]
    REPORT.write_text("\n".join(lines))


async def main() -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if today not in ALLOWED_UTC_DATES and "--force" not in sys.argv:
        print(f"{today} är inte probenatten (2026-08-02/03) — avstår. Kör --force för test.")
        return
    run_id = datetime.now(UTC).strftime("%m-%d %H:%M UTC")
    rows = await probe_once(run_id)
    with RESULTS.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_report()
    print(f"Körning {run_id}: {len(rows)} rader loggade.")


if __name__ == "__main__":
    asyncio.run(main())
