# BetPlay Analysis Bot

Bot que entra a betplay.com.co/apuestas#starting-soon, filtra partidos de
futbol masculino de primera division que arrancan en las proximas N horas,
trae estadisticas de los ultimos 10 partidos por equipo, calcula value%
(Poisson para goles/tarjetas, promedio historico para corners) y envia las
3 mejores apuestas por Telegram.

## Estructura

```
Betplay/
  app/
    __init__.py
    config.py
    scraper_betplay.py
    stats_provider.py
    football_data_provider.py
    google_ai_provider.py
    analysis.py
    telegram_notifier.py
    main.py
  deploy/
    betbot.service
    setup_vps.sh
  requirements.txt
  .env.example
  README.md
```

## Configuracion

1. Crea un bot de Telegram con @BotFather y copia el token.
2. Obten tu chat_id (puedes hablarle al bot y usar https://api.telegram.org/bot<TOKEN>/getUpdates).
3. Crea una cuenta gratis en API-Football (https://www.api-football.com/) y copia tu API key.
4. (Opcional) Crea una cuenta gratis en https://www.football-data.org/ y copia tu API key.
5. Copia .env.example a .env y llena los valores.

## Despliegue en VPS Oracle (Ubuntu)

```bash
sudo bash deploy/setup_vps.sh --confirm-wipe
sudo nano /opt/betplay-bot/.env
sudo systemctl start betbot
sudo journalctl -u betbot -f
```

## Cadena de fuentes de estadisticas

El bot intenta las fuentes en este orden, pasando a la siguiente solo si la
anterior no tiene datos suficientes (menos de `MIN_VALID_MATCHES` partidos
validos):

1. **API-Football** (principal, 100 req/dia gratis).
2. **football-data.org** (segunda fuente gratuita; su plan free solo aporta
   goles, no corners/tarjetas).
3. **Google AI (Modo IA de Google Search)** (ultimo respaldo, ver abajo).

## Fuente de respaldo: Modo IA de Google Search

Cuando ninguna de las dos APIs anteriores devuelve datos suficientes, el bot
intenta obtener las estadisticas automatizando un navegador contra el "Modo
IA" de Google Search (`google.com/search?udm=50`, la pestana que aparece
debajo del buscador, no el chat de `gemini.google.com`), igual que hace con
BetPlay, pidiendole que consulte hasta 5 fuentes confiables (Sofascore,
Flashscore, WhoScored, FootyStats, FBref) sin que el bot entre directamente
a esas paginas.

**Aviso importante:** esto automatiza la interfaz web de consumidor de
Google (no su API), lo cual va contra sus Terminos de Servicio y puede
resultar en el bloqueo/suspension de la cuenta de Google usada. Se eligio
este enfoque a pedido explicito en vez de la API oficial de Gemini (que si
permite automatizacion sin ese riesgo).

Para activarlo:

1. La primera vez, corre el bot con `GOOGLE_AI_HEADLESS=false` para que se
   abra una ventana de navegador.
2. Inicia sesion manualmente con la cuenta de Google que quieras usar.
3. La sesion queda guardada en la carpeta `GOOGLE_AI_PROFILE_DIR`
   (`.google_ai_profile` por defecto). En corridas siguientes puedes volver
   a `GOOGLE_AI_HEADLESS=true`.

## Limitaciones conocidas

- El scraper usa selectores genericos porque BetPlay no tiene API publica.
- Si el sitio cambia su HTML, hay que ajustar los selectores (lo mismo aplica
  a los selectores del Modo IA si Google cambia su interfaz).
- El plan gratuito de API-Football tiene 100 requests/dia.
- football-data.org (segunda fuente) no aporta corners/tarjetas en su plan
  gratuito, solo goles.
- El respaldo de Google AI depende de una sesion logueada y tiene riesgo de
  bloqueo de cuenta (ver seccion anterior).
- El mercado Handicap no esta modelado matematicamente.
