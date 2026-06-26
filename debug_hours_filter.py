"""Diagnostico: lista los botones de filtro de horas reales en la pestana
'Empieza pronto' de BetPlay, y cuantos partidos de 'Mundial' aparecen en
total (sin filtrar por validez), para ver si el filtro de horas o el
scroll se estan quedando cortos."""
from playwright.sync_api import sync_playwright

from app.config import BETPLAY_HEADLESS, HOURS_AHEAD
from app.scraper_betplay import BETPLAY_URL, _BULK_EXTRACT_JS, _click_text

with sync_playwright() as p:
    browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=0)
    page = browser.new_page()
    page.goto(BETPLAY_URL, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(2000)

    clicked_football = _click_text(page, "Football|F[uú]tbol", exact=True)
    page.wait_for_timeout(800)
    print(f"¿Se pudo clickear 'Football/Futbol'? {clicked_football}")

    print("\n--- Botones/tabs visibles de filtro de horas ---")
    candidates = page.locator("text=/^\\d+\\s*horas$/i")
    print(f"count = {candidates.count()}")
    for i in range(candidates.count()):
        try:
            print(f"  {candidates.nth(i).inner_text(timeout=1000)!r}")
        except Exception:
            pass

    clicked_hours = _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
    print(f"\n¿Se pudo clickear '{HOURS_AHEAD} horas'? {clicked_hours}")
    page.wait_for_timeout(1000)

    data = page.evaluate(_BULK_EXTRACT_JS)
    headers = sorted(data["headers"], key=lambda h: h["y"])
    teams = data["teams"]

    print(f"\n--- Headers de competicion detectados (primeros, tras 1 captura sin scroll): {len(headers)} ---")
    for h in headers[:30]:
        print(f"  {h['text']!r}")

    mundial_pairs = 0
    total_pairs = 0
    seen = set()

    def harvest():
        global mundial_pairs, total_pairs
        data = page.evaluate(_BULK_EXTRACT_JS)
        for h in data["headers"]:
            entry = (h["y"], h["text"])
            if entry not in [(hy, ht) for hy, ht in headers]:
                headers.append((h["y"], h["text"]))
        teams = data["teams"]
        for i in range(0, len(teams) - 1, 2):
            home, away = teams[i]["text"], teams[i + 1]["text"]
            if not home or not away or (home, away) in seen:
                continue
            seen.add((home, away))
            total_pairs += 1
            y = teams[i]["y"]
            competition = ""
            for hy, ht in sorted(headers, key=lambda e: e[0]):
                if hy <= y + 5:
                    competition = ht
                else:
                    break
            if "mundial" in competition.lower():
                mundial_pairs += 1
                print(f"  MUNDIAL: {home} vs {away}")

    headers = [(h["y"], h["text"]) for h in data["headers"]]
    harvest()
    for _ in range(80):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(300)
        harvest()

    print(f"\nTotal de partidos unicos vistos: {total_pairs}")
    print(f"Total de partidos de Mundial vistos: {mundial_pairs}")

    input("\n[ENTER para cerrar el navegador]")
    browser.close()
