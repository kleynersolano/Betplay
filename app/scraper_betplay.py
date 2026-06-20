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


def _is_within_window(kickoff: dt.datetime, hours_ahead: int = HOURS_AHEAD) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    return now <= kickoff <= now + dt.timedelta(hours=hours_ahead)


def fetch_upcoming_matches() -> list[Match]:
    """Abre BetPlay 'starting-soon' y devuelve partidos de futbol dentro de la
    ventana de horas configurada, ya filtrados por competicion valida."""
    matches: list[Match] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=150 if not BETPLAY_HEADLESS else 0)
        page = browser.new_page()
        page.goto(BETPLAY_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)
        _scroll_to_bottom(page)

        event_cards = page.locator("[data-testid='event-card'], .event-card, .sportsbook-event")
        count = event_cards.count()

        for i in range(count):
            card = event_cards.nth(i)
            try:
                competition = card.locator(".competition-name, .event-league").first.inner_text(timeout=2000)
                teams_text = card.locator(".event-teams, .team-names").first.inner_text(timeout=2000)
                time_text = card.locator(".event-time, .start-time").first.inner_text(timeout=2000)
            except Exception:
                continue

            if "vs" not in teams_text.lower() and " - " not in teams_text:
                continue

            sep = " vs " if "vs" in teams_text.lower() else " - "
            home, _, away = teams_text.partition(sep)

            kickoff = _parse_kickoff(time_text)
            if kickoff is None or not _is_within_window(kickoff):
                continue

            match = Match(
                competition=competition.strip(),
                home_team=home.strip(),
                away_team=away.strip(),
                kickoff=kickoff,
            )

            if not match.is_valid_competition:
                continue

            card.click()
            page.wait_for_timeout(1500)
            match.lines = _extract_market_lines(page)
            matches.append(match)
            page.go_back()
            page.wait_for_timeout(1000)

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


def _parse_kickoff(time_text: str) -> dt.datetime | None:
    now = dt.datetime.now(dt.timezone.utc)
    time_text = time_text.strip().lower()
    for fmt in ("%H:%M", "hoy %H:%M", "%d %b %H:%M"):
        try:
            parsed = dt.datetime.strptime(time_text, fmt)
            return now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
        except ValueError:
            continue
    return None


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
