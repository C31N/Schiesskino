from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

from .constants import APP_DIR, CALIBRATION_FILE, CONFIG_FILE, LASER_COLOR_PROFILE


LOGGER = logging.getLogger(__name__)
SETTINGS_VERSION = 7


@dataclass
class CameraConfig:
    device_index: int = 0
    width: int = 640
    height: int = 360
    fps: int = 30
    fourcc: str = "YUYV"


@dataclass
class ShotDetectionProfile:
    """Vom Benutzer einstellbare Untergrenzen eines Erkennungsprofils."""

    sensitivity: int = 50
    min_red_excess: int = 8
    min_frame_delta: int = 5
    min_value: int = 60
    min_area: int = LASER_COLOR_PROFILE["min_area"]
    max_area: int = LASER_COLOR_PROFILE["max_area"]
    strict_temporal: bool = False

    def bounded(self) -> "ShotDetectionProfile":
        return ShotDetectionProfile(
            sensitivity=max(0, min(100, int(self.sensitivity))),
            min_red_excess=max(3, min(180, int(self.min_red_excess))),
            min_frame_delta=max(3, min(200, int(self.min_frame_delta))),
            min_value=max(20, min(245, int(self.min_value))),
            min_area=max(1, min(20, int(self.min_area))),
            max_area=max(20, min(1400, int(self.max_area))),
            strict_temporal=bool(self.strict_temporal),
        )

    def runtime_values(self) -> tuple[int, int, int, int, int]:
        """Wendet den verständlichen Empfindlichkeitsregler auf die Basiswerte an."""

        sensitivity_factor = 1.6 - 1.2 * max(0, min(100, self.sensitivity)) / 100.0
        value_factor = 1.25 - 0.5 * max(0, min(100, self.sensitivity)) / 100.0
        return (
            max(3, min(180, round(self.min_red_excess * sensitivity_factor))),
            max(3, min(200, round(self.min_frame_delta * sensitivity_factor))),
            max(20, min(245, round(self.min_value * value_factor))),
            max(1, min(20, int(self.min_area))),
            max(max(20, int(self.min_area)), min(1400, int(self.max_area))),
        )


def _normal_detection_profile() -> ShotDetectionProfile:
    return ShotDetectionProfile()


def _red_filter_detection_profile() -> ShotDetectionProfile:
    return ShotDetectionProfile(
        sensitivity=50,
        min_red_excess=55,
        min_frame_delta=110,
        min_value=85,
        min_area=1,
        max_area=320,
        strict_temporal=True,
    )


@dataclass
class LaserProfile:
    lower1: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["lower1"])
    upper1: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["upper1"])
    lower2: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["lower2"])
    upper2: tuple[int, int, int] = field(default_factory=lambda: LASER_COLOR_PROFILE["upper2"])
    morph_kernel: int = LASER_COLOR_PROFILE["morph_kernel"]
    debounce_ms: int = 160
    background_alpha: float = 0.035
    filter_mode: str = "auto"
    normal: ShotDetectionProfile = field(default_factory=_normal_detection_profile)
    red_filter: ShotDetectionProfile = field(default_factory=_red_filter_detection_profile)

    def bounded(self) -> "LaserProfile":
        self.filter_mode = (
            self.filter_mode
            if self.filter_mode in {"auto", "normal", "red_filter"}
            else "auto"
        )
        self.normal = self.normal.bounded()
        self.red_filter = self.red_filter.bounded()
        self.morph_kernel = max(1, min(9, int(self.morph_kernel)))
        self.debounce_ms = max(80, min(500, int(self.debounce_ms)))
        self.background_alpha = max(0.005, min(0.25, float(self.background_alpha)))
        return self

    def profile(self, name: str) -> ShotDetectionProfile:
        return self.red_filter if name == "red_filter" else self.normal


@dataclass
class Settings:
    settings_version: int = SETTINGS_VERSION
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
            "settings_version": self.settings_version,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "camera": asdict(self.camera),
            "laser": asdict(self.laser),
            "dwell_ms": self.dwell_ms,
            "dwell_radius": self.dwell_radius,
            "ema_alpha": self.ema_alpha,
            "debug_overlay": self.debug_overlay,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        stored_version = int(data.get("settings_version", 1))
        camera_raw = data.get("camera", {}) if isinstance(data.get("camera"), dict) else {}
        camera_cfg = {
            key: camera_raw[key]
            for key in ("device_index", "width", "height", "fps", "fourcc")
            if key in camera_raw
        }
        laser_raw = data.get("laser", {}) if isinstance(data.get("laser"), dict) else {}
        normal_raw = laser_raw.get("normal")
        if not isinstance(normal_raw, dict):
            # Version 6 und älter speicherten das einzige Profil flach. Diese
            # Werte werden verlustfrei zum Profil „Ohne Filter“ migriert.
            normal_raw = {
                "min_red_excess": laser_raw.get("min_red_excess", 8),
                "min_frame_delta": laser_raw.get("min_frame_delta", 5),
                "min_value": min(
                    laser_raw.get("lower1", LASER_COLOR_PROFILE["lower1"])[2],
                    laser_raw.get("lower2", LASER_COLOR_PROFILE["lower2"])[2],
                ),
                "min_area": laser_raw.get("min_area", LASER_COLOR_PROFILE["min_area"]),
                "max_area": laser_raw.get("max_area", LASER_COLOR_PROFILE["max_area"]),
            }
        red_raw = laser_raw.get("red_filter")
        if not isinstance(red_raw, dict):
            red_raw = asdict(_red_filter_detection_profile())

        def detection_profile(raw: Dict[str, Any], fallback: ShotDetectionProfile) -> ShotDetectionProfile:
            values = asdict(fallback)
            for key in values:
                if key in raw:
                    values[key] = raw[key]
            return ShotDetectionProfile(**values).bounded()

        laser = LaserProfile(
            lower1=tuple(laser_raw.get("lower1", LASER_COLOR_PROFILE["lower1"])),
            upper1=tuple(laser_raw.get("upper1", LASER_COLOR_PROFILE["upper1"])),
            lower2=tuple(laser_raw.get("lower2", LASER_COLOR_PROFILE["lower2"])),
            upper2=tuple(laser_raw.get("upper2", LASER_COLOR_PROFILE["upper2"])),
            morph_kernel=laser_raw.get("morph_kernel", LASER_COLOR_PROFILE["morph_kernel"]),
            debounce_ms=laser_raw.get("debounce_ms", 160),
            background_alpha=laser_raw.get("background_alpha", 0.035),
            filter_mode=laser_raw.get("filter_mode", "auto"),
            normal=detection_profile(normal_raw, _normal_detection_profile()),
            red_filter=detection_profile(red_raw, _red_filter_detection_profile()),
        ).bounded()
        return cls(
            settings_version=SETTINGS_VERSION,
            screen_width=data.get("screen_width", 1024),
            screen_height=data.get("screen_height", 768),
            camera=CameraConfig(**camera_cfg),
            laser=laser,
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
            settings = Settings.from_dict(data)
            if data.get("settings_version") != SETTINGS_VERSION:
                save_settings(settings)
            return settings
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("Einstellungen defekt, lade Defaults: %s", exc)
            _backup_corrupt_file(CONFIG_FILE)
    settings = Settings()
    save_settings(settings)
    return settings


def save_settings(settings: Settings) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(CONFIG_FILE, settings.to_dict())


def _atomic_json_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


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


def save_calibration(
    matrix,
    points_camera,
    points_screen,
    *,
    alignment_mode: str = "automatic",
    source_size=None,
) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "homography": matrix.tolist() if matrix is not None else None,
        "camera_points": points_camera,
        "screen_points": points_screen,
        "alignment_mode": alignment_mode if alignment_mode in {"automatic", "manual"} else "automatic",
        "source_size": list(source_size) if source_size is not None else None,
    }
    _atomic_json_write(CALIBRATION_FILE, data)
