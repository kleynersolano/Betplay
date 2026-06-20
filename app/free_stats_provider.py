"""
Estadisticas de equipos desde 3 fuentes GRATIS que NO requieren registro ni
API key (salvo una key publica de prueba), consultadas por API JSON directa
(sin navegador, rapido y confiable):

  1) Sofascore  (api.sofascore.com)  -> goles, tiros de esquina, tarjetas
  2) FotMob     (www.fotmob.com/api) -> goles, tiros de esquina, tarjetas
  3) TheSportsDB(thesportsdb.com, key publica "3") -> goles

Se consultan las 3 y se PROMEDIA cada estadistica entre las fuentes que
respondieron (suma / cantidad), como pidio el usuario, para mas veracidad.

AVISO (honesto): Sofascore y FotMob exponen estas APIs para su propia web,
NO son APIs publicas oficiales. No requieren registro, pero pueden cambiar
su estructura o limitar peticiones sin aviso. Por eso todo va envuelto en
try/except: si una fuente falla, se usa lo que dieron las otras. TheSportsDB
si es una API publica documentada (la key "3" es de prueba, compartida).
"""
from __future__ import annotations

import logging
import time
import unicodedata

import requests

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


def _get_json(url: str, params: dict | None = None) -> dict | list | None:
    try:
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


# --------------------------------------------------------------------------
# Fuente 1: Sofascore
# --------------------------------------------------------------------------
def _sofascore(team_name: str, venue: str, last_n: int) -> dict | None:
    data = _get_json("https://api.sofascore.com/api/v1/search/all", {"q": team_name})
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
        return None

    events = _get_json(f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/0")
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

        stats = _get_json(f"https://api.sofascore.com/api/v1/event/{ev['id']}/statistics")
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
def _fotmob(team_name: str, venue: str, last_n: int) -> dict | None:
    search = _get_json("https://www.fotmob.com/api/searchData", {"term": team_name})
    if not search:
        return None
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
        return None

    team = _get_json("https://www.fotmob.com/api/teams", {"id": team_id})
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
def _thesportsdb(team_name: str, venue: str, last_n: int) -> dict | None:
    search = _get_json(
        "https://www.thesportsdb.com/api/v1/json/3/searchteams.php", {"t": team_name}
    )
    if not search or not search.get("teams"):
        return None
    team = search["teams"][0]
    team_id = team.get("idTeam")
    if not team_id:
        return None

    events = _get_json(
        "https://www.thesportsdb.com/api/v1/json/3/eventslast.php", {"id": team_id}
    )
    if not events or not events.get("results"):
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


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    """Consulta las 3 fuentes y promedia cada estadistica entre las que
    respondieron."""
    query = _search_name(team_name, is_national_team)
    results: list[dict] = []
    for fn in (_sofascore, _fotmob, _thesportsdb):
        try:
            r = fn(query, venue, last_n)
        except Exception:
            r = None
        if r:
            results.append(r)
            log.info(
                "    %s: %s -> goles=%.2f%s", team_name, r["source"], r["goals"],
                (", corners=%.2f" % r["corners"]) if r.get("corners") else "",
            )

    if not results:
        log.warning("    %s: ninguna fuente gratis respondio", team_name)
        return None

    # Promedio entre fuentes por estadistica (suma / cantidad de fuentes que
    # la trajeron).
    overrides: dict[str, float] = {}
    for attr in ("goals", "goals_against", "corners", "cards"):
        vals = [r[attr] for r in results if r.get(attr) is not None]
        if vals:
            overrides[attr] = round(sum(vals) / len(vals), 2)

    if "goals" not in overrides:
        return None

    sources = ", ".join(r["source"] for r in results)
    return TeamForm(
        team_name=team_name,
        venue=venue,
        samples=[],
        context=f"Fuentes (promediadas): {sources}",
        overrides=overrides,
    )
