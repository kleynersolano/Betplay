"""
Calculo de lambda, probabilidad real (Poisson para goles/tarjetas,
promedio historico para corners) y value%.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.config import MIN_VALUE_PERCENT
from app.scraper_betplay import Match, MarketLine
from app.stats_provider import TeamForm

def _stat_for_market(market: str) -> str | None:
    """Clasifica un nombre de mercado real de BetPlay (puede incluir el
    nombre del equipo, ej. 'Total de goles de Suecia') segun la estadistica
    que mide."""
    m = market.lower()
    if "esquina" in m:
        return "corners"
    if "tarjeta" in m:
        return "cards"
    if "gol" in m:
        return "goals"
    return None

USES_POISSON = {"goals", "cards"}


@dataclass
class BetEvaluation:
    match: Match
    market: str
    selection: str
    odds: float
    prob_real: float
    value_percent: float
    home_context: str | None = None
    away_context: str | None = None


def _poisson_over_prob(lam: float, line: float) -> float:
    k_max = math.floor(line)
    cumulative = sum(
        math.exp(-lam) * lam**k / math.factorial(k) for k in range(0, k_max + 1)
    )
    return max(0.0, min(1.0, 1 - cumulative))


def _parse_line_value(selection: str) -> tuple[str, float] | None:
    m = re.search(r"(\d+(?:\.\d+)?)", selection)
    if not m:
        return None
    line = float(m.group(1))
    direction = "over" if re.search(r"m[ae]s", selection, re.I) else "under"
    return direction, line


def _combined_lambda(home_avg: float, away_avg: float) -> float:
    return (home_avg + away_avg) / 2


def _goals_lambda(home_form: TeamForm, away_form: TeamForm) -> float | None:
    """Lambda de goles totales del partido = goles esperados de cada equipo,
    donde lo esperado de un equipo es el promedio de (sus goles anotados,
    los goles que concede su rival), siguiendo la metodologia pedida:
    'goles promedio equipo + goles concedidos promedio contrario / 2'."""
    home_for = home_form.average("goals")
    away_against = away_form.average("goals_against")
    away_for = away_form.average("goals")
    home_against = home_form.average("goals_against")
    if None in (home_for, away_against, away_for, home_against):
        return None
    home_expected = (home_for + away_against) / 2
    away_expected = (away_for + home_against) / 2
    return home_expected + away_expected


def _implied_prob(odds: float) -> float:
    return 1 / odds


def evaluate_match(
    match: Match, home_form: TeamForm | None, away_form: TeamForm | None
) -> list[BetEvaluation]:
    if home_form is None or away_form is None or not home_form.valid or not away_form.valid:
        return []

    evaluations: list[BetEvaluation] = []
    for line in match.lines:
        stat = _stat_for_market(line.market)
        if stat is None:
            continue

        if stat == "goals":
            lam = _goals_lambda(home_form, away_form)
        else:
            home_avg = home_form.average(stat)
            away_avg = away_form.average(stat)
            lam = _combined_lambda(home_avg, away_avg) if home_avg is not None and away_avg is not None else None
        if lam is None:
            continue

        parsed = _parse_line_value(line.selection)
        if parsed is None:
            continue

        direction, line_value = parsed

        if stat in USES_POISSON:
            prob_over = _poisson_over_prob(lam, line_value)
            prob_real = prob_over if direction == "over" else 1 - prob_over
        else:
            prob_over = min(0.95, max(0.05, 0.5 + (lam - line_value) / max(lam, 1)))
            prob_real = prob_over if direction == "over" else 1 - prob_over

        implied = _implied_prob(line.odds)
        value_percent = (prob_real - implied) / implied * 100 if implied > 0 else 0.0

        if value_percent >= MIN_VALUE_PERCENT:
            evaluations.append(
                BetEvaluation(
                    match=match,
                    market=line.market,
                    selection=line.selection,
                    odds=line.odds,
                    prob_real=prob_real,
                    value_percent=value_percent,
                    home_context=home_form.context,
                    away_context=away_form.context,
                )
            )

    evaluations.sort(key=lambda e: e.prob_real, reverse=True)
    return evaluations[:3]
