from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

LOGGER = logging.getLogger(__name__)


@dataclass
class PointerEvent:
    type: str  # move, down, up, click
    position: Tuple[int, int]
    source: str  # laser | mouse
    timestamp: float


class DwellClickDetector:
    def __init__(self, dwell_ms: int, radius_px: int, debounce_ms: int = 350):
        self.dwell_ms = dwell_ms
        self.radius_px = radius_px
        self.debounce_ms = debounce_ms
        self.anchor: Optional[Tuple[int, int]] = None
        self.anchor_ts: Optional[float] = None
        self.last_click_ts: float = 0

    def update(self, point: Optional[Tuple[int, int]]) -> Optional[str]:
        now = time.time() * 1000
        if point is None:
            self.anchor = None
            self.anchor_ts = None
            return None

        if self.anchor is None:
            self.anchor = point
            self.anchor_ts = now
            return None

        dx = point[0] - self.anchor[0]
        dy = point[1] - self.anchor[1]
        if (dx * dx + dy * dy) ** 0.5 > self.radius_px:
            self.anchor = point
            self.anchor_ts = now
            return None

        if now - self.anchor_ts >= self.dwell_ms and now - self.last_click_ts >= self.debounce_ms:
            self.last_click_ts = now
            LOGGER.debug("Dwell Click ausgelöst")
            self.anchor_ts = now
            return "click"
        return None


class PointerRouter:
    def __init__(
        self,
        on_event: Callable[[PointerEvent], None],
        dwell_ms: int,
        dwell_radius: int,
    ):
        self.on_event = on_event
        self.detector = DwellClickDetector(dwell_ms=dwell_ms, radius_px=dwell_radius)
        self.last_pos: Optional[Tuple[int, int]] = None
        self.source = "laser"

    def feed_point(self, point: Optional[Tuple[int, int]], source: str = "laser") -> None:
        now = time.time()
        self.source = source
        if point:
            self.last_pos = point
            self.on_event(PointerEvent("move", point, source, now))
            click = self.detector.update(point)
            if click:
                self.on_event(PointerEvent("click", point, source, now))
        else:
            self.detector.update(None)

    def feed_mouse_event(self, event_type: str, pos: Tuple[int, int]) -> None:
        now = time.time()
        self.last_pos = pos
        self.on_event(PointerEvent(event_type, pos, "mouse", now))
