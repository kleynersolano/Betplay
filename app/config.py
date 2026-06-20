import os
from dotenv import load_dotenv

load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")

# Segunda fuente gratuita de respaldo (https://www.football-data.org/, registro gratis).
# Solo aporta goles (su plan free no incluye corners/tarjetas).
FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")

# Respaldo: estadisticas via navegador automatizado contra el Modo IA de Google
# Search (la pestaña "Modo IA" debajo del buscador en google.com), no el chat
# de gemini.google.com. udm=50 abre Google Search directo en Modo IA.
GOOGLE_AI_URL = os.getenv("GOOGLE_AI_URL", "https://www.google.com/search?udm=50")
GOOGLE_AI_PROFILE_DIR = os.getenv("GOOGLE_AI_PROFILE_DIR", ".google_ai_profile")
GOOGLE_AI_HEADLESS = os.getenv("GOOGLE_AI_HEADLESS", "true").lower() == "true"

# Navegador visible para el scraper de BetPlay (util para correr localmente
# y ver el proceso en pantalla). En servidor sin entorno grafico debe ir en true.
BETPLAY_HEADLESS = os.getenv("BETPLAY_HEADLESS", "false").lower() == "true"

HOURS_AHEAD = int(os.getenv("HOURS_AHEAD", "4"))
RUN_INTERVAL_MINUTES = int(os.getenv("RUN_INTERVAL_MINUTES", "30"))
MIN_ODDS = float(os.getenv("MIN_ODDS", "2.0"))
MIN_VALUE_PERCENT = float(os.getenv("MIN_VALUE_PERCENT", "5.0"))
MIN_VALID_MATCHES = int(os.getenv("MIN_VALID_MATCHES", "6"))

# Margen tipico de la casa (overround) para un mercado de 2 vias en BetPlay.
# Cuando SI tenemos las dos cuotas (Mas y Menos de la misma linea) se calcula
# el overround real; cuando solo tenemos un lado se usa este valor para
# estimar y descontar el margen. 1.08 = 8% de margen, conservador.
DEFAULT_OVERROUND = float(os.getenv("DEFAULT_OVERROUND", "1.08"))

# Competiciones donde juegan selecciones nacionales (no clubes), usadas para
# pedirle a Google AI Mode estadisticas de "ultimos partidos oficiales" en
# vez de "ultimos partidos en su liga local" (las selecciones no tienen liga).
NATIONAL_TEAM_COMPETITION_KEYWORDS = [
    "mundial", "world cup", "eliminatoria", "copa america", "eurocopa", "euro",
]

VALID_COMPETITIONS_KEYWORDS = [
    "mundial", "world cup", "eliminatoria", "champions league", "europa league",
    "conference league", "premier league", "la liga", "laliga", "serie a",
    "bundesliga", "ligue 1", "eredivisie", "liga portugal", "primeira liga",
    "liga mx", "libertadores", "sudamericana", "copa america", "eurocopa",
    "euro", "categoria primera a", "primera division",
]

# Orden de prioridad pedido para presentar las mejores apuestas: primero
# tiros de esquina (total y por equipo), luego goles (total y por
# equipo), luego tarjetas (total y por equipo).
MARKET_PRIORITY = ["corners", "goals", "cards"]

EXCLUDED_KEYWORDS = [
    "femenino", "women", "(f)", "(w)", "sub-15", "sub-17", "sub-19", "sub-20",
    "sub-21", "sub 20", "reserve", "reservas", "amateur", "segunda division",
    "b division", "youth", "u15", "u17", "u19", "u20", "u21", "la liga 2",
    "rfef", "playoff", "promocion de ascenso", "primera rfef",
    "tercera division", "esports", "esport", "e-soccer", "e-football",
    "efootball", "fc 26", "cyber", "battle", "(e)", "virtual",
    "copa de la liga",
]
