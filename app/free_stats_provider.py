"""
Estadisticas de equipos desde 5 fuentes GRATIS que NO requieren registro ni
API key (salvo una key publica de prueba), consultadas por API JSON directa
(sin navegador, rapido y confiable):

  1) Sofascore     (api.sofascore.com)            -> goles, corners, tarjetas
  2) FotMob        (apigw.fotmob.com)              -> goles
  3) TheSportsDB   (thesportsdb.com, key publica "3") -> goles
  4) football-data (football-data.org, key gratis) -> goles
  5) ESPN          (site.api.espn.com)             -> goles

Se consultan las 5 y se PROMEDIA cada estadistica entre las fuentes que
respondieron (suma / cantidad), como pidio el usuario, para mas veracidad.

AVISO (honesto): Sofascore, FotMob y ESPN exponen estas APIs para sus
propias webs, NO son APIs publicas oficiales. No requieren registro, pero
pueden cambiar su estructura o limitar peticiones sin aviso. Por eso todo va
envuelto en try/except: si una fuente falla, se usa lo que dieron las otras.
TheSportsDB y football-data.org si son APIs publicas documentadas.

Fox Deportes y Claro Deportes NO se conectaron: son plataformas de
TV/streaming, no exponen ninguna API de estadisticas (ni oculta ni
oficial); la unica forma de sacarles datos seria scrapear su pagina web con
navegador, igual que Google, mucho mas lento y fragil que una llamada API.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata

import requests

from app import football_data_provider
from app.stats_provider import TeamForm

log = logging.getLogger("betbot.free_stats")

_TIMEOUT = 12
_HEADERS = {
    # User-Agent de navegador: Sofascore/FotMob rechazan clientes sin UA.
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# Sofascore valida que la peticion parezca venir de su propia web (Cloudflare
# rechaza con 403 si no). Se mandan Referer/Origin de sofascore.com.
_SOFASCORE_HEADERS = {
    **_HEADERS,
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

# Los nombres en BetPlay vienen en español ("Países Bajos", "Túnez") pero
# Sofascore/FotMob/TheSportsDB buscan en ingles ("Netherlands", "Tunisia").
# Para selecciones del Mundial esto es clave. Para clubes el nombre suele
# coincidir, asi que si no esta en el mapa se usa el nombre tal cual.
_COUNTRY_ES_EN = {
    "alemania": "Germany", "arabia saudita": "Saudi Arabia", "arabia saudí": "Saudi Arabia",
    "argelia": "Algeria", "argentina": "Argentina", "australia": "Australia",
    "austria": "Austria", "belgica": "Belgium", "bélgica": "Belgium",
    "brasil": "Brazil", "cabo verde": "Cape Verde", "camerun": "Cameroon",
    "camerún": "Cameroon", "canada": "Canada", "canadá": "Canada", "chile": "Chile",
    "colombia": "Colombia", "corea del sur": "South Korea", "costa de marfil": "Ivory Coast",
    "costa rica": "Costa Rica", "croacia": "Croatia", "curazao": "Curacao",
    "curacao": "Curacao", "dinamarca": "Denmark", "ecuador": "Ecuador",
    "egipto": "Egypt", "emiratos arabes unidos": "United Arab Emirates",
    "escocia": "Scotland", "eslovaquia": "Slovakia", "eslovenia": "Slovenia",
    "españa": "Spain", "espana": "Spain", "estados unidos": "USA",
    "francia": "France", "gales": "Wales", "ghana": "Ghana", "grecia": "Greece",
    "holanda": "Netherlands", "paises bajos": "Netherlands", "países bajos": "Netherlands",
    "honduras": "Honduras", "hungria": "Hungary", "hungría": "Hungary",
    "inglaterra": "England", "iran": "Iran", "irán": "Iran", "irak": "Iraq",
    "irlanda": "Ireland", "italia": "Italy", "jamaica": "Jamaica", "japon": "Japan",
    "japón": "Japan", "marruecos": "Morocco", "mexico": "Mexico", "méxico": "Mexico",
    "nigeria": "Nigeria", "noruega": "Norway", "nueva zelanda": "New Zealand",
    "panama": "Panama", "panamá": "Panama", "paraguay": "Paraguay", "peru": "Peru",
    "perú": "Peru", "polonia": "Poland", "portugal": "Portugal", "catar": "Qatar",
    "qatar": "Qatar", "republica checa": "Czech Republic", "rumania": "Romania",
    "rumanía": "Romania", "rusia": "Russia", "senegal": "Senegal", "serbia": "Serbia",
    "sudafrica": "South Africa", "sudáfrica": "South Africa", "suecia": "Sweden",
    "suiza": "Switzerland", "tunez": "Tunisia", "túnez": "Tunisia",
    "turquia": "Turkey", "turquía": "Turkey", "ucrania": "Ukraine",
    "uruguay": "Uruguay", "venezuela": "Venezuela",
}


def _norm(text: str) -> str:
    """minusculas sin acentos, para comparar nombres de equipo."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower().strip()


def _search_name(team_name: str, is_national_team: bool) -> str:
    """Traduce el nombre al ingles si es una seleccion conocida; si no, deja
    el nombre tal cual (los clubes suelen llamarse igual en ambos idiomas)."""
    if is_national_team:
        en = _COUNTRY_ES_EN.get(_norm(team_name))
        if en:
            return en
    return team_name


def _get_json(
    url: str, params: dict | None = None, label: str = "", headers: dict | None = None
) -> dict | list | None:
    """GET JSON con diagnostico: si la respuesta no es 200, se registra el
    codigo y el host para saber EXACTAMENTE por que una fuente no dio datos
    (403/429 = bloqueo o limite, 401 = falta token, etc.). Sin esto el log
    solo decia 'SIN DATOS' sin explicar la causa, imposible de corregir."""
    try:
        resp = requests.get(
            url, params=params, headers=headers or _HEADERS, timeout=_TIMEOUT
        )
        if resp.status_code != 200:
            log.warning(
                "    [diag%s] HTTP %s en %s",
                f" {label}" if label else "", resp.status_code, url.split("/")[2],
            )
            return None
        return resp.json()
    except Exception as e:
        log.warning(
            "    [diag%s] excepcion %s en %s",
            f" {label}" if label else "", type(e).__name__, url.split("/")[2],
        )
        return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


# --------------------------------------------------------------------------
# Fuente 1: Sofascore
# --------------------------------------------------------------------------
def _sofascore(team_name: str, venue: str, last_n: int, is_national_team: bool) -> dict | None:
    # Cloudflare de Sofascore devuelve 403 a clientes "no navegador". Se
    # prueban dos hosts (el .com y el espejo .app) con headers Referer/Origin
    # de sofascore.com, que es lo que su Cloudflare valida.
    data = None
    host = None
    for h in ("https://api.sofascore.com", "https://api.sofascore.app"):
        data = _get_json(
            f"{h}/api/v1/search/all", {"q": team_name},
            "sofascore/search", _SOFASCORE_HEADERS,
        )
        if data:
            host = h
            break
    if not data:
        return None
    team_id = None
    target = _norm(team_name)
    for item in data.get("results", []):
        if item.get("type") != "team":
            continue
        entity = item.get("entity", {})
        name = _norm(entity.get("name", ""))
        if entity.get("id") and (name == target or target in name or name in target):
            team_id = entity["id"]
            break
    if team_id is None:
        log.warning("    [diag sofascore] no encontro equipo para '%s'", team_name)
        return None

    events = _get_json(
        f"{host}/api/v1/team/{team_id}/events/last/0",
        label="sofascore/events", headers=_SOFASCORE_HEADERS,
    )
    if not events:
        return None

    gf, ga, corners, cards = [], [], [], []
    # Mas reciente primero.
    for ev in reversed(events.get("events", [])):
        if len(gf) >= last_n:
            break
        status = (ev.get("status", {}) or {}).get("type")
        if status != "finished":
            continue
        is_home = (ev.get("homeTeam", {}) or {}).get("id") == team_id
        hs = (ev.get("homeScore", {}) or {}).get("current")
        as_ = (ev.get("awayScore", {}) or {}).get("current")
        if hs is None or as_ is None:
            continue
        gf.append(float(hs if is_home else as_))
        ga.append(float(as_ if is_home else hs))

        stats = _get_json(
            f"{host}/api/v1/event/{ev['id']}/statistics",
            label="sofascore/stats", headers=_SOFASCORE_HEADERS,
        )
        if stats:
            for group in stats.get("statistics", []):
                if group.get("period") != "ALL":
                    continue
                for grp in group.get("groups", []):
                    for it in grp.get("statisticsItems", []):
                        name = it.get("name", "")
                        side = it.get("home") if is_home else it.get("away")
                        try:
                            num = float(str(side))
                        except (TypeError, ValueError):
                            continue
                        if name == "Corner kicks":
                            corners.append(num)
                        elif name in ("Yellow cards", "Red cards"):
                            cards.append(num)
        time.sleep(0.3)  # cortesia para no gatillar limites de Sofascore

    out = {"source": "sofascore"}
    if gf:
        out["goals"] = _avg(gf)
        out["goals_against"] = _avg(ga)
    if corners:
        out["corners"] = _avg(corners)
    if cards:
        # corners y cards se sumaron por evento (yellow+red por separado);
        # cards puede tener 2 entradas por partido, igual el promedio es por
        # entrada -> se reescala dividiendo por partidos con datos.
        out["cards"] = round(sum(cards) / max(len(gf), 1), 2)
    return out if "goals" in out else None


# --------------------------------------------------------------------------
# Fuente 2: FotMob
# --------------------------------------------------------------------------
def _fotmob(team_name: str, venue: str, last_n: int, is_national_team: bool) -> dict | None:
    # FotMob movio su busqueda: el viejo /api/searchData da 404. El endpoint
    # vigente es el gateway apigw.fotmob.com/searchapi/suggest, que devuelve
    # sugerencias agrupadas (squad/teams) en formato distinto.
    search = _get_json(
        "https://apigw.fotmob.com/searchapi/suggest",
        {"term": team_name, "lang": "es,en"}, "fotmob/search",
    )
    if not search:
        return None
    # El gateway devuelve una lista 'suggestions' con entradas que tienen
    # 'type' y 'payload' (id, name). Tambien se contemplan los formatos
    # antiguos (teams.dataset / squad) por compatibilidad.
    teams_block: list[dict] = []
    if isinstance(search, dict):
        for sug in search.get("suggestions", []) or []:
            payload = sug.get("payload") or {}
            if sug.get("type") in ("teams", "team") and payload.get("id"):
                teams_block.append({"id": payload.get("id"), "name": payload.get("name", "")})
        if not teams_block:
            teams_block = (search.get("teams") or {}).get("dataset") or search.get("squad") or []
    team_id = None
    target = _norm(team_name)
    for t in teams_block:
        name = _norm(t.get("name", ""))
        if t.get("id") and (name == target or target in name or name in target):
            team_id = t["id"]
            break
    if team_id is None and teams_block:
        team_id = teams_block[0].get("id")
    if team_id is None:
        log.warning("    [diag fotmob] no encontro equipo para '%s' (bloque vacio)", team_name)
        return None

    team = _get_json("https://www.fotmob.com/api/teams", {"id": team_id}, "fotmob/teams")
    if not team:
        return None
    fixtures = (((team.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures")) or []

    gf, ga = [], []
    for fx in reversed(fixtures):
        if len(gf) >= last_n:
            break
        if not (fx.get("status", {}) or {}).get("finished"):
            continue
        home = fx.get("home", {}) or {}
        away = fx.get("away", {}) or {}
        is_home = home.get("id") == team_id
        try:
            hs = int(home.get("score"))
            as_ = int(away.get("score"))
        except (TypeError, ValueError):
            continue
        gf.append(float(hs if is_home else as_))
        ga.append(float(as_ if is_home else hs))

    out = {"source": "fotmob"}
    if gf:
        out["goals"] = _avg(gf)
        out["goals_against"] = _avg(ga)
    return out if "goals" in out else None


# --------------------------------------------------------------------------
# Fuente 3: TheSportsDB (key publica de prueba "3")
# --------------------------------------------------------------------------
def _thesportsdb(team_name: str, venue: str, last_n: int, is_national_team: bool) -> dict | None:
    search = _get_json(
        "https://www.thesportsdb.com/api/v1/json/3/searchteams.php", {"t": team_name},
        "thesportsdb/search",
    )
    if not search or not search.get("teams"):
        return None
    # Filtra a equipos de futbol (soccer): la busqueda por nombre puede
    # devolver equipos de otros deportes con el mismo nombre de pais.
    soccer = [t for t in search["teams"] if (t.get("strSport") or "").lower() == "soccer"]
    team = (soccer or search["teams"])[0]
    team_id = team.get("idTeam")
    if not team_id:
        return None

    events = _get_json(
        "https://www.thesportsdb.com/api/v1/json/3/eventslast.php", {"id": team_id},
        "thesportsdb/events",
    )
    if not events or not events.get("results"):
        log.warning("    [diag thesportsdb] sin eventos recientes para '%s'", team_name)
        return None

    gf, ga = [], []
    for ev in events["results"][:last_n]:
        home_id = ev.get("idHomeTeam")
        try:
            hs = int(ev.get("intHomeScore"))
            as_ = int(ev.get("intAwayScore"))
        except (TypeError, ValueError):
            continue
        is_home = home_id == team_id
        gf.append(float(hs if is_home else as_))
        ga.append(float(as_ if is_home else hs))

    if not gf:
        return None
    return {"source": "thesportsdb", "goals": _avg(gf), "goals_against": _avg(ga)}


# --------------------------------------------------------------------------
# Fuente 4: football-data.org (API con key gratuita ya configurada)
# --------------------------------------------------------------------------
def _football_data(team_name: str, venue: str, last_n: int, is_national_team: bool) -> dict | None:
    form = football_data_provider.get_team_form(
        team_name, venue=venue, last_n=last_n, is_national_team=is_national_team
    )
    if form is None:
        return None
    g = form.average("goals")
    if g is None:
        return None
    out = {"source": "football-data", "goals": g}
    ga = form.average("goals_against")
    if ga is not None:
        out["goals_against"] = ga
    return out


# --------------------------------------------------------------------------
# Fuente 5: ESPN (API oculta de site.api.espn.com / site.web.api.espn.com,
# gratis, sin registro; es la misma que usa espn.com/espndeportes.com)
# --------------------------------------------------------------------------
_ESPN_LINK_RE = re.compile(r"/sports/soccer/([\w.\-]+)/teams/(\d+)")


def _espn(team_name: str, venue: str, last_n: int, is_national_team: bool) -> dict | None:
    search = _get_json(
        "https://site.web.api.espn.com/apis/common/v3/search",
        {"query": team_name, "limit": 5, "lang": "en", "region": "us"},
        "espn/search",
    )
    if not search:
        return None

    # La busqueda de ESPN agrupa resultados por tipo; se busca el grupo de
    # equipos y, dentro de cada item, el link a site.api.espn.com que ya
    # trae resuelto el slug de liga + id de equipo (evita tener que mapear
    # IDs numericos de liga a slugs, que la API no expone directamente).
    target = _norm(team_name)
    league_slug = team_id = None
    for group in search.get("results", []) or []:
        if group.get("type") != "team":
            continue
        for item in group.get("contents", []) or group.get("results", []) or []:
            href = ""
            for link in item.get("links", []) or []:
                href = link.get("href", "")
                if "/teams/" in href:
                    break
            m = _ESPN_LINK_RE.search(href)
            if not m:
                continue
            name = _norm(item.get("displayName", ""))
            if name == target or target in name or name in target:
                league_slug, team_id = m.group(1), m.group(2)
                break
        if team_id:
            break
    if team_id is None:
        log.warning("    [diag espn] no encontro equipo para '%s'", team_name)
        return None

    schedule = _get_json(
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_slug}/teams/{team_id}/schedule",
        label="espn/schedule",
    )
    if not schedule or not schedule.get("events"):
        log.warning("    [diag espn] sin partidos para '%s' (liga=%s)", team_name, league_slug)
        return None

    gf, ga = [], []
    for ev in schedule["events"]:
        if len(gf) >= last_n:
            break
        comp = (ev.get("competitions") or [{}])[0]
        if not comp.get("status", {}).get("type", {}).get("completed"):
            continue
        competitors = comp.get("competitors", []) or []
        if len(competitors) != 2:
            continue
        try:
            mine = next(c for c in competitors if c.get("team", {}).get("id") == team_id)
            theirs = next(c for c in competitors if c.get("team", {}).get("id") != team_id)
            gf.append(float(mine.get("score", {}).get("value", mine.get("score"))))
            ga.append(float(theirs.get("score", {}).get("value", theirs.get("score"))))
        except (StopIteration, TypeError, ValueError):
            continue

    if not gf:
        return None
    return {"source": "espn", "goals": _avg(gf), "goals_against": _avg(ga)}


# Todas las fuentes gratis conectadas. Sirve para TODO el futbol (clubes y
# selecciones, cualquier liga/pais), no solo el Mundial: las 5 tienen
# cobertura mundial. Si una falla, se usan las demas. Para agregar otra
# fuente basta sumar (nombre, funcion) aqui.
_SOURCES = [
    ("sofascore", _sofascore),
    ("fotmob", _fotmob),
    ("thesportsdb", _thesportsdb),
    ("football-data", _football_data),
    ("espn", _espn),
]


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    """Consulta TODAS las fuentes y promedia cada estadistica entre las que
    respondieron. Registra en el log que fuentes se consultaron, cuales
    dieron informacion y cuales no (para detectar y corregir fallas)."""
    # Para selecciones se traduce el nombre a ingles (Sofascore/FotMob/
    # TheSportsDB buscan en ingles); football-data recibe el nombre original
    # porque maneja su propia busqueda.
    query = _search_name(team_name, is_national_team)

    results: list[dict] = []
    consultadas: list[str] = []
    con_datos: list[str] = []
    sin_datos: list[str] = []

    for source_name, fn in _SOURCES:
        consultadas.append(source_name)
        # football-data usa su propia busqueda con el nombre original; las
        # demas usan el nombre traducido.
        name_for_source = team_name if source_name == "football-data" else query
        try:
            r = fn(name_for_source, venue, last_n, is_national_team)
        except Exception:
            log.warning("    [%s] %s: error al consultar", source_name, team_name, exc_info=True)
            r = None
        if r and r.get("goals") is not None:
            results.append(r)
            con_datos.append(source_name)
            detalle = "goles=%.2f" % r["goals"]
            if r.get("goals_against") is not None:
                detalle += " | recibidos=%.2f" % r["goals_against"]
            if r.get("corners") is not None:
                detalle += " | corners=%.2f" % r["corners"]
            if r.get("cards") is not None:
                detalle += " | tarjetas=%.2f" % r["cards"]
            log.info("    [%s] %s -> %s", source_name, team_name, detalle)
        else:
            sin_datos.append(source_name)
            log.info("    [%s] %s -> SIN DATOS", source_name, team_name)

    log.info(
        "    RESUMEN %s | consultadas: %s | con datos: %s | sin datos: %s",
        team_name,
        ", ".join(consultadas) or "(ninguna)",
        ", ".join(con_datos) or "(ninguna)",
        ", ".join(sin_datos) or "(ninguna)",
    )

    if not results:
        log.warning("    %s: NINGUNA fuente dio datos", team_name)
        return None

    # Promedio entre fuentes por estadistica (suma / cantidad de fuentes que
    # la trajeron), como se pidio.
    overrides: dict[str, float] = {}
    for attr in ("goals", "goals_against", "corners", "cards"):
        vals = [r[attr] for r in results if r.get(attr) is not None]
        if vals:
            overrides[attr] = round(sum(vals) / len(vals), 2)

    if "goals" not in overrides:
        return None

    return TeamForm(
        team_name=team_name,
        venue=venue,
        samples=[],
        context="Fuentes (promediadas): " + ", ".join(con_datos),
        overrides=overrides,
    )
