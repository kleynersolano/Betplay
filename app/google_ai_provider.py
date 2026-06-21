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
import os
import re
import time

from playwright.sync_api import sync_playwright

from app.config import (
    GOOGLE_AI_CACHE_FILE,
    GOOGLE_AI_CACHE_HOURS,
    GOOGLE_AI_HEADLESS,
    GOOGLE_AI_PROFILE_DIR,
    GOOGLE_AI_URL,
)
from app.stats_provider import TeamForm

log = logging.getLogger("betbot.google_ai")

# Una sola pregunta corta y directa por equipo: promedio de goles, corners
# y tarjetas de sus ultimos 10 partidos. Antes se pedia esto separado por
# local/visitante con instrucciones largas (mas lento y menos confiable de
# escribir/leer); como el contexto del partido no importa, se simplifico a
# una pregunta breve sobre los ultimos 10 partidos en general.
#
# OJO: NO debe terminar en salto de linea. Se escribe tecla por tecla en el
# cuadro del Modo IA y un '\n' final dispara un Enter extra que ensucia el
# envio. El Modo IA suele responder en PROSA (no en JSON) aunque se le pida
# JSON, asi que abajo se extraen los numeros del texto por cercania a las
# palabras clave; el JSON es solo un "si puedes".
PROMPT_TEMPLATE = (
    'Necesito 3 promedios por partido de "{team}", calculados sobre sus '
    "EXACTAMENTE 10 ultimos partidos oficiales ya jugados (clubes o "
    "selecciones, los mas recientes con resultado final). Estadisticas: "
    "goles anotados, tiros de esquina a favor (corners) y tarjetas "
    "recibidas (amarillas + rojas). "
    "METODO OBLIGATORIO Y RIGUROSO (para que el resultado sea SIEMPRE el "
    "mismo, sin variar entre consultas): "
    "1) Consulta estas 3 fuentes confiables, en este orden de "
    "confiabilidad: (a) Sofascore, (b) FBref, (c) WhoScored. "
    "2) En CADA fuente saca el promedio por partido de los mismos 10 "
    "ultimos partidos (suma de los 10 dividida entre 10). "
    "3) El valor final de cada estadistica es el PROMEDIO de los valores de "
    "las 3 fuentes. Si solo 2 fuentes tienen el dato, promedia entre esas "
    "2; si solo 1 la tiene, usa esa 1. Nunca inventes una fuente. "
    "4) Redondea cada resultado final a 2 decimales. "
    "5) NO estimes ni uses 'estilo de juego' ni partidos similares: solo "
    "cifras reales tomadas de esas fuentes. NUNCA respondas null ni 'no "
    "disponible'. "
    "Responde SOLO el JSON, sin texto extra: "
    '{{"goals": <numero>, "corners": <numero>, "cards": <numero>}}'
)

# Numeros decimales (con , o .): los promedios casi siempre se reportan asi
# ("1.8", "2,45"), no como enteros (años, marcadores). Filtra mucho ruido.
_NUM_RE = re.compile(r"\d+[.,]\d+")

# Rangos plausibles del PROMEDIO POR PARTIDO de UN equipo, para descartar
# numeros que claramente no son la estadistica (años 2026, posesion 60.5, etc.).
_RANGES = {
    "goals": (0.3, 4.0),
    "corners": (1.5, 11.0),
    "cards": (0.5, 6.0),
}

# Palabras clave junto a las que suele aparecer cada cifra en la respuesta.
# Para goles se prioriza "anotad/a favor" para no agarrar los recibidos.
_KEYWORDS = {
    "goals": ("goles anotados", "goles a favor", "anotad", "goles", "gol"),
    "corners": ("tiros de esquina", "esquina", "corner", "córner"),
    "cards": ("tarjetas", "tarjeta", "amarillas", "amonest"),
}


def _num_near(text: str, keywords, lo: float, hi: float) -> float | None:
    """Busca el primer numero decimal plausible (dentro del rango) que este
    cerca de alguna de las palabras clave dadas, recorriendolas en orden de
    prioridad. Asi se separa, dentro de la misma respuesta en prosa, cual
    cifra corresponde a goles, cual a corners y cual a tarjetas."""
    low = text.lower()
    for kw in keywords:
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx == -1:
                break
            window = text[max(0, idx - 45): idx + 45]
            for m in _NUM_RE.finditer(window):
                try:
                    v = float(m.group(0).replace(",", "."))
                except ValueError:
                    continue
                if lo <= v <= hi:
                    return round(v, 2)
            start = idx + len(kw)
    return None


def _extract_json(text: str) -> dict | None:
    """Busca el PRIMER objeto JSON VALIDO que contenga "goals" dentro del
    texto de la pagina. Importante: el texto trae primero el ECO de la
    pregunta, que incluye una plantilla {"goals": <n>, ...} que NO es JSON
    valido (los <n> rompen json.loads). Por eso no basta con el primer
    "goals": se recorren todas sus apariciones y se devuelve la primera que
    parsee bien (el bloque JSON real que el Modo IA pone como respuesta)."""
    search_start = 0
    while True:
        key_pos = text.find('"goals"', search_start)
        if key_pos == -1:
            return None
        start = text.rfind("{", 0, key_pos)
        if start == -1:
            search_start = key_pos + 7
            continue
        depth = 0
        end = None
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        # Esa aparicion no dio JSON valido (ej. el eco con <n>); seguir.
        search_start = key_pos + 7


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
_MAX_RELOADS = 3  # tope de recargas (F5) dentro de un mismo intento si se traba
_MAX_ATTEMPTS = 5  # tope de intentos completos (pagina nueva) si no llega ningun resultado


def _ask_google_ai_mode(prompt: str, max_wait_ms: int = 120_000) -> str | None:
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

    # El Modo IA responde en PROSA (no en JSON), generandola de a poco
    # ("streaming"). No se busca un texto literal: se mide el texto visible
    # de la pagina y se espera a que (1) CREZCA respecto a lo que habia justo
    # tras enviar (señal de que la respuesta empezo a aparecer) y luego (2)
    # se ESTABILICE (deje de crecer dos sondeos seguidos = termino de
    # escribir). Si en _STALL_RELOAD_MS no crecio nada, se asume trabado y se
    # recarga (F5) devolviendo None para reintentar.
    body = page.locator("body")
    try:
        base_text = body.inner_text(timeout=2000)
    except Exception:
        base_text = ""
    base_len = len(base_text)

    elapsed = 0
    poll_ms = 2000
    last_text = ""
    stable = 0
    grew_at = 0  # ultimo momento (ms) en que el texto crecio
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
        # La respuesta ya aporto contenido nuevo respecto al estado inicial.
        if len(text) > base_len + 40:
            if text == last_text:
                stable += 1
                # Estable ~2 sondeos seguidos => termino de generar.
                if stable >= 2:
                    return text
            else:
                stable = 0
                grew_at = elapsed
            last_text = text
        # Si nunca crecio (o se trabo) por _STALL_RELOAD_MS, recargar.
        no_growth_ms = elapsed - grew_at if last_text else elapsed
        if no_growth_ms >= _STALL_RELOAD_MS:
            log.warning("Modo IA trabado %ds sin avanzar, se recargara (F5)", no_growth_ms // 1000)
            try:
                page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            return None

    # Se acabo el tiempo: si alcanzo a aparecer algo de respuesta, se
    # devuelve lo ultimo capturado (puede tener igual los numeros).
    if last_text:
        return last_text
    log.warning("Timeout esperando respuesta del Modo IA (no aparecio nada)")
    return None


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get_team_form_once(team_name: str, venue: str) -> TeamForm | None:
    """Un intento completo: pregunta al Modo IA y parsea la respuesta. Puede
    devolver None si no hubo respuesta o no se pudo extraer el promedio de
    goles; el llamador (get_team_form) decide si reintentar con una pagina
    nueva."""
    prompt = PROMPT_TEMPLATE.format(team=team_name)
    try:
        raw = _ask_google_ai_mode(prompt)
    except Exception:
        log.exception("Fallo consultando Google AI para %s", team_name)
        return None
    if not raw:
        return None

    # DEBUG: se guarda la respuesta cruda del Modo IA en un archivo para poder
    # ver exactamente como redacta Google los numeros y afinar el parser.
    try:
        safe = re.sub(r"[^a-zA-Z0-9]+", "_", team_name).strip("_")
        dump_path = f"/tmp/modo_ia_{safe}.txt"
        with open(dump_path, "w", encoding="utf-8") as fh:
            fh.write(raw)
        log.info("    [debug] respuesta cruda guardada en %s", dump_path)
    except Exception:
        pass

    overrides: dict[str, float] = {}

    # 1) El Modo IA casi siempre responde con un bloque JSON limpio. Se le
    #    pide explicitamente que NUNCA ponga null, pero por si acaso aun cae
    #    alguno, abajo (paso 2) se rescata el dato de la prosa.
    data = _extract_json(raw)
    if data is not None:
        for key in ("goals", "corners", "cards"):
            val = _to_float(data.get(key))
            if val is not None:
                overrides[key] = val
                log.info("    %s %s=%.2f (Modo IA, JSON)", team_name, key, val)

    # 2) Para lo que falte (sin JSON, o un null que se colo), se rescata la
    #    cifra de la prosa por cercania a sus palabras clave, con rangos de
    #    sanidad. El objetivo es no dejar NINGUN dato vacio.
    goals_val = overrides.get("goals")
    for key, (lo, hi) in _RANGES.items():
        if key in overrides:
            continue
        val = _num_near(raw, _KEYWORDS[key], lo, hi)
        # Evita el bug viejo: que corners/tarjetas copien el numero de goles
        # cuando la prosa dice "no disponible" y el unico decimal cerca es el
        # de goles. Si la cifra rescatada es identica a los goles, se descarta.
        if val is not None and key != "goals" and goals_val is not None and abs(val - goals_val) < 0.001:
            val = None
        if val is not None:
            overrides[key] = val
            log.info("    %s %s=%.2f (Modo IA, prosa)", team_name, key, val)
        else:
            log.warning("    %s %s: el Modo IA no dio cifra", team_name, key)

    # Sin el promedio de goles no hay nada util (es el ancla del modelo).
    if "goals" not in overrides:
        log.warning("    %s: el Modo IA no dio promedio de goles claro", team_name)
        return None

    return TeamForm(team_name=team_name, venue=venue, samples=[], overrides=overrides)


def _load_cache() -> dict:
    try:
        with open(GOOGLE_AI_CACHE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _cache_get(team_name: str) -> dict | None:
    """Devuelve los overrides cacheados de un equipo si existen y no han
    expirado (TTL = GOOGLE_AI_CACHE_HOURS). Asi la misma estadistica se
    reusa entre ciclos en vez de re-preguntar al LLM (que da cifras
    distintas cada vez)."""
    entry = _load_cache().get(team_name.lower())
    if not entry:
        return None
    age_h = (time.time() - entry.get("ts", 0)) / 3600
    if age_h > GOOGLE_AI_CACHE_HOURS:
        return None
    overrides = entry.get("overrides")
    return overrides if isinstance(overrides, dict) and "goals" in overrides else None


def _cache_put(team_name: str, overrides: dict) -> None:
    cache = _load_cache()
    cache[team_name.lower()] = {"ts": time.time(), "overrides": overrides}
    try:
        tmp = f"{GOOGLE_AI_CACHE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, GOOGLE_AI_CACHE_FILE)
    except OSError:
        log.warning("    No se pudo escribir el cache de %s", team_name)


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    """Estadisticas de un equipo. PRIMERO mira el cache: si ya se consulto
    este equipo hace menos de GOOGLE_AI_CACHE_HOURS horas, REUSA esa cifra
    (clave para que las estadisticas no cambien al azar entre ciclos, ya que
    el Modo IA da numeros distintos cada vez que se le pregunta). Solo si no
    hay cache valido se consulta al Modo IA, reintentando con pagina nueva
    hasta _MAX_ATTEMPTS veces, y el resultado se guarda en cache."""
    cached = _cache_get(team_name)
    if cached is not None:
        log.info("    %s: usando estadisticas en cache %s", team_name, cached)
        return TeamForm(team_name=team_name, venue=venue, samples=[], overrides=dict(cached))

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        form = _get_team_form_once(team_name, venue)
        if form is not None:
            _cache_put(team_name, form.overrides)
            return form
        if attempt < _MAX_ATTEMPTS:
            log.warning(
                "    %s: sin resultado util en el intento %d/%d, reintentando con pagina nueva...",
                team_name, attempt, _MAX_ATTEMPTS,
            )
    log.warning("    %s: el Modo IA no dio resultado util tras %d intentos", team_name, _MAX_ATTEMPTS)
    return None
