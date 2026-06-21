"""
Calculo de lambda, probabilidad real (Poisson para goles/tarjetas/corners)
y value%.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass

from app.config import MIN_VALUE_PERCENT, MAX_VALUE_PERCENT, MARKET_PRIORITY, DEFAULT_OVERROUND

log = logging.getLogger("betbot.analysis")
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

# Goles, tarjetas y tiros de esquina son datos de CONTEO (numero de
# eventos por partido), por lo que la distribucion de Poisson aplica a los
# tres. Antes los corners usaban una aproximacion lineal cruda; se pasan a
# Poisson para seguir la misma metodologia cuantitativa que el resto.
USES_POISSON = {"goals", "cards", "corners"}

# Rangos PLAUSIBLES de la media (lambda) por partido, para futbol masculino
# de primera division. Si la lambda calculada cae fuera de su rango, los
# datos de origen (Google AI) son poco confiables y producirian una
# probabilidad absurda (ej. 52% de que un equipo reciba 0 tarjetas). En ese
# caso un apostador profesional NO apuesta ese mercado: se descarta. Hay un
# rango para el mercado TOTAL del partido y otro para el de UN equipo.
PLAUSIBLE_LAMBDA = {
    ("goals", "total"): (1.0, 5.0),
    ("goals", "team"): (0.3, 3.5),
    ("corners", "total"): (5.0, 15.0),
    ("corners", "team"): (1.5, 9.0),
    ("cards", "total"): (1.5, 8.0),
    ("cards", "team"): (0.8, 5.0),
}


def _lambda_is_plausible(lam: float, stat: str, is_team_market: bool) -> bool:
    lo, hi = PLAUSIBLE_LAMBDA.get((stat, "team" if is_team_market else "total"), (0.0, 1e9))
    return lo <= lam <= hi


@dataclass
class BetEvaluation:
    match: Match
    market: str
    selection: str
    odds: float
    prob_real: float
    value_percent: float
    stat: str = ""
    home_context: str | None = None
    away_context: str | None = None


def _poisson_over_prob(lam: float, line: float) -> float:
    k_max = math.floor(line)
    cumulative = sum(
        math.exp(-lam) * lam**k / math.factorial(k) for k in range(0, k_max + 1)
    )
    return max(0.0, min(1.0, 1 - cumulative))


def _parse_line_value(selection: str) -> tuple[str, float] | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)", selection)
    if not m:
        return None
    line = float(m.group(1).replace(",", "."))
    # OJO: la seleccion viene como "Más de 2.5" / "Menos de 2.5". El
    # patron anterior r"m[ae]s" NO coincidia con "Más" (tiene "á"
    # acentuada, no "a"), asi que TODA seleccion "Más de" terminaba
    # clasificada como "under" -- corrompiendo la mitad de los calculos
    # de probabilidad y por eso casi nunca habia value. Se usa "m[aá]s"
    # para cubrir la version con y sin acento.
    direction = "over" if re.search(r"m[aá]s", selection, re.I) else "under"
    return direction, line


def _combined_lambda(home_avg: float, away_avg: float) -> float:
    return (home_avg + away_avg) / 2


# --- Ajuste de lambda por contexto (como un apostador profesional) -----------
# Reglas FIJAS y acotadas (+/-15% maximo), disparadas SOLO por señales
# medibles (tabla/alineacion), nunca por corazonadas. Si la señal no esta
# presente, no hay ajuste.

def _team_goals_factor(form: TeamForm) -> float:
    """Multiplicador sobre los goles esperados de UN equipo."""
    factor = 1.0
    if form.must_win:
        factor *= 1.10          # necesita anotar -> ataca mas
    if form.role == "defensivo":
        factor *= 0.92          # se encierra -> genera menos ofensiva
    if form.key_attacker_out:
        factor *= 0.85          # baja de su goleador -> menos goles
    return factor


def _match_context_factor(home_form: TeamForm, away_form: TeamForm, stat: str) -> float:
    """Multiplicador de contexto a nivel PARTIDO para corners/tarjetas."""
    factor = 1.0
    pressing = (
        home_form.must_win or away_form.must_win
        or home_form.role == "favorito" or away_form.role == "favorito"
    )
    both_defensive = home_form.role == "defensivo" and away_form.role == "defensivo"
    if stat == "corners":
        if pressing:
            factor *= 1.10      # equipo presionando -> mas corners
        if both_defensive:
            factor *= 0.90      # dos equipos cerrados -> menos corners
    elif stat == "cards":
        # Partido de mucha tension (alguien obligado a ganar) -> mas faltas
        # y tarjetas.
        if home_form.must_win and away_form.must_win:
            factor *= 1.10
    # Se acota el ajuste total a +/-15% para no distorsionar el modelo.
    return max(0.85, min(1.15, factor))


def _team_goals_expected(team_form: TeamForm, opponent_form: TeamForm) -> float | None:
    """Goles esperados de UN equipo, ajustado por su propio contexto.

    La unica fuente de datos actual (Modo IA de Google) da el promedio de
    goles ANOTADOS por el equipo en sus ultimos partidos -- no separa goles
    a favor de goles en contra del rival, asi que no hay forma de calcular
    "goles que concede el rival" (ese dato simplemente no existe en
    overrides). Antes esta funcion promediaba goles_for con
    opponent.average("goals_against"), que para datos de Modo IA SIEMPRE es
    None -> devolvia None siempre -> el mercado "goals" quedaba excluido
    de toda evaluacion en evaluate_match() (nunca se llegaba ni a
    considerarlo). Se usa directamente el promedio de goles anotados del
    equipo, igual que ya se hace con corners/tarjetas."""
    team_for = team_form.average("goals")
    if team_for is None:
        return None
    return team_for * _team_goals_factor(team_form)


def _goals_lambda(home_form: TeamForm, away_form: TeamForm) -> float | None:
    """Lambda de goles TOTALES del partido (ambos equipos)."""
    home_expected = _team_goals_expected(home_form, away_form)
    away_expected = _team_goals_expected(away_form, home_form)
    if home_expected is None or away_expected is None:
        return None
    return home_expected + away_expected


def _market_team(market: str, home_team: str, away_team: str) -> str | None:
    """Detecta si un mercado es POR EQUIPO (ej. 'Total de goles de Curacao',
    'Total de tarjetas - Ecuador', 'Tiros de Esquina a favor de Ecuador') y
    de cual de los dos equipos del partido se trata. Devuelve 'home',
    'away' o None si el mercado es del TOTAL del partido (ningun equipo
    mencionado). Esto es critico: aplicar la lambda del partido completo a
    un mercado de un solo equipo infla la probabilidad real de forma
    masiva (se vio en produccion: value%>500 en 'Total de goles de
    Curacao')."""
    m = market.lower()
    if home_team.lower() in m:
        return "home"
    if away_team.lower() in m:
        return "away"
    return None


def _implied_prob(odds: float) -> float:
    return 1 / odds


def _build_opposite_index(match: Match) -> dict[tuple[str, float], dict[str, float]]:
    """Indexa las cuotas del partido por (mercado, valor de linea) para poder
    encontrar el lado opuesto. Para 'Total de goles | Más de 2.5 @ 2.05'
    guarda {('total de goles', 2.5): {'over': 2.05, 'under': <cuota Menos 2.5>}}.
    Con ambos lados se puede calcular el overround REAL del mercado y quitar
    el margen de la casa."""
    index: dict[tuple[str, float], dict[str, float]] = {}
    for line in match.lines:
        parsed = _parse_line_value(line.selection)
        if parsed is None:
            continue
        direction, line_value = parsed
        key = (line.market.lower(), line_value)
        index.setdefault(key, {})[direction] = line.odds
    return index


def _fair_implied_prob(
    line, direction: str, line_value: float,
    opp_index: dict[tuple[str, float], dict[str, float]],
) -> float:
    """Probabilidad implicita JUSTA = sin el margen de la casa (vig).
    La cuota cruda incluye el overround del corredor (Over+Under suman >100%).
    Si tenemos las dos cuotas de la linea, normalizamos por el overround real;
    si solo hay una, descontamos el margen tipico DEFAULT_OVERROUND. Sin esto
    el value queda inflado ~6-8% en TODA apuesta -> value falso."""
    raw = _implied_prob(line.odds)
    pair = opp_index.get((line.market.lower(), line_value), {})
    other = pair.get("under" if direction == "over" else "over")
    if other:
        overround = raw + _implied_prob(other)
    else:
        overround = DEFAULT_OVERROUND
    return raw / overround if overround > 0 else raw


def evaluate_match(
    match: Match, home_form: TeamForm | None, away_form: TeamForm | None
) -> list[BetEvaluation]:
    if home_form is None or away_form is None or not home_form.valid or not away_form.valid:
        log.info(
            "  [%s vs %s] sin forma valida (home=%s, away=%s) -> no se evalua",
            match.home_team, match.away_team,
            "ok" if home_form and home_form.valid else "FALTA",
            "ok" if away_form and away_form.valid else "FALTA",
        )
        return []

    evaluations: list[BetEvaluation] = []
    best_seen: tuple[float, str, str] | None = None  # (value%, market, selection)
    considered = 0
    opp_index = _build_opposite_index(match)
    for line in match.lines:
        stat = _stat_for_market(line.market)
        if stat is None:
            continue

        team = _market_team(line.market, match.home_team, match.away_team)

        if stat == "goals":
            if team == "home":
                lam = _team_goals_expected(home_form, away_form)
            elif team == "away":
                lam = _team_goals_expected(away_form, home_form)
            else:
                lam = _goals_lambda(home_form, away_form)
        else:
            if team == "home":
                team_avg = home_form.average(stat)
                lam = (
                    None if team_avg is None
                    else team_avg * _match_context_factor(home_form, away_form, stat)
                )
            elif team == "away":
                team_avg = away_form.average(stat)
                lam = (
                    None if team_avg is None
                    else team_avg * _match_context_factor(home_form, away_form, stat)
                )
            else:
                home_avg = home_form.average(stat)
                away_avg = away_form.average(stat)
                if home_avg is None or away_avg is None:
                    lam = None
                else:
                    lam = _combined_lambda(home_avg, away_avg) * _match_context_factor(
                        home_form, away_form, stat
                    )
        if lam is None:
            continue

        # Sanidad: si la media calculada es imposible para el mercado, los
        # datos de origen no son fiables -> no se apuesta (no se inventa value).
        if not _lambda_is_plausible(lam, stat, team is not None):
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

        # Probabilidad implicita JUSTA (sin el margen de la casa). Comparar
        # contra la cuota cruda sobreestimaba el value en el margen del
        # corredor (~6-8%), generando value falso en todo el tablero.
        implied = _fair_implied_prob(line, direction, line_value, opp_index)
        value_percent = (prob_real - implied) / implied * 100 if implied > 0 else 0.0

        considered += 1
        if best_seen is None or value_percent > best_seen[0]:
            best_seen = (value_percent, line.market, line.selection)

        if value_percent > MAX_VALUE_PERCENT:
            # Edge "demasiado bueno para ser verdad": casi siempre es lambda
            # mal estimada (Google AI dio cifras poco fiables), no una
            # oportunidad real. Se descarta en vez de inflar la confianza.
            log.info(
                "  [%s vs %s] descartado por value irreal (%.1f%% > %.1f%%) en %s | %s",
                match.home_team, match.away_team, value_percent, MAX_VALUE_PERCENT,
                line.market, line.selection,
            )
            continue

        if value_percent >= MIN_VALUE_PERCENT:
            evaluations.append(
                BetEvaluation(
                    match=match,
                    market=line.market,
                    selection=line.selection,
                    odds=line.odds,
                    prob_real=prob_real,
                    value_percent=value_percent,
                    stat=stat,
                    home_context=home_form.context,
                    away_context=away_form.context,
                )
            )

    if best_seen is not None:
        log.info(
            "  [%s vs %s] %d lineas evaluadas | mejor value=%.1f%% (umbral %.1f%%) en %s | %s",
            match.home_team, match.away_team, considered,
            best_seen[0], MIN_VALUE_PERCENT, best_seen[1], best_seen[2],
        )
    else:
        log.info(
            "  [%s vs %s] 0 lineas evaluables (las %d cuotas extraidas no eran goles/tarjetas/esquinas o no se pudieron parsear)",
            match.home_team, match.away_team, len(match.lines),
        )

    # Se ordenan TODAS las apuestas validas (cuota>=2.0 y value>=umbral)
    # por Probabilidad Real descendente y se toman las 3 mejores, segun la
    # metodologia pedida ("Ordena TODAS las apuestas validas por Prob.Real
    # mayor a menor, selecciona TOP 3"). Ante empate de probabilidad se
    # desempata por value% y luego por la prioridad de mercado
    # (esquinas > goles > tarjetas).
    evaluations.sort(
        key=lambda e: (
            e.prob_real,
            e.value_percent,
            -(MARKET_PRIORITY.index(e.stat) if e.stat in MARKET_PRIORITY else len(MARKET_PRIORITY)),
        ),
        reverse=True,
    )
    return evaluations[:3]
