from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.cans import CansApp
from laser_arcade.apps.arcade_leaderboard import ArcadeLeaderboardOverlay
from laser_arcade.apps.clay_shooting import ClayShootingApp
from laser_arcade.apps.reaction import ReactionApp
from laser_arcade.apps.target_range import TargetRangeApp
from laser_arcade.apps.timed_shooting import TimedShootingApp
from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI


OUTPUT = Path(os.environ.get("THEME_RENDER_DIR", "/tmp/schiesskino-theme-render"))


def save(screen: pygame.Surface, name: str) -> None:
    pygame.image.save(screen, OUTPUT / name)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pygame.init()
    display = pygame.display.set_mode((1024, 768))
    screen = pygame.Surface((1024, 768))
    leaderboard = ArcadeLeaderboardOverlay(screen, None)

    ui = LaserDiagnosticUI(
        screen,
        Settings(),
        object(),
        weapon_calibration_path=None,
        target_history_path=None,
        water_alarm_leaderboard_path=None,
    )
    ui.aligner.phase = "success"
    ui.aligner.verification = SimpleNamespace(max_error=3.0)
    ui.view_mode = "menu"
    ui._draw_main_menu()
    save(screen, "menu.png")
    ui.cans_game.sounds.stop_all()

    cans = CansApp(screen, audio_enabled=False)
    cans.start(100.0)
    cans.draw(100.0)
    leaderboard.draw_ready_preview("cans")
    save(screen, "cans-ready.png")
    cans.begin_countdown(100.0)
    cans.update(104.0)
    cans.update(104.1)
    cans.draw(104.1)
    save(screen, "cans-play.png")

    clay = ClayShootingApp(screen, audio_enabled=False, random_seed=1)
    clay.start(100.0)
    clay.draw(100.0)
    leaderboard.draw_ready_preview("clay")
    save(screen, "clay-ready.png")
    clay.begin_countdown(100.0)
    clay.update(104.0)
    clay.update(104.1)
    clay.update(104.8)
    clay.draw(104.8)
    save(screen, "clay-play.png")

    timed = TimedShootingApp(screen, audio_enabled=False, random_seed=2)
    timed.start(100.0)
    timed.draw(100.0)
    leaderboard.draw_ready_preview("timed")
    save(screen, "timed-ready.png")
    timed.begin_countdown(100.0)
    timed.update(104.0)
    timed.update(104.1)
    timed.draw(104.2)
    save(screen, "timed-play.png")

    reaction = ReactionApp(screen, audio_enabled=False, random_seed=3)
    reaction.start(100.0)
    reaction.draw(100.0)
    leaderboard.draw_ready_preview("reaction")
    save(screen, "reaction-ready.png")
    reaction.begin_countdown(100.0)
    reaction.update(104.0)
    reaction.next_signal_at = 104.1
    reaction.update(104.1)
    reaction.draw(104.2)
    save(screen, "reaction-play.png")

    target = TargetRangeApp(screen, audio_enabled=False, history_path=None)
    target.draw(100.0)
    save(screen, "range-play.png")
    for index in range(target.shot_limit):
        target.handle_shot(target.target_center, 100.0 + index)
    target.draw(104.5)
    save(screen, "range-result.png")

    pygame.display.quit()
    pygame.quit()


if __name__ == "__main__":
    main()
