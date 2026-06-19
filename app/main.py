import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app import football_data_provider, google_ai_provider
from app.analysis import evaluate_match
from app.config import RUN_INTERVAL_MINUTES
from app.scraper_betplay import fetch_upcoming_matches
from app.stats_provider import TeamForm, get_team_form
from app.telegram_notifier import notify_match_results, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("betbot")

# Orden de fuentes de estadisticas: API-Football (principal) -> football-data.org
# (segunda fuente gratuita) -> Google AI (hasta 5 fuentes via busqueda, sin entrar
# a las paginas). Se detiene en la primera fuente que devuelva datos validos.
STAT_PROVIDERS = [get_team_form, football_data_provider.get_team_form, google_ai_provider.get_team_form]


def _get_team_form_with_fallback(team_name: str, venue: str) -> TeamForm | None:
    best: TeamForm | None = None
    for provider in STAT_PROVIDERS:
        try:
            form = provider(team_name, venue=venue)
        except Exception:
            log.exception("Fallo consultando %s para %s", provider.__module__, team_name)
            continue
        if form is not None and form.valid:
            return form
        best = best or form
    return best


def run_cycle() -> None:
    log.info("Iniciando ciclo de analisis...")
    try:
        matches = fetch_upcoming_matches()
    except Exception:
        log.exception("Fallo al scrapear BetPlay")
        return
    log.info("Partidos validos encontrados: %d", len(matches))
    for match in matches:
        try:
            home_form = _get_team_form_with_fallback(match.home_team, venue="home")
            away_form = _get_team_form_with_fallback(match.away_team, venue="away")
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
