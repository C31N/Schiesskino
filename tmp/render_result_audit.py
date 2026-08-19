"""Rendert die tatsächlich sichtbaren Ergebnisansichten aller Spiele."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from laser_arcade.apps.chickens import ChickenApp
from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI


OUTPUT = Path(os.environ.get("RESULT_AUDIT_DIR", "/tmp/schiesskino-result-audit"))


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def reset_state(self) -> None:
        return None

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        return None


def prepare_standard_result(key: str, game) -> None:
    game.start(100.0)
    game.state = "game_over"
    if hasattr(game, "finish_reason"):
        game.finish_reason = "SPIEL BEENDET"
    if hasattr(game, "score"):
        game.score = 1250
    if hasattr(game, "shots"):
        game.shots = 15
    if hasattr(game, "hits"):
        game.hits = 12
    if hasattr(game, "best_combo"):
        game.best_combo = 5
    if key == "cans":
        game.knocked_down = 12
    elif key == "clay":
        game.launched = game.TOTAL_CLAYS
    elif key == "timed":
        game.reaction_times = [0.42] * 12
    elif key == "reaction":
        game.completed = game.ROUNDS
        game.false_starts = 1
        game.reaction_times = [0.39] * 12
    elif key == "ocean":
        game.trash_collected = 12
        game.cat_cans_collected = 4
        game.animal_hits = 1
    elif key == "tobia":
        game.rabbit_hits = 12
        game.person_hits = 1
    game.draw(200.0)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pygame.init()
    pygame.display.set_mode((1024, 768))
    screen = pygame.Surface((1024, 768))
    ui = LaserDiagnosticUI(
        screen,
        Settings(),
        DummyTracker(),
        weapon_calibration_path=None,
        target_history_path=None,
        water_alarm_leaderboard_path=None,
        arcade_leaderboard_path=None,
    )
    ui.aligner.phase = "success"
    ui.aligner.homography = np.eye(3, dtype=np.float32)
    ui.aligner.verification = SimpleNamespace(max_error=3.0)
    chicken = ChickenApp(screen, audio_enabled=False, random_seed=1919)

    frames: list[tuple[str, pygame.Surface]] = []

    water = ui.water_alarm_game
    water.start(100.0)
    water.score = 1750
    water.shots = 18
    water.hits = 15
    water.best_combo = 6
    water._finish(200.0)
    water.draw(200.0)
    frames.append(("water", screen.copy()))

    standard_games = (
        ("cans", ui.cans_game),
        ("timed", ui.timed_game),
        ("clay", ui.clay_game),
        ("reaction", ui.reaction_game),
        ("balloons", ui.balloon_game),
        ("aliens", ui.alien_game),
        ("stars", ui.star_game),
        ("math", ui.math_game),
        ("colors", ui.color_game),
        ("treasure", ui.treasure_game),
        ("tobia", ui.tobia_duel_game),
    )
    for key, game in standard_games:
        ui.arcade_leaderboard.clear()
        prepare_standard_result(key, game)
        if not ui.arcade_leaderboard.prepare(key, game):
            raise RuntimeError(f"Keine Ergebnis-Bestenliste für {key}")
        ui.arcade_leaderboard.draw()
        frames.append((key, screen.copy()))

    ocean = ui.ocean_cleanup_game
    ui.arcade_leaderboard.clear()
    prepare_standard_result("ocean", ocean)
    if ui.arcade_leaderboard.prepare("ocean", ocean):
        raise RuntimeError("Annas Meeresmission darf keine Bestenliste öffnen")
    frames.append(("ocean", screen.copy()))

    target = ui.target_range_game
    target.start(100.0)
    target.current_result = SimpleNamespace(
        mode="decimal", shot_count=5, result_value=51.8, display="51,8 RINGE"
    )
    target.state = "result"
    target.result_until = 999.0
    target.draw(200.0)
    ui.arcade_leaderboard.clear()
    ui.arcade_leaderboard.prepare("range", target)
    ui.arcade_leaderboard.draw()
    frames.append(("range", screen.copy()))

    chicken.start(100.0)
    chicken.state = "game_over"
    chicken.score = 1919
    chicken.hits = 14
    chicken.shots = 20
    chicken.best_score = 2200
    chicken.finish_reason = "DIE ZEIT IST ABGELAUFEN"
    chicken.draw(200.0)
    ui.arcade_leaderboard.clear()
    ui.arcade_leaderboard.prepare("chickens", chicken)
    ui.arcade_leaderboard.draw()
    frames.append(("chickens", screen.copy()))

    duel_games = (
        ("tictactoe", ui.tic_tac_toe_game),
        ("connect4", ui.connect_four_game),
        ("dots", ui.dots_boxes_game),
        ("memory_duel", ui.memory_duel_game),
        ("nim", ui.nim_duel_game),
        ("reversi", ui.reversi_light_game),
    )
    for key, game in duel_games:
        game.start(100.0)
        game._begin(101.0)
        game._finish(0, "SPIELER 1 GEWINNT", 200.0)
        game.draw(201.0)
        frames.append((key, screen.copy()))

    for key, frame in frames:
        pygame.image.save(frame, OUTPUT / f"result-{key}.png")

    font = pygame.font.SysFont("Arial", 22, bold=True)
    for group_index in range(4):
        group = frames[group_index * 6:(group_index + 1) * 6]
        montage = pygame.Surface((1536, 768))
        montage.fill((0, 5, 14))
        for index, (key, frame) in enumerate(group):
            thumb = pygame.transform.smoothscale(frame, (512, 384))
            x = (index % 3) * 512
            y = (index // 3) * 384
            montage.blit(thumb, (x, y))
            label = font.render(key.upper(), True, (0, 238, 180))
            backing = pygame.Surface((label.get_width() + 18, 34), pygame.SRCALPHA)
            backing.fill((0, 8, 20, 220))
            montage.blit(backing, (x + 6, y + 6))
            montage.blit(label, (x + 15, y + 9))
        pygame.image.save(montage, OUTPUT / f"result-montage-{group_index + 1}.png")

    ui.cans_game.sounds.stop_all()
    chicken.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
