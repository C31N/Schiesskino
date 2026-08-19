from __future__ import annotations

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
    build_theme_background,
    calibrated_hit_tolerance,
    distance,
    draw_button,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_cinematic_overlay,
    draw_countdown,
    draw_hud,
    draw_ready_card,
    draw_result_card,
    draw_translucent_panel,
    load_target_sprite,
    nearest_laser_button,
)
from .base import BaseApp
from .cans import CanGameSounds

WHITE = (225, 250, 255)
# Beschießbare Flächen bleiben deutlich unter der Projektor-Sättigung. Ein
# roter Laserpuls besitzt dadurch selbst auf Kontur, Text oder Motiv genügend
# Rotüberschuss und wird nicht zu einem gelblich/weißen Mischpixel.
TARGET_CYAN = (0, 136, 162)
TARGET_GREEN = (0, 148, 88)
TARGET_BLUE = (0, 88, 142)
TARGET_TEAL = (0, 126, 132)
TARGET_TEXT = (0, 164, 184)
_BACKGROUND_CACHE: dict[tuple[Tuple[int, int], str], pygame.Surface] = {}


@dataclass
class ArcadeSpark:
    x: float
    y: float
    velocity_x: float
    velocity_y: float
    born_at: float
    lifetime: float
    color: Tuple[int, int, int]


@dataclass
class ImpactRing:
    center: Tuple[int, int]
    born_at: float
    color: Tuple[int, int, int]


def _background(size: Tuple[int, int], theme: str) -> pygame.Surface:
    key = size, theme
    cached = _BACKGROUND_CACHE.get(key)
    if cached is not None:
        return cached
    # Die sechs Kinder-Spiele besitzen eigene, räumliche Spielwelten. Das
    # zentrale Laden skaliert sie einmalig und entfernt vorsorglich sämtliche
    # roten Dekorpixel, damit nur der echte Laser eine Schusssignatur erzeugt.
    asset = build_theme_background(size, theme)
    if theme in {"balloons", "aliens", "stars", "math", "colors", "treasure"}:
        _BACKGROUND_CACHE[key] = asset
        return asset
    palettes = {
        "balloons": ((0, 29, 62), (0, 92, 128)),
        "aliens": ((0, 8, 26), (0, 51, 62)),
        "stars": ((0, 6, 28), (0, 24, 58)),
        "math": ((0, 16, 38), (0, 55, 71)),
        "colors": ((0, 7, 24), (0, 35, 53)),
        "treasure": ((0, 28, 43), (0, 77, 79)),
    }
    top, bottom = palettes[theme]
    width, height = size
    surface = pygame.Surface(size)
    for y in range(height):
        blend = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * blend) for i in range(3))
        pygame.draw.line(surface, color, (0, y), (width, y))
    if theme == "balloons":
        for x, y, radius in ((95, 210, 62), (820, 190, 78), (520, 330, 104)):
            pygame.draw.circle(surface, (0, 112, 145), (x, y), radius, 2)
            pygame.draw.circle(surface, (0, 55, 82), (x + 24, y + 12), radius, 2)
        pygame.draw.rect(surface, (0, 37, 50), pygame.Rect(0, height - 105, width, 105))
    elif theme == "aliens":
        for x in range(48, width, 104):
            pygame.draw.line(surface, (0, 66, 72), (width // 2, 175), (x, height), 1)
        for y in range(205, height, 72):
            pygame.draw.line(surface, (0, 47, 57), (0, y), (width, y), 1)
        for x, y in ((120, 190), (310, 270), (760, 210), (905, 340)):
            pygame.draw.circle(surface, SAFE_CYAN, (x, y), 2)
    elif theme == "stars":
        rng = random.Random(1919)
        for _ in range(90):
            point = (rng.randrange(width), rng.randrange(120, height))
            pygame.draw.circle(surface, rng.choice((SAFE_MUTED, SAFE_BLUE, SAFE_CYAN)), point, rng.choice((1, 1, 2)))
        pygame.draw.arc(surface, (0, 90, 130), pygame.Rect(width - 310, height - 250, 380, 300), math.pi, math.tau, 3)
    elif theme == "math":
        for x in range(45, width, 94):
            pygame.draw.line(surface, (0, 72, 87), (x, 140), (x, height), 1)
        for y in range(165, height, 66):
            pygame.draw.line(surface, (0, 72, 87), (0, y), (width, y), 1)
    elif theme == "colors":
        for radius in range(380, 70, -55):
            pygame.draw.circle(surface, (0, 45 + radius // 12, 67 + radius // 10), (width // 2, height // 2 + 48), radius, 2)
    elif theme == "treasure":
        pygame.draw.ellipse(surface, (0, 88, 99), pygame.Rect(-120, 455, width + 240, 330))
        pygame.draw.ellipse(surface, (0, 55, 61), pygame.Rect(70, 390, width - 140, 320))
        for x in range(80, width, 130):
            pygame.draw.line(surface, (0, 106, 98), (x, 420), (x - 55, height), 2)
    _BACKGROUND_CACHE[key] = surface
    return surface


class KidsArcadeBase(BaseApp):
    COUNTDOWN_DURATION = 3.35
    GAME_DURATION = 45.0
    title = "KINDER-ARCADE"
    subtitle = "MIT EINEM SCHUSS STARTEN"
    instructions = ("Ziele mit der Pistole treffen.", "Punkte sammeln.", "Am Ende folgt deine Bestenliste.")
    theme = "stars"
    hit_sound = "target_hit"
    miss_sound = "miss"

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        sounds: Optional[CanGameSounds] = None,
        audio_enabled: bool = True,
        random_seed: int = 1919,
    ) -> None:
        super().__init__(screen)
        self.random = random.Random(random_seed)
        self.sounds = sounds or CanGameSounds(audio_enabled)
        self.hit_tolerance = calibrated_hit_tolerance(screen.get_size())
        self.font_tiny = pygame.font.SysFont("Arial", 14)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22, bold=True)
        self.font_large = pygame.font.SysFont("Arial", 35, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 49, bold=True)
        self.font_countdown = pygame.font.SysFont("Arial", 118, bold=True)
        width, height = screen.get_size()
        self.menu_button = pygame.Rect(width - 170, 22, 142, 44)
        self.start_card = pygame.Rect(0, 0, 670, 360)
        self.start_card.center = (width // 2, height // 2 + 24)
        self.start_button = pygame.Rect(width // 2 - 210, height - 104, 420, 58)
        self.result_card = pygame.Rect(0, 0, 760, 430)
        self.result_card.center = (width // 2, height // 2 + 15)
        self.repeat_button = pygame.Rect(width // 2 - 305, height - 92, 285, 54)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 92, 285, 54)
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.remaining = self.GAME_DURATION
        self.finish_reason = ""
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.last_count_value: Optional[int] = None
        self.recent_hit_zones: list[tuple[pygame.Rect, float]] = []
        self.sparks: list[ArcadeSpark] = []
        self.impact_rings: list[ImpactRing] = []
        self._reset_mode()

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def visual_transition_active(self) -> bool:
        return False

    @property
    def leaderboard_detail(self) -> str:
        return f"{self.hits} TREFFER · {self.accuracy:.0f} %"

    @property
    def leaderboard_metrics(self) -> tuple[tuple[str, str], ...]:
        return (
            ("PUNKTE", str(self.score)),
            ("TREFFER", str(self.hits)),
            ("PRÄZISION", f"{self.accuracy:.0f} %"),
            ("BESTE SERIE", str(self.best_combo)),
        )

    @property
    def result_values(self) -> tuple[tuple[str, str], ...]:
        return self.leaderboard_metrics

    def _reset_mode(self) -> None:
        return

    def _reset_round(self) -> None:
        self.remaining = self.GAME_DURATION
        self.finish_reason = ""
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.combo = 0
        self.best_combo = 0
        self.last_count_value = None
        self.recent_hit_zones = []
        self.sparks = []
        self.impact_rings = []
        self._reset_mode()

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")

    def stop(self) -> None:
        self.sounds.stop_all()

    def begin_countdown(self, now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        return "handled"

    def _begin_play(self, now: float) -> None:
        return

    def _can_accept_shot(self) -> bool:
        return True

    def _moving_target_margin(self, target_radius: int) -> int:
        """Fangrand für bewegte, vom Beamer dargestellte Ziele.

        Der Rand berücksichtigt sowohl die gemessene Waffenabweichung als
        auch die Bewegung zwischen Kamerabild und Spiellogik. Kleine Ziele
        bekommen relativ mehr Hilfe, ohne benachbarte Ziele zu verbinden.
        """

        return min(66, self.hit_tolerance + max(12, round(target_radius * 0.35)))

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        return "miss"

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if nearest_laser_button(pos, (("menu", self.menu_button),)) == "menu":
            self.sounds.play("button")
            return "menu"
        if self.state == "ready":
            if self.start_card.collidepoint(pos) or nearest_laser_button(pos, (("start", self.start_button),)) == "start":
                return self.begin_countdown(current)
            return "handled"
        if self.state == "game_over":
            selected = nearest_laser_button(
                pos,
                (("repeat", self.repeat_button), ("menu", self.result_menu_button)),
                expansion=(260, 220),
                group_rect=self.result_card.inflate(120, 120),
            )
            if selected == "menu":
                return "menu"
            if selected == "repeat":
                return self.begin_countdown(current)
            return "handled"
        if self.state != "playing" or not self._can_accept_shot():
            return "handled"
        self.recent_hit_zones = [(rect, until) for rect, until in self.recent_hit_zones if current < until]
        if any(rect.collidepoint(pos) for rect, _ in self.recent_hit_zones):
            return "handled"
        self.shots += 1
        self.sounds.play("shot")
        return self._handle_game_shot(pos, current)

    def _mark_hit_zone(self, rect: pygame.Rect, now: float) -> None:
        self.recent_hit_zones.append((rect.inflate(28, 28), now + 0.36))

    def _record_hit(
        self,
        points: int,
        pos: Optional[Tuple[int, int]] = None,
        *,
        sound: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        self.hits += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.score += points
        self.sounds.play(sound or self.hit_sound)
        if pos is not None:
            self._spawn_impact(pos, self.last_update if now is None else now)

    def _record_miss(self, penalty: int = 0, *, sound: Optional[str] = None) -> None:
        self.combo = 0
        self.score -= penalty
        self.sounds.play(sound or self.miss_sound)

    def _spawn_impact(
        self,
        pos: Tuple[int, int],
        now: float,
        color: Tuple[int, int, int] = SAFE_GREEN,
    ) -> None:
        self.impact_rings.append(ImpactRing(pos, now, color))
        for index in range(16):
            angle = math.tau * index / 16 + self.random.uniform(-0.12, 0.12)
            speed = self.random.uniform(90.0, 235.0)
            self.sparks.append(
                ArcadeSpark(
                    float(pos[0]),
                    float(pos[1]),
                    math.cos(angle) * speed,
                    math.sin(angle) * speed,
                    now,
                    self.random.uniform(0.28, 0.52),
                    color if index % 3 else SAFE_CYAN,
                )
            )

    def _update_effects(self, now: float, elapsed: float) -> None:
        for spark in self.sparks:
            spark.x += spark.velocity_x * elapsed
            spark.y += spark.velocity_y * elapsed
            spark.velocity_x *= max(0.0, 1.0 - 4.0 * elapsed)
            spark.velocity_y *= max(0.0, 1.0 - 4.0 * elapsed)
        self.sparks = [spark for spark in self.sparks if now - spark.born_at < spark.lifetime]
        self.impact_rings = [ring for ring in self.impact_rings if now - ring.born_at < 0.42]

    def _draw_effects(self, now: float) -> None:
        overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        for ring in self.impact_rings:
            progress = min(1.0, (now - ring.born_at) / 0.42)
            radius = round(12 + progress * 62)
            alpha = round(210 * (1.0 - progress))
            pygame.draw.circle(overlay, (*ring.color, alpha), ring.center, radius, max(2, 5 - round(progress * 3)))
        for spark in self.sparks:
            progress = min(1.0, (now - spark.born_at) / spark.lifetime)
            alpha = round(230 * (1.0 - progress))
            pygame.draw.circle(overlay, (*spark.color, alpha), (round(spark.x), round(spark.y)), max(2, round(5 - progress * 3)))
        self.screen.blit(overlay, (0, 0))

    def _update_game(self, now: float, elapsed: float) -> None:
        return

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        elapsed = max(0.0, min(0.25, current - self.last_update))
        self.last_update = current
        self._update_effects(current, elapsed)
        if self.state == "countdown":
            since_start = current - self.state_started
            count_value = 3 - int(since_start)
            if count_value > 0 and count_value != self.last_count_value:
                self.sounds.play("count")
                self.last_count_value = count_value
            if since_start >= self.COUNTDOWN_DURATION:
                self.state = "playing"
                self.state_started = current
                self.deadline = current + self.GAME_DURATION if self.GAME_DURATION > 0 else 0.0
                self.remaining = self.GAME_DURATION
                self._begin_play(current)
                self.sounds.play("go")
            return
        if self.state != "playing":
            return
        if self.GAME_DURATION > 0:
            self.remaining = max(0.0, self.deadline - current)
            if current >= self.deadline:
                self._finish("Zeit abgelaufen", current)
                return
        self._update_game(current, elapsed)

    def _finish(self, reason: str, now: float) -> None:
        if self.state == "game_over":
            return
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = reason
        self.sounds.play("finish")

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        return

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.screen.blit(_background(self.screen.get_size(), self.theme), (0, 0))
        draw_ambient_background(self.screen, self.theme, current)
        draw_cinematic_overlay(self.screen)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, self.theme, current)
            draw_ready_card(
                self.screen,
                self.title,
                self.subtitle,
                self.instructions,
                self.start_card,
                self.start_button,
                self.menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )
            return
        self._draw_game(current, subdued=self.state == "game_over")
        if self.state == "playing":
            self._draw_effects(current)
        draw_ambient_foreground(self.screen, self.theme, current)
        if self.state == "countdown":
            draw_countdown(self.screen, self.state_started, current, self.font_countdown)
        elif self.state == "game_over":
            draw_result_card(
                self.screen,
                self.result_card,
                "GESAMTAUSWERTUNG",
                self.finish_reason,
                self.result_values,
                self.repeat_button,
                self.result_menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )

    def _draw_title(self) -> None:
        title = self.font_large.render(self.title, True, SAFE_CYAN)
        self.screen.blit(title, (28, 23))
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)


@dataclass
class Balloon:
    x: float
    y: float
    radius: int
    velocity_x: float
    velocity_y: float
    phase: float


class BalloonHuntApp(KidsArcadeBase):
    name = "Ballonjagd"
    title = "BALLONJAGD"
    subtitle = "60 SEKUNDEN · DREI LEBEN"
    instructions = ("Ballons steigen von unten auf.", "Triff sie, bevor sie entkommen.", "Drei entkommene Ballons beenden die Runde.")
    theme = "balloons"
    hit_sound = "balloon_pop"
    GAME_DURATION = 60.0

    def _reset_mode(self) -> None:
        self.balloons: list[Balloon] = []
        self.lives = 3
        self.missed = 0
        self.next_spawn_at = 0.0

    def _begin_play(self, now: float) -> None:
        self.next_spawn_at = now + 0.35

    def _spawn(self, now: float) -> None:
        width, height = self.screen.get_size()
        difficulty = max(0.0, min(1.0, (now - self.state_started) / self.GAME_DURATION))
        radius = self.random.randint(25, 44)
        self.balloons.append(
            Balloon(
                self.random.uniform(radius + 35, width - radius - 35),
                height + radius,
                radius,
                self.random.uniform(-18, 18),
                -self.random.uniform(82 + difficulty * 42, 132 + difficulty * 78),
                self.random.random() * math.tau,
            )
        )

    def _update_game(self, now: float, elapsed: float) -> None:
        if now >= self.next_spawn_at and len(self.balloons) < 6:
            self._spawn(now)
            difficulty = max(0.0, min(1.0, (now - self.state_started) / self.GAME_DURATION))
            self.next_spawn_at = now + self.random.uniform(
                max(0.36, 0.62 - difficulty * 0.22),
                max(0.54, 1.05 - difficulty * 0.38),
            )
        survivors = []
        for balloon in self.balloons:
            balloon.phase += elapsed * 2.2
            balloon.x += (balloon.velocity_x + math.sin(balloon.phase) * 15) * elapsed
            balloon.y += balloon.velocity_y * elapsed
            if balloon.y + balloon.radius < 118:
                self.missed += 1
                self.lives -= 1
                self.combo = 0
            else:
                survivors.append(balloon)
        self.balloons = survivors
        if self.lives <= 0:
            self._finish("Drei Ballons entkommen", now)

    def _balloon_hit_radii(self, balloon: Balloon) -> tuple[float, float]:
        # Muss exakt von der gerenderten Sprite-Größe ausgehen. Der frühere
        # Kreis ließ insbesondere Knoten, Oberkante und bewegte Randtreffer aus.
        margin = self._moving_target_margin(balloon.radius)
        sprite_half_width = balloon.radius + 8
        sprite_half_height = balloon.radius * 1.35 + 14
        return sprite_half_width + margin, sprite_half_height + margin

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        def visible_balloon_hit(balloon: Balloon) -> bool:
            rx, ry = self._balloon_hit_radii(balloon)
            dx = pos[0] - balloon.x
            dy = pos[1] - balloon.y
            return (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) <= 1.0

        hits = [balloon for balloon in self.balloons if visible_balloon_hit(balloon)]
        if not hits:
            self._record_miss()
            return "miss"
        balloon = min(hits, key=lambda item: distance((item.x, item.y), pos))
        rect = pygame.Rect(0, 0, balloon.radius * 2, round(balloon.radius * 2.76))
        rect.center = round(balloon.x), round(balloon.y)
        self.balloons.remove(balloon)
        self._mark_hit_zone(rect, now)
        self._record_hit(
            100 + max(0, 45 - balloon.radius) * 3,
            rect.center,
            now=now,
        )
        return "hit"

    @property
    def result_values(self):
        return (("PUNKTE", str(self.score)), ("BALLONS", str(self.hits)), ("LEBEN", str(self.lives)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("BALLONS", str(self.hits)), ("LEBEN", str(self.lives)), ("ZEIT", f"{self.remaining:04.1f}")), self.font_small, self.font)
        if subdued:
            return
        for index, balloon in enumerate(self.balloons):
            center = round(balloon.x), round(balloon.y)
            sway = math.sin(now * 1.15 + balloon.phase) * 5.0
            sprite = load_target_sprite(
                "balloon",
                (balloon.radius * 2 + 16, round(balloon.radius * 2.7) + 28),
                angle=sway,
                # Der große Glanzpunkt des Ballons darf den Kamerasensor
                # nicht aussteuern. Die dunklere Oberfläche bleibt auf der
                # Nachtkulisse klar sichtbar und lässt dem roten Laser Reserve.
                brightness_limit=108,
            )
            self.screen.blit(sprite, sprite.get_rect(center=center))


@dataclass
class MovingTarget:
    x: float
    y: float
    radius: int
    velocity_x: float
    velocity_y: float
    spawned_at: float
    lifetime: float


class AlienAlarmApp(KidsArcadeBase):
    name = "Alien-Alarm"
    title = "ALIEN-ALARM"
    subtitle = "ALIENS ERSCHEINEN UND FLÜCHTEN"
    instructions = ("Aliens tauchen an zufälligen Stellen auf.", "Sie bewegen sich und verschwinden wieder.", "Kleine schnelle Aliens bringen mehr Punkte.")
    theme = "aliens"
    hit_sound = "alien_hit"
    GAME_DURATION = 45.0

    def _reset_mode(self) -> None:
        self.aliens: list[MovingTarget] = []
        self.escaped = 0
        self.next_spawn_at = 0.0

    def _begin_play(self, now: float) -> None:
        self.next_spawn_at = now + 0.25

    def _spawn(self, now: float) -> None:
        width, height = self.screen.get_size()
        radius = self.random.randint(28, 50)
        self.aliens.append(MovingTarget(self.random.uniform(90, width - 90), self.random.uniform(205, height - 105), radius, self.random.uniform(-105, 105), self.random.uniform(-55, 55), now, self.random.uniform(1.1, 2.2)))

    def _update_game(self, now: float, elapsed: float) -> None:
        if now >= self.next_spawn_at and len(self.aliens) < 4:
            self._spawn(now)
            self.next_spawn_at = now + self.random.uniform(0.42, 0.78)
        survivors = []
        width, height = self.screen.get_size()
        for alien in self.aliens:
            alien.x += alien.velocity_x * elapsed
            alien.y += alien.velocity_y * elapsed
            if alien.x < alien.radius + 28 or alien.x > width - alien.radius - 28:
                alien.velocity_x *= -1
            if alien.y < 180 or alien.y > height - alien.radius - 40:
                alien.velocity_y *= -1
            if now - alien.spawned_at >= alien.lifetime:
                self.escaped += 1
            else:
                survivors.append(alien)
        self.aliens = survivors

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        def visible_alien_hit(alien: Alien) -> bool:
            # Kopf, Körper und Beine des 3D-Aliens bilden eine kompakte,
            # längliche Trefferfläche. Dadurch treffen Randtreffer sichtbar
            # gezeichnete Teile zuverlässig.
            rect = pygame.Rect(
                0,
                0,
                alien.radius * 2 + 26,
                round(alien.radius * 2.65) + 20,
            )
            rect.center = round(alien.x), round(alien.y)
            margin = self._moving_target_margin(alien.radius)
            return rect.inflate(margin * 2, margin * 2).collidepoint(pos)

        candidates = [alien for alien in self.aliens if visible_alien_hit(alien)]
        if not candidates:
            self._record_miss()
            return "miss"
        alien = min(candidates, key=lambda item: distance((item.x, item.y), pos))
        rect = pygame.Rect(0, 0, alien.radius * 2 + 24, round(alien.radius * 2.55))
        rect.center = round(alien.x), round(alien.y)
        self.aliens.remove(alien)
        self._mark_hit_zone(rect, now)
        self._record_hit(
            120 + max(0, 50 - alien.radius) * 4,
            rect.center,
            now=now,
        )
        return "hit"

    @property
    def leaderboard_detail(self):
        return f"{self.hits} ALIENS · {self.escaped} ENTKOMMEN"

    @property
    def result_values(self):
        return (("PUNKTE", str(self.score)), ("ALIENS", str(self.hits)), ("ENTKOMMEN", str(self.escaped)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("ALIENS", str(self.hits)), ("ENTKOMMEN", str(self.escaped)), ("ZEIT", f"{self.remaining:04.1f}")), self.font_small, self.font)
        if subdued:
            return
        for alien in self.aliens:
            center = round(alien.x), round(alien.y)
            tilt = max(-7.0, min(7.0, -alien.velocity_x * 0.045))
            sprite = load_target_sprite(
                "alien",
                (alien.radius * 2 + 26, round(alien.radius * 2.65) + 20),
                flip_x=alien.velocity_x < 0,
                angle=tilt,
                brightness_limit=148,
            )
            self.screen.blit(sprite, sprite.get_rect(center=center))


class StarHuntApp(KidsArcadeBase):
    name = "Sternejagd"
    title = "STERNEJAGD"
    subtitle = "STERNE FINDEN · SCHNELL REAGIEREN"
    instructions = ("Immer ein Stern leuchtet auf.", "Kleine Sterne bringen mehr Punkte.", "Verpasste Sterne verschwinden wieder.")
    theme = "stars"
    hit_sound = "star_hit"
    GAME_DURATION = 45.0

    def _reset_mode(self) -> None:
        self.star: Optional[MovingTarget] = None
        self.missed = 0
        self.next_spawn_at = 0.0

    def _begin_play(self, now: float) -> None:
        self.next_spawn_at = now + 0.35

    def _spawn(self, now: float) -> None:
        width, height = self.screen.get_size()
        radius = self.random.randint(27, 58)
        self.star = MovingTarget(self.random.uniform(radius + 45, width - radius - 45), self.random.uniform(205, height - radius - 55), radius, 0, 0, now, self.random.uniform(0.85, 1.45))

    def _update_game(self, now: float, elapsed: float) -> None:
        if self.star is not None and now - self.star.spawned_at >= self.star.lifetime:
            self.missed += 1
            self.combo = 0
            self.star = None
            self.next_spawn_at = now + self.random.uniform(0.18, 0.42)
        if self.star is None and now >= self.next_spawn_at:
            self._spawn(now)

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        if self.star is None:
            self._record_miss()
            return "miss"
        margin = self._moving_target_margin(self.star.radius)
        visible_rect = pygame.Rect(
            0,
            0,
            self.star.radius * 2 + 12,
            self.star.radius * 2 + 12,
        )
        visible_rect.center = round(self.star.x), round(self.star.y)
        if not visible_rect.inflate(margin * 2, margin * 2).collidepoint(pos):
            self._record_miss()
            return "miss"
        star = self.star
        rect = pygame.Rect(0, 0, star.radius * 2, star.radius * 2)
        rect.center = round(star.x), round(star.y)
        self.star = None
        self.next_spawn_at = now + self.random.uniform(0.15, 0.34)
        self._mark_hit_zone(rect, now)
        self._record_hit(
            100 + max(0, 60 - star.radius) * 4,
            rect.center,
            now=now,
        )
        return "hit"

    @property
    def result_values(self):
        return (("PUNKTE", str(self.score)), ("STERNE", str(self.hits)), ("VERPASST", str(self.missed)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    def _draw_star(self, center: Tuple[int, int], radius: int) -> None:
        glow = pygame.Surface((radius * 3, radius * 3), pygame.SRCALPHA)
        glow_center = glow.get_rect().center
        for ring_radius, alpha in ((radius + 18, 12), (radius + 10, 20), (radius + 4, 28)):
            pygame.draw.circle(glow, (*TARGET_CYAN, alpha), glow_center, ring_radius, 5)
        self.screen.blit(glow, glow.get_rect(center=center))
        sprite = load_target_sprite(
            "star",
            (radius * 2 + 12, radius * 2 + 12),
            brightness_limit=146,
        )
        self.screen.blit(sprite, sprite.get_rect(center=center))

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("STERNE", str(self.hits)), ("VERPASST", str(self.missed)), ("ZEIT", f"{self.remaining:04.1f}")), self.font_small, self.font)
        if not subdued and self.star is not None:
            self._draw_star((round(self.star.x), round(self.star.y)), self.star.radius)


class MathDuelApp(KidsArcadeBase):
    name = "Rechenduell"
    title = "RECHENDUELL"
    subtitle = "60 SEKUNDEN · KLEINES EINMALEINS"
    instructions = ("Löse die Aufgabe oben.", "Schieße auf eine von vier Antworten.", "Richtig gibt 100, falsch kostet 25 Punkte.")
    theme = "math"
    hit_sound = "math_correct"
    miss_sound = "math_wrong"
    GAME_DURATION = 60.0

    def __init__(self, screen: pygame.Surface, **kwargs) -> None:
        super().__init__(screen, **kwargs)
        width = screen.get_width()
        self.answer_rects = (
            pygame.Rect(105, 300, 360, 128), pygame.Rect(width - 465, 300, 360, 128),
            pygame.Rect(105, 464, 360, 128), pygame.Rect(width - 465, 464, 360, 128),
        )

    def _reset_mode(self) -> None:
        self.factor_a = 1
        self.factor_b = 1
        self.answers = [1, 2, 3, 4]
        self.correct_index = 0
        self.questions = 0
        self.wrong = 0
        self.feedback: Optional[tuple[str, Tuple[int, int, int], float]] = None

    def _begin_play(self, now: float) -> None:
        self._new_question()

    def _new_question(self) -> None:
        self.factor_a = self.random.randint(1, 12)
        self.factor_b = self.random.randint(1, 12)
        correct = self.factor_a * self.factor_b
        wrong = set()
        while len(wrong) < 3:
            candidate = max(1, correct + self.random.choice((-12, -10, -8, -6, -5, -4, -3, -2, 2, 3, 4, 5, 6, 8, 10, 12)))
            if candidate != correct:
                wrong.add(candidate)
        self.answers = [correct, *wrong]
        self.random.shuffle(self.answers)
        self.correct_index = self.answers.index(correct)
        self.questions += 1

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        # Projektions- und Waffenabweichung gilt auch an den sichtbaren
        # Kartenrändern. Bei Überlappung ordnet nearest_laser_button weiterhin
        # eindeutig die nächstgelegene Antwort zu.
        selected = nearest_laser_button(pos, tuple(enumerate(self.answer_rects)), expansion=(72, 58))
        if selected is None:
            self._record_miss()
            return "miss"
        rect = self.answer_rects[selected]
        self._mark_hit_zone(rect, now)
        if selected == self.correct_index:
            self._record_hit(100, rect.center, sound="math_correct", now=now)
            self.feedback = ("RICHTIG · +100", SAFE_GREEN, now)
            result = "correct"
        else:
            self.wrong += 1
            self._record_miss(25, sound="math_wrong")
            self.feedback = (f"FALSCH · RICHTIG WAR {self.answers[self.correct_index]}", SAFE_CYAN, now)
            result = "wrong"
        self._new_question()
        return result

    @property
    def leaderboard_detail(self):
        return f"{self.hits} RICHTIG · {self.wrong} FALSCH"

    @property
    def result_values(self):
        return (("PUNKTE", str(self.score)), ("RICHTIG", str(self.hits)), ("FALSCH", str(self.wrong)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("RICHTIG", str(self.hits)), ("FALSCH", str(self.wrong)), ("ZEIT", f"{self.remaining:04.1f}")), self.font_small, self.font)
        if subdued:
            return
        question = self.font_title.render(f"{self.factor_a} × {self.factor_b} = ?", True, WHITE)
        question_panel = pygame.Rect(0, 0, 390, 84)
        question_panel.midtop = (self.screen.get_width() // 2, 174)
        draw_translucent_panel(self.screen, question_panel, (0, 12, 28), alpha=218, border_radius=18)
        pygame.draw.rect(self.screen, SAFE_CYAN, question_panel, 3, border_radius=18)
        self.screen.blit(question, question.get_rect(center=question_panel.center))
        for index, rect in enumerate(self.answer_rects):
            shadow = rect.inflate(12, 12)
            draw_translucent_panel(self.screen, shadow, (0, 4, 13), alpha=185, border_radius=22)
            draw_translucent_panel(self.screen, rect, SAFE_PANEL, alpha=218, border_radius=18)
            # Alle vier Antworten sehen vor dem Schuss exakt gleich aus. Eine
            # grüne Umrandung wäre eine falsche visuelle Lösungshilfe.
            pygame.draw.rect(self.screen, TARGET_CYAN, rect, 3, border_radius=18)
            badge = pygame.Rect(rect.left + 14, rect.top + 14, 38, 38)
            pygame.draw.circle(self.screen, (0, 11, 24), badge.center, 19)
            pygame.draw.circle(self.screen, TARGET_BLUE, badge.center, 19, 2)
            badge_text = self.font_small.render(chr(65 + index), True, TARGET_TEXT)
            self.screen.blit(badge_text, badge_text.get_rect(center=badge.center))
            value = self.font_title.render(str(self.answers[index]), True, TARGET_TEXT)
            self.screen.blit(value, value.get_rect(center=rect.center))
        if self.feedback and now - self.feedback[2] < 0.7:
            text = self.font.render(self.feedback[0], True, self.feedback[1])
            self.screen.blit(text, text.get_rect(midtop=(self.screen.get_width() // 2, 620)))


class ColorMemoryApp(KidsArcadeBase):
    name = "Farbenspiel"
    title = "FARBENSPIEL"
    subtitle = "MERKEN · WIEDERHOLEN · LEVEL STEIGERN"
    instructions = ("Beobachte die leuchtende Folge.", "Wiederhole sie mit der Pistole.", "Drei Fehler beenden das Spiel.")
    theme = "colors"
    hit_sound = "color_level"
    miss_sound = "math_wrong"
    GAME_DURATION = 0.0
    MAX_ROUNDS = 10
    SHOW_SLOT = 0.72

    def __init__(self, screen: pygame.Surface, **kwargs) -> None:
        super().__init__(screen, **kwargs)
        width, height = screen.get_size()
        self.pad_rects = (
            pygame.Rect(130, 220, 340, 200), pygame.Rect(width - 470, 220, 340, 200),
            pygame.Rect(130, 455, 340, 200), pygame.Rect(width - 470, 455, 340, 200),
        )

    def _reset_mode(self) -> None:
        self.sequence: list[int] = []
        self.input_index = 0
        self.completed_rounds = 0
        self.errors = 0
        self.phase = "waiting"
        self.show_started = 0.0
        self.pause_until = 0.0
        self.advance_after_pause = True

    @property
    def visual_transition_active(self) -> bool:
        return self.state == "playing" and self.phase == "showing"

    def _begin_play(self, now: float) -> None:
        self._start_sequence(now, add=True)

    def _start_sequence(self, now: float, *, add: bool) -> None:
        if add:
            self.sequence.append(self.random.randrange(4))
        self.input_index = 0
        self.phase = "showing"
        self.show_started = now

    def _can_accept_shot(self) -> bool:
        return self.phase == "input"

    def _active_pad(self, now: float) -> Optional[int]:
        if self.phase != "showing":
            return None
        elapsed = now - self.show_started
        index = int(elapsed / self.SHOW_SLOT)
        if index >= len(self.sequence) or elapsed % self.SHOW_SLOT > 0.43:
            return None
        return self.sequence[index]

    def _update_game(self, now: float, elapsed: float) -> None:
        if self.phase == "showing" and now - self.show_started >= len(self.sequence) * self.SHOW_SLOT + 0.18:
            self.phase = "input"
            self.input_index = 0
        elif self.phase == "pause" and now >= self.pause_until:
            self._start_sequence(now, add=self.advance_after_pause)

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        selected = nearest_laser_button(pos, tuple(enumerate(self.pad_rects)), expansion=(72, 58))
        if selected is None:
            self._record_miss()
            return "miss"
        self._mark_hit_zone(self.pad_rects[selected], now)
        expected = self.sequence[self.input_index]
        if selected != expected:
            self.errors += 1
            self._record_miss(50, sound="math_wrong")
            if self.errors >= 3:
                self._finish("Drei Folgen verwechselt", now)
                return "wrong"
            self.phase = "pause"
            self.pause_until = now + 0.8
            self.advance_after_pause = False
            return "wrong"
        self._record_hit(
            25,
            self.pad_rects[selected].center,
            sound=f"color{selected + 1}",
            now=now,
        )
        self.input_index += 1
        if self.input_index >= len(self.sequence):
            self.completed_rounds += 1
            self.score += len(self.sequence) * 75
            self.sounds.play("color_level")
            if self.completed_rounds >= self.MAX_ROUNDS:
                self._finish("Alle zehn Level geschafft", now)
                return "complete"
            self.phase = "pause"
            self.pause_until = now + 0.8
            self.advance_after_pause = True
        return "correct"

    @property
    def leaderboard_detail(self):
        return f"LEVEL {self.completed_rounds} · {self.errors} FEHLER"

    @property
    def leaderboard_metrics(self):
        return (("PUNKTE", str(self.score)), ("LEVEL", str(self.completed_rounds)), ("FEHLER", str(self.errors)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    @property
    def result_values(self):
        return self.leaderboard_metrics

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        status = "ZUSCHAUEN" if self.phase == "showing" else "JETZT WIEDERHOLEN" if self.phase == "input" else "NÄCHSTES LEVEL"
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("LEVEL", str(self.completed_rounds + 1)), ("FOLGE", str(len(self.sequence))), ("FEHLER", f"{self.errors}/3"), ("STATUS", status)), self.font_small, self.font)
        active = None if subdued else self._active_pad(now)
        colors = (TARGET_CYAN, TARGET_GREEN, TARGET_BLUE, TARGET_TEAL)
        labels = ("BLAU", "GRÜN", "TÜRKIS", "HELLBLAU")
        for index, rect in enumerate(self.pad_rects):
            color = colors[index]
            fill = (0, 64, 73) if active == index else (0, 13, 27)
            shadow = rect.inflate(12, 12)
            draw_translucent_panel(self.screen, shadow, (0, 3, 12), alpha=185, border_radius=25)
            draw_translucent_panel(self.screen, rect, fill, alpha=242 if active == index else 216, border_radius=22)
            pygame.draw.rect(self.screen, color, rect, 5 if active == index else 3, border_radius=22)
            pygame.draw.circle(self.screen, (0, 6, 18), (rect.left + 35, rect.centery), 17)
            pygame.draw.circle(self.screen, color, (rect.left + 35, rect.centery), 17, 3)
            number = self.font_small.render(str(index + 1), True, color)
            self.screen.blit(number, number.get_rect(center=(rect.left + 35, rect.centery)))
            label = self.font_large.render(labels[index], True, color)
            self.screen.blit(label, label.get_rect(center=rect.center))


@dataclass
class TreasureObject:
    kind: str
    center: Tuple[int, int]
    radius: int


class TreasureHuntApp(KidsArcadeBase):
    name = "Schatzsuche"
    title = "SCHATZSUCHE"
    subtitle = "FINDE DEN GESUCHTEN PIRATENSCHATZ"
    instructions = ("Oben steht der gesuchte Gegenstand.", "Finde ihn zwischen den Ablenkungen.", "Zehn gefundene Schätze gewinnen.")
    theme = "treasure"
    hit_sound = "treasure_found"
    miss_sound = "treasure_wrong"
    GAME_DURATION = 60.0
    TOTAL_LEVELS = 10
    KINDS = ("SCHLÜSSEL", "KRONE", "MÜNZE", "KOMPASS", "JUWEL", "FLASCHE", "ANKER", "FERNROHR", "KARTE")

    def _reset_mode(self) -> None:
        self.objects: list[TreasureObject] = []
        self.target_kind = "SCHLÜSSEL"
        self.found = 0
        self.wrong = 0
        self.level_shots = 0

    def _begin_play(self, now: float) -> None:
        self._new_level()

    def _new_level(self) -> None:
        positions = [(190 + column * 320, 275 + row * 170) for row in range(3) for column in range(3)]
        kinds = list(self.KINDS)
        self.random.shuffle(kinds)
        self.random.shuffle(positions)
        # Die Motive bleiben in allen zehn Leveln gleich groß und eindeutig.
        # Schwierigkeit entsteht durch die wechselnden Positionen und die Zeit,
        # nicht durch Symbole, die aus Projektorentfernung zu klein werden.
        radius = 62
        self.objects = [TreasureObject(kind, position, radius) for kind, position in zip(kinds, positions)]
        self.target_kind = self.random.choice(kinds)
        self.level_shots = 0

    def _handle_game_shot(self, pos: Tuple[int, int], now: float) -> str:
        self.level_shots += 1
        candidates = [
            obj
            for obj in self.objects
            if distance(obj.center, pos)
            <= obj.radius + self._moving_target_margin(obj.radius)
        ]
        if not candidates:
            self.wrong += 1
            self._record_miss(20, sound="treasure_wrong")
            return "miss"
        obj = min(candidates, key=lambda item: distance(item.center, pos))
        rect = pygame.Rect(0, 0, obj.radius * 2, obj.radius * 2)
        rect.center = obj.center
        self._mark_hit_zone(rect, now)
        if obj.kind != self.target_kind:
            self.wrong += 1
            self._record_miss(25, sound="treasure_wrong")
            return "wrong"
        bonus = max(0, 4 - self.level_shots) * 25
        self._record_hit(
            150 + bonus,
            obj.center,
            sound="treasure_found",
            now=now,
        )
        self.found += 1
        if self.found >= self.TOTAL_LEVELS:
            self._finish("Alle zehn Schätze gefunden", now)
            return "complete"
        self._new_level()
        return "found"

    @property
    def leaderboard_detail(self):
        return f"{self.found}/{self.TOTAL_LEVELS} SCHÄTZE · {self.wrong} FEHLER"

    @property
    def result_values(self):
        return (("PUNKTE", str(self.score)), ("SCHÄTZE", f"{self.found}/{self.TOTAL_LEVELS}"), ("FEHLER", str(self.wrong)), ("PRÄZISION", f"{self.accuracy:.0f} %"))

    def _draw_treasure_symbol(self, kind: str, center: Tuple[int, int], r: int) -> None:
        """Zeichnet eine große, kanonische Silhouette ohne roten Farbanteil."""
        x, y = center
        dark = (0, 9, 19)
        fill = (0, 72, 91)
        detail = TARGET_GREEN

        if kind == "SCHLÜSSEL":
            ring_center = (x - round(r * 0.32), y - round(r * 0.18))
            pygame.draw.circle(self.screen, TARGET_CYAN, ring_center, round(r * 0.27))
            pygame.draw.circle(self.screen, dark, ring_center, round(r * 0.13))
            pygame.draw.line(self.screen, detail, (x - round(r * 0.10), y), (x + round(r * 0.48), y + round(r * 0.42)), max(6, r // 7))
            pygame.draw.line(self.screen, TARGET_CYAN, (x + round(r * 0.27), y + round(r * 0.27)), (x + round(r * 0.45), y + round(r * 0.05)), max(4, r // 11))
            pygame.draw.line(self.screen, TARGET_CYAN, (x + round(r * 0.39), y + round(r * 0.36)), (x + round(r * 0.55), y + round(r * 0.18)), max(4, r // 11))
        elif kind == "KRONE":
            points = (
                (x-r//2, y+r//3), (x-r//2, y-r//3),
                (x-r//5, y-r//10), (x, y-r//2),
                (x+r//5, y-r//10), (x+r//2, y-r//3),
                (x+r//2, y+r//3),
            )
            pygame.draw.polygon(self.screen, fill, points)
            pygame.draw.lines(self.screen, TARGET_CYAN, True, points, max(3, r // 15))
            pygame.draw.rect(self.screen, detail, pygame.Rect(x-r//2, y+r//6, r, r//5), border_radius=3)
            for jewel_x in (x-r//3, x, x+r//3):
                pygame.draw.circle(self.screen, dark, (jewel_x, y+r//4), max(3, r//16))
        elif kind == "MÜNZE":
            pygame.draw.circle(self.screen, fill, (x, y), round(r * 0.52))
            pygame.draw.circle(self.screen, TARGET_CYAN, (x, y), round(r * 0.52), max(4, r//12))
            pygame.draw.circle(self.screen, detail, (x, y), round(r * 0.37), 3)
            coin_text = self.font.render("1", True, TARGET_CYAN)
            self.screen.blit(coin_text, coin_text.get_rect(center=(x, y)))
        elif kind == "KOMPASS":
            pygame.draw.circle(self.screen, fill, (x, y), round(r * 0.52))
            pygame.draw.circle(self.screen, TARGET_CYAN, (x, y), round(r * 0.52), max(4, r//12))
            pygame.draw.circle(self.screen, detail, (x, y), round(r * 0.39), 2)
            needle = ((x, y-r//2), (x+r//7, y), (x, y+r//2), (x-r//7, y))
            pygame.draw.polygon(self.screen, detail, needle)
            pygame.draw.line(self.screen, dark, (x, y-r//2+4), (x, y+r//2-4), 3)
            pygame.draw.circle(self.screen, TARGET_CYAN, (x, y), max(4, r//12))
            north = self.font_tiny.render("N", True, TARGET_CYAN)
            self.screen.blit(north, north.get_rect(midbottom=(x, y-r//2-2)))
        elif kind == "JUWEL":
            points = ((x-r//3,y-r//3),(x+r//3,y-r//3),(x+r//2,y-r//10),(x,y+r//2),(x-r//2,y-r//10))
            pygame.draw.polygon(self.screen, fill, points)
            pygame.draw.lines(self.screen, TARGET_CYAN, True, points, max(3, r//15))
            pygame.draw.line(self.screen, detail, (x-r//3,y-r//3), (x,y+r//2), 3)
            pygame.draw.line(self.screen, detail, (x+r//3,y-r//3), (x,y+r//2), 3)
            pygame.draw.line(self.screen, detail, (x-r//2,y-r//10), (x+r//2,y-r//10), 3)
            pygame.draw.polygon(self.screen, TARGET_BLUE, ((x-r//3,y-r//3),(x,y-r//10),(x+r//3,y-r//3)))
        elif kind == "FLASCHE":
            body = pygame.Rect(x-r//3, y-r//5, round(r*0.66), round(r*0.78))
            pygame.draw.rect(self.screen, fill, body, border_radius=max(7, r//8))
            pygame.draw.rect(self.screen, TARGET_CYAN, body, max(3, r//15), border_radius=max(7, r//8))
            neck = pygame.Rect(x-r//7, y-r//2, round(r*0.28), round(r*0.42))
            pygame.draw.rect(self.screen, (0, 86, 77), neck, border_radius=4)
            pygame.draw.rect(self.screen, detail, neck, max(3, r//16), border_radius=4)
            pygame.draw.line(self.screen, TARGET_CYAN, (neck.left-2, neck.top+2), (neck.right+2, neck.top+2), 5)
            scroll = pygame.Rect(x-r//5, y+r//15, round(r*0.4), round(r*0.22))
            pygame.draw.rect(self.screen, detail, scroll, border_radius=3)
            pygame.draw.line(self.screen, dark, (scroll.left+5, scroll.centery), (scroll.right-5, scroll.centery), 2)
        elif kind == "ANKER":
            pygame.draw.circle(self.screen, TARGET_CYAN, (x, y-r//3), r//6, max(4, r//12))
            pygame.draw.line(self.screen, detail, (x,y-r//6), (x,y+r//2), max(7, r//8))
            pygame.draw.line(self.screen, TARGET_CYAN, (x-r//3,y-r//10), (x+r//3,y-r//10), max(6, r//10))
            pygame.draw.arc(self.screen, TARGET_CYAN, pygame.Rect(x-r//2,y-r//8,r,r), 0, math.pi, max(6, r//10))
            pygame.draw.polygon(self.screen, TARGET_CYAN, ((x-r//2,y+r//3),(x-r//2,y+r//2),(x-r//3,y+r//2)))
            pygame.draw.polygon(self.screen, TARGET_CYAN, ((x+r//2,y+r//3),(x+r//2,y+r//2),(x+r//3,y+r//2)))
        elif kind == "FERNROHR":
            tube = ((x-r//2,y+r//5),(x+r//2,y-r//3),(x+r//2+6,y-r//8),(x-r//2+6,y+r//2))
            pygame.draw.polygon(self.screen, fill, tube)
            pygame.draw.lines(self.screen, TARGET_CYAN, True, tube, max(3, r//15))
            pygame.draw.circle(self.screen, detail, (x+r//2+4,y-r//4), r//5, max(4, r//12))
            pygame.draw.line(self.screen, detail, (x-r//3,y+r//9), (x-r//4,y+r//3), max(5, r//10))
            pygame.draw.line(self.screen, TARGET_BLUE, (x+r//7,y-r//8), (x+r//4,y+r//9), max(4, r//12))
        else:  # KARTE
            map_points = (
                (x-r//2,y-r//3),(x-r//6,y-r//2),(x+r//6,y-r//3),
                (x+r//2,y-r//2),(x+r//2,y+r//3),(x+r//6,y+r//2),
                (x-r//6,y+r//3),(x-r//2,y+r//2),
            )
            pygame.draw.polygon(self.screen, fill, map_points)
            pygame.draw.lines(self.screen, TARGET_CYAN, True, map_points, max(3, r//15))
            pygame.draw.line(self.screen, TARGET_BLUE, (x-r//6,y-r//2), (x-r//6,y+r//3), 3)
            pygame.draw.line(self.screen, TARGET_BLUE, (x+r//6,y-r//3), (x+r//6,y+r//2), 3)
            route = ((x-r//3,y+r//5),(x-r//8,y),(x+r//8,y+r//8),(x+r//3,y-r//5))
            pygame.draw.lines(self.screen, detail, False, route, 4)
            pygame.draw.line(self.screen, TARGET_CYAN, (x+r//4,y-r//4), (x+r//2,y), 4)
            pygame.draw.line(self.screen, TARGET_CYAN, (x+r//2,y-r//4), (x+r//4,y), 4)

    def _draw_object(self, obj: TreasureObject) -> None:
        x, y = obj.center
        r = obj.radius
        plate_radius = max(32, round(r * 0.91))
        # Vollflächiges dunkles Medaillon: Der Laser trifft dadurch auf jedem
        # sichtbaren Motivteil eine kontrollierte Fläche statt auf die helle
        # Hintergrundgrafik. Alle Farbkanäle behalten Sättigungsreserve.
        pygame.draw.circle(self.screen, (0, 4, 13), (x + 4, y + 6), plate_radius + 7)
        pygame.draw.circle(self.screen, (0, 20, 31), (x, y), plate_radius + 4)
        pygame.draw.circle(self.screen, TARGET_CYAN, (x, y), plate_radius + 4, 3)
        pygame.draw.circle(self.screen, (0, 31, 43), (x, y), plate_radius - 2)
        pygame.draw.circle(self.screen, TARGET_BLUE, (x, y), plate_radius - 3, 2)
        for angle in range(0, 360, 90):
            radians = math.radians(angle)
            marker = (
                x + round(math.cos(radians) * (plate_radius + 4)),
                y + round(math.sin(radians) * (plate_radius + 4)),
            )
            pygame.draw.circle(self.screen, TARGET_GREEN, marker, 3)

        self._draw_treasure_symbol(obj.kind, obj.center, round(r * 0.82))

        # Eine kleine Bezeichnung unterstützt Kinder und größere
        # Projektionsabstände, ohne das Motiv zu verdecken.
        label = self.font_small.render(obj.kind, True, TARGET_CYAN)
        label_rect = label.get_rect(midtop=(x, y + plate_radius + 5)).inflate(16, 7)
        pygame.draw.rect(self.screen, (0, 9, 20), label_rect, border_radius=6)
        pygame.draw.rect(self.screen, TARGET_BLUE, label_rect, 1, border_radius=6)
        self.screen.blit(label, label.get_rect(center=label_rect.center))

    def _draw_game(self, now: float, *, subdued: bool = False) -> None:
        self._draw_title()
        draw_hud(self.screen, (("PUNKTE", str(self.score)), ("SCHÄTZE", f"{self.found}/{self.TOTAL_LEVELS}"), ("FEHLER", str(self.wrong)), ("ZEIT", f"{self.remaining:04.1f}")), self.font_small, self.font)
        prompt = self.font_large.render(f"FINDE: {self.target_kind}", True, SAFE_GREEN)
        panel = prompt.get_rect(midtop=(self.screen.get_width() // 2 + 28, 153)).inflate(112, 26)
        draw_translucent_panel(self.screen, panel, SAFE_PANEL, alpha=216, border_radius=12)
        pygame.draw.rect(self.screen, SAFE_CYAN, panel, 2, border_radius=12)
        icon_center = (panel.left + 43, panel.centery)
        pygame.draw.circle(self.screen, (0, 16, 27), icon_center, 32)
        pygame.draw.circle(self.screen, TARGET_CYAN, icon_center, 32, 2)
        self._draw_treasure_symbol(self.target_kind, icon_center, 49)
        self.screen.blit(prompt, prompt.get_rect(midleft=(panel.left + 83, panel.centery)))
        if not subdued:
            for obj in self.objects:
                self._draw_object(obj)
