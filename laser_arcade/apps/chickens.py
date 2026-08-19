from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import pygame
import numpy as np

from .arcade_common import (
    SAFE_BG,
    SAFE_CYAN,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    build_theme_background,
    draw_aim_point,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_button,
    draw_cinematic_overlay,
    draw_countdown,
    draw_frame,
    draw_hud,
    draw_result_card,
    draw_translucent_panel,
    limit_projected_brightness,
    neutralize_laser_red,
    nearest_laser_button,
)
from .base import BaseApp


LOGGER = logging.getLogger(__name__)

MOORHUHN_DIR = Path(__file__).with_name("moorhuhn")
SOUNDS_DIR = MOORHUHN_DIR / "sounds"
SCORE_FILE = Path.home() / ".laser_arcade" / "moorhuhn_scores.json"
_SURFACE_MASK_CACHE: dict[int, pygame.Mask] = {}
_ROUND_MASK_CACHE: dict[int, pygame.Mask] = {}


def _scaled(surface: pygame.Surface, size: Tuple[int, int]) -> pygame.Surface:
    return pygame.transform.smoothscale(surface, (max(1, size[0]), max(1, size[1])))


def _prepare_original_sprite(path: Path, size: Tuple[int, int], brightness_limit: int) -> pygame.Surface:
    """Lädt ein Originalbild einmalig und macht nur echte Laser-Fehlfarben sicher."""

    source = pygame.image.load(str(path)).convert_alpha()
    pixels = pygame.surfarray.pixels3d(source)
    alpha = pygame.surfarray.pixels_alpha(source)
    rgb = pixels.astype(np.int16)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

    # Alte Magenta-Matte vollständig entfernen, einschließlich weicher
    # Antialias-Ränder. Die sichtbare Originalform selbst bleibt unangetastet.
    magenta_matte = (
        (alpha > 0)
        & (red >= 55)
        & (blue >= 55)
        & (green <= 105)
        & ((np.minimum(red, blue) - green) >= 22)
    )
    alpha[magenta_matte] = 0

    # Nur Pixel entschärfen, die wie ein roter Laserpuls aussehen könnten.
    # Braun, Gelb und die klassische Zeichnung bleiben dadurch weitgehend
    # erhalten; kritisches Rot wird in dunkles Original-Orange verschoben.
    strongest_other = np.maximum(green, blue)
    laser_red = (alpha > 0) & (red >= 70) & ((red - strongest_other) >= 28)
    adjusted_red = np.minimum(255, strongest_other + 20)
    pixels[:, :, 0][laser_red] = adjusted_red[laser_red].astype(np.uint8)
    del alpha, pixels

    limit_projected_brightness(source, brightness_limit)
    prepared = pygame.transform.smoothscale(
        source, (max(1, int(size[0])), max(1, int(size[1])))
    )
    # Skalierung kann wieder einzelne rote Zwischenpixel erzeugen.
    neutralize_laser_red(prepared)
    limit_projected_brightness(prepared, brightness_limit)
    return prepared


def _load_original_moorhuhn_sequence(
    folder: str,
    filenames: list[str],
    size: Tuple[int, int],
    *,
    brightness_limit: int = 154,
) -> list[pygame.Surface]:
    """Gemeinsamer, einmaliger Lader für alle Original-Moorhuhn-Folgen."""

    base = MOORHUHN_DIR / "img" / folder
    return [
        _prepare_original_sprite(base / filename, size, brightness_limit)
        for filename in filenames
    ]


def _alpha_hit(
    surface: pygame.Surface,
    rect: pygame.Rect,
    point: Tuple[int, int],
    margin: int,
) -> bool:
    """Prüft die sichtbare Alphaform einschließlich kalibriertem Fangrand."""

    if not rect.inflate(margin * 2, margin * 2).collidepoint(point):
        return False
    mask = _SURFACE_MASK_CACHE.setdefault(id(surface), pygame.mask.from_surface(surface, 10))
    local_x, local_y = point[0] - rect.left, point[1] - rect.top
    if 0 <= local_x < rect.width and 0 <= local_y < rect.height and mask.get_at((local_x, local_y)):
        return True
    if margin <= 0:
        return False
    circle = _ROUND_MASK_CACHE.get(margin)
    if circle is None:
        diameter = margin * 2 + 1
        circle_surface = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
        pygame.draw.circle(circle_surface, (255, 255, 255, 255), (margin, margin), margin)
        circle = pygame.mask.from_surface(circle_surface)
        _ROUND_MASK_CACHE[margin] = circle
    return mask.overlap(circle, (local_x - margin, local_y - margin)) is not None


def _wood_pan_surface(size: Tuple[int, int], direction: int, enabled: bool) -> pygame.Surface:
    """Großes, laserneutrales Holzschild für die Panoramasteuerung."""

    width, height = size
    surface = pygame.Surface(size, pygame.SRCALPHA)
    board = pygame.Rect(3, 8, width - 6, height - 16)
    shadow = board.move(3, 5)
    pygame.draw.rect(surface, (0, 12, 18, 150), shadow, border_radius=13)
    wood = (24, 58, 62) if enabled else (23, 36, 40)
    edge = (0, 119, 126) if enabled else (42, 66, 70)
    pygame.draw.rect(surface, wood, board, border_radius=13)
    pygame.draw.rect(surface, edge, board, 4, border_radius=13)
    for y in range(board.top + 15, board.bottom - 8, 17):
        offset = int(5 * math.sin(y * 0.17))
        pygame.draw.line(
            surface,
            (12, 78, 80) if enabled else (29, 49, 52),
            (board.left + 10, y),
            (board.right - 11 + offset, y - 3),
            2,
        )
    for corner in ((board.left + 12, board.top + 12), (board.right - 12, board.top + 12),
                   (board.left + 12, board.bottom - 12), (board.right - 12, board.bottom - 12)):
        pygame.draw.circle(surface, (83, 112, 114) if enabled else (51, 66, 68), corner, 5)
        pygame.draw.circle(surface, (7, 29, 33), corner, 2)
    arrow_color = (0, 174, 180) if enabled else (52, 79, 82)
    cx, cy = board.center
    tip_x = cx + direction * round(width * 0.29)
    neck_x = cx - direction * round(width * 0.02)
    tail_x = cx - direction * round(width * 0.27)
    arrow = (
        (tip_x, cy),
        (neck_x, cy - round(height * 0.25)),
        (neck_x, cy - round(height * 0.11)),
        (tail_x, cy - round(height * 0.11)),
        (tail_x, cy + round(height * 0.11)),
        (neck_x, cy + round(height * 0.11)),
        (neck_x, cy + round(height * 0.25)),
    )
    pygame.draw.polygon(surface, (4, 26, 31), arrow)
    pygame.draw.lines(surface, arrow_color, True, arrow, 4)
    return limit_projected_brightness(surface, 154)


def _pumpkin_surface(size: int, stage: int, stages: int) -> pygame.Surface:
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    progress = stage / max(1, stages - 1)
    radius_x = max(5, round(size * (0.35 - 0.15 * progress)))
    radius_y = max(4, round(size * (0.30 - 0.12 * progress)))
    center = (size // 2, round(size * (0.58 + 0.13 * progress)))
    color = SAFE_GREEN if stage == 0 else SAFE_MUTED
    pygame.draw.ellipse(surface, (0, 54, 47), pygame.Rect(center[0] - radius_x, center[1] - radius_y, radius_x * 2, radius_y * 2))
    pygame.draw.ellipse(surface, color, pygame.Rect(center[0] - radius_x, center[1] - radius_y, radius_x * 2, radius_y * 2), 3)
    for offset in (-0.18, 0.0, 0.18):
        x = center[0] + round(radius_x * offset * 2)
        pygame.draw.arc(
            surface,
            SAFE_CYAN,
            pygame.Rect(x - radius_x // 2, center[1] - radius_y, radius_x, radius_y * 2),
            -math.pi / 2,
            math.pi / 2,
            2,
        )
    pygame.draw.line(surface, SAFE_CYAN, (center[0], center[1] - radius_y), (center[0] + 2, center[1] - radius_y - size // 10), 3)
    if stage:
        pygame.draw.line(surface, SAFE_BG, (center[0] - radius_x // 2, center[1] - radius_y // 2), (center[0] + radius_x // 3, center[1] + radius_y // 2), 3)
    return limit_projected_brightness(surface, 162)


def _tree_surface(width: int, height: int) -> pygame.Surface:
    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    center = width // 2
    half_base = max(11, width // 8)
    half_top = max(6, width // 16)
    trunk_points = (
        (center - half_top - width // 35, 0),
        (center + half_top, 0),
        (center + half_base, height),
        (center - half_base + width // 30, height),
    )
    # Kühles, natürlich abgestuftes Holz statt der früheren fast schwarzen
    # Vollfläche. So bleibt der Vordergrundbaum klar erkennbar, wirkt aber
    # nicht mehr wie eine technische Panorama-Naht.
    pygame.draw.polygon(surface, (12, 40, 48), trunk_points)
    inner_trunk = (
        (center - max(3, half_top // 3), 0),
        (center + half_top - 2, 0),
        (center + max(5, half_base // 2), height),
        (center - max(5, half_base // 2), height),
    )
    pygame.draw.polygon(surface, (18, 55, 61), inner_trunk)
    pygame.draw.lines(surface, (0, 103, 111), True, trunk_points, 3)

    branch_specs = (
        (height // 4, -1, width // 2, height // 7),
        (height * 2 // 5, 1, width * 5 // 11, height // 8),
        (height * 3 // 5, -1, width * 4 // 9, height // 9),
    )
    for y, direction, reach, rise in branch_specs:
        start = (center + direction * width // 30, y)
        elbow = (
            center + direction * reach * 2 // 5,
            y - rise * 2 // 5,
        )
        end = (center + direction * reach, y - rise)
        thickness = max(7, width // 17)
        pygame.draw.lines(surface, (10, 39, 47), False, (start, elbow, end), thickness)
        pygame.draw.lines(
            surface,
            (0, 91, 100),
            False,
            ((start[0], start[1] - 2), (elbow[0], elbow[1] - 2), end),
            2,
        )

    # Kurze, versetzte Borkenlinien brechen die senkrechte Silhouette auf,
    # ohne als helle oder rote Schusssignatur zu erscheinen.
    for index, y in enumerate(range(height // 10, height, max(24, height // 11))):
        taper = half_top + (half_base - half_top) * y / max(1, height)
        sway = round(math.sin(index * 1.73) * taper * 0.42)
        start_x = round(center - taper * 0.55 + sway)
        end_x = round(center + taper * 0.30 + sway)
        pygame.draw.line(surface, (0, 77, 87), (start_x, y), (end_x, y - 11), 2)
    for y in (round(height * 0.33), round(height * 0.57), round(height * 0.76)):
        knot = (center + round(math.sin(y) * width * 0.05), y)
        pygame.draw.circle(surface, (4, 30, 38), knot, max(4, width // 27))
        pygame.draw.circle(surface, (0, 116, 124), knot, max(4, width // 27), 2)
    return limit_projected_brightness(surface, 162)


class SilentSound:
    def play(self, *args, **kwargs) -> None:
        return

    def stop(self) -> None:
        return

    def set_volume(self, volume: float) -> None:
        return


class MoorhuhnSounds:
    """Lädt die Originalsounds für kurze Ereignisse, jedoch keine Dauerschleifen."""

    FILES = {
        "ambient": "ambientloop.ogg",
        "big_popup": "big_chicken_pops_up.ogg",
        "button": "button_click.ogg",
        "hit1": "chick_hit1.ogg",
        "hit2": "chick_hit2.ogg",
        "hit3": "chick_hit3.ogg",
        "empty": "empty_shot_sound.ogg",
        "game_over": "game_over.ogg",
        "game_start": "game_start.ogg",
        "shot": "gun_shot_sound.ogg",
        "main_theme": "main_theme.ogg",
        "mill": "mill_hit_sound.ogg",
        "pumpkin": "pumpkin_shot_sound.ogg",
        "sign": "sign_post_sound.ogg",
        "time": "time_running.ogg",
        "tree": "treebranch_shot.wav",
        "type": "type_sound.wav",
        "reload": "update_ammo.ogg",
    }

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = False
        self.items: dict[str, pygame.mixer.Sound | SilentSound] = {}
        self.music_channel: Optional[pygame.mixer.Channel] = None
        if enabled:
            try:
                if pygame.mixer.get_init() is None:
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                self.items = {
                    name: pygame.mixer.Sound(str(SOUNDS_DIR / filename))
                    for name, filename in self.FILES.items()
                }
                volume_map = {
                    "main_theme": 0.0,
                    "ambient": 0.0,
                    "button": 0.24,
                    "shot": 0.42,
                    "empty": 0.22,
                    "game_start": 0.34,
                    "game_over": 0.36,
                    "big_popup": 0.30,
                    "hit1": 0.35,
                    "hit2": 0.35,
                    "hit3": 0.35,
                    "mill": 0.32,
                    "pumpkin": 0.30,
                    "sign": 0.28,
                    "tree": 0.30,
                    "time": 0.24,
                    "type": 0.22,
                    "reload": 0.24,
                }
                for name, volume in volume_map.items():
                    self.items[name].set_volume(volume)
                self.enabled = True
                LOGGER.info("Moorhuhn-Audio bereit: %s Originalsounds", len(self.items))
            except (pygame.error, OSError) as exc:
                LOGGER.warning("Moorhuhn läuft ohne Audio: %s", exc)
        if not self.items:
            self.items = {name: SilentSound() for name in self.FILES}

    def play(self, name: str) -> None:
        self.items[name].play()

    def play_hit(self, randomizer: random.Random) -> None:
        self.items[randomizer.choice(("hit1", "hit2", "hit3"))].play()

    def play_music(self, name: str) -> None:
        if not self.enabled:
            return
        self.stop_music()
        self.music_channel = pygame.mixer.find_channel(True)
        self.music_channel.play(self.items[name], loops=-1, fade_ms=250)

    def stop_music(self) -> None:
        if self.music_channel is not None:
            self.music_channel.fadeout(180)
            self.music_channel = None

    def stop_all(self) -> None:
        self.stop_music()
        for sound in self.items.values():
            sound.stop()


@dataclass
class ScorePopup:
    text: str
    x: float
    y: float
    born_at: float
    positive: bool = True


@dataclass
class FlyingChicken:
    kind: str
    frames: list[pygame.Surface]
    death_frames: list[pygame.Surface]
    x: float
    y: float
    speed: float
    points: int
    direction: int
    alive: bool = True
    frame_index: int = 0
    frame_timer: float = 0.0
    death_index: int = 0
    death_timer: float = 0.0
    remove: bool = False

    @property
    def image(self) -> pygame.Surface:
        return self.frames[self.frame_index] if self.alive else self.death_frames[self.death_index]

    @property
    def rect(self) -> pygame.Rect:
        return self.image.get_rect(topleft=(round(self.x), round(self.y)))

    def update(self, dt: float, screen_width: int, screen_height: int) -> None:
        if self.alive:
            self.frame_timer += dt
            while self.frame_timer >= 0.045:
                self.frame_timer -= 0.045
                self.frame_index = (self.frame_index + 1) % len(self.frames)
            self.x += self.direction * self.speed * dt
            if self.x > screen_width + 130 or self.x + self.rect.width < -130:
                self.remove = True
        else:
            self.death_timer += dt
            while self.death_timer >= 0.065:
                self.death_timer -= 0.065
                if self.death_index < len(self.death_frames) - 1:
                    self.death_index += 1
                else:
                    self.remove = True
                    break
            self.y += 215.0 * dt
            if self.y > screen_height + 100:
                self.remove = True

    def hit_test(self, point: Tuple[int, int], margin: int = 22) -> bool:
        return self.alive and _alpha_hit(self.image, self.rect, point, margin)

    def shoot(self) -> None:
        self.alive = False
        self.death_index = 0
        self.death_timer = 0.0

    def draw(self, screen: pygame.Surface) -> None:
        screen.blit(self.image, self.rect)


@dataclass
class MillChicken:
    phase: int
    alive: bool = True
    death_stage: int = 0
    death_timer: float = 0.0


@dataclass
class PopupChicken:
    world_x: float
    state: str = "appearing"
    frame_index: int = 0
    frame_timer: float = 0.0
    visible_for: float = 0.0
    remove: bool = False


class ChickenApp(BaseApp):
    """Laser-Adaption der Spiellogik aus AlesyaRabushka/Moorhuhn."""

    name = "Moorhuhn"
    GAME_DURATION = 90.0
    COUNTDOWN_DURATION = 3.1
    SPAWN_INTERVAL = 4.0
    BIG_FIRST_DELAY = 0.67
    BIG_INTERVAL = 5.67
    # Live auf dem Projektor gemessene rote Animationskanten lagen höchstens
    # bei Rotüberschuss 63 / Änderung 88. Ein echter Laser muss entweder klar
    # röter oder als wesentlich stärkerer kurzer Helligkeitssprung erscheinen.
    LASER_RED_EXCESS = 150
    LASER_FALLBACK_RED_EXCESS = 55
    LASER_FALLBACK_DELTA = 125

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        persist_scores: bool = True,
        random_seed: int = 20260722,
    ) -> None:
        super().__init__(screen)
        self.random = random.Random(random_seed)
        self.persist_scores = persist_scores
        self.scale = self.screen.get_height() / 600.0
        self.sounds = MoorhuhnSounds(audio_enabled)
        self.ui_font_small = pygame.font.SysFont("Arial", 17)
        self.ui_font = pygame.font.SysFont("Arial", 22)
        self.ui_font_large = pygame.font.SysFont("Arial", 35, bold=True)
        self.ui_font_title = pygame.font.SysFont("Arial", 50, bold=True)
        self.ui_font_countdown = pygame.font.SysFont("Arial", 116, bold=True)
        self.font_small = self.ui_font_small
        self.font = self.ui_font
        self.font_large = self.ui_font_title
        self._load_assets()

        width, height = screen.get_size()
        self.style_veil = pygame.Surface((width, height), pygame.SRCALPHA)
        self.style_veil.fill((0, 8, 16, 58))
        self.subdued_veil = pygame.Surface((width, height), pygame.SRCALPHA)
        self.subdued_veil.fill((0, 7, 14, 185))
        self.menu_button = pygame.Rect(width - 170, 24, 140, 40)
        self.pan_left_button = pygame.Rect(22, height // 2 - 78, 112, 156)
        self.pan_right_button = pygame.Rect(width - 134, height // 2 - 78, 112, 156)
        self.ready_target_button = pygame.Rect(0, 0, 700, 400)
        self.ready_target_button.center = (width // 2, height // 2 + 22)
        ready_button_y = height // 2 + 118
        self.ready_start_button = pygame.Rect(width // 2 - 315, ready_button_y, 360, 58)
        self.ready_score_button = pygame.Rect(width // 2 + 65, ready_button_y, 250, 58)
        self.ready_menu_button = self.menu_button.copy()
        self.result_card = pygame.Rect(0, 0, 700, 450)
        self.result_card.center = (width // 2, height // 2)
        self.repeat_button = pygame.Rect(width // 2 - 294, height - 132, 276, 58)
        self.result_menu_button = pygame.Rect(width // 2 + 18, height - 132, 276, 58)

        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.deadline = 0.0
        self.time_left = self.GAME_DURATION
        self.score = 0
        self.best_score = self._load_best_score()
        self.shots = 0
        self.hits = 0
        self.flying: list[FlyingChicken] = []
        self.popups: list[PopupChicken] = []
        self.score_popups: list[ScorePopup] = []
        self.spawn_timer = 0.0
        self.big_timer = 0.0
        self.next_big_delay = self.BIG_FIRST_DELAY
        self.warned_seconds: set[int] = set()
        self.camera = 0.0
        self.camera_target = 0.0
        self.pumpkin_alive = True
        self.pumpkin_frame = 0
        self.pumpkin_timer = 0.0
        self.mill: list[MillChicken] = []
        self.mill_frame_timer = 0.0
        self.shot_marker: Optional[Tuple[int, int]] = None
        self.shot_marker_until = 0.0
        self.finish_reason = ""

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def visual_transition_active(self) -> bool:
        """True während großer Bildwechsel, die DLP-Farbblitze erzeugen."""

        return any(
            popup.state in {"appearing", "disappearing", "dead"}
            for popup in self.popups
        )

    @classmethod
    def is_laser_signature(cls, peak_red_excess: int, peak_delta: int) -> bool:
        """Trennt den Laserimpuls von roten Kämmen der Originalanimationen."""

        return peak_red_excess >= cls.LASER_RED_EXCESS or (
            peak_red_excess >= cls.LASER_FALLBACK_RED_EXCESS
            and peak_delta >= cls.LASER_FALLBACK_DELTA
        )

    def _load_assets(self) -> None:
        self.flight_source = _load_original_moorhuhn_sequence(
            "chicken_flight", [f"chicken{index}.png" for index in range(1, 13)], (100, 100)
        )
        self.death_source = _load_original_moorhuhn_sequence(
            "chicken_flight_death", [f"chickendead{index}.png" for index in range(1, 9)], (100, 100)
        )
        big_size = round(300 * self.scale)
        self.big_frames = _load_original_moorhuhn_sequence(
            "big_chicken", [f"big_chicken{index}.png" for index in range(19)], (big_size, big_size)
        )
        self.big_death_frames = _load_original_moorhuhn_sequence(
            "big_chicken", [f"big_chicken_dead{index}.png" for index in range(6)], (big_size, big_size)
        )
        pumpkin_size = round(100 * self.scale)
        self.pumpkin_frames = [
            _pumpkin_surface(pumpkin_size, index, 9)
            for index in range(9)
        ]
        self.tree_surfaces = [
            self._load_tree_asset(
                filename,
                round(width * self.scale),
                self.screen.get_height(),
            )
            for filename, width in (("trunkBig1.png", 135), ("trunkSmall1.png", 78))
        ]
        mill_size = round(200 * self.scale)
        self.mill_frames = _load_original_moorhuhn_sequence(
            "mill", [f"chickenwindmil{index}.png" for index in range(1, 37)], (mill_size, mill_size)
        )
        self.mill_death = {
            (frame, stage): _load_original_moorhuhn_sequence(
                "mill", [f"chickenwindmildead{frame}_{stage}.png"], (mill_size, mill_size)
            )[0]
            for frame in range(1, 37)
            for stage in (1, 2)
        }
        pan_size = (112, 156)
        self.pan_surfaces = {
            (direction, enabled): _wood_pan_surface(pan_size, direction, enabled)
            for direction in (-1, 1)
            for enabled in (False, True)
        }
        self.background_layers = self._build_world_layers()
        self.flight_cache: dict[tuple[int, int], tuple[list[pygame.Surface], list[pygame.Surface]]] = {}
        LOGGER.info(
            "Moorhuhn-Originalgrafiken gepuffert: Flug=%s Tod=%s Groß=%s Mühle=%s Kürbis=%s",
            len(self.flight_source),
            len(self.death_source),
            len(self.big_frames),
            len(self.mill_frames) + len(self.mill_death),
            len(self.pumpkin_frames),
        )

    @staticmethod
    def _load_tree_asset(filename: str, width: int, height: int) -> pygame.Surface:
        """Lädt den originalen Fotostamm sauber freigestellt und laserneutral."""

        path = MOORHUHN_DIR / "img" / "world" / filename
        try:
            source = pygame.image.load(str(path)).convert_alpha()
            pixels = pygame.surfarray.pixels3d(source)
            alpha = pygame.surfarray.pixels_alpha(source)
            red = pixels[:, :, 0].astype("int16")
            green = pixels[:, :, 1].astype("int16")
            blue = pixels[:, :, 2].astype("int16")
            # Die Original-PNGs besitzen an wenigen freigestellten Kanten noch
            # magentafarbene Matte-Pixel. Sie werden vollständig transparent,
            # statt später als rote DLP-Kante oder künstliche Kontur zu wirken.
            matte = (
                (red >= 55)
                & (blue >= 55)
                & (green <= 95)
                & (red - green >= 24)
                & (blue - green >= 24)
            )
            alpha[matte] = 0
            del alpha, pixels
            neutralize_laser_red(source)
            limit_projected_brightness(source, 154)
            fitted = pygame.transform.smoothscale(source, (max(2, width), max(2, height)))
            return fitted
        except (OSError, pygame.error) as exc:
            LOGGER.warning("Moorhuhn-Fotostamm %s fehlt: %s", filename, exc)
            return _tree_surface(width, height)

    def _build_world_layers(self) -> list[tuple[pygame.Surface, int, float]]:
        width, height = self.screen.get_size()
        travels = (96.0 * self.scale, 240.0 * self.scale, 720.0 * self.scale, 1900.0 * self.scale)
        base = build_theme_background((width, height), "moorhuhn_game").copy()
        # Mond, Wolken und Nebel wurden vom Beamer deutlich heller abgebildet
        # als am Monitor. Die feste Reserve von 123 Kanalstufen verhindert,
        # dass der rote Laser auf diesen Flächen weiß ausbrennt. Farbton und
        # lokale Kontraste des Originalbilds bleiben dabei vollständig erhalten.
        limit_projected_brightness(base, 132)
        sky = pygame.transform.smoothscale(
            base,
            (width + round(travels[0]), height),
        )

        # Transparente Tiefenlagen erhalten die originale Zielreihenfolge:
        # kleine Hühner fliegen weit hinten, mittlere vor dem Tal und große im
        # Vordergrund. Nur dezente Nebel-/Graskanten werden zusätzlich bewegt.
        hills = pygame.Surface((width + round(travels[1]), height), pygame.SRCALPHA)
        haze_y = round(height * 0.63)
        pygame.draw.ellipse(
            hills,
            (0, 75, 96, 28),
            pygame.Rect(-100, haze_y - 55, hills.get_width() + 200, 155),
        )

        village = pygame.Surface((width + round(travels[2]), height), pygame.SRCALPHA)
        for x in range(80, village.get_width(), 260):
            pygame.draw.circle(
                village,
                (0, 132, 140, 65),
                (x, round(height * 0.72) + (x % 37)),
                2,
            )

        meadow = pygame.Surface((width + round(travels[3]), height), pygame.SRCALPHA)
        meadow_y = round(height * 0.91)
        for x in range(30, meadow.get_width(), 150):
            pygame.draw.ellipse(
                meadow,
                (0, 92, 96, 30),
                pygame.Rect(x - 80, meadow_y + (x % 11), 170, 22),
            )

        return [
            (sky, 0, travels[0]),
            (hills, 0, travels[1]),
            (village, 0, travels[2]),
            (meadow, 0, travels[3]),
        ]

    def _flight_frames(self, original_size: int, direction: int) -> tuple[list[pygame.Surface], list[pygame.Surface]]:
        key = (original_size, direction)
        if key not in self.flight_cache:
            width = round(original_size * self.scale)
            height = width
            flight = [
                _scaled(frame, (width, height))
                for frame in self.flight_source
            ]
            death = [
                _scaled(frame, (width, height))
                for frame in self.death_source
            ]
            # Die Quelldateien blicken nach links; für Rechtsflug spiegeln.
            if direction > 0:
                flight = [pygame.transform.flip(frame, True, False) for frame in flight]
                death = [pygame.transform.flip(frame, True, False) for frame in death]
            self.flight_cache[key] = (flight, death)
        return self.flight_cache[key]

    def start(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.state = "ready"
        self.state_started = current
        self.last_update = current
        self.time_left = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.finish_reason = ""
        self.flying.clear()
        self.popups.clear()
        self.score_popups.clear()
        self.camera = 0.0
        self.camera_target = 0.0
        # Im Schießkino gibt es bewusst keine durchgehende Menü-Musik.
        self.sounds.stop_music()
        LOGGER.info("Moorhuhn bereit")

    def stop(self) -> None:
        self.sounds.stop_all()

    def begin_countdown(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.sounds.stop_music()
        self.sounds.play("game_start")
        LOGGER.info("Moorhuhn gestartet: 90 Sekunden, unbegrenzte Munition")

    def _begin_playing(self, now: float) -> None:
        self.state = "playing"
        self.state_started = now
        self.deadline = now + self.GAME_DURATION
        self.time_left = self.GAME_DURATION
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.spawn_timer = 0.0
        self.big_timer = 0.0
        self.next_big_delay = self.BIG_FIRST_DELAY
        self.warned_seconds.clear()
        self.flying.clear()
        self.popups.clear()
        self.score_popups.clear()
        self.pumpkin_alive = True
        self.pumpkin_frame = 0
        self.pumpkin_timer = 0.0
        self.mill = [MillChicken(phase) for phase in (27, 0, 9, 18)]
        self.mill_frame_timer = 0.0
        self.camera = 0.0
        self.camera_target = 0.0
        self._spawn_wave()
        # Auch während der Runde bleibt der originale Ambient-Loop aus. Kurze
        # Schuss-, Treffer-, Ziel- und Zeitsignale werden weiterhin abgespielt.
        self.sounds.stop_music()

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if nearest_laser_button(pos, (("menu", self.menu_button),)) == "menu":
            self.sounds.play("button")
            return "menu"

        if self.state == "ready":
            ready_choice = nearest_laser_button(
                pos,
                (
                    ("menu", self.ready_menu_button),
                    ("scores", self.ready_score_button),
                    ("start", self.ready_start_button),
                ),
            )
            if ready_choice == "menu":
                self.sounds.play("button")
                return "menu"
            if ready_choice == "scores":
                self.sounds.play("button")
                self.state = "scores"
                self.state_started = current
                return "handled"
            if (
                ready_choice == "start"
                or self.ready_target_button.collidepoint(pos)
            ):
                self.sounds.play("button")
                self.begin_countdown(current)
            return "handled"

        if self.state == "scores":
            self.sounds.play("button")
            self.state = "ready"
            self.state_started = current
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
                self.sounds.play("button")
                self.begin_countdown(current)
            return "handled"

        if self.state != "playing":
            return "handled"

        pan = nearest_laser_button(
            pos,
            (("left", self.pan_left_button), ("right", self.pan_right_button)),
        )
        if pan == "left":
            self.camera_target = max(0.0, self.camera_target - 0.34)
            self.sounds.play("button")
            return "pan"
        if pan == "right":
            self.camera_target = min(1.0, self.camera_target + 0.34)
            self.sounds.play("button")
            return "pan"

        self.shots += 1
        self.sounds.play("shot")
        self.shot_marker = pos
        self.shot_marker_until = current + 0.34

        # Trefferreihenfolge wie im Original: großes Huhn, Bäume, Flug-Hühner,
        # Windmühle und zuletzt der Kürbis. Das Strafschild existiert nicht mehr.
        if self._shoot_popup(pos, current):
            return "hit"
        if self._shoot_tree(pos):
            return "tree"
        if self._shoot_flying(pos, current):
            return "hit"
        if self._shoot_mill(pos, current):
            return "hit"
        if self._shoot_pumpkin(pos, current):
            return "hit"

        LOGGER.info("Moorhuhn Fehlschuss: %s", pos)
        return "miss"

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        dt = max(0.0, min(0.1, current - self.last_update))
        self.last_update = current

        if self.state in {"ready", "scores"}:
            return
        if self.state == "countdown":
            if current - self.state_started >= self.COUNTDOWN_DURATION:
                self._begin_playing(current)
            return
        if self.state != "playing":
            return

        camera_step = 0.78 * dt
        if self.camera < self.camera_target:
            self.camera = min(self.camera_target, self.camera + camera_step)
        elif self.camera > self.camera_target:
            self.camera = max(self.camera_target, self.camera - camera_step)

        self.time_left = max(0.0, self.deadline - current)
        remaining_second = int(math.ceil(self.time_left))
        if 1 <= remaining_second <= 10 and remaining_second not in self.warned_seconds:
            self.warned_seconds.add(remaining_second)
            self.sounds.play("time")

        self.spawn_timer += dt
        if self.spawn_timer >= self.SPAWN_INTERVAL:
            self.spawn_timer -= self.SPAWN_INTERVAL
            self._spawn_wave()

        self.big_timer += dt
        if self.big_timer >= self.next_big_delay:
            self.big_timer = 0.0
            self.next_big_delay = self.BIG_INTERVAL
            self._spawn_big_chicken()

        for chicken in self.flying:
            chicken.update(dt, self.screen.get_width(), self.screen.get_height())
        self.flying = [chicken for chicken in self.flying if not chicken.remove]
        self._update_popup_chickens(dt)
        self._update_pumpkin(dt)
        self._update_mill(dt)
        self.score_popups = [popup for popup in self.score_popups if current - popup.born_at < 1.25]

        if self.time_left <= 0.0:
            self._finish(current)

    def _spawn_wave(self) -> None:
        for kind, size, speed, points, y_range in (
            ("small", 40, 95.0, 20, (100, 200)),
            ("middle", 60, 100.0, 15, (100, 300)),
            ("big", 80, 150.0, 10, (100, 500)),
        ):
            direction = self.random.choice((-1, 1))
            frames, deaths = self._flight_frames(size, direction)
            x = -frames[0].get_width() // 2 if direction > 0 else self.screen.get_width() - frames[0].get_width() // 2
            y = self.random.uniform(y_range[0] * self.scale, y_range[1] * self.scale)
            self.flying.append(
                FlyingChicken(
                    kind=kind,
                    frames=frames,
                    death_frames=deaths,
                    x=float(x),
                    y=float(y),
                    speed=speed * self.scale,
                    points=points,
                    direction=direction,
                )
            )
        LOGGER.info("Moorhuhn: drei neue Flug-Hühner")

    def _spawn_big_chicken(self) -> None:
        world_x = self.random.uniform(100.0, 1700.0)
        self.popups.append(PopupChicken(world_x=world_x))
        self.sounds.play("big_popup")

    def _update_popup_chickens(self, dt: float) -> None:
        for popup in self.popups:
            if popup.state == "holding":
                # Nach dem Auftauchen bleibt das Ziel stabil. Das bewahrt die
                # originale Sichtdauer, ohne dass wechselnde Großbilder selbst
                # rote DLP-Impulse erzeugen.
                popup.frame_index = 8
                popup.visible_for += dt
                if popup.visible_for >= 3.5:
                    popup.state = "disappearing"
                    popup.frame_index = 17
                    popup.frame_timer = 0.0
                continue
            popup.frame_timer += dt
            interval = 0.055
            while popup.frame_timer >= interval:
                popup.frame_timer -= interval
                if popup.state == "appearing":
                    popup.frame_index += 1
                    if popup.frame_index >= len(self.big_frames) - 1:
                        popup.frame_index = 8
                        popup.state = "holding"
                elif popup.state == "disappearing":
                    popup.frame_index -= 1
                    if popup.frame_index <= 0:
                        popup.remove = True
                        break
                elif popup.state == "dead":
                    popup.frame_index += 1
                    if popup.frame_index >= len(self.big_death_frames):
                        popup.remove = True
                        break
        self.popups = [popup for popup in self.popups if not popup.remove]

    def _update_pumpkin(self, dt: float) -> None:
        if self.pumpkin_alive or self.pumpkin_frame >= len(self.pumpkin_frames) - 1:
            return
        self.pumpkin_timer += dt
        while self.pumpkin_timer >= 0.065:
            self.pumpkin_timer -= 0.065
            self.pumpkin_frame = min(self.pumpkin_frame + 1, len(self.pumpkin_frames) - 1)

    def _update_mill(self, dt: float) -> None:
        self.mill_frame_timer += dt
        if self.mill_frame_timer >= 0.05:
            self.mill_frame_timer %= 0.05
            for chicken in self.mill:
                if chicken.alive:
                    chicken.phase = (chicken.phase + 1) % len(self.mill_frames)
                else:
                    chicken.death_timer += 0.05
                    if chicken.death_timer >= 0.075:
                        chicken.death_timer = 0.0
                        chicken.death_stage += 1

    def _world_screen_x(self, original_x: float) -> int:
        return round(original_x * self.scale - self.camera * 1900.0 * self.scale)

    def _pumpkin_rect(self) -> pygame.Rect:
        return self.pumpkin_frames[self.pumpkin_frame].get_rect(
            center=(self._world_screen_x(2110), round(410 * self.scale))
        )

    def _tree_rects(self) -> list[pygame.Rect]:
        return [
            self.tree_surfaces[0].get_rect(center=(self._world_screen_x(300), self.screen.get_height() // 2)),
            self.tree_surfaces[1].get_rect(center=(self._world_screen_x(1900), self.screen.get_height() // 2)),
        ]

    def _mill_rect(self, image: Optional[pygame.Surface] = None) -> pygame.Rect:
        surface = image if image is not None else self.mill_frames[0]
        return surface.get_rect(bottomleft=(self._world_screen_x(2380), round(310 * self.scale)))

    def _popup_rect(self, popup: PopupChicken, image: pygame.Surface) -> pygame.Rect:
        return image.get_rect(center=(self._world_screen_x(popup.world_x), round(450 * self.scale)))

    @staticmethod
    def _generous_hit(rect: pygame.Rect, point: Tuple[int, int], margin: int = 12) -> bool:
        return rect.inflate(margin * 2, margin * 2).collidepoint(point)

    def _add_score(self, points: int, point: Tuple[int, int], now: float) -> None:
        self.score += points
        self.score_popups.append(
            ScorePopup(
                text=f"{points:+d}",
                x=float(point[0]),
                y=float(point[1]),
                born_at=now,
                positive=points >= 0,
            )
        )

    def _shoot_popup(self, point: Tuple[int, int], now: float) -> bool:
        for popup in reversed(self.popups):
            if popup.state == "dead":
                continue
            image = self.big_frames[min(popup.frame_index, len(self.big_frames) - 1)]
            if _alpha_hit(image, self._popup_rect(popup, image), point, 14):
                popup.state = "dead"
                popup.frame_index = 0
                popup.frame_timer = 0.0
                self.hits += 1
                self._add_score(25, point, now)
                self.sounds.play_hit(self.random)
                LOGGER.info("Moorhuhn Treffer: großes Huhn +25")
                return True
        return False

    def _shoot_tree(self, point: Tuple[int, int]) -> bool:
        for rect in self._tree_rects():
            if rect.collidepoint(point):
                self.sounds.play("tree")
                return True
        return False

    def _shoot_flying(self, point: Tuple[int, int], now: float) -> bool:
        kind_order = ("big", "middle", "small")
        for kind in kind_order:
            for chicken in reversed(self.flying):
                if chicken.kind == kind and chicken.hit_test(point):
                    chicken.shoot()
                    self.hits += 1
                    self._add_score(chicken.points, point, now)
                    self.sounds.play_hit(self.random)
                    LOGGER.info("Moorhuhn Treffer: %s +%s", kind, chicken.points)
                    return True
        return False

    def _shoot_mill(self, point: Tuple[int, int], now: float) -> bool:
        for chicken in self.mill:
            if not chicken.alive:
                continue
            image = self.mill_frames[chicken.phase]
            rect = self._mill_rect(image)
            if not _alpha_hit(image, rect, point, 10):
                continue
            chicken.alive = False
            chicken.death_stage = 0
            chicken.death_timer = 0.0
            self.hits += 1
            self._add_score(25, point, now)
            self.sounds.play("mill")
            LOGGER.info("Moorhuhn Treffer: Windmühlen-Huhn +25")
            return True
        return False

    def _shoot_pumpkin(self, point: Tuple[int, int], now: float) -> bool:
        if not self.pumpkin_alive or not self._generous_hit(self._pumpkin_rect(), point, 10):
            return False
        self.pumpkin_alive = False
        self.pumpkin_timer = 0.0
        self.hits += 1
        self._add_score(15, point, now)
        self.sounds.play("pumpkin")
        LOGGER.info("Moorhuhn Treffer: Kürbis +15")
        return True

    def _finish(self, now: float) -> None:
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = "Die Zeit ist abgelaufen"
        self.best_score = max(self.best_score, self.score)
        self._save_best_score()
        self.sounds.stop_music()
        self.sounds.play("game_over")
        LOGGER.info(
            "Moorhuhn beendet: Punkte=%s Treffer=%s/%s Präzision=%.1f%%",
            self.score,
            self.hits,
            self.shots,
            self.accuracy,
        )

    def _load_best_score(self) -> int:
        if not self.persist_scores:
            return 0
        try:
            data = json.loads(SCORE_FILE.read_text(encoding="utf-8"))
            return max(0, int(data.get("best_score", 0)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0

    def _save_best_score(self) -> None:
        if not self.persist_scores:
            return
        try:
            SCORE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SCORE_FILE.write_text(
                json.dumps({"best_score": self.best_score}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("Moorhuhn-Bestwert konnte nicht gespeichert werden: %s", exc)

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        if self.state == "ready":
            self._draw_ready(current)
        elif self.state == "scores":
            self._draw_scores(current)
        elif self.state == "countdown":
            self._draw_world(current)
            self._draw_countdown(current)
        elif self.state == "playing":
            self._draw_world(current)
        else:
            self._draw_world(current, subdued=True)
            self._draw_result()

    def _draw_ready(self, now: float) -> None:
        draw_frame(self.screen, "moorhuhn", now)
        draw_ambient_foreground(self.screen, "moorhuhn", now)
        width = self.screen.get_width()
        title = self.ui_font_title.render("MOORHUHN", True, SAFE_CYAN)
        subtitle = self.ui_font.render(
            "90 SEKUNDEN  ·  UNBEGRENZTE SCHÜSSE  ·  ORIGINALWERTUNG",
            True,
            SAFE_GREEN,
        )
        if subtitle.get_width() > width - 330:
            subtitle = self.ui_font_small.render(
                "90 SEKUNDEN  ·  UNBEGRENZTE SCHÜSSE  ·  ORIGINALWERTUNG",
                True,
                SAFE_GREEN,
            )
        self.screen.blit(title, title.get_rect(midtop=(width // 2, 34)))
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(width // 2, 96)))

        card = self.ready_target_button
        draw_translucent_panel(
            self.screen, card, SAFE_PANEL, alpha=202, border_radius=16
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, card, 3, border_radius=16)
        draw_aim_point(self.screen, (card.right - 28, card.top + 28), SAFE_GREEN)
        heading = self.ui_font_large.render("SO FUNKTIONIERT ES", True, SAFE_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(card.centerx, card.top + 34)))
        lines = (
            "Triff die Moorhühner und die bekannten Sonderziele.",
            "Kleine und entfernte Ziele bringen mehr Punkte.",
            "Mit den Seitenflächen verschiebst du das Panorama.",
        )
        for index, line in enumerate(lines):
            text = self.ui_font.render(line, True, (0, 168, 198))
            self.screen.blit(text, text.get_rect(midtop=(card.centerx, card.top + 96 + index * 35)))
        draw_button(self.screen, self.ready_start_button, "SPIEL STARTEN", self.ui_font, SAFE_GREEN)
        draw_button(self.screen, self.ready_score_button, "BESTWERT", self.ui_font, SAFE_CYAN)
        draw_button(self.screen, self.menu_button, "MENÜ", self.ui_font_small, SAFE_CYAN)

    def _draw_scores(self, now: float) -> None:
        draw_frame(self.screen, "moorhuhn", now)
        draw_ambient_foreground(self.screen, "moorhuhn", now)
        card = self.result_card
        draw_translucent_panel(
            self.screen, card, SAFE_PANEL, alpha=202, border_radius=18
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, card, 3, border_radius=18)
        draw_aim_point(self.screen, (card.right - 28, card.top + 28), SAFE_GREEN)
        title = self.ui_font_title.render("BESTWERT", True, SAFE_CYAN)
        value = self.ui_font_countdown.render(str(self.best_score), True, SAFE_GREEN)
        hint = self.ui_font.render("AUF DAS FENSTER SCHIEßEN: ZURÜCK", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(card.centerx, card.top + 45)))
        self.screen.blit(value, value.get_rect(center=(card.centerx, card.centery)))
        self.screen.blit(hint, hint.get_rect(midbottom=(card.centerx, card.bottom - 48)))

    def _draw_world(self, now: float, subdued: bool = False) -> None:
        self.screen.fill((112, 150, 190))
        sky, hills, castle, meadow = self.background_layers
        self.screen.blit(sky[0], (-round(self.camera * sky[2]), sky[1]))
        draw_ambient_background(self.screen, "moorhuhn_game", now)
        self.screen.blit(hills[0], (-round(self.camera * hills[2]), hills[1]))
        for chicken in self.flying:
            if chicken.kind == "small":
                chicken.draw(self.screen)
        self.screen.blit(castle[0], (-round(self.camera * castle[2]), castle[1]))
        for chicken in self.flying:
            if chicken.kind == "middle":
                chicken.draw(self.screen)
        self.screen.blit(meadow[0], (-round(self.camera * meadow[2]), meadow[1]))
        draw_cinematic_overlay(self.screen)

        pumpkin_image = self.pumpkin_frames[self.pumpkin_frame]
        self.screen.blit(pumpkin_image, self._pumpkin_rect())
        self._draw_mill()
        for chicken in self.flying:
            if chicken.kind == "big":
                chicken.draw(self.screen)
        for tree, rect in zip(self.tree_surfaces, self._tree_rects()):
            self.screen.blit(tree, rect)
        draw_ambient_foreground(self.screen, "moorhuhn_game", now)
        self._draw_popup_chickens()
        self._draw_score_popups(now)

        self.screen.blit(self.style_veil, (0, 0))

        self._draw_hud()
        self._draw_pan_controls()
        self._draw_menu_button()

        if self.shot_marker is not None and now <= self.shot_marker_until:
            draw_aim_point(self.screen, self.shot_marker, SAFE_GREEN, 10)
        if subdued:
            self.screen.blit(self.subdued_veil, (0, 0))

    def _draw_mill(self) -> None:
        for chicken in self.mill:
            if chicken.alive:
                image = self.mill_frames[chicken.phase]
            elif chicken.death_stage <= 1:
                frame_number = chicken.phase + 1
                image = self.mill_death.get((frame_number, chicken.death_stage + 1), self.mill_frames[chicken.phase])
            else:
                continue
            self.screen.blit(image, self._mill_rect(image))

    def _draw_popup_chickens(self) -> None:
        for popup in self.popups:
            if popup.state == "dead":
                image = self.big_death_frames[min(popup.frame_index, len(self.big_death_frames) - 1)]
            else:
                image = self.big_frames[min(popup.frame_index, len(self.big_frames) - 1)]
            self.screen.blit(image, self._popup_rect(popup, image))

    def _draw_score_popups(self, now: float) -> None:
        for popup in self.score_popups:
            elapsed = now - popup.born_at
            color = SAFE_GREEN if popup.positive else SAFE_CYAN
            text = self.ui_font_large.render(popup.text, True, color)
            self.screen.blit(text, text.get_rect(center=(round(popup.x), round(popup.y - elapsed * 48))))

    def _draw_hud(self) -> None:
        title = self.ui_font_large.render("MOORHUHN", True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        draw_hud(
            self.screen,
            (
                ("PUNKTE", f"{self.score:05d}"),
                ("TREFFER", f"{self.hits}/{self.shots}"),
                ("ZEIT", f"{int(math.ceil(self.time_left)):02d}"),
                ("PRÄZISION", f"{self.accuracy:.0f} %"),
                ("BESTWERT", str(self.best_score)),
            ),
            self.ui_font_small,
            self.ui_font,
        )

    def _draw_pan_controls(self) -> None:
        for rect, direction, enabled in (
            (self.pan_left_button, -1, self.camera_target > 0.0),
            (self.pan_right_button, 1, self.camera_target < 1.0),
        ):
            self.screen.blit(self.pan_surfaces[(direction, enabled)], rect)

    def _draw_menu_button(self) -> None:
        draw_button(self.screen, self.menu_button, "MENÜ", self.ui_font_small, SAFE_CYAN)

    def _draw_countdown(self, now: float) -> None:
        draw_countdown(
            self.screen,
            self.state_started,
            now,
            self.ui_font_countdown,
            self.COUNTDOWN_DURATION,
        )

    def _draw_result(self) -> None:
        draw_result_card(
            self.screen,
            self.result_card,
            "GESAMTAUSWERTUNG",
            self.finish_reason,
            (
                ("PUNKTE", str(self.score)),
                ("TREFFER", f"{self.hits}/{self.shots}"),
                ("PRÄZISION", f"{self.accuracy:.0f} %"),
                ("BESTWERT", str(self.best_score)),
            ),
            self.repeat_button,
            self.result_menu_button,
            (
                self.ui_font,
                self.ui_font_large,
                self.ui_font_title,
                self.ui_font_small,
            ),
        )
