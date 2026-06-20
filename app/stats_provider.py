"""
Estadisticas de equipos via API-Football (api-sports.io / API-Football).
Plan gratuito: https://www.api-football.com/ (registro gratis, 100 req/dia).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from app.config import API_FOOTBALL_KEY, MIN_VALID_MATCHES

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}


@dataclass
class TeamMatchStats:
    corners: float | None
    goals: float
    goals_against: float | None
    cards: float | None


@dataclass
class TeamForm:
    team_name: str
    venue: str
    samples: list[TeamMatchStats]
    context: str | None = None
    # Señales de contexto MEDIBLES que un apostador profesional usa para
    # ajustar la lambda (no son corazonadas: vienen de tabla/alineacion).
    #   must_win:          el equipo necesita ganar/anotar (presion en tabla)
    #                      -> tiende a atacar mas (mas goles y corners).
    #   role:              "favorito" (presiona, genera corners),
    #                      "defensivo" (se encierra, menos ataque) o
    #                      "neutral".
    #   key_attacker_out:  baja confirmada de goleador/referente ofensivo
    #                      -> menos goles esperados de ese equipo.
    must_win: bool = False
    role: str = "neutral"
    key_attacker_out: bool = False
    # Promedios que vienen de OTRA fuente (ej. goles confiables de
    # football-data.org mientras corners/tarjetas vienen de Google). Si una
    # estadistica esta aqui, average() la devuelve directo sin mirar samples.
    overrides: dict[str, float] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        # Es valido si tiene suficientes muestras propias O si tiene goles
        # de una fuente externa confiable (caso football-data: goles via
        # override aunque corners/tarjetas falten).
        return len(self.samples) >= MIN_VALID_MATCHES or "goals" in self.overrides

    def average(self, attr: str) -> float | None:
        if attr in self.overrides:
            return self.overrides[attr]
        values = [getattr(s, attr) for s in self.samples if getattr(s, attr) is not None]
        if len(values) < MIN_VALID_MATCHES:
            return None
        return sum(values) / len(values)


def _find_team_id(team_name: str) -> int | None:
    if not API_FOOTBALL_KEY:
        return None
    resp = requests.get(f"{BASE_URL}/teams", headers=HEADERS, params={"search": team_name}, timeout=15)
    resp.raise_for_status()
    results = resp.json().get("response", [])
    return results[0]["team"]["id"] if results else None


def get_team_form(
    team_name: str, venue: str, last_n: int = 10, is_national_team: bool = False
) -> TeamForm | None:
    if not API_FOOTBALL_KEY:
        return None
    team_id = _find_team_id(team_name)
    if team_id is None:
        return None
    resp = requests.get(
        f"{BASE_URL}/fixtures",
        headers=HEADERS,
        params={"team": team_id, "last": last_n * 2, "status": "FT"},
        timeout=15,
    )
    resp.raise_for_status()
    fixtures = resp.json().get("response", [])
    samples: list[TeamMatchStats] = []
    for fx in fixtures:
        is_home = fx["teams"]["home"]["id"] == team_id
        if (venue == "home") != is_home:
            continue
        fixture_id = fx["fixture"]["id"]
        stats = _fixture_team_stats(fixture_id, team_id)
        if stats is None:
            continue
        samples.append(stats)
        if len(samples) >= last_n:
            break
    return TeamForm(team_name=team_name, venue=venue, samples=samples)


def _fixture_team_stats(fixture_id: int, team_id: int) -> TeamMatchStats | None:
    resp = requests.get(
        f"{BASE_URL}/fixtures/statistics",
        headers=HEADERS,
        params={"fixture": fixture_id, "team": team_id},
        timeout=15,
    )
    resp.raise_for_status()
    blocks = resp.json().get("response", [])
    if not blocks:
        return None
    stats_map = {s["type"]: s["value"] for s in blocks[0].get("statistics", [])}
    corners = stats_map.get("Corner Kicks")
    cards = (stats_map.get("Yellow Cards") or 0) + (stats_map.get("Red Cards") or 0)
    goals_resp = requests.get(
        f"{BASE_URL}/fixtures",
        headers=HEADERS,
        params={"id": fixture_id},
        timeout=15,
    )
    goals_resp.raise_for_status()
    fx_data = goals_resp.json().get("response", [])
    goals = 0.0
    goals_against = 0.0
    if fx_data:
        goals_block = fx_data[0]["goals"]
        is_home = fx_data[0]["teams"]["home"]["id"] == team_id
        goals = (goals_block["home"] if is_home else goals_block["away"]) or 0
        goals_against = (goals_block["away"] if is_home else goals_block["home"]) or 0
    return TeamMatchStats(
        corners=float(corners) if corners is not None else None,
        goals=float(goals),
        goals_against=float(goals_against),
        cards=float(cards),
    )
