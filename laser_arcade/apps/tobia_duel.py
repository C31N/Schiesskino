from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    SAFE_CYAN,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    calibrated_hit_tolerance,
    draw_button,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_cinematic_overlay,
    draw_countdown,
    draw_translucent_panel,
    limit_projected_brightness,
    nearest_laser_button,
)
from .base import BaseApp
from .cans import CanGameSounds

LOGGER = logging.getLogger(__name__)

WHITE = (225, 250, 255)
DEEP_BLUE = (0, 12, 30)


@dataclass
class PhotoTarget:
    kind: str
    rect: pygame.Rect
    spawned_at: float
    lifetime: float
    x: float
    y: float
    velocity_x: float
    velocity_y: float

    @property
    def speed(self) -> float:
        return (self.velocity_x * self.velocity_x + self.velocity_y * self.velocity_y) ** 0.5


class TobiaDuelApp(BaseApp):
    """Verstecktes Foto-Reaktionsspiel für Tobia."""

    name = "Tobias Blitzduell"
    COUNTDOWN_DURATION = 3.35
    GAME_DURATION = 45.0
    TARGET_LIFETIME_MIN = 0.78
    TARGET_LIFETIME_MAX = 1.30
    CAMERA_LATENCY_ALLOWANCE = 0.24
    RABBIT_POINTS = 50
    PERSON_POINTS = -100

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        sounds: Optional[CanGameSounds] = None,
        audio_enabled: bool = True,
        random_seed: int = 141919,
    ) -> None:
        super().__init__(screen)
        self.random = random.Random(random_seed)
        self.sounds = sounds or CanGameSounds(audio_enabled)
        self.hit_tolerance = calibrated_hit_tolerance(screen.get_size())
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 23, bold=True)
        self.font_large = pygame.font.SysFont("Arial", 36, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 50, bold=True)
        self.font_countdown = pygame.font.SysFont("Arial", 122, bold=True)
        width, height = screen.get_size()
        self.target_size = max(176, min(222, round(width * 0.215)))
        self.target_size_min = max(132, round(self.target_size * 0.64))
        self.target_size_max = min(248, round(self.target_size * 1.10))
        self.photo_source_size = max(300, self.target_size_max)
        self.background, self.rabbit_photo, self.person_photo = self._load_assets()
        self.photo_cache: dict[tuple[str, int], pygame.Surface] = {}

        self.menu_button = pygame.Rect(width - 164, 22, 136, 44)
        self.start_card = pygame.Rect(92, 126, width - 184, height - 268)
        self.start_button = pygame.Rect(width // 2 - 220, height - 110, 440, 60)
        self.result_card = pygame.Rect(72, 108, width - 144, height - 238)
        self.repeat_button = pygame.Rect(width // 2 - 305, height - 92, 285, 54)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 92, 285, 54)
        self.play_bounds = pygame.Rect(34, 126, width - 68, height - 258)
        top_y = self.play_bounds.top + self.play_bounds.height // 3
        bottom_y = self.play_bounds.top + 2 * self.play_bounds.height // 3
        self.target_centers = [
            (round(width * fraction), y)
            for y in (top_y, bottom_y)
            for fraction in (0.18, 0.5, 0.82)
        ]

        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.next_target_at = 0.0
        self.remaining = self.GAME_DURATION
        self.target: Optional[PhotoTarget] = None
        self.last_target_index: Optional[int] = None
        self.score = 0
        self.shots = 0
        self.rabbit_hits = 0
        self.person_hits = 0
        self.rabbit_misses = 0
        self.targets_seen = 0
        self.finish_reason = ""
        self.last_count_value: Optional[int] = None
        self.feedback: Optional[tuple[str, Tuple[int, int, int], float]] = None
        self.recent_hit_zones: list[tuple[pygame.Rect, float]] = []

    @property
    def accuracy(self) -> float:
        return 100.0 * self.rabbit_hits / self.shots if self.shots else 0.0

    @property
    def visual_transition_active(self) -> bool:
        return False

    @staticmethod
    def _neutralize_red_pixels(surface: pygame.Surface) -> None:
        """Entfernt Rotüberschuss, ohne Motiv oder Zuschnitt zu verändern."""

        pixels = pygame.surfarray.pixels3d(surface)
        red = pixels[:, :, 0]
        other = pygame.surfarray.array3d(surface)[:, :, 1:].max(axis=2)
        mask = (red >= 68) & ((red.astype("int16") - other.astype("int16")) >= 22)
        red[mask] = other[mask]
        del pixels

    @staticmethod
    def _cover_crop(surface: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
        target_width, target_height = size
        scale = max(target_width / surface.get_width(), target_height / surface.get_height())
        scaled = pygame.transform.smoothscale(
            surface,
            (
                max(target_width, round(surface.get_width() * scale)),
                max(target_height, round(surface.get_height() * scale)),
            ),
        )
        crop = pygame.Rect(0, 0, target_width, target_height)
        crop.center = scaled.get_rect().center
        return scaled.subsurface(crop).copy()

    def _load_assets(self) -> tuple[pygame.Surface, pygame.Surface, pygame.Surface]:
        root = Path(__file__).resolve().parents[2]
        asset_dir = root / "assets" / "tobia_duel"
        width, height = self.screen.get_size()

        try:
            background = pygame.image.load(str(asset_dir / "reaction_arena_v3.png")).convert()
            background = pygame.transform.smoothscale(background, (width, height))
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Tobia-Kulisse fehlt: %s", exc)
            background = pygame.Surface((width, height))
            background.fill(DEEP_BLUE)
        self._neutralize_red_pixels(background)
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((0, 7, 20, 52))
        background.blit(shade, (0, 0))

        photos: list[pygame.Surface] = []
        for label, filename in (
            ("Kaninchen", "rabbit_original.jpeg"),
            ("Tobia", "tobia_original.jpeg"),
        ):
            try:
                original = pygame.image.load(str(asset_dir / filename)).convert()
                photo = self._cover_crop(
                    original, (self.photo_source_size, self.photo_source_size)
                )
                # Motiv und Originaldatei bleiben gleich. Die Spielkopie wird
                # bewusst kühl und dunkler dargestellt: So hat der rote Laser
                # auf weißem Fell und heller Haut genügend Helligkeitsreserve.
                photo = pygame.transform.grayscale(photo)
                photo.fill((145, 175, 205), special_flags=pygame.BLEND_RGB_MULT)
                self._neutralize_red_pixels(photo)
                limit_projected_brightness(photo, 165)
            except (FileNotFoundError, pygame.error, ValueError) as exc:
                LOGGER.warning("%s-Foto fehlt: %s", label, exc)
                photo = pygame.Surface((self.photo_source_size, self.photo_source_size))
                photo.fill((25, 75, 105))
            photos.append(photo)
        return background, photos[0], photos[1]

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        LOGGER.info("Tobias Blitzduell geöffnet")

    def stop(self) -> None:
        self.sounds.stop_all()
        self.target = None

    def _reset_round(self) -> None:
        self.remaining = self.GAME_DURATION
        self.target = None
        self.last_target_index = None
        self.score = 0
        self.shots = 0
        self.rabbit_hits = 0
        self.person_hits = 0
        self.rabbit_misses = 0
        self.targets_seen = 0
        self.finish_reason = ""
        self.last_count_value = None
        self.feedback = None
        self.recent_hit_zones = []
        self.next_target_at = 0.0

    def begin_countdown(self, now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        return "handled"

    def _spawn_target(self, now: float, kind: Optional[str] = None) -> PhotoTarget:
        choices = [i for i in range(len(self.target_centers)) if i != self.last_target_index]
        index = self.random.choice(choices)
        self.last_target_index = index
        chosen_kind = kind or ("rabbit" if self.random.random() < 0.70 else "person")
        progress = max(
            0.0,
            min(1.0, (now - self.state_started) / max(1.0, self.GAME_DURATION)),
        )
        size_low = max(124, round(self.target_size_min - progress * 12))
        size_high = max(size_low + 18, round(self.target_size_max - progress * 6))
        size = self.random.randint(size_low, size_high)
        center_x, center_y = self.target_centers[index]
        half = size / 2.0
        center_x = max(
            self.play_bounds.left + half,
            min(self.play_bounds.right - half, center_x),
        )
        center_y = max(
            self.play_bounds.top + half,
            min(self.play_bounds.bottom - half, center_y),
        )
        rect = pygame.Rect(0, 0, size, size)
        rect.center = round(center_x), round(center_y)

        speed = self.random.uniform(
            62.0 + progress * 45.0,
            168.0 + progress * 105.0,
        )
        direction_x = self.random.choice((-1.0, 1.0))
        horizontal_share = self.random.uniform(0.72, 0.96)
        velocity_x = direction_x * speed * horizontal_share
        velocity_y = (
            self.random.choice((-1.0, 1.0))
            * speed
            * (1.0 - horizontal_share)
        )
        lifetime_center = 1.18 - progress * 0.18
        lifetime = max(
            self.TARGET_LIFETIME_MIN,
            min(
                self.TARGET_LIFETIME_MAX,
                self.random.uniform(
                    lifetime_center - 0.18,
                    lifetime_center + 0.16,
                ),
            ),
        )
        self.target = PhotoTarget(
            chosen_kind,
            rect,
            now,
            lifetime,
            float(rect.centerx),
            float(rect.centery),
            velocity_x,
            velocity_y,
        )
        self.targets_seen += 1
        return self.target

    def _move_target(self, target: PhotoTarget, elapsed: float) -> None:
        if elapsed <= 0.0:
            return
        target.x += target.velocity_x * elapsed
        target.y += target.velocity_y * elapsed
        left_half = target.rect.width // 2
        right_half = target.rect.width - left_half
        top_half = target.rect.height // 2
        bottom_half = target.rect.height - top_half
        minimum_x = self.play_bounds.left + left_half
        maximum_x = self.play_bounds.right - right_half
        minimum_y = self.play_bounds.top + top_half
        maximum_y = self.play_bounds.bottom - bottom_half
        if target.x < minimum_x or target.x > maximum_x:
            target.x = max(minimum_x, min(maximum_x, target.x))
            target.velocity_x *= -1.0
        if target.y < minimum_y or target.y > maximum_y:
            target.y = max(minimum_y, min(maximum_y, target.y))
            target.velocity_y *= -1.0
        target.rect.center = round(target.x), round(target.y)

    def _target_hit_rect(self, target: PhotoTarget) -> pygame.Rect:
        """Sichtbare Karte plus Bewegungskorridor der Kameralatenz."""

        previous = target.rect.move(
            round(-target.velocity_x * self.CAMERA_LATENCY_ALLOWANCE),
            round(-target.velocity_y * self.CAMERA_LATENCY_ALLOWANCE),
        )
        # Nach einem Abpraller zeigt der Geschwindigkeitsvektor bereits in die
        # neue Richtung. Der symmetrische Korridor deckt auch die unmittelbar
        # davor von der Kamera erfasste Bewegungsbahn zuverlässig ab.
        before_bounce = target.rect.move(
            round(target.velocity_x * self.CAMERA_LATENCY_ALLOWANCE),
            round(target.velocity_y * self.CAMERA_LATENCY_ALLOWANCE),
        )
        swept = target.rect.union(previous).union(before_bounce)
        reserve = max(self.hit_tolerance, round(target.rect.width * 0.14), 24)
        return swept.inflate(reserve * 2, reserve * 2)

    def _target_photo(self, target: PhotoTarget) -> pygame.Surface:
        key = target.kind, target.rect.width
        cached = self.photo_cache.get(key)
        if cached is None:
            source = self.rabbit_photo if target.kind == "rabbit" else self.person_photo
            cached = pygame.transform.smoothscale(source, target.rect.size)
            self.photo_cache[key] = cached
        return cached

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
                return "menu"
            if choice == "repeat":
                self.begin_countdown(current)
            return "handled"
        if self.state != "playing":
            return "handled"

        self.recent_hit_zones = [
            (zone, until) for zone, until in self.recent_hit_zones if current < until
        ]
        if any(zone.collidepoint(pos) for zone, _ in self.recent_hit_zones):
            return "handled"

        self.shots += 1
        self.sounds.play("shot")
        target = self.target
        if target is None:
            self.sounds.play("miss")
            return "miss"
        hit_rect = self._target_hit_rect(target)
        if not hit_rect.collidepoint(pos):
            self.sounds.play("miss")
            return "miss"

        self.recent_hit_zones.append((hit_rect.inflate(24, 24), current + 0.38))
        self.target = None
        self.next_target_at = current + self.random.uniform(0.24, 0.44)
        if target.kind == "rabbit":
            self.rabbit_hits += 1
            self.score += self.RABBIT_POINTS
            self.feedback = ("+50 · KANINCHEN", SAFE_GREEN, current)
            self.sounds.play("photo_hit")
            return "rabbit"

        self.person_hits += 1
        self.score += self.PERSON_POINTS
        self.feedback = ("−100 · PERSON GETROFFEN", SAFE_MUTED, current)
        self.sounds.play("miss")
        return "person"

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        previous_update = self.last_update
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
                self.remaining = self.GAME_DURATION
                self.next_target_at = current + 0.55
                self.sounds.play("go")
            return
        if self.state != "playing":
            return

        self.remaining = max(0.0, self.deadline - current)
        if current >= self.deadline:
            self.target = None
            self.state = "game_over"
            self.state_started = current
            self.finish_reason = "Zeit abgelaufen"
            self.sounds.play("finish")
            return

        elapsed = max(0.0, current - previous_update)
        if self.target is not None:
            self._move_target(self.target, elapsed)
        if (
            self.target is not None
            and current - self.target.spawned_at + 1e-6 >= self.target.lifetime
        ):
            if self.target.kind == "rabbit":
                self.rabbit_misses += 1
            self.target = None
            self.next_target_at = current + self.random.uniform(0.22, 0.46)
        if self.target is None and current >= self.next_target_at:
            self._spawn_target(current)

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.screen.blit(self.background, (0, 0))
        draw_ambient_background(self.screen, "tobia", current)
        draw_cinematic_overlay(self.screen)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "tobia", current)
            self._draw_ready()
            return
        self._draw_playfield(current, subdued=self.state == "game_over")
        draw_ambient_foreground(self.screen, "tobia", current)
        if self.state == "countdown":
            draw_countdown(self.screen, self.state_started, current, self.font_countdown)
        elif self.state == "game_over":
            self._draw_result()

    def _draw_header(self) -> None:
        header = pygame.Rect(18, 15, self.screen.get_width() - 36, 92)
        draw_translucent_panel(self.screen, header, SAFE_PANEL, alpha=210, border_radius=14)
        pygame.draw.rect(self.screen, SAFE_CYAN, header, 2, border_radius=14)
        title = self.font_large.render("TOBIAS BLITZDUELL", True, SAFE_CYAN)
        self.screen.blit(title, (header.left + 20, header.top + 13))
        values = (
            f"PUNKTE  {self.score}",
            f"KANINCHEN  {self.rabbit_hits}",
            f"PERSONEN  {self.person_hits}",
            f"ZEIT  {self.remaining:04.1f}",
        )
        x = header.left + 22
        for value in values:
            surface = self.font_small.render(value, True, SAFE_GREEN if x == header.left + 22 else WHITE)
            self.screen.blit(surface, (x, header.top + 59))
            x += 195
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)

    def _draw_ready(self) -> None:
        self._draw_header()
        draw_translucent_panel(self.screen, self.start_card, SAFE_PANEL, alpha=220, border_radius=18)
        pygame.draw.rect(self.screen, SAFE_CYAN, self.start_card, 2, border_radius=18)
        # Rechts bleibt ein eigener, ruhiger Bereich für Tobias Top 10 frei.
        # Dadurch liegt die Bestenliste weder auf Regeln noch auf den Fotos.
        content_right = self.start_card.right - 210
        content_center_x = (self.start_card.left + content_right) // 2
        title = self.font_title.render("FOTO-BLITZ", True, WHITE)
        self.screen.blit(title, title.get_rect(midtop=(content_center_x, self.start_card.top + 34)))
        subtitle = self.font.render(
            "GRÖßE, TEMPO UND EINBLENDZEIT WECHSELN", True, SAFE_CYAN
        )
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(content_center_x, self.start_card.top + 105)))
        rules = (
            ("KANINCHEN TREFFEN", "+50 PUNKTE", SAFE_GREEN),
            ("PERSON NICHT TREFFEN", "−100 PUNKTE", SAFE_MUTED),
        )
        for index, (label, value, color) in enumerate(rules):
            y = self.start_card.top + 178 + index * 76
            label_surface = self.font.render(label, True, WHITE)
            value_surface = self.font_large.render(value, True, color)
            self.screen.blit(label_surface, (self.start_card.left + 72, y))
            self.screen.blit(value_surface, value_surface.get_rect(topright=(content_right - 2, y - 6)))
        draw_button(self.screen, self.start_button, "MIT SCHUSS STARTEN", self.font, SAFE_GREEN)

    def _draw_playfield(self, now: float, *, subdued: bool = False) -> None:
        self._draw_header()
        if not subdued and self.target is not None:
            photo = self._target_photo(self.target)
            frame = self.target.rect.inflate(16, 16)
            glow = pygame.Surface(frame.size, pygame.SRCALPHA)
            glow.fill((0, 205, 245, 46))
            self.screen.blit(glow, frame.topleft)
            self.screen.blit(photo, self.target.rect)
            pygame.draw.rect(self.screen, SAFE_CYAN, frame, 5, border_radius=10)
            pygame.draw.rect(self.screen, SAFE_GREEN, self.target.rect, 2, border_radius=6)
        if self.target is None and self.state == "playing":
            hint = self.font.render("BEREITHALTEN …", True, SAFE_MUTED)
            self.screen.blit(hint, hint.get_rect(midtop=(self.screen.get_width() // 2, 122)))
        if self.feedback is not None and now - self.feedback[2] < 0.72:
            message, color, _ = self.feedback
            surface = self.font_large.render(message, True, color)
            panel = surface.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() - 70)).inflate(34, 18)
            draw_translucent_panel(self.screen, panel, SAFE_PANEL, alpha=214, border_radius=10)
            self.screen.blit(surface, surface.get_rect(center=panel.center))

    def _draw_result(self) -> None:
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 7, 18, 190))
        self.screen.blit(veil, (0, 0))
        draw_translucent_panel(self.screen, self.result_card, SAFE_PANEL, alpha=230, border_radius=18)
        pygame.draw.rect(self.screen, SAFE_CYAN, self.result_card, 2, border_radius=18)
        title = self.font_title.render("GESAMTAUSWERTUNG", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 28)))
        metrics = (
            ("PUNKTE", str(self.score)),
            ("KANINCHEN", str(self.rabbit_hits)),
            ("PERSONEN", str(self.person_hits)),
            ("PRÄZISION", f"{self.accuracy:.0f} %"),
        )
        width = (self.result_card.width - 70) // len(metrics)
        for index, (label, value) in enumerate(metrics):
            center_x = self.result_card.left + 35 + index * width + width // 2
            self.screen.blit(
                self.font_small.render(label, True, SAFE_MUTED),
                self.font_small.render(label, True, SAFE_MUTED).get_rect(center=(center_x, self.result_card.centery - 32)),
            )
            rendered = self.font_large.render(value, True, SAFE_GREEN if index == 0 else WHITE)
            self.screen.blit(rendered, rendered.get_rect(center=(center_x, self.result_card.centery + 12)))
        draw_button(self.screen, self.repeat_button, "NOCH EINMAL", self.font, SAFE_GREEN)
        draw_button(self.screen, self.result_menu_button, "MENÜ", self.font, SAFE_CYAN)
