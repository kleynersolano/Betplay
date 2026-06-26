"""Diagnostico: entra al primer partido valido, detecta el contenedor con
scroll propio donde viven los mercados (distinto del scroll de la
ventana), lo scrollea directamente, y vuelca el texto y los botones
clicables que encuentra ahi para terminar de mapear la pagina real."""
from playwright.sync_api import sync_playwright

from app.config import BETPLAY_HEADLESS, HOURS_AHEAD, VALID_COMPETITIONS_KEYWORDS, EXCLUDED_KEYWORDS
from app.scraper_betplay import BETPLAY_URL, _BULK_EXTRACT_JS, _click_text

_FIND_SCROLLABLE_JS = """
() => {
    const out = [];
    document.querySelectorAll('*').forEach((el, i) => {
        const style = window.getComputedStyle(el);
        const canScroll = style.overflowY === 'auto' || style.overflowY === 'scroll';
        if (canScroll && el.scrollHeight > el.clientHeight + 30 && el.clientHeight > 150) {
            el.setAttribute('data-debug-idx', String(i));
            out.push({
                idx: i,
                tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 100),
                id: el.id,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
            });
        }
    });
    return out;
}
"""

_LIST_BUTTONS_JS = """
(idx) => {
    const root = document.querySelector(`[data-debug-idx="${idx}"]`) || document.body;
    const out = [];
    root.querySelectorAll('button, a, [role="button"], [role="tab"]').forEach(el => {
        const text = (el.innerText || '').trim();
        if (text && text.length < 60) out.push(text);
    });
    return out;
}
"""

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

    scrollables = page.evaluate(_FIND_SCROLLABLE_JS)
    print(f"\n--- Contenedores con scroll propio detectados: {len(scrollables)} ---")
    for s in scrollables:
        print(f"  idx={s['idx']} tag={s['tag']} cls={s['cls']!r} id={s['id']!r} "
              f"scrollHeight={s['scrollHeight']} clientHeight={s['clientHeight']}")

    if not scrollables:
        print("\nNo se detecto ningun contenedor con overflow scroll. Revisa manualmente.")
        input("\n[ENTER para cerrar el navegador]")
        browser.close()
        raise SystemExit(0)

    target_el = max(scrollables, key=lambda s: s["scrollHeight"])
    idx = target_el["idx"]
    print(f"\nUsando el contenedor mas grande: idx={idx} (scrollHeight={target_el['scrollHeight']})")

    print("\n--- Botones/links clicables dentro de ese contenedor (estado inicial) ---")
    for b in page.evaluate(_LIST_BUTTONS_JS, idx):
        print(f"  {b!r}")

    print("\n--- Texto del contenedor, scrolleando ese contenedor paso a paso ---")
    last_text = ""
    for step in range(30):
        text = page.evaluate(
            "(idx) => document.querySelector(`[data-debug-idx=\"${idx}\"]`).innerText", idx
        )
        if text != last_text:
            scroll_top = page.evaluate(
                "(idx) => document.querySelector(`[data-debug-idx=\"${idx}\"]`).scrollTop", idx
            )
            print(f"\n=== scroll step {step} (scrollTop={scroll_top}) ===")
            print(text)
            last_text = text
        page.evaluate(
            "(idx) => document.querySelector(`[data-debug-idx=\"${idx}\"]`).scrollBy(0, 400)", idx
        )
        page.wait_for_timeout(400)

    print("\n--- Botones/links clicables dentro de ese contenedor (tras scrollear todo) ---")
    for b in page.evaluate(_LIST_BUTTONS_JS, idx):
        print(f"  {b!r}")

    input("\n[ENTER para cerrar el navegador]")
    browser.close()
