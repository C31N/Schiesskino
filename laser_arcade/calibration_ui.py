from __future__ import annotations

import logging
from typing import List, Tuple

import pygame

from .calibration import CALIB_POINTS, compute_homography
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

    def target_point(self) -> Tuple[int, int]:
        return CALIB_POINTS[self.index]

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            self.camera_points.append(pos)
            self.index += 1
            if self.index >= len(CALIB_POINTS):
                try:
                    data = compute_homography(self.camera_points)
                    self.on_done(data)
                    self.reset()
                except Exception as exc:
                    LOGGER.exception("Kalibrierung fehlgeschlagen: %s", exc)
                    self.camera_points = []
                    self.index = 0

    def reset(self) -> None:
        self.index = 0
        self.camera_points = []

    def update(self, dt: float) -> None:
        ...

    def draw(self) -> None:
        self.screen.fill((0, 0, 0))
        target = self.target_point()
        pygame.draw.circle(self.screen, (255, 255, 255), target, 18, 3)
        pygame.draw.circle(self.screen, (0, 200, 0), target, 6)
        info = f"Punkt {self.index + 1} / {len(CALIB_POINTS)}"
        text = self.font.render(info, True, (255, 255, 255))
        msg = self.font.render(self.message, True, (200, 200, 200))
        self.screen.blit(text, (20, 20))
        self.screen.blit(msg, (20, 60))
