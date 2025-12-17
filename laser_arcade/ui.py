from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Tuple

import pygame

from .constants import DEFAULT_FONT_SIZE, LARGE_FONT_SIZE

LOGGER = logging.getLogger(__name__)


def make_font(size: int) -> pygame.font.Font:
    return pygame.font.SysFont("Arial", size)


@dataclass
class Button:
    rect: pygame.Rect
    label: str
    action: Callable[[], None]
    font: pygame.font.Font
    bg_color: Tuple[int, int, int] = (30, 144, 255)
    text_color: Tuple[int, int, int] = (255, 255, 255)

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, self.bg_color, self.rect, border_radius=8)
        text_surf = self.font.render(self.label, True, self.text_color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        surface.blit(text_surf, text_rect)

    def contains(self, pos: Tuple[int, int]) -> bool:
        return self.rect.collidepoint(pos)


def layout_grid(
    surface: pygame.Surface,
    labels: List[str],
    columns: int,
    font_size: int = LARGE_FONT_SIZE,
    padding: int = 20,
    button_height: int = 120,
) -> List[Tuple[str, pygame.Rect]]:
    width, height = surface.get_size()
    rows = (len(labels) + columns - 1) // columns
    button_width = (width - (columns + 1) * padding) // columns
    total_height = rows * button_height + (rows + 1) * padding
    top = (height - total_height) // 2
    rects = []
    for idx, label in enumerate(labels):
        row = idx // columns
        col = idx % columns
        x = padding + col * (button_width + padding)
        y = top + row * (button_height + padding)
        rects.append((label, pygame.Rect(x, y, button_width, button_height)))
    return rects


def render_label(surface: pygame.Surface, text: str, y: int, size: int = DEFAULT_FONT_SIZE) -> None:
    font = make_font(size)
    text_surf = font.render(text, True, (255, 255, 255))
    text_rect = text_surf.get_rect(center=(surface.get_width() // 2, y))
    surface.blit(text_surf, text_rect)
