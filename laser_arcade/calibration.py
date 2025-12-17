from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .config import load_calibration, save_calibration
from .constants import SCREEN_HEIGHT, SCREEN_WIDTH

LOGGER = logging.getLogger(__name__)


CALIB_POINTS = [
    (0, 0),
    (SCREEN_WIDTH - 1, 0),
    (SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1),
    (0, SCREEN_HEIGHT - 1),
    (SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2),
]


@dataclass
class CalibrationData:
    homography: Optional[np.ndarray]
    camera_points: List[Tuple[int, int]]
    screen_points: List[Tuple[int, int]]


def compute_homography(camera_points: List[Tuple[int, int]]) -> CalibrationData:
    if len(camera_points) != len(CALIB_POINTS):
        raise ValueError("Es werden genau 5 Punkte benötigt")

    src = np.array(camera_points, dtype=np.float32)
    dst = np.array(CALIB_POINTS, dtype=np.float32)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC)
    if H is None:
        raise RuntimeError("Homographie konnte nicht berechnet werden")
    LOGGER.info("Homographie berechnet. mask=%s", mask.ravel().tolist())
    save_calibration(H, camera_points, CALIB_POINTS)
    return CalibrationData(homography=H, camera_points=camera_points, screen_points=CALIB_POINTS)


def load_homography() -> CalibrationData:
    stored = load_calibration()
    if not stored or stored.get("homography") is None:
        return CalibrationData(homography=None, camera_points=[], screen_points=CALIB_POINTS)
    H = np.array(stored["homography"], dtype=np.float32)
    return CalibrationData(
        homography=H,
        camera_points=stored.get("camera_points", []),
        screen_points=stored.get("screen_points", CALIB_POINTS),
    )


def apply_homography(H: np.ndarray, point: Tuple[int, int]) -> Tuple[int, int]:
    pts = np.array([[point]], dtype=np.float32)
    mapped = cv2.perspectiveTransform(pts, H)[0][0]
    return int(mapped[0]), int(mapped[1])
