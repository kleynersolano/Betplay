"""
Estadisticas de equipos via el Modo IA de Google Search (la pestana "Modo IA"
debajo del buscador en google.com, no el chat de gemini.google.com) usando un
navegador automatizado con Playwright, igual que el scraper de BetPlay.

AVISO: automatizar la interfaz web de consumidor de Google (en vez de su API
oficial) va contra los Terminos de Servicio de Google y puede resultar en el
bloqueo de la cuenta usada. Se eligio este enfoque a pedido explicito del
usuario, asumiendo ese riesgo. Requiere un login manual una sola vez: correr
el bot con GOOGLE_AI_HEADLESS=false, loguearse en la ventana que se abre, y
la sesion queda guardada en GOOGLE_AI_PROFILE_DIR para las siguientes corridas.

Se usa como fuente de respaldo cuando API-Football no tiene datos
suficientes (cuota agotada, equipo no encontrado, etc.).
"""
from __future__ import annotations

import json
import logging
import re

from playwright.sync_api import sync_playwright

from app.config import GOOGLE_AI_HEADLESS, GOOGLE_AI_PROFILE_DIR, GOOGLE_AI_URL, MIN_VALID_MATCHES
from app.stats_provider import TeamForm, TeamMatchStats

log = logging.getLogger("betbot.google_ai")

PROMPT_TEMPLATE = """Eres un asistente de datos deportivos. Busca en internet, consultando \
hasta 5 fuentes confiables (por ejemplo Sofascore, Flashscore, WhoScored, FootyStats, FBref) \
sin que yo necesite entrar a ninguna de esas paginas, las estadisticas de los ultimos {n} \
partidos del equipo "{team}" jugando de {venue} en su liga local. Para cada partido dame: \
goles anotados por "{team}", tarjetas (amarillas+rojas) recibidas por "{team}", y corners a \
favor de "{team}" (si no hay dato de corners, usa null). Responde UNICAMENTE con un JSON \
valido, sin texto adicional, con esta forma exacta:
{{"matches": [{{"goals": <numero>, "cards": <numero o null>, "corners": <numero o null>}}, ...]}}
"""


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _find_input(page):
    """Localiza el campo de texto del Modo IA probando, en orden: el
    placeholder visible ('Haz una pregunta' / 'Pregunta'), el rol de
    combobox/textbox, y por ultimo selectores clasicos del buscador."""
    candidates = [
        lambda: page.get_by_placeholder(re.compile("Haz una pregunta|Pregunta", re.I)),
        lambda: page.get_by_role("combobox"),
        lambda: page.get_by_role("textbox"),
        lambda: page.locator("textarea[name='q'], textarea#APjFqb"),
        lambda: page.locator("div[contenteditable='true']"),
    ]
    for build in candidates:
        try:
            loc = build().first
            if loc.count() > 0:
                loc.wait_for(state="visible", timeout=3000)
                return loc
        except Exception:
            continue
    return None


def _ask_google_ai_mode(prompt: str) -> str | None:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            GOOGLE_AI_PROFILE_DIR,
            headless=GOOGLE_AI_HEADLESS,
        )
        try:
            page = context.new_page()
            page.goto(GOOGLE_AI_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
            input_box = _find_input(page)
            if input_box is None:
                log.warning("No se encontro el campo de texto del Modo IA")
                return None
            input_box.click()
            page.wait_for_timeout(300)
            # keyboard.type escribe en el elemento enfocado y evita la revision
            # de "accionabilidad" del locator, que el textarea del Modo IA falla
            # (hacia timeout con input_box.type()).
            page.keyboard.type(prompt, delay=8)
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
            page.wait_for_timeout(20000)
            response_blocks = page.locator(
                "[data-async-context*='aimode'], .aimode-answer, #rso div[data-content-feature]"
            )
            if response_blocks.count() == 0:
                return None
            return response_blocks.last.inner_text()
        finally:
            context.close()


def get_team_form(team_name: str, venue: str, last_n: int = 10) -> TeamForm | None:
    prompt = PROMPT_TEMPLATE.format(n=last_n, team=team_name, venue=venue)
    try:
        raw = _ask_google_ai_mode(prompt)
    except Exception:
        log.exception("Fallo consultando Google AI para %s", team_name)
        return None
    if not raw:
        return None
    data = _extract_json(raw)
    if not data:
        return None
    samples = [
        TeamMatchStats(
            corners=float(m["corners"]) if m.get("corners") is not None else None,
            goals=float(m.get("goals", 0)),
            cards=float(m["cards"]) if m.get("cards") is not None else None,
        )
        for m in data.get("matches", [])[:last_n]
    ]
    if len(samples) < MIN_VALID_MATCHES:
        return None
    return TeamForm(team_name=team_name, venue=venue, samples=samples)
