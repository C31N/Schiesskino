from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    SAFE_BLUE,
    SAFE_CYAN,
    SAFE_DARK,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    TARGET_BLUE,
    TARGET_CYAN,
    TARGET_GREEN,
    distance,
    draw_button,
    draw_ambient_foreground,
    draw_countdown,
    draw_frame,
    draw_hud,
    load_target_sprite,
    draw_ready_card,
    draw_result_card,
    draw_size_step_button,
    calibrated_hit_tolerance,
    nearest_laser_button,
    sprite_hit_test,
)
from .base import BaseApp
from .cans import CanGameSounds

LOGGER = logging.getLogger(__name__)


@dataclass
class Clay:
    x: float
    y: float
    velocity_x: float
    velocity_y: float
    radius: int
    born_at: float
    alive: bool = True
    broken_at: float = 0.0


@dataclass
class Shard:
    x: float
    y: float
    velocity_x: float
    velocity_y: float
    born_at: float


class ClayShootingApp(BaseApp):
    name = "Tontaubenschießen"
    COUNTDOWN_DURATION = 3.35
    GAME_DURATION = 45.0
    TOTAL_CLAYS = 20
    LAUNCH_INTERVAL = 1.45
    TARGET_SCALES = (1.0, 1.2, 1.4, 1.6)
    GRAVITY = 260.0
    # Kamera, Bildauswertung und Beamer erzeugen gemeinsam eine kleine
    # Verzögerung. Die Trefferzone folgt deshalb auch der kurz zuvor sichtbaren
    # Flugbahn, statt nur die bereits weitergewanderte aktuelle Position zu prüfen.
    MOTION_COMPENSATION_SECONDS = 0.22

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        random_seed: int = 20260723,
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
        # Vor dem hellen Himmel braucht der rote Laser eine größere, wirklich
        # dunkle Ruhefläche. Die Taste bleibt getrennt von der Plus-Taste.
        self.menu_button = pygame.Rect(width - 194, 18, 166, 54)
        self.start_card = pygame.Rect(0, 0, 650, 365)
        self.start_card.center = (width // 2, height // 2 + 22)
        self.start_button = pygame.Rect(width // 2 - 175, height // 2 + 118, 350, 58)
        self.result_card = pygame.Rect(0, 0, 700, 440)
        self.result_card.center = (width // 2, height // 2 + 18)
        self.repeat_button = pygame.Rect(width // 2 - 300, height - 105, 280, 48)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 105, 280, 48)
        self.ready_size_minus_button = pygame.Rect(width // 2 - 212, height - 326, 82, 40)
        self.ready_size_plus_button = pygame.Rect(width // 2 + 130, height - 326, 82, 40)
        # Fester, breiter Beschriftungsbereich zwischen Minus und Plus. Die
        # Abstände bleiben auch mit der auf dem Pi installierten Schrift frei.
        self.size_minus_button = pygame.Rect(width - 474, 24, 70, 40)
        self.size_plus_button = pygame.Rect(width - 262, 24, 70, 40)
        self.target_scale_index = 1
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.remaining = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.launched = 0
        self.next_launch_at = 0.0
        self.clays: list[Clay] = []
        self.shards: list[Shard] = []
        self.last_count_value: Optional[int] = None
        self.finish_reason = ""

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def target_scale(self) -> float:
        return self.TARGET_SCALES[self.target_scale_index]

    @property
    def target_scale_label(self) -> str:
        return f"GRÖßE {round(self.target_scale * 100)} %"

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self._reset_round()
        LOGGER.info("Tontaubenschießen bereit")

    def stop(self) -> None:
        self.sounds.stop_all()

    def _reset_round(self) -> None:
        self.remaining = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.launched = 0
        self.clays = []
        self.shards = []
        self.last_count_value = None
        self.finish_reason = ""

    def begin_countdown(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        LOGGER.info("Tontaubenschießen gestartet: %s Ziele", self.TOTAL_CLAYS)

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        size_buttons = (
            (
                ("smaller", self.ready_size_minus_button),
                ("larger", self.ready_size_plus_button),
            )
            if self.state == "ready"
            else (
                ("smaller", self.size_minus_button),
                ("larger", self.size_plus_button),
            )
        )
        if self.state == "ready":
            control = nearest_laser_button(
                pos, (("menu", self.menu_button),), expansion=(148, 108)
            )
            if control is None:
                control = nearest_laser_button(pos, size_buttons, expansion=(96, 40))
        else:
            controls = (("menu", self.menu_button),)
            if self.state != "game_over":
                controls += size_buttons
            control = nearest_laser_button(pos, controls, expansion=(148, 108))
        if control == "menu":
            self.sounds.play("button")
            return "menu"
        if control in {"smaller", "larger"}:
            self._change_target_scale(-1 if control == "smaller" else 1)
            return "setting"
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
        candidates = [
            clay
            for clay in self.clays
            if clay.alive
            and (
                self._clay_visible_hit(clay, pos, current)
                or self._distance_to_recent_flight_path(clay, pos)
                <= clay.radius * 0.72 + self.hit_tolerance
            )
        ]
        if not candidates:
            self.combo = 0
            self.sounds.play("miss")
            return "miss"
        clay = min(
            candidates,
            key=lambda item: self._distance_to_recent_flight_path(item, pos),
        )
        clay.alive = False
        clay.broken_at = current
        self.hits += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        height_bonus = max(0, int((self.screen.get_height() - clay.y) * 0.18))
        points = 100 + height_bonus + min(150, (self.combo - 1) * 25)
        self.score += points
        self._make_shards(clay, current)
        self.sounds.play("clay_break")
        LOGGER.info(
            "Tontaube getroffen: %s/%s, Punkte=%s, Serie=%s",
            self.hits,
            self.launched,
            self.score,
            self.combo,
        )
        return "hit"

    def _distance_to_recent_flight_path(
        self,
        clay: Clay,
        point: Tuple[int, int],
    ) -> float:
        """Abstand zur aktuellen und kurz zuvor sichtbaren Flugposition."""

        delay = self.MOTION_COMPENSATION_SECONDS
        start_x = clay.x - clay.velocity_x * delay
        start_y = (
            clay.y
            - clay.velocity_y * delay
            + 0.5 * self.GRAVITY * delay * delay
        )
        end_x, end_y = clay.x, clay.y
        segment_x = end_x - start_x
        segment_y = end_y - start_y
        segment_length_sq = segment_x * segment_x + segment_y * segment_y
        if segment_length_sq <= 0.0001:
            return distance((end_x, end_y), point)
        projection = (
            (point[0] - start_x) * segment_x
            + (point[1] - start_y) * segment_y
        ) / segment_length_sq
        projection = max(0.0, min(1.0, projection))
        closest = (
            start_x + projection * segment_x,
            start_y + projection * segment_y,
        )
        return distance(closest, point)

    def _clay_sprite(self, clay: Clay, now: float) -> pygame.Surface:
        direction = 1 if clay.velocity_x >= 0 else -1
        spin = (now - clay.born_at) * 76.0
        return load_target_sprite(
            "clay",
            (clay.radius * 2 + 24, round(clay.radius * 1.45) + 18),
            flip_x=direction < 0,
            angle=math.sin(math.radians(spin)) * 9.0,
            brightness_limit=148,
        )

    def _clay_visible_hit(
        self,
        clay: Clay,
        pos: Tuple[int, int],
        now: float,
    ) -> bool:
        sprite = self._clay_sprite(clay, now)
        rect = sprite.get_rect(center=(round(clay.x), round(clay.y)))
        return sprite_hit_test(
            pos,
            rect,
            pygame.mask.from_surface(sprite, 8),
            margin=self.hit_tolerance,
        )

    def _change_target_scale(self, direction: int) -> None:
        previous_scale = self.target_scale
        self.target_scale_index = max(
            0,
            min(len(self.TARGET_SCALES) - 1, self.target_scale_index + direction),
        )
        current_scale = self.target_scale
        if current_scale != previous_scale:
            ratio = current_scale / previous_scale
            for clay in self.clays:
                clay.radius = max(12, round(clay.radius * ratio))
            LOGGER.info("Tontaubengröße geändert: %s", self.target_scale_label)
        self.sounds.play("button")

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        dt = max(0.0, min(0.1, current - self.last_update))
        self.last_update = current
        self._update_shards(dt, current)
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
                self.next_launch_at = current
                self.sounds.play("go")
            return
        if self.state != "playing":
            return
        self.remaining = max(0.0, self.deadline - current)
        if self.launched < self.TOTAL_CLAYS and current >= self.next_launch_at:
            self._launch(current)
            self.next_launch_at = current + self.LAUNCH_INTERVAL
        for clay in self.clays:
            if not clay.alive:
                continue
            clay.velocity_y += self.GRAVITY * dt
            clay.x += clay.velocity_x * dt
            clay.y += clay.velocity_y * dt
            if clay.y > self.screen.get_height() + 50 or clay.x < -60 or clay.x > self.screen.get_width() + 60:
                clay.alive = False
                self.combo = 0
        active = any(clay.alive for clay in self.clays)
        if self.remaining <= 0:
            self._finish("Die Zeit ist abgelaufen", current)
        elif self.launched >= self.TOTAL_CLAYS and not active:
            self._finish("Alle Tontauben geworfen", current)

    def _launch(self, now: float) -> None:
        width, height = self.screen.get_size()
        from_left = self.launched % 2 == 0
        x = -24.0 if from_left else width + 24.0
        velocity_x = self.random.uniform(245.0, 305.0) * (1.0 if from_left else -1.0)
        self.clays.append(
            Clay(
                x=x,
                y=float(height - self.random.randint(92, 132)),
                velocity_x=velocity_x,
                velocity_y=-self.random.uniform(315.0, 375.0),
                radius=round(self.random.randint(32, 38) * self.target_scale),
                born_at=now,
            )
        )
        self.launched += 1
        self.sounds.play("button")

    def _make_shards(self, clay: Clay, now: float) -> None:
        for index in range(9):
            angle = math.tau * index / 9.0 + self.random.uniform(-0.18, 0.18)
            speed = self.random.uniform(85.0, 210.0)
            self.shards.append(
                Shard(
                    clay.x,
                    clay.y,
                    math.cos(angle) * speed,
                    math.sin(angle) * speed,
                    now,
                )
            )

    def _update_shards(self, dt: float, now: float) -> None:
        for shard in self.shards:
            shard.velocity_y += 360.0 * dt
            shard.x += shard.velocity_x * dt
            shard.y += shard.velocity_y * dt
        self.shards = [shard for shard in self.shards if now - shard.born_at < 1.1]

    def _finish(self, reason: str, now: float) -> None:
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = reason
        self.score += int(self.remaining * 8)
        self.sounds.play("finish")
        LOGGER.info(
            "Tontaubenschießen beendet: %s, Treffer=%s/%s, Punkte=%s",
            reason,
            self.hits,
            self.TOTAL_CLAYS,
            self.score,
        )

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        draw_frame(self.screen, "clay", current)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "clay", current)
            draw_ready_card(
                self.screen,
                "TONTAUBENSCHIEßEN",
                "20 FLIEGENDE ZIELE  ·  45 SEKUNDEN",
                (
                    "Die Tontauben fliegen abwechselnd von beiden Seiten.",
                    "Hohe und schnelle Treffer bringen zusätzliche Punkte.",
                    "Trefferserien erhöhen den Bonus.",
                ),
                self.start_card,
                self.start_button,
                self.menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )
            self._draw_size_controls(
                self.ready_size_minus_button,
                self.ready_size_plus_button,
            )
            # draw_ready_card zeichnet die gemeinsame Standardtaste. Diese
            # vollständig deckende Variante stellt vor dem hellen Himmel die
            # benötigte Laserreserve wieder her.
            self._draw_laser_safe_menu_button()
            return
        self._draw_playfield(current, subdued=self.state == "game_over")
        draw_ambient_foreground(self.screen, "clay", current)
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
                    ("TREFFER", f"{self.hits}/{self.TOTAL_CLAYS}"),
                    ("PRÄZISION", f"{self.accuracy:.0f} %"),
                    ("BESTE SERIE", str(self.best_combo)),
                ),
                self.repeat_button,
                self.result_menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )

    def _draw_laser_safe_menu_button(self) -> None:
        """Dunkle, deckende Taste mit viel Kontrastreserve für den Rotpunkt."""

        outer = self.menu_button
        pygame.draw.rect(self.screen, (0, 5, 12), outer, border_radius=12)
        inner = outer.inflate(-6, -6)
        pygame.draw.rect(self.screen, SAFE_DARK, inner, border_radius=9)
        pygame.draw.rect(self.screen, TARGET_CYAN, outer, 2, border_radius=12)
        text = self.font_small.render("MENÜ", True, TARGET_CYAN)
        self.screen.blit(text, text.get_rect(center=outer.center))

    def _draw_playfield(self, now: float, *, subdued: bool = False) -> None:
        width, height = self.screen.get_size()
        title = self.font_large.render("TONTAUBENSCHIEßEN", True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        self._draw_laser_safe_menu_button()
        if not subdued:
            self._draw_size_controls(self.size_minus_button, self.size_plus_button)
        draw_hud(
            self.screen,
            (
                ("PUNKTE", f"{self.score:05d}"),
                ("GETROFFEN", f"{self.hits}/{self.TOTAL_CLAYS}"),
                ("ZEIT", f"{self.remaining:04.1f}"),
                ("PRÄZISION", f"{self.accuracy:.0f} %"),
                ("SERIE", f"{self.combo}x"),
            ),
            self.font_small,
            self.font,
        )
        ground_y = height - 74
        if subdued:
            return
        for clay in self.clays:
            if not clay.alive:
                continue
            center = (round(clay.x), round(clay.y))
            direction = 1 if clay.velocity_x >= 0 else -1
            for trail_index in range(3):
                trail_y = center[1] + (trail_index - 1) * 5
                pygame.draw.line(
                    self.screen,
                    SAFE_MUTED if trail_index else TARGET_BLUE,
                    (center[0] - direction * (clay.radius + 8), trail_y),
                    (center[0] - direction * (clay.radius + 42 + trail_index * 13), trail_y + 8),
                    2,
                )
            shadow = pygame.Rect(0, 0, clay.radius * 3, max(6, clay.radius // 3))
            shadow.center = (center[0], ground_y - 4)
            pygame.draw.ellipse(self.screen, SAFE_PANEL, shadow)
            sprite = self._clay_sprite(clay, now)
            self.screen.blit(sprite, sprite.get_rect(center=center))
            continue
            body_height = round(clay.radius * 1.2)
            outer = pygame.Rect(
                center[0] - clay.radius - 8,
                center[1] - body_height // 2 - 8,
                clay.radius * 2 + 16,
                body_height + 16,
            )
            glow = pygame.Surface((outer.width + 28, outer.height + 28), pygame.SRCALPHA)
            pygame.draw.ellipse(glow, (*TARGET_CYAN, 28), glow.get_rect().inflate(-8, -8), 6)
            self.screen.blit(glow, glow.get_rect(center=center))
            pygame.draw.ellipse(self.screen, SAFE_DARK, outer)
            body = pygame.Rect(
                center[0] - clay.radius,
                center[1] - body_height // 2,
                clay.radius * 2,
                body_height,
            )
            pygame.draw.ellipse(self.screen, (0, 38, 58), body)
            lower = body.copy()
            lower.top = body.centery
            lower.height = max(4, body.bottom - body.centery)
            pygame.draw.arc(self.screen, TARGET_BLUE, lower.inflate(0, body.height // 2), 0.05, math.pi - 0.05, 5)
            inner = pygame.Rect(
                center[0] - round(clay.radius * 0.62),
                center[1] - round(clay.radius * 0.26),
                round(clay.radius * 1.24),
                round(clay.radius * 0.52),
            )
            pygame.draw.ellipse(self.screen, (0, 68, 72), inner)
            pygame.draw.ellipse(self.screen, TARGET_GREEN, inner, max(3, clay.radius // 7))
            pygame.draw.ellipse(self.screen, TARGET_BLUE, body, 7)
            pygame.draw.ellipse(self.screen, TARGET_CYAN, body, 3)
            pygame.draw.circle(self.screen, SAFE_DARK, center, max(5, clay.radius // 4))
            pygame.draw.circle(self.screen, TARGET_GREEN, center, max(5, clay.radius // 4), 2)
            spin = (now - clay.born_at) * 10.0
            stripe_dx = round(math.cos(spin) * clay.radius * 0.72)
            stripe_dy = round(math.sin(spin) * body_height * 0.28)
            pygame.draw.line(
                self.screen,
                TARGET_CYAN,
                (center[0] - stripe_dx, center[1] - stripe_dy),
                (center[0] + stripe_dx, center[1] + stripe_dy),
                3,
            )
        for shard in self.shards:
            point = (round(shard.x), round(shard.y))
            pygame.draw.polygon(
                self.screen,
                TARGET_CYAN,
                ((point[0], point[1] - 5), (point[0] + 5, point[1] + 4), (point[0] - 4, point[1] + 3)),
            )

    def _draw_size_controls(
        self,
        minus_button: pygame.Rect,
        plus_button: pygame.Rect,
    ) -> None:
        draw_size_step_button(
            self.screen,
            minus_button,
            increase=False,
            color=SAFE_CYAN,
        )
        draw_size_step_button(
            self.screen,
            plus_button,
            increase=True,
            color=SAFE_GREEN,
        )
        label = self.font_small.render(self.target_scale_label, True, SAFE_GREEN)
        self.screen.blit(
            label,
            label.get_rect(center=((minus_button.right + plus_button.left) // 2, minus_button.centery)),
        )
