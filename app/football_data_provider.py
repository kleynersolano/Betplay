"""
Estadisticas de equipos via football-data.org (https://www.football-data.org/),
usado como segunda fuente gratuita cuando se agota la cuota de API-Football.

El plan gratuito de football-data.org no incluye estadisticas de partido
(corners/tarjetas), solo resultados. Por eso aqui solo se completan goles;
corners y tarjetas quedan en None y esos mercados se descartan si no hay
suficientes datos validos de otra fuente.
"""
from __future__ import annotations

import requests

from app.config import FOOTBALL_DATA_API_KEY, MIN_VALID_MATCHES
from app.stats_provider import TeamForm, TeamMatchStats

BASE_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": FOOTBALL_DATA_API_KEY}


def _find_team_id(team_name: str) -> int | None:
    if not FOOTBALL_DATA_API_KEY:
        return None
    resp = requests.get(f"{BASE_URL}/teams", headers=HEADERS, params={"name": team_name}, timeout=15)
    if resp.status_code != 200:
        return None
    results = resp.json().get("teams", [])
    return results[0]["id"] if results else None


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
