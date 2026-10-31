#!/usr/bin/env bash
# Abre el navegador del bot en un escritorio virtual visible por noVNC para iniciar sesión en LinkedIn una vez.
# noVNC solo escucha en localhost de la VM: se accede desde tu PC con un túnel SSH (ver deploy/GCP.md).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
set -a; source .env; set +a

if systemctl is-active --quiet linkedin-bot.service; then
  echo "Hay una tanda en curso; espera a que termine (systemctl status linkedin-bot)." >&2
  exit 1
fi

export DISPLAY=:99
Xvfb :99 -screen 0 1440x900x24 >/dev/null 2>&1 & XVFB=$!
sleep 1
x11vnc -display :99 -localhost -nopw -forever -quiet >/dev/null 2>&1 & VNC=$!
websockify --web /usr/share/novnc 127.0.0.1:6080 localhost:5900 >/dev/null 2>&1 & WS=$!
trap 'kill $WS $VNC $XVFB 2>/dev/null || true' EXIT
sleep 1

echo "Abre en tu PC:  http://localhost:6080/vnc.html  (pulsa Connect) e inicia sesión en LinkedIn."
.venv/bin/python -m bot login
