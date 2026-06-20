"""Diagnostico: entra al primer partido valido y vuelca lo que ve la
pestana de mercados, para revisar por que _extract_market_lines no
encuentra cuotas (0 cuotas extraidas)."""
from playwright.sync_api import sync_playwright

from app.config import BETPLAY_HEADLESS, HOURS_AHEAD, VALID_COMPETITIONS_KEYWORDS, EXCLUDED_KEYWORDS
from app.scraper_betplay import BETPLAY_URL, _BULK_EXTRACT_JS, _click_text

with sync_playwright() as p:
    browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=0)
    page = browser.new_page()
    page.goto(BETPLAY_URL, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(2000)

    _click_text(page, "Football|F[uú]tbol", exact=True)
    page.wait_for_timeout(800)
    _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
    page.wait_for_timeout(1000)

    data = page.evaluate(_BULK_EXTRACT_JS)
    headers = sorted(data["headers"], key=lambda h: h["y"])
    teams = data["teams"]

    target = None
    for i in range(0, len(teams) - 1, 2):
        home, away = teams[i]["text"], teams[i + 1]["text"]
        if not home or not away:
            continue
        y = teams[i]["y"]
        competition = ""
        for h in headers:
            if h["y"] <= y + 5:
                competition = h["text"]
            else:
                break
        comp = competition.lower()
        if any(bad in comp for bad in EXCLUDED_KEYWORDS):
            continue
        if any(good in comp for good in VALID_COMPETITIONS_KEYWORDS):
            target = (home, away, competition, teams[i]["vx"], teams[i]["vy"])
            break

    if target is None:
        print("No se encontro ningun partido valido en este momento.")
        browser.close()
        raise SystemExit(0)

    home, away, competition, vx, vy = target
    print(f"Entrando a: {home} vs {away} | liga: {competition}")
    page.mouse.click(vx, vy)
    page.wait_for_timeout(4000)

    print(f"\n--- Frames de la pagina: {len(page.frames)} ---")
    for fr in page.frames:
        print(f"  url={fr.url!r}")

    print("\n--- Texto completo, scrolleando paso a paso (window.scrollBy) ---")
    last_text = ""
    for step in range(40):
        text = page.locator("body").inner_text(timeout=2000)
        if text != last_text:
            print(f"\n=== scroll step {step} (scrollY={page.evaluate('window.scrollY')}) ===")
            print(text)
            last_text = text
        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(400)

    input("\n[ENTER para cerrar el navegador]")
    browser.close()
