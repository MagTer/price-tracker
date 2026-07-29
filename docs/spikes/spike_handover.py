"""SPIKE del 3: överlever en browser-präglad aws-waf-token överlämningen till curl_cffi?

Del 2 visade att webbläsaren FUNGERAR: den möts av 202, löser utmaningen i JS, laddar om
och får den riktiga produktsidan — och präglar en aws-waf-token på vägen. (Del 2:s eget
utfall var feldömt: skriptet läste navigeringens första statuskod och avbröt, trots att
sidtiteln bevisade att sidan laddat.)

Kvar står den enda fråga som avgör om en sidovagn är värd en dag: när VI är blockerade,
tar den billiga curl_cffi-klienten sig in med en token som webbläsaren präglat? AWS binder
sina token till klientens egenskaper i någon mån — om bindningen omfattar TLS/JA3 eller
UA-detaljer underkänns den i överlämningen, och då är hela arkitekturen död.

Protokollet, i den ordning verkligheten har:
  1. Prägla en token i webbläsaren.
  2. Provocera fram en vägg för curl_cffi (bar begäran tills 202).
  3. Varva: bar begäran (ska ge 202) mot begäran MED token (ger den 200?).
     Varje par ligger sekunder isär, så väggen inte hinner lyfta mellan dem — det
     confound som förstörde sonderingen 2026-07-26.
  4. Testa både enbart aws-waf-token och hela kakburken.
"""

from __future__ import annotations

import asyncio
import time

from curl_cffi.requests import AsyncSession

URL = (
    "https://handlaprivatkund.ica.se/stores/1004503/products/"
    "toalettpapper-6-p-milj%C3%B6m%C3%A4rkt-ica/1371787"
)
IMPERSONATE = "chrome"
CHROME = "/home/magnus/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
WAF = {202, 403, 405, 429}


async def probe(cookies: dict[str, str] | None = None) -> tuple[int, int]:
    async with AsyncSession(impersonate=IMPERSONATE) as session:
        response = await session.get(URL, cookies=cookies or {}, timeout=30)
        return response.status_code, len(response.content or b"")


async def mint() -> tuple[dict[str, str], str, int]:
    """Return (cookies, page title, rendered bytes). Judged on CONTENT, not on the
    navigation status — the challenge answers 202 and the real page arrives after."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME,
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
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
            await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(15_000)
            title = await page.title()
            body = await page.content()
        except Exception as exc:  # noqa: BLE001
            title, body = f"<error: {exc}>", ""
        cookies = {c["name"]: c["value"] for c in await context.cookies()}
        await browser.close()
    return cookies, title, len(body)


async def main() -> None:
    print("[1] Präglar token i webbläsaren...")
    cookies, title, body_len = await mint()
    token = cookies.get("aws-waf-token")
    print(f"    titel      : {title[:70]!r}")
    print(f"    renderat   : {body_len} bytes")
    print(f"    token      : {'JA (' + str(len(token)) + ' tecken)' if token else 'NEJ'}")
    if not token:
        print("\nSVAR: NEJ — ingen token att lämna över.")
        return

    print("\n[2] Provocerar fram en vägg för curl_cffi (bar begäran, 3 s isär)...")
    status = 200
    for attempt in range(15):
        status, size = await probe()
        print(f"    {time.strftime('%H:%M:%S')} försök {attempt + 1:2d} -> {status} ({size} b)")
        if status in WAF:
            break
        await asyncio.sleep(3)
    if status not in WAF:
        print("\n    Fick ingen vägg på 15 försök — kan inte avgöra. Kör om.")
        return

    print("\n[3] Överlämning — bar mot tokenbärande, sekunder isär:")
    bare_ok = tokened_ok = full_ok = 0
    for round_no in range(3):
        bare = await probe()
        await asyncio.sleep(2)
        tok = await probe({"aws-waf-token": token})
        await asyncio.sleep(2)
        full = await probe(cookies)
        await asyncio.sleep(2)
        bare_ok += bare[0] == 200
        tokened_ok += tok[0] == 200
        full_ok += full[0] == 200
        print(
            f"    runda {round_no + 1}:  bar={bare[0]} ({bare[1]} b)   "
            f"token={tok[0]} ({tok[1]} b)   alla kakor={full[0]} ({full[1]} b)"
        )

    print(f"\n    bar OK {bare_ok}/3   token OK {tokened_ok}/3   alla kakor OK {full_ok}/3")
    best = max(tokened_ok, full_ok)
    if best == 3 and bare_ok == 0:
        print("    SVAR: JA — token bär över till curl_cffi. Sidovagnen är värd att bygga.")
    elif best > bare_ok:
        print("    SVAR: SVAGT JA — bättre med token, men inte rent. Kör om innan beslut.")
    elif bare_ok:
        print("    SVAR: OAVGJORT — väggen släppte även bart. Stickprovsmässig; kör om.")
    else:
        print("    SVAR: NEJ — token underkänns hos curl_cffi. Sidovagnsidén faller.")


if __name__ == "__main__":
    asyncio.run(main())
