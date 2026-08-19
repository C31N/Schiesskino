from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    SAFE_CYAN,
    SAFE_DARK,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    SAFE_PANEL_LIGHT,
    build_theme_background,
    draw_button,
    draw_cinematic_overlay,
    draw_ready_card,
    draw_result_card,
    draw_translucent_panel,
    limit_projected_brightness,
    nearest_laser_button,
    neutralize_laser_red,
)
from .base import BaseApp
from .cans import CanGameSounds


WHITE = (225, 250, 255)
PLAYER_COLORS = ((0, 136, 162), (0, 148, 88))
PLAYER_BRIGHT = ((0, 205, 245), (0, 225, 120))
BOARD_DARK = (0, 12, 25)
BOARD_MID = (0, 31, 48)


_DUEL_ASSET_LAYOUTS = {
    "tic_tac_toe": (2, 1),
    "connect_four": (2, 1),
    "dots_boxes": (3, 1),
    "memory": (4, 2),
    "nim": (1, 1),
    "reversi": (2, 1),
}
_DUEL_SOURCE_CACHE: dict[tuple[str, int, int], pygame.Surface] = {}
_DUEL_SPRITE_CACHE: dict[tuple[str, int, tuple[int, int], int, int], pygame.Surface] = {}


def load_duel_sprite(
    name: str,
    index: int,
    size: tuple[int, int],
    *,
    angle: int = 0,
    brightness_limit: int = 154,
) -> pygame.Surface:
    """Lädt ein laserneutrales 3D-Motiv der Zweispieler-Arena gepuffert."""

    safe_size = max(2, int(size[0])), max(2, int(size[1]))
    angle_key = int(round(angle)) % 360
    key = name, int(index), safe_size, angle_key, int(brightness_limit)
    cached = _DUEL_SPRITE_CACHE.get(key)
    if cached is not None:
        return cached

    columns, rows = _DUEL_ASSET_LAYOUTS[name]
    if not 0 <= index < columns * rows:
        raise IndexError(f"Ungültiges Motiv {index} für {name}")
    source_key = name, int(index), int(brightness_limit)
    source = _DUEL_SOURCE_CACHE.get(source_key)
    if source is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "assets"
            / "duel_v2"
            / f"{name}_v2.png"
        )
        sheet = pygame.image.load(str(path))
        if pygame.display.get_surface() is not None:
            sheet = sheet.convert_alpha()
        cell_width = sheet.get_width() // columns
        cell_height = sheet.get_height() // rows
        # Ein Blatt wird nur einmal dekodiert. Sämtliche Zellen werden sofort
        # vorbereitet; dadurch gibt es beim ersten Aufdecken mehrerer
        # Memory-Karten keinen sichtbaren Nachladeruckler.
        for cell_index in range(columns * rows):
            column, row = cell_index % columns, cell_index // columns
            cell = sheet.subsurface(
                pygame.Rect(
                    column * cell_width,
                    row * cell_height,
                    cell_width,
                    cell_height,
                )
            ).copy()
            bounds = cell.get_bounding_rect(min_alpha=8)
            prepared = (
                cell.subsurface(bounds).copy()
                if bounds.width and bounds.height
                else cell
            )
            neutralize_laser_red(prepared)
            pixels = pygame.surfarray.pixels3d(prepared)
            pixels[:, :, 0] = 0
            del pixels
            limit_projected_brightness(prepared, brightness_limit)
            _DUEL_SOURCE_CACHE[(name, cell_index, int(brightness_limit))] = prepared
        source = _DUEL_SOURCE_CACHE[source_key]

    scale = min(safe_size[0] / source.get_width(), safe_size[1] / source.get_height())
    fitted = (
        max(2, round(source.get_width() * scale)),
        max(2, round(source.get_height() * scale)),
    )
    sprite = pygame.transform.smoothscale(source, fitted)
    if angle_key:
        sprite = pygame.transform.rotate(sprite, angle_key)
    _DUEL_SPRITE_CACHE[key] = sprite
    return sprite


class DuelBaseApp(BaseApp):
    """Gemeinsame, vollständig pistolenbedienbare Zwei-Spieler-Arena."""

    RESULT_REVEAL_SECONDS = 3.0
    leaderboard_enabled = False
    HANDOFF_SECONDS = 2.0
    BONUS_NOTICE_SECONDS = 1.15

    name = "Laser-Duell"
    title = "LASER-DUELL"
    subtitle = "ZWEI SPIELER · EINE PISTOLE"
    instructions = (
        "Spieler 1 beginnt und führt genau einen Zug aus.",
        "Danach die Pistole an Spieler 2 weitergeben.",
        "Leuchtende Flächen zeigen gültige Ziele und den aktiven Spieler.",
    )
    ready_art: tuple[tuple[str, int, tuple[int, int], tuple[int, int], int], ...] = ()

    def __init__(
        self,
        screen: pygame.Surface,
        *,
        sounds: Optional[CanGameSounds] = None,
        audio_enabled: bool = True,
        random_seed: int = 1919,
    ) -> None:
        super().__init__(screen)
        self.sounds = sounds or CanGameSounds(audio_enabled)
        self.random = random.Random(random_seed)
        self.font_tiny = pygame.font.SysFont("Arial", 14)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22, bold=True)
        self.font_large = pygame.font.SysFont("Arial", 34, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 48, bold=True)
        width, height = screen.get_size()
        self.menu_button = pygame.Rect(width - 170, 22, 142, 44)
        self.start_card = pygame.Rect(0, 0, 700, 370)
        self.start_card.center = (width // 2, height // 2 + 18)
        self.start_button = pygame.Rect(width // 2 - 210, height - 104, 420, 58)
        self.result_card = pygame.Rect(0, 0, 760, 430)
        self.result_card.center = (width // 2, height // 2 + 15)
        self.repeat_button = pygame.Rect(width // 2 - 305, height - 92, 285, 54)
        self.result_menu_button = pygame.Rect(width // 2 + 20, height - 92, 285, 54)
        self.state = "ready"
        self.state_started = time.monotonic()
        self.current_player = 0
        self.turn_number = 1
        self.handoff_until = 0.0
        self.handoff_text = ""
        self.celebration_until = 0.0
        self.winner: Optional[int] = None
        self.finish_reason = ""
        self.scores = [0, 0]
        self.shots = 0
        self.hits = 0
        self._reset_board()

    @property
    def visual_transition_active(self) -> bool:
        return self.state == "celebrating" or (
            self.state == "playing" and time.monotonic() < self.handoff_until
        )

    @property
    def accuracy(self) -> float:
        return 100.0 * self.hits / self.shots if self.shots else 0.0

    @property
    def leaderboard_detail(self) -> str:
        return self.finish_reason

    @property
    def leaderboard_metrics(self) -> tuple[tuple[str, str], ...]:
        return self.result_values

    @property
    def result_values(self) -> tuple[tuple[str, str], ...]:
        # Duelle haben bewusst weder Bestenlisten noch eine wertende
        # Trefferquote. Entscheidend ist ausschließlich der direkte
        # Spielstand der beiden Kinder.
        return (
            ("SPIELER 1", str(self.scores[0])),
            ("SPIELER 2", str(self.scores[1])),
        )

    def _reset_board(self) -> None:
        return

    def start(self, now: Optional[float] = None) -> None:
        self.state = "ready"
        self.state_started = now if now is not None else time.monotonic()
        self.current_player = 0
        self.turn_number = 1
        self.handoff_until = 0.0
        self.handoff_text = ""
        self.celebration_until = 0.0
        self.winner = None
        self.finish_reason = ""
        self.scores = [0, 0]
        self.shots = 0
        self.hits = 0
        self._reset_board()
        self.sounds.play("button")

    def stop(self) -> None:
        self.sounds.stop_all()

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        if self.state == "celebrating" and current >= self.celebration_until:
            self.state = "game_over"
            self.state_started = current
            self.handoff_until = 0.0

    def _begin(self, now: float) -> str:
        self.current_player = 0
        self.turn_number = 1
        self.state = "playing"
        self.state_started = now
        self.handoff_until = now + 0.35
        self.handoff_text = "SPIELER 1 BEGINNT"
        self.sounds.play("go")
        return "handled"

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        return "miss"

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        current = now if now is not None else time.monotonic()
        if nearest_laser_button(pos, (("menu", self.menu_button),)) == "menu":
            self.sounds.play("button")
            return "menu"
        if self.state == "ready":
            if self.start_card.collidepoint(pos) or nearest_laser_button(
                pos, (("start", self.start_button),)
            ) == "start":
                return self._begin(current)
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
                self.start(current)
                return self._begin(current)
            return "handled"
        if self.state == "celebrating":
            return "handled"
        if current < self.handoff_until:
            return "handled"
        self.shots += 1
        self.sounds.play("shot")
        return self._handle_turn(pos, current)

    def _valid_hit(self) -> None:
        self.hits += 1
        self.sounds.play("target_hit")

    def _invalid(self, message: str, now: float) -> str:
        self.handoff_text = message
        self.handoff_until = now + 0.65
        self.sounds.play("miss")
        return "miss"

    def _next_player(self, now: float, *, bonus: bool = False) -> None:
        if bonus:
            self.handoff_text = f"SPIELER {self.current_player + 1} DARF NOCH EINMAL"
        else:
            self.current_player = 1 - self.current_player
            self.turn_number += 1
            self.handoff_text = f"WAFFE AN SPIELER {self.current_player + 1} WEITERGEBEN"
        # Beim normalen Spielerwechsel bleibt genügend Zeit, die einzige
        # Pistole wirklich sicher weiterzugeben. Bonuszüge bleiben bewusst
        # kürzer, weil dabei kein Gerätewechsel stattfindet.
        self.handoff_until = now + (
            self.BONUS_NOTICE_SECONDS if bonus else self.HANDOFF_SECONDS
        )

    def _finish(self, winner: Optional[int], reason: str, now: float) -> None:
        self.winner = winner
        self.finish_reason = reason
        self.state = "celebrating"
        self.state_started = now
        self.celebration_until = now + self.RESULT_REVEAL_SECONDS
        self.handoff_text = reason
        self.handoff_until = self.celebration_until
        self.sounds.play("finish")

    def _draw_arena(self) -> None:
        self.screen.blit(build_theme_background(self.screen.get_size(), "menu"), (0, 0))
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 7, 17, 185))
        self.screen.blit(veil, (0, 0))
        width, height = self.screen.get_size()
        for radius in range(520, 110, -70):
            color = (0, 48 + radius // 18, 72 + radius // 15, 30)
            pygame.draw.ellipse(
                veil,
                color,
                pygame.Rect(width // 2 - radius, height - radius // 2, radius * 2, radius),
                2,
            )
        self.screen.blit(veil, (0, 0))
        draw_cinematic_overlay(self.screen)

    def _draw_header(self) -> None:
        # Der Spielerhinweis sitzt oben in der Mitte. Lange Spieltitel würden
        # dort sonst hineinragen (besonders MEMORY-DUELL und REVERSI LIGHT).
        # Der linke Titelbereich bleibt deshalb bewusst auf 290 Pixel begrenzt.
        title_font = self.font_large
        max_title_width = 290
        if title_font.size(self.title)[0] > max_title_width:
            title_font = self.font
        if title_font.size(self.title)[0] > max_title_width:
            title_font = self.font_small
        title = title_font.render(self.title, True, SAFE_CYAN)
        self.screen.blit(title, (28, 24))
        draw_button(self.screen, self.menu_button, "MENÜ", self.font_small, SAFE_CYAN)
        if self.state != "playing":
            return
        player_color = PLAYER_BRIGHT[self.current_player]
        pill = pygame.Rect(0, 0, 365, 54)
        pill.midtop = (self.screen.get_width() // 2, 18)
        draw_translucent_panel(self.screen, pill, SAFE_PANEL, alpha=226, border_radius=25)
        pygame.draw.rect(self.screen, player_color, pill, 3, border_radius=25)
        pygame.draw.circle(self.screen, PLAYER_COLORS[self.current_player], (pill.left + 29, pill.centery), 14)
        pygame.draw.circle(self.screen, player_color, (pill.left + 29, pill.centery), 14, 3)
        label = self.font.render(
            f"SPIELER {self.current_player + 1} IST AM ZUG",
            True,
            player_color,
        )
        self.screen.blit(label, label.get_rect(center=pill.center))

    def _score_panel_rect(self, player: int) -> pygame.Rect:
        panel_width = 228
        return pygame.Rect(
            28 if player == 0 else self.screen.get_width() - panel_width - 28,
            88,
            panel_width,
            54,
        )

    def _score_font(self, label: str, panel: pygame.Rect) -> pygame.font.Font:
        return (
            self.font_small
            if self.font_small.size(label)[0] <= panel.width - 18
            else self.font_tiny
        )

    def _draw_score(self, left_label: str = "PUNKTE") -> None:
        for player in range(2):
            panel = self._score_panel_rect(player)
            draw_translucent_panel(self.screen, panel, SAFE_PANEL, alpha=205, border_radius=12)
            pygame.draw.rect(self.screen, PLAYER_BRIGHT[player], panel, 2, border_radius=12)
            label = f"SPIELER {player + 1} · {left_label} {self.scores[player]}"
            score_font = self._score_font(label, panel)
            text = score_font.render(
                label,
                True,
                PLAYER_BRIGHT[player],
            )
            self.screen.blit(text, text.get_rect(center=panel.center))

    def _draw_handoff(self, now: float) -> None:
        if now >= self.handoff_until or not self.handoff_text:
            return
        if self.state == "celebrating":
            color = SAFE_CYAN if self.winner is None else PLAYER_BRIGHT[self.winner]
        else:
            color = PLAYER_BRIGHT[self.current_player]
        if self.state == "celebrating":
            # Die dreisekündige Gewinnmeldung nutzt die freie Position des
            # normalen Zughinweises. Spielerstände und Gewinnlinie bleiben
            # dadurch gleichzeitig vollständig sichtbar.
            banner = pygame.Rect(0, 0, 390, 64)
            banner.midtop = (self.screen.get_width() // 2, 14)
        else:
            banner = pygame.Rect(0, 0, 650, 52)
            banner.midtop = (self.screen.get_width() // 2, 86)
        draw_translucent_panel(self.screen, banner, SAFE_DARK, alpha=238, border_radius=14)
        pygame.draw.rect(self.screen, color, banner, 3, border_radius=14)
        text = self.font.render(self.handoff_text, True, color)
        text_center = (
            (banner.centerx, banner.top + 23)
            if self.state == "celebrating"
            else banner.center
        )
        self.screen.blit(text, text.get_rect(center=text_center))

        if self.state == "celebrating":
            remaining = max(0, math.ceil(self.celebration_until - now))
            hint = self.font_tiny.render(
                f"ERGEBNIS IN {remaining}", True, SAFE_MUTED
            )
            self.screen.blit(hint, hint.get_rect(center=(banner.centerx, banner.top + 47)))

    def _draw_board(self, now: float) -> None:
        return

    def _draw_ready_art(self) -> None:
        """Zeigt das Spielprinzip schon vor dem Start als klares 3D-Motiv."""

        center = (self.screen.get_width() // 2, self.start_card.top - 47)
        halo = pygame.Surface((250, 96), pygame.SRCALPHA)
        pygame.draw.ellipse(halo, (0, 138, 168, 34), halo.get_rect().inflate(-12, -12))
        pygame.draw.ellipse(halo, (0, 225, 120, 54), halo.get_rect().inflate(-12, -12), 2)
        self.screen.blit(halo, halo.get_rect(center=center))
        for name, index, size, offset, angle in self.ready_art:
            sprite = load_duel_sprite(name, index, size, angle=angle)
            position = center[0] + offset[0], center[1] + offset[1]
            self.screen.blit(sprite, sprite.get_rect(center=position))

    def draw(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self._draw_arena()
        if self.state == "ready":
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
            self._draw_ready_art()
            return
        self._draw_header()
        self._draw_board(current)
        self._draw_handoff(current)
        if self.state == "game_over":
            draw_result_card(
                self.screen,
                self.result_card,
                "DUELL BEENDET",
                self.finish_reason,
                self.result_values,
                self.repeat_button,
                self.result_menu_button,
                (self.font, self.font_large, self.font_title, self.font_small),
            )


class TicTacToeApp(DuelBaseApp):
    name = "Tic-Tac-Toe"
    title = "TIC-TAC-TOE"
    subtitle = "DREI IN EINER REIHE"
    instructions = (
        "Spieler 1 setzt X, Spieler 2 setzt O.",
        "Auf ein freies Feld schießen, danach Pistole weitergeben.",
        "Drei gleiche Zeichen in einer Reihe gewinnen.",
    )
    ready_art = (
        ("tic_tac_toe", 0, (76, 70), (-39, -2), -7),
        ("tic_tac_toe", 1, (76, 70), (39, 2), 7),
    )

    def _reset_board(self) -> None:
        self.board = [-1] * 9
        self.board_rect = pygame.Rect(212, 142, 600, 600)

    def _cells(self) -> list[pygame.Rect]:
        return [
            pygame.Rect(
                self.board_rect.left + (index % 3) * 200,
                self.board_rect.top + (index // 3) * 200,
                200,
                200,
            )
            for index in range(9)
        ]

    def _winning_player(self) -> Optional[int]:
        winning = self._winning_cells()
        return self.board[winning[0]] if winning is not None else None

    def _winning_cells(self) -> Optional[tuple[int, int, int]]:
        for a, b, c in ((0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)):
            if self.board[a] >= 0 and self.board[a] == self.board[b] == self.board[c]:
                return a, b, c
        return None

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        selected = nearest_laser_button(pos, tuple(enumerate(self._cells())), expansion=(24, 24))
        if selected is None or self.board[selected] >= 0:
            return self._invalid("BITTE AUF EIN FREIES FELD SCHIEßEN", now)
        self.board[selected] = self.current_player
        self._valid_hit()
        winner = self._winning_player()
        if winner is not None:
            self.scores[winner] = 1
            self._finish(winner, f"SPIELER {winner + 1} GEWINNT", now)
        elif all(value >= 0 for value in self.board):
            self._finish(None, "UNENTSCHIEDEN", now)
        else:
            self._next_player(now)
        return "hit"

    def _draw_board(self, now: float) -> None:
        cells = self._cells()
        winning = self._winning_cells()
        for index, rect in enumerate(cells):
            draw_translucent_panel(self.screen, rect.inflate(-8, -8), BOARD_DARK, alpha=232, border_radius=18)
            border = PLAYER_BRIGHT[self.winner] if winning and index in winning and self.winner is not None else SAFE_MUTED
            pygame.draw.rect(self.screen, border, rect.inflate(-8, -8), 5 if winning and index in winning else 2, border_radius=18)
            value = self.board[index]
            if value >= 0:
                sprite = load_duel_sprite("tic_tac_toe", value, (132, 132))
                self.screen.blit(sprite, sprite.get_rect(center=rect.center))
        if winning is not None and self.state == "celebrating" and self.winner is not None:
            pygame.draw.line(
                self.screen,
                PLAYER_BRIGHT[self.winner],
                cells[winning[0]].center,
                cells[winning[-1]].center,
                9,
            )


class ConnectFourApp(DuelBaseApp):
    name = "4 Gewinnt"
    title = "4 GEWINNT"
    subtitle = "SPALTE TREFFEN · STEIN FÄLLT"
    instructions = (
        "Auf eine der sieben großen Spalten schießen.",
        "Der Spielstein fällt automatisch auf den tiefsten freien Platz.",
        "Vier eigene Steine waagerecht, senkrecht oder diagonal gewinnen.",
    )
    ready_art = (
        ("connect_four", 0, (72, 68), (-34, 5), 0),
        ("connect_four", 1, (72, 68), (31, -5), 0),
    )

    def _reset_board(self) -> None:
        self.board = [[-1 for _ in range(7)] for _ in range(6)]
        self.board_rect = pygame.Rect(162, 164, 700, 540)

    def _column_rects(self) -> list[pygame.Rect]:
        return [pygame.Rect(self.board_rect.left + col * 100, 142, 100, 578) for col in range(7)]

    def _has_four(self, player: int) -> bool:
        return self._winning_cells(player) is not None

    def _winning_cells(self, player: int) -> Optional[tuple[tuple[int, int], ...]]:
        for row in range(6):
            for col in range(7):
                for dc, dr in ((1,0),(0,1),(1,1),(1,-1)):
                    cells = [(row + dr*i, col + dc*i) for i in range(4)]
                    if all(0 <= r < 6 and 0 <= c < 7 and self.board[r][c] == player for r, c in cells):
                        return tuple(cells)
        return None

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        column = nearest_laser_button(pos, tuple(enumerate(self._column_rects())), expansion=(20, 20))
        if column is None:
            return self._invalid("AUF EINE SPALTE SCHIEßEN", now)
        target_row = next((row for row in range(5, -1, -1) if self.board[row][column] < 0), None)
        if target_row is None:
            return self._invalid("DIESE SPALTE IST VOLL", now)
        self.board[target_row][column] = self.current_player
        self._valid_hit()
        if self._has_four(self.current_player):
            self.scores[self.current_player] = 1
            self._finish(self.current_player, f"SPIELER {self.current_player + 1} GEWINNT", now)
        elif all(value >= 0 for row in self.board for value in row):
            self._finish(None, "UNENTSCHIEDEN", now)
        else:
            self._next_player(now)
        return "hit"

    def _draw_board(self, now: float) -> None:
        winning = self._winning_cells(self.winner) if self.winner is not None else None
        draw_translucent_panel(self.screen, self.board_rect.inflate(22, 22), (0, 30, 54), alpha=238, border_radius=28)
        pygame.draw.rect(self.screen, SAFE_CYAN, self.board_rect.inflate(22, 22), 3, border_radius=28)
        for col, column_rect in enumerate(self._column_rects()):
            if col % 2 == 0:
                pygame.draw.rect(self.screen, (0, 42, 58), column_rect.inflate(-10, 0), border_radius=14)
            arrow = ((column_rect.centerx, 151), (column_rect.centerx-14, 171), (column_rect.centerx+14, 171))
            pygame.draw.polygon(self.screen, PLAYER_BRIGHT[self.current_player], arrow)
        for row in range(6):
            for col in range(7):
                center = (self.board_rect.left + col*100 + 50, self.board_rect.top + row*90 + 45)
                pygame.draw.circle(self.screen, (0, 6, 16), center, 34)
                pygame.draw.circle(self.screen, SAFE_MUTED, center, 34, 2)
                value = self.board[row][col]
                if value >= 0:
                    token = load_duel_sprite("connect_four", value, (70, 70))
                    self.screen.blit(token, token.get_rect(center=center))
                    owner = self.font_small.render(str(value + 1), True, WHITE)
                    self.screen.blit(owner, owner.get_rect(center=(center[0] + 6, center[1] + 5)))
                if winning is not None and (row, col) in winning:
                    pygame.draw.circle(self.screen, PLAYER_BRIGHT[self.winner], center, 39, 5)
        if winning is not None and self.state == "celebrating":
            first_row, first_col = winning[0]
            last_row, last_col = winning[-1]
            start = (
                self.board_rect.left + first_col * 100 + 50,
                self.board_rect.top + first_row * 90 + 45,
            )
            end = (
                self.board_rect.left + last_col * 100 + 50,
                self.board_rect.top + last_row * 90 + 45,
            )
            pygame.draw.line(self.screen, PLAYER_BRIGHT[self.winner], start, end, 7)


class DotsBoxesApp(DuelBaseApp):
    name = "Käsekästchen"
    title = "KÄSEKÄSTCHEN"
    subtitle = "LINIEN SETZEN · KÄSTCHEN EROBERN"
    instructions = (
        "Auf eine freie Linie zwischen zwei Punkten schießen.",
        "Wer ein Kästchen schließt, erhält einen Punkt.",
        "Nach einem geschlossenen Kästchen bleibt derselbe Spieler am Zug.",
    )
    ready_art = (
        ("dots_boxes", 0, (38, 38), (-65, 0), 0),
        ("dots_boxes", 1, (116, 25), (0, 0), 0),
        ("dots_boxes", 0, (38, 38), (65, 0), 0),
        ("dots_boxes", 2, (54, 45), (0, 15), 0),
    )

    def _reset_board(self) -> None:
        self.cols, self.rows = 5, 4
        self.origin = (172, 176)
        self.spacing = (170, 160)
        self.horizontal: dict[tuple[int,int], int] = {}
        self.vertical: dict[tuple[int,int], int] = {}
        self.boxes: dict[tuple[int,int], int] = {}

    def _edge_targets(self) -> list[tuple[tuple[str,int,int], pygame.Rect]]:
        ox, oy = self.origin
        sx, sy = self.spacing
        targets = []
        for row in range(self.rows):
            for col in range(self.cols - 1):
                rect = pygame.Rect(ox + col*sx + 18, oy + row*sy - 24, sx - 36, 48)
                targets.append((("h", row, col), rect))
        for row in range(self.rows - 1):
            for col in range(self.cols):
                rect = pygame.Rect(ox + col*sx - 24, oy + row*sy + 18, 48, sy - 36)
                targets.append((("v", row, col), rect))
        return targets

    def _box_complete(self, row: int, col: int) -> bool:
        return (
            (row, col) in self.horizontal
            and (row + 1, col) in self.horizontal
            and (row, col) in self.vertical
            and (row, col + 1) in self.vertical
        )

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        targets = self._edge_targets()
        selected = nearest_laser_button(pos, tuple((key, rect) for key, rect in targets), expansion=(18,18))
        if selected is None:
            return self._invalid("AUF EINE FREIE LINIE SCHIEßEN", now)
        kind, row, col = selected
        collection = self.horizontal if kind == "h" else self.vertical
        if (row, col) in collection:
            return self._invalid("DIESE LINIE IST BEREITS BELEGT", now)
        collection[(row, col)] = self.current_player
        self._valid_hit()
        completed = 0
        possible_boxes = ((row - 1, col), (row, col)) if kind == "h" else ((row, col - 1), (row, col))
        for box_row, box_col in possible_boxes:
            if 0 <= box_row < self.rows - 1 and 0 <= box_col < self.cols - 1:
                if (box_row, box_col) not in self.boxes and self._box_complete(box_row, box_col):
                    self.boxes[(box_row, box_col)] = self.current_player
                    self.scores[self.current_player] += 1
                    completed += 1
        if len(self.boxes) == (self.rows - 1) * (self.cols - 1):
            winner = None if self.scores[0] == self.scores[1] else (0 if self.scores[0] > self.scores[1] else 1)
            reason = "UNENTSCHIEDEN" if winner is None else f"SPIELER {winner + 1} GEWINNT"
            self._finish(winner, reason, now)
        else:
            self._next_player(now, bonus=completed > 0)
        return "hit"

    def _draw_board(self, now: float) -> None:
        self._draw_score("KÄSTCHEN")
        ox, oy = self.origin
        sx, sy = self.spacing
        # Jede noch freie Verbindung ist als ruhige, breite Zielspur sichtbar.
        # Damit erkennen auch Kinder sofort, wohin der nächste Schuss gehört.
        for (kind, row, col), target in self._edge_targets():
            collection = self.horizontal if kind == "h" else self.vertical
            player = collection.get((row, col))
            rail_size = (sx - 32, 30) if kind == "h" else (sy - 32, 30)
            rail = load_duel_sprite(
                "dots_boxes", 1, rail_size, angle=0 if kind == "h" else 90
            ).copy()
            if player is not None:
                tint = pygame.Surface(rail.get_size(), pygame.SRCALPHA)
                tint.fill((*PLAYER_BRIGHT[player], 255))
                rail.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            rail.set_alpha(238 if player is not None else 82)
            self.screen.blit(rail, rail.get_rect(center=target.center))
            if player is None:
                pygame.draw.circle(self.screen, SAFE_MUTED, target.center, 6, 2)
        for (row, col), player in self.boxes.items():
            rect = pygame.Rect(ox + col*sx + 12, oy + row*sy + 12, sx - 24, sy - 24)
            tile = load_duel_sprite("dots_boxes", 2, (rect.width - 12, rect.height - 12)).copy()
            tint = pygame.Surface(tile.get_size(), pygame.SRCALPHA)
            tint.fill((*PLAYER_BRIGHT[player], 145))
            tile.blit(tint, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
            tile.set_alpha(178)
            self.screen.blit(tile, tile.get_rect(center=rect.center))
            label = self.font_large.render(str(player + 1), True, PLAYER_BRIGHT[player])
            self.screen.blit(label, label.get_rect(center=rect.center))
        for row in range(self.rows):
            for col in range(self.cols):
                point = (ox+col*sx, oy+row*sy)
                node = load_duel_sprite("dots_boxes", 0, (38, 38))
                self.screen.blit(node, node.get_rect(center=point))


class MemoryDuelApp(DuelBaseApp):
    name = "Memory-Duell"
    title = "MEMORY-DUELL"
    subtitle = "PAARE FINDEN · BONUSZUG VERDIENEN"
    instructions = (
        "Pro Zug nacheinander zwei verdeckte Karten treffen.",
        "Gleiches Paar: ein Punkt und derselbe Spieler ist erneut dran.",
        "Kein Paar: Karten drehen sich zurück und die Pistole wird übergeben.",
    )
    SYMBOLS = ("WELLE", "STERN", "MOND", "ANKER", "KRONE", "BLITZ", "KREIS", "RAUTE")
    ready_art = (
        ("memory", 1, (48, 43), (-57, -12), 0),
        ("memory", 3, (48, 43), (-19, 12), 0),
        ("memory", 5, (48, 43), (19, -12), 0),
        ("memory", 7, (48, 43), (57, 12), 0),
    )

    def _reset_board(self) -> None:
        values = list(range(8)) * 2
        self.random.shuffle(values)
        self.cards = values
        self.revealed: list[int] = []
        self.matched: dict[int, int] = {}
        self.pending_hide_at = 0.0
        self.pending_bonus = False

    @property
    def visual_transition_active(self) -> bool:
        return super().visual_transition_active or self.pending_hide_at > 0.0

    def _card_rects(self) -> list[pygame.Rect]:
        left, top, width, height, gap = 142, 151, 170, 125, 18
        return [pygame.Rect(left+(i%4)*(width+gap), top+(i//4)*(height+gap), width, height) for i in range(16)]

    def update(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        super().update(current)
        if self.state != "playing":
            return
        if self.pending_hide_at and current >= self.pending_hide_at:
            self.revealed.clear()
            self.pending_hide_at = 0.0
            self._next_player(current, bonus=self.pending_bonus)
            self.pending_bonus = False

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        if self.pending_hide_at:
            return "handled"
        selected = nearest_laser_button(pos, tuple(enumerate(self._card_rects())), expansion=(28,24))
        if selected is None or selected in self.matched or selected in self.revealed:
            return self._invalid("EINE VERDECKTE KARTE AUSWÄHLEN", now)
        self.revealed.append(selected)
        self._valid_hit()
        if len(self.revealed) == 1:
            self.handoff_text = "NOCH EINE KARTE AUFDECKEN"
            self.handoff_until = now + 0.35
            return "hit"
        first, second = self.revealed
        match = self.cards[first] == self.cards[second]
        if match:
            self.matched[first] = self.current_player
            self.matched[second] = self.current_player
            self.scores[self.current_player] += 1
            if len(self.matched) == 16:
                winner = None if self.scores[0] == self.scores[1] else (0 if self.scores[0] > self.scores[1] else 1)
                reason = "UNENTSCHIEDEN" if winner is None else f"SPIELER {winner + 1} GEWINNT"
                self._finish(winner, reason, now)
            else:
                self.pending_hide_at = now + 0.85
                self.pending_bonus = True
                self.handoff_text = "PAAR GEFUNDEN · NOCH EINMAL"
                self.handoff_until = self.pending_hide_at
        else:
            self.pending_hide_at = now + 1.25
            self.pending_bonus = False
            self.handoff_text = "KEIN PAAR · KARTEN MERKEN"
            self.handoff_until = self.pending_hide_at
        return "hit"

    def _draw_symbol(self, rect: pygame.Rect, value: int, color: Tuple[int,int,int]) -> None:
        sprite = load_duel_sprite("memory", value, (94, 86))
        self.screen.blit(sprite, sprite.get_rect(center=rect.center))

    def _draw_board(self, now: float) -> None:
        self._draw_score("PAARE")
        for index, rect in enumerate(self._card_rects()):
            owner = self.matched.get(index)
            visible = index in self.revealed or owner is not None
            color = PLAYER_BRIGHT[owner] if owner is not None else SAFE_CYAN
            draw_translucent_panel(self.screen, rect, BOARD_DARK if visible else BOARD_MID, alpha=238, border_radius=16)
            pygame.draw.rect(self.screen, color if visible else SAFE_MUTED, rect, 3, border_radius=16)
            if visible:
                self._draw_symbol(rect, self.cards[index], color)
            else:
                pygame.draw.circle(self.screen, SAFE_MUTED, rect.center, 28, 3)
                pygame.draw.circle(self.screen, (0, 75, 94), rect.center, 12, 2)


class NimDuelApp(DuelBaseApp):
    name = "Nim-Duell"
    title = "NIM-DUELL"
    subtitle = "TAKTISCH 1, 2 ODER 3 ENTFERNEN"
    instructions = (
        "Zu Beginn liegen fünfzehn Energiestäbe bereit.",
        "Pro Zug auf −1, −2 oder −3 schießen.",
        "Wer den letzten Energiestab nimmt, gewinnt das Duell.",
    )
    ready_art = (
        ("nim", 0, (29, 72), (-38, 6), -5),
        ("nim", 0, (29, 72), (0, -4), 0),
        ("nim", 0, (29, 72), (38, 6), 5),
    )

    def _reset_board(self) -> None:
        self.remaining_items = 15
        self.choice_rects = (
            pygame.Rect(184, 570, 190, 100),
            pygame.Rect(417, 570, 190, 100),
            pygame.Rect(650, 570, 190, 100),
        )

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        available = tuple(
            (amount, rect)
            for amount, rect in enumerate(self.choice_rects, start=1)
            if amount <= self.remaining_items
        )
        selected = nearest_laser_button(pos, available, expansion=(48,36))
        if selected is None:
            unavailable = nearest_laser_button(
                pos,
                tuple(enumerate(self.choice_rects, start=1)),
                expansion=(20, 20),
            )
            if unavailable is not None and unavailable > self.remaining_items:
                noun = "STAB" if self.remaining_items == 1 else "STÄBE"
                return self._invalid(
                    f"ES SIND NUR NOCH {self.remaining_items} {noun} DA",
                    now,
                )
            return self._invalid("−1, −2 ODER −3 AUSWÄHLEN", now)
        amount = selected
        self.remaining_items -= amount
        self.scores[self.current_player] += amount
        self._valid_hit()
        if self.remaining_items == 0:
            self._finish(self.current_player, f"SPIELER {self.current_player + 1} NIMMT DEN LETZTEN STAB", now)
        else:
            self._next_player(now)
        return "hit"

    def _draw_board(self, now: float) -> None:
        self._draw_score("GENOMMEN")
        count = self.font_title.render(f"NOCH {self.remaining_items}", True, PLAYER_BRIGHT[self.current_player])
        self.screen.blit(count, count.get_rect(midtop=(self.screen.get_width()//2, 122)))
        for index in range(15):
            row, col = divmod(index, 5)
            rect = pygame.Rect(267 + col*105, 218 + row*100, 70, 72)
            active = index < self.remaining_items
            draw_translucent_panel(self.screen, rect, BOARD_MID if active else SAFE_DARK, alpha=230, border_radius=14)
            color = SAFE_CYAN if active else (0, 36, 48)
            pygame.draw.rect(self.screen, color, rect, 3 if active else 1, border_radius=14)
            if active:
                rod = load_duel_sprite("nim", 0, (47, 63))
                self.screen.blit(rod, rod.get_rect(center=rect.center))
        for amount, rect in enumerate(self.choice_rects, start=1):
            enabled = amount <= self.remaining_items
            color = PLAYER_BRIGHT[self.current_player] if enabled else (0, 52, 68)
            panel = SAFE_PANEL_LIGHT if enabled else SAFE_DARK
            draw_translucent_panel(self.screen, rect, panel, alpha=232, border_radius=20)
            pygame.draw.rect(self.screen, color, rect, 4 if enabled else 2, border_radius=20)
            text = self.font_title.render(f"−{amount}", True, color)
            self.screen.blit(text, text.get_rect(center=(rect.centerx, rect.centery - (8 if not enabled else 0))))
            if not enabled:
                hint = self.font_tiny.render("NICHT MÖGLICH", True, SAFE_MUTED)
                self.screen.blit(hint, hint.get_rect(midbottom=(rect.centerx, rect.bottom - 9)))


class ReversiLightApp(DuelBaseApp):
    name = "Reversi Light"
    title = "REVERSI LIGHT"
    subtitle = "6 × 6 · EINSCHLIEßEN UND WENDEN"
    instructions = (
        "Auf ein leuchtend markiertes gültiges Feld schießen.",
        "Eingeschlossene gegnerische Steine wechseln die Farbe.",
        "Kann ein Spieler nicht setzen, wird sein Zug automatisch übersprungen.",
    )
    ready_art = (
        ("reversi", 0, (62, 56), (-31, -13), 0),
        ("reversi", 1, (62, 56), (31, 13), 0),
        ("reversi", 1, (48, 43), (35, -20), 0),
        ("reversi", 0, (48, 43), (-35, 20), 0),
    )
    DIRECTIONS = tuple((dx,dy) for dx in (-1,0,1) for dy in (-1,0,1) if (dx,dy)!=(0,0))

    def _reset_board(self) -> None:
        self.board = [[-1 for _ in range(6)] for _ in range(6)]
        self.board[2][2] = self.board[3][3] = 0
        self.board[2][3] = self.board[3][2] = 1
        self.board_rect = pygame.Rect(227, 150, 570, 570)
        self.scores = [2, 2]

    def _cell_rects(self) -> list[pygame.Rect]:
        return [pygame.Rect(self.board_rect.left+(i%6)*95, self.board_rect.top+(i//6)*95, 95, 95) for i in range(36)]

    def _flips(self, row: int, col: int, player: int) -> list[tuple[int,int]]:
        if not (0 <= row < 6 and 0 <= col < 6) or self.board[row][col] >= 0:
            return []
        flips = []
        opponent = 1-player
        for dx, dy in self.DIRECTIONS:
            line = []
            x, y = col+dx, row+dy
            while 0 <= x < 6 and 0 <= y < 6 and self.board[y][x] == opponent:
                line.append((y,x)); x += dx; y += dy
            if line and 0 <= x < 6 and 0 <= y < 6 and self.board[y][x] == player:
                flips.extend(line)
        return flips

    def _valid_moves(self, player: int) -> dict[tuple[int,int], list[tuple[int,int]]]:
        return {(r,c): flips for r in range(6) for c in range(6) if (flips := self._flips(r,c,player))}

    def _refresh_scores(self) -> None:
        self.scores = [sum(value == player for row in self.board for value in row) for player in range(2)]

    def _handle_turn(self, pos: Tuple[int, int], now: float) -> str:
        selected = nearest_laser_button(pos, tuple(enumerate(self._cell_rects())), expansion=(16,16))
        if selected is None:
            return self._invalid("AUF EIN LEUCHTENDES FELD SCHIEßEN", now)
        row, col = divmod(selected, 6)
        flips = self._flips(row, col, self.current_player)
        if not flips:
            return self._invalid("DIESER ZUG SCHLIEßT KEINEN STEIN EIN", now)
        self.board[row][col] = self.current_player
        for flip_row, flip_col in flips:
            self.board[flip_row][flip_col] = self.current_player
        self._refresh_scores()
        self._valid_hit()
        next_player = 1-self.current_player
        if self._valid_moves(next_player):
            self._next_player(now)
        elif self._valid_moves(self.current_player):
            self._next_player(now, bonus=True)
            self.handoff_text = f"SPIELER {next_player + 1} MUSS AUSSETZEN"
        else:
            winner = None if self.scores[0] == self.scores[1] else (0 if self.scores[0] > self.scores[1] else 1)
            reason = "UNENTSCHIEDEN" if winner is None else f"SPIELER {winner + 1} GEWINNT"
            self._finish(winner, reason, now)
        return "hit"

    def _draw_board(self, now: float) -> None:
        self._draw_score("STEINE")
        valid = self._valid_moves(self.current_player)
        draw_translucent_panel(self.screen, self.board_rect.inflate(18,18), (0, 28, 45), alpha=238, border_radius=22)
        pygame.draw.rect(self.screen, SAFE_CYAN, self.board_rect.inflate(18,18), 3, border_radius=22)
        for index, rect in enumerate(self._cell_rects()):
            row, col = divmod(index, 6)
            fill = (0, 35, 44) if (row+col)%2==0 else (0, 47, 54)
            pygame.draw.rect(self.screen, fill, rect.inflate(-4,-4), border_radius=10)
            pygame.draw.rect(self.screen, (0, 78, 91), rect.inflate(-4,-4), 1, border_radius=10)
            value = self.board[row][col]
            if value >= 0:
                token = load_duel_sprite("reversi", value, (72, 72))
                self.screen.blit(token, token.get_rect(center=rect.center))
                owner = self.font_tiny.render(str(value + 1), True, WHITE)
                self.screen.blit(owner, owner.get_rect(center=(rect.centerx + 5, rect.centery + 5)))
            elif (row,col) in valid:
                pygame.draw.circle(self.screen, PLAYER_BRIGHT[self.current_player], rect.center, 20, 3)
                pygame.draw.circle(self.screen, PLAYER_COLORS[self.current_player], rect.center, 6)


DUEL_GAME_CLASSES = (
    TicTacToeApp,
    ConnectFourApp,
    DotsBoxesApp,
    MemoryDuelApp,
    NimDuelApp,
    ReversiLightApp,
)
