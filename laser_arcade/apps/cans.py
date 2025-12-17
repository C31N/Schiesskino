from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Tuple

import pygame

from .base import BaseApp
from ..ui import make_font


@dataclass
class Can:
    rect: pygame.Rect
    alive: bool = True


class CansApp(BaseApp):
    name = "Dosenschießen"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.font = make_font(28)
        self.reset()

    def reset(self) -> None:
        self.score = 0
        width, height = self.screen.get_size()
        self.cans = []
        can_width, can_height = 80, 120
        for i in range(6):
            x = 100 + i * (can_width + 20)
            y = height - can_height - 50
            self.cans.append(Can(pygame.Rect(x, y, can_width, can_height)))

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            for can in self.cans:
                if can.alive and can.rect.collidepoint(pos):
                    can.alive = False
                    self.score += 10
            if all(not c.alive for c in self.cans):
                self.reset()

    def update(self, dt: float) -> None:
        return

    def draw(self) -> None:
        self.screen.fill((10, 20, 30))
        for can in self.cans:
            color = (180, 180, 180) if can.alive else (70, 70, 70)
            pygame.draw.rect(self.screen, color, can.rect, border_radius=8)
            pygame.draw.rect(self.screen, (220, 220, 220), can.rect, 3, border_radius=8)

        score_surf = self.font.render(f"Punkte: {self.score}", True, (255, 255, 255))
        self.screen.blit(score_surf, (20, 20))
