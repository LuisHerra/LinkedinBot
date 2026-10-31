#!/usr/bin/env bash
# Instala el bot en una VM Ubuntu (Google Cloud e2-micro u otra). Ejecutar desde la carpeta del repo:
#   bash deploy/setup.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "==> Zona horaria Europe/Madrid (el temporizador y los logs van en hora española)"
sudo timedatectl set-timezone Europe/Madrid

echo "==> Swap de 2 GB (la e2-micro solo tiene 1 GB de RAM y Chromium necesita más)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Paquetes del sistema"
sudo apt-get update -q
sudo apt-get install -y -q python3-venv python3-pip xvfb x11vnc novnc websockify fonts-liberation

echo "==> Entorno de Python y Chromium"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

mkdir -p data "$HOME/linkedin-profile"

if [ ! -f .env ]; then
  sed "s#__HOME__#$HOME#g" deploy/env.example > .env
  chmod 600 .env
  echo "==> Creado $APP_DIR/.env: rellénalo con tus proxies (nano .env)"
fi

echo "==> Servicio y temporizador de systemd"
sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$USER#g" deploy/linkedin-bot.service \
  | sudo tee /etc/systemd/system/linkedin-bot.service >/dev/null
sudo cp deploy/linkedin-bot.timer /etc/systemd/system/linkedin-bot.timer
sudo systemctl daemon-reload

cat <<EOF

Instalación terminada. Siguientes pasos:
  1. nano .env                         (proxies)
  2. bash deploy/login.sh              (iniciar sesión en LinkedIn una vez, vía noVNC)
  3. xvfb-run -a .venv/bin/python -m bot run --force --quota 2 --dry-run
  4. sudo systemctl enable --now linkedin-bot.timer
EOF
