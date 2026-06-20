import gc
import logging
import subprocess

from apscheduler.schedulers.blocking import BlockingScheduler

from app import football_data_provider, free_stats_provider, google_search_provider
from app.analysis import evaluate_match
from app.config import RUN_INTERVAL_MINUTES
from app.scraper_betplay import fetch_upcoming_matches
from app.stats_provider import TeamForm
from app.telegram_notifier import notify_match_results, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("betbot")


def _get_team_form_with_fallback(
    team_name: str, venue: str, is_national_team: bool = False
) -> TeamForm | None:
    """Estrategia de datos: rapida, real y confiable primero, con respaldos.

    1) free_stats_provider: 3 fuentes gratis SIN registro (Sofascore, FotMob,
       TheSportsDB) por API JSON directa, promediadas. Dan goles, tiros de
       esquina y tarjetas reales por partido. Es la fuente principal: rapida
       (sin navegador) y confiable (datos oficiales, no estimaciones).
    2) football-data.org (API, requiere key ya configurada): respaldo de
       GOLES si las fuentes gratis no cubrieron la competicion.
    3) Busqueda en Google (navegador, lento): ultimo recurso para ligas
       exoticas que ninguna API cubra.

    En todos los casos, si football-data tiene goles se usan ESOS para el
    mercado de goles (datos oficiales) por encima del resto."""
    fd_form: TeamForm | None = None
    try:
        fd_form = football_data_provider.get_team_form(
            team_name, venue=venue, is_national_team=is_national_team
        )
    except Exception:
        log.exception("Fallo consultando football-data para %s", team_name)

    fd_goals = fd_form.average("goals") if fd_form else None
    fd_goals_against = fd_form.average("goals_against") if fd_form else None

    def _anchor_goals(form: TeamForm) -> TeamForm:
        # Si football-data dio goles oficiales, se anclan por encima de la
        # estimacion de otras fuentes (mas confiable para el mercado clave).
        if fd_goals is not None:
            form.overrides["goals"] = fd_goals
            if fd_goals_against is not None:
                form.overrides["goals_against"] = fd_goals_against
        return form

    # 1) Fuentes gratis sin registro (principal).
    try:
        free_form = free_stats_provider.get_team_form(
            team_name, venue=venue, is_national_team=is_national_team
        )
    except Exception:
        log.exception("Fallo consultando fuentes gratis para %s", team_name)
        free_form = None
    if free_form is not None and free_form.valid:
        log.info("    %s: fuentes gratis (Sofascore/FotMob/TheSportsDB)", team_name)
        return _anchor_goals(free_form)

    # 2) Google como ultimo recurso (navegador).
    try:
        g_form = google_search_provider.get_team_form(
            team_name, venue=venue, is_national_team=is_national_team
        )
    except Exception:
        log.exception("Fallo consultando Google para %s", team_name)
        g_form = None
    if g_form is not None:
        log.info("    %s: Google (respaldo)", team_name)
        return _anchor_goals(g_form)

    # 3) Solo football-data (solo serviran mercados de goles).
    if fd_form is not None and fd_form.valid:
        log.info("    %s: solo football-data", team_name)
        return fd_form
    return None


def _cleanup_before_cycle() -> None:
    """Tras varios ciclos seguidos lanzando y cerrando Chromium (BetPlay y
    Google AI Mode), procesos huerfanos que quedaron vivos por un cierre
    fallido (crash, Ctrl+C a mitad de operacion, etc.) se iban acumulando y
    terminaban consumiendo toda la RAM, dejando el equipo lento al punto de
    que ni BetPlay terminaba de cargar las cuotas. Antes de cada ciclo se
    matan procesos de Chromium que hayan quedado colgados (no deberia haber
    ninguno vivo entre ciclos, ya que cada scraper cierra su navegador al
    terminar) y se fuerza una recoleccion de basura de Python."""
    try:
        subprocess.run(
            ["pkill", "-9", "-f", "chrome-linux/headless_shell|chromium.*--remote-debugging"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    gc.collect()


def run_cycle() -> None:
    _cleanup_before_cycle()
    log.info("Iniciando ciclo de analisis...")
    try:
        matches = fetch_upcoming_matches()
    except Exception:
        log.exception("Fallo al scrapear BetPlay")
        return
    log.info("Partidos validos encontrados: %d", len(matches))
    for match in matches:
        if not match.lines:
            log.warning(
                "Sin cuotas extraidas para %s vs %s, se omite (no se consulta IA)",
                match.home_team, match.away_team,
            )
            continue
        try:
            home_form = _get_team_form_with_fallback(
                match.home_team, venue="home", is_national_team=match.is_national_team_match
            )
            away_form = _get_team_form_with_fallback(
                match.away_team, venue="away", is_national_team=match.is_national_team_match
            )
            evaluations = evaluate_match(match, home_form, away_form)
            if evaluations:
                notify_match_results(evaluations)
                log.info("Enviado a Telegram: %s vs %s", match.home_team, match.away_team)
            else:
                log.info("Sin value: %s vs %s", match.home_team, match.away_team)
        except Exception:
            log.exception("Error procesando %s vs %s", match.home_team, match.away_team)
    log.info("Ciclo terminado.")


def main() -> None:
    send_message("Bot de analisis BetPlay iniciado correctamente.")
    run_cycle()
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_cycle, "interval", minutes=RUN_INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
