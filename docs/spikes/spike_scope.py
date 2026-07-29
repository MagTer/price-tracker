"""SPIKE del 4: hur BRED och hur LÅNGLIVAD är en token?

Del 3 visade att en browser-präglad aws-waf-token bär över till curl_cffi. Två egenskaper
avgör nu hur ofta webbläsaren måste köra, och därmed hela kostnadsbilden för en sidovagn:

  BREDD:     gäller en token hela värden, eller bara den URL den präglades på? Är den
             per-URL är arkitekturen meningslös — då kostar varje sida en webbläsarstart.
  LIVSLÄNGD: håller den i minuter eller timmar? Det avgör om webbläsaren startar en
             handfull gånger per dygn eller vid varje kontroll.

Provet: prägla EN token på produkt A, och använd den mot tre andra ICA-URL:er (två i
samma butik, en i en annan butik) medan väggen är uppe — varvat med bara begäranden som
visar att väggen faktiskt står kvar. Kolla sedan om samma token fortfarande går in efter
en paus.
"""

from __future__ import annotations

import asyncio
import time

from curl_cffi.requests import AsyncSession

MINT_URL = (
    "https://handlaprivatkund.ica.se/stores/1004503/products/"
    "toalettpapper-6-p-milj%C3%B6m%C3%A4rkt-ica/1371787"
)
OTHERS = [
    "https://handlaprivatkund.ica.se/stores/1004503/products/"
    "j%C3%A4ttefranska-rostbr%C3%B6d-1-1kg-p%C3%A5gen/2010293",
    "https://handlaprivatkund.ica.se/stores/1004503/products/"
    "bryggkaffe-mellanrost-450g-gevalia/1528809",
    # annan butik — samma värd, annat store-id
    "https://handlaprivatkund.ica.se/stores/1003396/products/"
    "bearnaises%C3%A5s-original-230ml-lohmanders/2024316",
]
IMPERSONATE = "chrome"
CHROME = "/home/magnus/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
WAF = {202, 403, 405, 429}


async def probe(url: str, cookies: dict[str, str] | None = None) -> tuple[int, int]:
    async with AsyncSession(impersonate=IMPERSONATE) as session:
        response = await session.get(url, cookies=cookies or {}, timeout=30)
        return response.status_code, len(response.content or b"")


async def mint() -> str | None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME,
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        # UA-överskrivningen är INTE kosmetisk: utan den skickar Playwright
        # "HeadlessChrome/..." och ICA svarar med en hård 403 utan utmaning — alltså
        # ingen token att prägla. Med en vanlig Chrome-UA serveras utmaningen i stället.
        context = await browser.new_context(
            locale="sv-SE",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            await page.goto(MINT_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(15_000)
            print(f"    webbläsarens titel: {(await page.title())[:60]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"    webbläsarfel: {exc}")
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        await browser.close()
    return cookies.get("aws-waf-token")


async def main() -> None:
    # Ordningen är inte godtycklig: en token präglas bara när utmaningen SERVERAS. Kör
    # webbläsaren mot en olflaggad IP och den får sidan direkt, utan token att lämna över.
    print("[1] Provocerar fram väggen FÖRST (annars finns ingen utmaning att lösa)...")
    for attempt in range(15):
        status, _ = await probe(MINT_URL)
        print(f"    försök {attempt + 1:2d} -> {status}")
        if status in WAF:
            break
        await asyncio.sleep(3)
    else:
        print("    Ingen vägg — bredden går inte att avgöra just nu.")
        return

    print("\n[2] Präglar EN token på toalettpappret...")
    token = await mint()
    print(f"    token: {'JA' if token else 'NEJ'}")
    if not token:
        return
    minted_at = time.time()

    print("\n[3] BREDD — samma token mot ANDRA URL:er, varvat med bara begäranden:")
    for url in OTHERS:
        label = url.split("/products/")[1][:38]
        bare = await probe(url)
        await asyncio.sleep(2)
        tok = await probe(url, {"aws-waf-token": token})
        await asyncio.sleep(2)
        print(f"    {label:40} bar={bare[0]} ({bare[1]} b)  token={tok[0]} ({tok[1]} b)")

    print("\n[4] LIVSLÄNGD — samma token igen efter pauser:")
    for wait_s in (60, 180, 300):
        await asyncio.sleep(wait_s)
        age = int(time.time() - minted_at)
        bare = await probe(MINT_URL)
        await asyncio.sleep(2)
        tok = await probe(MINT_URL, {"aws-waf-token": token})
        print(f"    ålder {age:4d}s   bar={bare[0]} ({bare[1]} b)   token={tok[0]} ({tok[1]} b)")


if __name__ == "__main__":
    asyncio.run(main())
