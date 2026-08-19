from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional, Tuple

import pygame

from ..constants import TARGET_HISTORY_FILE
from .arcade_common import (
    SAFE_BLUE,
    SAFE_CYAN,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    SAFE_PANEL_LIGHT,
    TARGET_CYAN,
    TARGET_GREEN,
    distance,
    draw_aim_point,
    draw_ambient_foreground,
    draw_button,
    draw_frame,
    draw_translucent_panel,
    draw_vintage_enamel_panel,
    nearest_laser_button,
)
from .base import BaseApp
from .cans import CanGameSounds

LOGGER = logging.getLogger(__name__)


@dataclass
class TargetShot:
    point: Tuple[int, int]
    distance_px: float
    value: float
    display: str


@dataclass
class TargetResult:
    mode: str
    shot_count: int
    result_value: float
    display: str
    created_at: float


class TargetRangeApp(BaseApp):
    name = "Zielscheibe"
    SHOT_COUNTS = (3, 5, 10)
    MODES = ("whole", "decimal", "divider")
    MODE_LABELS = {
        "whole": "GANZE RINGE",
        "decimal": "ZEHNTEL",
        "divider": "TEILER",
    }
    TARGET_RADII = (180, 220, 250)
    # Originale Ringdurchmesser einer 10-m-Luftgewehrscheibe. Die Darstellung
    # wird proportional auf die Leinwand skaliert; die Wertungsgeometrie bleibt
    # bei jeder gewählten Größe identisch.
    RING_DIAMETERS_MM = {
        1: 45.5,
        2: 40.5,
        3: 35.5,
        4: 30.5,
        5: 25.5,
        6: 20.5,
        7: 15.5,
        8: 10.5,
        9: 5.5,
        10: 0.5,
    }
    RIFLE_PAPER = (54, 84, 104)
    RIFLE_INK = (1, 13, 24)
    RIFLE_INNER_LINE = (78, 190, 202)
    RIFLE_EDGE = (0, 126, 154)
    RIFLE_TEN_FILL = (0, 58, 76)
    RIFLE_TEN_EDGE = (0, 218, 205)
    # Die originale 0,5-mm-Zehn wäre auf der Standardleinwand nur rund
    # 2,4 Pixel groß. Für die Laserwaffe erhält sie eine klar sicht- und
    # erreichbare elektronische Wertungszone, bleibt aber deutlich innerhalb
    # des originalen Neunerrings.
    PRACTICAL_TEN_RADIUS_RATIO = 0.08
    PRACTICAL_TEN_MIN_RADIUS = 14
    PERFECT_TEN_RATIO = 0.32
    PERFECT_TEN_MIN_RADIUS = 5
    RESULT_DURATION = 3.0
    SETTING_COOLDOWN = 0.75

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        audio_enabled: bool = True,
        sounds: Optional[CanGameSounds] = None,
        history_path: Optional[Path] = TARGET_HISTORY_FILE,
    ) -> None:
        super().__init__(screen)
        self.sounds = sounds or CanGameSounds(audio_enabled)
        self.history_path = history_path
        self.font_micro = pygame.font.SysFont("Arial", 13)
        self.font_tiny = pygame.font.SysFont("Arial", 15)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22)
        self.font_large = pygame.font.SysFont("Arial", 35, bold=True)
        self.font_shot = pygame.font.SysFont("Arial", 26, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 48, bold=True)
        self.font_score = pygame.font.SysFont("Arial", 72, bold=True)
        width, height = screen.get_size()
        self.target_center = (width // 2, 420)
        self.mode_button = pygame.Rect(26, 82, 290, 82)
        self.shot_count_button = pygame.Rect(width - 210, 82, 184, 82)
        self.menu_button = pygame.Rect(26, height - 68, 185, 42)
        self.size_minus_button = pygame.Rect(286, height - 68, 150, 42)
        self.size_plus_button = pygame.Rect(485, height - 68, 150, 42)
        self.shot_list_rect = pygame.Rect(26, 178, 222, height - 260)
        self.history_rect = pygame.Rect(width - 248, 178, 222, height - 260)
        self.result_card = pygame.Rect(0, 0, 850, 610)
        self.result_card.center = (width // 2, height // 2)
        self.mode_index = 0
        self.shot_count_index = 1
        self.radius_index = 1
        self.shots: list[TargetShot] = []
        self.histories: dict[str, Deque[TargetResult]] = {
            mode: deque(maxlen=5) for mode in self.MODES
        }
        self.state = "playing"
        self.result_until = 0.0
        self.current_result: Optional[TargetResult] = None
        self.setting_locked_until = 0.0
        self._load_history()

    @property
    def mode(self) -> str:
        return self.MODES[self.mode_index]

    @property
    def shot_limit(self) -> int:
        return self.SHOT_COUNTS[self.shot_count_index]

    @property
    def target_radius(self) -> int:
        return self.TARGET_RADII[self.radius_index]

    def start(self, now: Optional[float] = None) -> None:
        self.state = "playing"
        self.shots = []
        self.current_result = None
        self.result_until = 0.0
        LOGGER.info(
            "Zielscheibe geöffnet: %s, %s Schüsse, Radius %s px",
            self.MODE_LABELS[self.mode],
            self.shot_limit,
            self.target_radius,
        )

    def stop(self) -> None:
        self.sounds.stop_all()

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        # Die Gesamtauswertung bleibt garantiert drei volle Sekunden sichtbar.
        # In dieser Phase darf weder ein Lasernachlauf noch ein weiterer Schuss
        # die Ansicht überspringen oder eine Einstellung ändern.
        if self.state == "result" and current < self.result_until:
            return "handled"
        control = nearest_laser_button(
            pos,
            (
                ("menu", self.menu_button),
                ("mode", self.mode_button),
                ("shots", self.shot_count_button),
                ("smaller", self.size_minus_button),
                ("larger", self.size_plus_button),
            ),
        )
        if control == "menu":
            self.sounds.play("button")
            return "menu"
        if control in {"mode", "shots", "smaller", "larger"}:
            if current < self.setting_locked_until:
                return "setting_locked"
            # Ein kurzer Laserimpuls kann bei sehr heller Projektion mehrere
            # Erkennungsflanken erzeugen. Pro Schuss darf die Auswahl trotzdem
            # nur genau eine Stufe weiterschalten.
            self.setting_locked_until = current + self.SETTING_COOLDOWN
        if control == "mode":
            self.mode_index = (self.mode_index + 1) % len(self.MODES)
            self._start_new_series()
            self.sounds.play("button")
            LOGGER.info("Zielscheibe Wertung: %s", self.MODE_LABELS[self.mode])
            return "setting"
        if control == "shots":
            self.shot_count_index = (self.shot_count_index + 1) % len(self.SHOT_COUNTS)
            self._start_new_series()
            self.sounds.play("button")
            LOGGER.info("Zielscheibe Schusszahl: %s", self.shot_limit)
            return "setting"
        if control == "smaller":
            self.radius_index = max(0, self.radius_index - 1)
            self._start_new_series()
            self.sounds.play("button")
            return "setting"
        if control == "larger":
            self.radius_index = min(len(self.TARGET_RADII) - 1, self.radius_index + 1)
            self._start_new_series()
            self.sounds.play("button")
            return "setting"
        if self.state == "result":
            if self.result_card.collidepoint(pos):
                self._start_new_series()
                self.sounds.play("button")
            return "handled"

        radial_distance = distance(self.target_center, pos)
        shot = self._score_shot(pos, radial_distance)
        self.shots.append(shot)
        self.sounds.play("target_hit" if radial_distance <= self.target_radius else "miss")
        LOGGER.info(
            "Zielscheibe Schuss %s/%s: %s bei %.1f px",
            len(self.shots),
            self.shot_limit,
            shot.display,
            radial_distance,
        )
        if len(self.shots) >= self.shot_limit:
            self._finish_series(current)
        return "hit" if radial_distance <= self.target_radius else "miss"

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        if self.state == "result" and current >= self.result_until:
            self._start_new_series()

    def _score_shot(self, pos: Tuple[int, int], radial_distance: float) -> TargetShot:
        if self.mode == "whole":
            value = float(self._whole_ring_value(radial_distance))
            display = f"{int(value)}"
        elif self.mode == "decimal":
            value = self._decimal_ring_value(radial_distance)
            display = f"{value:.1f}".replace(".", ",")
        else:
            millimeters = (
                radial_distance
                / self.target_radius
                * (self.RING_DIAMETERS_MM[1] / 2.0)
            )
            value = round(millimeters * 100.0)
            display = f"{int(value)} T"
        return TargetShot(pos, radial_distance, value, display)

    def _ring_radius(self, ring: int) -> float:
        """Skaliert den originalen Ringdurchmesser auf den Außenradius."""

        return (
            self.target_radius
            * self.RING_DIAMETERS_MM[ring]
            / self.RING_DIAMETERS_MM[1]
        )

    def _scoring_ring_radius(self, ring: int) -> float:
        """Liefert die spielbare Ringgrenze; nur die Zehn wird vergrößert."""

        if ring != 10:
            return self._ring_radius(ring)
        practical = max(
            float(self.PRACTICAL_TEN_MIN_RADIUS),
            self.target_radius * self.PRACTICAL_TEN_RADIUS_RATIO,
        )
        # Zwischen Zehn und Neun bleibt immer ein klarer eigener Ringbereich.
        return min(practical, self._ring_radius(9) * 0.72)

    def _perfect_ten_radius(self) -> float:
        """Erreichbarer Mittelpunktbereich für eine Wertung von 10,9."""

        return min(
            self._scoring_ring_radius(10) * 0.45,
            max(
                float(self.PERFECT_TEN_MIN_RADIUS),
                self._scoring_ring_radius(10) * self.PERFECT_TEN_RATIO,
            ),
        )

    def _whole_ring_value(self, radial_distance: float) -> int:
        for ring in range(10, 0, -1):
            if radial_distance <= self._scoring_ring_radius(ring):
                return ring
        return 0

    def _decimal_ring_value(self, radial_distance: float) -> float:
        if radial_distance > self._ring_radius(1):
            return 0.0
        ten_radius = self._scoring_ring_radius(10)
        if radial_distance <= ten_radius:
            perfect_radius = self._perfect_ten_radius()
            if radial_distance <= perfect_radius:
                return 10.9
            fraction_inward = (ten_radius - radial_distance) / max(
                0.001,
                ten_radius - perfect_radius,
            )
            return round(10.0 + 0.8 * fraction_inward, 1)
        for ring in range(9, 0, -1):
            inner = self._scoring_ring_radius(ring + 1)
            outer = self._scoring_ring_radius(ring)
            if radial_distance <= outer:
                fraction_inward = (outer - radial_distance) / max(0.001, outer - inner)
                return round(ring + 0.9 * fraction_inward, 1)
        return 0.0

    def _finish_series(self, now: float) -> None:
        if self.mode == "whole":
            value = sum(shot.value for shot in self.shots)
            display = f"{int(value)} RINGE"
        elif self.mode == "decimal":
            value = round(sum(shot.value for shot in self.shots), 1)
            display = f"{value:.1f}".replace(".", ",") + " RINGE"
        else:
            value = round(sum(shot.value for shot in self.shots) / len(self.shots))
            display = f"Ø {int(value)} TEILER"
        result = TargetResult(self.mode, self.shot_limit, value, display, time.time())
        self.histories[self.mode].appendleft(result)
        self.current_result = result
        self.state = "result"
        self.result_until = now + self.RESULT_DURATION
        self._save_history()
        self.sounds.play("finish")
        LOGGER.info("Zielscheibe Gesamtergebnis: %s", display)

    def _start_new_series(self) -> None:
        self.state = "playing"
        self.shots = []
        self.current_result = None
        self.result_until = 0.0

    def _load_history(self) -> None:
        if self.history_path is None or not self.history_path.exists():
            return
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            for mode in self.MODES:
                for item in data.get(mode, [])[:5]:
                    self.histories[mode].append(
                        TargetResult(
                            mode=mode,
                            shot_count=int(item["shot_count"]),
                            result_value=float(item["result_value"]),
                            display=str(item["display"]),
                            created_at=float(item.get("created_at", 0.0)),
                        )
                    )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            LOGGER.warning("Zielscheiben-Ergebnisse konnten nicht geladen werden: %s", exc)

    def _save_history(self) -> None:
        if self.history_path is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                mode: [
                    {
                        "shot_count": item.shot_count,
                        "result_value": item.result_value,
                        "display": item.display,
                        "created_at": item.created_at,
                    }
                    for item in history
                ]
                for mode, history in self.histories.items()
            }
            temporary = self.history_path.with_suffix(self.history_path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            temporary.replace(self.history_path)
        except OSError as exc:
            LOGGER.warning("Zielscheiben-Ergebnisse konnten nicht gespeichert werden: %s", exc)

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        draw_frame(self.screen, "range", current)
        draw_ambient_foreground(self.screen, "range", current)
        title = self.font_title.render("ZIELSCHEIBE", True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(self.screen.get_width() // 2, 22)))
        self._draw_mode_control()
        self._draw_shot_count_control()
        self._draw_target()
        self._draw_shot_list(self.shot_list_rect)
        self._draw_history()
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)
        draw_button(self.screen, self.size_minus_button, "KLEINER", self.font_small, SAFE_GREEN)
        draw_button(self.screen, self.size_plus_button, "GRÖßER", self.font_small, SAFE_GREEN)
        progress = self.font.render(
            f"SCHUSS {len(self.shots)} VON {self.shot_limit}",
            True,
            SAFE_GREEN,
        )
        self.screen.blit(progress, progress.get_rect(midbottom=(self.target_center[0], self.screen.get_height() - 78)))
        if self.state == "result" and self.current_result is not None:
            self._draw_result(current)

    def _draw_mode_control(self) -> None:
        draw_vintage_enamel_panel(
            self.screen, self.mode_button, 3, alpha=242
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, self.mode_button, 2, border_radius=11)
        draw_aim_point(self.screen, (self.mode_button.left + 18, self.mode_button.centery), SAFE_GREEN, 6)
        label = self.font_tiny.render("GENAUIGKEIT · WECHSELN", True, SAFE_MUTED)
        value = self.font.render(self.MODE_LABELS[self.mode], True, SAFE_CYAN)
        self.screen.blit(label, (self.mode_button.left + 34, self.mode_button.top + 13))
        self.screen.blit(value, (self.mode_button.left + 34, self.mode_button.top + 43))

    def _draw_shot_count_control(self) -> None:
        draw_vintage_enamel_panel(
            self.screen, self.shot_count_button, 2, alpha=242
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, self.shot_count_button, 2, border_radius=11)
        draw_aim_point(self.screen, (self.shot_count_button.left + 18, self.shot_count_button.centery), SAFE_GREEN, 6)
        value = self.font_large.render(str(self.shot_limit), True, SAFE_CYAN)
        label = self.font_micro.render("SCHÜSSE · WECHSELN", True, SAFE_MUTED)
        self.screen.blit(value, value.get_rect(center=(self.shot_count_button.centerx, self.shot_count_button.top + 30)))
        self.screen.blit(label, label.get_rect(center=(self.shot_count_button.centerx, self.shot_count_button.bottom - 17)))

    def _draw_target(self) -> None:
        radius = self.target_radius
        paper_radius = radius + 7
        shadow_center = (self.target_center[0] + 5, self.target_center[1] + 7)
        pygame.draw.circle(
            self.screen,
            (0, 5, 10),
            shadow_center,
            paper_radius + 4,
        )
        pygame.draw.circle(
            self.screen,
            self.RIFLE_PAPER,
            self.target_center,
            paper_radius,
        )
        pygame.draw.circle(
            self.screen,
            self.RIFLE_EDGE,
            self.target_center,
            paper_radius,
            3,
        )

        # Äußere Ringe 1–3 liegen dunkel auf dem blaugrauen Scheibengrund.
        for ring in range(1, 4):
            pygame.draw.circle(
                self.screen,
                self.RIFLE_INK,
                self.target_center,
                round(self._ring_radius(ring)),
                2,
            )

        # Der Spiegel reicht originalgetreu bis zur Außenkante von Ring 4.
        pygame.draw.circle(
            self.screen,
            self.RIFLE_INK,
            self.target_center,
            round(self._ring_radius(4)),
        )
        # Die inneren Ringlinien 5–9 sind cyanhell auf dem dunklen Spiegel.
        for ring in range(5, 10):
            pygame.draw.circle(
                self.screen,
                self.RIFLE_INNER_LINE,
                self.target_center,
                round(self._ring_radius(ring)),
                2,
            )
        # Der elektronische Zehnerbereich ist für die Laserwaffe groß genug,
        # während der kleine Innenkreis die erreichbare 10,9 klar markiert.
        ten_radius = round(self._scoring_ring_radius(10))
        perfect_radius = round(self._perfect_ten_radius())
        pygame.draw.circle(
            self.screen,
            self.RIFLE_TEN_FILL,
            self.target_center,
            ten_radius,
        )
        pygame.draw.circle(
            self.screen,
            self.RIFLE_TEN_EDGE,
            self.target_center,
            ten_radius,
            3,
        )
        pygame.draw.circle(
            self.screen,
            self.RIFLE_TEN_EDGE,
            self.target_center,
            perfect_radius,
            2,
        )

        # Wie auf der Papiervorlage stehen die Ringzahlen 1–8 auf allen vier
        # Achsen. Außen dunkel, innerhalb des Spiegels cyanhell.
        label_font = pygame.font.SysFont("Arial", max(11, round(radius / 18)))
        for ring in range(1, 9):
            outer = self._ring_radius(ring)
            inner = self._ring_radius(ring + 1)
            label_distance = round((outer + inner) / 2.0)
            color = self.RIFLE_INK if ring <= 3 else self.RIFLE_INNER_LINE
            marker = label_font.render(str(ring), True, color)
            positions = (
                (self.target_center[0], self.target_center[1] - label_distance),
                (self.target_center[0], self.target_center[1] + label_distance),
                (self.target_center[0] - label_distance, self.target_center[1]),
                (self.target_center[0] + label_distance, self.target_center[1]),
            )
            for position in positions:
                self.screen.blit(marker, marker.get_rect(center=position))
        for index, shot in enumerate(self.shots, start=1):
            x, y = shot.point
            pygame.draw.circle(self.screen, TARGET_GREEN, (x, y), 9, 2)
            pygame.draw.line(self.screen, TARGET_GREEN, (x - 13, y), (x + 13, y), 2)
            pygame.draw.line(self.screen, TARGET_GREEN, (x, y - 13), (x, y + 13), 2)
            number = self.font_tiny.render(str(index), True, TARGET_CYAN)
            self.screen.blit(number, (x + 10, y - 24))

    def _draw_shot_list(self, rect: pygame.Rect, *, result_view: bool = False) -> None:
        draw_translucent_panel(
            self.screen, rect, SAFE_PANEL, alpha=196, border_radius=12
        )
        pygame.draw.rect(
            self.screen,
            SAFE_GREEN if result_view else SAFE_MUTED,
            rect,
            2,
            border_radius=12,
        )
        heading = self.font.render("EINZELTREFFER", True, SAFE_CYAN)
        mode = self.font_tiny.render(self.MODE_LABELS[self.mode], True, SAFE_GREEN)
        self.screen.blit(heading, heading.get_rect(midtop=(rect.centerx, rect.top + 12)))
        self.screen.blit(mode, mode.get_rect(midtop=(rect.centerx, rect.top + 41)))
        if not self.shots:
            empty = self.font_small.render("Noch kein Treffer", True, SAFE_MUTED)
            self.screen.blit(empty, empty.get_rect(midtop=(rect.centerx, rect.top + 86)))
            return

        available_height = rect.height - 72
        row_height = min(42, max(31, available_height // max(1, self.shot_limit)))
        row_font = self.font_shot if row_height >= 38 else self.font
        for index, shot in enumerate(self.shots, start=1):
            row = pygame.Rect(
                rect.left + 12,
                rect.top + 66 + (index - 1) * row_height,
                rect.width - 24,
                row_height - 4,
            )
            if index % 2:
                draw_translucent_panel(
                    self.screen, row, SAFE_PANEL_LIGHT, alpha=142, border_radius=7
                )
            number = row_font.render(f"{index}.", True, SAFE_MUTED)
            value = row_font.render(shot.display, True, SAFE_GREEN)
            self.screen.blit(number, number.get_rect(midleft=(row.left + 12, row.centery)))
            self.screen.blit(value, value.get_rect(midright=(row.right - 12, row.centery)))

    def _draw_history(self) -> None:
        rect = self.history_rect
        draw_translucent_panel(
            self.screen, rect, SAFE_PANEL, alpha=196, border_radius=12
        )
        pygame.draw.rect(self.screen, SAFE_MUTED, rect, 2, border_radius=12)
        heading = self.font_small.render("LETZTE ERGEBNISSE", True, SAFE_CYAN)
        mode = self.font_tiny.render(self.MODE_LABELS[self.mode], True, SAFE_GREEN)
        self.screen.blit(heading, heading.get_rect(midtop=(rect.centerx, rect.top + 12)))
        self.screen.blit(mode, mode.get_rect(midtop=(rect.centerx, rect.top + 41)))
        history = self.histories[self.mode]
        if not history:
            empty = self.font_small.render("Noch kein Ergebnis", True, SAFE_MUTED)
            self.screen.blit(empty, empty.get_rect(midtop=(rect.centerx, rect.top + 86)))
            return

        row_height = 72
        for index, result in enumerate(history):
            row = pygame.Rect(
                rect.left + 10,
                rect.top + 66 + index * row_height,
                rect.width - 20,
                row_height - 7,
            )
            if index % 2 == 0:
                draw_translucent_panel(
                    self.screen, row, SAFE_PANEL_LIGHT, alpha=142, border_radius=7
                )
            value = self.font_small.render(
                result.display.replace(".", ","),
                True,
                SAFE_GREEN if index == 0 else SAFE_CYAN,
            )
            shots = self.font_tiny.render(
                f"{result.shot_count} SCHÜSSE",
                True,
                SAFE_MUTED,
            )
            self.screen.blit(value, value.get_rect(midleft=(row.left + 12, row.top + 22)))
            self.screen.blit(shots, shots.get_rect(bottomright=(row.right - 9, row.bottom - 8)))

    def _draw_result(self, now: float) -> None:
        result = self.current_result
        if result is None:
            return
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 7, 14, 205))
        self.screen.blit(veil, (0, 0))
        draw_translucent_panel(
            self.screen, self.result_card, SAFE_PANEL, alpha=205, border_radius=16
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, self.result_card, 3, border_radius=16)
        draw_aim_point(self.screen, (self.result_card.right - 28, self.result_card.top + 28), SAFE_GREEN)
        heading = self.font_large.render("GESAMTERGEBNIS", True, SAFE_CYAN)
        score = self.font_score.render(result.display, True, SAFE_GREEN)
        maximum_score_width = 360
        if score.get_width() > maximum_score_width:
            factor = maximum_score_width / score.get_width()
            score = pygame.transform.smoothscale(
                score,
                (maximum_score_width, max(1, round(score.get_height() * factor))),
            )
        detail = self.font.render(
            f"{result.shot_count} SCHÜSSE  ·  {self.MODE_LABELS[result.mode]}",
            True,
            SAFE_CYAN,
        )
        remaining = max(0.0, self.result_until - now)
        timer = self.font_small.render(
            f"BESTENLISTE IN {remaining:.1f} SEKUNDEN",
            True,
            SAFE_MUTED,
        )
        hint = self.font_small.render(
            "3 SEKUNDEN GESAMTAUSWERTUNG",
            True,
            SAFE_GREEN,
        )
        self.screen.blit(heading, heading.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 24)))

        right_center = self.result_card.right - 218
        total_label = self.font_small.render("GESAMTWERTUNG", True, SAFE_MUTED)
        self.screen.blit(total_label, total_label.get_rect(midtop=(right_center, self.result_card.top + 145)))
        self.screen.blit(score, score.get_rect(center=(right_center, self.result_card.top + 238)))
        self.screen.blit(detail, detail.get_rect(midtop=(right_center, self.result_card.top + 302)))
        self.screen.blit(timer, timer.get_rect(midtop=(right_center, self.result_card.bottom - 92)))
        self.screen.blit(hint, hint.get_rect(midtop=(right_center, self.result_card.bottom - 59)))

        list_rect = pygame.Rect(
            self.result_card.left + 28,
            self.result_card.top + 96,
            self.result_card.width // 2 - 66,
            self.result_card.height - 132,
        )
        self._draw_shot_list(list_rect, result_view=True)
