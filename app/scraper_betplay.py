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
import unicodedata
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


def _normalize(text: str) -> str:
    """minusculas y sin tildes, para que 'série a' (con acento, Brasil)
    coincida igual que 'serie a' en las listas de keywords."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


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
        # Filtro por LISTA BLANCA: solo se acepta Mundial, eliminatorias,
        # Libertadores, Champions, Europa League, Sudamericana y la PRIMERA
        # division de los paises futboleros principales. Se descarta todo lo
        # demas (ligas menores, segundas divisiones, reservas, amateur,
        # femenino, eSports), aunque no este explicitamente en la lista
        # negra: con lista negra sola se filtraban ligas como "Torneo
        # Federal A" (Argentina, 3ra) o "MLS Next Pro" (EE.UU., reservas)
        # porque ningun keyword negro las atrapaba.
        comp = _normalize(self.competition)
        if not comp.strip():
            return False
        if any(bad in comp for bad in EXCLUDED_KEYWORDS):
            return False
        return any(good in comp for good in VALID_COMPETITIONS_KEYWORDS)

    @property
    def is_national_team_match(self) -> bool:
        comp = _normalize(self.competition)
        return any(kw in comp for kw in NATIONAL_TEAM_COMPETITION_KEYWORDS)


_MATCH_LOADED_RE = re.compile(
    r"Resultado Final|^Total de (Tiros de Esquina|goles|tarjetas)", re.I | re.M
)


_STALL_RELOAD_MS = 60_000  # si en 60s no carga, se asume trabada y se recarga (F5)
_MAX_RELOADS = 2  # tope de recargas antes de rendirse y dejar el partido/listado como fallido


def _wait_for_match_page(page, timeout_ms: int = 20_000, poll_ms: int = 700) -> bool:
    """El widget de cuotas (Kambi) tarda en cargar/hidratar tras entrar a un
    partido; en pruebas reales 4-8 segundos no fueron suficientes. Se
    sondea el texto visible repetidamente, scrolleando un poco en cada
    intento, hasta que aparezca contenido de partido cargado o se agote
    el tiempo. Si la pagina queda trabada (60s sin cargar nada util,
    sintoma visto en pruebas reales cuando el equipo se sobrecarga), se
    fuerza un refresh (F5) e se le da otra oportunidad antes de rendirse."""
    elapsed = 0
    reloads = 0
    stall_elapsed = 0
    body = page.locator("body")
    while elapsed < timeout_ms or reloads < _MAX_RELOADS:
        try:
            text = body.inner_text(timeout=2000)
        except Exception:
            text = ""
        if _MATCH_LOADED_RE.search(text):
            return True
        if stall_elapsed >= _STALL_RELOAD_MS and reloads < _MAX_RELOADS:
            log.warning("  Pagina del partido trabada %ds, recargando (F5)...", stall_elapsed // 1000)
            try:
                page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            reloads += 1
            stall_elapsed = 0
            elapsed = 0
            page.wait_for_timeout(1500)
            continue
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        stall_elapsed += poll_ms
    return False


def _wait_for_listing(page, timeout_ms: int = 30_000) -> bool:
    """Espera a que el listado de partidos (SPA, renderiza via JS) tenga
    contenido real antes de seguir. 'load'/'networkidle' no garantizan que
    el listado ya se haya pintado; en pruebas reales, seguir sin esto
    causaba que el filtro de Football/horas nunca se clickeara (el ciclo
    terminaba en segundos con 0 partidos). Si el listado nunca llega a
    cargar (pagina trabada por sobrecarga del equipo), se recarga (F5) y
    se reintenta antes de rendirse."""
    for attempt in range(1 + _MAX_RELOADS):
        try:
            page.locator(".KambiBC-event-participants__name-participant-name").first.wait_for(
                state="visible", timeout=_STALL_RELOAD_MS if attempt == 0 else timeout_ms
            )
            return True
        except Exception:
            if attempt < _MAX_RELOADS:
                log.warning("  Listado trabado, recargando (F5)...")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
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
    // Antes se asignaba a cada fila de equipos el ULTIMO encabezado de
    // competencia visto en un recorrido global del documento (document
    // order). En pruebas reales esto seguia fallando: el DOM tiene
    // encabezados o bloques de otras pestañas/secciones que quedan
    // ocultos (display:none) pero siguen presentes, y el recorrido
    // global los recogia fuera de orden, asignando competencias
    // equivocadas (ej. "Colombia" a partidos del Mundial). Ahora se
    // busca, PARA CADA FILA, el encabezado mas cercano subiendo por sus
    // propios ancestros y revisando hermanos anteriores dentro de esa
    // misma rama -- y se descartan candidatos ocultos (offsetParent
    // null) para no recoger contenido de pestañas no visibles.
    const HEADER_RE = /^(⚽\\s*)?F[uú]tbol\\s*\\//i;
    const isVisible = (el) => !!el.offsetParent || el === document.body;

    const headerTextOf = (el) => {
        if (!el || !isVisible(el)) return null;
        const text = (el.innerText || '').trim();
        if (text && text.length <= 80 && text.indexOf('\\n') === -1 && HEADER_RE.test(text)) {
            return text;
        }
        return null;
    };

    const findCompetition = (rowEl) => {
        let ancestor = rowEl;
        for (let depth = 0; depth < 10 && ancestor; depth++) {
            let sib = ancestor.previousElementSibling;
            for (let hop = 0; hop < 30 && sib; hop++) {
                const direct = headerTextOf(sib);
                if (direct) return direct;
                // El encabezado tambien puede estar anidado dentro del
                // hermano anterior (no ser el mismo nodo de texto).
                if (isVisible(sib)) {
                    const nested = sib.querySelector ? sib.querySelector('*') : null;
                    if (nested) {
                        const all = sib.querySelectorAll('*');
                        for (let k = all.length - 1; k >= 0; k--) {
                            const found = headerTextOf(all[k]);
                            if (found) return found;
                        }
                    }
                }
                sib = sib.previousElementSibling;
            }
            ancestor = ancestor.parentElement;
        }
        return '';
    };

    // Hora de inicio: Kambi la pinta en un nodo <time> (o con
    // data-event-start) dentro de la misma fila/ancestro cercano de cada
    // partido. Se busca cerca del nombre del equipo; si no se encuentra,
    // el llamador usa la hora actual como antes (no rompe nada si el
    // selector no aplica en este theme de Kambi).
    const findStartTime = (rowEl) => {
        let ancestor = rowEl;
        for (let depth = 0; depth < 6 && ancestor; depth++) {
            const t = ancestor.querySelector ? ancestor.querySelector('time[datetime]') : null;
            if (t) {
                const dt = t.getAttribute('datetime');
                if (dt) return dt;
            }
            ancestor = ancestor.parentElement;
        }
        return null;
    };

    const teams = [];
    document.querySelectorAll('.KambiBC-event-participants__name-participant-name').forEach(el => {
        if (!isVisible(el)) return;
        const rect = el.getBoundingClientRect();
        teams.push({
            y: rect.top + window.scrollY,
            vx: rect.left + rect.width / 2,
            vy: rect.top + rect.height / 2,
            text: (el.innerText || '').trim(),
            competition: findCompetition(el),
            startTime: findStartTime(el.closest('a, li, div') || el),
        });
    });
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

            kickoff = dt.datetime.now(dt.timezone.utc)
            raw_start = teams[i].get("startTime")
            if raw_start:
                try:
                    kickoff = dt.datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                except ValueError:
                    pass

            match = Match(
                competition=competition,
                home_team=home,
                away_team=away,
                kickoff=kickoff,
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
                #
                # El listado virtualiza filas: las que no estan en pantalla
                # se quitan del DOM. Si la fila del partido ya no esta
                # visible (porque scrolleamos hacia otras mas abajo), el
                # locator no la encuentra y el click falla en timeout. Por
                # eso primero se hace scrollIntoView por JS (no depende de
                # que Playwright considere "accionable" el elemento) y
                # recien despues se clickea.
                row = page.locator(
                    ".KambiBC-event-participants__name-participant-name"
                ).nth(i)
                row.evaluate("el => el.scrollIntoView({block: 'center'})")
                page.wait_for_timeout(300)
                row.click(timeout=8000)
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
                # Antes esto detenia TODA la busqueda (raise _BrowserDied),
                # incluso cuando la falla era propia de este partido puntual
                # (timeout extrayendo cuotas, navegacion lenta, etc.) y no del
                # navegador en si. En pruebas reales esto corto la busqueda
                # en el partido numero 6 dejando afuera partidos validos que
                # venian despues. Ahora se descarta solo este partido y se
                # intenta volver al listado para seguir con los siguientes;
                # solo si TAMBIEN falla el regreso al listado se asume que el
                # navegador esta en mal estado y se detiene todo.
                log.warning(
                    "  Error inesperado procesando %s vs %s, se descarta y se continua",
                    home, away, exc_info=True,
                )
                try:
                    return_to_listing(page)
                except Exception:
                    log.warning("  No se pudo volver al listado, se detiene la busqueda")
                    raise _BrowserDied()
                return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=BETPLAY_HEADLESS, slow_mo=150 if not BETPLAY_HEADLESS else 0)
        try:
            # Todo lo que puede fallar (goto, clicks, cosecha) queda dentro
            # de este try/finally: antes browser.close() solo se llamaba al
            # final del bloque feliz, asi que si goto() o _click_text()
            # fallaban antes de llegar ahi, el proceso de Chromium quedaba
            # vivo (zombie) y se acumulaba ciclo tras ciclo, consumiendo RAM
            # hasta volver lenta toda la maquina.
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
        finally:
            try:
                browser.close()
            except Exception:
                pass
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
        if page.locator(_MARKET_HEADING_RE).count() > 0:
            return
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(500)


# Antes solo se buscaban encabezados que empezaran exactamente con
# "Total de Tiros de Esquina/goles/tarjetas", lo que dejaba afuera los
# mercados POR EQUIPO (ej. "Tiros de Esquina - Alemania", "Goles de Costa
# de Marfil"). Se amplio quitando el "Total de" obligatorio del inicio,
# pero SIN la "^" de anclaje al inicio del texto el regex empezo a
# emparejar con CUALQUIER elemento que contuviera "gol"/"tarjeta" en
# cualquier parte de su texto -- incluyendo contenedores enteros con todo
# el bloque de cuotas adentro -- lo que en pruebas reales disparo el
# conteo de "headings" a cientos y volvio la extraccion lentisima y
# practicamente vacia. Se mantiene el anclaje "^" (solo encabezados que
# EMPIEZAN con estas palabras) pero ahora sin exigir el prefijo "Total
# de", para cubrir variantes por equipo como "Tiros de Esquina - Alemania".
_MARKET_HEADING_RE = "text=/^(Total de )?(Tiros de Esquina|Goles|Tarjetas)\\b/i"
_EXCLUDED_HEADING_RE = re.compile(
    r"hándicap|handicap|goleador|anotador|primer gol|[uú]ltimo gol"
    r"|tiempo|mitad|[12]\s*\.?\s*[ªa]\.?\s*parte",
    re.I,
)


def _collect_visible_markets(page, lines: list[MarketLine]) -> None:
    """Lee los mercados de goles/tarjetas/tiros de esquina (total y por
    equipo) en la pestana activa de la pagina del partido. La pagina
    virtualiza esta seccion: el mercado "Total de X" siempre aparece
    primero, pero los mercados POR EQUIPO estan mas abajo y no entran al
    DOM hasta scrollear ahi. Antes se paraba de scrollear en cuanto
    aparecia el primer encabezado (el de "Total de..."), perdiendo los de
    equipo. Ahora se sigue scrolleando y recolectando hasta que dejan de
    aparecer encabezados nuevos."""
    _scroll_into_markets(page)
    seen_lines: set[tuple[str, str, float]] = {(l.market, l.selection, l.odds) for l in lines}
    seen_markets: set[str] = set()
    stable = 0
    for _ in range(40):
        headings = page.locator(_MARKET_HEADING_RE)
        new_market_found = False
        for i in range(headings.count()):
            heading = headings.nth(i)
            try:
                market_name = heading.inner_text(timeout=1000).strip()
            except Exception:
                continue
            # Si el "encabezado" en realidad es un contenedor grande (varias
            # lineas), el texto va a ser largo y con saltos de linea -- no es
            # un encabezado real, se descarta para no procesar bloques enteros
            # como si fueran un solo titulo.
            if not market_name or len(market_name) > 80 or "\n" in market_name:
                continue
            if _EXCLUDED_HEADING_RE.search(market_name):
                continue
            if market_name in seen_markets:
                continue
            seen_markets.add(market_name)
            new_market_found = True
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
                # Se guardan TODAS las cuotas, incluso por debajo de MIN_ODDS:
                # el lado barato de una linea (ej. favorito) se necesita para
                # calcular el overround real del lado caro que si se podria
                # apostar. El filtro de MIN_ODDS se aplica en analysis.py al
                # momento de elegir la apuesta final, no aqui.
                key = (market_name, f"{direction} {line_val}", odds)
                if key in seen_lines:
                    continue
                seen_lines.add(key)
                lines.append(
                    MarketLine(market=market_name, selection=f"{direction} {line_val}", odds=odds)
                )

        if new_market_found:
            stable = 0
        else:
            stable += 1
            if stable >= 4:
                break
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(400)


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
