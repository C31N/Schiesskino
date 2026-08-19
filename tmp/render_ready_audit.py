"""Rendert alle Startansichten in voller Projektorauflösung zur Sichtprüfung."""

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


OUTPUT = Path(os.environ.get("READY_AUDIT_DIR", "/tmp/schiesskino-ready-audit"))


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def reset_state(self) -> None:
        return None

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        return None


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

    games = (
        ("water", ui.water_alarm_game),
        ("cans", ui.cans_game),
        ("timed", ui.timed_game),
        ("clay", ui.clay_game),
        ("reaction", ui.reaction_game),
        ("range", ui.target_range_game),
        ("balloons", ui.balloon_game),
        ("aliens", ui.alien_game),
        ("stars", ui.star_game),
        ("math", ui.math_game),
        ("colors", ui.color_game),
        ("treasure", ui.treasure_game),
        ("tictactoe", ui.tic_tac_toe_game),
        ("connect4", ui.connect_four_game),
        ("dots", ui.dots_boxes_game),
        ("memory_duel", ui.memory_duel_game),
        ("nim", ui.nim_duel_game),
        ("reversi", ui.reversi_light_game),
        ("ocean", ui.ocean_cleanup_game),
        ("tobia", ui.tobia_duel_game),
        ("chickens", chicken),
    )
    preview_keys = {
        "cans", "timed", "clay", "reaction", "balloons", "aliens",
        "stars", "math", "colors", "treasure", "tobia", "chickens",
    }
    frames: list[tuple[str, pygame.Surface]] = []
    for key, game in games:
        ui.arcade_leaderboard.clear()
        game.start(100.0)
        game.draw(100.0)
        if key in preview_keys:
            ui.arcade_leaderboard.draw_ready_preview(key)
        frame = screen.copy()
        frames.append((key, frame))
        pygame.image.save(frame, OUTPUT / f"ready-{key}.png")

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
        pygame.image.save(montage, OUTPUT / f"ready-montage-{group_index + 1}.png")

    ui.cans_game.sounds.stop_all()
    chicken.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
