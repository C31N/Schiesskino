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
WEAPON_CALIBRATION_FILE = APP_DIR / "weapon_calibration.json"
TARGET_HISTORY_FILE = APP_DIR / "target_history.json"
WATER_ALARM_LEADERBOARD_FILE = APP_DIR / "water_alarm_leaderboard.json"
ARCADE_LEADERBOARD_FILE = APP_DIR / "arcade_leaderboards.json"
LOG_DIR = APP_DIR / "logs"

LASER_COLOR_PROFILE = {
    # 620 nm erscheint je nach Weißabgleich rot bis rot-orange. Der breite
    # Hue-Bereich wird zusätzlich durch zeitliche Differenz und Rotüberschuss
    # abgesichert.
    "lower1": (0, 12, 60),
    "upper1": (25, 255, 255),
    "lower2": (165, 12, 60),
    "upper2": (180, 255, 255),
    "min_area": 1,
    "max_area": 1400,
    "morph_kernel": 3,
}

EMA_ALPHA = 0.35
DWELL_MS = 300
DWELL_RADIUS = 10
