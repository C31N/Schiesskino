from __future__ import annotations

import logging
import os
import sys
import time

import pygame

from . import launcher as launcher_module
from .calibration import apply_homography, build_calib_points, load_homography
from .calibration_ui import CalibrationUI
from .config import Settings, load_settings
from .constants import FPS
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


def main() -> None:
    setup_logging()
    settings = load_settings()
    pygame.init()
    pygame.font.init()
    clock = pygame.time.Clock()
    screen = init_display(settings)
    screen_points = build_calib_points(*screen.get_size())

    homography_data = load_homography(screen_points)
    active_app = None
    calibration_ui = CalibrationUI(screen, on_done=lambda data: homography_data.__dict__.update(data.__dict__))
    test_mode = None
    launcher = launcher_module.Launcher(screen, on_start_app=lambda label: start_app(label))

    last_laser_point = None
    tracker = None
    camera_ok = True
    try:
        tracker = LaserTracker(settings)
        tracker.start()
    except Exception as exc:
        LOGGER.warning("Kamera-Start fehlgeschlagen, Maus-Modus aktiv: %s", exc)
        camera_ok = False

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
            test_mode = TestMode(screen, homography_data.homography, lambda: last_laser_point)
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
            txt = font.render("Kamera nicht verfügbar - Maus-Modus aktiv", True, (255, 200, 200))
            overlay.blit(txt, (40, 40))
            screen.blit(overlay, (0, 0))

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
