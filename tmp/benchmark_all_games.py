from __future__ import annotations

import os
import statistics
import time
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from laser_arcade.apps.chickens import ChickenApp
from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI


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
        for current in (now + 4.0, now + 4.4, now + 5.2):
            game.update(current)
    elif hasattr(game, "_begin"):
        game._begin(now)


def measure(name: str, game, frames: int = 180) -> tuple[str, float, float, float]:
    now = 106.0
    # Das Aufwärmen füllt alle skalierungs- und spritebezogenen Caches.
    for _ in range(20):
        now += 1.0 / 60.0
        game.update(now)
        game.draw(now)

    samples: list[float] = []
    started = time.perf_counter()
    for _ in range(frames):
        now += 1.0 / 60.0
        before = time.perf_counter()
        game.update(now)
        game.draw(now)
        samples.append((time.perf_counter() - before) * 1000.0)
    elapsed = time.perf_counter() - started
    fps = frames / elapsed
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95))]
    return name, fps, statistics.mean(samples), p95


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

    games = (
        ("Wasser-Alarm", ui.water_alarm_game),
        ("Dosenschießen", ui.cans_game),
        ("Zeitschießen", ui.timed_game),
        ("Tontauben", ui.clay_game),
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
    )

    selected = os.environ.get("BENCHMARK_GAME", "").strip().casefold()
    results: list[tuple[str, float, float, float]] = []
    for name, game in games:
        if selected and selected not in name.casefold():
            continue
        advance_to_playing(game)
        results.append(measure(name, game))

    chicken = None
    if not selected or selected in "moorhuhn":
        chicken = ChickenApp(screen, audio_enabled=False, random_seed=1919)
        advance_to_playing(chicken)
        results.append(measure("Moorhuhn", chicken))

    print("SPIEL\tFPS\tMITTEL_MS\tP95_MS")
    for name, fps, mean_ms, p95_ms in results:
        print(f"{name}\t{fps:.1f}\t{mean_ms:.2f}\t{p95_ms:.2f}")
    slowest = min(results, key=lambda row: row[1])
    print(f"LANGSAMSTES\t{slowest[0]}\t{slowest[1]:.1f} FPS")
    if slowest[1] < 30.0 or slowest[3] > 33.34:
        raise SystemExit(1)

    ui.cans_game.sounds.stop_all()
    if chicken is not None:
        chicken.stop()
    pygame.quit()


if __name__ == "__main__":
    main()
