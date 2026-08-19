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


OUTPUT = Path(os.environ.get("SECOND_RENDER_DIR", "/tmp/schiesskino-second-pass"))


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def reset_state(self) -> None:
        return None

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        return None


def save(surface: pygame.Surface, name: str) -> None:
    pygame.image.save(surface, OUTPUT / name)


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
    ui.view_mode = "menu"

    ui.menu_page = 0
    ui._draw_main_menu()
    save(screen, "menu-page-1.png")
    ui.menu_page = 1
    ui.easter_moorhuhn_armed = True
    ui.easter_moorhuhn_progress = 1
    ui._draw_main_menu()
    save(screen, "menu-page-2-easter.png")
    ui.menu_page = 2
    ui.easter_moorhuhn_armed = False
    ui._draw_main_menu()
    save(screen, "menu-page-3-duel.png")

    ui._request_program_close()
    ui.close_pin_digits = "19"
    ui.draw(60.0)
    save(screen, "program-close-pin.png")
    ui._cancel_program_close()

    ui.ocean_cleanup_game.start(100.0)
    ui.ocean_cleanup_game.draw(100.0)
    save(screen, "ocean-ready-neutral-title.png")

    games = [
        ui.water_alarm_game,
        ui.cans_game,
        ui.timed_game,
        ui.clay_game,
        ui.reaction_game,
        ui.target_range_game,
        ui.balloon_game,
        ui.alien_game,
        ui.star_game,
        ui.math_game,
        ui.color_game,
        ui.treasure_game,
        ui.ocean_cleanup_game,
        ui.tobia_duel_game,
    ]
    frames: list[pygame.Surface] = []
    for index, game in enumerate(games):
        if game is ui.target_range_game:
            game.start(100.0)
            game.draw(105.0)
        else:
            advance_to_playing(game)
            game.draw(105.2)
        frame = screen.copy()
        frames.append(frame)
        save(frame, f"game-{index + 1:02d}.png")

    chicken = ChickenApp(screen, audio_enabled=False, random_seed=1919)
    advance_to_playing(chicken)
    chicken.draw(105.2)
    frames.append(screen.copy())
    save(screen, "game-15-moorhuhn.png")

    chicken.state = "game_over"
    chicken.score = 1919
    chicken.hits = 14
    chicken.shots = 20
    chicken.best_score = 2200
    chicken.finish_reason = "Die Zeit ist abgelaufen"
    ui.chicken_game = chicken
    ui.view_mode = "chickens"
    ui.arcade_leaderboard.prepare("chickens", chicken)
    ui.draw(60.0)
    save(screen, "moorhuhn-top-10.png")

    montage = pygame.Surface((2048, 1536))
    montage.fill((0, 5, 14))
    for index, frame in enumerate(frames):
        thumb = pygame.transform.smoothscale(frame, (512, 384))
        montage.blit(thumb, ((index % 4) * 512, (index // 4) * 384))
    save(montage, "all-games-second-pass.png")

    ui.cans_game.sounds.stop_all()
    pygame.quit()


if __name__ == "__main__":
    main()
