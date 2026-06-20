"""Diagnostico puntual: entra al primer partido valido, scrollea toda la
pagina, y muestra exactamente que encuentran los locators de encabezados
de mercado y los botones/links dentro de cada uno, para comparar contra
lo que usa _collect_visible_markets en app/scraper_betplay.py."""
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

    print("\nScrolleando toda la pagina (window.scrollBy) para forzar que cargue todo...")
    for _ in range(30):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)

    print("\n--- Locator anclado: text=/^Total de (Tiros de Esquina|goles|tarjetas)/i ---")
    anchored = page.locator("text=/^Total de (Tiros de Esquina|goles|tarjetas)/i")
    print(f"count = {anchored.count()}")
    for i in range(anchored.count()):
        try:
            print(f"  [{i}] {anchored.nth(i).inner_text(timeout=1000)!r}")
        except Exception as e:
            print(f"  [{i}] ERROR: {e}")

    print("\n--- Locator sin ancla: text=/Total de (Tiros de Esquina|goles|tarjetas)/i ---")
    unanchored = page.locator("text=/Total de (Tiros de Esquina|goles|tarjetas)/i")
    print(f"count = {unanchored.count()}")
    for i in range(unanchored.count()):
        try:
            print(f"  [{i}] {unanchored.nth(i).inner_text(timeout=1000)!r}")
        except Exception as e:
            print(f"  [{i}] ERROR: {e}")

    if unanchored.count() > 0:
        print("\n--- Hermano siguiente del primer encabezado (sin ancla) ---")
        heading = unanchored.first
        container = heading.locator("xpath=following-sibling::*[1]")
        print(f"following-sibling count = {container.count()}")
        if container.count() > 0:
            try:
                print(repr(container.first.inner_text(timeout=1000)))
            except Exception as e:
                print(f"ERROR: {e}")

        print("\n--- xpath ancestro -> padre -> todo el texto del padre ---")
        parent = heading.locator("xpath=..")
        try:
            print(repr(parent.inner_text(timeout=1000)))
        except Exception as e:
            print(f"ERROR: {e}")

    input("\n[ENTER para cerrar el navegador]")
    browser.close()
