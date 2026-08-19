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
    output = Path(os.environ.get("MENU_MOUSE_RENDER_DIR", "/tmp/menu-mice"))
    output.mkdir(parents=True, exist_ok=True)
    pygame.init()
    pygame.display.set_mode((1024, 768))
    screen = pygame.Surface((1024, 768))
    ui = LaserDiagnosticUI(
        screen, Settings(), DummyTracker(), weapon_calibration_path=None,
        target_history_path=None, water_alarm_leaderboard_path=None,
        arcade_leaderboard_path=None,
    )
    ui.aligner.phase = "success"
    ui.aligner.homography = np.eye(3, dtype=np.float32)
    ui.aligner.verification = SimpleNamespace(max_error=3.0)
    ui._show_menu()

    for index, mouse in enumerate(ui.menu_mice):
        ui._spawn_menu_mouse(mouse, 100.0)
        mouse.x = 280.0 + index * 430.0
        mouse.y = 735 - index * 35
        mouse.target_x = mouse.x + (180 if index == 0 else -220)
        mouse.target_y = 690 + index * 42
        mouse.direction = 1 if mouse.target_x > mouse.x else -1
        mouse.last_update = 100.0
    for index, now in enumerate((100.0, 100.12, 100.24, 100.36)):
        ui._draw_main_menu(now)
        pygame.image.save(screen, output / f"menu-mice-{index + 1}.png")

    poses = ("standing", "sitting", "sniffing", "grooming")
    for index, pose in enumerate(poses):
        for mouse in ui.menu_mice:
            mouse.active = False
        mouse = ui.menu_mice[0]
        mouse.active = True
        mouse.x, mouse.y = 420.0 + index * 55.0, 725.0 - index * 11.0
        mouse.direction = -1 if index % 2 else 1
        mouse.heading_angle = 0
        mouse.state = pose
        mouse.state_until = 200.0
        mouse.last_update = 150.0
        ui._draw_main_menu(150.0 + index * 0.1)
        pygame.image.save(screen, output / f"menu-mouse-pose-{pose}.png")

    for side, x in (("left", 142.0), ("right", 815.0)):
        mouse = ui.menu_mice[0]
        mouse.active = True
        mouse.x, mouse.y = x, 730.0 if side == "left" else 720.0
        mouse.direction = -1
        mouse.heading_angle = 0
        mouse.state = "standing"
        mouse.exiting = side == "left"
        if side == "left":
            mouse.target_x, mouse.target_y = 70.0, 730.0
        mouse.state_until = 300.0
        mouse.last_update = 250.0
        ui._draw_main_menu(250.0)
        pygame.image.save(screen, output / f"menu-mouse-behind-{side}.png")

    mouse = ui.menu_mice[0]
    for index, x in enumerate((190.0, 166.0, 148.0, 130.0, 106.0, 82.0)):
        mouse.active = True
        mouse.x, mouse.y = x, 730.0
        mouse.direction = -1
        mouse.heading_angle = 0
        mouse.state = "entering"
        mouse.exiting = True
        mouse.target_x, mouse.target_y = 82.0, 730.0
        mouse.speed = 0.0
        mouse.last_update = 310.0 + index
        ui._draw_main_menu(310.0 + index)
        pygame.image.save(screen, output / f"menu-mouse-entry-{index + 1}.png")

    for index, x in enumerate((82.0, 106.0, 130.0, 148.0, 166.0, 190.0)):
        mouse.active = True
        mouse.x, mouse.y = x, 730.0
        mouse.direction = 1
        mouse.heading_angle = 0
        mouse.state = "emerging"
        mouse.exiting = False
        mouse.target_x, mouse.target_y = 190.0, 730.0
        mouse.speed = 0.0
        mouse.last_update = 330.0 + index
        ui._draw_main_menu(330.0 + index)
        pygame.image.save(screen, output / f"menu-mouse-exit-{index + 1}.png")
    ui.cans_game.sounds.stop_all()
    pygame.quit()


if __name__ == "__main__":
    main()
