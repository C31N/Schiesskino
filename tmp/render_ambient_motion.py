from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from laser_arcade.apps.chickens import ChickenApp
from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI


OUTPUT = Path(os.environ.get("AMBIENT_RENDER_DIR", "/tmp/schiesskino-ambient-motion"))


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def reset_state(self) -> None:
        return None

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        return None


def advance_to_playing(game, now: float = 100.0) -> None:
    game.start(now)
    begin = getattr(game, "begin_countdown", None)
    if begin is not None:
        begin(now)
        game.update(now + 4.0)
        game.update(now + 4.4)
        game.update(now + 5.2)


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

    games = [
        ("WASSER", ui.water_alarm_game),
        ("DOSEN", ui.cans_game),
        ("ZEIT", ui.timed_game),
        ("TONT.", ui.clay_game),
        ("REAKTION", ui.reaction_game),
        ("ZIEL", ui.target_range_game),
        ("BALLONS", ui.balloon_game),
        ("ALIENS", ui.alien_game),
        ("STERNE", ui.star_game),
        ("MATHE", ui.math_game),
        ("FARBEN", ui.color_game),
        ("SCHATZ", ui.treasure_game),
        ("MEER", ui.ocean_cleanup_game),
        ("FOTO", ui.tobia_duel_game),
    ]
    chicken = ChickenApp(screen, audio_enabled=False, persist_scores=False, random_seed=1919)
    games.append(("MOORHUHN", chicken))

    pairs: list[tuple[str, pygame.Surface, pygame.Surface, int, float]] = []
    for label, game in games:
        if game is ui.target_range_game:
            game.start(100.0)
        else:
            advance_to_playing(game)
        game.draw(106.0)
        first = screen.copy()
        first_rgb = pygame.surfarray.array3d(first).copy()
        game.draw(108.2)
        second = screen.copy()
        if label == "SCHATZ":
            pygame.image.save(second, OUTPUT / "schatz-vollbild.png")
        second_rgb = pygame.surfarray.array3d(second).copy()
        changed = int(np.any(first_rgb != second_rgb, axis=2).sum())

        started = time.perf_counter()
        frame_count = 18
        for index in range(frame_count):
            game.draw(110.0 + index / 30.0)
        elapsed_ms = (time.perf_counter() - started) * 1000.0 / frame_count
        pairs.append((label, first, second, changed, elapsed_ms))
        print(f"{label:10s} Änderung={changed:7d} Pixel  Render={elapsed_ms:6.2f} ms")

    font = pygame.font.SysFont("Arial", 18, bold=True)
    tiny = pygame.font.SysFont("Arial", 14)
    for group_index in range(3):
        group = pairs[group_index * 5:(group_index + 1) * 5]
        montage = pygame.Surface((1920, 620))
        montage.fill((0, 5, 13))
        for column, (label, first, second, changed, elapsed_ms) in enumerate(group):
            for row, frame in enumerate((first, second)):
                thumb = pygame.transform.smoothscale(frame, (384, 288))
                montage.blit(thumb, (column * 384, 22 + row * 299))
            heading = font.render(label, True, (0, 205, 245))
            details = tiny.render(f"{changed} Pixel · {elapsed_ms:.1f} ms", True, (0, 225, 120))
            montage.blit(heading, (column * 384 + 8, 1))
            montage.blit(details, (column * 384 + 150, 4))
        pygame.image.save(montage, OUTPUT / f"bewegung-{group_index + 1}.png")

    ui.cans_game.sounds.stop_all()
    chicken.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
