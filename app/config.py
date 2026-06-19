import os
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
      value = os.getenv(name)
      if not value:
                raise RuntimeError(f"Falta variable de entorno requerida: {name}")
            return value


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _required("TELEGRAM_CHAT_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
HOURS_AHEAD = int(os.getenv("HOURS_AHEAD", "4"))
RUN_INTERVAL_MINUTES = int(os.getenv("RUN_INTERVAL_MINUTES", "30"))
MIN_ODDS = float(os.getenv("MIN_ODDS", "2.0"))
MIN_VALUE_PERCENT = float(os.getenv("MIN_VALUE_PERCENT", "3.5"))
MIN_VALID_MATCHES = int(os.getenv("MIN_VALID_MATCHES", "6"))

VALID_COMPETITIONS_KEYWORDS = [
      "mundial", "world cup", "eliminatoria", "champions league", "europa league",
      "conference league", "premier league", "la liga", "laliga", "serie a",
      "bundesliga", "ligue 1", "eredivisie", "liga portugal", "primeira liga",
      "liga mx", "libertadores", "sudamericana", "copa america", "eurocopa",
      "euro", "categoria primera a", "primera division",
]

EXCLUDED_KEYWORDS = [
      "femenino", "women", "sub-15", "sub-17", "sub-19", "sub-20", "sub-21",
      "reserve", "reservas", "amateur", "segunda division", "b division",
      "youth", "u15", "u17", "u19", "u20", "u21",
]
