"""Diagnostico: abre BetPlay, hace los clicks y muestra que extrae el JS."""
from playwright.sync_api import sync_playwright

from app.config import BETPLAY_HEADLESS, HOURS_AHEAD
from app.scraper_betplay import BETPLAY_URL, _BULK_EXTRACT_JS, _click_text, _scroll_to_bottom

with sync_playwright() as p:
    browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=0)
    page = browser.new_page()
    page.goto(BETPLAY_URL, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(2000)

    ok_fb = _click_text(page, "Football|F[uú]tbol", exact=True)
    page.wait_for_timeout(800)
    ok_h = _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
    page.wait_for_timeout(1000)
    _scroll_to_bottom(page)

    print(f"\nClick Football: {ok_fb}   Click '{HOURS_AHEAD} horas': {ok_h}")

    # Cuantos elementos hay con la clase de equipo
    team_loc = page.locator(".KambiBC-event-participants__name-participant-name")
    print(f"Elementos con clase de equipo (.KambiBC-...): {team_loc.count()}")

    data = page.evaluate(_BULK_EXTRACT_JS)
    print(f"\n--- HEADERS detectados (Futbol /...): {len(data['headers'])} ---")
    for h in sorted(data["headers"], key=lambda x: x["y"]):
        print(f"  y={h['y']:.0f}  {h['text']!r}")
    print(f"\n--- TEAMS detectados: {len(data['teams'])} ---")
    for t in data["teams"]:
        print(f"  y={t['y']:.0f}  {t['text']!r}")

    input("\n[ENTER para cerrar el navegador]")
    browser.close()
