from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pygame

from .arcade_common import (
    LASER_RESULT_EXPANSION,
    SAFE_CYAN,
    SAFE_DARK,
    SAFE_GREEN,
    SAFE_MUTED,
    SAFE_PANEL,
    build_theme_background,
    build_name_keyboard_layout,
    draw_aim_point,
    draw_button,
    draw_translucent_panel,
    draw_vintage_enamel_panel,
    limit_projected_brightness,
    nearest_laser_button,
    neutralize_laser_red,
)

LOGGER = logging.getLogger(__name__)
WHITE = (225, 250, 255)
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ"


@dataclass
class ArcadeLeaderboardEntry:
    board: str
    name: str
    rank_value: float
    tie1: float
    tie2: float
    tie3: float
    value_text: str
    detail: str
    date: str

    @classmethod
    def from_dict(cls, data: dict) -> "ArcadeLeaderboardEntry":
        return cls(
            board=str(data.get("board", "")),
            name=str(data.get("name", ""))[:8],
            rank_value=float(data.get("rank_value", 0.0)),
            tie1=float(data.get("tie1", 0.0)),
            tie2=float(data.get("tie2", 0.0)),
            tie3=float(data.get("tie3", 0.0)),
            value_text=str(data.get("value_text", "0")),
            detail=str(data.get("detail", "")),
            date=str(data.get("date", "")),
        )


class ArcadeLeaderboardOverlay:
    """Gemeinsame, vollständig beschießbare Top-10-Oberfläche.

    Die eigentlichen Rangregeln bleiben je Spiel verschieden. Darstellung,
    freiwillige Namenseingabe und PIN-geschütztes Zurücksetzen sind dagegen
    überall identisch mit dem Wasser-Alarm-Ablauf.
    """

    ADMIN_PIN = "1919"
    MAX_NAME_LENGTH = 8
    def __init__(self, screen: pygame.Surface, path: Optional[Path]) -> None:
        self.screen = screen
        self.path = path
        self.entries = self._load()
        self.font_tiny = pygame.font.SysFont("Arial", 14)
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22)
        self.font_large = pygame.font.SysFont("Arial", 34, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 46, bold=True)
        self.background_cache: dict[str, pygame.Surface] = {}
        self.background = self._load_background()
        width, height = screen.get_size()
        self.result_card = pygame.Rect(42, 80, width - 84, height - 192)
        self.name_button = pygame.Rect(42, height - 82, 292, 54)
        self.repeat_button = pygame.Rect(366, height - 82, 292, 54)
        self.menu_button = pygame.Rect(690, height - 82, 292, 54)
        self.admin_button = pygame.Rect(width - 72, height - 151, 28, 28)
        self.save_button = pygame.Rect(34, height - 92, 300, 68)
        self.backspace_button = pygame.Rect(354, height - 92, 316, 68)
        self.clear_button = pygame.Rect(0, 0, 0, 0)
        self.skip_button = pygame.Rect(690, height - 92, 300, 68)
        self.key_buttons, self.name_keyboard_rect = build_name_keyboard_layout(
            screen.get_size()
        )
        self.admin_key_buttons: list[tuple[str, pygame.Rect]] = []
        admin_rows = (
            ("1", "2", "3"),
            ("4", "5", "6"),
            ("7", "8", "9"),
            ("C", "0", "ZURÜCK"),
        )
        for row_index, row in enumerate(admin_rows):
            for column, key in enumerate(row):
                self.admin_key_buttons.append(
                    (key, pygame.Rect(312 + column * 138, 230 + row_index * 78, 124, 64))
                )
        self.admin_confirm_button = pygame.Rect(width // 2 - 210, 564, 420, 62)
        self.admin_cancel_button = pygame.Rect(width // 2 - 145, 652, 290, 50)
        self.clear()

    def _load_background(self) -> pygame.Surface:
        path = (
            Path(__file__).resolve().parents[2]
            / "assets"
            / "arcade_themes"
            / "leaderboard_background_v1.png"
        )
        try:
            image = pygame.image.load(str(path))
            if pygame.display.get_surface() is not None:
                image = image.convert()
            if image.get_size() != self.screen.get_size():
                image = pygame.transform.smoothscale(image, self.screen.get_size())
            neutralize_laser_red(image)
            limit_projected_brightness(image, 148)
            return image
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Bestenlisten-Hintergrund fehlt: %s", exc)
            image = pygame.Surface(self.screen.get_size())
            image.fill(SAFE_DARK)
            return image

    def _background_for_active_game(self) -> pygame.Surface:
        """Liefert für jede Bestenliste die sichtbar passende Spielwelt."""

        game_key = self.active_game or "leaderboard"
        cached = self.background_cache.get(game_key)
        if cached is not None:
            return cached
        theme_map = {
            "cans": "cans",
            "clay": "clay",
            "timed": "timed",
            "reaction": "reaction",
            "range": "range",
            "balloons": "balloons",
            "aliens": "aliens",
            "stars": "stars",
            "math": "math",
            "colors": "colors",
            "treasure": "treasure",
            "chickens": "moorhuhn_game",
            # Tobias' persönliche Fotos bleiben unverändert; für die
            # Bestenliste dient nur die neutrale Reaktionsarena als Kulisse.
            "tobia": "reaction",
        }
        theme = theme_map.get(game_key, "leaderboard")
        image = build_theme_background(self.screen.get_size(), theme).copy()
        neutralize_laser_red(image)
        limit_projected_brightness(image, 168)
        self.background_cache[game_key] = image
        return image

    def clear(self) -> None:
        self.active_game: Optional[str] = None
        self.state = "inactive"
        self.candidate: Optional[ArcadeLeaderboardEntry] = None
        self.game_title = ""
        self.reason = ""
        self.metrics: tuple[tuple[str, str], ...] = ()
        self.player_name = ""
        self.qualifies = False
        self.saved = False
        self.skipped = False
        self.current_rank: Optional[int] = None
        self.admin_digits = ""
        self.admin_message = ""
        self.name_message = ""
        self.result_controls_armed_at = 0.0

    @property
    def active(self) -> bool:
        return self.state != "inactive"

    def is_active_for(self, game_key: str) -> bool:
        return self.active and self.active_game == game_key

    def prepare(self, game_key: str, game, now: Optional[float] = None) -> bool:
        if not bool(getattr(game, "leaderboard_enabled", True)):
            # Zweispieler-Duelle werden ausschließlich direkt ausgewertet.
            # Diese Sperre verhindert auch bei späteren Menüerweiterungen eine
            # versehentliche Top-10- oder Namenseingabe.
            self.clear()
            LOGGER.info("%s: Bestenliste für diesen Spielmodus deaktiviert", game_key)
            return False
        built = self._build_candidate(game_key, game)
        if built is None:
            return False
        candidate, title, reason, metrics = built
        self.active_game = game_key
        self.state = "result"
        self.candidate = candidate
        self.game_title = title
        self.reason = reason
        self.metrics = metrics
        self.player_name = ""
        self.saved = False
        self.skipped = False
        self.admin_message = ""
        self.result_controls_armed_at = 0.0
        ranked = [*self._board_entries(candidate.board), candidate]
        self._sort(ranked)
        rank = next(index for index, entry in enumerate(ranked, start=1) if entry is candidate)
        self.qualifies = rank <= 10
        self.current_rank = rank if self.qualifies else None
        LOGGER.info(
            "%s: Bestenlistenprüfung Wert=%s, Top10=%s, Rang=%s",
            title,
            candidate.value_text,
            self.qualifies,
            self.current_rank,
        )
        return True

    def _build_candidate(self, game_key: str, game):
        date = datetime.now().astimezone().isoformat(timespec="seconds")
        if game_key == "cans":
            value = int(game.score)
            entry = ArcadeLeaderboardEntry(
                "cans", "", value, game.knocked_down, game.accuracy,
                game.best_combo, self._points(value),
                f"{game.knocked_down} DOSEN · {game.accuracy:.0f} %", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("DOSEN", str(game.knocked_down)),
                ("PRÄZISION", f"{game.accuracy:.0f} %"),
                ("BESTE SERIE", str(game.best_combo)),
            )
            return entry, "DOSENSCHIEßEN", game.finish_reason, metrics
        if game_key == "clay":
            value = int(game.score)
            entry = ArcadeLeaderboardEntry(
                "clay", "", value, game.hits, game.accuracy,
                game.best_combo, self._points(value),
                f"{game.hits}/{game.TOTAL_CLAYS} · {game.accuracy:.0f} %", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("TREFFER", f"{game.hits}/{game.TOTAL_CLAYS}"),
                ("PRÄZISION", f"{game.accuracy:.0f} %"),
                ("BESTE SERIE", str(game.best_combo)),
            )
            return entry, "TONTAUBENSCHIEßEN", game.finish_reason, metrics
        if game_key == "timed":
            average = int(game.average_time_ms)
            value = int(game.score)
            speed_tie = -average if game.hits else -999999
            entry = ArcadeLeaderboardEntry(
                "timed", "", value, game.hits, speed_tie,
                game.best_combo, self._points(value),
                f"{game.hits}/{game.TOTAL_TARGETS} · Ø {average} ms", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("TREFFER", f"{game.hits}/{game.TOTAL_TARGETS}"),
                ("Ø REAKTION", f"{average} ms"),
                ("PRÄZISION", f"{game.accuracy:.0f} %"),
            )
            return entry, "ZEITSCHIEßEN", game.finish_reason, metrics
        if game_key == "reaction":
            average = int(game.average_ms)
            value = int(game.score)
            speed_tie = -average if game.hits else -999999
            entry = ArcadeLeaderboardEntry(
                "reaction", "", value, game.hits, speed_tie,
                -game.false_starts, self._points(value),
                f"Ø {average} ms · {game.false_starts} FRÜHSTARTS", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("TREFFER", f"{game.hits}/{game.ROUNDS}"),
                ("Ø REAKTION", f"{average} ms"),
                ("FRÜHSTARTS", str(game.false_starts)),
            )
            return entry, "REAKTION", game.finish_reason, metrics
        if game_key == "tobia":
            value = int(game.score)
            entry = ArcadeLeaderboardEntry(
                "tobia", "", value, game.rabbit_hits, -game.person_hits,
                game.accuracy, self._points(value),
                f"{game.rabbit_hits} KANINCHEN · {game.person_hits} PERSONEN", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("KANINCHEN", str(game.rabbit_hits)),
                ("PERSONEN", str(game.person_hits)),
                ("PRÄZISION", f"{game.accuracy:.0f} %"),
            )
            return entry, "TOBIAS BLITZDUELL", game.finish_reason, metrics
        if game_key == "chickens":
            value = int(game.score)
            entry = ArcadeLeaderboardEntry(
                "chickens", "", value, game.hits, game.accuracy,
                0.0, self._points(value),
                f"{game.hits}/{game.shots} TREFFER · {game.accuracy:.0f} %", date,
            )
            metrics = (
                ("PUNKTE", self._points(value)),
                ("TREFFER", f"{game.hits}/{game.shots}"),
                ("PRÄZISION", f"{game.accuracy:.0f} %"),
                ("BESTWERT", str(game.best_score)),
            )
            return entry, "MOORHUHN", game.finish_reason, metrics
        kids_titles = {
            "balloons": "BALLONJAGD",
            "aliens": "ALIEN-ALARM",
            "stars": "STERNEJAGD",
            "math": "RECHENDUELL",
            "colors": "FARBENSPIEL",
            "treasure": "SCHATZSUCHE",
        }
        if game_key in kids_titles:
            value = int(game.score)
            entry = ArcadeLeaderboardEntry(
                game_key,
                "",
                value,
                float(game.hits),
                float(game.accuracy),
                float(game.best_combo),
                self._points(value),
                game.leaderboard_detail,
                date,
            )
            return (
                entry,
                kids_titles[game_key],
                game.finish_reason,
                tuple(game.leaderboard_metrics),
            )
        if game_key == "range" and game.current_result is not None:
            result = game.current_result
            mode_label = game.MODE_LABELS[result.mode]
            board = f"range:{result.mode}:{result.shot_count}"
            rank_value = -result.result_value if result.mode == "divider" else result.result_value
            entry = ArcadeLeaderboardEntry(
                board, "", rank_value, 0.0, 0.0, 0.0,
                result.display, f"{result.shot_count} SCHÜSSE · {mode_label}", date,
            )
            metrics = (
                ("ERGEBNIS", result.display),
                ("MODUS", mode_label),
                ("SCHÜSSE", str(result.shot_count)),
            )
            title = f"ZIELSCHEIBE · {mode_label}"
            return entry, title, "Serie abgeschlossen", metrics
        return None

    @staticmethod
    def _points(value: int) -> str:
        return f"{value:,}".replace(",", ".")

    @staticmethod
    def _sort(entries: list[ArcadeLeaderboardEntry]) -> None:
        entries.sort(
            key=lambda entry: (
                -entry.rank_value,
                -entry.tie1,
                -entry.tie2,
                -entry.tie3,
                entry.date,
            )
        )

    def _board_entries(self, board: str) -> list[ArcadeLeaderboardEntry]:
        entries = [entry for entry in self.entries if entry.board == board]
        self._sort(entries)
        return entries[:10]

    def handle_shot(self, pos: Tuple[int, int], now: Optional[float] = None) -> str:
        if not self.active or self.candidate is None:
            return "ignored"
        current = now if now is not None else time.monotonic()
        if self.state == "name_entry":
            selected = nearest_laser_button(
                pos,
                (("save", self.save_button), ("back", self.backspace_button),
                 ("skip", self.skip_button)),
                expansion=(56, 40),
            )
            if selected == "save":
                return self._save_name(current)
            if selected == "back":
                self.player_name = self.player_name[:-1]
                self.name_message = ""
                return "leaderboard"
            if selected == "skip":
                self.state = "result"
                self.player_name = ""
                self.name_message = ""
                self.result_controls_armed_at = current + 0.35
                return "leaderboard"
            selected = nearest_laser_button(
                pos,
                self.key_buttons,
                expansion=(38, 28),
                group_rect=self.name_keyboard_rect,
            )
            if isinstance(selected, str) and selected in LETTERS:
                return self._append_name(selected)
            return "ignored"
        if self.state == "admin":
            controls = [(key, rect) for key, rect in self.admin_key_buttons]
            controls.extend(
                (("confirm", self.admin_confirm_button), ("cancel", self.admin_cancel_button))
            )
            selected = nearest_laser_button(pos, controls, expansion=(24, 20))
            if selected in {"cancel", "ZURÜCK"}:
                self.state = "result"
                self.admin_digits = ""
                return "leaderboard"
            if selected == "C":
                self.admin_digits = ""
                return "leaderboard"
            if selected == "confirm":
                return self._confirm_admin()
            if isinstance(selected, str) and selected.isdigit() and len(self.admin_digits) < 4:
                self.admin_digits += selected
                return "leaderboard"
            return "ignored"

        awaiting_name = self.qualifies and not (self.saved or self.skipped)
        controls = [("repeat", self.repeat_button), ("menu", self.menu_button)]
        if awaiting_name:
            controls.insert(0, ("name", self.name_button))
        if current < self.result_controls_armed_at:
            return "ignored"
        selected = nearest_laser_button(
            pos,
            controls,
            expansion=LASER_RESULT_EXPANSION,
        )
        if self.admin_button.inflate(28, 28).collidepoint(pos):
            self.state = "admin"
            self.admin_digits = ""
            self.admin_message = ""
            return "leaderboard"
        if selected == "name":
            self.state = "name_entry"
            self.player_name = ""
            self.name_message = ""
            return "leaderboard"
        if selected == "repeat":
            return "repeat"
        if selected == "menu":
            return "menu"
        return "ignored"

    def _append_name(self, character: str) -> str:
        character = character.upper()
        if character not in LETTERS:
            return "ignored"
        if len(self.player_name) >= self.MAX_NAME_LENGTH:
            self.name_message = "DER NAME HAT SCHON 8 BUCHSTABEN"
            return "full"
        self.player_name += character
        self.name_message = ""
        return "leaderboard"

    def handle_key(self, event: pygame.event.Event, now: Optional[float] = None) -> str:
        """Bedient Namen und Verwaltungs-PIN auch mit einer Tastatur."""

        if not self.active or event.type != pygame.KEYDOWN:
            return "ignored"
        current = now if now is not None else time.monotonic()
        if self.state == "name_entry":
            if event.key == pygame.K_BACKSPACE:
                self.player_name = self.player_name[:-1]
                self.name_message = ""
                return "leaderboard"
            if event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
                return self._save_name(current)
            character = (getattr(event, "unicode", "") or "").upper()
            return self._append_name(character) if character in LETTERS else "ignored"
        if self.state == "admin":
            if event.key == pygame.K_BACKSPACE:
                self.admin_digits = self.admin_digits[:-1]
                return "leaderboard"
            if event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
                return self._confirm_admin()
            character = getattr(event, "unicode", "") or ""
            if character.isdigit() and len(self.admin_digits) < 4:
                self.admin_digits += character
                return "leaderboard"
        return "ignored"

    def _save_name(self, now: Optional[float] = None) -> str:
        if not self.player_name or self.candidate is None:
            self.name_message = "BITTE ERST EINEN BUCHSTABEN AUSWÄHLEN"
            return "name_required"
        self.candidate.name = self.player_name
        self.entries.append(self.candidate)
        board = self.candidate.board
        board_entries = self._board_entries(board)
        retained = [entry for entry in self.entries if entry.board != board]
        self.entries = retained + board_entries
        self.current_rank = next(
            (index for index, entry in enumerate(board_entries, start=1) if entry is self.candidate),
            None,
        )
        self.saved = self.current_rank is not None
        self.state = "result"
        current = now if now is not None else time.monotonic()
        self.result_controls_armed_at = current + 0.55
        self._save()
        return "saved"

    def _skip(self, now: Optional[float] = None) -> str:
        self.skipped = True
        self.saved = False
        self.current_rank = None
        self.player_name = ""
        self.state = "result"
        current = now if now is not None else time.monotonic()
        self.result_controls_armed_at = current + 0.55
        return "skipped"

    def _confirm_admin(self) -> str:
        if self.admin_digits != self.ADMIN_PIN or self.candidate is None:
            self.admin_digits = ""
            self.admin_message = "PIN FALSCH"
            return "admin_denied"
        board = self.candidate.board
        self.entries = [entry for entry in self.entries if entry.board != board]
        self._save()
        self.state = "result"
        self.admin_digits = ""
        self.admin_message = "BESTENLISTE GELÖSCHT"
        self.qualifies = True
        self.current_rank = 1
        return "admin_reset"

    def draw(self) -> None:
        if not self.active or self.candidate is None:
            return
        if self.state == "name_entry":
            self._draw_name_entry()
        elif self.state == "admin":
            self._draw_admin()
        else:
            self._draw_result()

    def draw_ready_preview(self, game_key: str) -> None:
        """Zeigt vor Spielbeginn die eigene Liste ohne den Startdialog zu verdecken."""

        board = {
            "cans": "cans",
            "clay": "clay",
            "timed": "timed",
            "reaction": "reaction",
            "balloons": "balloons",
            "aliens": "aliens",
            "stars": "stars",
            "math": "math",
            "colors": "colors",
            "treasure": "treasure",
            "tobia": "tobia",
            "chickens": "chickens",
        }.get(game_key)
        if board is None:
            return
        if game_key == "tobia":
            preview_left = self.screen.get_width() - 264
        else:
            preview_left = 18
        rect = pygame.Rect(preview_left, 176, 152, 404)
        draw_vintage_enamel_panel(
            self.screen, rect, self._visual_variant(game_key), alpha=32, shadow=False
        )
        heading = self.font_small.render("TOP 10", True, SAFE_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(rect.centerx, rect.top + 12)))
        entries = self._board_entries(board)
        if not entries:
            empty = self.font_tiny.render("NOCH KEIN", True, SAFE_MUTED)
            empty2 = self.font_tiny.render("ERGEBNIS", True, SAFE_MUTED)
            self.screen.blit(empty, empty.get_rect(center=(rect.centerx, rect.centery - 10)))
            self.screen.blit(empty2, empty2.get_rect(center=(rect.centerx, rect.centery + 10)))
            return
        for index, entry in enumerate(entries[:10], start=1):
            y = rect.top + 50 + (index - 1) * 33
            rank = self.font_tiny.render(f"{index}.", True, SAFE_GREEN if index <= 3 else SAFE_MUTED)
            name = self.font_tiny.render(entry.name[:6], True, WHITE)
            value = self.font_tiny.render(entry.value_text, True, SAFE_CYAN)
            self.screen.blit(rank, (rect.left + 8, y))
            self.screen.blit(name, (rect.left + 29, y))
            self.screen.blit(value, value.get_rect(topright=(rect.right - 7, y)))

    def _veil(self) -> None:
        self.screen.blit(self._background_for_active_game(), (0, 0))
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 8, 16, 8))
        self.screen.blit(veil, (0, 0))

    def _visual_variant(self, game_key: Optional[str] = None) -> int:
        keys = (
            "cans", "clay", "timed", "reaction", "range", "balloons",
            "aliens", "stars", "math", "colors", "treasure", "tobia", "chickens",
        )
        active = game_key or self.active_game or "cans"
        try:
            return keys.index(active) % 6
        except ValueError:
            return 0

    def _panel(self, rect: pygame.Rect, color=SAFE_CYAN) -> None:
        draw_vintage_enamel_panel(
            self.screen, rect, self._visual_variant(), alpha=18, shadow=False
        )
        pygame.draw.rect(self.screen, color, rect, 2, border_radius=14)

    def _draw_result(self) -> None:
        self._veil()
        self._panel(self.result_card, SAFE_GREEN if self.saved else SAFE_CYAN)
        title = self.font_large.render(self.game_title, True, SAFE_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 18)))
        if self.admin_message:
            status_text = self.admin_message
        elif self.saved:
            status_text = f"{self.player_name} · RANG {self.current_rank} GESPEICHERT"
        elif self.skipped:
            status_text = "ERGEBNIS NICHT EINGETRAGEN"
        elif self.qualifies:
            status_text = f"TOP-10-ERGEBNIS · RANG {self.current_rank}"
        else:
            status_text = self.reason.upper()
        status = self.font_small.render(status_text, True, SAFE_GREEN)
        self.screen.blit(status, status.get_rect(midtop=(self.result_card.centerx, self.result_card.top + 62)))

        metric_top = self.result_card.top + 94
        metric_width = (self.result_card.width - 56) // max(1, len(self.metrics))
        for index, (label, value) in enumerate(self.metrics):
            center_x = self.result_card.left + 28 + metric_width * index + metric_width // 2
            label_surface = self.font_tiny.render(label, True, SAFE_MUTED)
            value_surface = self.font.render(value, True, SAFE_GREEN if index == 0 else SAFE_CYAN)
            self.screen.blit(label_surface, label_surface.get_rect(center=(center_x, metric_top + 12)))
            self.screen.blit(value_surface, value_surface.get_rect(center=(center_x, metric_top + 42)))

        table = pygame.Rect(
            self.result_card.left + 40,
            self.result_card.top + 164,
            self.result_card.width - 80,
            self.result_card.height - 210,
        )
        self._draw_table(table, compact=True)
        pygame.draw.rect(self.screen, SAFE_MUTED, self.admin_button, 1, border_radius=5)
        for offset in (-6, 0, 6):
            pygame.draw.circle(
                self.screen,
                SAFE_MUTED,
                (self.admin_button.centerx + offset, self.admin_button.centery),
                2,
            )
        awaiting_name = self.qualifies and not (self.saved or self.skipped)
        if awaiting_name:
            draw_button(
                self.screen,
                self.name_button,
                "NAMEN EINTRAGEN",
                self.font,
                SAFE_GREEN,
            )
        draw_button(
            self.screen,
            self.repeat_button,
            "NOCH EINMAL",
            self.font,
            SAFE_GREEN,
        )
        draw_button(
            self.screen,
            self.menu_button,
            "MENÜ",
            self.font,
            SAFE_CYAN,
        )

    def _draw_name_entry(self) -> None:
        self._veil()
        header_sign = pygame.Rect(326, 12, 372, 72)
        draw_vintage_enamel_panel(
            self.screen, header_sign, self._visual_variant(), alpha=22, shadow=False
        )
        rank = self.current_rank or 10
        title = self.font_large.render(f"TOP 10 · RANG {rank}", True, SAFE_GREEN)
        self.screen.blit(title, title.get_rect(midtop=(self.screen.get_width() // 2, 18)))
        value = self.font.render(self.candidate.value_text, True, WHITE)
        self.screen.blit(value, value.get_rect(midtop=(self.screen.get_width() // 2, 60)))
        entry_card = pygame.Rect(34, 96, self.screen.get_width() - 68, 180)
        self._panel(entry_card)
        label = self.font_small.render("DEIN NAME · BIS ZU 8 BUCHSTABEN", True, SAFE_CYAN)
        self.screen.blit(label, label.get_rect(midtop=(entry_card.centerx, entry_card.top + 14)))
        shown = self.player_name or "_ _ _ _ _ _ _ _"
        name_surface = self.font_large.render(shown, True, WHITE if self.player_name else SAFE_MUTED)
        name_box = pygame.Rect(entry_card.left + 24, entry_card.top + 46, entry_card.width - 48, 72)
        draw_translucent_panel(
            self.screen, name_box, SAFE_DARK, alpha=108, border_radius=10
        )
        pygame.draw.rect(self.screen, SAFE_GREEN, name_box, 2, border_radius=10)
        self.screen.blit(name_surface, name_surface.get_rect(center=name_box.center))
        hint_text = self.name_message or "BUCHSTABEN NACHEINANDER TREFFEN · DANACH NAME SPEICHERN"
        hint_color = SAFE_CYAN if self.name_message else SAFE_GREEN
        hint = self.font_small.render(hint_text, True, hint_color)
        self.screen.blit(hint, hint.get_rect(midtop=(entry_card.centerx, entry_card.top + 137)))
        for key, rect in self.key_buttons:
            draw_vintage_enamel_panel(
                self.screen,
                rect,
                (self._visual_variant() + ord(key[0])) % 6,
                alpha=224,
                shadow=False,
            )
            text = self.font_large.render(key, True, SAFE_CYAN)
            self.screen.blit(text, text.get_rect(center=rect.center))
        draw_button(self.screen, self.save_button, "NAME SPEICHERN", self.font, SAFE_GREEN)
        draw_button(self.screen, self.backspace_button, "1 BUCHSTABE LÖSCHEN", self.font_small, SAFE_CYAN)
        draw_button(self.screen, self.skip_button, "ZURÜCK ZUM ERGEBNIS", self.font_small, SAFE_CYAN)

    def _draw_table(self, rect: pygame.Rect, *, compact: bool) -> None:
        draw_vintage_enamel_panel(
            self.screen,
            rect,
            (self._visual_variant() + 2) % 6,
            alpha=12,
            shadow=False,
        )
        pygame.draw.rect(self.screen, SAFE_MUTED, rect, 2, border_radius=14)
        heading = self.font.render("TOP 10", True, SAFE_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(rect.centerx, rect.top + 10)))
        entries = self._board_entries(self.candidate.board) if self.candidate else []
        if not entries:
            empty = self.font_small.render("NOCH KEIN ERGEBNIS", True, SAFE_MUTED)
            self.screen.blit(empty, empty.get_rect(center=rect.center))
            return
        line_gap = min(31 if compact else 43, max(24, (rect.height - 60) // 10))
        for index, entry in enumerate(entries[:10], start=1):
            y = rect.top + 50 + (index - 1) * line_gap
            color = SAFE_GREEN if self.saved and entry is self.candidate else WHITE
            rank = self.font_small.render(f"{index:>2}.", True, SAFE_GREEN if index <= 3 else SAFE_MUTED)
            name = self.font_small.render(entry.name, True, color)
            value = self.font_small.render(entry.value_text, True, SAFE_CYAN)
            self.screen.blit(rank, (rect.left + 14, y))
            self.screen.blit(name, (rect.left + 50, y))
            self.screen.blit(value, value.get_rect(topright=(rect.right - 14, y)))

    def _draw_admin(self) -> None:
        self._veil()
        card = pygame.Rect(215, 74, 594, 642)
        self._panel(card)
        title = self.font_large.render("BESTENLISTE VERWALTEN", True, WHITE)
        self.screen.blit(title, title.get_rect(midtop=(card.centerx, card.top + 28)))
        subtitle = self.font_small.render("ADMIN-PIN EINGEBEN", True, SAFE_MUTED)
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(card.centerx, card.top + 82)))
        pin_text = "● " * len(self.admin_digits) + "○ " * (4 - len(self.admin_digits))
        pin = self.font_large.render(pin_text.strip(), True, SAFE_GREEN)
        self.screen.blit(pin, pin.get_rect(midtop=(card.centerx, card.top + 116)))
        if self.admin_message:
            message = self.font_small.render(self.admin_message, True, SAFE_MUTED)
            self.screen.blit(message, message.get_rect(midtop=(card.centerx, card.top + 163)))
        for key, rect in self.admin_key_buttons:
            draw_button(
                self.screen,
                rect,
                key,
                self.font_small if len(key) > 1 else self.font,
                SAFE_CYAN,
                aim=False,
            )
        draw_button(self.screen, self.admin_confirm_button, "BESTENLISTE ZURÜCKSETZEN", self.font, SAFE_GREEN)
        draw_button(self.screen, self.admin_cancel_button, "ABBRECHEN", self.font_small, SAFE_CYAN)

    def _load(self) -> list[ArcadeLeaderboardEntry]:
        if self.path is None or not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return [ArcadeLeaderboardEntry.from_dict(item) for item in raw]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.warning("Arcade-Bestenlisten konnten nicht geladen werden: %s", exc)
            return []

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps([asdict(entry) for entry in self.entries], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            LOGGER.warning("Arcade-Bestenlisten konnten nicht gespeichert werden: %s", exc)
