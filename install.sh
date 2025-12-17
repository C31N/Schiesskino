#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
SERVICE_FILE="/etc/systemd/system/laser-arcade.service"
USER_SERVICE_FILE="${HOME}/.config/systemd/user/laser-arcade.service"
AUTOSTART_DIR="${HOME}/.config/autostart"
DESKTOP_FILE="${AUTOSTART_DIR}/laser-arcade.desktop"
SERVICE_USER="${SUDO_USER:-$USER}"

sudo apt-get update
sudo apt-get install -y python3-venv python3-opencv python3-pip libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 libsdl2-mixer-2.0-0 x11-xserver-utils v4l-utils

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip
pip install -r "${PROJECT_DIR}/requirements.txt"

mkdir -p "${HOME}/.laser_arcade/logs"

cat <<'EOF' | sudo tee "${SERVICE_FILE}" >/dev/null
[Unit]
Description=Laser Arcade Service
After=network.target graphical.target

[Service]
User=__SERVICE_USER__
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=%h/Schiesskino
ExecStart=%h/Schiesskino/.venv/bin/python -m laser_arcade
Restart=on-failure

[Install]
WantedBy=graphical.target
EOF

sudo sed -i "s|__SERVICE_USER__|${SERVICE_USER}|g" "${SERVICE_FILE}"

sudo systemctl daemon-reload
sudo systemctl enable "laser-arcade.service"

# Desktop autostart fallback
mkdir -p "${AUTOSTART_DIR}"
cat <<'EOF' > "${DESKTOP_FILE}"
[Desktop Entry]
Type=Application
Exec=${HOME}/Schiesskino/.venv/bin/python -m laser_arcade
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=Laser Arcade
Comment=Start Laser Arcade bei Login
EOF

echo "Installation abgeschlossen. Bitte neu starten und Kalibrierung durchführen."
