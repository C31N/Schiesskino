from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple, TypeVar

import numpy as np
import pygame

from ..constants import WEAPON_CALIBRATION_FILE
from ..weapon_calibration import load_weapon_calibration

LOGGER = logging.getLogger(__name__)

SAFE_BG = (0, 8, 16)
SAFE_PANEL = (0, 20, 32)
SAFE_PANEL_LIGHT = (0, 31, 46)
SAFE_CYAN = (0, 205, 245)
SAFE_GREEN = (0, 225, 120)
SAFE_MUTED = (0, 82, 118)
SAFE_BLUE = (0, 112, 190)
SAFE_DARK = (0, 13, 24)
# Zielgrafiken benötigen mehr Helligkeitsreserve als reine Bedienelemente.
# Diese Farben bleiben auch nach dem Aufaddieren des roten Projektorlasers
# deutlich rot-dominant und vermeiden gelbe bzw. weiße Sättigungsflächen.
TARGET_CYAN = (0, 136, 162)
TARGET_GREEN = (0, 148, 88)
TARGET_BLUE = (0, 88, 142)

ButtonKey = TypeVar("ButtonKey")
LASER_BUTTON_EXPANSION = (120, 96)
LASER_RESULT_EXPANSION = (260, 220)
NAME_KEY_ROWS = ("ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZÄÖÜ")
_THEME_BACKGROUND_CACHE: dict[tuple[Tuple[int, int], str], pygame.Surface] = {}
_TARGET_SPRITE_CACHE: OrderedDict[
    tuple[str, Tuple[int, int], bool, int, int], pygame.Surface
] = OrderedDict()
_TARGET_MASK_CACHE: OrderedDict[
    tuple[str, Tuple[int, int], bool, int, int], pygame.mask.Mask
] = OrderedDict()
_TARGET_SOURCE_CACHE: dict[tuple[str, int], pygame.Surface] = {}
_TARGET_SCALED_CACHE: OrderedDict[
    tuple[str, Tuple[int, int], int], pygame.Surface
] = OrderedDict()
_TRANSPARENT_LAYER_CACHE: dict[tuple[Tuple[int, int], str], pygame.Surface] = {}
_CINEMATIC_OVERLAY_CACHE: dict[Tuple[int, int], pygame.Surface] = {}
_VINTAGE_PANEL_CACHE: dict[tuple[Tuple[int, int], int, bool, int], pygame.Surface] = {}
MAX_TARGET_SPRITE_CACHE = 512


@dataclass(frozen=True)
class ThemeVisualProfile:
    """Versionierte Darstellung einer Spielwelt einschließlich Bewegungsart."""

    background_file: str
    motion_kind: str
    brightness_limit: int = 168


THEME_VISUAL_PROFILES = {
    "menu": ThemeVisualProfile("menu_background_v4.png", "arcade", 164),
    "leaderboard": ThemeVisualProfile("leaderboard_background_v1.png", "light", 148),
    "cans": ThemeVisualProfile("cans_background_v3.png", "dust"),
    "clay": ThemeVisualProfile("clay_background_v3.png", "sky"),
    "timed": ThemeVisualProfile("timed_background_v3.png", "light"),
    "reaction": ThemeVisualProfile("reaction_background_v3.png", "light"),
    "range": ThemeVisualProfile("range_background_v3.png", "light"),
    "balloons": ThemeVisualProfile("kids_balloons_background_v3.png", "mist"),
    "aliens": ThemeVisualProfile("kids_aliens_background_v3.png", "space"),
    "stars": ThemeVisualProfile("kids_stars_background_v3.png", "space"),
    "math": ThemeVisualProfile("kids_math_background_v3.png", "light"),
    "colors": ThemeVisualProfile("kids_colors_background_v3.png", "light"),
    "treasure": ThemeVisualProfile("kids_treasure_background_v3.png", "water"),
    # Der Projektor hebt Mond, Wolken und Nebel deutlich stärker an als ein
    # Monitor. 132 lässt dort genug Rotkanalreserve für schwache Laserpulse,
    # ohne die nächtliche Originalstimmung zu verlieren.
    "moorhuhn_game": ThemeVisualProfile("moorhuhn_arcade_background_v3.png", "sky", 132),
}

_THEME_ASSET_FILES = {
    "menu": "menu_background_v4.png",
    **{name: profile.background_file for name, profile in THEME_VISUAL_PROFILES.items() if name != "menu"},
}

_TARGET_ASSET_FILES = {
    "alien": "alien_v3.png",
    "balloon": "balloon_v3.png",
    "can": "can_v3.png",
    "chicken": "chicken_v3.png",
    "clay": "clay_v3.png",
    "mechanical_target": "mechanical_target_v3.png",
    "star": "star_v3.png",
    "treasure_chest": "treasure_chest_v3.png",
    "water_ball": "water_ball_v3.png",
    "water_dolphin": "water_dolphin_v3.png",
    "water_duck": "water_duck_v3.png",
    "water_leak": "water_leak_v3.png",
}


def neutralize_laser_red(surface: pygame.Surface) -> pygame.Surface:
    """Entfernt rote Lasersignaturen aus dekorativen Bitmap-Spielwelten."""

    pixels = pygame.surfarray.pixels3d(surface)
    red = pixels[:, :, 0].astype(np.int16)
    green = pixels[:, :, 1].astype(np.int16)
    blue = pixels[:, :, 2].astype(np.int16)
    mask = (red >= 70) & (red - np.maximum(green, blue) >= 28)
    if bool(mask.any()):
        pixels[:, :, 0][mask] = np.maximum(
            pixels[:, :, 1][mask], pixels[:, :, 2][mask]
        )
    del pixels
    return surface


def limit_projected_brightness(
    surface: pygame.Surface,
    max_channel: int = 170,
) -> pygame.Surface:
    """Begrenzt helle Zielpixel, damit ein roter Laser nicht weiß ausbrennt."""

    pixels = pygame.surfarray.pixels3d(surface)
    rgb = pixels.astype(np.float32)
    brightest = rgb.max(axis=2)
    scale = np.ones_like(brightest, dtype=np.float32)
    too_bright = brightest > max_channel
    scale[too_bright] = max_channel / brightest[too_bright]
    pixels[:, :, :] = np.clip(rgb * scale[:, :, None], 0, 255).astype(np.uint8)
    del pixels
    return surface


def load_target_sprite(
    name: str,
    size: Tuple[int, int],
    *,
    flip_x: bool = False,
    angle: float = 0.0,
    brightness_limit: int = 154,
) -> pygame.Surface:
    """Lädt ein freigestelltes 3D-Ziel laserneutral, skaliert und gepuffert."""

    safe_size = max(2, int(size[0])), max(2, int(size[1]))
    # Zwei-Grad-Schritte sind visuell flüssig, reduzieren bei frei rotierenden
    # Zielen aber Cacheaufbau und Transformationsarbeit auf ein Viertel.
    angle_key = int(round(angle / 2.0))
    key = name, safe_size, flip_x, angle_key, int(brightness_limit)
    cached = _TARGET_SPRITE_CACHE.get(key)
    if cached is not None:
        _TARGET_SPRITE_CACHE.move_to_end(key)
        return cached

    filename = _TARGET_ASSET_FILES.get(name)
    if filename is None:
        raise KeyError(f"Unbekanntes Zielmotiv: {name}")
    path = Path(__file__).resolve().parents[2] / "assets" / "arcade_targets" / filename
    try:
        source_key = name, int(brightness_limit)
        source = _TARGET_SOURCE_CACHE.get(source_key)
        if source is None:
            source = pygame.image.load(str(path))
            if pygame.display.get_surface() is not None:
                source = source.convert_alpha()
            bounds = source.get_bounding_rect(min_alpha=8)
            if bounds.width and bounds.height:
                source = source.subsurface(bounds).copy()
            neutralize_laser_red(source)
            # Ziele selbst dürfen keinerlei statischen Rotanteil besitzen:
            # Nur der echte Laser soll im Kamerabild Rot liefern.
            target_pixels = pygame.surfarray.pixels3d(source)
            target_pixels[:, :, 0] = 0
            del target_pixels
            limit_projected_brightness(source, brightness_limit)
            _TARGET_SOURCE_CACHE[source_key] = source
        scaled_key = name, safe_size, int(brightness_limit)
        sprite = _TARGET_SCALED_CACHE.get(scaled_key)
        if sprite is None:
            sprite = pygame.transform.smoothscale(source, safe_size)
            _TARGET_SCALED_CACHE[scaled_key] = sprite
            while len(_TARGET_SCALED_CACHE) > 128:
                _TARGET_SCALED_CACHE.popitem(last=False)
        else:
            _TARGET_SCALED_CACHE.move_to_end(scaled_key)
    except (FileNotFoundError, pygame.error) as exc:
        LOGGER.warning("3D-Zielmotiv %s fehlt; nutze sicheren Fallback: %s", path, exc)
        sprite = pygame.Surface(safe_size, pygame.SRCALPHA)
        pygame.draw.ellipse(sprite, SAFE_PANEL_LIGHT, sprite.get_rect().inflate(-4, -4))
        pygame.draw.ellipse(sprite, TARGET_CYAN, sprite.get_rect().inflate(-4, -4), 3)
    if flip_x:
        sprite = pygame.transform.flip(sprite, True, False)
    if angle_key:
        sprite = pygame.transform.rotate(sprite, angle_key * 2.0)
    _TARGET_SPRITE_CACHE[key] = sprite
    _TARGET_SPRITE_CACHE.move_to_end(key)
    while len(_TARGET_SPRITE_CACHE) > MAX_TARGET_SPRITE_CACHE:
        _TARGET_SPRITE_CACHE.popitem(last=False)
    return sprite


def _transparent_layer(size: Tuple[int, int], purpose: str) -> pygame.Surface:
    """Gibt eine wiederverwendete, geleerte Alphaebene ohne Frame-Allokation."""

    key = size, purpose
    layer = _TRANSPARENT_LAYER_CACHE.get(key)
    if layer is None:
        layer = pygame.Surface(size, pygame.SRCALPHA)
        _TRANSPARENT_LAYER_CACHE[key] = layer
    else:
        layer.fill((0, 0, 0, 0))
    return layer


def draw_target_sprite(
    screen: pygame.Surface,
    name: str,
    center: Tuple[int, int],
    size: Tuple[int, int],
    *,
    flip_x: bool = False,
    angle: float = 0.0,
    brightness_limit: int = 154,
) -> tuple[pygame.Rect, pygame.mask.Mask]:
    """Zeichnet ein Ziel und liefert die transformierte sichtbare Treffermaske."""

    sprite = load_target_sprite(
        name,
        size,
        flip_x=flip_x,
        angle=angle,
        brightness_limit=brightness_limit,
    )
    rect = sprite.get_rect(center=center)
    screen.blit(sprite, rect)
    safe_size = max(2, int(size[0])), max(2, int(size[1]))
    mask_key = (
        name,
        safe_size,
        flip_x,
        int(round(angle / 2.0)),
        int(brightness_limit),
    )
    mask = _TARGET_MASK_CACHE.get(mask_key)
    if mask is None:
        mask = pygame.mask.from_surface(sprite, 8)
        _TARGET_MASK_CACHE[mask_key] = mask
        while len(_TARGET_MASK_CACHE) > MAX_TARGET_SPRITE_CACHE:
            _TARGET_MASK_CACHE.popitem(last=False)
    else:
        _TARGET_MASK_CACHE.move_to_end(mask_key)
    return rect, mask


def sprite_hit_test(
    point: Tuple[int, int],
    rect: pygame.Rect,
    mask: pygame.mask.Mask,
    *,
    margin: int = 0,
) -> bool:
    """Prüft sichtbare Pixel; der kalibrierte Fangrand bleibt erhalten."""

    local = point[0] - rect.left, point[1] - rect.top
    if 0 <= local[0] < mask.get_size()[0] and 0 <= local[1] < mask.get_size()[1]:
        if mask.get_at(local):
            return True
    if margin <= 0 or not rect.inflate(margin * 2, margin * 2).collidepoint(point):
        return False
    outline = mask.outline()
    return any((px + rect.left - point[0]) ** 2 + (py + rect.top - point[1]) ** 2 <= margin ** 2 for px, py in outline)


def calibrated_hit_tolerance(
    screen_size: Tuple[int, int],
    calibration_path: Path = WEAPON_CALIBRATION_FILE,
) -> int:
    """Liefert einen sicheren Fangrand aus der aktuellen Einschießqualität."""

    calibration = load_weapon_calibration(calibration_path, screen_size)
    residual = calibration.residual_px if calibration.active else 0.0
    return max(20, min(42, math.ceil(residual * 1.35)))


def scaled_target_margin(base_tolerance: int, target_size: float) -> int:
    """Erweitert die sichtbare Zielfläche passend zur gerenderten Zielgröße."""

    return min(48, base_tolerance + max(2, round(target_size * 0.08)))


def nearest_laser_button(
    point: Tuple[int, int],
    buttons: Iterable[tuple[ButtonKey, pygame.Rect]],
    *,
    expansion: Tuple[int, int] = LASER_BUTTON_EXPANSION,
    group_rect: Optional[pygame.Rect] = None,
) -> Optional[ButtonKey]:
    """Ordnet einen Lasertreffer eindeutig der nächsten Schaltfläche zu.

    ``pygame.Rect.inflate`` vergrößert um die angegebene Gesamtbreite und
    -höhe. Der Standard entspricht daher 60 Pixeln Fangrand links/rechts und
    48 Pixeln oben/unten. Überlappende Fangbereiche bleiben eindeutig, weil
    immer die nächstgelegene Schaltflächenmitte gewinnt.
    """

    items = tuple(buttons)
    candidates = [
        (key, rect)
        for key, rect in items
        if rect.inflate(*expansion).collidepoint(point)
    ]
    if not candidates and group_rect is not None and group_rect.collidepoint(point):
        candidates = list(items)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (item[1].centerx - point[0]) ** 2
        + (item[1].centery - point[1]) ** 2,
    )[0]


def build_name_keyboard_layout(
    screen_size: Tuple[int, int],
) -> tuple[list[tuple[str, pygame.Rect]], pygame.Rect]:
    """Baut eine große, pistolenfreundliche Buchstabentastatur ohne Zahlen."""

    width, _ = screen_size
    left, right, top = 34, width - 34, 300
    gap, row_gap, key_height = 8, 10, 72
    available_width = right - left
    buttons: list[tuple[str, pygame.Rect]] = []
    for row_index, row in enumerate(NAME_KEY_ROWS):
        key_width = (available_width - (len(row) - 1) * gap) // len(row)
        row_width = len(row) * key_width + (len(row) - 1) * gap
        row_left = left + (available_width - row_width) // 2
        for column, key in enumerate(row):
            buttons.append(
                (
                    key,
                    pygame.Rect(
                        row_left + column * (key_width + gap),
                        top + row_index * (key_height + row_gap),
                        key_width,
                        key_height,
                    ),
                )
            )
    keyboard_rect = pygame.Rect(
        left,
        top,
        available_width,
        len(NAME_KEY_ROWS) * key_height + (len(NAME_KEY_ROWS) - 1) * row_gap,
    )
    return buttons, keyboard_rect


def build_theme_background(size: Tuple[int, int], theme: str = "default") -> pygame.Surface:
    """Lädt die eigene laserneutrale Spielwelt oder erzeugt den Fallback."""

    key = (size, theme)
    cached = _THEME_BACKGROUND_CACHE.get(key)
    if cached is not None:
        return cached

    asset_name = _THEME_ASSET_FILES.get(theme)
    if asset_name is not None:
        asset_path = (
            Path(__file__).resolve().parents[2]
            / "assets"
            / "arcade_themes"
            / asset_name
        )
        try:
            image = pygame.image.load(str(asset_path))
            if pygame.display.get_surface() is not None:
                image = image.convert()
            if image.get_size() != size:
                image = pygame.transform.smoothscale(image, size)
            neutralize_laser_red(image)
            # Helle Lampen oder Reflexe eines Bitmap-Hintergrunds dürfen am
            # Projektor nicht ausbrennen. Die Reserve macht den roten
            # Laserpuls auch auf diesen Stellen weiterhin eindeutig sichtbar.
            profile = THEME_VISUAL_PROFILES.get(theme)
            limit_projected_brightness(
                image,
                profile.brightness_limit if profile is not None else 168,
            )
            _THEME_BACKGROUND_CACHE[key] = image
            return image
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning(
                "Spielwelt %s konnte nicht geladen werden; nutze Fallback: %s",
                asset_path,
                exc,
            )

    width, height = size
    surface = pygame.Surface(size)
    palettes = {
        "default": ((0, 8, 16), (0, 20, 32)),
        "menu": ((0, 11, 28), (0, 35, 48)),
        "cans": ((0, 9, 20), (0, 32, 43)),
        "clay": ((0, 24, 58), (0, 88, 116)),
        "timed": ((0, 9, 27), (0, 36, 58)),
        "reaction": ((0, 7, 22), (0, 26, 46)),
        "range": ((0, 13, 24), (0, 43, 55)),
        "moorhuhn": ((0, 25, 56), (0, 94, 104)),
    }
    top, bottom = palettes.get(theme, palettes["default"])
    for y in range(height):
        blend = y / max(1, height - 1)
        color = tuple(round(top[index] + (bottom[index] - top[index]) * blend) for index in range(3))
        pygame.draw.line(surface, color, (0, y), (width, y))

    if theme == "menu":
        horizon = round(height * 0.58)
        # Perspektivische Arcade-Halle mit Lichtportal und Bodenraster.
        for inset in range(0, 280, 42):
            rect = pygame.Rect(inset, 70 + inset // 3, width - 2 * inset, max(80, horizon - 70 - inset // 2))
            pygame.draw.rect(surface, (0, 55 + inset // 10, 82 + inset // 9), rect, 2, border_radius=18)
        for x in range(0, width + 1, 86):
            pygame.draw.line(surface, (0, 87, 108), (width // 2, horizon), (x, height), 1)
        for row in range(1, 7):
            progress = row / 6
            y = round(horizon + (height - horizon) * progress * progress)
            pygame.draw.line(surface, (0, 78, 99), (0, y), (width, y), 1)
        for x in (54, width - 54):
            pygame.draw.rect(surface, (0, 27, 45), pygame.Rect(x - 24, 82, 48, horizon - 82), border_radius=10)
            for y in range(112, horizon, 74):
                pygame.draw.circle(surface, SAFE_CYAN, (x, y), 5, 2)
    elif theme == "clay":
        # Weite Außenanlage: Horizont, Hügelketten und zwei Wurfstände.
        horizon = height - 112
        for x, y, length in ((112, 150, 105), (356, 116, 138), (714, 165, 120), (875, 105, 82)):
            pygame.draw.line(surface, (0, 129, 158), (x, y), (x + length, y), 3)
            pygame.draw.circle(surface, (0, 170, 195), (x + length, y), 3)
        hills = [(0, horizon), (0, horizon-70), (150, horizon-145), (295, horizon-55), (470, horizon-130), (680, horizon-62), (835, horizon-128), (width, horizon-58), (width, horizon)]
        pygame.draw.polygon(surface, (0, 34, 52), hills)
        pygame.draw.lines(surface, (0, 116, 142), False, hills[1:-1], 3)
        pygame.draw.rect(surface, (0, 20, 34), pygame.Rect(0, horizon, width, height-horizon))
        for center_x in (105, width-105):
            stand = pygame.Rect(center_x-45, horizon-36, 90, 44)
            pygame.draw.rect(surface, (0, 47, 61), stand, border_radius=7)
            pygame.draw.rect(surface, SAFE_CYAN, stand, 2, border_radius=7)
    elif theme == "timed":
        # Digitale Zeit-Arena mit Tunnel, Taktmarken und zentraler Uhr.
        center = (width // 2, height // 2 + 44)
        for radius in range(330, 70, -52):
            pygame.draw.circle(surface, (0, 45 + radius // 10, 70 + radius // 11), center, radius, 2)
        for index in range(24):
            angle = math.tau * index / 24
            inner = (center[0] + round(math.cos(angle) * 250), center[1] + round(math.sin(angle) * 250))
            outer = (center[0] + round(math.cos(angle) * 274), center[1] + round(math.sin(angle) * 274))
            pygame.draw.line(surface, (0, 116, 145), inner, outer, 2 if index % 3 else 4)
        for x in range(82, width, 172):
            pygame.draw.line(surface, (0, 48, 70), (x, 155), (width//2, height), 2)
    elif theme == "reaction":
        # Signalwand: neun eingelassene Felder und verbindende Leiterbahnen.
        centers = [(width//2 + (column-1)*245, 250 + row*185) for row in range(3) for column in range(3)]
        for row in range(3):
            pygame.draw.line(surface, (0, 64, 88), centers[row*3], centers[row*3+2], 4)
        for column in range(3):
            pygame.draw.line(surface, (0, 64, 88), centers[column], centers[6+column], 4)
        for center in centers:
            pygame.draw.circle(surface, (0, 18, 34), center, 74)
            pygame.draw.circle(surface, (0, 72, 99), center, 74, 3)
            pygame.draw.circle(surface, (0, 112, 126), center, 6, 2)
    elif theme == "range":
        # Moderner Schießstand mit akustischen Wandpaneelen und Zielbahn.
        vanishing = (width // 2, 410)
        for x in (0, 245, 390, 634, 779, width):
            pygame.draw.line(surface, (0, 74, 88), vanishing, (x, height), 2)
        for x in range(26, width, 92):
            pygame.draw.rect(surface, (0, 29, 42), pygame.Rect(x, 82, 66, 270), border_radius=5)
            pygame.draw.rect(surface, (0, 78, 92), pygame.Rect(x, 82, 66, 270), 1, border_radius=5)
        for y in (500, 578, 654, 724):
            pygame.draw.line(surface, (0, 76, 86), (0, y), (width, y), 2)
        pygame.draw.circle(surface, (0, 42, 48), vanishing, 285, 3)
    elif theme == "moorhuhn":
        # Ruhige stilisierte Landschaft für Menü und Ergebnisansicht.
        pygame.draw.circle(surface, (0, 171, 182), (width-160, 132), 64)
        pygame.draw.circle(surface, (0, 46, 67), (width-160, 132), 48)
        back_hills = [(0, height), (0, 470), (180, 330), (360, 460), (570, 295), (770, 430), (width, 340), (width, height)]
        pygame.draw.polygon(surface, (0, 61, 70), back_hills)
        front_hills = [(0, height), (0, 580), (210, 450), (430, 590), (690, 430), (width, 560), (width, height)]
        pygame.draw.polygon(surface, (0, 36, 48), front_hills)
        pygame.draw.lines(surface, (0, 151, 134), False, front_hills[1:-1], 3)

    _THEME_BACKGROUND_CACHE[key] = surface
    return surface


def _ambient_cloud(
    screen: pygame.Surface,
    center: Tuple[int, int],
    size: int,
    color: Tuple[int, int, int],
) -> None:
    """Zeichnet eine ruhige, laserneutrale Wolkensilhouette."""

    x, y = center
    pygame.draw.ellipse(screen, color, pygame.Rect(x - size, y, size * 2, size // 2), 2)
    pygame.draw.ellipse(screen, color, pygame.Rect(x - size // 2, y - size // 3, size, size * 2 // 3), 2)
    pygame.draw.ellipse(screen, color, pygame.Rect(x - size, y - size // 6, size, size // 2), 2)


def _ambient_windmill(
    screen: pygame.Surface,
    center: Tuple[int, int],
    size: int,
    angle: float,
) -> None:
    """Erzeugtes Windrad mit langsam rotierenden Flügeln."""

    x, y = center
    base_y = y + size
    pygame.draw.polygon(
        screen,
        (0, 23, 32),
        ((x - size // 7, base_y), (x + size // 7, base_y), (x + 5, y), (x - 5, y)),
    )
    pygame.draw.lines(
        screen,
        (0, 92, 104),
        False,
        ((x - size // 7, base_y), (x - 5, y), (x + 5, y), (x + size // 7, base_y)),
        2,
    )
    hub = (x, y)
    for index in range(4):
        blade_angle = angle + index * math.pi / 2
        tip = (
            x + round(math.cos(blade_angle) * size * 0.58),
            y + round(math.sin(blade_angle) * size * 0.58),
        )
        side = (
            x + round(math.cos(blade_angle + 0.15) * size * 0.31),
            y + round(math.sin(blade_angle + 0.15) * size * 0.31),
        )
        pygame.draw.polygon(screen, (0, 54, 67), (hub, tip, side))
        pygame.draw.line(screen, (0, 132, 145), hub, tip, 2)
    pygame.draw.circle(screen, (0, 145, 151), hub, max(3, size // 18), 2)


def _ambient_rotor(
    screen: pygame.Surface,
    center: Tuple[int, int],
    radius: int,
    angle: float,
    color: Tuple[int, int, int] = (0, 105, 125),
) -> None:
    pygame.draw.circle(screen, (0, 20, 31), center, radius + 5)
    pygame.draw.circle(screen, color, center, radius + 5, 2)
    for index in range(6):
        blade_angle = angle + index * math.tau / 6
        inner = (
            center[0] + round(math.cos(blade_angle) * radius * 0.22),
            center[1] + round(math.sin(blade_angle) * radius * 0.22),
        )
        outer = (
            center[0] + round(math.cos(blade_angle + 0.26) * radius),
            center[1] + round(math.sin(blade_angle + 0.26) * radius),
        )
        pygame.draw.line(screen, color, inner, outer, 3)
    pygame.draw.circle(screen, (0, 39, 53), center, max(3, radius // 5))


def _ambient_stars(screen: pygame.Surface, now: float, *, dense: bool = False) -> None:
    width, height = screen.get_size()
    count = 42 if dense else 24
    for index in range(count):
        depth = 1 + index % 3
        x = round((37 + index * 149 + now * depth * 2.4) % (width + 18) - 9)
        y = 118 + (index * 83) % max(100, height - 250)
        pulse = 0.5 + 0.5 * math.sin(now * (0.8 + depth * 0.17) + index * 1.7)
        green = round(68 + pulse * 72)
        blue = min(162, green + 22)
        radius = 2 if pulse > 0.78 and index % 4 == 0 else 1
        pygame.draw.circle(screen, (0, green, blue), (x, y), radius)


def _ambient_bubbles(
    screen: pygame.Surface,
    now: float,
    *,
    count: int = 13,
    ceiling: int = 120,
) -> None:
    width, height = screen.get_size()
    travel = max(160, height - ceiling + 80)
    for index in range(count):
        radius = 3 + index % 5
        speed = 7 + index % 4 * 3
        x = 32 + (index * 173) % max(40, width - 64)
        x += round(math.sin(now * 0.65 + index) * (5 + index % 3 * 2))
        y = height + 24 - round((now * speed + index * 79) % travel)
        pygame.draw.circle(screen, (0, 96 + index % 3 * 16, 126 + index % 2 * 20), (x, y), radius, 1)


def _ambient_grass(
    screen: pygame.Surface,
    now: float,
    *,
    height: int = 48,
    color: Tuple[int, int, int] = (0, 68, 65),
    spacing: int = 31,
) -> None:
    width, screen_height = screen.get_size()
    for index, x in enumerate(range(-8, width + 16, spacing)):
        blade = height + (index * 11) % max(8, height // 2)
        sway = round(math.sin(now * 0.72 + index * 0.63) * (5 + index % 4))
        pygame.draw.line(
            screen,
            color,
            (x, screen_height),
            (x + sway, screen_height - blade),
            2 if index % 3 == 0 else 1,
        )


def _ambient_tree(
    screen: pygame.Surface,
    base: Tuple[int, int],
    size: int,
    now: float,
    phase: float,
) -> None:
    """Mehrteiliger Vordergrundbaum mit langsam schwingender Krone."""

    sway = round(math.sin(now * 0.46 + phase) * max(3, size * 0.045))
    base_x, base_y = base
    crown = (base_x + sway, base_y - size)
    trunk_width = max(5, size // 15)
    pygame.draw.polygon(
        screen,
        (0, 17, 24),
        (
            (base_x - trunk_width, base_y),
            (base_x + trunk_width, base_y),
            (crown[0] + trunk_width // 2, crown[1] + size // 3),
            (crown[0] - trunk_width // 2, crown[1] + size // 3),
        ),
    )
    branch_color = (0, 58, 57)
    pygame.draw.line(screen, branch_color, (base_x, base_y - size // 3), (crown[0] - size // 4, crown[1] + size // 3), 3)
    pygame.draw.line(screen, branch_color, (base_x, base_y - size // 2), (crown[0] + size // 4, crown[1] + size // 4), 3)
    for offset_x, offset_y, radius in (
        (0, 0, 0.26), (-0.20, 0.12, 0.20), (0.21, 0.13, 0.22), (0.0, 0.24, 0.24)
    ):
        pygame.draw.circle(
            screen,
            (0, 35, 39),
            (crown[0] + round(size * offset_x), crown[1] + round(size * offset_y)),
            max(8, round(size * radius)),
        )
        pygame.draw.circle(
            screen,
            (0, 82, 73),
            (crown[0] + round(size * offset_x), crown[1] + round(size * offset_y)),
            max(8, round(size * radius)),
            2,
        )


def draw_ambient_background(
    screen: pygame.Surface,
    theme: str,
    now: Optional[float] = None,
) -> None:
    """Zeichnet eine performante, thematische Bewegungsebene hinter den Zielen.

    Sämtliche Farben enthalten keinen Rotanteil. Dadurch liefern Wolken,
    Sterne, Windräder und Lichtläufe niemals selbst eine Lasersignatur.
    """

    current = time.monotonic() if now is None else now
    width, height = screen.get_size()
    if theme in {"stars", "aliens", "moorhuhn", "moorhuhn_game"}:
        _ambient_stars(screen, current, dense=theme in {"stars", "aliens"})
        if theme == "aliens":
            planet_x = round(width * 0.79 + math.sin(current * 0.17) * 18)
            planet_y = round(height * 0.29 + math.cos(current * 0.13) * 8)
            pygame.draw.circle(screen, (0, 31, 48), (planet_x, planet_y), 46)
            pygame.draw.circle(screen, (0, 105, 119), (planet_x, planet_y), 46, 2)
            pygame.draw.ellipse(screen, (0, 83, 112), pygame.Rect(planet_x - 68, planet_y - 17, 136, 34), 2)
    elif theme in {"clay", "balloons"}:
        for index, (base, y, size, speed) in enumerate(((80, 190, 42, 7), (510, 250, 58, 4), (850, 155, 35, 9))):
            x = round((base + current * speed) % (width + size * 3) - size)
            _ambient_cloud(screen, (x, y + round(math.sin(current * 0.23 + index) * 6)), size, (0, 92, 116))
        if theme == "clay":
            _ambient_windmill(screen, (width - 138, height - 205), 94, current * 0.34)
            _ambient_windmill(screen, (145, height - 168), 58, -current * 0.27)
    elif theme == "water":
        # Im Hallenbad keine Gras-, Halm- oder bildbreiten Bogenlinien mehr.
        # Kleine halbtransparente Reflexflächen lassen ausschließlich die
        # vorhandene fotorealistische Wasseroberfläche ruhig leben.
        overlay = _transparent_layer((width, height), "ambient-water")
        water_top = round(height * 0.55)
        for index in range(7):
            travel = max(1, width - 180)
            x = 90 + round((current * (9 + index * 1.7) + index * 173) % travel)
            y = water_top + 34 + (index * 47) % max(70, height - water_top - 70)
            wobble = round(math.sin(current * 0.55 + index) * 10)
            pygame.draw.ellipse(
                overlay,
                (0, 132 + index * 4, 158 + index * 5, 18),
                pygame.Rect(x - 28 + wobble, y, 56, 9),
            )
        screen.blit(overlay, (0, 0))
    elif theme == "treasure":
        _ambient_bubbles(screen, current, count=12)
        for index in range(5):
            offset = round(math.sin(current * 0.34 + index * 0.9) * 28)
            y = 170 + index * 105
            pygame.draw.arc(
                screen,
                (0, 75 + index * 8, 96 + index * 8),
                pygame.Rect(-90 + offset, y, width + 180, 78),
                0.15,
                math.pi - 0.15,
                2,
            )
    elif theme == "ocean":
        # Annas Unterwasserfoto enthält bereits eine natürliche Wasserfläche.
        # Die früher zusätzlich gezeichneten, bildbreiten Wellenbögen wirkten
        # auf der Leinwand wie horizontale Striche. Kleine Blasen beleben die
        # Szene, ohne das Foto oder vorbeischwimmende Ziele zu überlagern.
        _ambient_bubbles(screen, current, count=12)
    elif theme in {"timed", "math", "colors"}:
        centers = ((88, height - 130), (width - 92, 205))
        for index, center in enumerate(centers):
            _ambient_rotor(screen, center, 34 + index * 7, current * (0.34 if index == 0 else -0.27), (0, 83, 112))
        scan_angle = current * 0.24
        center = (width // 2, height // 2 + 44)
        scan_end = (
            center[0] + round(math.cos(scan_angle) * width * 0.43),
            center[1] + round(math.sin(scan_angle) * height * 0.38),
        )
        pygame.draw.line(screen, (0, 72, 93), center, scan_end, 2)
    elif theme in {"reaction", "range", "cans"}:
        for index, center in enumerate(((86, 210), (width - 86, height - 158))):
            _ambient_rotor(screen, center, 27 + index * 6, current * (0.48 - index * 0.81), (0, 78, 99))
        travel = max(1, width - 120)
        for index in range(4):
            x = 60 + round((current * (20 + index * 4) + index * 241) % travel)
            y = 178 + index * 112
            pygame.draw.circle(screen, (0, 116, 126), (x, y), 4, 1)
    elif theme == "tobia":
        for index in range(14):
            x = round((index * 97 + current * (5 + index % 3)) % (width + 40) - 20)
            y = 130 + (index * 61) % (height - 210)
            drift = round(math.sin(current * 0.7 + index) * 9)
            pygame.draw.ellipse(screen, (0, 76, 82), pygame.Rect(x + drift, y, 9, 4), 1)


def draw_ambient_foreground(
    screen: pygame.Surface,
    theme: str,
    now: Optional[float] = None,
) -> None:
    """Zeichnet eine sparsame bewegte Vordergrundebene für räumliche Tiefe."""

    current = time.monotonic() if now is None else now
    width, height = screen.get_size()
    if theme in {"water", "treasure"}:
        # Weiche Tiefenpartikel statt der früheren generischen Grasstriche.
        overlay = _transparent_layer((width, height), "foreground-water")
        for index in range(5):
            x = round((index * 223 + current * (7 + index)) % (width + 80) - 40)
            y = height - 24 - (index % 3) * 17
            pygame.draw.ellipse(
                overlay,
                (0, 74 + index * 6, 92 + index * 8, 22),
                pygame.Rect(x, y, 72 + index * 8, 12),
            )
        screen.blit(overlay, (0, 0))
    elif theme == "ocean":
        # Runde, langsam driftende Schwebeteilchen statt flacher Ellipsen. So
        # entstehen auch im Vordergrund keine horizontalen Strichmuster mehr.
        overlay = _transparent_layer((width, height), "foreground-ocean")
        for index in range(7):
            x = round((index * 157 + current * (4 + index % 3)) % (width + 40) - 20)
            y = height - 36 - (index * 43) % 145
            radius = 2 + index % 3
            pygame.draw.circle(
                overlay,
                (0, 82 + index * 4, 104 + index * 5, 26),
                (x, y),
                radius,
            )
        screen.blit(overlay, (0, 0))
    elif theme in {"clay", "tobia", "moorhuhn", "moorhuhn_game"}:
        # Die zwei Nebelflächen liegen ausschließlich am unteren Rand. Eine
        # Vollbild-Alphaebene würde trotzdem pro Frame 1024×768 Pixel mischen.
        # Kleine Teilflächen behalten dieselbe Optik bei wesentlich weniger
        # Speicherbandbreite – besonders wichtig für Moorhuhn auf dem Pi.
        overlay_size = (118, 50)
        for index, base_x in enumerate((36, width - 116)):
            drift = round(math.sin(current * 0.42 + index * 2.1) * 12)
            overlay = _transparent_layer(overlay_size, f"foreground-land-{index}")
            pygame.draw.ellipse(
                overlay,
                (0, 48, 55, 24),
                pygame.Rect(18, 12, 82, 24),
            )
            screen.blit(overlay, (base_x + drift - 18, height - 50))
    elif theme == "balloons":
        for index in range(12):
            x = round((index * 113 + current * (13 + index % 3 * 4)) % (width + 40) - 20)
            y = 190 + (index * 67) % (height - 230)
            sway = round(math.sin(current * 1.1 + index) * 8)
            pygame.draw.line(screen, (0, 92, 112), (x, y), (x + sway, y + 18), 2)
            pygame.draw.circle(screen, (0, 122, 136), (x, y), 3)
    elif theme in {"stars", "aliens"}:
        asteroid_x = round(width - 48 + math.sin(current * 0.21) * 14)
        asteroid_y = round(height - 120 + math.cos(current * 0.28) * 12)
        points = ((asteroid_x - 22, asteroid_y), (asteroid_x - 8, asteroid_y - 18), (asteroid_x + 18, asteroid_y - 9), (asteroid_x + 23, asteroid_y + 15), (asteroid_x - 5, asteroid_y + 21))
        pygame.draw.polygon(screen, (0, 20, 31), points)
        pygame.draw.lines(screen, (0, 82, 103), True, points, 2)
    else:
        # Technische Spiele erhalten wandernde nahe Rastermarken statt Natur.
        for index in range(7):
            x = round((current * (15 + index * 2) + index * 181) % (width + 80) - 40)
            y = height - 38 - (index % 3) * 18
            pygame.draw.line(screen, (0, 72, 92), (x, y), (x + 28, y - 7), 3)


def draw_cinematic_overlay(screen: pygame.Surface) -> None:
    """Gibt jeder Spielwelt Tiefe, ohne die Ziele oder den Laser zu überdecken."""

    width, height = screen.get_size()
    cache_key = width, height
    cached = _CINEMATIC_OVERLAY_CACHE.get(cache_key)
    if cached is not None:
        screen.blit(cached, (0, 0))
        return
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)

    # Weiche Randabdunklung statt eines flachen schwarzen Rahmens. Sämtliche
    # Farben bleiben bewusst laserneutral (Rotanteil immer null).
    steps = 10
    for step in range(steps):
        inset = step * 5
        alpha = max(2, 18 - step)
        pygame.draw.rect(
            overlay,
            (0, 3, 12, alpha),
            pygame.Rect(inset, inset, width - inset * 2, height - inset * 2),
            max(4, 10 - step // 2),
            border_radius=max(2, 18 - step),
        )

    # Sehr dezente Lichtkante oben und Spiegelung unten verbinden Foto- und
    # Vektorgrafiken zu einer gemeinsamen Arcade-Optik.
    for offset in range(36):
        alpha = round(22 * (1.0 - offset / 36.0))
        pygame.draw.line(overlay, (0, 88, 118, alpha), (20, 16 + offset), (width - 20, 16 + offset))
        pygame.draw.line(overlay, (0, 48, 72, alpha // 2), (20, height - 17 - offset), (width - 20, height - 17 - offset))
    _CINEMATIC_OVERLAY_CACHE[cache_key] = overlay
    screen.blit(overlay, (0, 0))


def draw_frame(
    screen: pygame.Surface,
    theme: str = "default",
    now: Optional[float] = None,
) -> None:
    screen.blit(build_theme_background(screen.get_size(), theme), (0, 0))
    draw_ambient_background(screen, theme, now)
    draw_cinematic_overlay(screen)


def draw_aim_point(
    screen: pygame.Surface,
    point: Tuple[int, int],
    color: Tuple[int, int, int] = SAFE_GREEN,
    radius: int = 8,
) -> None:
    pygame.draw.circle(screen, color, point, radius, 2)
    pygame.draw.line(screen, color, (point[0] - radius - 4, point[1]), (point[0] + radius + 4, point[1]), 1)
    pygame.draw.line(screen, color, (point[0], point[1] - radius - 4), (point[0], point[1] + radius + 4), 1)


def draw_translucent_panel(
    screen: pygame.Surface,
    rect: pygame.Rect,
    color: Tuple[int, int, int] = SAFE_PANEL,
    *,
    alpha: int = 190,
    border_radius: int = 9,
) -> None:
    """Zeichnet eine lesbare Box, ohne die Spielwelt vollständig zu verdecken."""

    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(
        panel,
        (*color, max(0, min(255, alpha))),
        panel.get_rect(),
        border_radius=border_radius,
    )
    screen.blit(panel, rect.topleft)
    if rect.width >= 28 and rect.height >= 18:
        highlight = (0, min(105, color[1] + 34), min(132, color[2] + 42))
        pygame.draw.line(
            screen,
            highlight,
            (rect.left + border_radius, rect.top + 1),
            (rect.right - border_radius, rect.top + 1),
            1,
        )


def build_vintage_enamel_panel(
    size: Tuple[int, int],
    variant: int = 0,
    *,
    active: bool = True,
    alpha: int = 238,
) -> pygame.Surface:
    """Erzeugt ein gepuffertes, laserneutrales Blechschild mit Patina."""

    safe_size = max(12, int(size[0])), max(12, int(size[1]))
    key = safe_size, int(variant) % 6, bool(active), int(alpha)
    cached = _VINTAGE_PANEL_CACHE.get(key)
    if cached is not None:
        return cached

    palettes = (
        ((0, 19, 27), (0, 49, 58), (0, 151, 154)),
        ((0, 18, 31), (0, 43, 69), (0, 129, 177)),
        ((0, 24, 29), (0, 63, 53), (0, 151, 109)),
        ((0, 15, 29), (0, 38, 56), (0, 111, 141)),
        ((0, 22, 31), (0, 53, 64), (0, 141, 132)),
        ((0, 17, 25), (0, 47, 49), (0, 118, 103)),
    )
    dark, enamel, edge = palettes[key[1]]
    if not active:
        enamel = (0, enamel[1] // 2, enamel[2] // 2)
        edge = (0, edge[1] // 2, edge[2] // 2)

    width, height = safe_size
    panel = pygame.Surface(safe_size, pygame.SRCALPHA)
    radius = min(17, max(7, min(width, height) // 8))
    pygame.draw.rect(panel, (*dark, alpha), panel.get_rect(), border_radius=radius)
    inner = panel.get_rect().inflate(-5, -5)
    pygame.draw.rect(panel, (*enamel, min(246, alpha)), inner, border_radius=max(4, radius - 3))

    # Emaille bekommt eine dezente vertikale Lichtkante und unregelmäßige,
    # dunkle Gebrauchsspuren. Alles bleibt ohne roten Farbanteil.
    for y in range(inner.top + 1, inner.bottom - 1):
        ratio = (y - inner.top) / max(1, inner.height - 1)
        shade = round((1.0 - abs(ratio - 0.34) * 1.7) * 14)
        shade = max(0, shade)
        pygame.draw.line(
            panel,
            (0, min(255, enamel[1] + shade), min(255, enamel[2] + shade), 38),
            (inner.left + radius, y),
            (inner.right - radius, y),
        )
    if width >= 120 and height >= 50:
        for index in range(6):
            x = 18 + ((index * 71 + key[1] * 23) % max(20, width - 36))
            y = 17 + ((index * 43 + key[1] * 31) % max(18, height - 34))
            length = 7 + index % 4 * 4
            pygame.draw.line(panel, (0, 9, 14, 58), (x, y), (min(width - 12, x + length), y - 1), 1)

    pygame.draw.rect(panel, (*edge, 235), panel.get_rect(), 3, border_radius=radius)
    pygame.draw.rect(panel, (0, 16, 22, 230), inner, 2, border_radius=max(4, radius - 3))
    for x, y in ((12, 12), (width - 13, 12), (12, height - 13), (width - 13, height - 13)):
        pygame.draw.circle(panel, (0, 10, 15, 240), (x, y), 5)
        pygame.draw.circle(panel, (*edge, 220), (x, y), 4, 1)
        pygame.draw.circle(panel, (0, 75, 82, 190), (x - 1, y - 1), 1)

    _VINTAGE_PANEL_CACHE[key] = panel
    return panel


def draw_vintage_enamel_panel(
    screen: pygame.Surface,
    rect: pygame.Rect,
    variant: int = 0,
    *,
    active: bool = True,
    alpha: int = 238,
    shadow: bool = False,
) -> None:
    """Zeichnet ein nostalgisches Blechschild ohne teure Neuberechnung."""

    if shadow:
        # Ein symmetrischer Rand vertieft das Schild, ohne eine zweite,
        # verschobene Fläche unter Text und Rahmen vorzutäuschen.
        shadow_rect = rect.inflate(8, 8)
        draw_translucent_panel(
            screen, shadow_rect, (0, 3, 8), alpha=96, border_radius=17
        )
    screen.blit(
        build_vintage_enamel_panel(rect.size, variant, active=active, alpha=alpha),
        rect.topleft,
    )


def draw_button(
    screen: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    color: Tuple[int, int, int] = SAFE_GREEN,
    *,
    aim: bool = True,
) -> None:
    variant = 2 if color == SAFE_GREEN else 1
    draw_vintage_enamel_panel(
        screen, rect, variant, alpha=242, shadow=False
    )
    pygame.draw.rect(screen, color, rect, 3, border_radius=11)
    if aim:
        draw_aim_point(screen, (rect.left + 13, rect.centery), color, 5)
    text = font.render(label, True, color)
    # Der Zielpunkt liegt dekorativ am linken Rand und darf die Beschriftung
    # nicht aus der geometrischen Mitte der eigentlichen Taste verschieben.
    screen.blit(text, text.get_rect(center=rect.center))


def draw_size_step_button(
    screen: pygame.Surface,
    rect: pygame.Rect,
    *,
    increase: bool,
    color: Tuple[int, int, int],
) -> None:
    """Zeichnet ein großes, auch aus Entfernung klares Plus oder Minus."""

    draw_vintage_enamel_panel(
        screen, rect, 1 if increase else 3, alpha=242, shadow=False
    )
    pygame.draw.rect(screen, color, rect, 2, border_radius=9)
    half_width = min(17, max(12, rect.width // 5))
    half_height = min(15, max(11, rect.height // 3))
    stroke = max(4, min(6, rect.height // 8))
    pygame.draw.line(
        screen,
        color,
        (rect.centerx - half_width, rect.centery),
        (rect.centerx + half_width, rect.centery),
        stroke,
    )
    if increase:
        pygame.draw.line(
            screen,
            color,
            (rect.centerx, rect.centery - half_height),
            (rect.centerx, rect.centery + half_height),
            stroke,
        )


def draw_hud(
    screen: pygame.Surface,
    values: Iterable[tuple[str, str]],
    label_font: pygame.font.Font,
    value_font: pygame.font.Font,
    *,
    top: int = 82,
) -> pygame.Rect:
    width = screen.get_width()
    items = tuple(values)
    hud = pygame.Rect(24, top, width - 48, 70)
    draw_translucent_panel(screen, hud, SAFE_PANEL, alpha=188, border_radius=10)
    pygame.draw.rect(screen, SAFE_MUTED, hud, 2, border_radius=10)
    segment = hud.width // max(1, len(items))
    for index, (label, value) in enumerate(items):
        center_x = hud.left + segment * index + segment // 2
        if index:
            divider_x = hud.left + segment * index
            pygame.draw.line(
                screen,
                (0, 58, 76),
                (divider_x, hud.top + 13),
                (divider_x, hud.bottom - 13),
                1,
            )
        label_surface = label_font.render(label, True, SAFE_MUTED)
        value_surface = value_font.render(value, True, SAFE_CYAN if index == 2 else SAFE_GREEN)
        screen.blit(label_surface, label_surface.get_rect(center=(center_x, hud.top + 20)))
        screen.blit(value_surface, value_surface.get_rect(center=(center_x, hud.top + 48)))
    return hud


def draw_ready_card(
    screen: pygame.Surface,
    title: str,
    subtitle: str,
    lines: Iterable[str],
    start_card: pygame.Rect,
    start_button: pygame.Rect,
    menu_button: pygame.Rect,
    fonts: tuple[pygame.font.Font, pygame.font.Font, pygame.font.Font, pygame.font.Font],
) -> None:
    font, font_large, font_title, font_small = fonts
    width = screen.get_width()
    heading = font_title.render(title, True, SAFE_CYAN)
    screen.blit(heading, heading.get_rect(midtop=(width // 2, 34)))
    sub = font.render(subtitle, True, SAFE_GREEN)
    screen.blit(sub, sub.get_rect(midtop=(width // 2, 96)))
    shadow = start_card.move(0, 10).inflate(18, 14)
    draw_translucent_panel(screen, shadow, (0, 3, 10), alpha=176, border_radius=24)
    draw_translucent_panel(screen, start_card, SAFE_PANEL, alpha=220, border_radius=20)
    pygame.draw.rect(screen, SAFE_CYAN, start_card, 3, border_radius=20)
    pygame.draw.line(
        screen,
        SAFE_GREEN,
        (start_card.left + 45, start_card.top + 7),
        (start_card.right - 45, start_card.top + 7),
        3,
    )
    draw_aim_point(screen, (start_card.right - 28, start_card.top + 28), SAFE_GREEN)
    ready = font_large.render("SO FUNKTIONIERT ES", True, (225, 250, 255))
    screen.blit(ready, ready.get_rect(midtop=(width // 2, start_card.top + 24)))
    for index, line in enumerate(lines):
        center_y = start_card.top + 102 + index * 55
        row = pygame.Rect(start_card.left + 28, center_y - 24, start_card.width - 56, 48)
        if index % 2 == 0:
            draw_translucent_panel(screen, row, SAFE_PANEL_LIGHT, alpha=125, border_radius=10)
        marker = (start_card.left + 59, center_y)
        pygame.draw.circle(screen, SAFE_DARK, marker, 21)
        pygame.draw.circle(screen, SAFE_GREEN if index != 1 else SAFE_CYAN, marker, 21, 3)
        number = font_small.render(str(index + 1), True, SAFE_GREEN)
        screen.blit(number, number.get_rect(center=marker))
        surface = font_small.render(line, True, (225, 250, 255))
        screen.blit(surface, surface.get_rect(midleft=(start_card.left + 91, center_y)))
    draw_button(screen, start_button, "SPIEL STARTEN", font, SAFE_GREEN)
    draw_button(screen, menu_button, "MENÜ", font_small, SAFE_CYAN)


def draw_countdown(
    screen: pygame.Surface,
    state_started: float,
    now: float,
    font: pygame.font.Font,
    duration: float = 3.35,
) -> None:
    elapsed = now - state_started
    number = max(0, 3 - int(elapsed))
    label = "LOS!" if elapsed >= duration - 0.35 else str(number)
    overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    overlay.fill((0, 8, 16, 150))
    screen.blit(overlay, (0, 0))
    text = font.render(label, True, SAFE_GREEN if label == "LOS!" else SAFE_CYAN)
    screen.blit(text, text.get_rect(center=screen.get_rect().center))


def draw_target_rings(
    screen: pygame.Surface,
    center: Tuple[int, int],
    radius: int,
    *,
    active: bool = True,
    rings: int = 5,
) -> None:
    outer = TARGET_GREEN if active else SAFE_MUTED
    inner = TARGET_CYAN if active else TARGET_BLUE
    halo = pygame.Surface((radius * 2 + 46, radius * 2 + 46), pygame.SRCALPHA)
    halo_center = halo.get_rect().center
    pygame.draw.circle(
        halo,
        (*inner, 30 if active else 12),
        halo_center,
        radius + 16,
        10,
    )
    screen.blit(halo, halo.get_rect(center=center))
    draw_target_sprite(
        screen,
        "mechanical_target",
        center,
        (radius * 2 + 18, radius * 2 + 18),
        brightness_limit=150,
    )
    if not active:
        veil = pygame.Surface((radius * 2 + 18, radius * 2 + 18), pygame.SRCALPHA)
        veil.fill((0, 8, 18, 92))
        screen.blit(veil, veil.get_rect(center=center))
    # Wenige Wertungslinien bleiben funktional, das Motiv selbst liefert aber
    # jetzt Material, Tiefe und den klaren sichtbaren Trefferkörper.
    for index in range(max(2, rings), 0, -1):
        ring_radius = max(5, round(radius * index / max(2, rings)))
        pygame.draw.circle(
            screen,
            outer if index % 2 else inner,
            center,
            ring_radius,
            2 if active else 1,
        )


def draw_result_card(
    screen: pygame.Surface,
    card: pygame.Rect,
    title: str,
    reason: str,
    values: Iterable[tuple[str, str]],
    repeat_button: pygame.Rect,
    menu_button: pygame.Rect,
    fonts: tuple[pygame.font.Font, pygame.font.Font, pygame.font.Font, pygame.font.Font],
) -> None:
    font, font_large, font_title, font_small = fonts
    veil = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    veil.fill((0, 7, 14, 185))
    screen.blit(veil, (0, 0))
    shadow = card.move(0, 10).inflate(18, 16)
    draw_translucent_panel(screen, shadow, (0, 2, 9), alpha=184, border_radius=24)
    draw_translucent_panel(screen, card, SAFE_PANEL, alpha=224, border_radius=20)
    pygame.draw.rect(screen, SAFE_GREEN, card, 3, border_radius=20)
    pygame.draw.line(screen, SAFE_CYAN, (card.left + 42, card.top + 8), (card.right - 42, card.top + 8), 3)
    draw_aim_point(screen, (card.right - 28, card.top + 28), SAFE_GREEN)
    heading = font_title.render(title, True, SAFE_CYAN)
    screen.blit(heading, heading.get_rect(midtop=(card.centerx, card.top + 24)))
    reason_surface = font.render(reason, True, SAFE_GREEN)
    screen.blit(reason_surface, reason_surface.get_rect(midtop=(card.centerx, card.top + 88)))
    items = tuple(values)
    width = card.width - 70
    segment = width // max(1, len(items))
    for index, (label, value) in enumerate(items):
        center_x = card.left + 35 + index * segment + segment // 2
        metric_card = pygame.Rect(
            card.left + 38 + index * segment,
            card.top + 145,
            max(80, segment - 6),
            95,
        )
        draw_translucent_panel(screen, metric_card, SAFE_PANEL_LIGHT, alpha=155, border_radius=11)
        pygame.draw.rect(screen, SAFE_MUTED, metric_card, 1, border_radius=11)
        label_surface = font_small.render(label, True, SAFE_MUTED)
        value_surface = font_large.render(value, True, SAFE_GREEN)
        screen.blit(label_surface, label_surface.get_rect(center=(center_x, card.top + 166)))
        screen.blit(value_surface, value_surface.get_rect(center=(center_x, card.top + 207)))
    hint = font_small.render("NOCH EINMAL ODER MENÜ WÄHLEN", True, SAFE_CYAN)
    screen.blit(hint, hint.get_rect(midtop=(card.centerx, card.bottom - 83)))
    draw_button(screen, repeat_button, "NOCH EINMAL", font_small, SAFE_GREEN)
    draw_button(screen, menu_button, "MENÜ", font_small, SAFE_CYAN)


def distance(first: Tuple[float, float], second: Tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])
