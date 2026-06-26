"""
Registro de la actividad del bot en una Google Sheet, usando Playwright
(automatizacion del navegador) en vez de la API oficial: no requiere crear
un proyecto de Google Cloud, cuenta de servicio ni credenciales -- solo
que el navegador este logueado en la cuenta de Google que tiene acceso a
la hoja (el mismo perfil persistente que ya usa el bot para el Modo IA).

Se abre y cierra UNA sola vez por ciclo del bot, despues de que BetPlay y
el Modo IA ya cerraron sus propias sesiones de Playwright: el modulo sync
de Playwright no permite mas de una sesion activa a la vez en el mismo
hilo, asi que mantenerlo abierto durante todo el proceso (como se hacia
antes) hacia chocar al scraper de BetPlay con un error de asyncio.

Si algo falla (la hoja no carga, no hay sesion de Google, cambia el DOM),
se omite el registro en silencio: el bot sigue funcionando igual
(Telegram, consola) aunque Sheets no se pueda actualizar.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger("betbot.sheets_writer")

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1nXHOkxjj5T8yng68JWfVkMDdftCRP79RrEYNMln3tW8/edit"
)

TAB_DESCARTADOS = "Descartados"
TAB_ANALIZADOS = "Analizados"
TAB_PRONOSTICOS = "Pronosticos"

# "Fútbol" (con o sin el icono ⚽ que antepone el scraper) es el prefijo fijo
# de toda competicion extraida; se descarta para quedarnos solo con Pais/Liga.
_PREFIXES_TO_DROP = ("futbol", "fútbol", "⚽ futbol", "⚽ fútbol")

_FEMALE_KEYWORDS = ("femenino", "femenina", "women", "(f)", "(w)")


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


class SheetsWriter:
    """Usar como context manager (with SheetsWriter() as sheet: ...) para
    garantizar que el navegador siempre se cierra, incluso si algo falla."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self.page: Optional[Page] = None

    def __enter__(self) -> "SheetsWriter":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> bool:
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False)
            self.page = self._browser.new_page()
            self.page.goto(SHEET_URL, timeout=60000)
            self.page.wait_for_load_state("load")
            time.sleep(2)
            log.info("Google Sheets abierto (Playwright)")
            return True
        except Exception:
            log.exception("No se pudo abrir Google Sheets, se omite el registro de este ciclo")
            self.close()
            return False

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None
        self.page = None

    def _click_tab(self, tab_name: str) -> bool:
        try:
            self.page.click(f"text={tab_name}", timeout=5000)
            time.sleep(0.8)
            return True
        except Exception:
            log.debug("No se encontro la pestana %s", tab_name)
            return False

    def _append_row(self, tab_name: str, row: list) -> bool:
        if not self.page:
            return False
        try:
            if not self._click_tab(tab_name):
                return False
            # Insertar una fila nueva justo debajo del encabezado (siempre
            # la fila visual 2, data-row-index 1) y llenarla -- robusto sin
            # importar cuantas filas ya tenga la pestana.
            self.page.click('[data-row-index="1"][data-column-index="0"]', timeout=5000)
            time.sleep(0.3)
            self.page.click('[data-row-index="1"]', button="right", timeout=5000)
            time.sleep(0.8)
            try:
                self.page.click("text=Insertar 1 encima", timeout=3000)
            except Exception:
                self.page.click("text=Insert 1 above", timeout=3000)
            time.sleep(0.8)
            self.page.click('[data-row-index="1"][data-column-index="0"]', timeout=5000)
            time.sleep(0.3)
            for i, value in enumerate(row):
                self.page.keyboard.type(str(value), delay=8)
                if i < len(row) - 1:
                    self.page.keyboard.press("Tab")
            self.page.keyboard.press("Enter")
            time.sleep(0.5)
            return True
        except Exception:
            log.exception("No se pudo agregar la fila a la pestana %s", tab_name)
            return False

    def log_discarded(self, match) -> None:
        self._append_row(TAB_DESCARTADOS, _match_row(match))

    def log_analizado(self, match) -> None:
        self._append_row(TAB_ANALIZADOS, _match_row(match))

    def log_pronostico(self, evaluation) -> None:
        row = _match_row(evaluation.match) + [
            f"{evaluation.value_percent:.1f}%",
            f"{evaluation.prob_real * 100:.1f}%",
            f"{evaluation.odds:.2f}",
            evaluation.market,
            evaluation.selection,
            "",  # monto apostado: lo completa el usuario a mano
        ]
        self._append_row(TAB_PRONOSTICOS, row)
