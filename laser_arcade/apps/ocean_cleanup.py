from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    LASER_RESULT_EXPANSION,
    SAFE_CYAN,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    TARGET_CYAN,
    TARGET_GREEN,
    calibrated_hit_tolerance,
    draw_aim_point,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_button,
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
DEEP_BLUE = (0, 25, 58)
SEA_BLUE = (0, 122, 176)
SEA_LIGHT = (0, 210, 235)
TARGET_LIGHT = (0, 148, 174)
SEA_GREEN = (0, 190, 130)
SAND = (212, 204, 160)
VIOLET = (90, 100, 175)
INK = (18, 24, 30)


@dataclass
class OceanTarget:
    kind: str
    x: float
    y: float
    radius: int
    velocity_x: float
    velocity_y: float
    spawned_at: float
    phase: float
    animal: bool
    hit_cooldown_until: float = 0.0

    @property
    def center(self) -> Tuple[int, int]:
        return round(self.x), round(self.y)


@dataclass
class Bubble:
    x: float
    y: float
    velocity_x: float
    velocity_y: float
    born_at: float
    lifetime: float
    radius: int


@dataclass
class FloatingText:
    text: str
    x: float
    y: float
    born_at: float
    color: Tuple[int, int, int]


class OceanCleanupApp(BaseApp):
    """Versteckte Meeresreinigung nach Annas eigener Spielidee."""

    name = "Annas Meeresmission"
    leaderboard_enabled = False
    COUNTDOWN_DURATION = 3.35
    GAME_DURATION = 60.0
    MAX_TARGETS = 7
    TRASH_KINDS = ("cat_can", "bottle", "bag", "cup")
    ANIMAL_KINDS = ("dolphin", "fish", "turtle")

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
        self.font_tiny = pygame.font.SysFont("Arial", 15)
        self.font_small = pygame.font.SysFont("Arial", 18)
        self.font = pygame.font.SysFont("Arial", 23, bold=True)
        self.font_large = pygame.font.SysFont("Arial", 36, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 50, bold=True)
        self.font_countdown = pygame.font.SysFont("Arial", 126, bold=True)
        (
            self.background,
            self.cat_can,
            self.dolphin,
            self.fish,
            self.turtle,
        ) = self._load_assets()
        self._scaled_sprite_cache: dict[tuple[str, tuple[int, int]], pygame.Surface] = {}
        self._posed_sprite_cache: dict[
            tuple[str, tuple[int, int], bool, int], pygame.Surface
        ] = {}
        self._bubble_surface_cache: dict[tuple[int, int], pygame.Surface] = {}
        width, height = self.screen.get_size()
        self.menu_button = pygame.Rect(width - 158, 20, 130, 44)
        self.start_card = pygame.Rect(52, 122, width - 104, height - 268)
        self.start_button = pygame.Rect(width // 2 - 240, height - 116, 480, 64)
        self.result_card = pygame.Rect(64, 106, width - 128, height - 234)
        self.repeat_button = pygame.Rect(width // 2 - 305, height - 92, 285, 54)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 92, 285, 54)
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.next_spawn_at = 0.0
        self.remaining = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.trash_collected = 0
        self.cat_cans_collected = 0
        self.animal_hits = 0
        self.finish_reason = ""
        self.targets: list[OceanTarget] = []
        self.bubbles: list[Bubble] = []
        self.floating_texts: list[FloatingText] = []
        self.recent_hit_zones: list[tuple[pygame.Rect, float]] = []
        self.last_count_value: Optional[int] = None

    @property
    def accuracy(self) -> float:
        return 100.0 * self.trash_collected / self.shots if self.shots else 0.0

    @property
    def leaderboard_detail(self) -> str:
        return (
            f"{self.trash_collected} MÜLL · "
            f"{self.cat_cans_collected} KATZENDOSEN · "
            f"{self.animal_hits} TIERE GETROFFEN"
        )

    @property
    def leaderboard_metrics(self) -> tuple[tuple[str, str], ...]:
        return (
            ("PUNKTE", f"{self.score:,}".replace(",", ".")),
            ("MÜLL", str(self.trash_collected)),
            ("KATZENDOSEN", str(self.cat_cans_collected)),
            ("PRÄZISION", f"{self.accuracy:.0f} %"),
        )

    @property
    def visual_transition_active(self) -> bool:
        return False

    def _load_assets(
        self,
    ) -> tuple[
        pygame.Surface,
        pygame.Surface,
        pygame.Surface,
        pygame.Surface,
        pygame.Surface,
    ]:
        root = Path(__file__).resolve().parents[2]
        asset_dir = root / "assets" / "ocean_cleanup"
        width, height = self.screen.get_size()
        try:
            background = pygame.image.load(
                str(asset_dir / "underwater_background_v3.png")
            ).convert()
            background = pygame.transform.smoothscale(background, (width, height))
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Unterwasser-Hintergrund fehlt: %s", exc)
            background = pygame.Surface((width, height))
            background.fill(DEEP_BLUE)
        self._neutralize_red_pixels(background)
        # Gleichmäßige Abdunklung schafft Reserve für den roten Laserpunkt.
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((0, 8, 20, 112))
        background.blit(shade, (0, 0))

        try:
            original = pygame.image.load(
                str(asset_dir / "cat_can_original.png")
            ).convert()
            # Exakter Bildausschnitt der eingesandten Zeichnung. Die Figur wird
            # nicht neu gezeichnet, eingefärbt oder in ihren Proportionen geändert.
            source = pygame.Rect(270, 590, 440, 585).clip(original.get_rect())
            cat_can = original.subsurface(source).copy()
            # Das eingesandte Motiv und sein Zuschnitt bleiben unverändert;
            # lediglich die Spitzenhelligkeit wird für sichere Lasertreffer auf
            # weißen Bildstellen begrenzt.
            limit_projected_brightness(cat_can, 160)
        except (FileNotFoundError, pygame.error, ValueError) as exc:
            LOGGER.warning("Originalzeichnung der Katzendose fehlt: %s", exc)
            cat_can = pygame.Surface((220, 290))
            cat_can.fill((210, 210, 205))

        animal_surfaces: list[pygame.Surface] = []
        for kind, filename in (
            ("Delfin", "realistic_dolphin.png"),
            ("Fisch", "realistic_fish.png"),
            ("Schildkröte", "realistic_turtle.png"),
        ):
            try:
                animal = pygame.image.load(str(asset_dir / filename)).convert_alpha()
                animal = self._trim_alpha(animal)
                self._neutralize_red_pixels(animal)
                limit_projected_brightness(animal, 160)
            except (FileNotFoundError, pygame.error, ValueError) as exc:
                LOGGER.warning("Realistische %s-Grafik fehlt: %s", kind, exc)
                animal = pygame.Surface((220, 120), pygame.SRCALPHA)
                pygame.draw.ellipse(animal, SEA_BLUE, pygame.Rect(22, 25, 164, 70))
                pygame.draw.polygon(animal, SEA_GREEN, ((25, 60), (0, 34), (0, 88)))
            animal_surfaces.append(animal)
        return background, cat_can, *animal_surfaces

    @staticmethod
    def _trim_alpha(surface: pygame.Surface) -> pygame.Surface:
        bounds = surface.get_bounding_rect(min_alpha=8)
        if bounds.width < 2 or bounds.height < 2:
            raise ValueError("Grafik besitzt keine sichtbare Kontur")
        return surface.subsurface(bounds).copy()

    @staticmethod
    def _neutralize_red_pixels(surface: pygame.Surface) -> None:
        """Entfernt vereinzelte rote Pixel aus der generierten Kulisse."""

        pixels = pygame.surfarray.pixels3d(surface)
        red = pixels[:, :, 0]
        other = pygame.surfarray.array3d(surface)[:, :, 1:].max(axis=2)
        mask = (red >= 70) & ((red.astype("int16") - other.astype("int16")) >= 24)
        red[mask] = other[mask]
        del pixels

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        LOGGER.info("Annas Meeresmission geöffnet")

    def stop(self) -> None:
        self.sounds.stop_all()
        self.targets.clear()
        self.bubbles.clear()
        self.floating_texts.clear()

    def _reset_round(self) -> None:
        self.remaining = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.trash_collected = 0
        self.cat_cans_collected = 0
        self.animal_hits = 0
        self.finish_reason = ""
        self.targets = []
        self.bubbles = []
        self.floating_texts = []
        self.recent_hit_zones = []
        self.next_spawn_at = 0.0
        self.last_count_value = None

    def begin_countdown(self, now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        self._reset_round()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.play("button")
        return "handled"

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if nearest_laser_button(pos, (("menu", self.menu_button),)) == "menu":
            self.sounds.play("button")
            return "menu"
        if self.state == "ready":
            if self.start_card.collidepoint(pos) or nearest_laser_button(
                pos, (("start", self.start_button),)
            ) == "start":
                return self.begin_countdown(current)
            return "handled"
        if self.state == "game_over":
            choice = nearest_laser_button(
                pos,
                (("repeat", self.repeat_button), ("menu", self.result_menu_button)),
                expansion=LASER_RESULT_EXPANSION,
            )
            if choice == "menu":
                self.sounds.play("button")
                return "menu"
            if choice == "repeat":
                return self.begin_countdown(current)
            return "handled"
        if self.state != "playing":
            return "handled"

        self.recent_hit_zones = [
            (rect, until) for rect, until in self.recent_hit_zones if current < until
        ]
        if any(rect.collidepoint(pos) for rect, _ in self.recent_hit_zones):
            return "handled"
        cooling_target = next(
            (
                target
                for target in self.targets
                if current < target.hit_cooldown_until
                and self._target_hit_rect(target).collidepoint(pos)
            ),
            None,
        )
        if cooling_target is not None:
            return "handled"

        self.shots += 1
        self.sounds.play("shot")
        candidates = [
            target
            for target in self.targets
            if current >= target.hit_cooldown_until
            and self._target_hit_rect(target).collidepoint(pos)
        ]
        if not candidates:
            self.sounds.play("miss")
            return "miss"
        target = min(candidates, key=lambda item: math.dist(pos, item.center))
        if target.animal:
            self.score -= 10
            self.animal_hits += 1
            target.hit_cooldown_until = current + 0.9
            target.velocity_x *= 1.35
            self._add_text("TIER BESCHÜTZEN  −10", target.x, target.y, current, WHITE)
            self._add_bubbles(target.x, target.y, current, 7)
            self.sounds.play("miss")
            return "animal"

        points = 10 if target.kind == "cat_can" else 5
        self.score += points
        self.trash_collected += 1
        if target.kind == "cat_can":
            self.cat_cans_collected += 1
        self.recent_hit_zones.append((self._target_hit_rect(target), current + 0.60))
        self.targets.remove(target)
        self._add_text(f"+{points}", target.x, target.y, current, SEA_GREEN)
        self._add_bubbles(target.x, target.y, current, 11)
        self.sounds.play("water_hit" if points == 10 else "target_hit")
        return "trash"

    def update(self, now: float) -> None:
        delta = max(0.0, min(0.08, now - self.last_update))
        self.last_update = now
        if self.state == "countdown":
            count = max(0, 3 - int(now - self.state_started))
            if count != self.last_count_value:
                self.sounds.play("go" if count == 0 else "count")
                self.last_count_value = count
            if now - self.state_started >= self.COUNTDOWN_DURATION:
                self.state = "playing"
                self.state_started = now
                self.deadline = now + self.GAME_DURATION
                self.next_spawn_at = now
                self.last_update = now
            return
        if self.state != "playing":
            return
        self.remaining = max(0.0, self.deadline - now)
        if now >= self.deadline:
            self.state = "game_over"
            self.state_started = now
            self.finish_reason = "DIE MISSION IST BEENDET"
            self.targets.clear()
            self.sounds.play("finish")
            LOGGER.info(
                "Meeresmission beendet: Punkte=%s Müll=%s Katzendosen=%s Tiere=%s",
                self.score,
                self.trash_collected,
                self.cat_cans_collected,
                self.animal_hits,
            )
            return

        while now >= self.next_spawn_at and len(self.targets) < self.MAX_TARGETS:
            self._spawn_target(now)
            self.next_spawn_at += self.random.uniform(0.72, 1.18)

        width = self.screen.get_width()
        for target in list(self.targets):
            target.x += target.velocity_x * delta
            target.y += target.velocity_y * delta
            target.y += math.sin(now * 2.0 + target.phase) * 7.0 * delta
            if (
                target.x < -target.radius * 2.5
                or target.x > width + target.radius * 2.5
            ):
                self.targets.remove(target)
        self.bubbles = [
            bubble for bubble in self.bubbles if now - bubble.born_at < bubble.lifetime
        ]
        for bubble in self.bubbles:
            bubble.x += bubble.velocity_x * delta
            bubble.y += bubble.velocity_y * delta
        self.floating_texts = [
            item for item in self.floating_texts if now - item.born_at < 1.0
        ]
        self.recent_hit_zones = [
            (rect, until) for rect, until in self.recent_hit_zones if now < until
        ]

    def _spawn_target(self, now: float, kind: Optional[str] = None) -> OceanTarget:
        if kind is None:
            # Rund zwei Drittel Müll sorgen für ein klares, gut spielbares Zielbild.
            kind = self.random.choice(self.TRASH_KINDS) if self.random.random() < 0.68 else self.random.choice(self.ANIMAL_KINDS)
        animal = kind in self.ANIMAL_KINDS
        radii = {
            "cat_can": 48,
            "bottle": 37,
            "bag": 43,
            "cup": 36,
            "dolphin": 64,
            "fish": 58,
            "turtle": 58,
        }
        radius = radii[kind]
        direction = self.random.choice((-1, 1))
        x = -radius * 2 if direction > 0 else self.screen.get_width() + radius * 2
        y = self.random.randint(190, self.screen.get_height() - 105)
        for _ in range(14):
            if all(abs(y - target.y) > target.radius + radius + 18 for target in self.targets):
                break
            y = self.random.randint(190, self.screen.get_height() - 105)
        speed = self.random.uniform(54, 88) if animal else self.random.uniform(38, 68)
        target = OceanTarget(
            kind,
            float(x),
            float(y),
            radius,
            direction * speed,
            self.random.uniform(-4, 4),
            now,
            self.random.uniform(0, math.tau),
            animal,
        )
        self.targets.append(target)
        return target

    @staticmethod
    def _target_visual_size(target: OceanTarget) -> Tuple[int, int]:
        return {
            "cat_can": (82, 109),
            "bottle": (64, 104),
            "bag": (82, 86),
            "cup": (72, 112),
            "dolphin": (176, 88),
            "fish": (132, 80),
            "turtle": (150, 98),
        }[target.kind]

    def _target_hit_rect(self, target: OceanTarget) -> pygame.Rect:
        width, height = self._target_visual_size(target)
        # Kamera-, Projektions- und Bewegungsnachlauf werden gemeinsam
        # berücksichtigt. Die gesamte sichtbare Kontur bleibt so treffbar.
        margin = max(34, self.hit_tolerance + 18)
        rect = pygame.Rect(0, 0, width + margin * 2, height + margin * 2)
        rect.center = target.center
        return rect

    def _add_bubbles(self, x: float, y: float, now: float, count: int) -> None:
        for _ in range(count):
            self.bubbles.append(
                Bubble(
                    x + self.random.uniform(-24, 24),
                    y + self.random.uniform(-16, 16),
                    self.random.uniform(-20, 20),
                    self.random.uniform(-95, -45),
                    now,
                    self.random.uniform(0.55, 1.1),
                    self.random.randint(3, 9),
                )
            )

    def _add_text(
        self,
        text: str,
        x: float,
        y: float,
        now: float,
        color: Tuple[int, int, int],
    ) -> None:
        self.floating_texts.append(FloatingText(text, x, y, now, color))

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.screen.blit(self.background, (0, 0))
        draw_ambient_background(self.screen, "ocean", current)
        draw_cinematic_overlay(self.screen)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "ocean", current)
            self._draw_ready()
            return
        if self.state == "game_over":
            self._draw_result()
            return
        self._draw_playfield(current)
        draw_ambient_foreground(self.screen, "ocean", current)
        if self.state == "countdown":
            draw_countdown(
                self.screen,
                self.state_started,
                current,
                self.font_countdown,
                self.COUNTDOWN_DURATION,
            )

    def _draw_ready(self) -> None:
        width = self.screen.get_width()
        title = self.font_title.render("ANNAS MEERESMISSION", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(width // 2, 28)))
        subtitle = self.font.render("DAS VERSTECKTE UNTERWASSERSPIEL", True, SAFE_GREEN)
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(width // 2, 88)))
        draw_translucent_panel(self.screen, self.start_card, SAFE_PANEL, alpha=188, border_radius=18)
        pygame.draw.rect(self.screen, SAFE_CYAN, self.start_card, 3, border_radius=18)

        preview = pygame.transform.smoothscale(self.cat_can, (145, 192))
        preview_rect = preview.get_rect(midleft=(self.start_card.left + 45, self.start_card.centery + 18))
        self.screen.blit(preview, preview_rect)
        rules = (
            ("KATZENDOSE", "+10 PUNKTE", SAFE_GREEN),
            ("ANDERER MÜLL", "+5 PUNKTE", SAFE_CYAN),
            ("MEERESTIERE", "BESCHÜTZEN  ·  −10", WHITE),
        )
        for index, (label, value, color) in enumerate(rules):
            y = self.start_card.top + 100 + index * 86
            pygame.draw.circle(self.screen, color, (self.start_card.left + 275, y), 21, 3)
            label_surface = self.font.render(label, True, WHITE)
            value_surface = self.font_small.render(value, True, color)
            self.screen.blit(label_surface, (self.start_card.left + 315, y - 24))
            self.screen.blit(value_surface, (self.start_card.left + 315, y + 9))
        hint = self.font_small.render("NUR DEN MÜLL AUS DEM MEER HOLEN", True, SEA_LIGHT)
        self.screen.blit(hint, hint.get_rect(midbottom=(self.start_card.centerx + 115, self.start_card.bottom - 24)))
        draw_button(self.screen, self.start_button, "MISSION STARTEN", self.font, SAFE_GREEN)
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)

    def _draw_playfield(self, now: float) -> None:
        hud = pygame.Rect(22, 20, self.screen.get_width() - 190, 72)
        draw_translucent_panel(self.screen, hud, SAFE_PANEL, alpha=172, border_radius=11)
        pygame.draw.rect(self.screen, SAFE_MUTED, hud, 2, border_radius=11)
        values = (
            ("PUNKTE", str(self.score)),
            ("MÜLL GESAMMELT", str(self.trash_collected)),
            ("KATZENDOSEN", str(self.cat_cans_collected)),
            ("ZEIT", f"{math.ceil(self.remaining):02d}"),
        )
        segment = hud.width // len(values)
        for index, (label, value) in enumerate(values):
            center_x = hud.left + index * segment + segment // 2
            label_surface = self.font_tiny.render(label, True, SEA_LIGHT)
            value_surface = self.font.render(value, True, SAFE_GREEN if index != 3 else SAFE_CYAN)
            self.screen.blit(label_surface, label_surface.get_rect(center=(center_x, hud.top + 19)))
            self.screen.blit(value_surface, value_surface.get_rect(center=(center_x, hud.top + 48)))
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)

        for target in self.targets:
            self._draw_target(target, now)
        for bubble in self.bubbles:
            progress = (now - bubble.born_at) / bubble.lifetime
            alpha = max(0, min(255, round(210 * (1.0 - progress))))
            alpha_step = max(0, min(208, round(alpha / 16) * 16))
            bubble_key = bubble.radius, alpha_step
            layer = self._bubble_surface_cache.get(bubble_key)
            if layer is None:
                layer = pygame.Surface((bubble.radius * 2 + 4, bubble.radius * 2 + 4), pygame.SRCALPHA)
                pygame.draw.circle(layer, (*SEA_LIGHT, alpha_step), (bubble.radius + 2, bubble.radius + 2), bubble.radius, 2)
                self._bubble_surface_cache[bubble_key] = layer
            self.screen.blit(layer, (round(bubble.x - bubble.radius - 2), round(bubble.y - bubble.radius - 2)))
        for item in self.floating_texts:
            age = now - item.born_at
            text = self.font.render(item.text, True, item.color)
            text.set_alpha(max(0, min(255, round(255 * (1.0 - age)))))
            self.screen.blit(text, text.get_rect(center=(round(item.x), round(item.y - 42 - age * 32))))

    def _draw_target(self, target: OceanTarget, now: float) -> None:
        x, y = target.center
        facing_right = target.velocity_x > 0
        self._draw_target_backdrop(target)
        if target.kind == "cat_can":
            angle = math.sin(now * 1.8 + target.phase) * 4
            sprite = self._cached_target_pose(
                "cat_can", self.cat_can, self._target_visual_size(target), False, angle
            )
            self.screen.blit(sprite, sprite.get_rect(center=(x, y)))
            return
        if target.kind == "bottle":
            body = pygame.Rect(x - 16, y - 34, 32, 66)
            neck = pygame.Rect(x - 8, y - 48, 16, 18)
            pygame.draw.rect(self.screen, (0, 115, 154), body, border_radius=9)
            pygame.draw.rect(self.screen, TARGET_LIGHT, body, 3, border_radius=9)
            pygame.draw.rect(self.screen, (0, 76, 116), neck, border_radius=3)
            pygame.draw.rect(self.screen, TARGET_GREEN, neck, 2, border_radius=3)
            pygame.draw.line(self.screen, TARGET_LIGHT, (x - 8, y - 15), (x + 7, y - 21), 3)
            return
        if target.kind == "bag":
            points = [(x - 37, y - 23), (x + 31, y - 31), (x + 38, y + 30), (x - 31, y + 35)]
            pygame.draw.polygon(self.screen, (40, 82, 116), points)
            pygame.draw.lines(self.screen, TARGET_CYAN, True, points, 3)
            pygame.draw.arc(self.screen, TARGET_GREEN, pygame.Rect(x - 21, y - 42, 42, 30), math.pi, math.tau, 3)
            return
        if target.kind == "cup":
            cup = [(x - 27, y - 30), (x + 27, y - 30), (x + 20, y + 31), (x - 18, y + 31)]
            pygame.draw.polygon(self.screen, VIOLET, cup)
            pygame.draw.lines(self.screen, SEA_LIGHT, True, cup, 3)
            pygame.draw.line(self.screen, TARGET_GREEN, (x + 15, y - 30), (x + 31, y - 54), 4)
            return
        if target.kind == "dolphin":
            _, angle = self._animal_swim_pose(target, now)
            sprite = self._cached_target_pose(
                "dolphin", self.dolphin, self._target_visual_size(target), not facing_right, angle
            )
            self.screen.blit(sprite, sprite.get_rect(center=(x, y)))
        elif target.kind == "fish":
            _, angle = self._animal_swim_pose(target, now)
            sprite = self._cached_target_pose(
                "fish", self.fish, self._target_visual_size(target), not facing_right, angle
            )
            self.screen.blit(sprite, sprite.get_rect(center=(x, y)))
        elif target.kind == "turtle":
            _, angle = self._animal_swim_pose(target, now)
            sprite = self._cached_target_pose(
                "turtle", self.turtle, self._target_visual_size(target), not facing_right, angle
            )
            self.screen.blit(sprite, sprite.get_rect(center=(x, y)))

        if target.animal and now < target.hit_cooldown_until:
            pygame.draw.circle(self.screen, WHITE, (x, y), target.radius + 8, 3)

    def _cached_target_pose(
        self,
        kind: str,
        source: pygame.Surface,
        size: tuple[int, int],
        flip_x: bool,
        angle: float,
    ) -> pygame.Surface:
        safe_size = max(2, int(size[0])), max(2, int(size[1]))
        scaled_key = kind, safe_size
        scaled = self._scaled_sprite_cache.get(scaled_key)
        if scaled is None:
            scaled = pygame.transform.smoothscale(source, safe_size)
            self._scaled_sprite_cache[scaled_key] = scaled
        angle_key = int(round(angle / 2.0))
        pose_key = kind, safe_size, flip_x, angle_key
        posed = self._posed_sprite_cache.get(pose_key)
        if posed is None:
            posed = pygame.transform.flip(scaled, True, False) if flip_x else scaled
            if angle_key:
                posed = pygame.transform.rotate(posed, angle_key * 2.0)
            self._posed_sprite_cache[pose_key] = posed
            if len(self._posed_sprite_cache) > 384:
                self._posed_sprite_cache.pop(next(iter(self._posed_sprite_cache)))
        return posed

    def _animal_swim_pose(
        self, target: OceanTarget, now: float
    ) -> tuple[bool, float]:
        facing_right = target.velocity_x > 0
        direction = 1.0 if facing_right else -1.0
        bob_velocity = math.cos(now * 2.0 + target.phase) * 14.0
        vertical_velocity = target.velocity_y + bob_velocity
        slope = math.degrees(
            math.atan2(vertical_velocity, max(1.0, abs(target.velocity_x)))
        )
        # Die Nase folgt der tatsächlichen Schwimmrichtung. Beim Spiegeln muss
        # auch das Vorzeichen der Drehung gespiegelt werden.
        angle = max(-11.0, min(11.0, -direction * slope))
        return facing_right, angle

    def _draw_target_backdrop(self, target: OceanTarget) -> None:
        width, height = self._target_visual_size(target)
        padding = 24
        layer = pygame.Surface((width + padding * 2, height + padding * 2), pygame.SRCALPHA)
        rect = layer.get_rect().inflate(-4, -4)
        pygame.draw.ellipse(layer, (0, 7, 20, 178), rect)
        pygame.draw.ellipse(layer, (*TARGET_CYAN, 130), rect, 3)
        inner = rect.inflate(-12, -12)
        pygame.draw.ellipse(layer, (0, 18, 35, 92), inner)
        # Vier klare Eckmarken zeigen die tatsächlich großzügige Trefferfläche,
        # ohne die eingesandte Katzendosen-Zeichnung zu übermalen.
        corner = 20
        color = (*TARGET_GREEN, 190)
        for left, top, sx, sy in (
            (rect.left + 5, rect.top + 5, 1, 1),
            (rect.right - 5, rect.top + 5, -1, 1),
            (rect.left + 5, rect.bottom - 5, 1, -1),
            (rect.right - 5, rect.bottom - 5, -1, -1),
        ):
            pygame.draw.line(layer, color, (left, top), (left + sx * corner, top), 3)
            pygame.draw.line(layer, color, (left, top), (left, top + sy * corner), 3)
        self.screen.blit(layer, layer.get_rect(center=target.center))

    def _draw_result(self) -> None:
        draw_translucent_panel(self.screen, self.result_card, SAFE_PANEL, alpha=202, border_radius=18)
        pygame.draw.rect(self.screen, SAFE_GREEN, self.result_card, 3, border_radius=18)
        draw_aim_point(self.screen, (self.result_card.right - 30, self.result_card.top + 30), SAFE_CYAN)
        title = self.font_title.render("MISSION GESCHAFFT!", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 30)))
        score = self.font_large.render(f"{self.score} PUNKTE", True, SAFE_GREEN)
        self.screen.blit(score, score.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 105)))
        metrics = (
            ("MÜLL GESAMMELT", str(self.trash_collected)),
            ("KATZENDOSEN", str(self.cat_cans_collected)),
            ("TIERE GETROFFEN", str(self.animal_hits)),
            ("TREFFERQUOTE", f"{self.accuracy:.0f} %"),
        )
        segment = (self.result_card.width - 60) // len(metrics)
        for index, (label, value) in enumerate(metrics):
            center_x = self.result_card.left + 30 + index * segment + segment // 2
            value_surface = self.font_large.render(value, True, SAFE_GREEN if index < 2 else SAFE_CYAN)
            label_surface = self.font_tiny.render(label, True, SEA_LIGHT)
            self.screen.blit(value_surface, value_surface.get_rect(center=(center_x, self.result_card.top + 235)))
            self.screen.blit(label_surface, label_surface.get_rect(center=(center_x, self.result_card.top + 274)))
        message = "DANKE, DASS DU DAS MEER SAUBER HÄLTST!"
        surface = self.font.render(message, True, WHITE)
        self.screen.blit(surface, surface.get_rect(center=(self.result_card.centerx, self.result_card.bottom - 72)))
        draw_button(self.screen, self.repeat_button, "NOCH EINMAL", self.font, SAFE_GREEN)
        draw_button(self.screen, self.result_menu_button, "MENÜ", self.font, SAFE_CYAN)
