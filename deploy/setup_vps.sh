#!/usr/bin/env bash
# Despliega el bot de BetPlay en una VPS Ubuntu de Oracle Cloud (free tier)
# y elimina cualquier otro servicio/proyecto que estuviera corriendo, de
# forma que en el VPS solo quede este bot.
#
# ADVERTENCIA: este script es DESTRUCTIVO. Borra contenedores Docker,
# servicios systemd de usuario y el contenido de /opt y /home/*/apps antes
# de instalar el bot. Ejecutalo solo si realmente quieres dejar el VPS
# limpio con unicamente este proyecto.
#
# Uso:
#   sudo bash setup_vps.sh --confirm-wipe

set -euo pipefail

if [[ "${1:-}" != "--confirm-wipe" ]]; then
  echo "Este script borra todo lo demas en el VPS."
    echo "Vuelve a ejecutar con: sudo bash setup_vps.sh --confirm-wipe"
      exit 1
      fi

      echo ">>> Deteniendo y eliminando contenedores/servicios existentes..."
      if command -v docker >/dev/null 2>&1; then
        docker ps -aq | xargs -r docker rm -f
          docker system prune -af --volumes || true
          fi

          for svc in $(systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -vE '^(systemd|network|ssh|cron|cloud-init|oracle|ocid|rsyslog|snapd|dbus|user@|getty|serial-getty)'); do
            systemctl stop "$svc" 2>/dev/null || true
              systemctl disable "$svc" 2>/dev/null || true
              done

              echo ">>> Limpiando directorios de proyectos anteriores..."
              rm -rf /opt/* 2>/dev/null || true
              find /home -mindepth 2 -maxdepth 2 -type d -name "apps" -exec rm -rf {} + 2>/dev/null || true

              echo ">>> Instalando dependencias del sistema..."
              apt-get update -y
              apt-get install -y python3 python3-venv python3-pip git curl \
                libnss3 libatk-bridge2.0-0 libxkbcommon0 libgbm1 libasound2 fonts-liberation

                echo ">>> Creando usuario de servicio 'betbot'..."
                id -u betbot &>/dev/null || useradd -r -m -s /bin/bash betbot

                echo ">>> Copiando proyecto a /opt/betplay-bot..."
                mkdir -p /opt/betplay-bot
                cp -r "$(dirname "$0")/.." /opt/betplay-bot_src
                rsync -a --exclude venv /opt/betplay-bot_src/ /opt/betplay-bot/
                rm -rf /opt/betplay-bot_src

                cd /opt/betplay-bot

                echo ">>> Configurando entorno Python..."
                python3 -m venv venv
                ./venv/bin/pip install --upgrade pip
                ./venv/bin/pip install -r requirements.txt
                ./venv/bin/playwright install --with-deps chromium

                if [[ ! -f .env ]]; then
                  cp .env.example .env
                    echo "!! Edita /opt/betplay-bot/.env con tus credenciales reales antes de iniciar el servicio."
                    fi

                    chown -R betbot:betbot /opt/betplay-bot

                    echo ">>> Instalando servicio systemd..."
                    cp deploy/betbot.service /etc/systemd/system/betbot.service
                    systemctl daemon-reload
                    systemctl enable betbot

                    echo ""
                    echo "=== Listo ==="
                    echo "1. Edita las credenciales: sudo nano /opt/betplay-bot/.env"
                    echo "2. Inicia el bot:          sudo systemctl start betbot"
                    echo "3. Ver logs en vivo:       sudo journalctl -u betbot -f"
