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

from app.config import GOOGLE_AI_HEADLESS, GOOGLE_AI_PROFILE_DIR, GOOGLE_AI_URL
from app.stats_provider import TeamForm

log = logging.getLogger("betbot.google_ai")

# Una sola pregunta corta y directa por equipo: promedio de goles, corners
# y tarjetas de sus ultimos 10 partidos. Antes se pedia esto separado por
# local/visitante con instrucciones largas (mas lento y menos confiable de
# escribir/leer); como el contexto del partido no importa, se simplifico a
# una pregunta breve sobre los ultimos 10 partidos en general.
PROMPT_TEMPLATE = """Da el promedio por partido de "{team}" en sus ultimos 10 partidos: \
goles anotados (goals), tiros de esquina (corners) y tarjetas amarillas+rojas (cards). \
Responde UNICAMENTE con un JSON valido: {{"goals": <numero>, "corners": <numero o null>, \
"cards": <numero o null>}}
"""


def _extract_json(text: str) -> dict | None:
    """Busca el objeto JSON que contiene "goals" dentro de un texto que
    puede tener mucho mas contenido alrededor (toda la pagina visible).
    No usa una regex greedy de '{...}' porque con el texto de la pagina
    completa eso capturaria desde el primer '{' hasta el ULTIMO '}' de
    toda la pagina. En vez de eso, ubica el '{' que abre el objeto que
    contiene "goals" y cuenta llaves balanceadas hasta cerrarlo."""
    key_pos = text.find('"goals"')
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


def _input_center(page, locator) -> tuple[int, int]:
    """Coordenadas del centro del campo de texto, para un click por mouse
    como ultimo recurso cuando ni el foco por JS ni el click de Playwright
    (normal o forzado) lograron meter el foco en el elemento."""
    try:
        box = locator.bounding_box(timeout=1000)
        if box:
            return (int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2))
    except Exception:
        pass
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    return (viewport["width"] // 2, viewport["height"] // 2)


_STALL_RELOAD_MS = 60_000  # si en 60s no carga/responde, se asume trabado y se recarga (F5)
_MAX_RELOADS = 1  # tope de recargas antes de rendirse con este equipo


def _ask_google_ai_mode(prompt: str, max_wait_ms: int = 90_000) -> str | None:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            GOOGLE_AI_PROFILE_DIR,
            headless=GOOGLE_AI_HEADLESS,
        )
        try:
            page = context.new_page()
            for attempt in range(1 + _MAX_RELOADS):
                try:
                    result = _ask_once(page, prompt, max_wait_ms)
                except Exception:
                    # Una excepcion aca (ej. navegacion destruyendo el
                    # contexto de ejecucion justo tras el Enter) antes
                    # se propagaba sin atrapar y terminaba cerrando todo
                    # el navegador via el "finally" de abajo, en vez de
                    # dejar reintentar con la misma pagina.
                    log.warning("Modo IA: excepcion en el intento, reintentando...")
                    result = None
                if result is not None:
                    return result
                if attempt < _MAX_RELOADS:
                    log.warning("Modo IA trabado, recargando (F5) y reintentando...")
            return None
        finally:
            context.close()


def _ask_once(page, prompt: str, max_wait_ms: int) -> str | None:
    """Un intento completo: navega, escribe el prompt y espera la respuesta.
    Si la pagina queda trabada 60s sin avanzar (sintoma visto en pruebas
    reales cuando el equipo se sobrecarga), se hace un refresh (F5) y se
    devuelve None para que el llamador reintente desde cero con la pagina
    ya recargada."""
    try:
        page.goto(GOOGLE_AI_URL, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        log.warning("La pagina del Modo IA no cargo, recargando...")
        try:
            page.reload(wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            pass
    page.wait_for_timeout(2500)
    input_box = _find_input(page)
    if input_box is None:
        log.warning("No se encontro el campo de texto del Modo IA")
        return None

    def _typed_text() -> str:
        # input_value() solo funciona en <textarea>/<input>; el Modo IA
        # a veces usa un div contenteditable, donde input_value()
        # lanza error aunque el texto SI haya entrado. text_content()
        # cubre ambos casos.
        try:
            val = input_box.input_value(timeout=800)
            if val:
                return val.strip()
        except Exception:
            pass
        try:
            return (input_box.text_content(timeout=800) or "").strip()
        except Exception:
            return ""

    # Se intentan varias estrategias de foco/escritura en orden, cada
    # una mas agresiva que la anterior, porque ninguna sola resulto
    # confiable en pruebas reales: el autofocus a veces no alcanza, el
    # click normal de Playwright exige "accionabilidad" que el
    # textarea del Modo IA no siempre cumple (timeout de 30s), y el
    # click forzado por si solo no garantiza que el foco quede en el
    # elemento correcto. Se verifica el texto realmente escrito tras
    # cada intento antes de pasar al siguiente.
    strategies = [
        lambda: input_box.evaluate("el => el.focus()"),
        lambda: input_box.click(timeout=3000),
        lambda: input_box.click(force=True, timeout=3000),
        lambda: page.mouse.click(*_input_center(page, input_box)),
    ]
    typed = ""
    for strategy in strategies:
        try:
            strategy()
        except Exception:
            pass
        page.wait_for_timeout(200)
        try:
            input_box.evaluate("el => { el.value !== undefined ? el.value = '' : el.textContent = ''; }")
        except Exception:
            pass
        page.keyboard.type(prompt, delay=8)
        page.wait_for_timeout(300)
        typed = _typed_text()
        if typed:
            break
    if not typed:
        log.warning("No se pudo escribir el prompt en el Modo IA")
        return None
    try:
        page.keyboard.press("Enter")
    except Exception:
        # Si Enter dispara una navegacion de pagina completa, Playwright
        # puede lanzar "Execution context was destroyed" justo en este
        # instante. Antes esto no se atrapaba y la excepcion se propagaba
        # hasta el "finally" de _ask_google_ai_mode, que cerraba el
        # navegador entero en vez de dejar que el sondeo de abajo
        # esperara la respuesta. Se ignora y se sigue al sondeo.
        pass

    # No se usa un selector fijo del contenedor de respuesta (la UI de
    # Google cambia seguido y nunca se confirmo contra el DOM real).
    # En vez de eso se espera a que el JSON pedido ("goals" aparezca
    # en cualquier parte del texto visible de la pagina, sondeando. Si
    # pasan 60s sin respuesta se asume que la pagina/el equipo esta
    # trabado y se recarga (F5) en vez de seguir esperando indefinidamente.
    elapsed = 0
    poll_ms = 1500
    body = page.locator("body")
    while elapsed < max_wait_ms:
        try:
            page.wait_for_timeout(poll_ms)
        except Exception:
            pass
        elapsed += poll_ms
        try:
            text = body.inner_text(timeout=2000)
        except Exception:
            continue
        if '"goals"' in text:
            return text
        if elapsed >= _STALL_RELOAD_MS:
            log.warning("Modo IA trabado %ds sin responder, se recargara (F5)", elapsed // 1000)
            try:
                page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            return None
    log.warning("Timeout esperando respuesta del Modo IA (no aparecio el JSON)")
    return None


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    prompt = PROMPT_TEMPLATE.format(team=team_name)
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

    overrides: dict[str, float] = {}
    for key in ("goals", "corners", "cards"):
        val = _to_float(data.get(key))
        if val is not None:
            overrides[key] = val

    # Sin el promedio de goles no hay nada util (es el ancla del modelo).
    if "goals" not in overrides:
        return None

    return TeamForm(team_name=team_name, venue=venue, samples=[], overrides=overrides)
