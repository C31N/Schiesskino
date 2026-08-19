#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
SERVICE_FILE="/etc/systemd/system/laser-arcade.service"
SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_HOME="$(getent passwd "${SERVICE_USER}" | cut -d: -f6)"
SERVICE_UID="$(id -u "${SERVICE_USER}")"
AUTOSTART_DIR="${SERVICE_HOME}/.config/autostart"
DESKTOP_FILE="${AUTOSTART_DIR}/laser-arcade.desktop"
UDEV_RULE="/etc/udev/rules.d/99-logitech-c922.rules"
SUDOERS_FILE="/etc/sudoers.d/laser-arcade"

sudo apt-get update
sudo apt-get install -y \
  python3-venv python3-opencv python3-pip \
  libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-ttf-2.0-0 libsdl2-mixer-2.0-0 \
  locales xdg-user-dirs libglib2.0-bin \
  x11-xserver-utils xdotool unclutter-xfixes v4l-utils

# Das dedizierte Spielsystem verwendet durchgehend deutsche Sprache und
# Tastatur. Die Befehle sind idempotent und wirken nach der nächsten Anmeldung.
sudo sed -i 's/^# *\(de_DE.UTF-8 UTF-8\)/\1/' /etc/locale.gen
sudo locale-gen de_DE.UTF-8
sudo update-locale LANG=de_DE.UTF-8 LANGUAGE=de_DE:de

sudo -u "${SERVICE_USER}" env HOME="${SERVICE_HOME}" \
  python3 -m venv "${VENV_DIR}"
sudo -u "${SERVICE_USER}" env HOME="${SERVICE_HOME}" \
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip
sudo -u "${SERVICE_USER}" env HOME="${SERVICE_HOME}" \
  "${VENV_DIR}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt"

sudo install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" \
  "${SERVICE_HOME}/.laser_arcade" "${SERVICE_HOME}/.laser_arcade/logs"

# UDEV-Regel für den Capture-Knoten der Logitech C922 (046d:085c).
# ATTR{index}=="0" verhindert, dass der Alias auf dem Metadaten-Knoten landet.
cat <<'EOF' | sudo tee "${UDEV_RULE}" >/dev/null
SUBSYSTEM=="video4linux", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="085c", ATTR{index}=="0", GROUP="video", MODE="0660", SYMLINK+="video-c922"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux
sudo usermod -aG video,audio,input "${SERVICE_USER}"

cat <<'EOF' | sudo tee "${SERVICE_FILE}" >/dev/null
[Unit]
Description=Laser Arcade Service
After=network.target graphical.target display-manager.service
Wants=graphical.target

[Service]
User=__SERVICE_USER__
Environment=PYTHONUNBUFFERED=1
Environment=HOME=__SERVICE_HOME__
Environment=XDG_RUNTIME_DIR=/run/user/__SERVICE_UID__
Environment=WAYLAND_DISPLAY=wayland-0
Environment=DISPLAY=:0
Environment=XAUTHORITY=__SERVICE_HOME__/.Xauthority
WorkingDirectory=__PROJECT_DIR__
ExecStartPre=/bin/sh -c 'until [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] || [ -S /tmp/.X11-unix/X0 ]; do sleep 1; done'
ExecStart=__PROJECT_DIR__/.venv/bin/python -m laser_arcade
Restart=on-failure
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=graphical.target
EOF

sudo sed -i "s|__SERVICE_USER__|${SERVICE_USER}|g" "${SERVICE_FILE}"
sudo sed -i "s|__SERVICE_HOME__|${SERVICE_HOME}|g" "${SERVICE_FILE}"
sudo sed -i "s|__SERVICE_UID__|${SERVICE_UID}|g" "${SERVICE_FILE}"
sudo sed -i "s|__PROJECT_DIR__|${PROJECT_DIR}|g" "${SERVICE_FILE}"

sudo systemctl daemon-reload
sudo systemctl enable "laser-arcade.service"

# Das Programm beendet den Dienst absichtlich ohne automatischen Neustart.
# Die Desktop-Verknüpfung darf ausschließlich diesen einen Dienst wieder
# starten; weitere Root-Befehle werden dadurch nicht freigegeben.
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl start laser-arcade.service\n' \
  "${SERVICE_USER}" | sudo tee "${SUDOERS_FILE}" >/dev/null
sudo chmod 0440 "${SUDOERS_FILE}"
sudo visudo -cf "${SUDOERS_FILE}" >/dev/null

# Deutsche Tastaturbelegung für Labwc/Wayland. Die normale Cursorform bleibt
# erhalten und wird in der Anwendung nur bei Inaktivität ausgeblendet.
LABWC_DIR="${SERVICE_HOME}/.config/labwc"
sudo install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${LABWC_DIR}"
sudo install -m 0644 -o "${SERVICE_USER}" -g "${SERVICE_USER}" \
  "${PROJECT_DIR}/systemd/labwc-environment" "${LABWC_DIR}/environment"

# Sichtbare Desktop-Verknüpfung zum Wiederstart nach „Programm beenden“.
# xdg-user-dir berücksichtigt sowohl „Desktop“ als auch „Schreibtisch“.
DESKTOP_DIR="$(sudo -u "${SERVICE_USER}" env HOME="${SERVICE_HOME}" \
  xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -z "${DESKTOP_DIR}" || "${DESKTOP_DIR}" == "${SERVICE_HOME}" ]]; then
  if [[ -d "${SERVICE_HOME}/Schreibtisch" ]]; then
    DESKTOP_DIR="${SERVICE_HOME}/Schreibtisch"
  else
    DESKTOP_DIR="${SERVICE_HOME}/Desktop"
  fi
fi
sudo install -d -m 0755 -o "${SERVICE_USER}" -g "${SERVICE_USER}" "${DESKTOP_DIR}"
DESKTOP_LAUNCHER="${DESKTOP_DIR}/Schiesskino-starten.desktop"
LEGACY_DESKTOP_LAUNCHER="${DESKTOP_DIR}/Schiesskino.desktop"
sed "s|/home/pi/Schiesskino|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/systemd/schiesskino.desktop" | \
  sudo tee "${DESKTOP_LAUNCHER}" >/dev/null
sudo chown "${SERVICE_USER}:${SERVICE_USER}" "${DESKTOP_LAUNCHER}"
sudo chmod 0755 "${DESKTOP_LAUNCHER}"
sudo chmod 0755 "${PROJECT_DIR}/scripts/start_laser_arcade.sh"
sudo -u "${SERVICE_USER}" env HOME="${SERVICE_HOME}" \
  gio set "${DESKTOP_LAUNCHER}" metadata::trusted true >/dev/null 2>&1 || true
# Dieser frühere Dateiname wurde von Raspberry Pi OS teilweise nur als
# „Schiesskino-desktop“ angezeigt. Er darf neben dem korrekt benannten Starter
# nicht als zweites Symbol liegen bleiben.
sudo rm -f "${LEGACY_DESKTOP_LAUNCHER}"

# Ein zweiter Desktop-Autostart würde parallel zum systemd-Dienst eine weitere
# Instanz öffnen. Entferne daher einen von älteren Installationen angelegten
# Fallback-Eintrag.
sudo rm -f "${DESKTOP_FILE}"

echo "Installation abgeschlossen. Bitte neu starten und Kalibrierung durchführen."
