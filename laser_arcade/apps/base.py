from __future__ import annotations

from typing import Tuple

import pygame


class BaseApp:
    name: str = "Base"

    def __init__(self, screen: pygame.Surface):
        self.screen = screen

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        ...

    def update(self, dt: float) -> None:
        ...

    def draw(self) -> None:
        ...
