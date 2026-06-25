"""
Registro de la actividad del bot en una Google Sheet, para llevar control
manual del historial: partidos descartados, partidos analizados, y los
pronosticos (value%) que encontro el bot en cada corrida.

Usa una cuenta de servicio de Google Cloud (credenciales JSON) en vez del
navegador automatizado que se usa para el Modo IA: es la API oficial de
Sheets, estable y no depende de la UI de Google. Si las credenciales o el
ID de la hoja no estan configurados (GOOGLE_SHEETS_CREDENTIALS_FILE /
GOOGLE_SHEETS_ID vacios), el registro se omite en silencio: el bot sigue
funcionando igual (Telegram, consola) aunque Sheets no este configurado.
"""
from __future__ import annotations

import logging

from app.config import GOOGLE_SHEETS_CREDENTIALS_FILE, GOOGLE_SHEETS_ID

log = logging.getLogger("betbot.sheets")

TAB_DESCARTADOS = "Descartados"
TAB_ANALIZADOS = "Analizados"
TAB_PRONOSTICOS = "Pronosticos"

_MATCH_HEADERS = [
    "Pais", "Liga", "Fecha juego", "Hora Juego",
    "Equipo local", "Equipo Visitante", "Femenino/Masculino",
]
_HEADERS = {
    TAB_DESCARTADOS: _MATCH_HEADERS,
    TAB_ANALIZADOS: _MATCH_HEADERS,
    TAB_PRONOSTICOS: _MATCH_HEADERS + [
        "Value", "% de probabilidad", "cuota", "mercado", "apuestas", "monto apostado",
    ],
}

# "Fútbol" (con o sin el icono ⚽ que antepone el scraper) es el prefijo fijo
# de toda competicion extraida; se descarta para quedarnos solo con Pais/Liga.
_PREFIXES_TO_DROP = ("futbol", "fútbol", "⚽ futbol", "⚽ fútbol")

_FEMALE_KEYWORDS = ("femenino", "femenina", "women", "(f)", "(w)")

_client = None  # cache: una sola autenticacion por proceso, no por llamada
_sheet = None


def _get_sheet():
    """Abre (y cachea) el documento de Sheets. Devuelve None si no hay
    credenciales configuradas o si falla la conexion -- en ambos casos el
    llamador debe seguir funcionando sin Sheets."""
    global _client, _sheet
    if not GOOGLE_SHEETS_CREDENTIALS_FILE or not GOOGLE_SHEETS_ID:
        return None
    if _sheet is not None:
        return _sheet
    try:
        import gspread
        _client = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS_FILE)
        _sheet = _client.open_by_key(GOOGLE_SHEETS_ID)
        return _sheet
    except Exception:
        log.exception("No se pudo conectar a Google Sheets, se omite el registro")
        return None


def _ensure_tab(sheet, name: str):
    try:
        ws = sheet.worksheet(name)
    except Exception:
        ws = sheet.add_worksheet(title=name, rows=2000, cols=20)
    if not ws.row_values(1):
        ws.append_row(_HEADERS[name], value_input_option="USER_ENTERED")
    return ws


def _country_and_league(competition: str) -> tuple[str, str]:
    """'Futbol / Estados Unidos / USL Championship' -> ('Estados Unidos',
    'USL Championship'). Torneos de seleccion sin pais propio ('Futbol /
    Mundial Futbol 2026') no tienen un tercer segmento: se devuelve
    'Internacional' como pais."""
    parts = [p.strip() for p in competition.split("/") if p.strip()]
    parts = [p for p in parts if p.lower() not in _PREFIXES_TO_DROP]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return "Internacional", parts[0]
    return "", competition


def _gender(competition: str) -> str:
    comp = competition.lower()
    return "F" if any(kw in comp for kw in _FEMALE_KEYWORDS) else "M"


def _match_row(match) -> list[str]:
    pais, liga = _country_and_league(match.competition)
    return [
        pais, liga,
        match.kickoff.strftime("%m/%d/%Y"),
        match.kickoff.strftime("%H:%M"),
        match.home_team, match.away_team,
        _gender(match.competition),
    ]


def _append(tab: str, row: list) -> None:
    sheet = _get_sheet()
    if sheet is None:
        return
    try:
        ws = _ensure_tab(sheet, tab)
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        log.exception("No se pudo agregar la fila a la pestana %s", tab)


def log_discarded(match) -> None:
    _append(TAB_DESCARTADOS, _match_row(match))


def log_analizado(match) -> None:
    _append(TAB_ANALIZADOS, _match_row(match))


def log_pronostico(evaluation) -> None:
    row = _match_row(evaluation.match) + [
        f"{evaluation.value_percent:.1f}%",
        f"{evaluation.prob_real * 100:.1f}%",
        f"{evaluation.odds:.2f}",
        evaluation.market,
        evaluation.selection,
        "",  # monto apostado: lo completa el usuario a mano
    ]
    _append(TAB_PRONOSTICOS, row)
