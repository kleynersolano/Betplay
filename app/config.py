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

# Cache de estadisticas por equipo. El Modo IA es un LLM y NO es
# deterministico: la misma pregunta puede dar cifras distintas entre ciclos
# (se vio Uruguay goles 1.40 vs 0.90 con 10 min de diferencia), lo que hacia
# cambiar las apuestas al azar. Para fijar el dato, la primera consulta de un
# equipo se guarda en este archivo y se REUSA durante GOOGLE_AI_CACHE_HOURS
# horas en vez de volver a preguntar. Asi las estadisticas (y por tanto los
# pronosticos) son estables. Pasado el TTL se vuelve a consultar (los ultimos
# 10 partidos cambian con el tiempo, asi que no se cachea para siempre).
GOOGLE_AI_CACHE_FILE = os.getenv("GOOGLE_AI_CACHE_FILE", ".google_ai_cache.json")
GOOGLE_AI_CACHE_HOURS = float(os.getenv("GOOGLE_AI_CACHE_HOURS", "12"))

# Navegador visible para el scraper de BetPlay (util para correr localmente
# y ver el proceso en pantalla). En servidor sin entorno grafico debe ir en true.
BETPLAY_HEADLESS = os.getenv("BETPLAY_HEADLESS", "false").lower() == "true"

HOURS_AHEAD = int(os.getenv("HOURS_AHEAD", "4"))
RUN_INTERVAL_MINUTES = int(os.getenv("RUN_INTERVAL_MINUTES", "30"))
MIN_ODDS = float(os.getenv("MIN_ODDS", "2.0"))
MIN_VALUE_PERCENT = float(os.getenv("MIN_VALUE_PERCENT", "5.0"))
# Techo de sanidad: un edge real (incluso en libros blandos) casi nunca
# supera ~25-30%. Un value% por encima de esto no es una oportunidad, es
# casi siempre lambda mal estimada (datos de Google AI poco confiables) o
# una muestra insuficiente. Un apostador profesional descarta estos casos
# en vez de apostarles con "confianza maxima".
MAX_VALUE_PERCENT = float(os.getenv("MAX_VALUE_PERCENT", "30.0"))
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

# Lista BLANCA: solo se aceptan estas competiciones (ver
# Match.is_valid_competition en scraper_betplay.py). Incluye los torneos
# de seleccion/copas continentales pedidos y la PRIMERA division (top
# flight) de los paises futboleros principales. "serie a" cubre tanto
# Italia como Brasil (cuyo nombre normalizado, sin tildes, tambien
# contiene "serie a" dentro de "campeonato brasileiro serie a"); se
# agregan ademas "brasileirao"/"campeonato brasileiro" por si BetPlay usa
# ese nombre sin "serie a".
VALID_COMPETITIONS_KEYWORDS = [
    "mundial", "world cup", "eliminatoria", "champions league", "europa league",
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "eredivisie", "liga portugal", "primeira liga", "liga mx", "libertadores",
    "sudamericana", "copa america", "eurocopa", "euro", "categoria primera a",
    "primera division", "liga profesional", "brasileirao", "campeonato brasileiro",
    "mls",
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
    # Ligas/divisiones inferiores y de desarrollo que se cuelan en la
    # lista blanca por compartir palabras con la primera division
    # ("serie a/b", "primera a/b", etc.) y deben descartarse explicitamente.
    "serie b", "serie c", "serie d", "primera b", "segunda b", "expansion mx",
    "liga de desarrollo", "liga regional", "torneo federal", "federal a",
    "next pro", "catarinense", "paulista", "carioca", "mineiro", "gaucho",
    "copa do brasil", "challenger", "usl",
]
