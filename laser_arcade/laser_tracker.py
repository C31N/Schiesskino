from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import LaserProfile, Settings, load_calibration
from .constants import CALIBRATION_FILE
from .light_adaptation import AmbientLightController
from .shot_detector import DetectionConfig, PulseShotDetector

LOGGER = logging.getLogger(__name__)


def build_camera_detection_masks(
    camera_points: list[Tuple[int, int]],
    processing_size: Tuple[int, int],
    source_size: Tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Erzeugt die Spiel- und Übersichtsmasken im Kamerabild.

    Die normale Maske umfasst nur die Leinwand. Die zweite Maske ergänzt
    ausschließlich die seitlichen Kamerabereiche bis zum Bildrand. Dadurch
    kann dort in der Spielübersicht geblättert werden, ohne den Bereich ober-
    oder unterhalb der Leinwand als Bedienfläche freizugeben.
    """

    processing_width, processing_height = processing_size
    source_width, source_height = source_size
    polygon = np.asarray(camera_points, dtype=np.float32)
    if polygon.shape != (4, 2):
        raise ValueError("Für die Kameramasken werden vier Leinwandecken benötigt")
    polygon[:, 0] *= processing_width / max(1, source_width)
    polygon[:, 1] *= processing_height / max(1, source_height)
    corners = np.rint(polygon).astype(np.int32)
    top_left, top_right, bottom_right, bottom_left = corners

    projection_mask = np.zeros((processing_height, processing_width), dtype=np.uint8)
    cv2.fillConvexPoly(projection_mask, corners, 255)
    projection_mask = cv2.erode(
        projection_mask,
        np.ones((7, 7), dtype=np.uint8),
        iterations=1,
    )

    overview_mask = projection_mask.copy()
    left_zone = np.asarray(
        ((0, 0), tuple(top_left), tuple(bottom_left), (0, processing_height - 1)),
        dtype=np.int32,
    )
    right_zone = np.asarray(
        (
            tuple(top_right),
            (processing_width - 1, 0),
            (processing_width - 1, processing_height - 1),
            tuple(bottom_right),
        ),
        dtype=np.int32,
    )
    cv2.fillConvexPoly(overview_mask, left_zone, 255)
    cv2.fillConvexPoly(overview_mask, right_zone, 255)
    return projection_mask, overview_mask


@dataclass
class LaserDetection:
    point: Optional[Tuple[int, int]]
    area: float
    confidence: float
    frame_ts: float
    mask_preview: Optional[np.ndarray]
    frame_preview: Optional[np.ndarray]
    shot: bool = False
    peak_red_excess: int = 0
    peak_delta: int = 0
    red_threshold: int = 0
    delta_threshold: int = 0
    active_filter_profile: str = "normal"
    filter_confidence: float = 0.0
    observed_point: Optional[Tuple[int, int]] = None
    observed_area: float = 0.0
    observed_peak_red: int = 0
    observed_peak_delta: int = 0
    observed_peak_value: int = 0


class CameraFilterClassifier:
    """Erkennt einen roten optischen Vorsatz nur aus stabiler, breiter Rotdominanz."""

    ENTER_RATIO = 1.65
    EXIT_RATIO = 1.30
    ENTER_FRACTION = 0.35
    EXIT_FRACTION = 0.18
    STABLE_MS = 3000.0

    def __init__(self, mode: str = "auto") -> None:
        self.mode = mode if mode in {"auto", "normal", "red_filter"} else "auto"
        self.active = "red_filter" if self.mode == "red_filter" else "normal"
        self.candidate = self.active
        self.candidate_since_ms = 0.0
        self.confidence = 1.0 if self.mode != "auto" else 0.0

    def set_mode(self, mode: str, now_ms: float = 0.0) -> bool:
        mode = mode if mode in {"auto", "normal", "red_filter"} else "auto"
        old = self.active
        self.mode = mode
        if mode != "auto":
            self.active = mode
            self.confidence = 1.0
        self.candidate = self.active
        self.candidate_since_ms = now_ms
        return self.active != old

    def seed_automatic_result(
        self,
        profile: str,
        confidence: float,
        now_ms: float = 0.0,
    ) -> bool:
        """Übernimmt das Ergebnis des kontrollierten Start-Farbtests."""

        if self.mode != "auto" or profile not in {"normal", "red_filter"}:
            return False
        old = self.active
        self.active = profile
        self.candidate = profile
        self.candidate_since_ms = now_ms
        self.confidence = max(0.0, min(1.0, float(confidence)))
        return self.active != old

    def update(
        self,
        frame_bgr: np.ndarray,
        now_ms: float,
        region_mask: Optional[np.ndarray] = None,
    ) -> tuple[str, float, bool]:
        if self.mode != "auto":
            changed = self.active != self.mode
            self.active = self.mode
            self.confidence = 1.0
            return self.active, self.confidence, changed

        blue, green, red = cv2.split(frame_bgr)
        value = np.maximum(red, np.maximum(green, blue))
        valid = value >= 30
        if region_mask is not None and region_mask.shape == value.shape:
            valid &= region_mask.astype(bool)
        if int(valid.sum()) < 500:
            self.confidence = 0.0
            return self.active, self.confidence, False

        rv = red[valid].astype(np.float32)
        gv = green[valid].astype(np.float32)
        bv = blue[valid].astype(np.float32)
        other = np.maximum(gv, bv)
        red_dominant = (rv >= 45.0) & (rv >= other * 1.65 + 8.0)
        red_fraction = float(red_dominant.mean())
        ratio = float(np.median(rv) / max(1.0, float(np.median(other))))
        enter_score = min(ratio / self.ENTER_RATIO, red_fraction / self.ENTER_FRACTION)
        exit_score = min(self.EXIT_RATIO / max(ratio, 0.01), self.EXIT_FRACTION / max(red_fraction, 0.001))

        desired = self.active
        if ratio >= self.ENTER_RATIO and red_fraction >= self.ENTER_FRACTION:
            desired = "red_filter"
            self.confidence = max(0.0, min(1.0, enter_score - 0.15))
        elif ratio <= self.EXIT_RATIO and red_fraction <= self.EXIT_FRACTION:
            desired = "normal"
            self.confidence = max(0.0, min(1.0, exit_score - 0.15))
        else:
            self.confidence = 0.5

        if desired != self.candidate:
            self.candidate = desired
            self.candidate_since_ms = now_ms
            return self.active, self.confidence, False
        if desired != self.active and now_ms - self.candidate_since_ms >= self.STABLE_MS:
            self.active = desired
            return self.active, self.confidence, True
        return self.active, self.confidence, False


class LaserTracker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cap: Optional[cv2.VideoCapture] = None
        self._moorhuhn_filter_enabled = False
        self.filter_classifier = CameraFilterClassifier(settings.laser.filter_mode)
        self._active_filter_profile = self.filter_classifier.active
        self.detector = self._make_detector()
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0
        self.processing_width = 640
        self.processing_height = 480
        self.processing_fps = 0.0
        self._detector_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame_ready = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._latest_detection: Optional[LaserDetection] = None
        self._pending_shots: deque[LaserDetection] = deque(maxlen=32)
        self._capture_error: Optional[str] = None
        self.light_controller = AmbientLightController()
        self._projection_mask: Optional[np.ndarray] = None
        self._overview_navigation_mask: Optional[np.ndarray] = None
        self._overview_navigation_enabled = False
        self._calibration_mtime = -1.0
        self._exposure_settle_frames = 0
        self._last_light_log_ms = -1e12
        self._last_mask_refresh_ms = -1e12
        self._calibration_mode = False

    def _make_detector(self) -> PulseShotDetector:
        laser = self.settings.laser
        profile = laser.profile(self._active_filter_profile).bounded()
        min_red, min_delta, min_value, min_area, max_area = profile.runtime_values()
        detector = PulseShotDetector(
            DetectionConfig(
                hue_max=int(laser.upper1[0]),
                hue_min_high=int(laser.lower2[0]),
                min_saturation=int(min(laser.lower1[1], laser.lower2[1])),
                min_value=min_value,
                min_red_excess=min_red,
                min_frame_delta=min_delta,
                min_area=float(min_area),
                max_area=float(max_area),
                morph_kernel=int(laser.morph_kernel),
                debounce_ms=int(laser.debounce_ms),
                background_alpha=float(laser.background_alpha),
                strict_temporal=profile.strict_temporal,
            )
        )
        detector.set_signature_filter(self._moorhuhn_filter_enabled)
        return detector

    @property
    def active_filter_profile(self) -> str:
        return self._active_filter_profile

    def apply_laser_settings(self, laser: LaserProfile) -> None:
        """Wendet einen gespeicherten oder vorläufigen Einstellungsstand global an."""

        with self._detector_lock:
            self.settings.laser = deepcopy(laser).bounded()
            changed = self.filter_classifier.set_mode(
                self.settings.laser.filter_mode,
                time.monotonic() * 1000.0,
            )
            requested = self.filter_classifier.active
            if changed or requested != self._active_filter_profile:
                self._active_filter_profile = requested
            self.detector = self._make_detector()
        with self._state_lock:
            self._pending_shots.clear()

    def apply_startup_optical_profile(
        self,
        profile: str,
        confidence: float,
        *,
        white_peak: Optional[float] = None,
        ambient_luma: Optional[float] = None,
    ) -> None:
        """Aktiviert den automatisch unter kontrollierten Farben erkannten Filter."""

        with self._detector_lock:
            changed = self.filter_classifier.seed_automatic_result(
                profile,
                confidence,
                time.monotonic() * 1000.0,
            )
            requested = self.filter_classifier.active
            if changed or requested != self._active_filter_profile:
                self._active_filter_profile = requested
            self.detector = self._make_detector()
        with self._state_lock:
            self._pending_shots.clear()
        if white_peak is not None:
            old_exposure = self.light_controller.exposure
            if white_peak >= 248.0:
                candidate = round(old_exposure * 0.72)
            elif white_peak >= 238.0:
                candidate = round(old_exposure * 0.84)
            elif white_peak <= 155.0 and (ambient_luma or 0.0) < 70.0:
                candidate = round(old_exposure * 1.18)
            elif white_peak <= 185.0 and (ambient_luma or 0.0) < 45.0:
                candidate = round(old_exposure * 1.08)
            else:
                candidate = old_exposure
            candidate = max(
                self.light_controller.minimum_exposure,
                min(self.light_controller.maximum_exposure, candidate),
            )
            if abs(candidate - old_exposure) >= 3 and self._apply_camera_exposure(candidate):
                self.light_controller.exposure = candidate
                self._exposure_settle_frames = max(self._exposure_settle_frames, 6)
                LOGGER.info(
                    "Start-Farbtest setzt Belichtung %s → %s (Weiß-Peak %.1f)",
                    old_exposure,
                    candidate,
                    white_peak,
                )
        self._exposure_settle_frames = max(self._exposure_settle_frames, 5)
        LOGGER.info(
            "Start-Farbtest übernommen: %s (Sicherheit %.0f%%)",
            "Rotfilter" if self._active_filter_profile == "red_filter" else "Ohne Filter",
            confidence * 100.0,
        )

    def set_calibration_mode(self, enabled: bool) -> bool:
        """Friert Belichtung und Trefferpuffer während optischer Messbilder ein."""

        enabled = bool(enabled)
        if enabled == self._calibration_mode:
            return False
        self._calibration_mode = enabled
        with self._detector_lock:
            self.detector = self._make_detector()
        with self._state_lock:
            self._pending_shots.clear()
        if not enabled:
            self.light_controller = AmbientLightController(
                initial_exposure=self.light_controller.exposure
            )
            self._exposure_settle_frames = max(self._exposure_settle_frames, 5)
        LOGGER.info("Optischer Kalibriermodus: %s", "aktiv" if enabled else "beendet")
        return True

    def reload_calibration(self) -> None:
        self._calibration_mtime = -1.0
        self._projection_mask = None
        self._overview_navigation_mask = None
        self._refresh_projection_mask()
        self.reset_state()

    def set_overview_navigation(self, enabled: bool) -> bool:
        """Erlaubt Laserpulse in den seitlichen Kamerazonen nur im Hauptmenü."""

        enabled = bool(enabled)
        if enabled == self._overview_navigation_enabled:
            return False
        self._overview_navigation_enabled = enabled
        self.reset_state()
        LOGGER.info(
            "Seitliche Laser-Seitenwahl: %s",
            "aktiv" if enabled else "aus",
        )
        return True

    def reset_state(self) -> None:
        """Reset stateful detection helpers after parameter changes."""
        with self._detector_lock:
            self.detector = self._make_detector()
        with self._state_lock:
            self._pending_shots.clear()

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        """Schaltet den Animationsfilter atomar vor der Flankenerkennung um."""

        if enabled == self._moorhuhn_filter_enabled:
            return
        self._moorhuhn_filter_enabled = enabled
        with self._detector_lock:
            self.detector = self._make_detector()
        with self._state_lock:
            self._pending_shots.clear()
        LOGGER.info("Moorhuhn-Animationsfilter: %s", "aktiv" if enabled else "aus")

    def _configure_camera_controls(self, device_index: int) -> None:
        controls = (
            "power_line_frequency=1,"
            "auto_exposure=1,"
            "exposure_dynamic_framerate=0,"
            f"exposure_time_absolute={self.light_controller.exposure},"
            "white_balance_automatic=0,"
            "white_balance_temperature=4000,"
            "focus_automatic_continuous=0,"
            "gain=0"
        )
        try:
            result = subprocess.run(
                [
                    "v4l2-ctl",
                    "-d",
                    f"/dev/video{device_index}",
                    f"--set-ctrl={controls}",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode:
                LOGGER.warning("Kamerasteuerung nicht vollständig gesetzt: %s", result.stderr.strip())
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            LOGGER.warning("v4l2-Kamerasteuerung übersprungen: %s", exc)

    def _apply_camera_exposure(self, exposure: int) -> bool:
        try:
            result = subprocess.run(
                [
                    "v4l2-ctl",
                    "-d",
                    f"/dev/video{self.settings.camera.device_index}",
                    f"--set-ctrl=exposure_time_absolute={exposure}",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0:
                return True
            LOGGER.warning(
                "Automatische Belichtung konnte nicht gesetzt werden: %s",
                result.stderr.strip(),
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            LOGGER.warning("Automatische Belichtung übersprungen: %s", exc)
        return False

    def _refresh_projection_mask(self) -> None:
        try:
            mtime = CALIBRATION_FILE.stat().st_mtime
        except OSError:
            self._projection_mask = None
            self._overview_navigation_mask = None
            return
        if mtime == self._calibration_mtime and self._projection_mask is not None:
            return
        data = load_calibration()
        points = data.get("camera_points") if data else None
        if not points or len(points) != 4:
            self._projection_mask = None
            self._overview_navigation_mask = None
            return

        source_size = data.get("source_size") if data else None
        source_width = max(
            1,
            int(source_size[0])
            if isinstance(source_size, list) and len(source_size) == 2
            else self.settings.camera.width,
        )
        source_height = max(
            1,
            int(source_size[1])
            if isinstance(source_size, list) and len(source_size) == 2
            else self.settings.camera.height,
        )
        self._projection_mask, self._overview_navigation_mask = build_camera_detection_masks(
            [tuple(int(value) for value in point) for point in points],
            (self.processing_width, self.processing_height),
            (source_width, source_height),
        )
        self._calibration_mtime = mtime

    def start(self) -> None:
        cam = self.settings.camera
        self.cap = cv2.VideoCapture(cam.device_index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            raise RuntimeError("Kamera konnte nicht geöffnet werden")
        fourcc = (cam.fourcc or "MJPG")[:4].ljust(4)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam.height)
        self.cap.set(cv2.CAP_PROP_FPS, cam.fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._configure_camera_controls(cam.device_index)
        self.actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.actual_fps = float(self.cap.get(cv2.CAP_PROP_FPS))
        if self.actual_width > 0 and self.actual_height > 0:
            self.processing_width = min(640, self.actual_width)
            self.processing_height = max(
                1, int(round(self.actual_height * self.processing_width / self.actual_width))
            )
        self._refresh_projection_mask()
        actual_fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        actual_fourcc_text = "".join(chr((actual_fourcc >> (8 * i)) & 0xFF) for i in range(4))
        LOGGER.info(
            "Kamera gestartet (%s x %s @ %.1ffps, %s)",
            self.actual_width,
            self.actual_height,
            self.actual_fps,
            actual_fourcc_text,
        )
        self._stop_event.clear()
        self._capture_error = None
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="laser-camera",
            daemon=True,
        )
        self._capture_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
        self.cap = None
        self._capture_thread = None
        LOGGER.info("Kamera gestoppt")

    def read(self) -> LaserDetection:
        if self.cap is None or self._capture_thread is None:
            raise RuntimeError("Kamera nicht initialisiert")
        if not self._frame_ready.wait(timeout=2.0):
            raise RuntimeError(self._capture_error or "Kein Kamerabild empfangen")
        with self._state_lock:
            if self._pending_shots:
                return self._pending_shots.popleft()
            if self._latest_detection is None:
                raise RuntimeError(self._capture_error or "Kein Kamerabild verfügbar")
            return self._latest_detection

    def _capture_loop(self) -> None:
        last_frame_time = time.monotonic()
        try:
            while not self._stop_event.is_set():
                if self.cap is None:
                    return
                ret, frame = self.cap.read()
                if not ret:
                    raise RuntimeError("Frame konnte nicht gelesen werden")
                detection = self._process_frame(frame)
                now = time.monotonic()
                elapsed = now - last_frame_time
                last_frame_time = now
                if elapsed > 0:
                    instant_fps = 1.0 / elapsed
                    self.processing_fps = (
                        instant_fps
                        if self.processing_fps <= 0
                        else self.processing_fps * 0.9 + instant_fps * 0.1
                    )
                with self._state_lock:
                    if detection.shot:
                        self._pending_shots.append(detection)
                        self._latest_detection = replace(detection, shot=False)
                    else:
                        self._latest_detection = detection
                self._frame_ready.set()
        except Exception as exc:
            self._capture_error = str(exc)
            self._frame_ready.set()
            LOGGER.exception("Kamera-Thread beendet: %s", exc)

    def _process_frame(self, frame: np.ndarray) -> LaserDetection:
        processing_frame = cv2.resize(
            frame,
            (self.processing_width, self.processing_height),
            interpolation=cv2.INTER_AREA,
        )
        now_ms = time.monotonic() * 1000.0
        if now_ms - self._last_mask_refresh_ms >= 15000.0:
            self._refresh_projection_mask()
            self._last_mask_refresh_ms = now_ms
        with self._detector_lock:
            active_profile, filter_confidence, profile_changed = self.filter_classifier.update(
                processing_frame,
                now_ms,
                self._projection_mask,
            )
            if profile_changed or active_profile != self._active_filter_profile:
                self._active_filter_profile = active_profile
                self.detector = self._make_detector()
        if profile_changed:
            with self._state_lock:
                self._pending_shots.clear()
            self._exposure_settle_frames = max(self._exposure_settle_frames, 4)
            LOGGER.info(
                "Kamerafilterprofil automatisch gewechselt: %s (Sicherheit %.0f%%)",
                "Rotfilter" if active_profile == "red_filter" else "Ohne Filter",
                filter_confidence * 100.0,
            )
        decision = None
        if not self._calibration_mode:
            decision = self.light_controller.update(
                processing_frame,
                now_ms,
                self._projection_mask,
            )
        if decision is not None:
            metrics = decision.metrics
            if decision.changed and self._apply_camera_exposure(decision.exposure):
                self._exposure_settle_frames = 4
            if decision.changed or now_ms - self._last_light_log_ms >= 15000.0:
                LOGGER.info(
                    "Lichtautomatik: Belichtung=%s, Leinwand=%.0f/%.0f/%.0f, "
                    "Umgebung=%.0f, Verhältnis=%.2f, Ausbrennen=%.1f%%",
                    decision.exposure,
                    metrics.screen_median,
                    metrics.screen_highlight,
                    metrics.screen_peak,
                    metrics.outside_median,
                    metrics.screen_to_ambient_ratio,
                    metrics.clipped_fraction * 100.0,
                )
                self._last_light_log_ms = now_ms
        with self._detector_lock:
            if self._exposure_settle_frames > 0:
                self.detector.reset()
                self._exposure_settle_frames -= 1
            detection_mask = (
                self._overview_navigation_mask
                if self._overview_navigation_enabled
                and self._overview_navigation_mask is not None
                else self._projection_mask
            )
            pulse = self.detector.process(
                processing_frame,
                now_ms,
                detection_mask,
            )

        mask_preview = None
        frame_preview = None
        try:
            preview_small = cv2.resize(pulse.mask, (200, 112), interpolation=cv2.INTER_NEAREST)
            mask_preview = cv2.cvtColor(preview_small, cv2.COLOR_GRAY2RGB)
        except Exception:
            LOGGER.debug("Konnte Masken-Vorschau nicht erzeugen", exc_info=True)
        try:
            frame_preview = cv2.cvtColor(processing_frame, cv2.COLOR_BGR2RGB)
        except Exception:
            LOGGER.debug("Konnte Kamera-Vorschau nicht erzeugen", exc_info=True)
        return LaserDetection(
            point=pulse.point,
            area=pulse.area,
            confidence=pulse.confidence,
            frame_ts=time.time(),
            mask_preview=mask_preview,
            frame_preview=frame_preview,
            shot=pulse.shot,
            peak_red_excess=pulse.peak_red_excess,
            peak_delta=pulse.peak_delta,
            red_threshold=pulse.red_threshold,
            delta_threshold=pulse.delta_threshold,
            active_filter_profile=self._active_filter_profile,
            filter_confidence=filter_confidence,
            observed_point=pulse.observed_point,
            observed_area=pulse.observed_area,
            observed_peak_red=pulse.observed_peak_red,
            observed_peak_delta=pulse.observed_peak_delta,
            observed_peak_value=pulse.observed_peak_value,
        )
