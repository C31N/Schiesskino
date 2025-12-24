from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Union

import pygame

from .base import BaseApp


MOORHUHN_DIR = Path(__file__).with_name("moorhuhn")
ASSETS_DIR = MOORHUHN_DIR / "img"
SOUNDS_DIR = MOORHUHN_DIR / "sounds"
FONTS_DIR = MOORHUHN_DIR / "fonts"

LOGGER = logging.getLogger(__name__)


class _SilentSound:
    def play(self, *_, **__) -> None:
        return


def _sorted_by_number(paths: List[Path]) -> List[Path]:
    def key(path: Path) -> int:
        digits = "".join(filter(str.isdigit, path.stem))
        return int(digits) if digits else 0

    return sorted(paths, key=key)


def _load_images(directory: Path) -> List[pygame.Surface]:
    return [pygame.image.load(str(path)).convert_alpha() for path in _sorted_by_number(list(directory.glob("*.png")))]


def _load_sound(path: Path) -> Union[pygame.mixer.Sound, _SilentSound]:
    try:
        return pygame.mixer.Sound(str(path))
    except Exception:
        LOGGER.warning("Konnte Sound nicht laden: %s", path, exc_info=True)
        return _SilentSound()


def _scale_frames(frames: List[pygame.Surface], size: Tuple[int, int], flip: bool) -> List[pygame.Surface]:
    scaled = [pygame.transform.smoothscale(frame, size) for frame in frames]
    if flip:
        scaled = [pygame.transform.flip(frame, True, False) for frame in scaled]
    return scaled


@dataclass
class ChickenSprite:
    frames: List[pygame.Surface]
    death_frames: List[pygame.Surface]
    rect: pygame.Rect
    speed: float
    points: int
    alive: bool = True
    frame_index: int = 0
    frame_timer: float = 0.0
    death_index: int = 0
    death_timer: float = 0.0
    remove: bool = False

    def update(self, dt: float, bounds_width: int) -> None:
        if self.alive:
            self.frame_timer += dt
            if self.frame_timer >= 0.08:
                self.frame_timer = 0.0
                self.frame_index = (self.frame_index + 1) % len(self.frames)
            self.rect.x += int(self.speed * dt)
            if self.rect.right < -120 or self.rect.left > bounds_width + 120:
                self.remove = True
        else:
            self.death_timer += dt
            if self.death_timer >= 0.1:
                self.death_timer = 0.0
                if self.death_index < len(self.death_frames) - 1:
                    self.death_index += 1
                else:
                    self.remove = True
            self.rect.y += int(120 * dt)

    def draw(self, screen: pygame.Surface) -> None:
        frame = self.frames[self.frame_index] if self.alive else self.death_frames[self.death_index]
        screen.blit(frame, self.rect)

    def shoot(self) -> None:
        if self.alive:
            self.alive = False
            self.death_index = 0
            self.death_timer = 0.0


class ChickenApp(BaseApp):
    name = "Moorhuhn"

    def __init__(self, screen: pygame.Surface):
        super().__init__(screen)
        self.audio_available = True
        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except Exception:
                LOGGER.warning("Audio konnte nicht initialisiert werden, fahre ohne Sound fort.", exc_info=True)
                self.audio_available = False
        self.base_flight_frames = _load_images(ASSETS_DIR / "chicken_flight")
        self.base_death_frames = _load_images(ASSETS_DIR / "chicken_flight_death")
        self.cursor_image, self.cursor_red = self._load_cursors()
        self.background_layers = self._load_background(screen)
        self.menu_background = pygame.image.load(str(ASSETS_DIR / "main_menu_background" / "main_menu.png")).convert()
        self.menu_logo = pygame.image.load(str(ASSETS_DIR / "main_menu_background" / "moorhuhn.png")).convert_alpha()
        self.font = pygame.font.Font(str(FONTS_DIR / "AA_Magnum.ttf"), 40)
        self.small_font = pygame.font.Font(str(FONTS_DIR / "AA_Magnum.ttf"), 28)
        self.sounds = self._load_sounds()
        self.pointer: Tuple[int, int] = (self.screen.get_width() // 2, self.screen.get_height() // 2)
        self.state = "menu"
        self.score = 0
        self.best_score = 0
        self.time_left = 90.0
        self.spawn_timer = 0.0
        self.spawn_interval = 1.4
        self.chickens: List[ChickenSprite] = []
        self.flash_timer = 0.0
        self.crosshair_flash = False
        self.ambient_channel: pygame.mixer.Channel | None = None

    def _load_cursors(self) -> Tuple[pygame.Surface, pygame.Surface]:
        cursor_path = ASSETS_DIR / "cursor" / "cursor.png"
        cursor_red_path = ASSETS_DIR / "cursor" / "cursorred.png"

        base_cursor = pygame.image.load(str(cursor_path)).convert_alpha()

        if cursor_red_path.exists():
            cursor_red = pygame.image.load(str(cursor_red_path)).convert_alpha()
        else:
            LOGGER.warning("Roter Cursor fehlt (%s), verwende eingefärbte Version des Standards.", cursor_red_path)
            cursor_red = base_cursor.copy()
            tint = pygame.Surface(cursor_red.get_size(), pygame.SRCALPHA)
            tint.fill((255, 0, 0, 120))
            cursor_red.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

        return base_cursor, cursor_red

    def _load_sounds(self) -> Dict[str, Union[pygame.mixer.Sound, _SilentSound]]:
        if not self.audio_available:
            return {
                "shot": _SilentSound(),
                "hit": [_SilentSound(), _SilentSound(), _SilentSound()],
                "ambient": _SilentSound(),
                "game_over": _SilentSound(),
                "main_theme": _SilentSound(),
                "button": _SilentSound(),
            }
        return {
            "shot": _load_sound(SOUNDS_DIR / "gun_shot_sound.ogg"),
            "hit": [
                _load_sound(SOUNDS_DIR / "chick_hit1.ogg"),
                _load_sound(SOUNDS_DIR / "chick_hit2.ogg"),
                _load_sound(SOUNDS_DIR / "chick_hit3.ogg"),
            ],
            "ambient": _load_sound(SOUNDS_DIR / "ambientloop.ogg"),
            "game_over": _load_sound(SOUNDS_DIR / "game_over.ogg"),
            "main_theme": _load_sound(SOUNDS_DIR / "main_theme.ogg"),
            "button": _load_sound(SOUNDS_DIR / "button_click.ogg"),
        }

    def _load_background(self, screen: pygame.Surface) -> List[pygame.Surface]:
        width, height = screen.get_size()
        sky = pygame.transform.smoothscale(
            pygame.image.load(str(ASSETS_DIR / "world" / "sky.png")).convert(), (width, height)
        )
        hills = pygame.transform.smoothscale(
            pygame.image.load(str(ASSETS_DIR / "world" / "backgroundHills.gif")).convert(), (width, height)
        )
        castle = pygame.transform.smoothscale(
            pygame.image.load(str(ASSETS_DIR / "world" / "background1.png")).convert(), (width, height)
        )
        meadow = pygame.transform.smoothscale(
            pygame.image.load(str(ASSETS_DIR / "world" / "background2.png")).convert(), (width, height)
        )
        return [sky, hills, castle, meadow]

    def handle_pointer(self, event_type: str, pos: Tuple[int, int]) -> None:
        self.pointer = pos
        pygame.mouse.set_pos(pos)
        if event_type in {"down", "click"}:
            if self.state == "menu":
                self.sounds["button"].play()
                self._start_game()
            elif self.state == "game_over":
                self.sounds["button"].play()
                self.state = "menu"
                self.score = 0
                self.time_left = 90.0
                self.chickens.clear()
            else:
                self._shoot()

    def _start_game(self) -> None:
        self.state = "playing"
        self.score = 0
        self.time_left = 90.0
        self.spawn_timer = 0.0
        self.chickens.clear()
        self.crosshair_flash = False
        self.flash_timer = 0.0
        self._play_music(loop=True)

    def _play_music(self, loop: bool = False) -> None:
        if not self.audio_available:
            return
        if self.ambient_channel is None:
            self.ambient_channel = pygame.mixer.find_channel()
        if self.ambient_channel:
            sound = self.sounds["ambient"] if self.state == "playing" else self.sounds["main_theme"]
            self.ambient_channel.play(sound, loops=-1 if loop else 0)

    def _stop_music(self) -> None:
        if self.audio_available and self.ambient_channel:
            self.ambient_channel.stop()

    def _shoot(self) -> None:
        if self.state != "playing":
            return
        self.sounds["shot"].play()
        hit = False
        for chicken in self.chickens:
            if chicken.alive and chicken.rect.collidepoint(self.pointer):
                chicken.shoot()
                hit = True
                self.score += chicken.points
                random.choice(self.sounds["hit"]).play()
                break
        self.crosshair_flash = True
        self.flash_timer = 0.15
        if hit:
            self.best_score = max(self.best_score, self.score)

    def _spawn_chicken(self) -> None:
        width, height = self.screen.get_size()
        size, speed, points = random.choice(
            [
                ((48, 36), random.uniform(120, 170), 15),
                ((64, 50), random.uniform(150, 210), 25),
                ((80, 64), random.uniform(190, 260), 40),
            ]
        )
        direction = random.choice([-1, 1])
        x = -size[0] - 40 if direction > 0 else width + 40
        y = random.randint(int(height * 0.25), int(height * 0.75))
        frames = _scale_frames(self.base_flight_frames, size, flip=direction < 0)
        death_frames = _scale_frames(self.base_death_frames, size, flip=direction < 0)
        chicken = ChickenSprite(
            frames=frames,
            death_frames=death_frames,
            rect=pygame.Rect(x, y, *size),
            speed=speed * direction,
            points=points,
        )
        self.chickens.append(chicken)

    def update(self, dt: float) -> None:
        if self.state == "menu":
            if not self.ambient_channel or not self.ambient_channel.get_busy():
                self._play_music(loop=True)
            return
        if self.state == "playing":
            self.time_left = max(0.0, self.time_left - dt)
            self.spawn_timer += dt
            if self.spawn_timer >= self.spawn_interval:
                self.spawn_timer = 0.0
                self._spawn_chicken()
            for chicken in list(self.chickens):
                chicken.update(dt, self.screen.get_width())
                if chicken.remove:
                    self.chickens.remove(chicken)
            if self.time_left <= 0:
                self.best_score = max(self.best_score, self.score)
                self.state = "game_over"
                self._stop_music()
                self.sounds["game_over"].play()
        if self.crosshair_flash:
            self.flash_timer -= dt
            if self.flash_timer <= 0:
                self.crosshair_flash = False

    def _draw_hud(self) -> None:
        hud = self.font.render(f"Punkte: {self.score}", True, (255, 235, 214))
        timer = self.font.render(f"Zeit: {int(self.time_left)}s", True, (255, 235, 214))
        best = self.small_font.render(f"Best: {self.best_score}", True, (200, 200, 200))
        self.screen.blit(hud, (24, 20))
        self.screen.blit(timer, (24, 70))
        self.screen.blit(best, (24, 120))

    def _draw_pointer(self) -> None:
        cursor = self.cursor_red if self.crosshair_flash else self.cursor_image
        rect = cursor.get_rect(center=self.pointer)
        self.screen.blit(cursor, rect)

    def draw(self) -> None:
        if self.state == "menu":
            self.screen.blit(pygame.transform.smoothscale(self.menu_background, self.screen.get_size()), (0, 0))
            logo_rect = self.menu_logo.get_rect(center=(self.screen.get_width() // 2, 150))
            self.screen.blit(self.menu_logo, logo_rect)
            prompt = self.font.render("Klicke zum Starten", True, (255, 255, 255))
            prompt_rect = prompt.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() - 120))
            self.screen.blit(prompt, prompt_rect)
            self._draw_pointer()
            return

        if self.state in {"playing", "game_over"}:
            for idx, layer in enumerate(self.background_layers):
                self.screen.blit(layer, (0, 0))
            for chicken in self.chickens:
                chicken.draw(self.screen)
            self._draw_hud()

            if self.state == "game_over":
                overlay = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 150))
                self.screen.blit(overlay, (0, 0))
                game_over = self.font.render("Zeit abgelaufen!", True, (255, 215, 0))
                final_score = self.font.render(f"Endstand: {self.score}", True, (255, 255, 255))
                restart = self.small_font.render("Klicke zum Neustart", True, (230, 230, 230))
                self.screen.blit(game_over, game_over.get_rect(center=(self.screen.get_width() // 2, 200)))
                self.screen.blit(final_score, final_score.get_rect(center=(self.screen.get_width() // 2, 260)))
                self.screen.blit(restart, restart.get_rect(center=(self.screen.get_width() // 2, 320)))

            self._draw_pointer()

    def __del__(self) -> None:
        self._stop_music()
