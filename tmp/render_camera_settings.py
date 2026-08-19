from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import numpy as np
import pygame

from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI
from laser_arcade.laser_tracker import LaserDetection
from tests.test_camera_settings import CameraSettingsTracker


def main() -> None:
    output = Path(os.environ.get("CAMERA_SETTINGS_RENDER_DIR", "/tmp/camera-settings-render"))
    output.mkdir(parents=True, exist_ok=True)
    pygame.init()
    screen = pygame.display.set_mode((1024, 768))
    settings = Settings()
    tracker = CameraSettingsTracker(settings)
    ui = LaserDiagnosticUI(
        screen,
        settings,
        tracker,
        weapon_calibration_path=None,
        target_history_path=None,
        water_alarm_leaderboard_path=None,
        arcade_leaderboard_path=None,
    )
    ui.aligner.phase = "success"
    ui.aligner.homography = np.eye(3, dtype=np.float32)
    ui.aligner.corners = np.asarray(
        [(120, 72), (510, 84), (525, 325), (105, 314)], dtype=np.float32
    )
    yy, xx = np.mgrid[:360, :640]
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :, 0] = np.clip(45 + xx * 170 / 640, 0, 255)
    frame[:, :, 1] = np.clip(25 + yy * 130 / 360, 0, 255)
    frame[:, :, 2] = np.clip(18 + (xx + yy) * 70 / 1000, 0, 255)
    ui.last_frame_rgb = frame
    ui.last_mask_rgb = np.zeros((112, 200, 3), dtype=np.uint8)
    ui.last_mask_rgb[52:59, 92:99, 1:] = 255
    ui.last_detection = LaserDetection(
        point=(310, 178),
        area=8.0,
        confidence=0.94,
        frame_ts=0.0,
        mask_preview=ui.last_mask_rgb,
        frame_preview=frame,
        peak_red_excess=138,
        peak_delta=164,
        red_threshold=55,
        delta_threshold=110,
        active_filter_profile="red_filter",
        filter_confidence=0.91,
    )
    ui._open_camera_settings()
    ui.draw(60.0)
    pygame.image.save(screen, output / "ausrichtung.png")
    ui.camera_settings_tab = "detection"
    ui._set_camera_filter_mode("red_filter")
    ui.draw(60.0)
    pygame.image.save(screen, output / "erkennung-einfach.png")
    ui.camera_settings_advanced = True
    ui.draw(60.0)
    pygame.image.save(screen, output / "erkennung-erweitert.png")
    ui.close()
    pygame.quit()


if __name__ == "__main__":
    main()
