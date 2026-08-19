from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time

import pygame

from .config import Settings, load_settings
from .diagnostic_ui import LaserDiagnosticUI
from .laser_tracker import LaserTracker
from .logging_utils import setup_logging


LOGGER = logging.getLogger(__name__)
UI_FPS = 30


def init_display(settings: Settings) -> pygame.Surface:
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    screen = pygame.display.set_mode(
        (settings.screen_width, settings.screen_height), pygame.FULLSCREEN
    )
    pygame.display.set_caption("Laser-Erkennung")
    pygame.mouse.set_visible(True)
    LOGGER.info("SDL-Anzeigetreiber: %s", pygame.display.get_driver())
    return screen


def start_system_cursor_hider() -> subprocess.Popen | None:
    """Blendet den Xwayland-Zeiger auch ohne angeschlossene Maus aus."""

    if pygame.display.get_driver() != "x11":
        return None
    executable = shutil.which("unclutter")
    if executable is None:
        LOGGER.warning("Systemweite Zeigerausblendung fehlt: unclutter ist nicht installiert")
        return None
    try:
        process = subprocess.Popen(
            [
                executable,
                "--timeout",
                "1",
                "--jitter",
                "3",
                "--ignore-scrolling",
                "--start-hidden",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        LOGGER.info("Systemweite Zeigerausblendung gestartet (start-hidden)")
        return process
    except OSError as exc:
        LOGGER.warning("Systemweite Zeigerausblendung konnte nicht starten: %s", exc)
        return None


def restore_desktop_cursor() -> None:
    """Stellt nach dem Vollbildprogramm den normalen Desktop-Zeiger wieder her."""

    xsetroot = shutil.which("xsetroot")
    if xsetroot is not None:
        try:
            subprocess.run(
                [xsetroot, "-cursor_name", "left_ptr"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("Desktop-Mauszeiger konnte nicht gesetzt werden: %s", exc)

    # Eine minimale Bewegung veranlasst Xwayland/Labwc, die wiederhergestellte
    # Cursorform sofort neu zu zeichnen. Ohne xdotool bleibt xsetroot wirksam;
    # die nächste echte Mausbewegung übernimmt dann die Aktualisierung.
    xdotool = shutil.which("xdotool")
    if xdotool is not None:
        try:
            subprocess.run(
                [xdotool, "mousemove_relative", "--", "1", "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOGGER.warning("Desktop-Zeigeraktualisierung fehlgeschlagen: %s", exc)
    LOGGER.info("Desktop-Mauszeiger wiederhergestellt")


def try_set_resolution(settings: Settings) -> None:
    """Best-effort-Anpassung der XWayland-Ausgabe auf die Zielauflösung."""

    try:
        result = subprocess.run(
            ["xrandr", "--current"], capture_output=True, text=True, check=True, timeout=4
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Auflösungsprüfung übersprungen: %s", exc)
        return

    for line in result.stdout.splitlines():
        if " connected" not in line:
            continue
        output = line.split()[0]
        command = [
            "xrandr",
            "--output",
            output,
            "--mode",
            f"{settings.screen_width}x{settings.screen_height}",
            "--rate",
            "60",
        ]
        applied = subprocess.run(command, capture_output=True, text=True, timeout=4)
        if applied.returncode == 0:
            LOGGER.info("Auflösung über xrandr für %s gesetzt.", output)
            return
        LOGGER.warning("Auflösung für %s nicht gesetzt: %s", output, applied.stderr.strip())


def main() -> None:
    setup_logging()
    settings = load_settings()
    pygame.init()
    pygame.font.init()
    try_set_resolution(settings)
    screen = init_display(settings)
    cursor_hider = start_system_cursor_hider()
    clock = pygame.time.Clock()

    tracker = LaserTracker(settings)
    tracker.start()
    ui = LaserDiagnosticUI(screen, settings, tracker)
    running = True

    try:
        while running:
            for event in pygame.event.get():
                running = ui.handle_event(event) and running
            if not running or ui.close_requested:
                break

            try:
                detection = tracker.read()
            except Exception as exc:
                LOGGER.exception("Kamerabild konnte nicht gelesen werden: %s", exc)
                raise

            now = time.monotonic()
            ui.update(detection, now)
            if ui.close_requested:
                break
            ui.draw(clock.get_fps())
            pygame.display.flip()
            clock.tick(UI_FPS)
    finally:
        tracker.stop()
        if cursor_hider is not None and cursor_hider.poll() is None:
            cursor_hider.terminate()
            try:
                cursor_hider.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                cursor_hider.kill()
        ui.close()
        pygame.quit()
        restore_desktop_cursor()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        LOGGER.exception("Fehler im Hauptprogramm: %s", exc)
        sys.exit(1)
