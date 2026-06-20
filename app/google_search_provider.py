"""
Estadisticas de equipos via la busqueda NORMAL de Google (no el Modo IA):
se hace una busqueda por estadistica, se lee el recuadro de respuesta y los
snippets que aparecen arriba (de varias fuentes: Sofascore, FBref, etc.), se
scrollea para cargar mas resultados, y se extrae el promedio por partido
buscando numeros decimales plausibles en el texto visible. Se citan las
fuentes (dominios) que aparecieron, para tener trazabilidad.

AVISO: automatizar la busqueda de Google va contra sus Terminos de Servicio
y, sobre todo, Google BLOQUEA agresivamente el scraping: tras varias
busquedas seguidas suele mostrar un CAPTCHA ("trafico inusual" / "no soy un
robot"). Cuando eso pasa, esta fuente deja de dar datos hasta que se resuelva
manualmente. Por eso conviene combinar con football-data.org (goles reales).

Requiere el mismo perfil persistente que el Modo IA
(GOOGLE_AI_PROFILE_DIR); correr una vez con GOOGLE_AI_HEADLESS=false para
aceptar el consentimiento de cookies de Google deja la sesion guardada.
"""
from __future__ import annotations

import logging
import re
import statistics
import urllib.parse

from playwright.sync_api import sync_playwright

from app.config import GOOGLE_AI_HEADLESS, GOOGLE_AI_PROFILE_DIR
from app.stats_provider import TeamForm

log = logging.getLogger("betbot.google_search")

# hl=es / gl=co fuerzan resultados en español y de Colombia, para que los
# encabezados ("promedio", "goles", etc.) y las cifras vengan en el formato
# esperado.
_SEARCH_URL = "https://www.google.com/search?hl=es&gl=co&q={q}"

# Rangos plausibles del PROMEDIO POR PARTIDO de UN equipo. Se usan para
# descartar numeros que claramente no son la estadistica buscada (años como
# 2026, marcadores como 2-1, porcentajes de posesion 60.5, etc.).
_RANGES = {
    "goals": (0.3, 4.0),
    "goals_against": (0.3, 4.0),
    "corners": (1.5, 9.0),
    "cards": (0.5, 6.0),
}

# Una busqueda por estadistica. Se pide explicitamente el "promedio por
# partido" para empujar a Google a mostrar un recuadro/snippet con la cifra.
_QUERIES = {
    "goals": 'promedio de goles anotados por partido de {team} ultimos 10 partidos',
    "goals_against": 'promedio de goles recibidos por partido de {team} ultimos 10 partidos',
    "corners": 'promedio de tiros de esquina por partido de {team} ultimos 10 partidos',
    "cards": 'promedio de tarjetas por partido de {team} ultimos 10 partidos',
}

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


def _collect_sources(page, limit: int = 4) -> list[str]:
    """Junta los dominios de las fuentes citadas en los resultados (los
    elementos <cite> que Google pone bajo cada resultado), para tener
    trazabilidad de donde salio la cifra."""
    domains: list[str] = []
    try:
        cites = page.locator("cite")
        n = min(cites.count(), 20)
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
    """Extrae el promedio del texto visible: toma todos los numeros
    decimales dentro del rango plausible y devuelve la MEDIANA (mas robusta
    que el promedio frente a un dato atipico que se haya colado)."""
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
    return round(statistics.median(cands), 2)


def _search(page, query: str) -> tuple[str, list[str], bool]:
    """Hace una busqueda en Google, scrollea para cargar mas snippets y
    devuelve (texto_visible, fuentes, bloqueado)."""
    url = _SEARCH_URL.format(q=urllib.parse.quote(query))
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    _maybe_consent(page)
    # Scroll para que carguen el recuadro de respuesta y mas resultados de
    # distintas fuentes (no solo el primero).
    for _ in range(3):
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
    overrides: dict[str, float] = {}
    sources: list[str] = []
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            GOOGLE_AI_PROFILE_DIR, headless=GOOGLE_AI_HEADLESS
        )
        try:
            page = context.new_page()
            for attr, query_tpl in _QUERIES.items():
                query = query_tpl.format(team=team_name)
                try:
                    text, domains, blocked = _search(page, query)
                except Exception:
                    log.warning("    Fallo la busqueda de %s para %s", attr, team_name)
                    continue
                if blocked:
                    log.warning(
                        "    Google bloqueo la busqueda (CAPTCHA/trafico inusual) para %s; "
                        "se detiene la consulta a Google", team_name
                    )
                    break
                lo, hi = _RANGES[attr]
                val = _extract_avg(text, lo, hi)
                if val is not None:
                    overrides[attr] = val
                    log.info("    %s %s=%.2f (Google)", team_name, attr, val)
                else:
                    log.info("    %s %s: sin cifra clara en Google", team_name, attr)
                for d in domains:
                    if d not in sources:
                        sources.append(d)
        finally:
            context.close()

    # Sin el promedio de goles no hay nada util (es el ancla del modelo).
    if "goals" not in overrides:
        log.warning("    %s: Google no dio promedio de goles", team_name)
        return None

    context_str = ("Fuentes: " + ", ".join(sources[:4])) if sources else None
    return TeamForm(
        team_name=team_name,
        venue=venue,
        samples=[],
        context=context_str,
        overrides=overrides,
    )
