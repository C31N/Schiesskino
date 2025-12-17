from __future__ import annotations

import logging
from typing import Tuple

import pygame

from .calibration import apply_homography
from .ui import make_font

LOGGER = logging.getLogger(__name__)


class TestMode:
    name = "Testmodus"

    def __init__(self, screen: pygame.Surface, homography, last_point_provider):
        self.screen = screen
        self.font = make_font(26)
        self.homography = homography
        self.last_point_provider = last_point_provider
        self.last_mapped = None

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "move":
            self.last_mapped = pos

    def update(self, dt: float) -> None:
        ...

    def draw(self) -> None:
        self.screen.fill((15, 10, 15))
        laser_pos = self.last_point_provider()
        mapped = apply_homography(self.homography, laser_pos) if (self.homography is not None and laser_pos) else None

        if laser_pos:
            pygame.draw.circle(self.screen, (255, 0, 0), laser_pos, 10)
        if mapped:
            pygame.draw.circle(self.screen, (0, 255, 0), mapped, 12, 3)
            cross = [
                (mapped[0] - 20, mapped[1]),
                (mapped[0] + 20, mapped[1]),
                (mapped[0], mapped[1] - 20),
                (mapped[0], mapped[1] + 20),
            ]
            pygame.draw.line(self.screen, (0, 255, 0), cross[0], cross[1], 2)
            pygame.draw.line(self.screen, (0, 255, 0), cross[2], cross[3], 2)

        txt = f"Laser: {laser_pos} | Mapped: {mapped}"
        text = self.font.render(txt, True, (255, 255, 255))
        self.screen.blit(text, (20, 20))
