from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Tuple

import pygame

from .base import BaseApp
from ..ui import make_font


@dataclass
class Bird:
    rect: pygame.Rect
    speed: float
    alive: bool = True


class ChickenApp(BaseApp):
    name = "Moorhuhn"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.font = make_font(28)
        self.reset()

    def reset(self) -> None:
        self.score = 0
        self.time_left = 60
        width, height = self.screen.get_size()
        self.birds = [
            Bird(
                rect=pygame.Rect(
                    random.randint(0, width - 80), random.randint(50, height // 2), 80, 50
                ),
                speed=random.uniform(60, 120),
            )
            for _ in range(5)
        ]

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            for bird in self.birds:
                if bird.alive and bird.rect.collidepoint(pos):
                    bird.alive = False
                    self.score += 15

    def update(self, dt: float) -> None:
        width, _ = self.screen.get_size()
        self.time_left = max(0, self.time_left - dt)
        for bird in self.birds:
            if not bird.alive:
                continue
            bird.rect.x += bird.speed * dt
            if bird.rect.right > width or bird.rect.left < 0:
                bird.speed *= -1
                bird.rect.y += 20
            if bird.rect.y > self.screen.get_height():
                bird.rect.y = 50
        if self.time_left == 0:
            self.reset()

    def draw(self) -> None:
        self.screen.fill((5, 30, 50))
        for bird in self.birds:
            color = (250, 200, 80) if bird.alive else (120, 120, 120)
            pygame.draw.rect(self.screen, color, bird.rect, border_radius=10)
            pygame.draw.rect(self.screen, (0, 0, 0), bird.rect, 2, border_radius=10)
        hud = self.font.render(f"Punkte: {self.score}   Zeit: {int(self.time_left)}s", True, (255, 255, 255))
        self.screen.blit(hud, (20, 20))
