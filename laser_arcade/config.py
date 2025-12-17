from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from .constants import APP_DIR, CALIBRATION_FILE, CONFIG_FILE, LASER_COLOR_PROFILE


LOGGER = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass
class LaserProfile:
    lower1: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["lower1"])
    upper1: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["upper1"])
    lower2: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["lower2"])
    upper2: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["upper2"])
    min_area: int = LASER_COLOR_PROFILE["min_area"]
    max_area: int = LASER_COLOR_PROFILE["max_area"]
    morph_kernel: int = LASER_COLOR_PROFILE["morph_kernel"]


@dataclass
class Settings:
    screen_width: int = 1024
    screen_height: int = 768
    camera: CameraConfig = field(default_factory=CameraConfig)
    laser: LaserProfile = field(default_factory=LaserProfile)
    dwell_ms: int = 300
    dwell_radius: int = 10
    ema_alpha: float = 0.35
    debug_overlay: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "camera": vars(self.camera),
            "laser": vars(self.laser),
            "dwell_ms": self.dwell_ms,
            "dwell_radius": self.dwell_radius,
            "ema_alpha": self.ema_alpha,
            "debug_overlay": self.debug_overlay,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        camera_cfg = data.get("camera", {})
        laser_cfg = data.get("laser", {})
        return cls(
            screen_width=data.get("screen_width", 1024),
            screen_height=data.get("screen_height", 768),
            camera=CameraConfig(**camera_cfg),
            laser=LaserProfile(**laser_cfg),
            dwell_ms=data.get("dwell_ms", 300),
            dwell_radius=data.get("dwell_radius", 10),
            ema_alpha=data.get("ema_alpha", 0.35),
            debug_overlay=data.get("debug_overlay", False),
        )


def load_settings() -> Settings:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return Settings.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("Einstellungen defekt, lade Defaults: %s", exc)
            _backup_corrupt_file(CONFIG_FILE)
    settings = Settings()
    save_settings(settings)
    return settings


def save_settings(settings: Settings) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, indent=2)


def _backup_corrupt_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        backup_path = path.with_suffix(path.suffix + ".bak")
        counter = 1
        while backup_path.exists():
            backup_path = path.with_suffix(path.suffix + f".bak{counter}")
            counter += 1
        path.rename(backup_path)
        LOGGER.info("Defekte Datei gesichert unter %s", backup_path)
    except OSError as exc:
        LOGGER.warning("Backup fehlgeschlagen für %s: %s", path, exc)


def load_calibration() -> Dict[str, Any] | None:
    if not CALIBRATION_FILE.exists():
        return None
    try:
        with CALIBRATION_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Kalibrierungsdatei defekt, verwende Defaults: %s", exc)
        _backup_corrupt_file(CALIBRATION_FILE)
        return None


def save_calibration(matrix, points_camera, points_screen) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "homography": matrix.tolist() if matrix is not None else None,
        "camera_points": points_camera,
        "screen_points": points_screen,
    }
    with CALIBRATION_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
