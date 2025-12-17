from __future__ import annotations

import random
from typing import Tuple

import pygame

from .base import BaseApp
from ..ui import make_font


class ReactionApp(BaseApp):
    name = "Reaktion"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.font = make_font(28)
        self.reset()

    def reset(self) -> None:
        self.target_rect = self._new_target()
        self.best_time = None
        self.wait_time = random.uniform(1.0, 2.5)
        self.timer = 0
        self.active = False

    def _new_target(self) -> pygame.Rect:
        w, h = 80, 80
        width, height = self.screen.get_size()
        return pygame.Rect(
            random.randint(50, width - 50 - w),
            random.randint(80, height - 50 - h),
            w,
            h,
        )

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click" and self.active and self.target_rect.collidepoint(pos):
            if self.timer > 0:
                self.best_time = min(self.best_time, self.timer) if self.best_time else self.timer
            self.timer = 0
            self.wait_time = random.uniform(1.0, 2.5)
            self.active = False
            self.target_rect = self._new_target()

    def update(self, dt: float) -> None:
        self.wait_time -= dt
        if self.wait_time <= 0:
            self.active = True
            self.timer += dt
        if self.timer > 10:
            self.timer = 0
            self.active = False
            self.wait_time = random.uniform(1.0, 2.5)

    def draw(self) -> None:
        self.screen.fill((0, 30, 20))
        color = (0, 200, 0) if self.active else (60, 60, 60)
        pygame.draw.rect(self.screen, color, self.target_rect, border_radius=8)
        info = f"Reaktionszeit: {self.timer:.2f}s"
        if self.best_time:
            info += f" | Bestzeit: {self.best_time:.2f}s"
        text = self.font.render(info, True, (255, 255, 255))
        self.screen.blit(text, (20, 20))
