from __future__ import annotations

import logging
from typing import List, Tuple

import pygame

from .calibration import build_calib_points, compute_homography
from .ui import make_font

LOGGER = logging.getLogger(__name__)


class CalibrationUI:
    def __init__(self, screen: pygame.Surface, on_done):
        self.screen = screen
        self.on_done = on_done
        self.font = make_font(28)
        self.index = 0
        self.camera_points: List[Tuple[int, int]] = []
        self.message = "Ziele auf den Marker und halte kurz still"
        self.screen_points: List[Tuple[int, int]] = build_calib_points(*self.screen.get_size())

    def target_point(self) -> Tuple[int, int]:
        return self.screen_points[self.index]

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            self.camera_points.append(pos)
            self.index += 1
            if self.index >= len(self.screen_points):
                try:
                    data = compute_homography(self.camera_points, self.screen_points)
                    self.on_done(data)
                    self.reset(success_message="Kalibrierung erfolgreich gespeichert.")
                except Exception as exc:
                    LOGGER.exception("Kalibrierung fehlgeschlagen: %s", exc)
                    self.camera_points = []
                    self.index = 0
                    self.message = "Fehler – bitte erneut versuchen."
            else:
                self.message = "Punkt gesetzt – weiter zum nächsten Marker."

    def reset(self, success_message: str | None = None) -> None:
        self.index = 0
        self.camera_points = []
        self.screen_points = build_calib_points(*self.screen.get_size())
        if success_message:
            self.message = success_message
        elif len(self.screen_points) == 5:
            self.message = "Ziele auf den Marker und halte kurz still"
        else:
            self.message = "Kalibrierung bereit"

    def update(self, dt: float) -> None:
        # Platzhalter für zukünftige Animationen oder Audiofeedback
        return

    def draw(self) -> None:
        self.screen.fill((0, 0, 0))
        target = self.target_point()
        pygame.draw.circle(self.screen, (255, 255, 255), target, 18, 3)
        pygame.draw.circle(self.screen, (0, 200, 0), target, 6)
        info = f"Punkt {self.index + 1} / {len(self.screen_points)}"
        text = self.font.render(info, True, (255, 255, 255))
        msg = self.font.render(self.message, True, (200, 200, 200))
        self.screen.blit(text, (20, 20))
        self.screen.blit(msg, (20, 60))
