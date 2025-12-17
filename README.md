# Laser Arcade (Schiesskino)

Interaktives Beamer+Kamera-System für Raspberry Pi OS (64-bit). Die Anwendung erkennt einen roten Laserpunkt (Logitech C922 Webcam) oder Maus-Eingaben und steuert damit eine Vollbild-Launcher-Oberfläche plus fünf integrierte Spiele.

## Features
- Vollbild-Launcher auf 1024x768 (4:3, 100" Zielsetup) mit großen Buttons.
- Kamera-Laser-Tracking (HSV in zwei Rot-Bändern, Morphology, Flächenfilter, EMA-Smoothing).
- Dwell-Click (Standard 300 ms, Radius 10 px, Debounce).
- 5-Punkt-Kalibrierung (4 Ecken + Center) via `cv2.findHomography` + `cv2.perspectiveTransform`, Speicherung in `~/.laser_arcade/calibration.json`.
- Testmodus mit Overlay und gemappten Koordinaten.
- Fallback auf Maus-Modus, falls Kamera nicht verfügbar ist.
- 5 Spiele/Apps als Plugins:
  - Dosenschießen
  - Moorhuhn-ähnlich
  - Zielscheibe
  - Reaktionsspiel
  - Malen
- Logging nach `~/.laser_arcade/logs/`.
- Debug-Overlay (FPS, Fläche, Confidence, Masken-Vorschau) zuschaltbar.
- Best-Effort Auflösungs-Set (1024x768@60) via `xrandr` beim Start.

## Projektstruktur (Auszug)
- `laser_arcade/__main__.py` – Einstiegspunkt, Display-Setup, App-Routing, Debug-Overlay.
- `laser_arcade/calibration.py|calibration_ui.py` – 5-Punkt-Kalibrierung, Homographie und UI.
- `laser_arcade/laser_tracker.py` – HSV-Tracking (2 Rot-Bänder), Morphology, EMA, Masken-Vorschau.
- `laser_arcade/pointer.py` – Vereinheitlichte Pointer-Events + Dwell-Click.
- `laser_arcade/apps/` – Spiele (Dosenschießen, Moorhuhn, Zielscheibe, Reaktion, Malen).
- `install.sh` – Installer inkl. apt, venv, pip, UDEV-Regel, systemd-Service, Autostart.
- `systemd/laser-arcade.service` – Beispiel-Unit-Datei für Anpassungen.

## Installation
### Voraussetzungen
- Raspberry Pi OS Bookworm 64-bit, Python 3.11+
- Beamer auf 1024x768 @ 60 Hz (Epson EMP-822 via aktiver Micro-HDMI→VGA-Adapter)
- Logitech C922 am USB-Port (v4l2)

### Schnelleinrichtung
```bash
./install.sh
```
Was das Skript tut:
- Installiert Systemabhängigkeiten (`python3-venv`, `python3-opencv`, SDL2, `v4l-utils`, `x11-xserver-utils`).
- Legt ein venv `.venv` an und installiert `requirements.txt` (numpy, opencv-python-headless, pygame-ce).
- Erstellt `~/.laser_arcade/` inkl. `logs/`.
- Legt eine optionale UDEV-Regel für die Logitech C922 (046d:085c) an (`/dev/video-c922` Alias, Mode 0666).
- Schreibt/aktiviert `laser-arcade.service` (WorkingDirectory = Repo-Pfad, ExecStart = venv Python).
- Erstellt Desktop-Autostart-Fallback (`~/.config/autostart/laser-arcade.desktop`).
- Hinweis auf Reboot, damit systemd/udev aktiv werden.

**Nach der Installation neu booten.**

## Start/Stop Service
```bash
sudo systemctl enable laser-arcade.service
sudo systemctl start laser-arcade.service
sudo systemctl status laser-arcade.service
sudo systemctl stop laser-arcade.service
```

## Manuell starten (Debug)
```bash
source .venv/bin/activate
python -m laser_arcade
```

## Kalibrierung (5-Punkt)
1. Stelle sicher, dass der Beamer auf 1024x768@60 Hz läuft (xrandr oder KMS/WL).
2. Starte die Anwendung (Service oder manuell).
3. Wähle im Launcher „Kalibrierung“.
4. Zielen: 4 Ecken + Mitte – der Laserpunkt sollte einige Millisekunden ruhig auf dem Marker bleiben (Dwell-Click löst den Punkt).
5. Nach allen 5 Punkten wird die Homographie berechnet und in `~/.laser_arcade/calibration.json` gespeichert.
6. Optional: „Testmodus“ öffnen, Kreuz + Laser/Mapped-Position prüfen. Bei Drift erneut kalibrieren („Re-Align“ durch erneute Kalibrierung).
7. Debug-Overlay bei Bedarf in `settings.json` (`"debug_overlay": true`) aktivieren.

## Bedienung
- Laser oder Maus steuert einen gemeinsamen Pointer. Maus funktioniert immer; Laser-Fallback auf Maus, wenn Kamera fehlt.
- Dwell-Click löst Klick-Events aus; Maus-Klicks funktionieren wie gewohnt.
- ESC wechselt jederzeit zurück in den Launcher.

## Spiele/Apps
- **Dosenschießen**: Triff alle Dosen, Score + Neustart.
- **Moorhuhn**: Fliegende Ziele, Punkte, 60 s Runden.
- **Zielscheibe**: Ringe mit unterschiedlichen Punkten.
- **Reaktion**: Wartet, dann Ziel anklicken; Bestzeit wird gemessen.
- **Malen**: Linien zeichnen, Klick wechselt Farbe.

## Einstellungen/Defaults (für 100" 4:3)
- Auflösung UI: 1024x768.
- Kamera: 640x480 @ 30 fps (kann in `settings.json` angepasst werden).
- Laser-Profil: Zwei Rot-Hue-Bereiche, Morph-Kernel 3, Flächenfilter 12–4000 px².
- EMA α = 0.35, Dwell 300 ms, Radius 10 px.
- Logs liegen unter `~/.laser_arcade/logs/laser_arcade.log`.

## Troubleshooting
- **Kein Kamerabild**: `v4l2-ctl --list-devices`, Exposure/Auto-Exposure prüfen, USB-Kabel tauschen.
- **Laser nicht erkannt**: HSV-Grenzen in `~/.laser_arcade/settings.json` anpassen. Eventuell Rotfilter (LEE 106) einsetzen.
- **Auflösung**: `xrandr --output HDMI-1 --mode 1024x768 --rate 60`. Bei Wayland/KMS: gleiches Ziel-Mode setzen.
- **Performance**: Kamera-Auflösung reduzieren, Debug-Overlay aus lassen.
- **Autostart**: Systemd-Service prüfen (`journalctl -u laser-arcade`), ansonsten Desktop-Autostart verwenden.
- **Debug-Overlay**: Zeigt FPS, Fläche, Confidence und Masken-Vorschau. Wenn leer: HSV-Toleranzen erhöhen oder Belichtung manuell setzen.

## App-Entwicklung (Plugins)
- Neue Apps im Ordner `laser_arcade/apps/` anlegen, Klasse von `BaseApp` ableiten, `name` setzen, `handle_pointer/update/draw` implementieren.
- App in `laser_arcade/launcher.py` in `APP_CLASSES` registrieren.

## Erststart-Kurzanleitung
1. `./install.sh` ausführen.
2. Reboot.
3. Kalibrierung (5 Punkte) durchführen.
4. Im Launcher ein Spiel auswählen.
