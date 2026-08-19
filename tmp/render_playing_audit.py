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


OUTPUT = Path("/tmp/schiesskino-playing-audit")


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def reset_state(self) -> None:
        return None

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        return None


def advance_to_playing(game, now: float = 100.0) -> float:
    game.start(now)
    begin = getattr(game, "begin_countdown", None)
    if begin is not None:
        begin(now)
        for current in (now + 4.0, now + 4.4, now + 5.2):
            game.update(current)
        now += 5.2
    elif hasattr(game, "_begin"):
        game._begin(now)
        now += 0.8
        game.update(now)
    for _ in range(24):
        now += 1.0 / 30.0
        game.update(now)
    return now


def main() -> None:
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

    games = [
        ("Wasser-Alarm", ui.water_alarm_game),
        ("Dosenschießen", ui.cans_game),
        ("Zeitschießen", ui.timed_game),
        ("Tontaubenschießen", ui.clay_game),
        ("Reaktion", ui.reaction_game),
        ("Zielscheibe", ui.target_range_game),
        ("Ballonjagd", ui.balloon_game),
        ("Alien-Alarm", ui.alien_game),
        ("Sternejagd", ui.star_game),
        ("Rechenduell", ui.math_game),
        ("Farbenspiel", ui.color_game),
        ("Schatzsuche", ui.treasure_game),
        ("Annas Meeresmission", ui.ocean_cleanup_game),
        ("Tobias Blitzduell", ui.tobia_duel_game),
        ("Tic-Tac-Toe", ui.tic_tac_toe_game),
        ("4 Gewinnt", ui.connect_four_game),
        ("Käsekästchen", ui.dots_boxes_game),
        ("Memory-Duell", ui.memory_duel_game),
        ("Nim-Duell", ui.nim_duel_game),
        ("Reversi Light", ui.reversi_light_game),
    ]
    chicken = ChickenApp(screen, audio_enabled=False, random_seed=1919)
    games.append(("Moorhuhn", chicken))

    OUTPUT.mkdir(parents=True, exist_ok=True)
    font = pygame.font.SysFont("Arial", 21, bold=True)
    rendered: list[tuple[str, pygame.Surface]] = []
    for index, (label, game) in enumerate(games, start=1):
        now = advance_to_playing(game)
        # Bewegte Spiele benötigen einen kurzen stabilen Ausschnitt nach dem
        # Start. So zeigt das Audit echte Ziele statt einer Spawn- oder
        # Panoramaübergangsphase.
        for _ in range(90):
            now += 1.0 / 30.0
            game.update(now)
        game.draw(now)
        image = screen.copy()
        pygame.image.save(image, OUTPUT / f"playing-{index:02d}.png")
        rendered.append((label, image))

    tile_size = (512, 384)
    label_height = 34
    for page in range(4):
        montage = pygame.Surface((1536, 2 * (tile_size[1] + label_height)))
        montage.fill((0, 8, 20))
        for slot, (label, image) in enumerate(rendered[page * 6 : page * 6 + 6]):
            column, row = slot % 3, slot // 3
            label_surface = font.render(
                f"{page * 6 + slot + 1:02d} · {label}",
                True,
                (225, 250, 255),
            )
            montage.blit(
                label_surface,
                (column * tile_size[0] + 10, row * (tile_size[1] + label_height) + 5),
            )
            tile = pygame.transform.smoothscale(image, tile_size)
            montage.blit(
                tile,
                (column * tile_size[0], row * (tile_size[1] + label_height) + label_height),
            )
        pygame.image.save(montage, OUTPUT / f"playing-montage-{page + 1}.png")

    ui.cans_game.sounds.stop_all()
    chicken.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
