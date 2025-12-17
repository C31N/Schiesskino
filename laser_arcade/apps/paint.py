from __future__ import annotations

import itertools
from typing import List, Tuple

import pygame

from .base import BaseApp
from ..ui import make_font


class PaintApp(BaseApp):
    name = "Malen"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.font = make_font(28)
        self.colors = [(255, 0, 0), (0, 255, 0), (0, 120, 255), (255, 255, 0), (255, 255, 255)]
        self.color_cycle = itertools.cycle(self.colors)
        self.current_color = next(self.color_cycle)
        self.lines: List[Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]] = []
        self.last_pos = None

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            self.current_color = next(self.color_cycle)
            self.last_pos = None
        elif event_type == "move":
            if self.last_pos:
                self.lines.append((self.last_pos, pos, self.current_color))
            self.last_pos = pos

    def update(self, dt: float) -> None:
        return

    def draw(self) -> None:
        self.screen.fill((0, 0, 0))
        for start, end, color in self.lines[-2000:]:
            pygame.draw.line(self.screen, color, start, end, width=4)
        text = self.font.render("Klick zum Farbwechsel", True, (255, 255, 255))
        self.screen.blit(text, (20, 20))
