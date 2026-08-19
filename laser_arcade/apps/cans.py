from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pygame

from .arcade_common import (
    build_theme_background,
    calibrated_hit_tolerance,
    draw_cinematic_overlay,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_result_card,
    draw_size_step_button,
    draw_translucent_panel,
    draw_vintage_enamel_panel,
    load_target_sprite,
    nearest_laser_button,
    scaled_target_margin,
    sprite_hit_test,
)
from .base import BaseApp

LOGGER = logging.getLogger(__name__)


SAFE_BG = (0, 8, 16)
SAFE_PANEL = (0, 20, 32)
SAFE_CYAN = (0, 205, 245)
SAFE_GREEN = (0, 225, 120)
SAFE_MUTED = (0, 82, 118)
SAFE_BLUE = (0, 112, 190)


@dataclass
class Can:
    rect: pygame.Rect
    row: int = 0
    column: int = 0
    alive: bool = True
    hit_at: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    spin: float = 0.0
    fall_x: Optional[float] = None
    fall_y: Optional[float] = None
    air_hits: int = 0
    last_air_hit_at: float = 0.0


@dataclass
class Particle:
    x: float
    y: float
    velocity_x: float
    velocity_y: float
    born_at: float
    lifetime: float
    color: Tuple[int, int, int]


@dataclass
class FloatingScore:
    text: str
    x: float
    y: float
    born_at: float
    color: Tuple[int, int, int]


class CanGameSounds:
    """Kurze, räumliche Arcade-Klänge ohne störende Dauerschleife."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = False
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        if not enabled:
            return
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            self.frequency, _, self.channels = pygame.mixer.get_init()
            self._build()
            self.enabled = True
            LOGGER.info("Arcade-Audio bereit (%s Hz, %s Kanäle, %s Effekte)", self.frequency, self.channels, len(self.sounds))
        except (pygame.error, ValueError) as exc:
            LOGGER.warning("Arcade-Spiele laufen ohne Audio: %s", exc)

    def _as_sound(self, mono: np.ndarray, volume: float) -> pygame.mixer.Sound:
        peak = max(1.0, float(np.max(np.abs(mono))))
        mono = np.clip(mono / peak * 30000.0, -32768, 32767).astype(np.int16)
        if self.channels == 1:
            samples = mono
        else:
            # Wenige Millisekunden Laufzeitunterschied erzeugen Breite, ohne
            # dass die Richtung des realen Schusses akustisch verfälscht wird.
            delay = max(1, int(self.frequency * 0.0035))
            right = np.zeros_like(mono)
            right[delay:] = mono[:-delay]
            samples = np.column_stack((mono, right))
        sound = pygame.sndarray.make_sound(np.ascontiguousarray(samples))
        sound.set_volume(volume)
        return sound

    def _tone(
        self,
        frequency: float,
        duration: float,
        decay: float = 4.0,
        harmonics: tuple[tuple[float, float], ...] = (),
    ) -> np.ndarray:
        count = max(1, int(self.frequency * duration))
        t = np.arange(count, dtype=np.float64) / self.frequency
        wave = np.sin(2.0 * np.pi * frequency * t)
        for ratio, gain in harmonics:
            wave += gain * np.sin(2.0 * np.pi * frequency * ratio * t)
        envelope = np.exp(-decay * t)
        attack = min(count, max(1, int(self.frequency * 0.006)))
        envelope[:attack] *= np.linspace(0.0, 1.0, attack)
        return wave * envelope

    def _chirp(
        self,
        start: float,
        end: float,
        duration: float,
        *,
        decay: float = 5.0,
    ) -> np.ndarray:
        count = max(1, int(self.frequency * duration))
        t = np.arange(count, dtype=np.float64) / self.frequency
        sweep = (end - start) / max(duration, 0.001)
        phase = 2.0 * np.pi * (start * t + 0.5 * sweep * t * t)
        envelope = np.exp(-decay * t)
        attack = min(count, max(1, int(self.frequency * 0.004)))
        envelope[:attack] *= np.linspace(0.0, 1.0, attack)
        return np.sin(phase) * envelope

    def _noise(self, duration: float, *, decay: float = 20.0) -> np.ndarray:
        count = max(1, int(self.frequency * duration))
        t = np.arange(count, dtype=np.float64) / self.frequency
        rng = np.random.default_rng(round(duration * 100000) + 7319)
        raw = rng.normal(0.0, 1.0, count)
        # Differenzieren nimmt dem Rauschen den dumpfen Charakter und erzeugt
        # einen kurzen, knackigen Materialimpuls.
        bright = np.concatenate(([raw[0]], np.diff(raw)))
        return bright * np.exp(-decay * t)

    @staticmethod
    def _mix(*waves: tuple[np.ndarray, float]) -> np.ndarray:
        length = max(len(wave) for wave, _ in waves)
        result = np.zeros(length, dtype=np.float64)
        for wave, gain in waves:
            result[: len(wave)] += wave * gain
        return result

    def _echo(self, wave: np.ndarray, delay: float = 0.045, gain: float = 0.24) -> np.ndarray:
        offset = max(1, int(self.frequency * delay))
        result = np.zeros(len(wave) + offset * 2, dtype=np.float64)
        result[: len(wave)] += wave
        result[offset : offset + len(wave)] += wave * gain
        result[offset * 2 : offset * 2 + len(wave)] += wave * gain * 0.38
        return result

    def _sequence(self, notes: tuple[tuple[float, float], ...]) -> np.ndarray:
        chunks = []
        for frequency, duration in notes:
            chunks.append(self._tone(frequency, duration, decay=2.8, harmonics=((2.0, 0.18),)))
            chunks.append(np.zeros(int(self.frequency * 0.018), dtype=np.float64))
        return np.concatenate(chunks)

    def _build(self) -> None:
        shot = self._mix(
            (self._noise(0.095, decay=38.0), 0.72),
            (self._chirp(210, 82, 0.12, decay=24.0), 0.52),
        )
        miss = self._echo(self._chirp(240, 82, 0.19, decay=10.0), 0.035, 0.15)
        metal1 = self._echo(self._mix(
            (self._tone(1040, 0.32, decay=11.0, harmonics=((1.43, 0.55), (2.17, 0.25))), 1.0),
            (self._noise(0.08, decay=26.0), 0.18),
        ))
        metal2 = self._echo(self._mix(
            (self._tone(1320, 0.29, decay=12.0, harmonics=((1.51, 0.50), (2.29, 0.22))), 1.0),
            (self._noise(0.07, decay=29.0), 0.16),
        ), 0.037, 0.20)
        self.sounds = {
            "shot": self._as_sound(shot, 0.18),
            "miss": self._as_sound(miss, 0.22),
            "metal1": self._as_sound(metal1, 0.34),
            "metal2": self._as_sound(metal2, 0.32),
            "can_hit": self._as_sound(metal1, 0.36),
            "clay_break": self._as_sound(self._mix(
                (self._noise(0.24, decay=15.0), 0.72),
                (self._tone(1760, 0.20, decay=15.0, harmonics=((1.67, 0.32),)), 0.62),
            ), 0.33),
            "reaction_hit": self._as_sound(self._echo(self._chirp(620, 1420, 0.13, decay=8.0), 0.052, 0.20), 0.30),
            "target_hit": self._as_sound(self._echo(self._sequence(((740, 0.055), (1110, 0.12))), 0.042, 0.18), 0.29),
            "water_hit": self._as_sound(self._mix(
                (self._chirp(310, 980, 0.17, decay=10.0), 0.70),
                (self._tone(1260, 0.10, decay=18.0), 0.35),
            ), 0.28),
            "photo_hit": self._as_sound(self._mix(
                (self._noise(0.12, decay=30.0), 0.42),
                (self._chirp(480, 920, 0.16, decay=11.0), 0.68),
            ), 0.29),
            "balloon_pop": self._as_sound(self._mix(
                (self._noise(0.12, decay=36.0), 0.72),
                (self._chirp(740, 1180, 0.10, decay=18.0), 0.44),
            ), 0.31),
            "alien_hit": self._as_sound(self._echo(self._chirp(1180, 210, 0.28, decay=6.0), 0.060, 0.23), 0.30),
            "star_hit": self._as_sound(self._echo(self._sequence(((1047, 0.045), (1568, 0.055), (2093, 0.16))), 0.070, 0.22), 0.28),
            "math_correct": self._as_sound(self._sequence(((659, 0.065), (988, 0.075), (1319, 0.16))), 0.29),
            "math_wrong": self._as_sound(self._chirp(330, 125, 0.24, decay=7.0), 0.22),
            "color1": self._as_sound(self._tone(523, 0.18, decay=5.5, harmonics=((2.0, 0.12),)), 0.25),
            "color2": self._as_sound(self._tone(659, 0.18, decay=5.5, harmonics=((2.0, 0.12),)), 0.25),
            "color3": self._as_sound(self._tone(784, 0.18, decay=5.5, harmonics=((2.0, 0.12),)), 0.25),
            "color4": self._as_sound(self._tone(988, 0.18, decay=5.5, harmonics=((2.0, 0.12),)), 0.25),
            "color_level": self._as_sound(self._sequence(((523, 0.05), (659, 0.05), (784, 0.05), (1047, 0.15))), 0.29),
            "treasure_found": self._as_sound(self._echo(self._sequence(((784, 0.06), (988, 0.07), (1319, 0.18))), 0.055, 0.22), 0.31),
            "treasure_wrong": self._as_sound(self._chirp(280, 150, 0.18, decay=9.0), 0.20),
            "count": self._as_sound(self._tone(720, 0.10, decay=15.0), 0.34),
            "go": self._as_sound(
                self._echo(self._sequence(((660, 0.09), (880, 0.09), (1100, 0.18)))), 0.36
            ),
            "wave": self._as_sound(
                self._echo(self._sequence(((523, 0.11), (659, 0.11), (784, 0.11), (1047, 0.24)))),
                0.42,
            ),
            "finish": self._as_sound(
                self._echo(self._sequence(
                    ((392, 0.12), (523, 0.12), (659, 0.12), (784, 0.16), (1047, 0.36))
                ), 0.075, 0.21),
                0.44,
            ),
            "button": self._as_sound(
                self._sequence(((520, 0.045), (760, 0.075))), 0.23
            ),
        }

    def play(self, name: str) -> None:
        if self.enabled and name in self.sounds:
            self.sounds[name].play()

    def stop_all(self) -> None:
        if self.enabled:
            pygame.mixer.stop()


class CansApp(BaseApp):
    name = "Dosenschießen"
    SECONDS_PER_LEVEL = 20.0
    COUNTDOWN_DURATION = 3.35
    WAVE_CLEAR_DURATION = 1.7
    WAVE_ROWS = (
        (3, 2, 1),
        (4, 3, 2, 1),
        (5, 4, 3, 2, 1),
        (6, 5, 4, 3, 2, 1),
        (7, 6, 5, 4, 3, 2, 1),
    )
    LEVEL_COUNTS = (1, 2, 3, 4, 5)
    TARGET_SCALES = (0.8, 1.0, 1.2, 1.4)
    FALL_GRAVITY = 560.0
    FALL_VISIBLE_SECONDS = 1.35
    AIR_HIT_COOLDOWN = 0.24
    AIR_HIT_BASE_POINTS = 250

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        random_seed: int = 20260722,
    ) -> None:
        super().__init__(screen)
        self.random = random.Random(random_seed)
        self.sounds = CanGameSounds(audio_enabled)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22)
        self.font_large = pygame.font.SysFont("Arial", 35, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 52, bold=True)
        self.font_countdown = pygame.font.SysFont("Arial", 116, bold=True)
        self.can_surface_cache: dict[Tuple[int, int], pygame.Surface] = {}
        self.hit_tolerance = calibrated_hit_tolerance(screen.get_size())
        self.background = build_theme_background(screen.get_size(), "cans")
        width, height = screen.get_size()
        self.menu_button = pygame.Rect(width - 170, 24, 140, 40)
        self.start_card = pygame.Rect(0, 0, 650, 400)
        self.start_card.center = (width // 2, height // 2 + 20)
        self.start_button = pygame.Rect(width // 2 - 175, height // 2 + 148, 350, 58)
        self.result_card = pygame.Rect(0, 0, 680, 440)
        self.result_card.center = (width // 2, height // 2 + 18)
        self.repeat_button = pygame.Rect(width // 2 - 300, height - 104, 280, 48)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 104, 280, 48)
        self.ready_size_minus_button = pygame.Rect(width // 2 - 212, height - 348, 82, 44)
        self.ready_size_plus_button = pygame.Rect(width // 2 + 130, height - 348, 82, 44)
        self.ready_level_minus_button = pygame.Rect(width // 2 - 212, height - 296, 82, 44)
        self.ready_level_plus_button = pygame.Rect(width // 2 + 130, height - 296, 82, 44)
        # Fester, breiter Beschriftungsbereich zwischen Minus und Plus. Die
        # Abstände bleiben auch mit der auf dem Pi installierten Schrift frei.
        self.size_minus_button = pygame.Rect(width - 474, 24, 70, 40)
        self.size_plus_button = pygame.Rect(width - 262, 24, 70, 40)
        self.target_scale_index = 1
        self.level_count_index = 2
        self.state = "ready"
        self.state_started = time.monotonic()
        self.last_update = self.state_started
        self.play_started = 0.0
        self.deadline = 0.0
        self.remaining = self.game_duration
        self.wave = 1
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.knocked_down = 0
        self.combo = 0
        self.best_combo = 0
        self.cans: list[Can] = []
        self.particles: list[Particle] = []
        self.floating_scores: list[FloatingScore] = []
        self.last_count_value: Optional[int] = None
        self.finish_reason = ""

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def total_cans(self) -> int:
        return sum(sum(rows) for rows in self.WAVE_ROWS[: self.level_count])

    @property
    def level_count(self) -> int:
        return self.LEVEL_COUNTS[self.level_count_index]

    @property
    def game_duration(self) -> float:
        return self.level_count * self.SECONDS_PER_LEVEL

    @property
    def level_count_label(self) -> str:
        return f"RUNDEN {self.level_count}"

    @property
    def hit_cans(self) -> int:
        return self.knocked_down

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
        self.remaining = self.game_duration
        self.wave = 1
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.knocked_down = 0
        self.combo = 0
        self.best_combo = 0
        self.cans = []
        self.particles = []
        self.floating_scores = []
        self.last_count_value = None
        self.finish_reason = ""
        LOGGER.info("Dosenschießen bereit")

    def stop(self) -> None:
        self.sounds.stop_all()

    def begin_countdown(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.score = 0
        self.shots = 0
        self.hits = 0
        self.knocked_down = 0
        self.combo = 0
        self.best_combo = 0
        self.wave = 1
        self.remaining = self.game_duration
        self.cans = []
        self.particles = []
        self.floating_scores = []
        self.state = "countdown"
        self.state_started = current
        self.last_update = current
        self.last_count_value = None
        self.finish_reason = ""
        self.sounds.play("button")
        LOGGER.info(
            "Dosenschießen gestartet: %.0f Sekunden, %s Runden, %s Dosen",
            self.game_duration,
            self.level_count,
            self.total_cans,
        )

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if self.state == "ready":
            control = nearest_laser_button(pos, (("menu", self.menu_button),))
            if control is None:
                control = nearest_laser_button(
                    pos,
                    (
                        ("size_smaller", self.ready_size_minus_button),
                        ("size_larger", self.ready_size_plus_button),
                        ("levels_fewer", self.ready_level_minus_button),
                        ("levels_more", self.ready_level_plus_button),
                    ),
                    expansion=(96, 40),
                )
        else:
            controls = (("menu", self.menu_button),)
            if self.state != "game_over":
                controls += (
                    ("size_smaller", self.size_minus_button),
                    ("size_larger", self.size_plus_button),
                )
            control = nearest_laser_button(pos, controls)
        if control == "menu":
            self.sounds.play("button")
            return "menu"
        if control in {"size_smaller", "size_larger"}:
            self._change_target_scale(-1 if control == "size_smaller" else 1)
            return "setting"
        if control in {"levels_fewer", "levels_more"}:
            self._change_level_count(-1 if control == "levels_fewer" else 1)
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
        if self.state not in {"playing", "wave_clear"}:
            return "handled"

        if self._falling_hit_is_cooling_down(pos, current):
            return "handled"

        self.shots += 1
        self.sounds.play("shot")
        # Ein sichtbarer Streifschuss muss die Dose zuverlässig umwerfen. Falls
        # sich die großzügigen Fangbereiche berühren, gewinnt die nächstgelegene
        # Dosenmitte und der Treffer bleibt eindeutig.
        candidates = [
            can
            for can in self.cans
            if can.alive
            and self._point_hits_standing_can(can, pos)
        ]
        hit = min(
            candidates,
            key=lambda can: (can.rect.centerx - pos[0]) ** 2
            + (can.rect.centery - pos[1]) ** 2,
            default=None,
        )
        if hit is None:
            falling_hit = self._nearest_falling_can(pos, current)
            if falling_hit is not None:
                return self._hit_falling_can(falling_hit, pos, current)
            self.combo = 0
            self.sounds.play("miss")
            self._spawn_miss_feedback(pos, current)
            LOGGER.info("Dosenschießen Fehlschuss: Schuss=%s", self.shots)
            return "miss"

        side = pos[0] - hit.rect.centerx
        direction = -1.0 if side < 0 else 1.0
        if abs(side) < hit.rect.width * 0.12:
            direction = self.random.choice((-1.0, 1.0))
        # Positives Y bedeutet im Bildschirmkoordinatensystem nach unten: Die
        # Dose fällt ab dem ersten Bild und scheint nie nach oben zu schweben.
        self._start_fall(
            hit,
            current,
            direction * self.random.uniform(105.0, 205.0),
            self.random.uniform(35.0, 90.0),
            direction * self.random.uniform(230.0, 390.0),
        )
        self.hits += 1
        self.knocked_down += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        points = 100 + min(200, (self.combo - 1) * 20)
        self.score += points
        self.sounds.play("can_hit" if self.hits % 2 else "metal2")
        self._spawn_hit_feedback(hit.rect.center, points, current)
        collapsed = self._collapse_unsupported(current)
        if collapsed:
            collapse_points = collapsed * 60
            self.score += collapse_points
            self.floating_scores.append(
                FloatingScore(
                    f"{collapsed} UMGEWORFEN  +{collapse_points}",
                    float(hit.rect.centerx),
                    float(hit.rect.top - 28),
                    current,
                    SAFE_CYAN,
                )
            )
            self.sounds.play("metal2")
            LOGGER.info("Dosenschießen Kettensturz: %s weitere Dosen", collapsed)
        LOGGER.info(
            "Dosenschießen Treffer: Runde=%s direkt=%s umgeworfen=%s/%s Punkte=%s Serie=%s",
            self.wave,
            self.hits,
            self.knocked_down,
            self.total_cans,
            self.score,
            self.combo,
        )

        if all(not can.alive for can in self.cans):
            self.score += 350 + int(max(0.0, self.remaining) * 5)
            self.state = "wave_clear"
            self.state_started = current
            self.sounds.play("wave")
            LOGGER.info("Dosenschießen Runde %s geschafft", self.wave)
        return "hit"

    def _start_fall(
        self,
        can: Can,
        now: float,
        velocity_x: float,
        velocity_y: float,
        spin: float,
    ) -> None:
        can.alive = False
        can.hit_at = now
        can.fall_x = float(can.rect.centerx)
        can.fall_y = float(can.rect.centery)
        can.velocity_x = velocity_x
        can.velocity_y = max(20.0, velocity_y)
        can.spin = spin

    def _falling_state(
        self,
        can: Can,
        now: float,
    ) -> tuple[float, float, float, float]:
        elapsed = max(0.0, now - can.hit_at)
        anchor_x = float(can.rect.centerx) if can.fall_x is None else can.fall_x
        anchor_y = float(can.rect.centery) if can.fall_y is None else can.fall_y
        center_x = anchor_x + can.velocity_x * elapsed
        center_y = (
            anchor_y
            + can.velocity_y * elapsed
            + 0.5 * self.FALL_GRAVITY * elapsed * elapsed
        )
        velocity_y = can.velocity_y + self.FALL_GRAVITY * elapsed
        angle = can.spin * elapsed
        return center_x, center_y, velocity_y, angle

    def _point_hits_falling_can(
        self,
        can: Can,
        pos: Tuple[int, int],
        now: float,
    ) -> bool:
        center_x, center_y, _, angle = self._falling_state(can, now)
        surface = pygame.transform.rotate(self._can_surface(can.rect.size), angle)
        rect = surface.get_rect(center=(round(center_x), round(center_y)))
        return sprite_hit_test(
            pos,
            rect,
            pygame.mask.from_surface(surface, 8),
            margin=self._hit_margin(can),
        )

    def _point_hits_standing_can(self, can: Can, pos: Tuple[int, int]) -> bool:
        surface = self._can_surface(can.rect.size)
        return sprite_hit_test(
            pos,
            can.rect,
            pygame.mask.from_surface(surface, 8),
            margin=self._hit_margin(can),
        )

    def _nearest_falling_can(
        self,
        pos: Tuple[int, int],
        now: float,
    ) -> Optional[Can]:
        candidates = []
        for can in self.cans:
            elapsed = now - can.hit_at
            if (
                can.alive
                or elapsed < self.AIR_HIT_COOLDOWN
                or elapsed > self.FALL_VISIBLE_SECONDS
                or now - can.last_air_hit_at < self.AIR_HIT_COOLDOWN
                or not self._point_hits_falling_can(can, pos, now)
            ):
                continue
            center_x, center_y, _, _ = self._falling_state(can, now)
            candidates.append((can, (center_x - pos[0]) ** 2 + (center_y - pos[1]) ** 2))
        return min(candidates, key=lambda item: item[1], default=(None, 0.0))[0]

    def _falling_hit_is_cooling_down(
        self,
        pos: Tuple[int, int],
        now: float,
    ) -> bool:
        # Eine noch stehende Dose hat bei visueller Überlagerung Vorrang. So
        # blockiert die Schutzzeit einer davor vorbeifallenden Dose kein neues Ziel.
        if any(
            can.alive
            and self._point_hits_standing_can(can, pos)
            for can in self.cans
        ):
            return False
        for can in self.cans:
            if can.alive or now - can.hit_at > self.FALL_VISIBLE_SECONDS:
                continue
            cooling_down = (
                now - can.hit_at < self.AIR_HIT_COOLDOWN
                or now - can.last_air_hit_at < self.AIR_HIT_COOLDOWN
            )
            if cooling_down and self._point_hits_falling_can(can, pos, now):
                return True
        return False

    def _hit_falling_can(
        self,
        can: Can,
        pos: Tuple[int, int],
        now: float,
    ) -> str:
        center_x, center_y, current_velocity_y, _ = self._falling_state(can, now)
        # Für den Spieler zählt die sichtbare linke oder rechte Bildschirmseite
        # der Dose – unabhängig davon, wie weit sie sich bereits gedreht hat.
        normalized_x = max(
            -1.0,
            min(1.0, (pos[0] - center_x) / max(1.0, can.rect.width / 2)),
        )
        # Ein Treffer links drückt die Dose sichtbar nach rechts, ein Treffer
        # rechts entsprechend nach links. Ein mittiger Treffer behält die
        # bisherige Richtung bei und verstärkt sie leicht.
        if abs(normalized_x) < 0.16:
            push_direction = 1.0 if can.velocity_x >= 0 else -1.0
            new_velocity_x = max(
                -430.0,
                min(430.0, can.velocity_x + push_direction * 95.0),
            )
            new_spin = max(
                -760.0,
                min(760.0, can.spin + push_direction * 150.0),
            )
        else:
            push_direction = 1.0 if normalized_x < 0 else -1.0
            impulse = 165.0 + 105.0 * abs(normalized_x)
            new_velocity_x = push_direction * min(
                430.0,
                max(170.0, abs(can.velocity_x) * 0.35 + impulse),
            )
            new_spin = push_direction * min(
                760.0,
                max(230.0, abs(can.spin) * 0.35 + 185.0 + 120.0 * abs(normalized_x)),
            )
        vertical_offset = (pos[1] - center_y) / max(1.0, can.rect.height / 2)
        new_velocity_y = max(
            35.0,
            current_velocity_y + max(-70.0, min(95.0, -vertical_offset * 85.0)),
        )
        can.fall_x = center_x
        can.fall_y = center_y
        can.hit_at = now
        can.velocity_x = new_velocity_x
        can.velocity_y = new_velocity_y
        can.spin = new_spin
        can.air_hits += 1
        can.last_air_hit_at = now

        self.hits += 1
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        bonus = self.AIR_HIT_BASE_POINTS + min(300, (can.air_hits - 1) * 100)
        self.score += bonus
        self.sounds.play("metal2")
        center = (round(center_x), round(center_y))
        self._spawn_hit_feedback(
            center,
            bonus,
            now,
            label=f"FLUGTREFFER  +{bonus}",
            color=SAFE_CYAN,
        )
        LOGGER.info(
            "Dosenschießen Flugtreffer: Bonus=%s, Lufttreffer=%s, "
            "Geschwindigkeit=(%.0f, %.0f), Drehung=%.0f",
            bonus,
            can.air_hits,
            can.velocity_x,
            can.velocity_y,
            can.spin,
        )
        return "air_hit"

    def _change_target_scale(self, direction: int) -> None:
        previous_index = self.target_scale_index
        self.target_scale_index = max(
            0,
            min(len(self.TARGET_SCALES) - 1, self.target_scale_index + direction),
        )
        if self.target_scale_index != previous_index and self.cans:
            previous = {(can.row, can.column): can for can in self.cans}
            resized = self._wave_cans(self.wave)
            for can in resized:
                old = previous[(can.row, can.column)]
                can.alive = old.alive
                can.hit_at = old.hit_at
                can.velocity_x = old.velocity_x
                can.velocity_y = old.velocity_y
                can.spin = old.spin
                can.fall_x = old.fall_x
                can.fall_y = old.fall_y
                can.air_hits = old.air_hits
                can.last_air_hit_at = old.last_air_hit_at
            self.cans = resized
        if self.target_scale_index != previous_index:
            LOGGER.info("Dosengröße geändert: %s", self.target_scale_label)
        self.sounds.play("button")

    def _change_level_count(self, direction: int) -> None:
        previous_index = self.level_count_index
        self.level_count_index = max(
            0,
            min(len(self.LEVEL_COUNTS) - 1, self.level_count_index + direction),
        )
        if self.level_count_index != previous_index:
            self.remaining = self.game_duration
            LOGGER.info(
                "Rundenanzahl geändert: %s, %.0f Sekunden, %s Dosen",
                self.level_count,
                self.game_duration,
                self.total_cans,
            )
        self.sounds.play("button")

    def _hit_margin(self, can: Can) -> int:
        return scaled_target_margin(
            self.hit_tolerance,
            min(can.rect.width, can.rect.height),
        )

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        dt = max(0.0, min(0.1, current - self.last_update))
        self.last_update = current
        self._update_effects(current, dt)

        if self.state == "countdown":
            elapsed = current - self.state_started
            count_value = 3 - int(elapsed)
            if count_value > 0 and count_value != self.last_count_value:
                self.sounds.play("count")
                self.last_count_value = count_value
            if elapsed >= self.COUNTDOWN_DURATION:
                self.state = "playing"
                self.state_started = current
                self.play_started = current
                self.deadline = current + self.game_duration
                self.remaining = self.game_duration
                self._spawn_wave(self.wave)
                self.sounds.play("go")
        elif self.state == "playing":
            self.remaining = max(0.0, self.deadline - current)
            if self.remaining <= 0:
                self._finish("Die Zeit ist abgelaufen", current)
        elif self.state == "wave_clear":
            self.remaining = max(0.0, self.deadline - current)
            if self.remaining <= 0:
                self._finish("Die Zeit ist abgelaufen", current)
            elif current - self.state_started >= self.WAVE_CLEAR_DURATION:
                if self.wave >= self.level_count:
                    self._finish("Alle Dosen getroffen", current)
                else:
                    self.wave += 1
                    self.combo = 0
                    self._spawn_wave(self.wave)
                    self.state = "playing"
                    self.state_started = current

    def _finish(self, reason: str, now: float) -> None:
        self.state = "game_over"
        self.state_started = now
        self.finish_reason = reason
        if reason == "Alle Dosen getroffen":
            self.score += int(max(0.0, self.remaining) * 20)
        self.sounds.play("finish")
        LOGGER.info(
            "Dosenschießen beendet: %s, Punkte=%s, Treffer=%s/%s, Präzision=%.1f%%",
            reason,
            self.score,
            self.hits,
            self.shots,
            self.accuracy,
        )

    def _spawn_wave(self, wave: int) -> None:
        self.cans = self._wave_cans(wave)
        LOGGER.info("Dosenschießen Runde %s aufgebaut: %s Dosen", wave, len(self.cans))

    def _wave_cans(self, wave: int) -> list[Can]:
        rows = self.WAVE_ROWS[wave - 1]
        width, height = self.screen.get_size()
        shelf_y = height - 116
        can_width = round((66, 58, 50, 44, 38)[wave - 1] * self.target_scale)
        can_height = round((92, 82, 72, 64, 58)[wave - 1] * self.target_scale)
        gap_x = round((15, 13, 11, 9, 8)[wave - 1] * self.target_scale)
        # Die Dose selbst hat transparente Ränder für Deckel und Boden. Acht
        # Pixel Rechtecküberlappung lassen deshalb die sichtbaren Metallkanten
        # exakt aufeinander stehen, ohne dass die Dosen ineinander rutschen.
        vertical_overlap = max(5, round(8 * self.target_scale))
        vertical_step = can_height - vertical_overlap
        cans: list[Can] = []
        for row_index, count in enumerate(rows):
            row_width = count * can_width + (count - 1) * gap_x
            start_x = (width - row_width) // 2
            # Der sichtbare Dosenboden (drei Pixel innerhalb der Grafik) steht
            # genau auf der Oberkante des Brettes.
            y = shelf_y - can_height + 3 - row_index * vertical_step
            for column in range(count):
                x = start_x + column * (can_width + gap_x)
                cans.append(
                    Can(
                        pygame.Rect(x, y, can_width, can_height),
                        row=row_index,
                        column=column,
                    )
                )
        return cans

    def _collapse_unsupported(self, now: float) -> int:
        """Lässt obere Dosen fallen, sobald eine ihrer beiden Stützen fehlt."""

        collapsed = 0
        changed = True
        while changed:
            changed = False
            for can in self.cans:
                if not can.alive or can.row == 0:
                    continue
                supports = [
                    support
                    for support in self.cans
                    if support.row == can.row - 1
                    and support.column in {can.column, can.column + 1}
                ]
                if len(supports) == 2 and all(support.alive for support in supports):
                    continue
                direction = -1.0 if can.column < len(self.WAVE_ROWS[self.wave - 1]) / 2 else 1.0
                self._start_fall(
                    can,
                    now,
                    direction * self.random.uniform(85.0, 190.0),
                    self.random.uniform(20.0, 75.0),
                    direction * self.random.uniform(180.0, 340.0),
                )
                self.knocked_down += 1
                collapsed += 1
                changed = True
        return collapsed

    def _spawn_hit_feedback(
        self,
        center: Tuple[int, int],
        points: int,
        now: float,
        *,
        label: Optional[str] = None,
        color: Tuple[int, int, int] = SAFE_GREEN,
    ) -> None:
        for index in range(18):
            angle = self.random.uniform(0.0, math.tau)
            speed = self.random.uniform(65.0, 230.0)
            self.particles.append(
                Particle(
                    float(center[0]),
                    float(center[1]),
                    math.cos(angle) * speed,
                    math.sin(angle) * speed - 45.0,
                    now,
                    self.random.uniform(0.35, 0.75),
                    SAFE_GREEN if index % 3 else SAFE_CYAN,
                )
            )
        self.floating_scores.append(
            FloatingScore(
                label or f"+{points}",
                float(center[0]),
                float(center[1] - 20),
                now,
                color,
            )
        )

    def _spawn_miss_feedback(self, pos: Tuple[int, int], now: float) -> None:
        self.floating_scores.append(
            FloatingScore("DANEBEN", float(pos[0]), float(pos[1]), now, SAFE_MUTED)
        )

    def _update_effects(self, now: float, dt: float) -> None:
        for particle in self.particles:
            particle.x += particle.velocity_x * dt
            particle.y += particle.velocity_y * dt
            particle.velocity_y += 430.0 * dt
        self.particles = [p for p in self.particles if now - p.born_at <= p.lifetime]
        self.floating_scores = [f for f in self.floating_scores if now - f.born_at <= 1.0]

    def _build_background(self) -> pygame.Surface:
        width, height = self.screen.get_size()
        surface = pygame.Surface((width, height), pygame.SRCALPHA)
        for y in range(height):
            blend = y / max(1, height - 1)
            color = (0, int(7 + 18 * blend), int(17 + 34 * blend), 255)
            pygame.draw.line(surface, color, (0, y), (width, y))

        # Weiches Bühnenlicht hinter der Pyramide. Mehrere transparente
        # Ellipsen erzeugen Tiefe, ohne rote Projektionsanteile einzuführen.
        glow = pygame.Surface((width, height), pygame.SRCALPHA)
        for index in range(10, 0, -1):
            glow_width = int(width * (0.24 + index * 0.055))
            glow_height = int(height * (0.30 + index * 0.035))
            alpha = 3 + (10 - index)
            pygame.draw.ellipse(
                glow,
                (0, 105, 160, alpha),
                pygame.Rect(
                    width // 2 - glow_width // 2,
                    152,
                    glow_width,
                    glow_height,
                ),
            )
        surface.blit(glow, (0, 0))

        # Dezente Studiowand mit Paneelfugen und Schraubpunkten.
        for y in range(190, height - 150, 76):
            pygame.draw.line(surface, (0, 31, 52, 150), (22, y), (width - 22, y), 1)
        for x in range(42, width, 78):
            pygame.draw.circle(surface, (0, 78, 105, 190), (x, 153), 2)
            pygame.draw.circle(surface, (0, 20, 34, 220), (x, 153), 1)

        floor_top = height - 146
        for y in range(floor_top, height):
            blend = (y - floor_top) / max(1, height - floor_top)
            pygame.draw.line(
                surface,
                (0, int(18 - 8 * blend), int(35 - 17 * blend), 255),
                (0, y),
                (width, y),
            )
        pygame.draw.line(surface, (0, 64, 87), (0, floor_top), (width, floor_top), 2)
        return surface

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.screen.blit(self.background, (0, 0))
        draw_ambient_background(self.screen, "cans", current)
        draw_cinematic_overlay(self.screen)
        if self.state == "ready":
            draw_ambient_foreground(self.screen, "cans", current)
            self._draw_ready()
        elif self.state == "countdown":
            self._draw_playfield(current)
            draw_ambient_foreground(self.screen, "cans", current)
            self._draw_countdown(current)
        elif self.state in {"playing", "wave_clear"}:
            self._draw_playfield(current)
            draw_ambient_foreground(self.screen, "cans", current)
            if self.state == "wave_clear":
                self._draw_wave_clear()
        else:
            self._draw_playfield(current, subdued=True)
            draw_ambient_foreground(self.screen, "cans", current)
            self._draw_result()

    def _draw_ready(self) -> None:
        width = self.screen.get_width()
        title = self.font_title.render("DOSENSCHIEßEN", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(width // 2, 34)))
        subtitle = self.font.render(
            f"{self.level_count} RUNDEN  ·  {self.game_duration:.0f} SEKUNDEN"
            f"  ·  {self.total_cans} DOSEN",
            True,
            SAFE_GREEN,
        )
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(width // 2, 96)))

        draw_translucent_panel(
            self.screen, self.start_card, SAFE_PANEL, alpha=202, border_radius=16
        )
        pygame.draw.rect(self.screen, SAFE_CYAN, self.start_card, 3, border_radius=16)
        self._draw_aim_point((self.start_card.right - 28, self.start_card.top + 28), SAFE_GREEN)
        heading = self.font_large.render("SO FUNKTIONIERT ES", True, (225, 250, 255))
        self.screen.blit(heading, heading.get_rect(midtop=(width // 2, self.start_card.top + 36)))
        lines = (
            "Triff alle Dosen, bevor die Zeit abläuft.",
            "Fallende Dosen bringen zusätzliche Punkte.",
            "Treffer links oder rechts ändern ihre Flugrichtung.",
        )
        for index, line in enumerate(lines):
            center_y = self.start_card.top + 105 + index * 35
            marker = (self.start_card.left + 50, center_y)
            pygame.draw.circle(self.screen, (0, 13, 24), marker, 17)
            pygame.draw.circle(self.screen, SAFE_GREEN, marker, 17, 2)
            number = self.font_small.render(str(index + 1), True, SAFE_GREEN)
            self.screen.blit(number, number.get_rect(center=marker))
            text = self.font_small.render(line, True, (225, 250, 255))
            self.screen.blit(text, text.get_rect(midleft=(self.start_card.left + 78, center_y)))
        self._draw_button(self.start_button, "SPIEL STARTEN", SAFE_GREEN)
        self._draw_button(self.menu_button, "MENÜ", SAFE_CYAN)
        self._draw_size_controls(
            self.ready_size_minus_button,
            self.ready_size_plus_button,
        )
        self._draw_level_controls(
            self.ready_level_minus_button,
            self.ready_level_plus_button,
        )

    def _draw_playfield(self, now: float, subdued: bool = False) -> None:
        width, height = self.screen.get_size()
        title = self.font_large.render("DOSENSCHIEßEN", True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        self._draw_button(self.menu_button, "MENÜ", SAFE_CYAN)
        if not subdued:
            self._draw_size_controls(self.size_minus_button, self.size_plus_button)

        hud = pygame.Rect(24, 82, width - 48, 70)
        draw_translucent_panel(
            self.screen, hud, SAFE_PANEL, alpha=188, border_radius=10
        )
        pygame.draw.rect(self.screen, SAFE_MUTED, hud, 2, border_radius=10)
        values = (
            ("PUNKTE", f"{self.score:06d}"),
            ("RUNDE", f"{self.wave}/{self.level_count}"),
            ("ZEIT", f"{self.remaining:04.1f}"),
            ("PRÄZISION", f"{self.accuracy:.0f} %"),
            ("SERIE", f"{self.combo}x"),
        )
        segment = hud.width // len(values)
        for index, (label, value) in enumerate(values):
            center_x = hud.left + segment * index + segment // 2
            label_surface = self.font_small.render(label, True, SAFE_MUTED)
            value_surface = self.font.render(value, True, SAFE_GREEN if index != 2 else SAFE_CYAN)
            self.screen.blit(label_surface, label_surface.get_rect(center=(center_x, hud.top + 20)))
            self.screen.blit(value_surface, value_surface.get_rect(center=(center_x, hud.top + 48)))

        self._draw_shelf()

        if not subdued:
            for can in self.cans:
                self._draw_can(can, now)
            self._draw_effects(now)

    def _draw_can(self, can: Can, now: float) -> None:
        if can.alive:
            surface = self._can_surface(can.rect.size)
            shadow = pygame.Rect(
                can.rect.left + 5,
                can.rect.bottom - 6,
                max(8, can.rect.width - 10),
                10,
            )
            pygame.draw.ellipse(self.screen, (0, 20, 29), shadow)
            self.screen.blit(surface, can.rect)
            return
        elapsed = now - can.hit_at
        if elapsed > self.FALL_VISIBLE_SECONDS:
            return
        center_x, center_y, _, angle = self._falling_state(can, now)
        surface = pygame.transform.rotate(self._can_surface(can.rect.size), angle)
        self.screen.blit(
            surface,
            surface.get_rect(center=(round(center_x), round(center_y))),
        )

    def _can_surface(self, size: Tuple[int, int]) -> pygame.Surface:
        cached = self.can_surface_cache.get(size)
        if cached is not None:
            return cached

        # Das versionierte 3D-Motiv enthält echte Deckel-, Falz- und
        # Materialdetails. Die zentrale Laderoutine begrenzt gerade diese
        # hellen Metallbereiche, damit sie für den roten Laser offen bleiben.
        finished = load_target_sprite("can", size, brightness_limit=146)
        self.can_surface_cache[size] = finished
        return finished

        width, height = size
        scale = 3
        w, h = width * scale, height * scale
        surface = pygame.Surface((w, h), pygame.SRCALPHA)
        body = pygame.Rect(3 * scale, 7 * scale, w - 6 * scale, h - 13 * scale)
        radius = max(5 * scale, w // 10)

        # Zylindrische Metalllackierung mit bewusst begrenzter Leuchtdichte.
        # Projektor-Cyan plus roter Laser würde auf sehr hellen Flächen nahezu
        # weiß ausbrennen; deshalb bleiben selbst Reflex, Deckel und Boden klar
        # unterhalb der Sättigung und der rote Punkt behält seinen Überschuss.
        body_gradient = pygame.Surface((w, h), pygame.SRCALPHA)
        for x in range(body.left, body.right):
            position = (x - body.left) / max(1, body.width - 1)
            curvature = math.sin(math.pi * position) ** 0.62
            green = int(27 + 72 * curvature)
            blue = int(43 + 88 * curvature)
            if 0.24 < position < 0.34:
                highlight = 1.0 - abs(position - 0.29) / 0.05
                green = min(126, green + int(27 * highlight))
                blue = min(151, blue + int(22 * highlight))
            pygame.draw.line(
                body_gradient,
                (0, green, blue, 255),
                (x, body.top),
                (x, body.bottom),
            )
        body_mask = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(body_mask, (255, 255, 255, 255), body, border_radius=radius)
        body_gradient.blit(body_mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surface.blit(body_gradient, (0, 0))

        # Leicht gebogene Etikettfläche mit seitlicher Abdunklung.
        band_top = int(h * 0.39)
        band_height = int(h * 0.31)
        pygame.draw.rect(
            surface,
            (0, 28, 55, 245),
            pygame.Rect(body.left, band_top, body.width, band_height),
        )
        for stripe in range(-h, w + h, 22 * scale):
            pygame.draw.line(
                surface,
                (0, 69, 102, 150),
                (stripe, band_top + band_height),
                (stripe + 18 * scale, band_top),
                3 * scale,
            )
        side_shade = max(2 * scale, w // 9)
        pygame.draw.rect(
            surface,
            (0, 9, 25, 190),
            pygame.Rect(body.left, band_top, side_shade, band_height),
        )
        pygame.draw.rect(
            surface,
            (0, 12, 29, 150),
            pygame.Rect(body.right - side_shade, band_top, side_shade, band_height),
        )
        pygame.draw.line(
            surface,
            (0, 142, 104, 255),
            (body.left + 2 * scale, band_top),
            (body.right - 2 * scale, band_top),
            scale,
        )
        pygame.draw.line(
            surface,
            (0, 86, 112, 255),
            (body.left + 2 * scale, band_top + band_height),
            (body.right - 2 * scale, band_top + band_height),
            scale,
        )

        # Prägnantes Ziel-Emblem statt eines flachen Fadenkreuzes.
        emblem_center = (w // 2, band_top + band_height // 2)
        emblem_radius = max(7 * scale, int(width * 0.16 * scale))
        pygame.draw.circle(surface, (0, 15, 31, 255), emblem_center, emblem_radius + 2 * scale)
        pygame.draw.circle(surface, (0, 148, 108, 255), emblem_center, emblem_radius, 2 * scale)
        pygame.draw.circle(surface, (0, 126, 148, 255), emblem_center, max(2 * scale, emblem_radius // 3), scale)
        pygame.draw.line(
            surface,
            (0, 148, 108, 255),
            (emblem_center[0] - emblem_radius - 5 * scale, emblem_center[1]),
            (emblem_center[0] + emblem_radius + 5 * scale, emblem_center[1]),
            scale,
        )
        pygame.draw.line(
            surface,
            (0, 148, 108, 255),
            (emblem_center[0], emblem_center[1] - emblem_radius - 3 * scale),
            (emblem_center[0], emblem_center[1] + emblem_radius + 3 * scale),
            scale,
        )

        # Gepresster Deckel mit Doppelrand, Vertiefung und Zuglasche.
        top_outer = pygame.Rect(3 * scale, 2 * scale, w - 6 * scale, 14 * scale)
        pygame.draw.ellipse(surface, (0, 42, 58, 255), top_outer)
        pygame.draw.ellipse(surface, (0, 112, 132, 255), top_outer, 2 * scale)
        top_inner = top_outer.inflate(-6 * scale, -4 * scale)
        pygame.draw.ellipse(surface, (0, 21, 34, 255), top_inner)
        pygame.draw.arc(surface, (0, 88, 109, 255), top_inner, math.pi, math.tau, 2 * scale)
        tab = pygame.Rect(
            w // 2 - 5 * scale,
            top_inner.centery - 2 * scale,
            10 * scale,
            5 * scale,
        )
        pygame.draw.ellipse(surface, (0, 82, 101, 255), tab, 2 * scale)
        pygame.draw.circle(surface, (0, 25, 39, 255), (tab.centerx + 2 * scale, tab.centery), scale)

        # Unterer Falz und geprägte Seitenlinien.
        bottom = pygame.Rect(3 * scale, h - 14 * scale, w - 6 * scale, 11 * scale)
        pygame.draw.ellipse(surface, (0, 20, 34, 255), bottom)
        pygame.draw.arc(surface, (0, 94, 116, 255), bottom, 0, math.pi, 2 * scale)
        pygame.draw.line(
            surface,
            (0, 118, 132, 210),
            (body.left + body.width // 4, 15 * scale),
            (body.left + body.width // 4, h - 16 * scale),
            2 * scale,
        )
        pygame.draw.line(
            surface,
            (0, 34, 62, 210),
            (body.right - body.width // 6, 16 * scale),
            (body.right - body.width // 6, h - 16 * scale),
            2 * scale,
        )

        # Wenige Lichttropfen beleben die Oberfläche auch bei kleinen Dosen.
        for drop_x, drop_y, drop_radius in (
            (0.70, 0.24, 1.4),
            (0.76, 0.31, 1.0),
            (0.21, 0.76, 1.2),
        ):
            pygame.draw.circle(
                surface,
                (0, 112, 132, 205),
                (int(w * drop_x), int(h * drop_y)),
                max(scale, int(drop_radius * scale)),
            )
            pygame.draw.circle(
                surface,
                (0, 52, 76, 220),
                (int(w * drop_x), int(h * drop_y)),
                max(scale, int(drop_radius * scale)),
                scale,
            )

        pygame.draw.rect(surface, (0, 122, 143, 220), body, 2 * scale, border_radius=radius)
        finished = pygame.transform.smoothscale(surface, size)
        self.can_surface_cache[size] = finished
        return finished

    def _draw_shelf(self) -> None:
        width, height = self.screen.get_size()
        shelf_y = height - 116
        shadow = pygame.Rect(70, shelf_y + 14, width - 140, 20)
        pygame.draw.ellipse(self.screen, (0, 7, 15), shadow)

        beam = pygame.Rect(76, shelf_y, width - 152, 24)
        pygame.draw.rect(self.screen, (0, 25, 39), beam, border_radius=7)
        pygame.draw.rect(
            self.screen,
            (0, 70, 92),
            pygame.Rect(beam.left + 2, beam.top + 4, beam.width - 4, beam.height - 6),
            border_radius=5,
        )
        pygame.draw.line(self.screen, (0, 231, 240), beam.topleft, beam.topright, 3)
        pygame.draw.line(
            self.screen,
            (0, 25, 42),
            (beam.left + 6, beam.bottom - 3),
            (beam.right - 6, beam.bottom - 3),
            3,
        )
        for bolt_x in (beam.left + 26, beam.right - 26):
            pygame.draw.circle(self.screen, (0, 180, 195), (bolt_x, beam.centery + 2), 4)
            pygame.draw.circle(self.screen, (0, 28, 43), (bolt_x, beam.centery + 2), 2)

        for top_x, bottom_x in ((112, 87), (width - 112, width - 87)):
            pygame.draw.line(
                self.screen,
                (0, 23, 37),
                (top_x + 5, shelf_y + 23),
                (bottom_x + 5, height - 29),
                10,
            )
            pygame.draw.line(
                self.screen,
                (0, 116, 155),
                (top_x, shelf_y + 23),
                (bottom_x, height - 29),
                6,
            )
            pygame.draw.line(
                self.screen,
                (0, 211, 221),
                (top_x - 2, shelf_y + 24),
                (bottom_x - 2, height - 31),
                1,
            )
            pygame.draw.line(
                self.screen,
                (0, 39, 58),
                (bottom_x - 12, height - 28),
                (bottom_x + 13, height - 28),
                6,
            )

    def _draw_effects(self, now: float) -> None:
        for particle in self.particles:
            alpha = max(0.0, 1.0 - (now - particle.born_at) / particle.lifetime)
            radius = max(1, int(4 * alpha))
            pygame.draw.circle(self.screen, particle.color, (int(particle.x), int(particle.y)), radius)
        for floating in self.floating_scores:
            elapsed = now - floating.born_at
            surface = self.font.render(floating.text, True, floating.color)
            rect = surface.get_rect(center=(int(floating.x), int(floating.y - elapsed * 52)))
            self.screen.blit(surface, rect)

    def _draw_countdown(self, now: float) -> None:
        elapsed = now - self.state_started
        value = 3 - int(elapsed)
        text = str(value) if value > 0 else "LOS!"
        surface = self.font_countdown.render(text, True, SAFE_GREEN if value <= 0 else SAFE_CYAN)
        panel = surface.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2)).inflate(80, 44)
        draw_translucent_panel(
            self.screen, panel, SAFE_PANEL, alpha=198, border_radius=18
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, panel, 3, border_radius=18)
        self.screen.blit(surface, surface.get_rect(center=panel.center))

    def _draw_wave_clear(self) -> None:
        text = self.font_title.render(f"RUNDE {self.wave} GESCHAFFT!", True, SAFE_GREEN)
        panel = text.get_rect(center=(self.screen.get_width() // 2, 260)).inflate(54, 30)
        draw_translucent_panel(
            self.screen, panel, SAFE_PANEL, alpha=194, border_radius=14
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, panel, 3, border_radius=14)
        self.screen.blit(text, text.get_rect(center=panel.center))

    def _draw_result(self) -> None:
        heading_text = "ALLE DOSEN!" if self.finish_reason == "Alle Dosen getroffen" else "ZEIT VORBEI!"
        draw_result_card(
            self.screen,
            self.result_card,
            heading_text,
            self.finish_reason,
            (
                ("PUNKTE", f"{self.score}"),
                ("DOSEN", f"{self.hit_cans}/{self.total_cans}"),
                ("PRÄZISION", f"{self.accuracy:.0f} %"),
                ("BESTE SERIE", f"{self.best_combo}"),
            ),
            self.repeat_button,
            self.result_menu_button,
            (self.font, self.font_large, self.font_title, self.font_small),
        )

    def _draw_button(self, rect: pygame.Rect, label: str, color: Tuple[int, int, int]) -> None:
        draw_vintage_enamel_panel(
            self.screen,
            rect,
            2 if color == SAFE_GREEN else 1,
            alpha=242,
        )
        pygame.draw.rect(self.screen, color, rect, 2, border_radius=8)
        self._draw_aim_point((rect.left + 13, rect.centery), color, radius=5)
        surface = self.font_small.render(label, True, color)
        self.screen.blit(surface, surface.get_rect(center=rect.center))

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

    def _draw_level_controls(
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
        label = self.font_small.render(self.level_count_label, True, SAFE_GREEN)
        self.screen.blit(
            label,
            label.get_rect(
                center=(
                    (minus_button.right + plus_button.left) // 2,
                    minus_button.centery,
                )
            ),
        )

    def _draw_aim_point(
        self,
        point: Tuple[int, int],
        color: Tuple[int, int, int],
        radius: int = 8,
    ) -> None:
        pygame.draw.circle(self.screen, color, point, radius, 2)
        pygame.draw.line(self.screen, color, (point[0] - radius - 4, point[1]), (point[0] + radius + 4, point[1]), 1)
        pygame.draw.line(self.screen, color, (point[0], point[1] - radius - 4), (point[0], point[1] + radius + 4), 1)
