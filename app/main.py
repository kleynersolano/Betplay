import gc
import logging
import subprocess

from apscheduler.schedulers.blocking import BlockingScheduler

from app import google_search_provider
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
    """Google (busqueda normal, no Modo IA) es la UNICA fuente de datos: una
    sola busqueda por equipo que pide a la vez goles, tiros de esquina y
    tarjetas de sus ultimos 10 partidos, exigiendo al menos 3 fuentes
    distintas citadas para considerar el dato confiable. Si Google no da
    datos (CAPTCHA, bloqueo, menos de 3 fuentes), el equipo no se evalua;
    ya no se usan APIs como respaldo porque solo cubren goles."""
    try:
        return google_search_provider.get_team_form(
            team_name, venue=venue, is_national_team=is_national_team
        )
    except Exception:
        log.exception("Fallo consultando Google para %s", team_name)
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
