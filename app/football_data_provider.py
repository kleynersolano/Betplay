"""
Estadisticas de equipos via football-data.org (https://www.football-data.org/),
usado como segunda fuente gratuita cuando se agota la cuota de API-Football.

El plan gratuito de football-data.org no incluye estadisticas de partido
(corners/tarjetas), solo resultados. Por eso aqui solo se completan goles;
corners y tarjetas quedan en None y esos mercados se descartan si no hay
suficientes datos validos de otra fuente.

IMPORTANTE (verificado con curl real): el endpoint de busqueda libre
/v4/teams?name=... da 403 en el plan gratuito ("restricted ... check your
subscription"), aunque la key sea valida -- esa busqueda quedo solo para
planes pagos. El plan gratis si permite listar equipos por competicion
(/v4/competitions/{code}/teams), asi que se busca el nombre dentro de las
competiciones que el plan gratis cubre.
"""
from __future__ import annotations

import requests

from app.config import FOOTBALL_DATA_API_KEY, MIN_VALID_MATCHES
from app.stats_provider import TeamForm, TeamMatchStats

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}

# Competiciones que cubre el plan gratuito de football-data.org (las mismas
# que el bot ya prioriza via VALID_COMPETITIONS_KEYWORDS).
_FREE_COMPETITIONS = [
    "WC", "CL", "BL1", "BSA", "ELC", "EC", "PL", "PD", "FL1", "SA", "PPL", "DED",
]


def _find_team_id(team_name: str) -> int | None:
    if not FOOTBALL_DATA_API_KEY:
        return None
    target = team_name.strip().lower()
    for code in _FREE_COMPETITIONS:
        resp = requests.get(f"{BASE_URL}/competitions/{code}/teams", headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            # Free tier es 10 req/min; si se gasta la cuota recorriendo
            # competiciones se detiene en vez de seguir fallando en cadena.
            break
        if resp.status_code != 200:
            continue
        for team in resp.json().get("teams", []):
            name = (team.get("name") or "").lower()
            short = (team.get("shortName") or "").lower()
            if target == name or target == short or target in name or name in target:
                return team["id"]
    return None


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    if not FOOTBALL_DATA_API_KEY:
        return None
    team_id = _find_team_id(team_name)
    if team_id is None:
        return None
    resp = requests.get(
        f"{BASE_URL}/teams/{team_id}/matches",
        headers=HEADERS,
        params={"status": "FINISHED", "limit": last_n * 3},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    matches = resp.json().get("matches", [])
    samples: list[TeamMatchStats] = []
    for m in matches:
        is_home = m["homeTeam"]["id"] == team_id
        if (venue == "home") != is_home:
            continue
        score = m.get("score", {}).get("fullTime", {})
        goals = (score.get("home") if is_home else score.get("away")) or 0
        goals_against = (score.get("away") if is_home else score.get("home")) or 0
        samples.append(
            TeamMatchStats(
                corners=None, goals=float(goals), goals_against=float(goals_against), cards=None
            )
        )
        if len(samples) >= last_n:
            break
    if len(samples) < MIN_VALID_MATCHES:
        return None
    return TeamForm(team_name=team_name, venue=venue, samples=samples)
