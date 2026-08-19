from __future__ import annotations

import logging
import random
import time
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    SAFE_CYAN,
    SAFE_GREEN,
    TARGET_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    distance,
    draw_button,
    draw_ambient_foreground,
    draw_countdown,
    draw_frame,
    draw_hud,
    draw_ready_card,
    draw_result_card,
    draw_target_rings,
    calibrated_hit_tolerance,
    nearest_laser_button,
)
from .base import BaseApp
from .cans import CanGameSounds

LOGGER = logging.getLogger(__name__)


class ReactionApp(BaseApp):
    name = "Reaktion"
    COUNTDOWN_DURATION = 3.35
    ROUNDS = 12
    SIGNAL_LIFETIME = 2.0

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        random_seed: int = 20260725,
        sounds: Optional[CanGameSounds] = None,
    ) -> None:
        super().__init__(screen)
        self.random = random.Random(random_seed)
        self.hit_tolerance = calibrated_hit_tolerance(screen.get_size())
        self.sounds = sounds or CanGameSounds(audio_enabled)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22)
        self.font_large = pygame.font.SysFont("Arial", 35, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 50, bold=True)
        self.font_countdown = pygame.font.SysFont("Arial", 116, bold=True)
        width, height = screen.get_size()
        self.menu_button = pygame.Rect(width - 170, 24, 140, 40)
        self.start_card = pygame.Rect(0, 0, 650, 365)
        self.start_card.center = (width // 2, height // 2 + 22)
        self.start_button = pygame.Rect(width // 2 - 175, height // 2 + 100, 350, 58)
        self.result_card = pygame.Rect(0, 0, 700, 440)
        self.result_card.center = (width // 2, height // 2 + 18)
        self.repeat_button = pygame.Rect(width // 2 - 300, height - 105, 280, 48)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 105, 280, 48)
        self.pad_centers = [
            (width // 2 + (column - 1) * 245, 250 + row * 185)
            for row in range(3)
            for column in range(3)
        ]
        self.pad_radius = 54
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.phase = "waiting"
        self.next_signal_at = 0.0
        self.signal_started = 0.0
        self.active_index: Optional[int] = None
        self.completed = 0
        self.hits = 0
        self.shots = 0
        self.false_starts = 0
        self.score = 0
        self.reaction_times: list[float] = []
        self.last_count_value: Optional[int] = None
        self.finish_reason = ""

    @property
    def average_ms(self) -> int:
        return round(1000.0 * sum(self.reaction_times) / len(self.reaction_times)) if self.reaction_times else 0

    @property
    def best_ms(self) -> int:
        return round(1000.0 * min(self.reaction_times)) if self.reaction_times else 0

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self._reset_round()
        LOGGER.info("Reaktionsspiel bereit")

    def stop(self) -> None:
        self.sounds.stop_all()

    def _reset_round(self) -> None:
        self.phase = "waiting"
        self.active_index = None
        self.completed = 0
        self.hits = 0
        self.shots = 0
        self.false_starts = 0
        self.score = 0
        self.reaction_times = []
        self.last_count_value = None
        self.finish_reason = ""

    def begin_countdown(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        LOGGER.info("Reaktionsspiel gestartet: %s Signale", self.ROUNDS)

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if nearest_laser_button(pos, (("menu", self.menu_button),)) == "menu":
            self.sounds.play("button")
            return "menu"
        if self.state == "ready":
            if self.start_card.collidepoint(pos) or nearest_laser_button(
                pos, (("start", self.start_button),)
            ) == "start":
                self.begin_countdown(current)
            return "handled"
        if self.state == "game_over":
            choice = nearest_laser_button(
                pos,
                (
                    ("repeat", self.repeat_button),
                    ("menu", self.result_menu_button),
                ),
                expansion=(260, 220),
                group_rect=self.result_card.inflate(120, 120),
            )
            if choice == "menu":
                self.sounds.play("button")
                return "menu"
            if choice == "repeat":
                self.begin_countdown(current)
            return "handled"
        if self.state != "playing":
            return "handled"

        self.shots += 1
        self.sounds.play("shot")
        if self.phase == "waiting" or self.active_index is None:
            self.false_starts += 1
            self.score = max(0, self.score - 100)
            self.next_signal_at = max(self.next_signal_at, current + 0.7)
            self.sounds.play("miss")
            LOGGER.info("Reaktion Frühstart: %s", self.false_starts)
            return "early"
        center = self.pad_centers[self.active_index]
        if distance(center, pos) > self.pad_radius + self.hit_tolerance:
            self.score = max(0, self.score - 50)
            self.sounds.play("miss")
            return "miss"
        reaction = max(0.0, current - self.signal_started)
        self.reaction_times.append(reaction)
        self.hits += 1
        self.score += 150 + max(0, round((self.SIGNAL_LIFETIME - reaction) * 300))
        self.sounds.play("reaction_hit")
        self._complete_signal(current)
        LOGGER.info(
            "Reaktionsziel getroffen: Runde=%s/%s, Zeit=%sms",
            self.completed,
            self.ROUNDS,
            round(reaction * 1000),
        )
        return "hit"

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.last_update = current
        if self.state == "countdown":
            elapsed = current - self.state_started
            count_value = 3 - int(elapsed)
            if count_value > 0 and count_value != self.last_count_value:
                self.sounds.play("count")
                self.last_count_value = count_value
            if elapsed >= self.COUNTDOWN_DURATION:
                self.state = "playing"
                self.state_started = current
                self.phase = "waiting"
                self.next_signal_at = current + self.random.uniform(0.8, 1.8)
                self.sounds.play("go")
            return
        if self.state != "playing":
            return
        if self.phase == "waiting" and current >= self.next_signal_at:
            previous = self.active_index
            choices = [index for index in range(len(self.pad_centers)) if index != previous]
            self.active_index = self.random.choice(choices)
            self.phase = "active"
            self.signal_started = current
            self.sounds.play("button")
        elif self.phase == "active" and current - self.signal_started >= self.SIGNAL_LIFETIME:
            self.sounds.play("miss")
            self._complete_signal(current)

    def _complete_signal(self, now: float) -> None:
        self.completed += 1
        self.active_index = None
        if self.completed >= self.ROUNDS:
            self._finish("Alle Signale abgeschlossen", now)
            return
        self.phase = "waiting"
        self.next_signal_at = now + self.random.uniform(0.75, 1.75)

    def _finish(self, reason: str, now: float) -> None:
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = reason
        self.active_index = None
        self.sounds.play("finish")
        LOGGER.info(
            "Reaktionsspiel beendet: Treffer=%s/%s, Ø=%sms, Frühstarts=%s",
            self.hits,
            self.ROUNDS,
            self.average_ms,
            self.false_starts,
        )

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        draw_frame(self.screen, "reaction", current)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "reaction", current)
            draw_ready_card(
                self.screen,
                "REAKTION",
                "12 SIGNALE  ·  ERST SCHIEßEN, WENN EIN ZIEL AUFLEUCHTET",
                (
                    "Neun Felder bleiben zunächst dunkel.",
                    "Triff ausschließlich das hervorgehobene Ziel.",
                    "Frühstarts kosten Punkte und verzögern das Signal.",
                ),
                self.start_card,
                self.start_button,
                self.menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )
            return
        self._draw_playfield(subdued=self.state == "game_over")
        draw_ambient_foreground(self.screen, "reaction", current)
        if self.state == "countdown":
            draw_countdown(self.screen, self.state_started, current, self.font_countdown)
        elif self.state == "game_over":
            draw_result_card(
                self.screen,
                self.result_card,
                "GESAMTAUSWERTUNG",
                self.finish_reason,
                (
                    ("PUNKTE", str(self.score)),
                    ("TREFFER", f"{self.hits}/{self.ROUNDS}"),
                    ("Ø REAKTION", f"{self.average_ms} ms"),
                    ("BESTZEIT", f"{self.best_ms} ms"),
                ),
                self.repeat_button,
                self.result_menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )

    def _draw_playfield(self, *, subdued: bool = False) -> None:
        title = self.font_large.render("REAKTION", True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)
        draw_hud(
            self.screen,
            (
                ("PUNKTE", f"{self.score:05d}"),
                ("RUNDE", f"{self.completed}/{self.ROUNDS}"),
                ("STATUS", "JETZT!" if self.phase == "active" else "BEREITHALTEN"),
                ("Ø REAKTION", f"{self.average_ms} ms"),
                ("FRÜHSTARTS", str(self.false_starts)),
            ),
            self.font_small,
            self.font,
        )
        for index, center in enumerate(self.pad_centers):
            active = not subdued and self.phase == "active" and index == self.active_index
            draw_target_rings(
                self.screen,
                center,
                self.pad_radius,
                active=active,
                rings=4,
            )
            number = self.font_small.render(str(index + 1), True, TARGET_GREEN if active else SAFE_MUTED)
            self.screen.blit(number, number.get_rect(center=center))
        if self.phase == "waiting" and not subdued:
            hint = self.font.render("BEREITHALTEN …", True, SAFE_MUTED)
            self.screen.blit(hint, hint.get_rect(midtop=(self.screen.get_width() // 2, 162)))
