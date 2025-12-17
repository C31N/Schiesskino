from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import Settings
from .constants import EMA_ALPHA

LOGGER = logging.getLogger(__name__)


@dataclass
class LaserDetection:
    point: Optional[Tuple[int, int]]
    area: float
    confidence: float
    frame_ts: float
    mask_preview: Optional[np.ndarray]


class LaserTracker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cap: Optional[cv2.VideoCapture] = None
        self.last_point: Optional[np.ndarray] = None

    def start(self) -> None:
        cam = self.settings.camera
        self.cap = cv2.VideoCapture(cam.device_index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("Kamera konnte nicht geöffnet werden")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam.height)
        self.cap.set(cv2.CAP_PROP_FPS, cam.fps)
        LOGGER.info("Kamera gestartet (%s x %s @ %sfps)", cam.width, cam.height, cam.fps)

    def stop(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None
            LOGGER.info("Kamera gestoppt")

    def read(self) -> LaserDetection:
        if self.cap is None:
            raise RuntimeError("Kamera nicht initialisiert")
        ret, frame = self.cap.read()
        if not ret:
            raise RuntimeError("Frame konnte nicht gelesen werden")
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        laser_cfg = self.settings.laser

        lower1 = np.array(laser_cfg.lower1)
        upper1 = np.array(laser_cfg.upper1)
        lower2 = np.array(laser_cfg.lower2)
        upper2 = np.array(laser_cfg.upper2)

        mask1 = cv2.inRange(hsv, lower1, upper1)
        mask2 = cv2.inRange(hsv, lower2, upper2)
        mask = cv2.bitwise_or(mask1, mask2)

        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (laser_cfg.morph_kernel, laser_cfg.morph_kernel)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_area = 0
        for c in contours:
            area = cv2.contourArea(c)
            if area < laser_cfg.min_area or area > laser_cfg.max_area:
                continue
            if area > best_area:
                best_area = area
                M = cv2.moments(c)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    best = (cx, cy)

        smoothed = None
        if best is not None:
            point_arr = np.array(best, dtype=np.float32)
            if self.last_point is None:
                smoothed = point_arr
            else:
                alpha = self.settings.ema_alpha or EMA_ALPHA
                smoothed = alpha * point_arr + (1 - alpha) * self.last_point
            self.last_point = smoothed

        confidence = min(1.0, best_area / max(laser_cfg.min_area, 1))

        mask_preview = None
        try:
            preview_small = cv2.resize(mask, (160, 120), interpolation=cv2.INTER_NEAREST)
            mask_preview = cv2.cvtColor(preview_small, cv2.COLOR_GRAY2RGB)
        except Exception:
            LOGGER.debug("Konnte Masken-Vorschau nicht erzeugen", exc_info=True)
        return LaserDetection(
            point=tuple(int(x) for x in smoothed) if smoothed is not None else None,
            area=best_area,
            confidence=confidence,
            frame_ts=time.time(),
            mask_preview=mask_preview,
        )
