"""
Scraper de BetPlay (betplay.com.co) usando Playwright.

NOTA IMPORTANTE: BetPlay no tiene API publica documentada. Este scraper
depende de la estructura HTML actual del sitio (clases/selectores), que
puede cambiar sin aviso. Si BetPlay cambia su frontend, los selectores
de este archivo deberan actualizarse. Revisa los data-testid / clases
reales con el inspector del navegador si algo deja de funcionar.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field

from playwright.sync_api import sync_playwright

from app.config import (
    BETPLAY_HEADLESS,
    EXCLUDED_KEYWORDS,
    HOURS_AHEAD,
    MIN_ODDS,
    VALID_COMPETITIONS_KEYWORDS,
)

log = logging.getLogger("betbot.scraper")

BETPLAY_URL = "https://betplay.com.co/apuestas#starting-soon"


@dataclass
class MarketLine:
    market: str
    selection: str
    odds: float


@dataclass
class Match:
    competition: str
    home_team: str
    away_team: str
    kickoff: dt.datetime
    lines: list[MarketLine] = field(default_factory=list)

    @property
    def is_valid_competition(self) -> bool:
        comp = self.competition.lower()
        if any(bad in comp for bad in EXCLUDED_KEYWORDS):
            return False
        return any(good in comp for good in VALID_COMPETITIONS_KEYWORDS)


def _click_text(page, pattern: str, exact: bool = False) -> bool:
    locator = page.locator(f"text=/^({pattern})$/i") if exact else page.locator(f"text=/{pattern}/i")
    if locator.count() == 0:
        return False
    try:
        locator.first.click(timeout=2000)
        return True
    except Exception:
        return False


_BULK_EXTRACT_JS = """
() => {
    const headers = [];
    const teams = [];
    const seen = new Set();
    document.querySelectorAll('*').forEach(el => {
        const text = (el.innerText || '').trim();
        if (!text || text.length > 80 || text.indexOf('\\n') !== -1) return;
        if (/^(⚽\\s*)?F[uú]tbol\\s*\\//i.test(text)) {
            const rect = el.getBoundingClientRect();
            const y = rect.top + window.scrollY;
            const key = text + '|' + Math.round(y);
            if (seen.has(key)) return;
            seen.add(key);
            headers.push({y, text});
        }
    });
    document.querySelectorAll('.KambiBC-event-participants__name-participant-name').forEach(el => {
        const rect = el.getBoundingClientRect();
        teams.push({
            y: rect.top + window.scrollY,
            vx: rect.left + rect.width / 2,
            vy: rect.top + rect.height / 2,
            text: (el.innerText || '').trim(),
        });
    });
    return {headers, teams};
}
"""


def fetch_upcoming_matches() -> list[Match]:
    """Abre BetPlay 'starting-soon', selecciona Football + ventana de horas y
    devuelve los partidos listados (ya filtrados por competicion valida).
    Como la pestana de horas (ej. '4 horas') ya filtra el listado del lado del
    sitio, no se vuelve a filtrar por hora aqui.

    BetPlay (Kambi) VIRTUALIZA la lista: solo mantiene en el DOM las filas
    visibles, y reutiliza esos mismos nodos para mostrar otro partido al
    scrollear. Por eso NO se puede juntar todo el listado primero y procesar
    despues (las posiciones quedarian mezcladas entre partidos distintos).
    En vez de eso, en cada paso de scroll se extrae lo que esta visible AHORA
    y se entra de inmediato a los partidos validos nuevos, mientras la fila
    todavia esta en el DOM. El clic se hace por coordenadas de pantalla
    (mouse.click) en vez de buscar por texto, porque nombres de equipo como
    "Países Bajos" se repiten muchas veces (esports) y un buscador por texto
    podria clicar la fila equivocada."""
    matches: list[Match] = []
    seen_pairs: set[tuple[str, str]] = set()
    header_positions: list[tuple[float, str]] = []

    def harvest_and_process(page) -> None:
        data = page.evaluate(_BULK_EXTRACT_JS)
        for h in data["headers"]:
            entry = (h["y"], h["text"])
            if entry not in header_positions:
                header_positions.append(entry)
        header_positions.sort(key=lambda e: e[0])

        teams = data["teams"]
        for i in range(0, len(teams) - 1, 2):
            home, away = teams[i]["text"], teams[i + 1]["text"]
            if not home or not away or (home, away) in seen_pairs:
                continue
            seen_pairs.add((home, away))

            y = teams[i]["y"]
            competition = ""
            for header_y, text in header_positions:
                if header_y <= y + 5:
                    competition = text
                else:
                    break

            match = Match(
                competition=competition,
                home_team=home,
                away_team=away,
                kickoff=dt.datetime.now(dt.timezone.utc),
            )
            if not match.is_valid_competition:
                log.info(
                    "  DESCARTADO  %-26s vs %-26s | liga: %s",
                    home, away, competition or "(sin liga detectada)",
                )
                continue
            log.info("  ANALIZANDO  %-26s vs %-26s | liga: %s", home, away, competition)

            try:
                page.mouse.click(teams[i]["vx"], teams[i]["vy"])
            except Exception:
                log.warning("  No se pudo entrar al partido %s vs %s", home, away)
                continue
            page.wait_for_timeout(1500)
            match.lines = _extract_market_lines(page)
            log.info("  -> %d cuotas extraidas", len(match.lines))
            matches.append(match)
            page.go_back(timeout=10_000)
            page.wait_for_timeout(1200)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=150 if not BETPLAY_HEADLESS else 0)
        page = browser.new_page()
        page.goto(BETPLAY_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)

        _click_text(page, "Football|F[uú]tbol", exact=True)
        page.wait_for_timeout(800)
        _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
        page.wait_for_timeout(1000)

        harvest_and_process(page)
        previous_height = -1
        stable = 0
        for _ in range(60):
            current_height = page.evaluate("document.body.scrollHeight")
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(350)
            harvest_and_process(page)
            if current_height == previous_height:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            previous_height = current_height

        browser.close()
    return matches


def _scroll_to_bottom(page, max_scrolls: int = 25) -> None:
    """Hace scroll hasta que la altura de la pagina deja de crecer (carga
    perezosa de mas partidos) o se alcanza el limite de intentos."""
    previous_height = -1
    for _ in range(max_scrolls):
        current_height = page.evaluate("document.body.scrollHeight")
        if current_height == previous_height:
            break
        previous_height = current_height
        page.mouse.wheel(0, current_height)
        page.wait_for_timeout(700)


_MARKET_HEADING_RE = re.compile(r"^Total de (Tiros de Esquina|goles|tarjetas)", re.I)
_ROW_RE = re.compile(r"^(M[aá]s de|Menos de)\s*([\d.,]+)\s+([\d.,]+)$", re.I)


def _collect_visible_markets(page, lines: list[MarketLine]) -> None:
    """Lee los mercados de goles/tarjetas/tiros de esquina actualmente
    visibles en la pestana activa de la pagina del partido."""
    headings = page.locator("text=/^Total de (Tiros de Esquina|goles|tarjetas)/i")
    for i in range(headings.count()):
        heading = headings.nth(i)
        try:
            market_name = heading.inner_text(timeout=1000).strip()
        except Exception:
            continue
        container = heading.locator("xpath=following-sibling::*[1]")
        if container.count() == 0:
            continue
        show_list = container.first.locator("text=/Mostrar la lista|Ver m[aá]s/i").first
        if show_list.count() > 0:
            try:
                show_list.click(timeout=1000)
                page.wait_for_timeout(400)
            except Exception:
                pass
        try:
            block_text = container.first.inner_text(timeout=1000)
        except Exception:
            continue
        for row in block_text.splitlines():
            m = _ROW_RE.match(row.strip())
            if not m:
                continue
            direction, line_val, odds_val = m.groups()
            try:
                odds = float(odds_val.replace(",", "."))
            except ValueError:
                continue
            if odds >= MIN_ODDS:
                lines.append(
                    MarketLine(market=market_name, selection=f"{direction} {line_val}", odds=odds)
                )


def _extract_market_lines(page) -> list[MarketLine]:
    """Extrae cuotas de goles (pestana 'Todos', visible por defecto), y de
    tiros de esquina y tarjetas (pestana 'Tarjetas y Tiros de Esquina', con
    sub-pestanas 'Tiros de Esquina' y 'Tarjetas')."""
    lines: list[MarketLine] = []
    _collect_visible_markets(page, lines)

    combined_tab = page.locator("text=/Tarjetas y Tiros de Esquina/i").first
    if combined_tab.count() > 0:
        combined_tab.click()
        page.wait_for_timeout(800)
        _collect_visible_markets(page, lines)

        cards_subtab = page.locator("text=/^Tarjetas$/i").first
        if cards_subtab.count() > 0:
            cards_subtab.click()
            page.wait_for_timeout(800)
            _collect_visible_markets(page, lines)

    return lines
