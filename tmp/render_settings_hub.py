from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

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


def main() -> None:
    output = Path(os.environ.get("SETTINGS_HUB_RENDER_DIR", "/tmp/settings-hub"))
    output.mkdir(parents=True, exist_ok=True)
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

    ui._show_menu()
    ui._draw_main_menu()
    pygame.image.save(screen, output / "main-menu.png")

    ui._toggle_view()
    ui.draw(30.0)
    pygame.image.save(screen, output / "camera-hub.png")
    ui.cans_game.sounds.stop_all()
    pygame.quit()


if __name__ == "__main__":
    main()
