"""
Estadisticas de equipos via la busqueda NORMAL de Google (no el Modo IA):
UNA sola busqueda por equipo, pidiendo a la vez el promedio de goles, tiros
de esquina y tarjetas (amarillas+rojas) en sus ultimos 10 partidos. Se lee
el texto visible de la pagina de resultados (recuadros + snippets de varias
fuentes: Sofascore, FBref, etc.), se scrollea para cargar mas resultados, y
se promedia (suma/cantidad) cada estadistica entre las cifras plausibles
encontradas. Se exige al menos 3 fuentes (dominios) distintas citadas para
considerar el dato confiable; si no se llega a esa cantidad, el partido no
se evalua con esa fuente.

No importa el contexto del partido (local/visitante, club/seleccion): la
pregunta siempre es por los "ultimos 10 partidos" del equipo en general.

AVISO: automatizar la busqueda de Google va contra sus Terminos de Servicio
y, sobre todo, Google BLOQUEA agresivamente el scraping: tras varias
busquedas seguidas suele mostrar un CAPTCHA ("trafico inusual" / "no soy un
robot"). Cuando eso pasa, esta fuente deja de dar datos hasta que se resuelva
manualmente.

Requiere el mismo perfil persistente que el Modo IA
(GOOGLE_AI_PROFILE_DIR); correr una vez con GOOGLE_AI_HEADLESS=false para
aceptar el consentimiento de cookies de Google deja la sesion guardada.
"""
from __future__ import annotations

import logging
import re
import statistics
from playwright.sync_api import sync_playwright

from app.config import GOOGLE_AI_HEADLESS, GOOGLE_AI_PROFILE_DIR
from app.stats_provider import TeamForm

log = logging.getLogger("betbot.google_search")

# Pagina de inicio de Google (no la URL de busqueda directa). Se abre esta y
# se ESCRIBE la pregunta en el buscador como un humano (en vez de ir directo
# a /search?q=...), porque Google detecta la navegacion directa a la URL de
# resultados como automatizacion y responde con CAPTCHA casi siempre. hl=es /
# gl=co fuerzan español y region Colombia.
_HOME_URL = "https://www.google.com/?hl=es&gl=co"

# Minimo de fuentes (dominios distintos citados) requeridas para considerar
# el dato confiable, segun lo pedido por el usuario.
_MIN_SOURCES = 3

# Rangos plausibles del PROMEDIO POR PARTIDO de UN equipo. Se usan para
# descartar numeros que claramente no son la estadistica buscada (años como
# 2026, marcadores como 2-1, porcentajes de posesion 60.5, etc.) y para
# separar, dentro del mismo texto, cuales cifras corresponden a cada
# estadistica cuando los rangos no se superponen.
_RANGES = {
    "goals": (0.3, 4.0),
    "corners": (1.5, 9.0),
    "cards": (0.5, 6.0),
}

# UNA sola busqueda combinada por equipo: se pide a la vez goles, corners y
# tarjetas de los ultimos 10 partidos, sin importar si juega de local o
# visitante ni si es seleccion o club.
_QUERY = (
    "promedio por partido de {team} en sus ultimos 10 partidos: "
    "goles anotados, tiros de esquina (corners) y tarjetas amarillas y rojas"
)

# Solo numeros DECIMALES (con , o .): los promedios casi siempre se reportan
# asi ("1.8", "4,5"), mientras que años y marcadores son enteros. Esto filtra
# muchisimo ruido sin tener que entender el texto.
_NUM_RE = re.compile(r"\d+[.,]\d+")

# Señales de que Google nos esta bloqueando con un CAPTCHA / muro de "trafico
# inusual". Se detectan para avisar claramente en el log (si no, el bot
# pareceria "sin datos" sin explicar por que).
_BLOCK_RE = re.compile(
    r"tr[aá]fico inusual|unusual traffic|no soy un robot|not a robot|"
    r"systems have detected|detectado tr[aá]fico",
    re.I,
)


def _maybe_consent(page) -> None:
    """Google muestra una pantalla de consentimiento de cookies la primera
    vez. Se intenta aceptar para llegar a los resultados; si ya se acepto
    antes (perfil persistente), no aparece y esto no hace nada."""
    for label in ("Aceptar todo", "Acepto", "Aceptar", "Accept all", "I agree"):
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue


def _collect_sources(page, limit: int = 8) -> list[str]:
    """Junta los dominios de las fuentes citadas en los resultados (los
    elementos <cite> que Google pone bajo cada resultado), para tener
    trazabilidad de donde salio la cifra y poder exigir al menos
    _MIN_SOURCES distintas."""
    domains: list[str] = []
    try:
        cites = page.locator("cite")
        n = min(cites.count(), 30)
        for i in range(n):
            try:
                raw = cites.nth(i).inner_text(timeout=500).strip()
            except Exception:
                continue
            # "https://www.sofascore.com › ... " -> "sofascore.com"
            dom = raw.split("›")[0].split("/")[0].strip()
            dom = re.sub(r"^https?://", "", dom).replace("www.", "").strip()
            if dom and "." in dom and dom not in domains:
                domains.append(dom)
            if len(domains) >= limit:
                break
    except Exception:
        pass
    return domains


def _extract_avg(text: str, lo: float, hi: float) -> float | None:
    """Extrae el promedio del texto visible: toma todas las cifras que dieron
    las distintas fuentes (numeros dentro del rango plausible) y las PROMEDIA
    (suma / cantidad), tal como se pidio. Ej: si las fuentes dicen 3, 4, 5, 3,
    4 -> (3+4+5+3+4)/5 = 3.8."""
    cands: list[float] = []
    for m in _NUM_RE.finditer(text):
        try:
            v = float(m.group(0).replace(",", "."))
        except ValueError:
            continue
        if lo <= v <= hi:
            cands.append(v)
    if not cands:
        return None
    return round(statistics.mean(cands), 2)


def _type_query(page, query: str) -> bool:
    """Escribe la pregunta en el cuadro de busqueda de Google y presiona
    Enter, imitando a un humano (tecla por tecla con pequeñas pausas), en vez
    de navegar directo a la URL de resultados. Devuelve True si logro lanzar
    la busqueda."""
    # El cuadro de Google es un <textarea name="q"> (antes era <input>); se
    # contemplan ambos por si cambia.
    box = None
    for sel in ("textarea[name='q']", "input[name='q']"):
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                box = loc.first
                break
        except Exception:
            continue
    if box is None:
        return False
    try:
        box.click(timeout=5000)
        page.wait_for_timeout(300)
        # type() emite cada tecla con delay, mas parecido a un humano que
        # fill() (que pega el texto de golpe).
        box.type(query, delay=60)
        page.wait_for_timeout(400)
        box.press("Enter")
        return True
    except Exception:
        return False


def _search(page, query: str) -> tuple[str, list[str], bool]:
    """Abre la home de Google, escribe la pregunta en el buscador como un
    humano, espera los resultados, scrollea para cargar mas snippets y
    devuelve (texto_visible, fuentes, bloqueado)."""
    page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    _maybe_consent(page)
    if not _type_query(page, query):
        log.warning("    No se encontro el cuadro de busqueda de Google")
        return "", [], False
    # Espera a que cargue la pagina de resultados tras el Enter.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    _maybe_consent(page)
    # Scroll para que carguen el recuadro de respuesta y mas resultados de
    # distintas fuentes (no solo el primero).
    for _ in range(4):
        page.evaluate("window.scrollBy(0, 800)")
        page.wait_for_timeout(500)
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        text = ""
    if _BLOCK_RE.search(text):
        return text, [], True
    return text, _collect_sources(page), False


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    query = _QUERY.format(team=team_name)
    overrides: dict[str, float] = {}
    sources: list[str] = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            GOOGLE_AI_PROFILE_DIR, headless=GOOGLE_AI_HEADLESS
        )
        try:
            page = context.new_page()
            try:
                text, sources, blocked = _search(page, query)
            except Exception:
                log.warning("    Fallo la busqueda combinada de Google para %s", team_name)
                text, sources, blocked = "", [], False
            if blocked:
                log.warning(
                    "    Google bloqueo la busqueda (CAPTCHA/trafico inusual) para %s",
                    team_name,
                )
        finally:
            context.close()

    if len(sources) < _MIN_SOURCES:
        log.warning(
            "    %s: solo %d fuente(s) en Google (se requieren %d), se descarta",
            team_name, len(sources), _MIN_SOURCES,
        )
        return None

    for attr, (lo, hi) in _RANGES.items():
        val = _extract_avg(text, lo, hi)
        if val is not None:
            overrides[attr] = val
            log.info("    %s %s=%.2f (Google, %d fuentes)", team_name, attr, val, len(sources))
        else:
            log.info("    %s %s: sin cifra clara en Google", team_name, attr)

    # Sin el promedio de goles no hay nada util (es el ancla del modelo).
    if "goals" not in overrides:
        log.warning("    %s: Google no dio promedio de goles", team_name)
        return None

    context_str = "Fuentes: " + ", ".join(sources[:4])
    return TeamForm(
        team_name=team_name,
        venue=venue,
        samples=[],
        context=context_str,
        overrides=overrides,
    )
