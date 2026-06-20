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

# Se piden PROMEDIOS directos (no la lista partido por partido): el modelo
# de Poisson solo necesita la media, y pedir el promedio es mas rapido, mas
# corto y mas robusto que pedir 20 partidos y promediarlos nosotros (eso
# devolvia respuestas largas, lentas y a veces vacias). Se pide al modelo
# que de su MEJOR estimacion numerica (probado: exigir verificacion estricta
# en 3 fuentes hacia que devolviera todo null).
_STATS_INSTRUCTIONS = """Da tu MEJOR estimacion numerica de los PROMEDIOS por partido \
(no devuelvas null salvo que el dato realmente no exista) de "{team}": goles anotados \
(goals_for), goles recibidos (goals_against), tiros de esquina a favor (corners), y \
tarjetas amarillas+rojas recibidas (cards). Ademas, un campo "context" con un resumen \
breve (maximo 2 frases) del contexto reciente relevante para apostar (lesiones de \
titulares, racha, motivacion del partido). Y un objeto "signals" con SEÑALES MEDIBLES \
basadas en datos reales (tabla, alineacion confirmada), NO opiniones: \
"must_win" (true si "{team}" NECESITA ganar/anotar por su situacion en la tabla, si no false), \
"role" ("favorito" si suele presionar, "defensivo" si suele encerrarse, "neutral" si no hay \
rol claro), \
"key_attacker_out" (true SOLO si hay baja confirmada de un goleador titular, si no false). \
Responde UNICAMENTE con un JSON valido, sin texto adicional, con esta forma exacta:
{{"goals_for": <numero>, "goals_against": <numero>, "corners": <numero o null>, \
"cards": <numero o null>, "context": "<resumen breve o cadena vacia>", \
"signals": {{"must_win": <true|false>, "role": "<favorito|defensivo|neutral>", \
"key_attacker_out": <true|false>}}}}
"""

PROMPT_TEMPLATE_CLUB = """Eres un analista de datos deportivos. Usando como fuente \
principal Sofascore o FBref (sin que yo necesite entrar a esas paginas), calcula las \
estadisticas promedio de los ultimos {n} partidos oficiales del equipo "{team}".
""" + _STATS_INSTRUCTIONS

# Las selecciones nacionales no tienen "liga local" (juegan eliminatorias,
# mundiales, amistosos, copas continentales): se piden sus ultimos partidos
# oficiales con la seleccion, sin filtrar por venue/liga.
PROMPT_TEMPLATE_NATIONAL = """Eres un analista de datos deportivos. Usando como fuente \
principal Sofascore o FBref (sin que yo necesite entrar a esas paginas), calcula las \
estadisticas promedio de los ultimos {n} partidos oficiales (eliminatorias, mundial, \
copas continentales, amistosos) de la seleccion nacional de "{team}".
""" + _STATS_INSTRUCTIONS


def _extract_json(text: str) -> dict | None:
    """Busca el objeto JSON que contiene "signals" dentro de un texto que
    puede tener mucho mas contenido alrededor (toda la pagina visible).
    No usa una regex greedy de '{...}' porque con el texto de la pagina
    completa eso capturaria desde el primer '{' hasta el ULTIMO '}' de
    toda la pagina. En vez de eso, ubica el '{' que abre el objeto que
    contiene "signals" y cuenta llaves balanceadas hasta cerrarlo."""
    key_pos = text.find('"signals"')
    if key_pos == -1:
        return None
    start = text.rfind("{", 0, key_pos)
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
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


def _ask_google_ai_mode(prompt: str, max_wait_ms: int = 30_000) -> str | None:
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
            # El textarea del Modo IA tiene autofocus y a veces nunca queda
            # "stable" para Playwright -> input_box.click() hacia timeout de
            # 30s y tumbaba la consulta entera (paso con Tunez). Se enfoca por
            # JS (no exige accionabilidad) y, si falla, se sigue igual porque
            # el autofocus suele bastar. Luego se escribe por teclado.
            try:
                input_box.evaluate("el => el.focus()")
            except Exception:
                pass
            page.wait_for_timeout(200)
            # keyboard.type escribe en el elemento enfocado y evita la revision
            # de "accionabilidad" del locator, que el textarea del Modo IA falla
            # (hacia timeout con input_box.type()).
            page.keyboard.type(prompt, delay=8)
            page.wait_for_timeout(300)
            # Verifica que el texto realmente entro al campo; si el foco fallo,
            # el prompt se perderia y la respuesta nunca llegaria. En ese caso
            # se reintenta una vez con click forzado.
            try:
                typed = (input_box.input_value(timeout=1000) or "").strip()
            except Exception:
                typed = ""
            if not typed:
                try:
                    input_box.click(force=True, timeout=3000)
                    page.keyboard.type(prompt, delay=8)
                    page.wait_for_timeout(300)
                except Exception:
                    log.warning("No se pudo escribir el prompt en el Modo IA")
                    return None
            page.keyboard.press("Enter")

            # No se usa un selector fijo del contenedor de respuesta (la UI de
            # Google cambia seguido y nunca se confirmo contra el DOM real).
            # En vez de eso se espera a que el JSON pedido ("signals" aparezca
            # en cualquier parte del texto visible de la pagina, sondeando.
            elapsed = 0
            poll_ms = 1500
            body = page.locator("body")
            while elapsed < max_wait_ms:
                page.wait_for_timeout(poll_ms)
                elapsed += poll_ms
                try:
                    text = body.inner_text(timeout=2000)
                except Exception:
                    continue
                if '"signals"' in text:
                    return text
            log.warning("Timeout esperando respuesta del Modo IA (no aparecio el JSON)")
            return None
        finally:
            context.close()


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_team_form(
    team_name: str, venue: str, last_n: int = 20, is_national_team: bool = False
) -> TeamForm | None:
    if is_national_team:
        prompt = PROMPT_TEMPLATE_NATIONAL.format(n=last_n, team=team_name)
    else:
        prompt = PROMPT_TEMPLATE_CLUB.format(n=last_n, team=team_name, venue=venue)
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

    # Ahora la respuesta trae PROMEDIOS directos (no lista de partidos). Se
    # cargan como overrides para que TeamForm.average() los devuelva tal cual.
    overrides: dict[str, float] = {}
    for key, attr in (
        ("goals_for", "goals"),
        ("goals_against", "goals_against"),
        ("corners", "corners"),
        ("cards", "cards"),
    ):
        val = _to_float(data.get(key))
        if val is not None:
            overrides[attr] = val

    # Sin el promedio de goles no hay nada util (es el ancla del modelo).
    if "goals" not in overrides:
        return None

    context = (data.get("context") or "").strip() or None
    signals = data.get("signals") or {}
    role = str(signals.get("role", "neutral")).strip().lower()
    if role not in ("favorito", "defensivo", "neutral"):
        role = "neutral"
    return TeamForm(
        team_name=team_name,
        venue=venue,
        samples=[],
        context=context,
        must_win=bool(signals.get("must_win", False)),
        role=role,
        key_attacker_out=bool(signals.get("key_attacker_out", False)),
        overrides=overrides,
    )
