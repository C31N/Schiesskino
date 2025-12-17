from __future__ import annotations

from typing import Tuple

import pygame

from .base import BaseApp
from ..ui import make_font


class TargetApp(BaseApp):
    name = "Zielscheibe"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.font = make_font(28)
        self.score = 0
        self.center = (screen.get_width() // 2, screen.get_height() // 2)
        self.radii = [160, 120, 80, 40]
        self.values = [5, 10, 20, 50]

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            dx = pos[0] - self.center[0]
            dy = pos[1] - self.center[1]
            dist2 = dx * dx + dy * dy
            for r, value in zip(self.radii, self.values):
                if dist2 <= r * r:
                    self.score += value
                    break

    def update(self, dt: float) -> None:
        return

    def draw(self) -> None:
        self.screen.fill((15, 15, 25))
        colors = [(200, 0, 0), (255, 255, 255), (0, 0, 200), (255, 215, 0)]
        for r, color in zip(self.radii, colors):
            pygame.draw.circle(self.screen, color, self.center, r)
            pygame.draw.circle(self.screen, (0, 0, 0), self.center, r, 3)
        score_surf = self.font.render(f"Punkte: {self.score}", True, (255, 255, 255))
        self.screen.blit(score_surf, (20, 20))
