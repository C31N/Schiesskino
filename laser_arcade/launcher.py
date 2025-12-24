from __future__ import annotations

import logging
from typing import Callable, Dict, Tuple, Type

import pygame

from .apps.base import BaseApp
from .apps.cans import CansApp
from .apps.chickens import ChickenApp
from .apps.paint import PaintApp
from .apps.reaction import ReactionApp
from .apps.target import TargetApp
from .constants import DEFAULT_FONT_SIZE, SCREEN_HEIGHT, SCREEN_WIDTH, TITLE_FONT_SIZE
from .ui import Button, layout_grid, make_font, render_label

LOGGER = logging.getLogger(__name__)


APP_CLASSES: Dict[str, Type[BaseApp]] = {
    CansApp.name: CansApp,
    ChickenApp.name: ChickenApp,
    TargetApp.name: TargetApp,
    ReactionApp.name: ReactionApp,
    PaintApp.name: PaintApp,
}


class Launcher:
    def __init__(
        self,
        screen: pygame.Surface,
        on_start_app: Callable[[str], None],
        on_quit: Callable[[], None],
    ):
        self.screen = screen
        self.on_start_app = on_start_app
        self.on_quit = on_quit
        self.font = make_font(DEFAULT_FONT_SIZE)
        self.title_font = make_font(TITLE_FONT_SIZE)
        self.buttons = self._create_buttons()

    def _create_buttons(self):
        labels = list(APP_CLASSES.keys()) + ["Kalibrierung", "Testmodus", "Beenden"]
        grid = layout_grid(self.screen, labels=labels, columns=2)
        buttons = []
        for label, rect in grid:
            buttons.append(
                Button(
                    rect=rect,
                    label=label,
                    action=lambda l=label: self._handle_click(l),
                    font=self.font,
                )
            )
        return buttons

    def _handle_click(self, label: str) -> None:
        if label in APP_CLASSES:
            self.on_start_app(label)
        elif label == "Kalibrierung":
            self.on_start_app("__calibrate__")
        elif label == "Testmodus":
            self.on_start_app("__test__")
        elif label == "Beenden":
            self.on_quit()

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        if event_type == "click":
            for btn in self.buttons:
                if btn.contains(pos):
                    btn.action()

    def draw(self) -> None:
        self.screen.fill((20, 20, 40))
        title_surf = self.title_font.render("Laser Arcade", True, (255, 255, 255))
        title_rect = title_surf.get_rect(center=(SCREEN_WIDTH // 2, 100))
        self.screen.blit(title_surf, title_rect)
        render_label(self.screen, "Zielen und klicken mit Laser oder Maus", 160, size=DEFAULT_FONT_SIZE)
        for btn in self.buttons:
            btn.draw(self.screen)
