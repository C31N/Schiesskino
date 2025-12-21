from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

import pygame

from . import launcher as launcher_module
from .calibration import apply_homography, build_calib_points, load_homography
from .calibration_ui import CalibrationUI
from .config import Settings, load_settings
from .constants import FPS
from .laser_tracker import LaserDetection
from .laser_tracker import LaserTracker
from .logging_utils import setup_logging
from .pointer import PointerRouter
from .test_mode import TestMode


LOGGER = logging.getLogger(__name__)


def init_display(settings: Settings) -> pygame.Surface:
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    flags = pygame.FULLSCREEN
    screen = pygame.display.set_mode((settings.screen_width, settings.screen_height), flags)
    pygame.display.set_caption("Laser Arcade")
    return screen


def try_set_resolution(settings: Settings) -> None:
    """Best-effort Anpassung auf 1024x768@60 via xrandr, falls verfügbar."""
    import subprocess

    try:
        result = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        LOGGER.info("xrandr nicht verfügbar – überspringe Auflösungs-Check")
        return
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("xrandr-Aufruf fehlgeschlagen: %s", exc)
        return

    outputs = []
    for line in result.stdout.splitlines():
        if " connected" in line:
            outputs.append(line.split()[0])
    if not outputs:
        LOGGER.info("Keine verbundenen Displays von xrandr gemeldet")
        return

    for output in outputs:
        cmd = [
            "xrandr",
            "--output",
            output,
            "--mode",
            f"{settings.screen_width}x{settings.screen_height}",
            "--rate",
            "60",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            LOGGER.info("Auflösung über xrandr für %s gesetzt.", output)
            return
        LOGGER.warning("Konnte Auflösung für %s nicht setzen: %s", output, res.stderr.strip())


def render_debug_overlay(
    screen: pygame.Surface,
    detection: Optional[LaserDetection],
    fps: float,
) -> None:
    overlay = pygame.Surface((360, 180), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 160))
    font = pygame.font.SysFont("Arial", 20)
    lines = [
        f"FPS: {fps:.1f}",
        f"Area: {detection.area if detection else 0:.1f}",
        f"Confidence: {detection.confidence if detection else 0:.2f}",
        f"Point: {detection.point if detection else None}",
    ]
    for idx, line in enumerate(lines):
        txt = font.render(line, True, (255, 255, 255))
        overlay.blit(txt, (10, 10 + idx * 26))

    if detection and detection.mask_preview is not None:
        try:
            surf = pygame.image.frombuffer(
                detection.mask_preview.tobytes(),
                detection.mask_preview.shape[1::-1],
                "RGB",
            )
            preview_rect = surf.get_rect(bottomright=(overlay.get_width() - 10, overlay.get_height() - 10))
            pygame.draw.rect(overlay, (200, 200, 200), preview_rect.inflate(6, 6), 1)
            overlay.blit(surf, preview_rect)
        except Exception:
            LOGGER.debug("Fehler beim Rendern der Masken-Vorschau", exc_info=True)

    screen.blit(overlay, (15, 15))


def main() -> None:
    setup_logging()
    settings = load_settings()
    pygame.init()
    pygame.font.init()
    clock = pygame.time.Clock()
    try_set_resolution(settings)
    screen = init_display(settings)
    screen_points = build_calib_points(*screen.get_size())

    homography_data = load_homography(screen_points)
    active_app = None
    calibration_ui = CalibrationUI(screen, on_done=lambda data: homography_data.__dict__.update(data.__dict__))
    test_mode = None
    launcher = launcher_module.Launcher(screen, on_start_app=lambda label: start_app(label))

    last_laser_point = None
    last_detection: Optional[LaserDetection] = None
    tracker = None
    camera_ok = True
    camera_error: Optional[str] = None
    try:
        tracker = LaserTracker(settings)
        tracker.start()
    except Exception as exc:
        LOGGER.warning("Kamera-Start fehlgeschlagen, Maus-Modus aktiv: %s", exc)
        camera_ok = False
        camera_error = str(exc)

    def reinitialize_display() -> Optional[str]:
        nonlocal screen, screen_points, homography_data, launcher, calibration_ui, active_app, test_mode
        try_set_resolution(settings)
        screen = init_display(settings)
        screen_points = build_calib_points(*screen.get_size())
        homography_data = load_homography(screen_points)
        calibration_ui.screen = screen
        calibration_ui.reset()
        launcher = launcher_module.Launcher(screen, on_start_app=lambda label: start_app(label))
        if test_mode:
            test_mode.update_context(screen, homography_data.homography)
        if active_app:
            app_cls = type(active_app)
            try:
                active_app = app_cls(screen)
            except Exception:
                LOGGER.exception("Aktive App konnte nach Auflösungswechsel nicht neu gestartet werden.")
                active_app = None
        return "Anzeige neu initialisiert."

    def restart_tracker() -> tuple[bool, Optional[str]]:
        nonlocal tracker, camera_ok, camera_error
        if tracker:
            tracker.stop()
        tracker = None
        camera_error = None
        camera_ok = True
        try:
            tracker = LaserTracker(settings)
            tracker.start()
            LOGGER.info("Kamera nach Auswahl neu gestartet.")
        except Exception as exc:
            LOGGER.warning("Kamera-Start fehlgeschlagen: %s", exc)
            camera_ok = False
            camera_error = str(exc)
        if test_mode:
            test_mode.set_camera_status(camera_ok, camera_error)
        return camera_ok, camera_error

    def pointer_handler(evt):
        nonlocal active_app, launcher, calibration_ui, test_mode
        target = active_app or test_mode or calibration_ui if calibration_active else launcher
        if calibration_active:
            calibration_ui.handle_pointer(evt.type, evt.position)
        elif test_mode:
            test_mode.handle_pointer(evt.type, evt.position)
        elif active_app:
            active_app.handle_pointer(evt.type, evt.position)
        else:
            launcher.handle_pointer(evt.type, evt.position)

    pointer_router = PointerRouter(
        on_event=pointer_handler,
        dwell_ms=settings.dwell_ms,
        dwell_radius=settings.dwell_radius,
    )

    calibration_active = False
    running = True

    def start_app(label: str):
        nonlocal active_app, calibration_active, test_mode
        if label == "__calibrate__":
            calibration_active = True
            test_mode = None
            active_app = None
            calibration_ui.reset()
        elif label == "__test__":
            calibration_active = False
            active_app = None
            test_mode = TestMode(
                screen,
                settings,
                homography_data.homography,
                lambda: last_laser_point,
                on_camera_change=restart_tracker,
                on_resolution_change=reinitialize_display,
            )
            test_mode.set_camera_status(camera_ok, camera_error)
        else:
            calibration_active = False
            test_mode = None
            cls = launcher_module.APP_CLASSES[label]
            active_app = cls(screen)

    while running:
        dt = clock.tick(FPS) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                active_app = None
                calibration_active = False
                test_mode = None
            elif event.type == pygame.MOUSEMOTION:
                pointer_router.feed_mouse_event("move", event.pos)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                pointer_router.feed_mouse_event("down", event.pos)
            elif event.type == pygame.MOUSEBUTTONUP:
                pointer_router.feed_mouse_event("click", event.pos)

        if tracker:
            try:
                detection = tracker.read()
                last_detection = detection
                if test_mode:
                    test_mode.set_detection(detection)
                last_laser_point = detection.point
                if detection.point:
                    mapped = (
                        apply_homography(homography_data.homography, detection.point)
                        if homography_data.homography is not None
                        else detection.point
                    )
                    pointer_router.feed_point(mapped, source="laser")
            except Exception as exc:
                LOGGER.error("Kamera-Feed Fehler: %s", exc)
                tracker.stop()
                tracker = None
                camera_ok = False
                camera_error = str(exc)
                if test_mode:
                    test_mode.set_camera_status(camera_ok, camera_error)

        if calibration_active:
            calibration_ui.update(dt)
            calibration_ui.draw()
        elif test_mode:
            test_mode.update(dt)
            test_mode.draw()
        elif active_app:
            active_app.update(dt)
            active_app.draw()
        else:
            launcher.draw()

        if not camera_ok:
            overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 120))
            font = pygame.font.SysFont("Arial", 28)
            txt = font.render(
                f"Kamera nicht verfügbar - Maus-Modus aktiv ({camera_error or 'unbekannt'})",
                True,
                (255, 200, 200),
            )
            overlay.blit(txt, (40, 40))
            screen.blit(overlay, (0, 0))
        elif settings.debug_overlay:
            render_debug_overlay(screen, last_detection, clock.get_fps())

        pygame.display.flip()

    if tracker:
        tracker.stop()
    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.basicConfig(level=logging.ERROR)
        LOGGER.exception("Fehler im Hauptprogramm: %s", exc)
        sys.exit(1)
