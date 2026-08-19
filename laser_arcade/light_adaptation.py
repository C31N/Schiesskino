from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class LightMetrics:
    screen_median: float
    screen_highlight: float
    screen_peak: float
    outside_median: float
    screen_to_ambient_ratio: float
    clipped_fraction: float


@dataclass(frozen=True)
class ExposureDecision:
    metrics: LightMetrics
    exposure: int
    changed: bool


class AmbientLightController:
    """Regelt eine feste Kamerabelichtung anhand von Leinwand und Umgebung."""

    def __init__(
        self,
        *,
        initial_exposure: int = 160,
        minimum_exposure: int = 24,
        maximum_exposure: int = 300,
        settle_ms: float = 6500.0,
        interval_ms: float = 1800.0,
    ) -> None:
        self.exposure = initial_exposure
        self.minimum_exposure = minimum_exposure
        self.maximum_exposure = maximum_exposure
        self.settle_ms = settle_ms
        self.interval_ms = interval_ms
        self.started_ms: Optional[float] = None
        self.last_sample_ms = -1e12
        self.last_metrics: Optional[LightMetrics] = None

    @staticmethod
    def measure(
        frame_bgr: np.ndarray,
        projection_mask: Optional[np.ndarray] = None,
    ) -> LightMetrics:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if projection_mask is None or projection_mask.shape != gray.shape:
            inside = np.ones(gray.shape, dtype=bool)
        else:
            inside = projection_mask.astype(bool)
            if int(inside.sum()) < 100:
                inside = np.ones(gray.shape, dtype=bool)

        outside = ~inside
        screen_values = gray[inside]
        if int(outside.sum()) >= 100:
            outside_median = float(np.median(gray[outside]))
        else:
            outside_median = float(np.percentile(screen_values, 20.0))

        screen_median = float(np.median(screen_values))
        screen_highlight = float(np.percentile(screen_values, 97.5))
        # Kleine Zielmitten und dichte Treffermarkierungen belegen deutlich
        # weniger als 2,5 % der Leinwand. Ein hohes Perzentil hält dort
        # trotzdem ausreichend Kamerasensor-Reserve für den Laser frei.
        screen_peak = float(np.percentile(screen_values, 99.7))
        ratio = (screen_median + 6.0) / (outside_median + 6.0)
        clipped = float(np.mean(screen_values >= 246))
        return LightMetrics(
            screen_median=screen_median,
            screen_highlight=screen_highlight,
            screen_peak=screen_peak,
            outside_median=outside_median,
            screen_to_ambient_ratio=ratio,
            clipped_fraction=clipped,
        )

    def update(
        self,
        frame_bgr: np.ndarray,
        now_ms: float,
        projection_mask: Optional[np.ndarray] = None,
    ) -> Optional[ExposureDecision]:
        if self.started_ms is None:
            self.started_ms = now_ms
        if now_ms - self.started_ms < self.settle_ms:
            return None
        if now_ms - self.last_sample_ms < self.interval_ms:
            return None
        self.last_sample_ms = now_ms

        metrics = self.measure(frame_bgr, projection_mask)
        self.last_metrics = metrics
        old_exposure = self.exposure

        if metrics.screen_peak >= 246 or metrics.clipped_fraction > 0.003:
            candidate = round(old_exposure * 0.82)
        elif (
            metrics.clipped_fraction > 0.012
            and metrics.outside_median >= 75
        ):
            candidate = round(old_exposure * 0.72)
        elif metrics.outside_median >= 145:
            candidate = round(old_exposure * 0.84)
        elif metrics.outside_median >= 112:
            candidate = round(old_exposure * 0.92)
        elif metrics.outside_median <= 28 and metrics.screen_highlight <= 170:
            candidate = round(old_exposure * 1.18)
        elif metrics.outside_median <= 45 and metrics.screen_highlight <= 190:
            candidate = round(old_exposure * 1.09)
        else:
            candidate = old_exposure

        self.exposure = max(
            self.minimum_exposure,
            min(self.maximum_exposure, candidate),
        )
        changed = abs(self.exposure - old_exposure) >= 3
        if not changed:
            self.exposure = old_exposure
        return ExposureDecision(metrics, self.exposure, changed)
