from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
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


@dataclass
class TimedTarget:
    center: Tuple[int, int]
    radius: int
    appeared_at: float
    expires_at: float


class TimedShootingApp(BaseApp):
    name = "Zeitschießen"
    COUNTDOWN_DURATION = 3.35
    GAME_DURATION = 40.0
    TOTAL_TARGETS = 20
    TARGET_LIFETIME = 1.75

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        random_seed: int = 20260724,
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
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.next_target_at = 0.0
        self.remaining = self.GAME_DURATION
        self.target: Optional[TimedTarget] = None
        self.targets_shown = 0
        self.completed_targets = 0
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.reaction_times: list[float] = []
        self.last_hit: Optional[Tuple[int, int]] = None
        self.last_hit_until = 0.0
        self.last_count_value: Optional[int] = None
        self.finish_reason = ""

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def average_time_ms(self) -> int:
        return round(1000.0 * sum(self.reaction_times) / len(self.reaction_times)) if self.reaction_times else 0

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self._reset_round()
        LOGGER.info("Zeitschießen bereit")

    def stop(self) -> None:
        self.sounds.stop_all()

    def _reset_round(self) -> None:
        self.remaining = self.GAME_DURATION
        self.target = None
        self.targets_shown = 0
        self.completed_targets = 0
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.reaction_times = []
        self.last_hit = None
        self.last_hit_until = 0.0
        self.last_count_value = None
        self.finish_reason = ""

    def begin_countdown(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        LOGGER.info("Zeitschießen gestartet: %s Ziele", self.TOTAL_TARGETS)

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
                (("repeat", self.repeat_button), ("menu", self.result_menu_button)),
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
        target = self.target
        if (
            target is None
            or distance(target.center, pos) > target.radius + self.hit_tolerance
        ):
            self.combo = 0
            self.sounds.play("miss")
            return "miss"
        reaction = max(0.0, current - target.appeared_at)
        self.reaction_times.append(reaction)
        self.hits += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        speed_points = max(0, round((self.TARGET_LIFETIME - reaction) * 180))
        points = 100 + speed_points + min(160, (self.combo - 1) * 20)
        self.score += points
        self.last_hit = target.center
        self.last_hit_until = current + 0.35
        self.target = None
        self.completed_targets += 1
        self.next_target_at = current + 0.22
        self.sounds.play("target_hit")
        LOGGER.info(
            "Zeitziel getroffen: %s/%s, Reaktion=%sms, Punkte=%s",
            self.hits,
            self.TOTAL_TARGETS,
            round(reaction * 1000),
            self.score,
        )
        if self.completed_targets >= self.TOTAL_TARGETS:
            self._finish("Alle Ziele abgeschlossen", current)
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
                self.deadline = current + self.GAME_DURATION
                self.next_target_at = current
                self.sounds.play("go")
            return
        if self.state != "playing":
            return
        self.remaining = max(0.0, self.deadline - current)
        if self.target is not None and current >= self.target.expires_at:
            self.target = None
            self.combo = 0
            self.completed_targets += 1
            self.next_target_at = current + 0.18
            self.sounds.play("miss")
        if (
            self.target is None
            and self.targets_shown < self.TOTAL_TARGETS
            and self.completed_targets < self.TOTAL_TARGETS
            and current >= self.next_target_at
        ):
            self._spawn_target(current)
        if self.remaining <= 0:
            self._finish("Die Zeit ist abgelaufen", current)
        elif self.completed_targets >= self.TOTAL_TARGETS:
            self._finish("Alle Ziele abgeschlossen", current)

    def _spawn_target(self, now: float) -> None:
        width, height = self.screen.get_size()
        radius = self.random.randint(38, 54)
        x = self.random.randint(90 + radius, width - 90 - radius)
        y = self.random.randint(190 + radius, height - 75 - radius)
        if self.target is not None:
            return
        self.target = TimedTarget((x, y), radius, now, now + self.TARGET_LIFETIME)
        self.targets_shown += 1
        self.sounds.play("button")

    def _finish(self, reason: str, now: float) -> None:
        if self.state == "game_over":
            return
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = reason
        self.target = None
        self.score += int(self.remaining * 10)
        self.sounds.play("finish")
        LOGGER.info(
            "Zeitschießen beendet: %s, Treffer=%s/%s, Ø=%sms",
            reason,
            self.hits,
            self.TOTAL_TARGETS,
            self.average_time_ms,
        )

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        draw_frame(self.screen, "timed", current)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "timed", current)
            draw_ready_card(
                self.screen,
                "ZEITSCHIEßEN",
                "20 ZIELE  ·  40 SEKUNDEN  ·  TEMPO ENTSCHEIDET",
                (
                    "Triff jedes Ziel, bevor es wieder verschwindet.",
                    "Schnelle Treffer und Serien erhöhen die Punkte.",
                    "Nach 20 Zielen folgt automatisch die Auswertung.",
                ),
                self.start_card,
                self.start_button,
                self.menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )
            return
        self._draw_playfield(current, subdued=self.state == "game_over")
        draw_ambient_foreground(self.screen, "timed", current)
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
                    ("TREFFER", f"{self.hits}/{self.TOTAL_TARGETS}"),
                    ("Ø REAKTION", f"{self.average_time_ms} ms"),
                    ("PRÄZISION", f"{self.accuracy:.0f} %"),
                ),
                self.repeat_button,
                self.result_menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )

    def _draw_playfield(self, now: float, *, subdued: bool = False) -> None:
        title = self.font_large.render("ZEITSCHIEßEN", True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)
        draw_hud(
            self.screen,
            (
                ("PUNKTE", f"{self.score:05d}"),
                ("ZIELE", f"{self.completed_targets}/{self.TOTAL_TARGETS}"),
                ("ZEIT", f"{self.remaining:04.1f}"),
                ("Ø REAKTION", f"{self.average_time_ms} ms"),
                ("SERIE", f"{self.combo}x"),
            ),
            self.font_small,
            self.font,
        )
        playfield = pygame.Rect(35, 170, self.screen.get_width() - 70, self.screen.get_height() - 205)
        playfield_fill = pygame.Surface(playfield.size, pygame.SRCALPHA)
        playfield_fill.fill((0, 18, 32, 118))
        self.screen.blit(playfield_fill, playfield)
        pygame.draw.rect(self.screen, SAFE_MUTED, playfield, 2, border_radius=14)
        if subdued:
            return
        if self.target is not None:
            pulse = int(3 * (1.0 + pygame.time.get_ticks() % 300 / 300.0))
            draw_target_rings(
                self.screen,
                self.target.center,
                self.target.radius + pulse,
                active=True,
                rings=5,
            )
        if self.last_hit is not None and now < self.last_hit_until:
            pygame.draw.circle(self.screen, TARGET_GREEN, self.last_hit, 24, 3)
