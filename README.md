# BetPlay Analysis Bot

Bot que entra a betplay.com.co/apuestas#starting-soon, filtra partidos de
futbol masculino de primera division que arrancan en las proximas N horas,
trae estadisticas de los ultimos 10 partidos por equipo (API-Football),
calcula value% (Poisson para goles/tarjetas, promedio historico para
corners) y envia las 3 mejores apuestas por Telegram.

## Estructura

```
Betplay/
  app/
    __init__.py
    config.py
    scraper_betplay.py
    stats_provider.py
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
2. 2. Obten tu chat_id (puedes hablarle al bot y usar https://api.telegram.org/bot<TOKEN>/getUpdates).
   3. 3. Crea una cuenta gratis en API-Football (https://www.api-football.com/) y copia tu API key.
      4. 4. Copia .env.example a .env y llena los valores.
        
         5. ## Despliegue en VPS Oracle (Ubuntu)
        
         6. ```bash
            sudo bash deploy/setup_vps.sh --confirm-wipe
            sudo nano /opt/betplay-bot/.env
            sudo systemctl start betbot
            sudo journalctl -u betbot -f
            ```

            ## Limitaciones conocidas

            - El scraper usa selectores genericos porque BetPlay no tiene API publica.
            -   Si el sitio cambia su HTML, hay que ajustar los selectores.
            -   - El plan gratuito de API-Football tiene 100 requests/dia.
                - - El mercado Handicap no esta modelado matematicamente.
