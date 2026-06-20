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
    NATIONAL_TEAM_COMPETITION_KEYWORDS,
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

    @property
    def is_national_team_match(self) -> bool:
        comp = self.competition.lower()
        return any(kw in comp for kw in NATIONAL_TEAM_COMPETITION_KEYWORDS)


_MATCH_LOADED_RE = re.compile(
    r"Resultado Final|^Total de (Tiros de Esquina|goles|tarjetas)", re.I | re.M
)


def _wait_for_match_page(page, timeout_ms: int = 20_000, poll_ms: int = 700) -> bool:
    """El widget de cuotas (Kambi) tarda en cargar/hidratar tras entrar a un
    partido; en pruebas reales 4-8 segundos no fueron suficientes. Se
    sondea el texto visible repetidamente, scrolleando un poco en cada
    intento, hasta que aparezca contenido de partido cargado o se agote
    el tiempo."""
    elapsed = 0
    body = page.locator("body")
    while elapsed < timeout_ms:
        try:
            text = body.inner_text(timeout=2000)
        except Exception:
            text = ""
        if _MATCH_LOADED_RE.search(text):
            return True
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
    return False


def _wait_for_listing(page, timeout_ms: int = 30_000) -> bool:
    """Espera a que el listado de partidos (SPA, renderiza via JS) tenga
    contenido real antes de seguir. 'load'/'networkidle' no garantizan que
    el listado ya se haya pintado; en pruebas reales, seguir sin esto
    causaba que el filtro de Football/horas nunca se clickeara (el ciclo
    terminaba en segundos con 0 partidos)."""
    try:
        page.locator(".KambiBC-event-participants__name-participant-name").first.wait_for(
            state="visible", timeout=timeout_ms
        )
        return True
    except Exception:
        return False


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
    // Antes se emparejaba cada fila de equipos con el encabezado de
    // competencia mas cercano por coordenada Y en pixeles. En pruebas
    // reales esto fallaba: BetPlay no agrupa la lista en bloques limpios
    // por competencia (parece ordenar por hora de inicio), y la
    // virtualizacion recicla filas, asi que la Y de un encabezado
    // capturado en un instante podia no corresponder al bloque real de
    // un partido capturado en otro instante. El orden del DOM (document
    // order) si es confiable: recorremos el documento de arriba a abajo
    // y vamos asignando a cada fila de equipos el ULTIMO encabezado de
    // competencia visto hasta ese punto, en ese mismo recorrido.
    const teams = [];
    let currentHeader = '';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node = walker.currentNode;
    while (node) {
        if (node.classList && node.classList.contains('KambiBC-event-participants__name-participant-name')) {
            const rect = node.getBoundingClientRect();
            teams.push({
                y: rect.top + window.scrollY,
                vx: rect.left + rect.width / 2,
                vy: rect.top + rect.height / 2,
                text: (node.innerText || '').trim(),
                competition: currentHeader,
            });
        } else {
            const text = (node.innerText || '').trim();
            if (text && text.length <= 80 && text.indexOf('\\n') === -1 && /^(⚽\\s*)?F[uú]tbol\\s*\\//i.test(text)) {
                currentHeader = text;
            }
        }
        node = walker.nextNode();
    }
    return {teams};
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
    class _BrowserDied(Exception):
        pass

    matches: list[Match] = []
    seen_pairs: set[tuple[str, str]] = set()

    def return_to_listing(page) -> None:
        # page.go_back() resulto poco confiable en esta SPA: en pruebas
        # reales, tras extraer las cuotas de un partido el navegador
        # terminaba saliendo de BetPlay por completo y no volvia a cargar
        # el listado. En vez de depender del historial, se navega de
        # nuevo directo a la URL del listado y se reaplican los filtros.
        page.goto(BETPLAY_URL, wait_until="load", timeout=60_000)
        if not _wait_for_listing(page):
            log.warning("El listado no termino de cargar tras volver a BetPlay")
        _click_text(page, "Football|F[uú]tbol", exact=True)
        page.wait_for_timeout(800)
        _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
        page.wait_for_timeout(1000)

    def harvest_and_process(page) -> None:
        data = page.evaluate(_BULK_EXTRACT_JS)
        teams = data["teams"]
        for i in range(0, len(teams) - 1, 2):
            home, away = teams[i]["text"], teams[i + 1]["text"]
            if not home or not away or (home, away) in seen_pairs:
                continue
            seen_pairs.add((home, away))

            competition = teams[i]["competition"]

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
                # Antes se usaba page.mouse.click(vx, vy) por coordenadas:
                # en pruebas reales fallaba en silencio (no lanzaba error,
                # pero tampoco entraba al partido) cuando algo tapaba esa
                # posicion en pantalla (ej. la barra de filtros pegajosa).
                # locator.click() si verifica que el elemento sea visible y
                # no este tapado antes de clickear, y lanza error si no.
                page.locator(
                    ".KambiBC-event-participants__name-participant-name"
                ).nth(i).click(timeout=5000)
            except Exception:
                log.warning("  No se pudo entrar al partido %s vs %s", home, away)
                continue
            try:
                if not _wait_for_match_page(page):
                    log.warning("  La pagina del partido %s vs %s no termino de cargar", home, away)
                match.lines = _extract_market_lines(page)
                log.info("  -> %d cuotas extraidas", len(match.lines))
                for line in match.lines:
                    log.info("     · %s | %s @ %.2f", line.market, line.selection, line.odds)
                matches.append(match)
                return_to_listing(page)
                # El DOM del listado se reseteo por completo (volvimos al
                # tope); seguir iterando "teams" aqui usaria coordenadas
                # obsoletas. Se corta este harvest y el loop de scroll de
                # afuera vuelve a llamar a harvest_and_process con datos
                # frescos.
                return
            except Exception:
                # El navegador puede crashear/cerrarse tras varias
                # navegaciones seguidas; se descarta este partido y se
                # detiene la cosecha en curso, pero se conservan los
                # partidos ya encontrados en vez de tumbar todo el ciclo.
                log.warning(
                    "  Error inesperado procesando %s vs %s, se detiene la busqueda", home, away
                )
                raise _BrowserDied()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=150 if not BETPLAY_HEADLESS else 0)
        page = browser.new_page()
        page.goto(BETPLAY_URL, wait_until="load", timeout=60_000)
        if not _wait_for_listing(page):
            log.warning("El listado no termino de cargar al iniciar")

        _click_text(page, "Football|F[uú]tbol", exact=True)
        page.wait_for_timeout(800)
        _click_text(page, f"{HOURS_AHEAD} horas", exact=True)
        page.wait_for_timeout(1000)

        try:
            harvest_and_process(page)
            stable = 0
            for _ in range(120):
                seen_before = len(seen_pairs)
                # window.scrollBy en vez de mouse.wheel: este ultimo depende
                # de la posicion del cursor (por defecto (0,0), sobre la
                # barra lateral), y en pruebas reales terminaba scrolleando
                # el panel equivocado, dejando el listado pegado arriba sin
                # avanzar nunca hacia partidos mas abajo (ej. el Mundial).
                page.evaluate("window.scrollBy(0, 1200)")
                page.wait_for_timeout(350)
                harvest_and_process(page)
                # document.body.scrollHeight no sirve para detectar "no hay
                # mas contenido" porque la lista esta virtualizada (la altura
                # total ya refleja el tamaño completo desde el inicio); en
                # vez de eso se considera estable cuando ya no aparecen
                # partidos nuevos.
                if len(seen_pairs) == seen_before:
                    stable += 1
                    if stable >= 6:
                        break
                else:
                    stable = 0
        except _BrowserDied:
            pass
        except Exception:
            # Errores como "Execution context was destroyed" pueden ocurrir
            # si la pagina sigue navegando justo cuando se llama
            # page.evaluate (ej. tras un return_to_listing que no termino de
            # asentarse). No tumbamos todo el ciclo: se conservan los
            # partidos ya encontrados hasta este punto.
            log.warning("Se detuvo la cosecha por un error de navegacion inesperado", exc_info=True)

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
_DIRECTION_RE = re.compile(r"^(M[aá]s de|Menos de)$", re.I)
_NUMERIC_RE = re.compile(r"^[\d.,]+$")


def _parse_market_rows(block_text: str) -> list[tuple[str, str, str]]:
    """Cada cuota viene repartida en 3 lineas separadas (direccion, valor
    de linea, cuota), no en una sola linea como '"Mas de 3.5  2.28"'. Se
    recorre el texto buscando una linea de direccion seguida de 2 lineas
    numericas."""
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    rows: list[tuple[str, str, str]] = []
    i = 0
    while i < len(lines):
        m = _DIRECTION_RE.match(lines[i])
        if (
            m
            and i + 2 < len(lines)
            and _NUMERIC_RE.match(lines[i + 1])
            and _NUMERIC_RE.match(lines[i + 2])
        ):
            rows.append((m.group(1), lines[i + 1], lines[i + 2]))
            i += 3
        else:
            i += 1
    return rows


def _scroll_into_markets(page, max_scrolls: int = 40) -> None:
    """La pagina de detalle del partido tambien virtualiza secciones: los
    mercados 'Total de goles/tarjetas/Tiros de Esquina' no entran al DOM
    hasta que se scrollea hacia ellos, y el widget de cuotas puede tardar
    en cargar. Se scrollea hasta que aparezca al menos un encabezado de
    mercado o se agoten los intentos."""
    for _ in range(max_scrolls):
        if page.locator("text=/^Total de (Tiros de Esquina|goles|tarjetas)/i").count() > 0:
            return
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(500)


def _collect_visible_markets(page, lines: list[MarketLine]) -> None:
    """Lee los mercados de goles/tarjetas/tiros de esquina actualmente
    visibles en la pestana activa de la pagina del partido."""
    _scroll_into_markets(page)
    headings = page.locator("text=/^Total de (Tiros de Esquina|goles|tarjetas)/i")
    for i in range(headings.count()):
        heading = headings.nth(i)
        try:
            market_name = heading.inner_text(timeout=1000).strip()
        except Exception:
            continue
        # El heading no tiene un following-sibling util: en el DOM real de
        # Kambi, heading y filas viven dentro de un ancestro comun con la
        # clase 'KambiBC-bet-offer-subcategory__container' (confirmado
        # inspeccionando el DOM real con Playwright).
        container = heading.locator(
            "xpath=ancestor::*[contains(@class,'KambiBC-bet-offer-subcategory__container')][1]"
        )
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
        for direction, line_val, odds_val in _parse_market_rows(block_text):
            try:
                odds = float(odds_val.replace(",", "."))
            except ValueError:
                continue
            if odds >= MIN_ODDS:
                lines.append(
                    MarketLine(market=market_name, selection=f"{direction} {line_val}", odds=odds)
                )


def _extract_market_lines(page) -> list[MarketLine]:
    """Extrae cuotas de goles (pestana 'Partido & Total de goles'), y de
    tiros de esquina y tarjetas (pestana 'Tarjetas y Tiros de Esquina', con
    sub-pestanas 'Tiros de Esquina' y 'Tarjetas')."""
    lines: list[MarketLine] = []
    page.evaluate("window.scrollTo(0, 0)")

    # La pestana con el mercado de goles NO siempre esta activa por
    # defecto al entrar al partido (en pruebas reales, solo el primer
    # partido la tenia activa por casualidad; los demas mostraban otra
    # pestana y por eso no se encontraba "Total de goles"). Se clickea
    # explicitamente para garantizar que este visible.
    goals_tab = page.locator("text=/Partido\\s*&?\\s*Total de goles|^Total de goles$/i").first
    if goals_tab.count() > 0:
        try:
            goals_tab.click(timeout=2000)
            page.wait_for_timeout(800)
        except Exception:
            pass

    _collect_visible_markets(page, lines)

    combined_tab = page.locator("text=/Tarjetas y Tiros de Esquina/i").first
    if combined_tab.count() > 0:
        combined_tab.click()
        page.wait_for_timeout(800)

        corners_subtab = page.locator("text=/^Tiros de Esquina$/i").first
        if corners_subtab.count() > 0:
            corners_subtab.click()
            page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
        _collect_visible_markets(page, lines)

        cards_subtab = page.locator("text=/^Tarjetas$/i").first
        if cards_subtab.count() > 0:
            cards_subtab.click()
            page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, 0)")
            _collect_visible_markets(page, lines)

    return lines
