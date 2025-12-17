from __future__ import annotations

from pathlib import Path

# Display defaults for the 100" 4:3 setup
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 768
FPS = 60
DEFAULT_FONT_SIZE = 32
LARGE_FONT_SIZE = 44
TITLE_FONT_SIZE = 56

HOME_DIR = Path.home()
APP_DIR = HOME_DIR / ".laser_arcade"
CONFIG_FILE = APP_DIR / "settings.json"
CALIBRATION_FILE = APP_DIR / "calibration.json"
LOG_DIR = APP_DIR / "logs"

LASER_COLOR_PROFILE = {
    "lower1": (0, 120, 120),
    "upper1": (8, 255, 255),
    "lower2": (170, 120, 120),
    "upper2": (180, 255, 255),
    "min_area": 12,
    "max_area": 4000,
    "morph_kernel": 3,
}

EMA_ALPHA = 0.35
DWELL_MS = 300
DWELL_RADIUS = 10
