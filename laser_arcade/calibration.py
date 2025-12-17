from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import load_calibration, save_calibration
from .constants import SCREEN_HEIGHT, SCREEN_WIDTH

LOGGER = logging.getLogger(__name__)


def build_calib_points(width: int, height: int) -> List[Tuple[int, int]]:
    """Baue die Kalibrierpunkte basierend auf der tatsächlichen Bildschirmgröße."""

    return [
        (0, 0),
        (width - 1, 0),
        (width - 1, height - 1),
        (0, height - 1),
        (width // 2, height // 2),
    ]


@dataclass
class CalibrationData:
    homography: Optional[np.ndarray]
    camera_points: List[Tuple[int, int]]
    screen_points: List[Tuple[int, int]]


def compute_homography(
    camera_points: List[Tuple[int, int]],
    screen_points: Optional[List[Tuple[int, int]]] = None,
) -> CalibrationData:
    screen_points = screen_points or build_calib_points(SCREEN_WIDTH, SCREEN_HEIGHT)
    if len(camera_points) != len(screen_points):
        raise ValueError(f"Es werden genau {len(screen_points)} Punkte benötigt")

    src = np.array(camera_points, dtype=np.float32)
    dst = np.array(screen_points, dtype=np.float32)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC)
    if H is None:
        raise RuntimeError("Homographie konnte nicht berechnet werden")
    LOGGER.info("Homographie berechnet. mask=%s", mask.ravel().tolist())
    save_calibration(H, camera_points, screen_points)
    return CalibrationData(homography=H, camera_points=camera_points, screen_points=screen_points)


def load_homography(screen_points: Optional[List[Tuple[int, int]]] = None) -> CalibrationData:
    screen_points = screen_points or build_calib_points(SCREEN_WIDTH, SCREEN_HEIGHT)
    stored = load_calibration()
    if not stored or stored.get("homography") is None:
        return CalibrationData(homography=None, camera_points=[], screen_points=screen_points)
    H = np.array(stored["homography"], dtype=np.float32)
    stored_screen_points = stored.get("screen_points") or screen_points
    camera_points = stored.get("camera_points", [])
    if len(camera_points) != len(stored_screen_points):
        LOGGER.warning(
            "Ungültige Kalibrierdaten: Anzahl Kamera- (%s) und Screen-Punkte (%s) stimmt nicht überein",
            len(camera_points),
            len(stored_screen_points),
        )
    return CalibrationData(
        homography=H,
        camera_points=camera_points,
        screen_points=stored_screen_points,
    )


def apply_homography(H: np.ndarray, point: Tuple[int, int]) -> Tuple[int, int]:
    pts = np.array([[point]], dtype=np.float32)
    mapped = cv2.perspectiveTransform(pts, H)[0][0]
    return int(mapped[0]), int(mapped[1])
