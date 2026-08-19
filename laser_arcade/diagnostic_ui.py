from __future__ import annotations

import ctypes
import ctypes.util
import logging
import math
import random
import re
import time
import textwrap
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Optional, Tuple

import cv2
import numpy as np
import pygame

from .auto_alignment import (
    AlignmentResult,
    PrecisionAlignmentResult,
    StartupOpticalResult,
    VerificationResult,
    analyze_startup_color_response,
    detect_projection_quad,
    detect_verification_markers,
    refine_homography_from_precision_markers,
    startup_color_rects,
)
from .calibration import (
    apply_homography,
    calculate_homography,
    compute_homography,
    load_homography,
    validate_camera_quad,
)
from .apps.cans import CansApp
from .apps.chickens import ChickenApp
from .apps.clay_shooting import ClayShootingApp
from .apps.reaction import ReactionApp
from .apps.target_range import TargetRangeApp
from .apps.timed_shooting import TimedShootingApp
from .apps.water_alarm import WaterAlarmApp
from .apps.ocean_cleanup import OceanCleanupApp
from .apps.tobia_duel import TobiaDuelApp
from .apps.kids_arcade import (
    AlienAlarmApp,
    BalloonHuntApp,
    ColorMemoryApp,
    MathDuelApp,
    StarHuntApp,
    TreasureHuntApp,
)
from .apps.duel_games import (
    ConnectFourApp,
    DotsBoxesApp,
    MemoryDuelApp,
    NimDuelApp,
    ReversiLightApp,
    TicTacToeApp,
    load_duel_sprite,
)
from .apps.arcade_leaderboard import ArcadeLeaderboardOverlay
from .apps.arcade_common import (
    build_theme_background,
    build_vintage_enamel_panel,
    draw_button,
    draw_vintage_enamel_panel,
    draw_frame,
    draw_translucent_panel,
    load_target_sprite,
    limit_projected_brightness,
    nearest_laser_button,
    neutralize_laser_red,
)
from .config import LaserProfile, Settings, save_calibration, save_settings
from .constants import (
    APP_DIR,
    ARCADE_LEADERBOARD_FILE,
    TARGET_HISTORY_FILE,
    WATER_ALARM_LEADERBOARD_FILE,
    WEAPON_CALIBRATION_FILE,
)
from .laser_tracker import LaserDetection, LaserTracker
from .weapon_calibration import (
    WeaponCalibration,
    fit_weapon_calibration,
    load_weapon_calibration,
    save_weapon_calibration,
)


@dataclass
class MenuMouse:
    """Eine perspektivisch bewegte Maus auf dem Holzboden der Übersicht."""

    active: bool = False
    x: float = 0.0
    y: float = 724.0
    target_x: float = 0.0
    target_y: float = 724.0
    direction: int = 1
    heading_angle: int = 0
    speed: float = 72.0
    size_factor: float = 1.0
    state: str = "moving"
    state_until: float = 0.0
    depart_at: float = 0.0
    pauses_used: int = 0
    exiting: bool = False
    spawn_at: float = 0.0
    last_update: float = 0.0
    current_rect: Optional[pygame.Rect] = None
    current_frame: int = 0
    current_mask: Optional[pygame.mask.Mask] = None

LOGGER = logging.getLogger(__name__)


class XFixesCursorController:
    """Steuert den Cursor direkt auf dem SDL-X11-Fenster."""

    def __init__(self) -> None:
        self.display = None
        self.window = 0
        self.hidden = False
        self.x11 = None
        self.xfixes = None
        if pygame.display.get_driver() != "x11":
            return
        try:
            window = int(pygame.display.get_wm_info().get("window", 0))
            x11_path = ctypes.util.find_library("X11")
            xfixes_path = ctypes.util.find_library("Xfixes")
            if not window or not x11_path or not xfixes_path:
                raise RuntimeError("X11-Fenster oder XFixes-Bibliothek fehlt")
            self.x11 = ctypes.CDLL(x11_path)
            self.xfixes = ctypes.CDLL(xfixes_path)
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.x11.XOpenDisplay.restype = ctypes.c_void_p
            self.x11.XFlush.argtypes = [ctypes.c_void_p]
            self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            self.xfixes.XFixesHideCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self.xfixes.XFixesShowCursor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self.display = self.x11.XOpenDisplay(None)
            if not self.display:
                raise RuntimeError("X11-Anzeige konnte nicht geöffnet werden")
            self.window = window
            LOGGER.info("Direkte XFixes-Zeigersteuerung aktiv für Fenster 0x%x", window)
        except Exception as exc:
            LOGGER.warning("Direkte XFixes-Zeigersteuerung nicht verfügbar: %s", exc)
            self.display = None
            self.window = 0

    def hide(self, force: bool = False) -> None:
        if not self.display or not self.window or (self.hidden and not force):
            return
        self.xfixes.XFixesHideCursor(self.display, self.window)
        self.x11.XFlush(self.display)
        self.hidden = True

    def show(self) -> None:
        if not self.display or not self.window or not self.hidden:
            return
        self.xfixes.XFixesShowCursor(self.display, self.window)
        self.x11.XFlush(self.display)
        self.hidden = False

    def close(self) -> None:
        if not self.display:
            return
        # Das Schließen der X11-Verbindung hebt alle von diesem Client
        # gesetzten XFixes-Sperren automatisch auf. Ein zusätzliches
        # XFixesShowCursor kann unter Xwayland mit BadMatch abbrechen.
        self.x11.XCloseDisplay(self.display)
        self.display = None
        self.window = 0
        self.hidden = False


@dataclass
class ShotRecord:
    number: int
    camera_point: Tuple[int, int]
    screen_point: Optional[Tuple[int, int]]
    confidence: float
    timestamp: float


class AutomaticAligner:
    """Wartet auf den Beamer und vermisst Fläche, Rahmen und Farbwiedergabe."""

    PROJECTOR_STABLE_SECONDS = 3.0
    PROJECTOR_MIN_RESPONSE = 12.0

    def __init__(self, screen_size: Tuple[int, int], previous_homography=None):
        self.screen_size = screen_size
        self.homography = previous_homography
        self.corners: Optional[np.ndarray] = None
        self.confidence = 0.0
        self.phase = "idle"
        self.phase_started = 0.0
        self.dark_frames: list[np.ndarray] = []
        self.bright_frames: list[np.ndarray] = []
        self.verification_dark_frames: list[np.ndarray] = []
        self.marker_frames: list[np.ndarray] = []
        self.precision_dark_frames: list[np.ndarray] = []
        self.precision_marker_frames: list[np.ndarray] = []
        self.color_dark_frames: list[np.ndarray] = []
        self.color_frames: list[np.ndarray] = []
        self.projector_reference_frames: list[np.ndarray] = []
        self.projector_last_frame: Optional[np.ndarray] = None
        self.projector_stable_since: Optional[float] = None
        self.projector_response_history: Deque[tuple[float, float]] = deque(maxlen=240)
        self.projector_response = 0.0
        self.projector_stability = 0.0
        self.after_projector_phase = "dark_settle"
        self.precision: Optional[PrecisionAlignmentResult] = None
        self.optical_result: Optional[StartupOpticalResult] = None
        self.pending_source_size: Optional[Tuple[int, int]] = None
        self.message = "Automatische Ausrichtung bereit"
        self.last_result: Optional[AlignmentResult] = None
        self.verification: Optional[VerificationResult] = None
        self.preserve_alignment_on_verification_failure = False

    @property
    def active(self) -> bool:
        return self.phase in {
            "projector_dark_settle",
            "projector_dark_capture",
            "projector_wait",
            "dark_settle",
            "dark_capture",
            "bright_settle",
            "bright_capture",
            "verify_dark_settle",
            "verify_dark_capture",
            "marker_settle",
            "marker_capture",
            "precision_dark_settle",
            "precision_dark_capture",
            "precision_marker_settle",
            "precision_marker_capture",
            "color_dark_settle",
            "color_dark_capture",
            "color_settle",
            "color_capture",
        }

    @property
    def display_color(self) -> Tuple[int, int, int]:
        if self.phase in {"projector_wait", "bright_settle", "bright_capture"}:
            return (255, 255, 255)
        return (0, 0, 0)

    @property
    def shows_markers(self) -> bool:
        return self.phase in {"marker_settle", "marker_capture"}

    @property
    def shows_precision_frame(self) -> bool:
        return self.phase in {"precision_marker_settle", "precision_marker_capture"}

    @property
    def shows_color_test(self) -> bool:
        return self.phase in {"color_settle", "color_capture"}

    @property
    def marker_screen_points(self) -> list[Tuple[int, int]]:
        width, height = self.screen_size
        inset = max(42, int(min(width, height) * 0.065))
        return [
            (inset, inset),
            (width - 1 - inset, inset),
            (width - 1 - inset, height - 1 - inset),
            (inset, height - 1 - inset),
        ]

    @property
    def precision_screen_points(self) -> list[Tuple[int, int]]:
        width, height = self.screen_size
        inset = max(38, int(min(width, height) * 0.055))
        left, right = inset, width - 1 - inset
        top, bottom = inset, height - 1 - inset
        x1 = int(round(left + (right - left) / 3.0))
        x2 = int(round(left + 2.0 * (right - left) / 3.0))
        y1 = int(round(top + (bottom - top) / 3.0))
        y2 = int(round(top + 2.0 * (bottom - top) / 3.0))
        return [
            (left, top), (x1, top), (x2, top), (right, top),
            (right, y1), (right, y2),
            (right, bottom), (x2, bottom), (x1, bottom), (left, bottom),
            (left, y2), (left, y1),
        ]

    def start(self, now: Optional[float] = None) -> None:
        self.phase = "projector_dark_settle"
        self.phase_started = now if now is not None else time.monotonic()
        self.dark_frames.clear()
        self.bright_frames.clear()
        self.verification_dark_frames.clear()
        self.marker_frames.clear()
        self.precision_dark_frames.clear()
        self.precision_marker_frames.clear()
        self.color_dark_frames.clear()
        self.color_frames.clear()
        self.projector_reference_frames.clear()
        self.projector_last_frame = None
        self.projector_stable_since = None
        self.projector_response_history.clear()
        self.projector_response = 0.0
        self.projector_stability = 0.0
        self.after_projector_phase = "dark_settle"
        self.precision = None
        self.optical_result = None
        self.pending_source_size = None
        self.verification = None
        self.preserve_alignment_on_verification_failure = False
        self.message = "Beamerstart wird vorbereitet"

    def start_verification(
        self,
        homography: np.ndarray,
        corners: list[Tuple[int, int]],
        now: Optional[float] = None,
    ) -> None:
        """Prüft eine manuell gesperrte Ausrichtung, ohne sie bei Fehler zu löschen."""

        self.homography = homography.copy()
        self.corners = np.asarray(corners, dtype=np.float32)
        self.confidence = 1.0
        self.verification_dark_frames.clear()
        self.marker_frames.clear()
        self.precision_dark_frames.clear()
        self.precision_marker_frames.clear()
        self.color_dark_frames.clear()
        self.color_frames.clear()
        self.projector_reference_frames.clear()
        self.projector_last_frame = None
        self.projector_stable_since = None
        self.projector_response_history.clear()
        self.after_projector_phase = "verify_dark_settle"
        self.verification = None
        self.precision = None
        self.optical_result = None
        self.pending_source_size = None
        self.preserve_alignment_on_verification_failure = True
        self._advance(
            "projector_dark_settle",
            now if now is not None else time.monotonic(),
            "Beamerstart vor Prüfung der manuellen Ecken",
        )

    def feed(self, frame_rgb: np.ndarray, now: float) -> bool:
        """Liefert True genau dann, wenn ein Ausrichtversuch beendet wurde."""

        elapsed = now - self.phase_started
        if self.phase == "projector_dark_settle" and elapsed >= 0.9:
            self._advance("projector_dark_capture", now, "Umgebungslicht wird gemessen")
        elif self.phase == "projector_dark_capture":
            self.projector_reference_frames.append(frame_rgb.copy())
            if elapsed >= 0.55 and len(self.projector_reference_frames) >= 10:
                self._advance(
                    "projector_wait",
                    now,
                    "Warte auf ein helles, stabiles Beamerbild",
                )
        elif self.phase == "projector_wait":
            if self._projector_is_ready(frame_rgb, now):
                if self.after_projector_phase == "verify_dark_settle":
                    self._advance(
                        "verify_dark_settle",
                        now,
                        "Beamer stabil – manuelle Ecken werden geprüft",
                    )
                else:
                    self._advance("dark_settle", now, "Beamer stabil – Schwarzbild wird gemessen")
        elif self.phase == "dark_settle" and elapsed >= 0.55:
            self._advance("dark_capture", now, "Schwarzbild wird aufgenommen")
        elif self.phase == "dark_capture":
            self.dark_frames.append(frame_rgb.copy())
            if elapsed >= 0.40 and len(self.dark_frames) >= 8:
                self._advance("bright_settle", now, "Weißbild wird gemessen")
        elif self.phase == "bright_settle" and elapsed >= 0.80:
            self._advance("bright_capture", now, "Projektionsfläche wird aufgenommen")
        elif self.phase == "bright_capture":
            self.bright_frames.append(frame_rgb.copy())
            if elapsed >= 0.40 and len(self.bright_frames) >= 8:
                return self._finish_alignment(now)
        elif self.phase == "verify_dark_settle" and elapsed >= 0.45:
            self._advance("verify_dark_capture", now, "Eckprüfung: Referenzbild")
        elif self.phase == "verify_dark_capture":
            self.verification_dark_frames.append(frame_rgb.copy())
            if elapsed >= 0.32 and len(self.verification_dark_frames) >= 7:
                self._advance("marker_settle", now, "Vier Eckmarker werden geprüft")
        elif self.phase == "marker_settle" and elapsed >= 0.60:
            self._advance("marker_capture", now, "Eckmarker werden zurückgemessen")
        elif self.phase == "marker_capture":
            self.marker_frames.append(frame_rgb.copy())
            if elapsed >= 0.35 and len(self.marker_frames) >= 7:
                return self._finish_verification(now)
        elif self.phase == "precision_dark_settle" and elapsed >= 0.42:
            self._advance("precision_dark_capture", now, "Rahmenprüfung: Referenzbild")
        elif self.phase == "precision_dark_capture":
            self.precision_dark_frames.append(frame_rgb.copy())
            if elapsed >= 0.32 and len(self.precision_dark_frames) >= 7:
                self._advance(
                    "precision_marker_settle",
                    now,
                    "Leinwandrahmen und zwölf Messpunkte werden geprüft",
                )
        elif self.phase == "precision_marker_settle" and elapsed >= 0.65:
            self._advance("precision_marker_capture", now, "Rahmen wird präzise zurückgemessen")
        elif self.phase == "precision_marker_capture":
            self.precision_marker_frames.append(frame_rgb.copy())
            if elapsed >= 0.38 and len(self.precision_marker_frames) >= 7:
                return self._finish_precision_alignment(now)
        elif self.phase == "color_dark_settle" and elapsed >= 0.42:
            self._advance("color_dark_capture", now, "Lichttest: dunkle Referenz")
        elif self.phase == "color_dark_capture":
            self.color_dark_frames.append(frame_rgb.copy())
            if elapsed >= 0.32 and len(self.color_dark_frames) >= 7:
                self._advance("color_settle", now, "Farben und Rotfilter werden geprüft")
        elif self.phase == "color_settle" and elapsed >= 0.70:
            self._advance("color_capture", now, "Umgebungslicht und Laserreserve werden gemessen")
        elif self.phase == "color_capture":
            self.color_frames.append(frame_rgb.copy())
            if elapsed >= 0.42 and len(self.color_frames) >= 8:
                self._finish_color_test()
                return True
        return False

    def _projector_is_ready(self, frame_rgb: np.ndarray, now: float) -> bool:
        reference = np.mean(
            np.stack(self.projector_reference_frames).astype(np.float32), axis=0
        ).astype(np.uint8)
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        reference_gray = cv2.cvtColor(reference, cv2.COLOR_RGB2GRAY)
        response = cv2.absdiff(gray, reference_gray)
        # Das 82. Perzentil erkennt auch eine kleinere, vollständig sichtbare
        # Leinwand, verlangt aber weiterhin eine breite Bildänderung und
        # ignoriert einzelne Lampen oder Reflexe.
        self.projector_response = float(np.percentile(response, 82.0))
        self.projector_response_history.append((now, self.projector_response))
        while (
            self.projector_response_history
            and now - self.projector_response_history[0][0] > self.PROJECTOR_STABLE_SECONDS + 0.25
        ):
            self.projector_response_history.popleft()
        small = cv2.resize(gray, (80, 45), interpolation=cv2.INTER_AREA)
        if self.projector_last_frame is None:
            stability = float("inf")
        else:
            stability = float(
                np.mean(np.abs(small.astype(np.float32) - self.projector_last_frame.astype(np.float32)))
            )
        self.projector_last_frame = small
        self.projector_stability = stability if np.isfinite(stability) else 99.0
        responsive = self.projector_response >= self.PROJECTOR_MIN_RESPONSE
        history_span = (
            now - self.projector_response_history[0][0]
            if self.projector_response_history else 0.0
        )
        values = [value for _, value in self.projector_response_history]
        response_drift = max(values) - min(values) if values else float("inf")
        drift_limit = max(4.0, self.projector_response * 0.055)
        stable = stability <= 2.8 and response_drift <= drift_limit
        if responsive and stable:
            held = min(history_span, self.PROJECTOR_STABLE_SECONDS)
            self.message = (
                f"Beamerbild erkannt – stabilisiert sich {min(held, self.PROJECTOR_STABLE_SECONDS):.1f}"
                f"/{self.PROJECTOR_STABLE_SECONDS:.0f} s"
            )
            return history_span >= self.PROJECTOR_STABLE_SECONDS
        self.message = "Warte auf den Beamer – das Bild darf in Ruhe warm werden"
        return False

    def _advance(self, phase: str, now: float, message: str) -> None:
        self.phase = phase
        self.phase_started = now
        self.message = message

    def _finish_alignment(self, now: float) -> bool:
        try:
            result = detect_projection_quad(self.dark_frames, self.bright_frames)
            width, height = self.screen_size
            screen_points = [
                (0, 0),
                (width - 1, 0),
                (width - 1, height - 1),
                (0, height - 1),
            ]
            camera_points = [tuple(int(round(v)) for v in point) for point in result.corners]
            frame_height, frame_width = self.bright_frames[-1].shape[:2]
            # Noch nicht speichern: Erst Eck-, Rahmen- und Farbprüfung müssen
            # erfolgreich sein. So bleibt eine zuvor funktionierende
            # Kalibrierung bei einem noch kalten Beamer unangetastet.
            data = calculate_homography(camera_points, screen_points)
            data.source_size = (frame_width, frame_height)
            self.homography = data.homography
            self.corners = result.corners
            self.confidence = result.confidence
            self.last_result = result
            self.pending_source_size = (frame_width, frame_height)
            APP_DIR.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(APP_DIR / "alignment_difference.png"), result.difference)
            cv2.imwrite(str(APP_DIR / "alignment_mask.png"), result.mask)
            LOGGER.info(
                "Automatische Ausrichtung erfolgreich: confidence=%.3f corners=%s",
                result.confidence,
                camera_points,
            )
            self._advance("verify_dark_settle", now, "Ausrichtung gefunden – Ecken werden geprüft")
            return False
        except Exception as exc:
            self._fail("Automatische Ausrichtung fehlgeschlagen", exc)
            return True

    def _finish_verification(self, now: float) -> bool:
        try:
            if self.homography is None:
                raise RuntimeError("Keine Homographie für die Eckprüfung vorhanden")
            verification = detect_verification_markers(
                self.verification_dark_frames,
                self.marker_frames,
                self.homography,
                self.marker_screen_points,
            )
            self.verification = verification
            self.message = f"4/4 Ecken bestätigt · max. Abweichung {verification.max_error:.0f} px"
            cv2.imwrite(str(APP_DIR / "alignment_verification_mask.png"), verification.mask)
            LOGGER.info(
                "Eckprüfung erfolgreich: 4/4 max_error=%.2f errors=%s",
                verification.max_error,
                [round(float(value), 2) for value in verification.errors],
            )
            self._advance(
                "precision_dark_settle",
                now,
                "Ecken bestätigt – vollständiger Rahmen folgt",
            )
            return False
        except Exception as exc:
            self._fail(
                "Eckprüfung fehlgeschlagen",
                exc,
                clear_alignment=not self.preserve_alignment_on_verification_failure,
            )
            return True

    def _finish_precision_alignment(self, now: float) -> bool:
        try:
            if self.homography is None or self.corners is None:
                raise RuntimeError("Keine Grundausrichtung für die Rahmenprüfung vorhanden")
            precision = refine_homography_from_precision_markers(
                self.precision_dark_frames,
                self.precision_marker_frames,
                self.homography,
                self.precision_screen_points,
            )
            self.precision = precision
            cv2.imwrite(str(APP_DIR / "alignment_precision_mask.png"), precision.mask)
            if not self.preserve_alignment_on_verification_failure:
                self.homography = precision.homography
                width, height = self.screen_size
                screen_corners = np.asarray(
                    [(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)],
                    dtype=np.float32,
                )
                inverse = np.linalg.inv(self.homography)
                refined_corners = cv2.perspectiveTransform(
                    screen_corners.reshape(-1, 1, 2), inverse
                ).reshape(-1, 2)
                frame_height, frame_width = self.precision_marker_frames[-1].shape[:2]
                valid, _ = validate_camera_quad(
                    [tuple(int(round(value)) for value in point) for point in refined_corners],
                    (frame_width, frame_height),
                )
                if valid:
                    self.corners = refined_corners
            LOGGER.info(
                "Rahmenprüfung erfolgreich: %s/12, Mittel=%.2f px, Maximum=%.2f px%s",
                len(precision.errors),
                precision.mean_error,
                precision.max_error,
                " (manuelle Ausrichtung unverändert)"
                if self.preserve_alignment_on_verification_failure else "",
            )
            self._advance("color_dark_settle", now, "Rahmen präzisiert – Lichttest folgt")
            return False
        except Exception as exc:
            self._fail(
                "Rahmenprüfung fehlgeschlagen",
                exc,
                clear_alignment=not self.preserve_alignment_on_verification_failure,
            )
            return True

    def _finish_color_test(self) -> None:
        try:
            if self.homography is None:
                raise RuntimeError("Keine Ausrichtung für den Farbtest vorhanden")
            self.optical_result = analyze_startup_color_response(
                self.color_dark_frames,
                self.color_frames,
                self.homography,
                self.screen_size,
            )
            result = self.optical_result
            if not self.preserve_alignment_on_verification_failure:
                if self.corners is None:
                    raise RuntimeError("Keine Leinwandecken zum Speichern vorhanden")
                width, height = self.screen_size
                camera_points = [
                    tuple(int(round(value)) for value in point) for point in self.corners
                ]
                screen_points = [
                    (0, 0), (width - 1, 0),
                    (width - 1, height - 1), (0, height - 1),
                ]
                source_size = self.pending_source_size
                if source_size is None and self.color_frames:
                    frame_height, frame_width = self.color_frames[-1].shape[:2]
                    source_size = (frame_width, frame_height)
                save_calibration(
                    self.homography,
                    camera_points,
                    screen_points,
                    alignment_mode="automatic",
                    source_size=source_size,
                )
            self.phase = "success"
            self.message = (
                f"Ausrichtung und Licht geprüft · "
                f"{'Rotfilter' if result.active_filter_profile == 'red_filter' else 'ohne Filter'}"
            )
            LOGGER.info(
                "Optischer Starttest: Profil=%s Sicherheit=%.0f%% Umgebung=%.1f "
                "Weiß=%.1f Peak=%.1f Rotverhältnis=%.2f Laserreserve=%.1f",
                result.active_filter_profile,
                result.filter_confidence * 100.0,
                result.ambient_luma,
                result.white_luma,
                result.white_peak,
                result.red_ratio,
                result.laser_headroom,
            )
        except Exception as exc:
            self._fail(
                "Automatischer Licht- und Filtertest fehlgeschlagen",
                exc,
                clear_alignment=not self.preserve_alignment_on_verification_failure,
            )

    def _fail(self, prefix: str, exc: Exception, *, clear_alignment: bool = True) -> None:
        if clear_alignment:
            self.homography = None
            self.corners = None
            self.confidence = 0.0
        self.verification = None
        self.phase = "failed"
        self.message = str(exc)
        LOGGER.exception("%s: %s", prefix, exc)


class LaserDiagnosticUI:
    """Reduzierte Oberfläche für Ausrichtung und Schusserkennung."""

    CURSOR_HIDE_DELAY = 1.5
    MOORHUHN_EASTER_CODE = (1, 6, 2, 5)

    BG = (13, 17, 25)
    PANEL = (25, 32, 45)
    BORDER = (72, 91, 116)
    TEXT = (235, 241, 248)
    MUTED = (155, 172, 194)
    CYAN = (35, 215, 235)
    GREEN = (75, 220, 135)
    # Rot darf die Kameraansicht selbst nicht enthalten: Sonst würde die
    # projizierte Diagnoseoberfläche zum Schusskandidaten. Kandidaten und Fehler
    # werden deshalb bewusst blau/violett markiert.
    YELLOW = (75, 145, 255)
    RED = (185, 125, 255)
    # Das Zielbild verwendet absichtlich keinen roten RGB-Anteil. Bei einem
    # DLP-Beamer können selbst weiße Linien als kurze rote Farbblitze in der
    # Kamera erscheinen und damit einen falschen Lasertreffer auslösen.
    TARGET_BG = (0, 8, 16)
    TARGET_CYAN = (0, 205, 245)
    TARGET_GREEN = (0, 225, 120)
    TARGET_MUTED = (0, 82, 118)
    # Fließtext muss auch auf einer hellen Leinwand und aus größerer Entfernung
    # lesbar bleiben. Der Farbton bleibt bewusst laserneutral (kein Rotanteil).
    TARGET_BODY = (0, 158, 190)
    TARGET_DARK_TEXT = (0, 22, 34)
    DETECTION_TEST_COLORS = (
        ("DUNKEL", (0, 8, 16)),
        ("BLAU", (0, 42, 92)),
        ("TÜRKIS", (0, 102, 126)),
        ("GRÜN", (0, 112, 72)),
        ("GRAU", (86, 102, 116)),
        ("HELL", (158, 174, 184)),
    )

    @classmethod
    def _camera_test_label_color(
        cls, background: Tuple[int, int, int]
    ) -> Tuple[int, int, int]:
        """Wählt eine laserneutrale, deutlich kontrastierende Feldbeschriftung."""

        return cls.TARGET_DARK_TEXT if max(background) >= 100 else cls.TARGET_CYAN

    @classmethod
    def _camera_detection_verdict(
        cls,
        tested: int,
        safe: int,
        total_colors: int,
        quiet_completed: bool,
        quiet_false_triggers: int,
    ) -> tuple[str, Tuple[int, int, int]]:
        """Liefert die nächste eindeutige Aktion der geführten Schussprüfung."""

        if tested < 3:
            remaining = 3 - tested
            text = (
                "Noch eine weitere Farbe für einen Vorschlag testen"
                if remaining == 1
                else f"Noch {remaining} weitere Farben für einen Vorschlag testen"
            )
            return text, cls.TARGET_CYAN
        if tested < total_colors:
            remaining = total_colors - tested
            rest = "eine Testfläche" if remaining == 1 else f"{remaining} Testflächen"
            text = (
                f"Vorschlag bereit · noch {rest} prüfen"
                if safe == tested
                else f"Vorschlag nutzen · danach noch {rest} prüfen"
            )
            return text, cls.TARGET_CYAN if safe == tested else cls.RED
        if safe == tested and quiet_completed and quiet_false_triggers == 0:
            return "EINSTELLUNG ZUVERLÄSSIG", cls.TARGET_GREEN
        if safe == tested:
            return "Treffer gut · jetzt ohne Schuss prüfen", cls.TARGET_CYAN
        return "Empfindlichkeit anpassen oder Vorschlag nutzen", cls.RED

    def __init__(
        self,
        screen: pygame.Surface,
        settings: Settings,
        tracker: LaserTracker,
        weapon_calibration_path: Optional[Path] = WEAPON_CALIBRATION_FILE,
        target_history_path: Optional[Path] = TARGET_HISTORY_FILE,
        water_alarm_leaderboard_path: Optional[Path] = WATER_ALARM_LEADERBOARD_FILE,
        arcade_leaderboard_path: Optional[Path] = ARCADE_LEADERBOARD_FILE,
    ):
        self.screen = screen
        self.settings = settings
        self.tracker = tracker
        stored = load_homography()
        self.aligner = AutomaticAligner(screen.get_size(), stored.homography)
        self.manual_alignment_active = bool(
            stored.alignment_mode == "manual"
            and stored.homography is not None
            and len(stored.camera_points) == 4
        )
        if self.manual_alignment_active:
            self.aligner.start_verification(stored.homography, stored.camera_points)
        else:
            self.aligner.start()
        self.shots: Deque[ShotRecord] = deque(maxlen=32)
        self.next_shot_number = 1
        self.sighting_step = 0
        self.sighting_phase = "shooting"
        self.stage_shots: list[ShotRecord] = []
        self.completed_groups: list[list[ShotRecord]] = [[] for _ in range(5)]
        self.weapon_calibration_path = weapon_calibration_path
        self.weapon_calibration = (
            load_weapon_calibration(weapon_calibration_path, screen.get_size())
            if weapon_calibration_path is not None
            else WeaponCalibration()
        )
        self.weapon_calibration_message = (
            self._weapon_calibration_text()
            if self.weapon_calibration.active
            else "Waffenkorrektur wird nach dem 17. Schuss gespeichert"
        )
        self.last_detection: Optional[LaserDetection] = None
        self.last_frame_rgb: Optional[np.ndarray] = None
        self.last_mask_rgb: Optional[np.ndarray] = None
        # Die laufende Maske wird mit jedem Kameraframe ersetzt. Für eine
        # nachvollziehbare Einstellung halten wir den letzten bestätigten
        # Schuss-Peak samt Maske und Messwerten separat fest.
        self.last_peak_detection: Optional[LaserDetection] = None
        self.last_peak_mask_rgb: Optional[np.ndarray] = None
        self.armed_at = float("inf")
        # Beim reinen Pistolenbetrieb sofort ausblenden. Eine echte Bewegung
        # macht den Zeiger jederzeit wieder sichtbar.
        self.last_mouse_activity = time.monotonic() - self.CURSOR_HIDE_DELAY
        self.last_mouse_position = pygame.mouse.get_pos()
        self.physical_mouse_connected = self._physical_mouse_connected()
        self.last_mouse_probe = time.monotonic()
        LOGGER.info(
            "Physische Maus erkannt: %s", "ja" if self.physical_mouse_connected else "nein"
        )
        self.cursor_hidden = False
        self.default_cursor = None
        self.transparent_cursor = None
        try:
            self.default_cursor = pygame.mouse.get_cursor()
            blank = (0,) * 8
            self.transparent_cursor = pygame.cursors.Cursor(
                (8, 8), (0, 0), blank, blank
            )
        except pygame.error:
            LOGGER.debug("Transparenter Mauszeiger wird von SDL nicht unterstützt")
        pygame.mouse.set_visible(True)
        self.xfixes_cursor = XFixesCursorController()
        self.font_small = pygame.font.SysFont("Arial", 17)
        self.font = pygame.font.SysFont("Arial", 22)
        self.font_large = pygame.font.SysFont("Arial", 34, bold=True)
        self.font_title = pygame.font.SysFont("Arial", 38, bold=True)
        self.font_target = pygame.font.SysFont("Arial", 46, bold=True)
        self.font_menu_title = pygame.font.SysFont("Arial", 54, bold=True)
        self.font_card = pygame.font.SysFont("Arial", 25, bold=True)
        self.card_font_cache: dict[tuple[str, int], pygame.font.Font] = {}
        self.menu_card_background_cache: dict[tuple[str, tuple[int, int]], pygame.Surface] = {}
        self.view_mode = "diagnostic"
        self.return_view_mode = "menu"
        self.selected_game = ""
        self.close_requested = False
        self.close_pin_active = False
        self.close_pin_digits = ""
        self.close_pin_message = ""
        self.close_pin_return_view = "diagnostic"
        # Die fünf Hauptaktionen bilden unten eine breite, gleichmäßige Reihe.
        # Dadurch sind sie aus Beamerentfernung lesbar und mit der Pistole
        # wesentlich leichter zu treffen als die frühere 27-Pixel-Spalte.
        diagnostic_button_y = screen.get_height() - 78
        diagnostic_button_width = 188
        diagnostic_button_gap = 10
        diagnostic_button_x = 20
        diagnostic_buttons = [
            pygame.Rect(
                diagnostic_button_x + index * (diagnostic_button_width + diagnostic_button_gap),
                diagnostic_button_y,
                diagnostic_button_width,
                58,
            )
            for index in range(5)
        ]
        (
            self.diagnostic_target_button,
            self.diagnostic_settings_button,
            self.diagnostic_sighting_button,
            self.align_button,
            self.diagnostic_close_button,
        ) = diagnostic_buttons
        # Nur noch als unsichtbarer Kompatibilitätsanker für ältere Zustände;
        # im Kamerabild gibt es keinen missverständlichen „Ablauf neu“-Knopf.
        self.clear_button = pygame.Rect(0, 0, 0, 0)
        screen_width, screen_height = self.screen.get_size()
        footer_y = screen_height - 64
        self.target_menu_button = pygame.Rect(screen_width // 2 - 370, footer_y, 140, 36)
        self.target_live_button = pygame.Rect(screen_width // 2 - 220, footer_y, 170, 36)
        self.target_align_button = pygame.Rect(screen_width // 2 - 40, footer_y, 220, 36)
        self.target_clear_button = pygame.Rect(screen_width // 2 + 190, footer_y, 180, 36)
        # Das mechanische Zahnrad sitzt als echtes Bauteil oben rechts in der
        # Bühnenkulisse. Die unsichtbare Trefferfläche bleibt pistolenfreundlich.
        self.menu_settings_button = pygame.Rect(screen_width - 91, 18, 68, 68)
        self.menu_settings_hit_rect = self.menu_settings_button.inflate(42, 34)
        # Die früheren drei Hauptmenüknöpfe sind bewusst stillgelegt.
        self.menu_sighting_button = pygame.Rect(0, 0, 0, 0)
        self.menu_camera_button = pygame.Rect(0, 0, 0, 0)
        self.menu_align_button = pygame.Rect(0, 0, 0, 0)
        self.menu_page = 0
        self.menu_previous_button = pygame.Rect(7, 340, 38, 76)
        self.menu_next_button = pygame.Rect(screen_width - 45, 340, 38, 76)
        self.menu_previous_hit_rect = pygame.Rect(0, 306, 48, 144)
        self.menu_next_hit_rect = pygame.Rect(screen_width - 48, 306, 48, 144)
        self.camera_settings_open = False
        self.camera_settings_tab = "alignment"
        self.camera_settings_advanced = False
        self.camera_settings_message = ""
        self.camera_settings_snapshot: Optional[LaserProfile] = None
        self.camera_settings_draft: Optional[LaserProfile] = None
        self.camera_corner_snapshot: list[Tuple[int, int]] = []
        self.camera_corner_draft: list[Tuple[int, int]] = []
        self.camera_selected_corner = 0
        self.camera_corner_step = 1
        self.camera_drag_corner: Optional[int] = None
        self.camera_drag_slider: Optional[str] = None
        self.camera_original_preview_until = 0.0
        self.camera_settings_dirty = False
        self.camera_corners_dirty = False
        self.camera_detection_dirty = False
        self.camera_detection_samples: dict[str, list[dict[str, float | int | bool]]] = {}
        self.camera_detection_last_peak: Optional[dict[str, float | int | bool | str]] = None
        self.camera_detection_last_capture_at = -1e12
        self.camera_quiet_test_until = 0.0
        self.camera_quiet_test_armed_at = 0.0
        self.camera_quiet_false_triggers = 0
        self.camera_quiet_test_completed = False
        self.alignment_changed_since_sighting = False
        self.advance_button = pygame.Rect(screen_width // 2 - 160, screen_height - 120, 320, 44)
        self.cans_game = CansApp(self.screen)
        shared_sounds = self.cans_game.sounds
        self.clay_game = ClayShootingApp(self.screen, sounds=shared_sounds)
        self.timed_game = TimedShootingApp(self.screen, sounds=shared_sounds)
        self.reaction_game = ReactionApp(self.screen, sounds=shared_sounds)
        self.target_range_game = TargetRangeApp(
            self.screen,
            sounds=shared_sounds,
            history_path=target_history_path,
        )
        self.water_alarm_game = WaterAlarmApp(
            self.screen,
            sounds=shared_sounds,
            leaderboard_path=water_alarm_leaderboard_path,
        )
        self.ocean_cleanup_game = OceanCleanupApp(
            self.screen,
            sounds=shared_sounds,
        )
        self.tobia_duel_game = TobiaDuelApp(
            self.screen,
            sounds=shared_sounds,
        )
        self.balloon_game = BalloonHuntApp(self.screen, sounds=shared_sounds)
        self.alien_game = AlienAlarmApp(self.screen, sounds=shared_sounds)
        self.star_game = StarHuntApp(self.screen, sounds=shared_sounds)
        self.math_game = MathDuelApp(self.screen, sounds=shared_sounds)
        self.color_game = ColorMemoryApp(self.screen, sounds=shared_sounds)
        self.treasure_game = TreasureHuntApp(self.screen, sounds=shared_sounds)
        self.tic_tac_toe_game = TicTacToeApp(self.screen, sounds=shared_sounds)
        self.connect_four_game = ConnectFourApp(self.screen, sounds=shared_sounds)
        self.dots_boxes_game = DotsBoxesApp(self.screen, sounds=shared_sounds)
        self.memory_duel_game = MemoryDuelApp(self.screen, sounds=shared_sounds)
        self.nim_duel_game = NimDuelApp(self.screen, sounds=shared_sounds)
        self.reversi_light_game = ReversiLightApp(self.screen, sounds=shared_sounds)
        self.easter_title_hits = 0
        self.easter_corner_hits: set[str] = set()
        self.easter_moorhuhn_armed = False
        self.easter_moorhuhn_progress = 0
        self.menu_mouse_rng = random.Random(1919)
        self.menu_mouse_source_frames = self._load_menu_mouse_frames()
        self.menu_mouse_behavior_frames = self._load_menu_mouse_behavior_frames()
        self.menu_mouse_frame_cache: dict[
            tuple[str, int, int, int, int], tuple[pygame.Surface, pygame.mask.Mask]
        ] = {}
        self.menu_mouse_foreground: Optional[pygame.Surface] = None
        # Bewusst nur eine selten auftauchende Maus: Das Detail soll die
        # Übersicht beleben, aber nicht als dauerhafte Animation ablenken.
        self.menu_mice = [MenuMouse(spawn_at=time.monotonic() + 9.0)]
        self.menu_mouse_hits = 0
        self.menu_settings_image = self._load_menu_settings_image()
        # Das Loch ist ab V4 direkt in die perspektivische Schrankkulisse
        # eingearbeitet. Dieses Rechteck beschreibt nur seine reale Öffnung
        # für Eintrittspfad und Verdeckung – es wird nichts darübergeklebt.
        self.menu_mouse_hole_image = None
        self.menu_mouse_hole_rect = pygame.Rect(130, 678, 33, 52)
        self.menu_feather_image = self._load_menu_feather_image()
        self.menu_title_image = self._load_menu_title_image()
        self.menu_title_rect = pygame.Rect(331, 8, 362, 64)
        self.arcade_leaderboard = ArcadeLeaderboardOverlay(
            self.screen,
            arcade_leaderboard_path,
        )
        self.standard_game_states = {
            id(game): game.state for game in self._standard_games()
        }
        self.standard_visual_transitions = {
            id(game): False for game in self._standard_games()
        }
        # Alte Moorhuhn-Sonderpfade bleiben während der Migration inaktiv.
        self.chicken_game = None
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False

    def _standard_games(self) -> tuple:
        return (
            self.cans_game,
            self.clay_game,
            self.timed_game,
            self.reaction_game,
            self.target_range_game,
            self.water_alarm_game,
            self.ocean_cleanup_game,
            self.tobia_duel_game,
            self.balloon_game,
            self.alien_game,
            self.star_game,
            self.math_game,
            self.color_game,
            self.treasure_game,
            self.tic_tac_toe_game,
            self.connect_four_game,
            self.dots_boxes_game,
            self.memory_duel_game,
            self.nim_duel_game,
            self.reversi_light_game,
        )

    def _standard_game_for_view(self):
        if self.aligner.phase != "success":
            return None
        games = {
            "cans": self.cans_game,
            "clay": self.clay_game,
            "timed": self.timed_game,
            "reaction": self.reaction_game,
            "range": self.target_range_game,
            "water": self.water_alarm_game,
            "ocean": self.ocean_cleanup_game,
            "tobia": self.tobia_duel_game,
            "balloons": self.balloon_game,
            "aliens": self.alien_game,
            "stars": self.star_game,
            "math": self.math_game,
            "colors": self.color_game,
            "treasure": self.treasure_game,
            "tictactoe": self.tic_tac_toe_game,
            "connect4": self.connect_four_game,
            "dots": self.dots_boxes_game,
            "memory_duel": self.memory_duel_game,
            "nim": self.nim_duel_game,
            "reversi": self.reversi_light_game,
        }
        return games.get(self.view_mode)

    def _stop_standard_games(self, except_game=None) -> None:
        for game in self._standard_games():
            if game is not except_game:
                game.stop()

    def handle_event(self, event: pygame.event.Event) -> bool:
        if self.close_requested:
            return False
        if event.type == pygame.MOUSEMOTION and self.physical_mouse_connected:
            position = tuple(getattr(event, "pos", pygame.mouse.get_pos()))
            delta_x = position[0] - self.last_mouse_position[0]
            delta_y = position[1] - self.last_mouse_position[1]
            self.last_mouse_position = position
            # Wayland kann unveränderte oder minimale synthetische
            # Bewegungsereignisse liefern. Nur eine echte Bewegung zählt.
            if delta_x * delta_x + delta_y * delta_y >= 4:
                self._mark_mouse_activity()
        elif self.physical_mouse_connected and event.type in {
            pygame.MOUSEBUTTONDOWN,
            pygame.MOUSEBUTTONUP,
            pygame.MOUSEWHEEL,
        }:
            self._mark_mouse_activity()
        if event.type == pygame.QUIT:
            self._request_program_close()
            return True
        if self.camera_settings_open:
            return self._handle_camera_settings_event(event)
        if event.type == pygame.KEYDOWN:
            if self.close_pin_active:
                self._handle_close_pin_key(event)
                return True
            standard_game = self._standard_game_for_view()
            if (
                standard_game is not None
                and self.arcade_leaderboard.is_active_for(self.view_mode)
                and self.arcade_leaderboard.state in {"name_entry", "admin"}
            ):
                action = self.arcade_leaderboard.handle_key(event, time.monotonic())
                self._handle_arcade_leaderboard_action(
                    standard_game, action, time.monotonic()
                )
                # Während der Eingabe dürfen Buchstaben wie M oder A keine
                # globalen Menü- beziehungsweise Ausrichtungsbefehle auslösen.
                return True
            if (
                self.view_mode == "chickens"
                and self.chicken_game is not None
                and self.arcade_leaderboard.is_active_for("chickens")
                and self.arcade_leaderboard.state in {"name_entry", "admin"}
            ):
                action = self.arcade_leaderboard.handle_key(event, time.monotonic())
                self._handle_arcade_leaderboard_action(
                    self.chicken_game, action, time.monotonic()
                )
                return True
            if event.key == pygame.K_ESCAPE:
                self._request_program_close()
                return True
            if self.view_mode == "water" and self.aligner.phase == "success":
                action = self.water_alarm_game.handle_key(event)
                if action == "menu":
                    self._show_menu()
                return True
            if event.key == pygame.K_a:
                self.start_alignment()
            elif event.key == pygame.K_c:
                self.clear_shots()
            elif event.key == pygame.K_l and not self.aligner.active:
                self._toggle_view()
            elif event.key == pygame.K_m and not self.aligner.active:
                self._show_menu()
            elif event.key in {pygame.K_SPACE, pygame.K_RETURN, pygame.K_n}:
                self._advance_sighting()
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.close_pin_active:
                self._handle_close_pin_shot(event.pos)
                return True
            standard_game = self._standard_game_for_view()
            if standard_game is not None:
                if self.arcade_leaderboard.is_active_for(self.view_mode):
                    action = self.arcade_leaderboard.handle_shot(
                        event.pos, time.monotonic()
                    )
                    self._handle_arcade_leaderboard_action(
                        standard_game,
                        action,
                        time.monotonic(),
                    )
                else:
                    action = standard_game.handle_shot(event.pos)
                    if action == "menu":
                        self._show_menu()
            elif (
                self.view_mode == "chickens"
                and self.aligner.phase == "success"
                and self.chicken_game is not None
            ):
                if self.arcade_leaderboard.is_active_for("chickens"):
                    action = self.arcade_leaderboard.handle_shot(
                        event.pos, time.monotonic()
                    )
                    self._handle_arcade_leaderboard_action(
                        self.chicken_game, action, time.monotonic()
                    )
                else:
                    action = self.chicken_game.handle_shot(event.pos)
                    if action == "menu":
                        self._show_menu()
            elif self.view_mode == "target" and self.aligner.phase == "success":
                if self.advance_button.collidepoint(event.pos) and self.sighting_phase in {
                    "evaluation",
                    "complete",
                }:
                    self._advance_sighting()
                elif self.target_live_button.collidepoint(event.pos):
                    self._toggle_view()
                elif self.target_menu_button.collidepoint(event.pos):
                    self._show_menu()
                elif self.target_align_button.collidepoint(event.pos):
                    self.start_alignment()
                elif self.target_clear_button.collidepoint(event.pos):
                    self.clear_shots()
            elif self.view_mode == "menu" and self.aligner.phase == "success":
                current = time.monotonic()
                if (
                    self.menu_settings_hit_rect.collidepoint(event.pos)
                    and not self.easter_corner_hits
                ):
                    self._toggle_view()
                    return True
                if self._hit_menu_mouse(event.pos, current):
                    return True
                if self._easter_moorhuhn_rect().collidepoint(event.pos):
                    self._register_easter_moorhuhn_shot()
                    return True
                if self.easter_moorhuhn_armed:
                    number = self._menu_entry_number_at(event.pos)
                    if number is not None:
                        self._register_easter_moorhuhn_code_shot(number)
                    else:
                        self._cancel_easter_moorhuhn_sequence(reason="falsche Stelle")
                    return True
                page_direction = self._menu_arrow_at(event.pos)
                if page_direction is not None:
                    self._change_menu_page(page_direction)
                else:
                    selected = self._menu_entry_at(event.pos)
                    if selected is not None:
                        self._select_game(*selected)
            elif self.view_mode == "coming_soon" and self.aligner.phase == "success":
                if self._coming_soon_rect().collidepoint(event.pos):
                    self._show_menu()
            else:
                if self.diagnostic_target_button.collidepoint(event.pos):
                    self._show_menu()
                elif self.diagnostic_settings_button.collidepoint(event.pos):
                    self._open_camera_settings()
                elif self.diagnostic_sighting_button.collidepoint(event.pos):
                    self._start_sighting()
                elif self.align_button.collidepoint(event.pos):
                    self.start_alignment()
                elif self.diagnostic_close_button.collidepoint(event.pos):
                    self._request_program_close()
        return True

    def _mark_mouse_activity(self, now: Optional[float] = None) -> None:
        if not self.physical_mouse_connected:
            return
        self.last_mouse_activity = now if now is not None else time.monotonic()
        was_hidden = self.cursor_hidden
        if self.default_cursor is not None:
            try:
                pygame.mouse.set_cursor(self.default_cursor)
            except pygame.error:
                LOGGER.debug("Standard-Mauszeiger konnte nicht wiederhergestellt werden")
        self.xfixes_cursor.show()
        pygame.mouse.set_visible(True)
        self.cursor_hidden = False
        if was_hidden:
            LOGGER.info("Mausbewegung erkannt: Mauszeiger eingeblendet")

    def _hide_cursor_for_pistol(self) -> None:
        """Blendet den Zeiger bei Pistolenbedienung sofort und zentral aus."""

        if self.transparent_cursor is not None:
            try:
                pygame.mouse.set_cursor(self.transparent_cursor)
            except pygame.error:
                LOGGER.debug("Transparenter Mauszeiger konnte nicht gesetzt werden")
        pygame.mouse.set_visible(False)
        self.xfixes_cursor.hide()
        self.cursor_hidden = True

    def _update_cursor_visibility(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        if current - self.last_mouse_probe >= 2.0:
            was_connected = self.physical_mouse_connected
            self.physical_mouse_connected = self._physical_mouse_connected()
            self.last_mouse_probe = current
            if self.physical_mouse_connected and not was_connected:
                # Eine im reinen Pistolenbetrieb aufgebaute XFixes-Sperre wird
                # durch das Schließen dieser separaten X11-Verbindung sicher
                # und vollständig freigegeben. Danach beginnt ein frischer
                # Controller ohne verschachtelte Hide-Zähler.
                self.xfixes_cursor.close()
                self.xfixes_cursor = XFixesCursorController()
                self.last_mouse_position = pygame.mouse.get_pos()
                self.last_mouse_activity = current
                if self.default_cursor is not None:
                    try:
                        pygame.mouse.set_cursor(self.default_cursor)
                    except pygame.error:
                        LOGGER.debug(
                            "Standard-Mauszeiger konnte nach dem Anschließen "
                            "nicht wiederhergestellt werden"
                        )
                pygame.mouse.set_visible(True)
                self.cursor_hidden = False
                LOGGER.info("Maus angeschlossen: Mauszeiger freigegeben")
            elif was_connected and not self.physical_mouse_connected:
                LOGGER.info("Maus entfernt: Mauszeiger dauerhaft ausgeblendet")
        if not self.physical_mouse_connected:
            # Ohne physisches Zeigegerät bleibt der Cursor permanent verborgen.
            # Das erzwungene XFixes-Signal in jedem Bild neutralisiert auch
            # synthetische Xwayland-Bewegungsereignisse.
            pygame.mouse.set_visible(False)
            self.xfixes_cursor.hide(force=True)
            self.cursor_hidden = True
            return
        if not self.cursor_hidden and current - self.last_mouse_activity >= self.CURSOR_HIDE_DELAY:
            if self.transparent_cursor is not None:
                try:
                    pygame.mouse.set_cursor(self.transparent_cursor)
                except pygame.error:
                    LOGGER.debug("Transparenter Mauszeiger konnte nicht gesetzt werden")
            pygame.mouse.set_visible(False)
            # SDL allein blendet den Hardwarezeiger unter Xwayland nicht immer
            # zuverlässig aus. XFixes wird hier genau einmal gesetzt und bei
            # der nächsten echten Mausbewegung in _mark_mouse_activity gelöst.
            self.xfixes_cursor.hide()
            self.cursor_hidden = True
            LOGGER.info("Mauszeiger wegen Inaktivität ausgeblendet")

    @staticmethod
    def _physical_mouse_connected() -> bool:
        roots = (Path("/dev/input/by-id"), Path("/dev/input/by-path"))
        if any(
            path.is_dir() and any(path.glob("*event-mouse"))
            for path in roots
        ):
            return True

        # Bluetooth-Kombigeräte (Tastatur mit Touchpad) besitzen auf Raspberry
        # Pi OS häufig keinen by-id-/by-path-Symlink. Der Kernel weist ihren
        # separaten Zeigerkanal aber eindeutig als ``mouseN`` aus.
        try:
            devices = Path("/proc/bus/input/devices").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return False
        return LaserDiagnosticUI._input_devices_include_mouse(devices)

    @staticmethod
    def _input_devices_include_mouse(devices: str) -> bool:
        return any(
            re.search(r"\bmouse\d+\b", line) is not None
            for line in devices.splitlines()
            if line.startswith("H: Handlers=")
        )

    def close(self) -> None:
        self._stop_standard_games()
        if self.chicken_game is not None:
            self.chicken_game.stop()
        # SDL merkt sich auch einen transparenten Cursor über das Ende des
        # Vollbildfensters hinaus. Vor dem Schließen deshalb zuerst Form und
        # Sichtbarkeit ausdrücklich auf den normalen Desktop-Zeiger setzen.
        if self.default_cursor is not None:
            try:
                pygame.mouse.set_cursor(self.default_cursor)
            except pygame.error:
                LOGGER.debug("Standard-Mauszeiger konnte beim Beenden nicht gesetzt werden")
        try:
            pygame.mouse.set_visible(True)
            pygame.event.pump()
            self.cursor_hidden = False
        except pygame.error:
            LOGGER.debug("Mauszeiger konnte beim Beenden nicht freigegeben werden")
        self.xfixes_cursor.close()

    def _toggle_view(self) -> None:
        if self.aligner.phase != "success":
            self.view_mode = "diagnostic"
            return
        if self.view_mode == "diagnostic":
            self.view_mode = self.return_view_mode
        else:
            self.return_view_mode = self.view_mode
            self.view_mode = "diagnostic"
        strict_moorhuhn = (
            self.view_mode == "chickens"
            and self.chicken_game is not None
            and self.chicken_game.state == "playing"
        )
        self.tracker.set_moorhuhn_filter(strict_moorhuhn)
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _camera_settings_tabs(self) -> dict[str, pygame.Rect]:
        return {
            "alignment": pygame.Rect(24, 72, 250, 44),
            "detection": pygame.Rect(286, 72, 290, 44),
        }

    def _camera_settings_footer(self) -> dict[str, pygame.Rect]:
        return {
            "automatic": pygame.Rect(24, 704, 230, 48),
            "discard": pygame.Rect(530, 704, 220, 48),
            "apply": pygame.Rect(768, 704, 232, 48),
        }

    def _camera_settings_preview_box(self) -> pygame.Rect:
        if self.camera_settings_tab == "alignment":
            return pygame.Rect(24, 132, 680, 382)
        return pygame.Rect(24, 148, 500, 282)

    def _camera_settings_frame_view(self) -> pygame.Rect:
        box = self._camera_settings_preview_box()
        if self.last_frame_rgb is None:
            return box
        frame_h, frame_w = self.last_frame_rgb.shape[:2]
        scale = min(box.width / frame_w, box.height / frame_h)
        view = pygame.Rect(0, 0, round(frame_w * scale), round(frame_h * scale))
        view.center = box.center
        return view

    def _camera_settings_filter_buttons(self) -> dict[str, pygame.Rect]:
        return {
            "auto": pygame.Rect(550, 176, 142, 44),
            "normal": pygame.Rect(704, 176, 142, 44),
            "red_filter": pygame.Rect(858, 176, 142, 44),
        }

    def _camera_detection_color_rects(self) -> dict[str, pygame.Rect]:
        return {
            name: pygame.Rect(24 + (index % 3) * 166, 180 + (index // 3) * 108, 154, 96)
            for index, (name, _) in enumerate(self.DETECTION_TEST_COLORS)
        }

    def _camera_detection_action_buttons(self) -> dict[str, pygame.Rect]:
        return {
            "clear": pygame.Rect(550, 512, 138, 44),
            "quiet": pygame.Rect(700, 512, 138, 44),
            "recommend": pygame.Rect(850, 512, 150, 44),
            "advanced": pygame.Rect(550, 566, 214, 42),
            "original": pygame.Rect(780, 566, 220, 42),
        }

    def _camera_settings_slider_rows(self) -> list[tuple[str, str, int, int, int]]:
        rows = [("sensitivity", "Empfindlichkeit", 0, 100, 5)]
        if self.camera_settings_advanced:
            rows.extend(
                [
                    ("min_red_excess", "Rotüberschuss", 3, 180, 5),
                    ("min_frame_delta", "Bildänderung", 3, 200, 5),
                    ("min_value", "Mindesthelligkeit", 20, 245, 5),
                    ("min_area", "Kleinste Punktfläche", 1, 20, 1),
                    ("max_area", "Größte Punktfläche", 20, 1400, 20),
                ]
            )
        return rows

    def _camera_settings_slider_rects(self) -> dict[str, pygame.Rect]:
        start_y = 320 if self.camera_settings_advanced else 652
        spacing = 58 if self.camera_settings_advanced else 80
        return {
            key: pygame.Rect(608, start_y + index * spacing, 300, 18)
            for index, (key, _, _, _, _) in enumerate(self._camera_settings_slider_rows())
        }

    def _camera_settings_slider_buttons(self) -> list[tuple[str, int, pygame.Rect]]:
        result: list[tuple[str, int, pygame.Rect]] = []
        for key, rect in self._camera_settings_slider_rects().items():
            result.append((key, -1, pygame.Rect(rect.left - 55, rect.centery - 22, 44, 44)))
            result.append((key, 1, pygame.Rect(rect.right + 11, rect.centery - 22, 44, 44)))
        return result

    def _camera_corner_control_rects(self) -> dict[str, pygame.Rect]:
        return {
            "corner_0": pygame.Rect(742, 166, 108, 44),
            "corner_1": pygame.Rect(862, 166, 108, 44),
            "corner_3": pygame.Rect(742, 220, 108, 44),
            "corner_2": pygame.Rect(862, 220, 108, 44),
            "up": pygame.Rect(824, 306, 64, 54),
            "left": pygame.Rect(754, 366, 64, 54),
            "right": pygame.Rect(894, 366, 64, 54),
            "down": pygame.Rect(824, 426, 64, 54),
            "step": pygame.Rect(754, 494, 204, 44),
        }

    def _default_camera_corners(self) -> list[Tuple[int, int]]:
        if self.aligner.corners is not None and len(self.aligner.corners) == 4:
            return [tuple(int(round(value)) for value in point) for point in self.aligner.corners]
        stored = load_homography()
        if len(stored.camera_points) == 4:
            return [tuple(int(value) for value in point) for point in stored.camera_points]
        if self.last_frame_rgb is not None:
            height, width = self.last_frame_rgb.shape[:2]
        else:
            width = max(1, self.tracker.processing_width)
            height = max(1, self.tracker.processing_height)
        return [
            (round(width * 0.15), round(height * 0.15)),
            (round(width * 0.85), round(height * 0.15)),
            (round(width * 0.85), round(height * 0.85)),
            (round(width * 0.15), round(height * 0.85)),
        ]

    def _open_camera_settings(self) -> None:
        if self.camera_settings_open:
            return
        self.camera_settings_open = True
        self.camera_settings_tab = "alignment"
        self.camera_settings_advanced = False
        self.camera_settings_snapshot = deepcopy(self.settings.laser)
        self.camera_settings_draft = deepcopy(self.settings.laser)
        self.camera_corner_snapshot = self._default_camera_corners()
        self.camera_corner_draft = list(self.camera_corner_snapshot)
        self.camera_selected_corner = 0
        self.camera_corner_step = 1
        self.camera_drag_corner = None
        self.camera_drag_slider = None
        self.camera_settings_dirty = False
        self.camera_corners_dirty = False
        self.camera_detection_dirty = False
        self._reset_camera_detection_test()
        self.camera_settings_message = "Änderungen sind zunächst nur eine Vorschau"
        self.view_mode = "diagnostic"
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.5

    def _close_camera_settings(self, *, save: bool) -> None:
        if not self.camera_settings_open:
            return
        if not save and self.camera_settings_snapshot is not None:
            self.tracker.apply_laser_settings(self.camera_settings_snapshot)
        self.camera_settings_open = False
        self.camera_drag_corner = None
        self.camera_drag_slider = None
        self.camera_original_preview_until = 0.0
        self.camera_settings_message = ""
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _active_camera_draft_profile(self):
        if self.camera_settings_draft is None:
            return None
        mode = self.camera_settings_draft.filter_mode
        profile_name = (
            "red_filter"
            if mode == "red_filter"
            or (mode == "auto" and self.tracker.active_filter_profile == "red_filter")
            else "normal"
        )
        return self.camera_settings_draft.profile(profile_name)

    def _preview_camera_detection_settings(self) -> None:
        if self.camera_settings_draft is None:
            return
        self.camera_settings_draft.bounded()
        self.tracker.apply_laser_settings(self.camera_settings_draft)
        self.camera_settings_dirty = True
        self.camera_detection_dirty = True
        self.armed_at = time.monotonic() + 0.45

    def _set_camera_filter_mode(self, mode: str) -> None:
        if self.camera_settings_draft is None:
            return
        self.camera_settings_draft.filter_mode = mode
        self._reset_camera_detection_test()
        self._preview_camera_detection_settings()
        labels = {"auto": "Automatik", "normal": "Ohne Filter", "red_filter": "Rotfilter"}
        self.camera_settings_message = f"Vorschau: {labels.get(mode, mode)}"

    def _reset_camera_detection_test(self) -> None:
        self.camera_detection_samples = {
            name: [] for name, _ in self.DETECTION_TEST_COLORS
        }
        self.camera_detection_last_peak = None
        self.camera_detection_last_capture_at = -1e12
        self.camera_quiet_test_until = 0.0
        self.camera_quiet_test_armed_at = 0.0
        self.camera_quiet_false_triggers = 0
        self.camera_quiet_test_completed = False
        self.last_peak_detection = None
        self.last_peak_mask_rgb = None

    def _reset_camera_detection_profile(self) -> None:
        if self.camera_settings_draft is None:
            return
        active_name = (
            "red_filter"
            if self._active_camera_draft_profile() is self.camera_settings_draft.red_filter
            else "normal"
        )
        default_profile = deepcopy(LaserProfile().profile(active_name))
        if active_name == "red_filter":
            self.camera_settings_draft.red_filter = default_profile
        else:
            self.camera_settings_draft.normal = default_profile
        self._reset_camera_detection_test()
        self._preview_camera_detection_settings()
        self.camera_settings_message = "Standardwerte geladen · Testfarben erneut beschießen"

    def _start_camera_quiet_test(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.camera_quiet_test_until = current + 5.6
        self.camera_quiet_test_armed_at = current + 0.6
        self.camera_quiet_false_triggers = 0
        self.camera_quiet_test_completed = False
        self.tracker.reset_state()
        self.armed_at = self.camera_quiet_test_armed_at
        self.camera_settings_message = "Prüfung läuft · 5 Sekunden nicht schießen"

    def _camera_detection_recommendation(self) -> Optional[int]:
        profile = self._active_camera_draft_profile()
        samples = [
            sample
            for values in self.camera_detection_samples.values()
            for sample in values[-3:]
        ]
        tested_colors = sum(bool(values) for values in self.camera_detection_samples.values())
        if profile is None or tested_colors < 3 or not samples:
            return None
        ratios = [
            min(
                float(sample["red"]) / max(1.0, float(sample["red_threshold"])),
                float(sample["delta"]) / max(1.0, float(sample["delta_threshold"])),
            )
            for sample in samples
        ]
        weakest_ratio = max(0.2, min(ratios))
        current_factor = 1.6 - 1.2 * profile.sensitivity / 100.0
        target_factor = max(0.4, min(1.6, current_factor * weakest_ratio / 1.35))
        return max(0, min(100, int(round((1.6 - target_factor) / 1.2 * 100))))

    def _apply_camera_detection_recommendation(self) -> None:
        profile = self._active_camera_draft_profile()
        recommendation = self._camera_detection_recommendation()
        if profile is None or recommendation is None:
            self.camera_settings_message = "Bitte zuerst mindestens drei Testfarben beschießen"
            return
        profile.sensitivity = recommendation
        self._preview_camera_detection_settings()
        self.camera_settings_message = (
            f"Vorschlag {recommendation} % eingestellt · Testfarben zur Kontrolle wiederholen"
        )

    def _update_camera_detection_test(
        self,
        detection: LaserDetection,
        now: float,
    ) -> None:
        if not self.camera_settings_open or self.camera_settings_tab != "detection":
            return
        if self.camera_quiet_test_until:
            if now >= self.camera_quiet_test_until:
                self.camera_quiet_test_until = 0.0
                self.camera_quiet_test_completed = True
                self.camera_settings_message = (
                    "Ohne Schuss geprüft · keine Fehlauslösung"
                    if self.camera_quiet_false_triggers == 0
                    else f"Ohne Schuss: {self.camera_quiet_false_triggers} Fehlauslösung(en)"
                )
            elif now >= self.camera_quiet_test_armed_at and detection.shot:
                self.camera_quiet_false_triggers += 1
                self.camera_settings_message = (
                    f"Prüfung läuft · {self.camera_quiet_false_triggers} Fehlauslösung(en)"
                )
            return

        point = detection.observed_point or detection.point
        red = max(detection.observed_peak_red, detection.peak_red_excess)
        delta = max(detection.observed_peak_delta, detection.peak_delta)
        value = max(detection.observed_peak_value, 0)
        area = max(detection.observed_area, detection.area)
        if point is None or red < 3 or delta < 8:
            return
        detected = bool(detection.shot and detection.point is not None)
        # Der sehr offene Messkanal sieht auch das geringe periodische
        # Farbflimmern von LED-Lampen und Beamerflächen. Solche Ruhepeaks dürfen
        # weder eine Testfarbe abhaken noch die große Peakanzeige ständig neu
        # auslösen. Unterhalb der eigentlichen Schussschwelle übernehmen wir
        # daher nur einen klaren Änderungsimpuls oder einen besonders starken,
        # kompakten Rotimpuls. Bereits sicher erkannte Schüsse bleiben immer
        # sichtbar, auch auf nahezu weißen Flächen mit wenig Helligkeitsreserve.
        red_threshold = max(1, detection.red_threshold)
        delta_threshold = max(1, detection.delta_threshold)
        clear_change = (
            delta >= 18
            and red >= max(12, min(60, round(red_threshold * 0.55)))
        )
        clear_compact_red = (
            area <= 80
            and red >= max(120, red_threshold + 20)
        )
        if not detected and not (clear_change or clear_compact_red):
            return
        if now - self.camera_detection_last_capture_at < 0.28:
            return

        mapped: Optional[Tuple[int, int]] = None
        if self.aligner.homography is not None:
            mapped = apply_homography(self.aligner.homography, point)
        color_name = None
        if mapped is not None:
            color_name = next(
                (
                    name
                    for name, rect in self._camera_detection_color_rects().items()
                    if pygame.Rect(
                        rect.left + 8,
                        rect.top + 27,
                        rect.width - 16,
                        rect.height - 60,
                    ).collidepoint(mapped)
                ),
                None,
            )
        # Die Einstellhilfe misst bewusst nur auf der freien Farbfläche.
        # DLP-Farbwechsel, Lampenflimmern oder Bewegung außerhalb der sechs
        # Prüfflächen dürfen weder den großen Peak verändern noch den
        # Aufnahmeschutz auslösen.
        if color_name is None:
            return
        sample: dict[str, float | int | bool] = {
            "red": red,
            "delta": delta,
            "value": value,
            "area": area,
            "red_threshold": red_threshold,
            "delta_threshold": delta_threshold,
            "detected": detected,
        }
        self.camera_detection_last_peak = {
            **sample,
            "color": color_name,
        }
        self.camera_detection_last_capture_at = now
        samples = self.camera_detection_samples.setdefault(color_name, [])
        samples.append(sample)
        del samples[:-5]
        self.camera_settings_message = (
            f"{color_name}: sicher erkannt · weitere Farben testen"
            if detected
            else f"{color_name}: Peak gemessen, aber noch nicht sicher erkannt"
        )

    def _adjust_camera_profile_value(self, key: str, direction: int) -> None:
        profile = self._active_camera_draft_profile()
        row = next((item for item in self._camera_settings_slider_rows() if item[0] == key), None)
        if profile is None or row is None:
            return
        _, _, minimum, maximum, step = row
        value = int(getattr(profile, key)) + int(direction) * step
        setattr(profile, key, max(minimum, min(maximum, value)))
        if profile.max_area < profile.min_area:
            if key == "min_area":
                profile.max_area = profile.min_area
            else:
                profile.min_area = min(profile.min_area, profile.max_area)
        self._preview_camera_detection_settings()

    def _set_camera_profile_slider(self, key: str, x: int) -> None:
        profile = self._active_camera_draft_profile()
        rect = self._camera_settings_slider_rects().get(key)
        row = next((item for item in self._camera_settings_slider_rows() if item[0] == key), None)
        if profile is None or rect is None or row is None:
            return
        _, _, minimum, maximum, step = row
        fraction = max(0.0, min(1.0, (x - rect.left) / max(1, rect.width)))
        value = minimum + fraction * (maximum - minimum)
        value = round(value / step) * step
        setattr(profile, key, max(minimum, min(maximum, int(value))))
        if profile.max_area < profile.min_area:
            if key == "min_area":
                profile.max_area = profile.min_area
            else:
                profile.min_area = min(profile.min_area, profile.max_area)
        self._preview_camera_detection_settings()

    def _camera_point_to_settings_view(self, point: Tuple[int, int]) -> Tuple[int, int]:
        view = self._camera_settings_frame_view()
        if self.last_frame_rgb is not None:
            height, width = self.last_frame_rgb.shape[:2]
        else:
            width = max(1, self.tracker.processing_width)
            height = max(1, self.tracker.processing_height)
        return (
            view.left + round(point[0] * view.width / max(1, width)),
            view.top + round(point[1] * view.height / max(1, height)),
        )

    def _settings_view_to_camera_point(self, point: Tuple[int, int]) -> Tuple[int, int]:
        view = self._camera_settings_frame_view()
        if self.last_frame_rgb is not None:
            height, width = self.last_frame_rgb.shape[:2]
        else:
            width = max(1, self.tracker.processing_width)
            height = max(1, self.tracker.processing_height)
        return (
            max(0, min(width - 1, round((point[0] - view.left) * width / max(1, view.width)))),
            max(0, min(height - 1, round((point[1] - view.top) * height / max(1, view.height)))),
        )

    def _move_camera_corner(self, dx: int, dy: int) -> None:
        if not self.camera_corner_draft:
            return
        index = max(0, min(3, self.camera_selected_corner))
        x, y = self.camera_corner_draft[index]
        if self.last_frame_rgb is not None:
            height, width = self.last_frame_rgb.shape[:2]
        else:
            width = max(1, self.tracker.processing_width)
            height = max(1, self.tracker.processing_height)
        candidate = list(self.camera_corner_draft)
        candidate[index] = (
            max(0, min(width - 1, x + dx)),
            max(0, min(height - 1, y + dy)),
        )
        valid, message = validate_camera_quad(candidate, (width, height))
        if valid:
            self.camera_corner_draft = candidate
            self.camera_settings_dirty = True
            self.camera_corners_dirty = True
            self.camera_settings_message = "Ecken geändert · noch nicht gespeichert"
        else:
            self.camera_settings_message = message

    def _apply_camera_settings(self) -> None:
        if self.camera_settings_draft is None or len(self.camera_corner_draft) != 4:
            return
        if self.last_frame_rgb is not None:
            frame_height, frame_width = self.last_frame_rgb.shape[:2]
        else:
            frame_width = max(1, self.tracker.processing_width)
            frame_height = max(1, self.tracker.processing_height)
        valid, message = validate_camera_quad(
            self.camera_corner_draft,
            (frame_width, frame_height),
        )
        if not valid:
            self.camera_settings_message = message
            return
        screen_width, screen_height = self.screen.get_size()
        screen_points = [
            (0, 0),
            (screen_width - 1, 0),
            (screen_width - 1, screen_height - 1),
            (0, screen_height - 1),
        ]
        try:
            calibration = calculate_homography(self.camera_corner_draft, screen_points)
            self.settings.laser = deepcopy(self.camera_settings_draft).bounded()
            save_settings(self.settings)
            if self.camera_corners_dirty:
                save_calibration(
                    calibration.homography,
                    calibration.camera_points,
                    calibration.screen_points,
                    alignment_mode="manual",
                    source_size=(frame_width, frame_height),
                )
        except Exception as exc:
            self.camera_settings_message = f"Speichern fehlgeschlagen: {exc}"
            LOGGER.exception("Manuelle Kameraeinstellungen konnten nicht gespeichert werden")
            return
        if self.camera_corners_dirty:
            self.aligner.homography = calibration.homography
            self.aligner.corners = np.asarray(self.camera_corner_draft, dtype=np.float32)
            self.aligner.confidence = 1.0
            self.aligner.phase = "success"
            self.aligner.message = "Manuelle Ecken gespeichert · Einschießen prüfen"
            self.manual_alignment_active = True
            self.alignment_changed_since_sighting = True
        self.tracker.apply_laser_settings(self.settings.laser)
        if self.camera_corners_dirty:
            self.tracker.reload_calibration()
        LOGGER.info(
            "Manuelle Kameraeinstellungen gespeichert: Ecken=%s Filtermodus=%s",
            self.camera_corner_draft,
            self.settings.laser.filter_mode,
        )
        self._close_camera_settings(save=True)

    def _camera_settings_original_preview(self) -> None:
        self.camera_original_preview_until = time.monotonic() + 5.0
        self.tracker.reset_state()
        self.armed_at = self.camera_original_preview_until + 0.6
        self.camera_settings_message = "Originalfarbe 5 Sekunden · Schusserkennung gesperrt"

    def _handle_camera_settings_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._close_camera_settings(save=False)
            elif event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
                self._apply_camera_settings()
            elif self.camera_settings_tab == "alignment" and event.key in {
                pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN
            }:
                delta = {
                    pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0),
                    pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1),
                }[event.key]
                self._move_camera_corner(*delta)
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._handle_camera_settings_pointer_action(event.pos):
                return True
            if self.camera_settings_tab == "alignment":
                for index, corner in enumerate(self.camera_corner_draft):
                    handle = self._camera_point_to_settings_view(corner)
                    if (event.pos[0] - handle[0]) ** 2 + (event.pos[1] - handle[1]) ** 2 <= 28 ** 2:
                        self.camera_selected_corner = index
                        self.camera_drag_corner = index
                        return True
            else:
                for key, rect in self._camera_settings_slider_rects().items():
                    if rect.inflate(0, 24).collidepoint(event.pos):
                        self.camera_drag_slider = key
                        self._set_camera_profile_slider(key, event.pos[0])
                        return True
            return True
        if event.type == pygame.MOUSEMOTION:
            if self.camera_drag_corner is not None:
                candidate = self._settings_view_to_camera_point(event.pos)
                old = self.camera_corner_draft[self.camera_drag_corner]
                self._move_camera_corner(candidate[0] - old[0], candidate[1] - old[1])
            elif self.camera_drag_slider is not None:
                self._set_camera_profile_slider(self.camera_drag_slider, event.pos[0])
            return True
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.camera_drag_corner = None
            self.camera_drag_slider = None
            return True
        return True

    def _handle_camera_settings_pointer_action(self, point: Tuple[int, int]) -> bool:
        for tab, rect in self._camera_settings_tabs().items():
            if rect.collidepoint(point):
                self.camera_settings_tab = tab
                return True
        footer = self._camera_settings_footer()
        if footer["discard"].collidepoint(point):
            self._close_camera_settings(save=False)
            return True
        if footer["apply"].collidepoint(point):
            self._apply_camera_settings()
            return True
        if footer["automatic"].collidepoint(point):
            if self.camera_settings_tab == "detection":
                self._reset_camera_detection_profile()
            else:
                self.start_alignment()
            return True
        if self.camera_settings_tab == "alignment":
            for key, rect in self._camera_corner_control_rects().items():
                if not rect.collidepoint(point):
                    continue
                if key.startswith("corner_"):
                    self.camera_selected_corner = int(key[-1])
                elif key == "up":
                    self._move_camera_corner(0, -self.camera_corner_step)
                elif key == "down":
                    self._move_camera_corner(0, self.camera_corner_step)
                elif key == "left":
                    self._move_camera_corner(-self.camera_corner_step, 0)
                elif key == "right":
                    self._move_camera_corner(self.camera_corner_step, 0)
                elif key == "step":
                    self.camera_corner_step = 5 if self.camera_corner_step == 1 else 1
                return True
            if pygame.Rect(742, 560, 216, 44).collidepoint(point):
                self.camera_corner_draft = list(self.camera_corner_snapshot)
                self.camera_corners_dirty = False
                self.camera_settings_dirty = self.camera_detection_dirty
                self.camera_settings_message = "Gespeicherte Ecken wiederhergestellt"
                return True
        else:
            for mode, rect in self._camera_settings_filter_buttons().items():
                if rect.collidepoint(point):
                    self._set_camera_filter_mode(mode)
                    return True
            if self.camera_settings_advanced:
                advanced_rect = pygame.Rect(550, 230, 214, 42)
                original_rect = pygame.Rect(780, 230, 220, 42)
                if advanced_rect.collidepoint(point):
                    self.camera_settings_advanced = False
                    return True
                if original_rect.collidepoint(point):
                    self._camera_settings_original_preview()
                    return True
            else:
                actions = self._camera_detection_action_buttons()
                if actions["clear"].collidepoint(point):
                    self._reset_camera_detection_test()
                    self.camera_settings_message = "Messungen gelöscht · Testfarben neu beschießen"
                    return True
                if actions["quiet"].collidepoint(point):
                    self._start_camera_quiet_test()
                    return True
                if actions["recommend"].collidepoint(point):
                    self._apply_camera_detection_recommendation()
                    return True
                if actions["advanced"].collidepoint(point):
                    self.camera_settings_advanced = True
                    return True
                if actions["original"].collidepoint(point):
                    self._camera_settings_original_preview()
                    return True
            for key, direction, rect in self._camera_settings_slider_buttons():
                if rect.collidepoint(point):
                    self._adjust_camera_profile_value(key, direction)
                    return True
        return False

    def _handle_camera_settings_shot(
        self, point: Tuple[int, int], now: Optional[float] = None
    ) -> bool:
        if self._handle_camera_settings_pointer_action(point):
            self.tracker.reset_state()
            self.armed_at = (now if now is not None else time.monotonic()) + 0.32
            return True
        if self.camera_settings_tab == "alignment":
            for index, corner in enumerate(self.camera_corner_draft):
                handle = self._camera_point_to_settings_view(corner)
                if (point[0] - handle[0]) ** 2 + (point[1] - handle[1]) ** 2 <= 38 ** 2:
                    self.camera_selected_corner = index
                    self.tracker.reset_state()
                    self.armed_at = (now if now is not None else time.monotonic()) + 0.25
                    return True
        return False

    def _show_menu(self) -> None:
        if self.aligner.phase != "success":
            return
        self._stop_standard_games()
        if self.chicken_game is not None:
            self.chicken_game.stop()
        self.tracker.set_moorhuhn_filter(False)
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False
        self.arcade_leaderboard.clear()
        self.easter_title_hits = 0
        self.easter_corner_hits.clear()
        self.easter_moorhuhn_armed = False
        self.easter_moorhuhn_progress = 0
        current = time.monotonic()
        for mouse in self.menu_mice:
            mouse.active = False
            mouse.current_rect = None
            mouse.current_mask = None
            mouse.spawn_at = current + self.menu_mouse_rng.uniform(8.0, 16.0)
        self.view_mode = "menu"
        self.return_view_mode = "menu"
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _load_menu_mouse_sheet(self, filename: str) -> tuple[pygame.Surface, ...]:
        """Lädt ein 2×2-Mausblatt, beschneidet und neutralisiert jede Phase."""

        path = Path(__file__).resolve().parents[1] / "assets" / "arcade_themes" / filename
        try:
            sheet = pygame.image.load(str(path)).convert_alpha()
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Menü-Mausgrafik fehlt: %s", exc)
            return ()
        cell_w, cell_h = sheet.get_width() // 2, sheet.get_height() // 2
        frames: list[pygame.Surface] = []
        for row in range(2):
            for column in range(2):
                cell = sheet.subsurface(
                    pygame.Rect(column * cell_w, row * cell_h, cell_w, cell_h)
                ).copy()
                alpha = pygame.surfarray.array_alpha(cell)
                occupied = np.argwhere(alpha >= 10)
                if occupied.size == 0:
                    continue
                min_x, min_y = occupied.min(axis=0)
                max_x, max_y = occupied.max(axis=0)
                crop = pygame.Rect(
                    max(0, int(min_x) - 4),
                    max(0, int(min_y) - 4),
                    min(cell_w, int(max_x) + 5) - max(0, int(min_x) - 4),
                    min(cell_h, int(max_y) + 5) - max(0, int(min_y) - 4),
                )
                frame = cell.subsurface(crop).copy()
                neutralize_laser_red(frame)
                limit_projected_brightness(frame, 142)
                frames.append(frame)
        return tuple(frames)

    def _load_menu_mouse_frames(self) -> tuple[pygame.Surface, ...]:
        return self._load_menu_mouse_sheet("menu_mouse_run_sheet_v1.png")

    def _load_menu_mouse_behavior_frames(self) -> tuple[pygame.Surface, ...]:
        return self._load_menu_mouse_sheet("menu_mouse_behavior_sheet_v1.png")

    @staticmethod
    def _menu_floor_bounds(y: float) -> tuple[float, float]:
        """Seitengrenzen des perspektivischen, sichtbaren Holzbodens."""

        depth = max(0.0, min(1.0, (y - 674.0) / 78.0))
        return 188.0 - 168.0 * depth, 806.0 - 11.0 * depth

    def _random_menu_floor_point(self, *, avoid: Optional[tuple[float, float]] = None) -> tuple[float, float]:
        y = self.menu_mouse_rng.uniform(684.0, 747.0)
        left, right = self._menu_floor_bounds(y)
        for _ in range(8):
            x = self.menu_mouse_rng.uniform(left + 34.0, right - 34.0)
            if avoid is None or math.hypot(x - avoid[0], (y - avoid[1]) * 2.2) >= 150.0:
                return x, y
        return x, y

    def _menu_mouse_scaled_frame(
        self, mouse: MenuMouse, state: str, frame_index: int
    ) -> tuple[pygame.Surface, pygame.mask.Mask]:
        running_states = {"moving", "emerging", "approaching_hole", "entering"}
        sources = (
            self.menu_mouse_source_frames
            if state in running_states
            else self.menu_mouse_behavior_frames
        )
        if not sources:
            empty = pygame.Surface((1, 1), pygame.SRCALPHA)
            return empty, pygame.mask.from_surface(empty)
        index = frame_index % len(sources) if state in running_states else {
            "standing": 0,
            "sitting": 1,
            "sniffing": 2,
            "grooming": 3,
        }.get(state, 0)
        source = sources[index]
        depth = max(0.0, min(1.0, (mouse.y - 674.0) / 78.0))
        base_height = 25.0 + depth * 20.0
        pose_factor = 1.28 if state in {"sitting", "grooming"} else 1.0
        height = max(22, round(base_height * mouse.size_factor * pose_factor))
        width = max(30, round(source.get_width() * height / source.get_height()))
        angle = mouse.heading_angle if state in running_states else 0
        cache_state = "moving" if state in running_states else state
        key = cache_state, index, width, mouse.direction, angle
        cached = self.menu_mouse_frame_cache.get(key)
        if cached is None:
            frame = pygame.transform.smoothscale(source, (width, height))
            if mouse.direction < 0:
                frame = pygame.transform.flip(frame, True, False)
            if angle:
                frame = pygame.transform.rotate(frame, angle)
            cached = frame, pygame.mask.from_surface(frame, 10)
            self.menu_mouse_frame_cache[key] = cached
        return cached

    def _load_menu_settings_image(self) -> Optional[pygame.Surface]:
        """Lädt das fotorealistische, laserneutrale Bühnen-Zahnrad."""

        path = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "arcade_themes"
            / "menu_settings_gear_v1.png"
        )
        try:
            image = pygame.image.load(str(path)).convert_alpha()
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Menü-Zahnradgrafik fehlt: %s", exc)
            return None
        alpha = pygame.surfarray.array_alpha(image)
        occupied = np.argwhere(alpha >= 10)
        if occupied.size:
            min_x, min_y = occupied.min(axis=0)
            max_x, max_y = occupied.max(axis=0)
            image = image.subsurface(
                pygame.Rect(
                    max(0, int(min_x) - 5),
                    max(0, int(min_y) - 5),
                    min(image.get_width(), int(max_x) + 6) - max(0, int(min_x) - 5),
                    min(image.get_height(), int(max_y) + 6) - max(0, int(min_y) - 5),
                )
            ).copy()
        neutralize_laser_red(image)
        limit_projected_brightness(image, 145)
        return pygame.transform.smoothscale(image, self.menu_settings_button.size)

    def _load_menu_mouse_hole_image(self) -> Optional[pygame.Surface]:
        """Lädt das kleine, in den linken Holzschrank eingelassene Mäuseloch."""

        path = Path(__file__).resolve().parents[1] / "assets" / "arcade_themes" / "menu_mouse_hole_v1.png"
        try:
            image = pygame.image.load(str(path)).convert_alpha()
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Menü-Mäuselochgrafik fehlt: %s", exc)
            return None
        bounds = image.get_bounding_rect(min_alpha=10)
        if bounds.width and bounds.height:
            image = image.subsurface(bounds).copy()
        neutralize_laser_red(image)
        limit_projected_brightness(image, 112)
        # Nicht das ganze Loch wie einen Aufkleber drehen: Eine perspektivische
        # Verformung hält den Bogen aufrecht, legt aber seine Unterkante exakt
        # in die nach rechts ansteigende Schrank-/Bodenfuge. Die Öffnung ist an
        # die Körperhöhe der Maus (ohne Schwanz) angepasst.
        source = pygame.surfarray.array3d(image).swapaxes(0, 1)
        alpha = pygame.surfarray.array_alpha(image).swapaxes(0, 1)
        rgba = np.dstack((source, alpha))
        source_h, source_w = rgba.shape[:2]
        source_quad = np.float32(
            ((0, 0), (source_w - 1, 0), (source_w - 1, source_h - 1), (0, source_h - 1))
        )
        target_quad = np.float32(((3, 9), (51, 0), (51, 20), (0, 41)))
        transform = cv2.getPerspectiveTransform(source_quad, target_quad)
        warped = cv2.warpPerspective(
            rgba,
            transform,
            (52, 42),
            flags=cv2.INTER_AREA,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
        return pygame.image.frombuffer(warped.tobytes(), (52, 42), "RGBA").convert_alpha()

    def _load_menu_feather_image(self) -> Optional[pygame.Surface]:
        """Lädt die dezente echte Feder für den Moorhuhn-Geheimeingang."""

        path = Path(__file__).resolve().parents[1] / "assets" / "arcade_themes" / "menu_feather_v1.png"
        try:
            image = pygame.image.load(str(path)).convert_alpha()
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Menü-Federgrafik fehlt: %s", exc)
            return None
        bounds = image.get_bounding_rect(min_alpha=10)
        if bounds.width and bounds.height:
            image = image.subsurface(bounds).copy()
        neutralize_laser_red(image)
        limit_projected_brightness(image, 92)
        image = pygame.transform.smoothscale(image, (43, 25))
        image.set_alpha(168)
        return image

    def _load_menu_title_image(self) -> Optional[pygame.Surface]:
        """Lädt das laserneutrale Emaille-/Messing-Schild mit exaktem Titel."""

        path = Path(__file__).resolve().parents[1] / "assets" / "arcade_themes" / "menu_title_sign_v1.png"
        try:
            image = pygame.image.load(str(path)).convert_alpha()
        except (FileNotFoundError, pygame.error) as exc:
            LOGGER.warning("Menü-Titelschild fehlt: %s", exc)
            return None
        neutralize_laser_red(image)
        limit_projected_brightness(image, 148)
        return pygame.transform.smoothscale(image, (362, 64))

    def _spawn_menu_mouse(self, mouse: MenuMouse, now: float) -> None:
        if not self.menu_mouse_source_frames or not self.menu_mouse_behavior_frames:
            mouse.spawn_at = now + 30.0
            return
        mouse.y = self.menu_mouse_rng.uniform(684.0, 746.0)
        left, right = self._menu_floor_bounds(mouse.y)
        from_left = self.menu_mouse_rng.choice((True, False))
        if from_left:
            # Die Maus beginnt vollständig hinter dem Schrank und läuft zuerst
            # gerade durch die sichtbare Öffnung. Erst danach darf sie auf eine
            # zufällige Bahn über den Boden abbiegen.
            mouse.x, mouse.y = 82.0, float(self.menu_mouse_hole_rect.bottom)
            mouse.target_x, mouse.target_y = 190.0, mouse.y
            mouse.state = "emerging"
        else:
            mouse.x = right + 50.0
            mouse.target_x, mouse.target_y = self._random_menu_floor_point()
            mouse.state = "moving"
        mouse.direction = 1 if mouse.target_x >= mouse.x else -1
        mouse.heading_angle = 0
        mouse.speed = self.menu_mouse_rng.uniform(58.0, 94.0)
        mouse.size_factor = self.menu_mouse_rng.uniform(0.88, 1.12)
        mouse.state_until = 0.0
        mouse.depart_at = now + self.menu_mouse_rng.uniform(7.0, 13.0)
        mouse.pauses_used = 0
        mouse.exiting = False
        mouse.last_update = now
        mouse.current_frame = 0
        mouse.current_rect = None
        mouse.current_mask = None
        mouse.active = True

    def _choose_menu_mouse_destination(self, mouse: MenuMouse, now: float) -> None:
        """Wählt eine Pause, einen neuen Tiefenpfad oder den Weg ins Versteck."""

        roll = self.menu_mouse_rng.random()
        must_leave = now >= mouse.depart_at or mouse.pauses_used >= 1
        if not must_leave and roll < 0.48:
            mouse.state = self.menu_mouse_rng.choice(
                ("standing", "standing", "sniffing", "sitting", "grooming")
            )
            mouse.state_until = now + self.menu_mouse_rng.uniform(1.1, 4.2)
            mouse.pauses_used += 1
            return
        if not must_leave and roll < 0.72:
            mouse.target_x, mouse.target_y = self._random_menu_floor_point(
                avoid=(mouse.x, mouse.y)
            )
            mouse.exiting = False
        else:
            exit_y = self.menu_mouse_rng.uniform(684.0, 747.0)
            left, right = self._menu_floor_bounds(exit_y)
            if abs(mouse.x - left) <= abs(mouse.x - right):
                # Zuerst wird ein fester Anlaufpunkt direkt vor dem Loch
                # erreicht. So läuft die Maus niemals diagonal neben die
                # Öffnung. Der eigentliche Tunnelweg beginnt erst dort.
                mouse.target_x = 190.0
                mouse.target_y = float(self.menu_mouse_hole_rect.bottom)
                mouse.state = "approaching_hole"
            else:
                mouse.target_x = right + 58.0
                mouse.target_y = exit_y
                mouse.state = "moving"
            mouse.exiting = True
        if not mouse.exiting:
            mouse.state = "moving"
        mouse.direction = 1 if mouse.target_x >= mouse.x else -1

    def _update_menu_mice(self, now: float) -> None:
        if self.view_mode != "menu" or self.aligner.phase != "success":
            return
        for mouse in self.menu_mice:
            if not mouse.active:
                if now >= mouse.spawn_at:
                    self._spawn_menu_mouse(mouse, now)
                continue
            delta = max(0.0, min(0.1, now - mouse.last_update))
            mouse.last_update = now
            running_states = {"moving", "emerging", "approaching_hole", "entering"}
            if mouse.state not in running_states:
                if now >= mouse.state_until:
                    self._choose_menu_mouse_destination(mouse, now)
                continue
            dx, dy = mouse.target_x - mouse.x, mouse.target_y - mouse.y
            distance = math.hypot(dx, dy * 1.7)
            if distance <= 2.0:
                mouse.x, mouse.y = mouse.target_x, mouse.target_y
                if mouse.state == "emerging":
                    mouse.state = "moving"
                    mouse.exiting = False
                    mouse.target_x, mouse.target_y = self._random_menu_floor_point(
                        avoid=(mouse.x, mouse.y)
                    )
                    mouse.direction = 1 if mouse.target_x >= mouse.x else -1
                elif mouse.state == "approaching_hole":
                    mouse.state = "entering"
                    mouse.target_x = 82.0
                    mouse.target_y = float(self.menu_mouse_hole_rect.bottom)
                    mouse.direction = -1
                    mouse.heading_angle = 0
                elif mouse.exiting:
                    mouse.active = False
                    mouse.current_rect = None
                    mouse.current_mask = None
                    mouse.spawn_at = now + self.menu_mouse_rng.uniform(18.0, 40.0)
                else:
                    self._choose_menu_mouse_destination(mouse, now)
                continue
            step = min(distance, mouse.speed * delta)
            if step >= distance:
                mouse.x, mouse.y = mouse.target_x, mouse.target_y
                if mouse.state == "emerging":
                    mouse.state = "moving"
                    mouse.exiting = False
                    mouse.target_x, mouse.target_y = self._random_menu_floor_point(
                        avoid=(mouse.x, mouse.y)
                    )
                    mouse.direction = 1 if mouse.target_x >= mouse.x else -1
                elif mouse.state == "approaching_hole":
                    mouse.state = "entering"
                    mouse.target_x = 82.0
                    mouse.target_y = float(self.menu_mouse_hole_rect.bottom)
                    mouse.direction = -1
                    mouse.heading_angle = 0
                elif mouse.exiting:
                    mouse.active = False
                    mouse.current_rect = None
                    mouse.current_mask = None
                    mouse.spawn_at = now + self.menu_mouse_rng.uniform(18.0, 40.0)
                else:
                    self._choose_menu_mouse_destination(mouse, now)
                continue
            mouse.x += dx / distance * step
            mouse.y += dy / distance * step
            if abs(dx) > 2.0:
                mouse.direction = 1 if dx > 0 else -1
                # Leichte perspektivische Drehung zeigt den Lauf nach hinten
                # beziehungsweise nach vorn, ohne die Maus flach zu kippen.
                slope = math.degrees(math.atan2(-dy, max(1.0, abs(dx))))
                mouse.heading_angle = round(max(-18.0, min(18.0, slope)) / 3.0) * 3

    def _menu_mouse_hole_aperture(self) -> pygame.Surface:
        """Maske der dunklen, tatsächlich offenen Fläche des Mäuselochs."""

        cached = getattr(self, "_menu_mouse_hole_aperture_cache", None)
        if cached is not None:
            return cached
        aperture = pygame.Surface(self.menu_mouse_hole_rect.size, pygame.SRCALPHA)
        # Ein schmaler Rundbogen mit geraden Seiten. Der vier Pixel breite
        # Rand bleibt unberührt, damit die Maus nie über den Holzrahmen läuft.
        pygame.draw.ellipse(aperture, (255, 255, 255, 255), (5, 3, 23, 28))
        pygame.draw.rect(aperture, (255, 255, 255, 255), (5, 16, 23, 36))
        self._menu_mouse_hole_aperture_cache = aperture
        return aperture

    def _draw_menu_mouse_tunnel_part(self, frame: pygame.Surface, rect: pygame.Rect) -> None:
        """Zeigt die Maus im Loch und rechts davon, niemals auf dem Schrank."""

        hole = self.menu_mouse_hole_rect
        # Lochmaske und freier Boden überlappen um vier Pixel. Zuvor endete die
        # innere Öffnung bei x=157, während der Boden erst bei x=162 begann.
        # Dieser unbemalte Spalt schnitt die Maus als schwarzen Vertikalstrich.
        floor_edge = hole.right - 5
        floor_left = max(rect.left, floor_edge)
        if floor_left < rect.right:
            source = pygame.Rect(floor_left - rect.left, 0, rect.right - floor_left, rect.height)
            self.screen.blit(frame, (floor_left, rect.top), source)

        overlap = rect.clip(hole)
        if not overlap.width or not overlap.height:
            return
        fragment = pygame.Surface(overlap.size, pygame.SRCALPHA)
        fragment.blit(
            frame,
            (0, 0),
            pygame.Rect(overlap.left - rect.left, overlap.top - rect.top, overlap.width, overlap.height),
        )
        aperture = self._menu_mouse_hole_aperture().subsurface(
            pygame.Rect(overlap.left - hole.left, overlap.top - hole.top, overlap.width, overlap.height)
        )
        fragment.blit(aperture, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        self.screen.blit(fragment, overlap)

    def _menu_mouse_visible_at(self, mouse: MenuMouse, point: Tuple[int, int]) -> bool:
        if mouse.state not in {"emerging", "entering"}:
            return True
        if point[0] >= self.menu_mouse_hole_rect.right - 5:
            return True
        if not self.menu_mouse_hole_rect.collidepoint(point):
            return False
        local = point[0] - self.menu_mouse_hole_rect.left, point[1] - self.menu_mouse_hole_rect.top
        return bool(pygame.mask.from_surface(self._menu_mouse_hole_aperture(), 10).get_at(local))

    def _menu_mouse_foreground_layer(self) -> pygame.Surface:
        """Bewahrt Schrank und Maschine als physische Vordergrundobjekte."""

        if self.menu_mouse_foreground is not None:
            return self.menu_mouse_foreground
        layer = self.screen.copy().convert_alpha()
        alpha_mask = pygame.Surface(self.screen.get_size(), depth=8)
        alpha_mask.fill(0)
        left_points = ((0, 600), (190, 600), (190, 672), (20, 752), (0, 752))
        right_points = ((806, 600), (1024, 600), (1024, 768), (795, 768), (795, 752), (806, 674))
        for points in (left_points, right_points):
            pygame.draw.polygon(alpha_mask, 255, points)
        alpha = pygame.surfarray.pixels_alpha(layer)
        alpha[:, :] = pygame.surfarray.array2d(alpha_mask).astype(np.uint8)
        del alpha
        self.menu_mouse_foreground = layer
        return layer

    def _draw_menu_mice(self, now: float) -> None:
        # Vordergrund sichern, bevor eine Maus gezeichnet wird. Andernfalls
        # würde die gecachte Möbelmaske eine alte Maus als Geisterbild tragen.
        foreground = self._menu_mouse_foreground_layer()
        self._update_menu_mice(now)
        tunnel_mice: list[tuple[MenuMouse, pygame.Surface, pygame.Rect]] = []
        for mouse in self.menu_mice:
            if not mouse.active:
                continue
            frame_index = int(now * 9.0 + mouse.speed) % max(1, len(self.menu_mouse_source_frames))
            mouse.current_frame = frame_index
            frame, mask = self._menu_mouse_scaled_frame(mouse, mouse.state, frame_index)
            rect = frame.get_rect(midbottom=(round(mouse.x), round(mouse.y)))
            mouse.current_rect = rect
            mouse.current_mask = mask
            shadow_width = max(18, round(frame.get_width() * 0.58))
            shadow = pygame.Surface((shadow_width, 8), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow, (0, 4, 8, 76), shadow.get_rect())
            if mouse.state in {"emerging", "entering"}:
                shadow_rect = shadow.get_rect(midbottom=(round(mouse.x), round(mouse.y) + 2))
                # Kein hartes Abschneiden mehr an einer senkrechten x-Kante:
                # der Schatten wird über den Tunnelweg weich ein-/ausgeblendet
                # und anschließend wie die Maus von den Möbeln überdeckt.
                progress = max(
                    0.0,
                    min(
                        1.0,
                        (mouse.x - (self.menu_mouse_hole_rect.right - 5))
                        / max(1.0, 190.0 - (self.menu_mouse_hole_rect.right - 5)),
                    ),
                )
                if progress > 0.0:
                    faded_shadow = shadow.copy()
                    faded_shadow.set_alpha(round(255 * progress))
                    self.screen.blit(faded_shadow, shadow_rect)
                tunnel_mice.append((mouse, frame, rect))
            else:
                self.screen.blit(shadow, shadow.get_rect(midbottom=(round(mouse.x), round(mouse.y) + 2)))
                self.screen.blit(frame, rect)
        self.screen.blit(foreground, (0, 0))
        # Tunnelteile werden nach den Möbeln gezeichnet, aber streng auf die
        # dunkle Öffnung und den freien Boden rechts davon begrenzt.
        for _mouse, frame, rect in tunnel_mice:
            self._draw_menu_mouse_tunnel_part(frame, rect)

    def _hit_menu_mouse(self, point: Tuple[int, int], now: float) -> bool:
        for mouse in reversed(self.menu_mice):
            rect = mouse.current_rect
            if not mouse.active or rect is None or not rect.collidepoint(point):
                continue
            local = point[0] - rect.left, point[1] - rect.top
            mask = mouse.current_mask
            if mask is None:
                continue
            if (
                0 <= local[0] < mask.get_size()[0]
                and 0 <= local[1] < mask.get_size()[1]
                and mask.get_at(local)
                and self._menu_mouse_visible_at(mouse, point)
            ):
                mouse.active = False
                mouse.current_rect = None
                mouse.spawn_at = now + self.menu_mouse_rng.uniform(20.0, 45.0)
                self.menu_mouse_hits += 1
                self.cans_game.sounds.play("hit")
                self.tracker.reset_state()
                self.armed_at = now + 0.28
                LOGGER.info("Menü-Maus getroffen; neuer Auftritt folgt zufällig")
                return True
        return False

    def _request_program_close(self) -> None:
        """Öffnet die geschützte PIN-Eingabe, beendet aber noch nichts."""

        if self.close_pin_active:
            return
        self.close_pin_return_view = self.view_mode
        self.close_pin_active = True
        self.close_pin_digits = ""
        self.close_pin_message = ""
        if self.aligner.phase == "success":
            self.view_mode = "diagnostic"
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.35
        LOGGER.info("Programmende angefordert: PIN-Eingabe geöffnet")

    def _cancel_program_close(self) -> None:
        self.close_pin_active = False
        self.close_pin_digits = ""
        self.close_pin_message = ""
        if self.aligner.phase == "success":
            self.view_mode = self.close_pin_return_view
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.45
        LOGGER.info("PIN-Eingabe für Programmende abgebrochen")

    def _append_close_pin_digit(self, digit: str) -> None:
        if not self.close_pin_active or digit not in "0123456789":
            return
        if len(self.close_pin_digits) >= 4:
            self.close_pin_digits = ""
        self.close_pin_digits += digit
        self.close_pin_message = ""
        self.cans_game.sounds.play("button")
        if len(self.close_pin_digits) < 4:
            return
        if self.close_pin_digits == "1919":
            LOGGER.info("Programmende mit korrekter PIN bestätigt")
            self.close_pin_active = False
            self.close_requested = True
            self.cans_game.sounds.play("finish")
            return
        self.close_pin_digits = ""
        self.close_pin_message = "PIN FALSCH – BITTE ERNEUT EINGEBEN"
        self.cans_game.sounds.play("math_wrong")
        LOGGER.warning("Programmende abgelehnt: falsche PIN")

    def _close_pin_buttons(self) -> list[tuple[str, pygame.Rect]]:
        width, height = self.screen.get_size()
        button_width = 132
        button_height = 55
        gap = 14
        left = width // 2 - (button_width * 3 + gap * 2) // 2
        top = max(310, height // 2 - 70)
        labels = (
            "1", "2", "3",
            "4", "5", "6",
            "7", "8", "9",
            "LÖSCHEN", "0", "ABBRECHEN",
        )
        return [
            (
                label,
                pygame.Rect(
                    left + (index % 3) * (button_width + gap),
                    top + (index // 3) * (button_height + gap),
                    button_width,
                    button_height,
                ),
            )
            for index, label in enumerate(labels)
        ]

    def _handle_close_pin_shot(
        self, point: Tuple[int, int], now: Optional[float] = None
    ) -> bool:
        if not self.close_pin_active:
            return False
        buttons = self._close_pin_buttons()
        selected = nearest_laser_button(
            point,
            buttons,
            expansion=(44, 30),
        )
        if selected is None:
            return False
        if selected == "ABBRECHEN":
            self._cancel_program_close()
        elif selected == "LÖSCHEN":
            self.close_pin_digits = self.close_pin_digits[:-1]
            self.close_pin_message = ""
            self.cans_game.sounds.play("button")
        else:
            self._append_close_pin_digit(selected)
        self.tracker.reset_state()
        current = now if now is not None else time.monotonic()
        self.armed_at = current + 0.22
        return True

    def _handle_close_pin_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self._cancel_program_close()
            return
        if event.key == pygame.K_BACKSPACE:
            self.close_pin_digits = self.close_pin_digits[:-1]
            self.close_pin_message = ""
            return
        digit = getattr(event, "unicode", "")
        if digit in "0123456789":
            self._append_close_pin_digit(digit)

    def _start_sighting(self) -> None:
        self._stop_standard_games()
        if self.chicken_game is not None:
            self.chicken_game.stop()
        self.tracker.set_moorhuhn_filter(False)
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False
        self.arcade_leaderboard.clear()
        self._reset_sighting_session()
        self.view_mode = "target"
        self.return_view_mode = "target"
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _select_game(self, name: str, available: bool) -> None:
        LOGGER.info("Menüauswahl: %s", name)
        if available and name == "DOSENSCHIE\u00dfEN":
            self._start_standard_game("cans", self.cans_game, "Dosenschießen")
            return
        if available and name == "TONTAUBENSCHIE\u00dfEN":
            self._start_standard_game("clay", self.clay_game, "Tontaubenschießen")
            return
        if available and name == "ZEITSCHIE\u00dfEN":
            self._start_standard_game("timed", self.timed_game, "Zeitschießen")
            return
        if available and name == "WASSER-ALARM":
            self._start_standard_game("water", self.water_alarm_game, "Wasser-Alarm")
            return
        if available and name == "REAKTION":
            self._start_standard_game("reaction", self.reaction_game, "Reaktion")
            return
        if available and name == "ZIELSCHEIBE":
            self._start_standard_game("range", self.target_range_game, "Zielscheibe")
            return
        if available and name == "BALLONJAGD":
            self._start_standard_game("balloons", self.balloon_game, "Ballonjagd")
            return
        if available and name == "ALIEN-ALARM":
            self._start_standard_game("aliens", self.alien_game, "Alien-Alarm")
            return
        if available and name == "STERNEJAGD":
            self._start_standard_game("stars", self.star_game, "Sternejagd")
            return
        if available and name == "RECHENDUELL":
            self._start_standard_game("math", self.math_game, "Rechenduell")
            return
        if available and name == "FARBENSPIEL":
            self._start_standard_game("colors", self.color_game, "Farbenspiel")
            return
        if available and name == "SCHATZSUCHE":
            self._start_standard_game("treasure", self.treasure_game, "Schatzsuche")
            return
        if available and name == "TIC-TAC-TOE":
            self._start_standard_game("tictactoe", self.tic_tac_toe_game, "Tic-Tac-Toe")
            return
        if available and name == "4 GEWINNT":
            self._start_standard_game("connect4", self.connect_four_game, "4 Gewinnt")
            return
        if available and name == "KÄSEKÄSTCHEN":
            self._start_standard_game("dots", self.dots_boxes_game, "Käsekästchen")
            return
        if available and name == "MEMORY-DUELL":
            self._start_standard_game("memory_duel", self.memory_duel_game, "Memory-Duell")
            return
        if available and name == "NIM-DUELL":
            self._start_standard_game("nim", self.nim_duel_game, "Nim-Duell")
            return
        if available and name == "REVERSI LIGHT":
            self._start_standard_game("reversi", self.reversi_light_game, "Reversi Light")
            return
        self.selected_game = name
        self.view_mode = "coming_soon"
        self.return_view_mode = "menu"
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _start_standard_game(self, view_mode: str, game, label: str) -> None:
        self._stop_standard_games(except_game=game)
        if self.chicken_game is not None:
            self.chicken_game.stop()
        self.tracker.set_moorhuhn_filter(False)
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False
        self.arcade_leaderboard.clear()
        game.start()
        self.standard_game_states[id(game)] = game.state
        self.standard_visual_transitions[id(game)] = False
        self.view_mode = view_mode
        self.return_view_mode = view_mode
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8
        LOGGER.info("%s geöffnet", label)

    def _handle_arcade_leaderboard_action(self, game, action: str, now: float) -> None:
        """Setzt Aktionen der gemeinsamen Bestenlistenansicht sicher um."""

        if action == "repeat":
            self.arcade_leaderboard.clear()
            game.start(now)
            self.standard_game_states[id(game)] = game.state
            self.standard_visual_transitions[id(game)] = False
            self.tracker.reset_state()
            self.armed_at = now + 0.6
            return
        if action == "menu":
            self.arcade_leaderboard.clear()
            self._show_menu()
            return
        if action != "ignored":
            # Tastatur-, Ergebnis- und Adminwechsel verändern große Flächen.
            self.tracker.reset_state()
            self.armed_at = now + 0.35

    def _start_chicken_game(self) -> None:
        self._stop_standard_games()
        self.tracker.set_moorhuhn_filter(False)
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False
        self.arcade_leaderboard.clear()
        if self.chicken_game is None:
            self.chicken_game = ChickenApp(self.screen)
        self.chicken_game.start()
        self.view_mode = "chickens"
        self.return_view_mode = "chickens"
        self.tracker.reset_state()
        # Das Laden der umfangreichen Originalgrafiken und der Wechsel vom roten
        # Originalmenü dürfen nicht als Laserschuss übernommen werden.
        self.armed_at = time.monotonic() + 1.2
        LOGGER.info("Moorhuhn geöffnet")

    def start_alignment(self) -> None:
        if self.camera_settings_open:
            self._close_camera_settings(save=False)
        self._stop_standard_games()
        if self.chicken_game is not None:
            self.chicken_game.stop()
        self.tracker.set_moorhuhn_filter(False)
        self.chicken_pan_was_moving = False
        self.chicken_visual_transition = False
        self.arcade_leaderboard.clear()
        self._reset_sighting_session()
        self.tracker.reset_state()
        self.aligner.start()
        self.manual_alignment_active = False
        self.armed_at = float("inf")
        self.view_mode = "diagnostic"

    def clear_shots(self) -> None:
        self._reset_sighting_session()
        self.last_peak_detection = None
        self.last_peak_mask_rgb = None
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def _reset_sighting_session(self) -> None:
        self.shots.clear()
        self.next_shot_number = 1
        self.sighting_step = 0
        self.sighting_phase = "shooting"
        self.stage_shots = []
        self.completed_groups = [[] for _ in range(5)]

    def _advance_sighting(self) -> None:
        if self.aligner.phase != "success":
            return
        if self.sighting_phase == "evaluation":
            if self.sighting_step < 4:
                self.sighting_step += 1
                self.stage_shots = []
                self.sighting_phase = "shooting"
            else:
                self.sighting_phase = "complete"
        elif self.sighting_phase == "complete":
            self._reset_sighting_session()
        else:
            return
        # Der Wechsel der projizierten Zielgrafik darf nicht als Laserimpuls
        # in den nächsten Abschnitt übernommen werden.
        self.tracker.reset_state()
        self.armed_at = time.monotonic() + 0.8

    def update(self, detection: LaserDetection, now: float) -> None:
        self.last_detection = detection
        if detection.frame_preview is not None:
            self.last_frame_rgb = detection.frame_preview
        if detection.mask_preview is not None:
            self.last_mask_rgb = detection.mask_preview
        if detection.shot and detection.point is not None:
            self.last_peak_detection = detection
            if detection.mask_preview is not None:
                self.last_peak_mask_rgb = detection.mask_preview.copy()
        self._update_camera_detection_test(detection, now)

        set_calibration_mode = getattr(self.tracker, "set_calibration_mode", None)
        if callable(set_calibration_mode):
            set_calibration_mode(self.aligner.active)

        # Nur die Spielübersicht darf die beiden Kamerabereiche links und
        # rechts neben der Leinwand als Seitenwechsel verwenden. Ein
        # Moduswechsel leert dabei alte Treffer, damit kein Außenimpuls in ein
        # gestartetes Spiel übernommen wird.
        set_overview_navigation = getattr(
            self.tracker,
            "set_overview_navigation",
            None,
        )
        if callable(set_overview_navigation) and set_overview_navigation(
            self.view_mode == "menu" and self.aligner.phase == "success"
        ):
            self.armed_at = max(self.armed_at, now + 0.25)
            return

        if self.aligner.active:
            if self.last_frame_rgb is not None:
                finished = self.aligner.feed(self.last_frame_rgb, now)
                if finished:
                    # Schwarz/Weiß-Umschaltung nicht als Schuss interpretieren.
                    if self.aligner.phase == "success":
                        self.tracker.reload_calibration()
                        optical = self.aligner.optical_result
                        apply_optical_profile = getattr(
                            self.tracker, "apply_startup_optical_profile", None
                        )
                        if optical is not None and callable(apply_optical_profile):
                            apply_optical_profile(
                                optical.active_filter_profile,
                                optical.filter_confidence,
                                white_peak=optical.white_peak,
                                ambient_luma=optical.ambient_luma,
                            )
                    else:
                        self.tracker.reset_state()
                    self.armed_at = now + 1.5
                    self.view_mode = (
                        "menu" if self.aligner.phase == "success" else "diagnostic"
                    )
                    if self.aligner.phase == "success":
                        self._reset_sighting_session()
                        self.return_view_mode = "menu"
            return

        standard_game = self._standard_game_for_view()
        if standard_game is not None and not self.arcade_leaderboard.is_active_for(
            self.view_mode
        ):
            if (
                self.view_mode == "range"
                and standard_game.state == "result"
                and now >= standard_game.result_until
            ):
                # Erst die spieleigene Gesamtauswertung drei Sekunden zeigen,
                # danach die getrennte Bestenlisten-/Namensebene öffnen.
                if self.arcade_leaderboard.prepare(self.view_mode, standard_game, now):
                    self.tracker.reset_state()
                    self.armed_at = now + 0.4
                    LOGGER.info(
                        "Zielscheibe: Gesamtauswertung vollständig angezeigt, "
                        "Bestenliste geöffnet"
                    )
                    return
            previous_game_state = self.standard_game_states.get(
                id(standard_game), standard_game.state
            )
            standard_game.update(now)
            current_game_state = standard_game.state
            self.standard_game_states[id(standard_game)] = current_game_state
            if current_game_state != previous_game_state:
                finished = (
                    self.view_mode == "range" and current_game_state == "result"
                ) or (
                    self.view_mode in {
                        "cans", "clay", "timed", "reaction", "tobia",
                        "balloons", "aliens", "stars", "math", "colors", "treasure",
                        "ocean",
                    }
                    and current_game_state == "game_over"
                )
                if finished and self.view_mode != "range":
                    self.arcade_leaderboard.prepare(self.view_mode, standard_game, now)
                # Countdown, Rundenwechsel und Ergebnisansichten verändern
                # große Teile des projizierten Bildes. Diese Umschaltung darf
                # nicht als neuer Laserimpuls in das Spiel zurücklaufen.
                self.tracker.reset_state()
                self.armed_at = now + 0.4
                LOGGER.info(
                    "%s: Bildübergang %s → %s, Schusserkennung kurz gesperrt",
                    standard_game.name,
                    previous_game_state,
                    current_game_state,
                )
                return
            transition_active = bool(
                getattr(standard_game, "visual_transition_active", False)
            )
            transition_was_active = self.standard_visual_transitions.get(
                id(standard_game), False
            )
            self.standard_visual_transitions[id(standard_game)] = transition_active
            if transition_active:
                # Besonders das rote Original-Vereinslogo wird als Schutzziel
                # eingeblendet. Der Grafikwechsel selbst ist kein Laserschuss.
                return
            if transition_was_active:
                self.tracker.reset_state()
                self.armed_at = now + 0.30
                LOGGER.info(
                    "%s: dynamischer Bildübergang beendet, Schusserkennung neu bereit",
                    standard_game.name,
                )
                return
        elif standard_game is not None:
            # Während Ergebnis, Namenseingabe oder Adminansicht bleibt der
            # Spielzustand eingefroren; insbesondere die Zielscheibe darf ihre
            # Auswertung nicht nach vier Sekunden automatisch schließen.
            pass
        elif (
            self.view_mode == "chickens"
            and self.aligner.phase == "success"
            and self.chicken_game is not None
            and not self.arcade_leaderboard.is_active_for("chickens")
        ):
            previous_chicken_state = self.chicken_game.state
            self.chicken_game.update(now)
            if self.chicken_game.state != previous_chicken_state:
                if self.chicken_game.state == "game_over":
                    self.tracker.set_moorhuhn_filter(False)
                    self.arcade_leaderboard.prepare("chickens", self.chicken_game, now)
                    self.armed_at = now + 0.4
                    LOGGER.info("Moorhuhn: eigene Top-10 geöffnet")
                    return
                if self.chicken_game.state == "playing":
                    self.tracker.set_moorhuhn_filter(True)
                    self.armed_at = now + 0.35
                    LOGGER.info("Moorhuhn: Schusserkennung für bewegte Spielszene bereit")
                    return
                if previous_chicken_state == "playing":
                    self.tracker.set_moorhuhn_filter(False)
                    self.armed_at = now + 0.35
                    return
        elif (
            self.view_mode == "chickens"
            and self.chicken_game is not None
            and self.arcade_leaderboard.is_active_for("chickens")
        ):
            if self.chicken_game.state == "playing" and self.chicken_game.visual_transition_active:
                self.chicken_visual_transition = True
                return
            if self.chicken_visual_transition:
                self.chicken_visual_transition = False
                self.tracker.reset_state()
                self.armed_at = now + 0.25
                LOGGER.info("Moorhuhn: Bildübergang beendet, Schusserkennung neu bereit")
                return
            pan_is_moving = (
                self.chicken_game.state == "playing"
                and abs(self.chicken_game.camera - self.chicken_game.camera_target) > 0.004
            )
            if pan_is_moving:
                self.chicken_pan_was_moving = True
                return
            if self.chicken_pan_was_moving:
                self.chicken_pan_was_moving = False
                self.tracker.reset_state()
                self.armed_at = now + 0.35
                LOGGER.info("Moorhuhn: Panorama steht, Schusserkennung neu bereit")
                return

        if now < self.armed_at:
            return
        if self.camera_settings_open and now < self.camera_original_preview_until:
            return
        if not detection.shot or detection.point is None:
            return

        # Jede bestätigte Laserpuls-Bedienung schaltet augenblicklich zurück
        # in den Pistolenmodus. Eine echte Mausbewegung blendet den Zeiger über
        # handle_event jederzeit wieder ein.
        self._hide_cursor_for_pistol()

        raw_mapped = None
        if self.aligner.homography is not None:
            raw_mapped = apply_homography(self.aligner.homography, detection.point)
        if raw_mapped is not None and self.view_mode == "menu":
            outside_direction = self._outside_menu_page_direction(raw_mapped)
            if outside_direction is not None:
                changed = self._change_menu_page(outside_direction, now)
                LOGGER.info(
                    "Spielübersicht: Außentreffer Kamera=%s, Richtung=%s, Wechsel=%s",
                    detection.point,
                    "rechts" if outside_direction > 0 else "links",
                    "ja" if changed else "nicht möglich",
                )
                return
        if raw_mapped is not None and not self.screen.get_rect().collidepoint(raw_mapped):
            LOGGER.info(
                "Treffer außerhalb der Leinwand ignoriert: Kamera=%s, Projektion=%s, Ansicht=%s",
                detection.point,
                raw_mapped,
                self.view_mode,
            )
            return
        mapped = (
            self.weapon_calibration.apply(raw_mapped, self.screen.get_size())
            if raw_mapped is not None
            else None
        )
        standard_game = self._standard_game_for_view()
        if mapped is not None and standard_game is not None:
            leaderboard_was_active = self.arcade_leaderboard.is_active_for(self.view_mode)
            if leaderboard_was_active:
                action = self.arcade_leaderboard.handle_shot(mapped, now)
                self._handle_arcade_leaderboard_action(standard_game, action, now)
            else:
                action = standard_game.handle_shot(mapped, now)
            LOGGER.info(
                "%s: Schuss bei Bildschirm=%s, Ergebnis=%s",
                standard_game.name,
                mapped,
                action,
            )
            if action == "menu" and not leaderboard_was_active:
                self._show_menu()
            return
        if mapped is not None and self.view_mode == "chickens" and self.chicken_game is not None:
            leaderboard_was_active = self.arcade_leaderboard.is_active_for("chickens")
            if leaderboard_was_active:
                action = self.arcade_leaderboard.handle_shot(mapped, now)
                self._handle_arcade_leaderboard_action(
                    self.chicken_game, action, now
                )
                LOGGER.info(
                    "Moorhuhn-Bestenliste: Schuss bei Bildschirm=%s, Ergebnis=%s",
                    mapped,
                    action,
                )
                return
            # Nur die laufende Spielszene enthält bewegte rote Kämme. In den
            # statischen Menüs würde diese Zusatzschwelle einen echten Laser auf
            # einem roten Button unnötig abschwächen oder vollständig sperren.
            if self.chicken_game.state == "playing" and not self.chicken_game.is_laser_signature(
                detection.peak_red_excess, detection.peak_delta
            ):
                LOGGER.info(
                    "Moorhuhn: rote Spielanimation verworfen bei Bildschirm=%s, "
                    "Fläche=%.1f, Rotüberschuss=%s, Änderung=%s",
                    mapped,
                    detection.area,
                    detection.peak_red_excess,
                    detection.peak_delta,
                )
                return
            action = self.chicken_game.handle_shot(mapped, now)
            LOGGER.info(
                "Moorhuhn: Schuss bei Bildschirm=%s, Ergebnis=%s, Fläche=%.1f, "
                "Rotüberschuss=%s, Änderung=%s, Sicherheit=%.2f",
                mapped,
                action,
                detection.area,
                detection.peak_red_excess,
                detection.peak_delta,
                detection.confidence,
            )
            if action == "menu":
                self._show_menu()
            return
        if mapped is not None and self._handle_laser_control(mapped, now):
            return
        if mapped is not None and self.view_mode == "diagnostic":
            # Die Diagnoseansicht braucht ein eigenes Trefferprotokoll. Bislang
            # wurden dort nur Schüsse auf Bedienelemente verarbeitet; ein
            # normaler Testschuss auf die Leinwand erreichte die Vorschau
            # „Letzter Schuss“ deshalb nie.
            record = ShotRecord(
                number=self.next_shot_number,
                camera_point=detection.point,
                screen_point=mapped,
                confidence=detection.confidence,
                timestamp=time.time(),
            )
            self.shots.append(record)
            self.next_shot_number += 1
            self.tracker.reset_state()
            self.armed_at = now + 0.18
            LOGGER.info(
                "DIAGNOSE-SCHUSS #%s Kamera=%s Leinwand=%s Confidence=%.3f",
                record.number,
                record.camera_point,
                record.screen_point,
                record.confidence,
            )
            return
        if self.view_mode != "target":
            return
        if self.aligner.phase == "success" and self.sighting_phase != "shooting":
            return
        record = ShotRecord(
            number=self.next_shot_number,
            camera_point=detection.point,
            # Beim Einschießen wird bewusst die unkorrigierte Treffpunktlage
            # gespeichert. Nur so kann eine neue Waffenabweichung gemessen werden.
            screen_point=raw_mapped,
            confidence=detection.confidence,
            timestamp=time.time(),
        )
        self.shots.append(record)
        self.stage_shots.append(record)
        self.next_shot_number += 1
        required = 5 if self.sighting_step == 0 else 3
        if len(self.stage_shots) >= required:
            self.completed_groups[self.sighting_step] = list(self.stage_shots)
            # Nach dem 17. Schuss direkt abschließen; die letzte Ecke ist
            # bereits Teil der Gesamtauswertung und braucht keinen Extra-Klick.
            self.sighting_phase = "complete" if self.sighting_step == 4 else "evaluation"
            if self.sighting_step == 4:
                self._calibrate_weapon_from_sighting()
            self.tracker.reset_state()
        LOGGER.info(
            "SCHUSS #%s Kamera=%s Bildschirm=%s Confidence=%.3f Area=%.1f Rot=%s Delta=%s",
            record.number,
            record.camera_point,
            record.screen_point,
            record.confidence,
            detection.area,
            detection.peak_red_excess,
            detection.peak_delta,
        )

    def _calibrate_weapon_from_sighting(self) -> None:
        stages = self._sighting_stages()
        groups = [
            [shot.screen_point for shot in group if shot.screen_point is not None]
            for group in self.completed_groups
        ]
        try:
            calibration = fit_weapon_calibration(
                groups,
                [stage[3] for stage in stages],
                self.screen.get_size(),
            )
            self.weapon_calibration = calibration
            self.alignment_changed_since_sighting = False
            self.weapon_calibration_message = self._weapon_calibration_text()
            if self.weapon_calibration_path is not None:
                save_weapon_calibration(
                    self.weapon_calibration_path,
                    calibration,
                    self.screen.get_size(),
                )
            LOGGER.info(
                "Waffenkalibrierung gespeichert: Korrektur x=%+.1f y=%+.1f, "
                "Restabweichung=%.1f px, Schüsse=%s",
                calibration.offset_x,
                calibration.offset_y,
                calibration.residual_px,
                calibration.sample_count,
            )
        except (ValueError, OSError) as exc:
            self.weapon_calibration_message = f"Waffenkorrektur nicht gespeichert: {exc}"
            LOGGER.warning("Waffenkalibrierung fehlgeschlagen: %s", exc)

    def _weapon_calibration_text(self) -> str:
        calibration = self.weapon_calibration
        return (
            f"Waffenkorrektur aktiv: X {calibration.offset_x:+.0f} px  ·  "
            f"Y {calibration.offset_y:+.0f} px  ·  Qualität ±{calibration.residual_px:.0f} px"
        )

    def _menu_entries(
        self,
    ) -> list[tuple[pygame.Rect, str, str, bool]]:
        width, _ = self.screen.get_size()
        margin = 50
        gap = 18
        card_width = (width - 2 * margin - 2 * gap) // 3
        card_height = 210
        definitions = self._menu_pages()[self.menu_page]
        entries: list[tuple[pygame.Rect, str, str, bool]] = []
        for index, (name, subtitle, available) in enumerate(definitions):
            column = index % 3
            row = index // 3
            rect = pygame.Rect(
                margin + column * (card_width + gap),
                150 + row * 228,
                card_width,
                card_height,
            )
            entries.append((rect, name, subtitle, available))
        return entries

    @staticmethod
    def _menu_pages() -> tuple[tuple[tuple[str, str, bool], ...], ...]:
        return (
            (
                ("WASSER-ALARM", "60 Sekunden Wasser-Arcade für alle", True),
                ("DOSENSCHIE\u00dfEN", "Dosen schnell und präzise treffen", True),
                ("ZEITSCHIE\u00dfEN", "Treffer gegen die laufende Uhr", True),
                ("TONTAUBENSCHIE\u00dfEN", "Fliegende Ziele klassisch treffen", True),
                ("REAKTION", "Ziele erkennen und sofort reagieren", True),
                ("ZIELSCHEIBE", "Variable Wertung und Ergebnisverlauf", True),
            ),
            (
                ("BALLONJAGD", "Bewegte Ballons schnell platzen lassen", True),
                ("ALIEN-ALARM", "Aliens erkennen und sicher treffen", True),
                ("STERNEJAGD", "Leuchtende Sterne einsammeln", True),
                ("RECHENDUELL", "Aufgabe lösen und richtige Zahl treffen", True),
                ("FARBENSPIEL", "Farben merken und Reihenfolge treffen", True),
                ("SCHATZSUCHE", "Gesuchte Schätze im Bild finden", True),
            ),
            (
                ("TIC-TAC-TOE", "Drei Zeichen in eine Reihe setzen", True),
                ("4 GEWINNT", "Steine taktisch in sieben Spalten setzen", True),
                ("KÄSEKÄSTCHEN", "Linien schließen und Kästchen erobern", True),
                ("MEMORY-DUELL", "Paare merken und Bonuszüge gewinnen", True),
                ("NIM-DUELL", "Den letzten Energiestab taktisch nehmen", True),
                ("REVERSI LIGHT", "Steine einschließen und Felder wenden", True),
            ),
        )

    def _menu_page_heading(self) -> str:
        # Die Spielkarten und Seitennavigation erklären die Auswahl bereits.
        # Eine zusätzliche Textzeile zwischen Titelschild und Karten stört nur
        # die ruhige Kulisse. Seite drei bleibt über zwei Figuren wortlos als
        # Zweispielerbereich erkennbar.
        return ""

    @property
    def menu_page_count(self) -> int:
        return len(self._menu_pages())

    def _change_menu_page(
        self, direction: int, now: Optional[float] = None
    ) -> bool:
        target = max(0, min(self.menu_page_count - 1, self.menu_page + direction))
        if target == self.menu_page:
            return False
        self.menu_page = target
        current = now if now is not None else time.monotonic()
        self.tracker.reset_state()
        self.armed_at = current + 0.35
        LOGGER.info("Spielübersicht: Seite %s/%s", self.menu_page + 1, self.menu_page_count)
        return True

    def _menu_arrow_at(self, point: Tuple[int, int]) -> Optional[int]:
        if self.menu_page > 0 and self.menu_previous_hit_rect.collidepoint(point):
            return -1
        if (
            self.menu_page < self.menu_page_count - 1
            and self.menu_next_hit_rect.collidepoint(point)
        ):
            return 1
        return None

    def _outside_menu_page_direction(
        self,
        raw_mapped: Tuple[int, int],
    ) -> Optional[int]:
        """Ordnet einen Kameratreffer seitlich der Leinwand einer Seite zu."""

        width, _ = self.screen.get_size()
        edge_guard = max(10, round(width * 0.012))
        if raw_mapped[0] <= -edge_guard:
            return -1
        if raw_mapped[0] >= width - 1 + edge_guard:
            return 1
        return None

    def _menu_entry_at(self, point: Tuple[int, int]) -> Optional[tuple[str, bool]]:
        entries = self._menu_entries()
        selected = nearest_laser_button(
            point,
            ((index, entry[0]) for index, entry in enumerate(entries)),
        )
        if selected is None:
            return None
        _, name, _, available = entries[selected]
        return name, available

    def _menu_entry_number_at(self, point: Tuple[int, int]) -> Optional[int]:
        entries = self._menu_entries()
        selected = nearest_laser_button(
            point,
            ((index, entry[0]) for index, entry in enumerate(entries)),
        )
        if selected is None:
            return None
        return self.menu_page * len(self._menu_pages()[0]) + selected + 1

    def _coming_soon_rect(self) -> pygame.Rect:
        width, height = self.screen.get_size()
        card = pygame.Rect(0, 0, 660, 350)
        card.center = (width // 2, height // 2 + 20)
        return card

    def _handle_laser_control(
        self, point: Tuple[int, int], now: Optional[float] = None
    ) -> bool:
        """Führt eine sichtbare Schaltfläche durch einen Lasertreffer aus."""

        if self.camera_settings_open:
            return self._handle_camera_settings_shot(point, now)
        if self.close_pin_active:
            return self._handle_close_pin_shot(point, now)

        controls: list[tuple[pygame.Rect, str, Callable[[], None]]] = []
        if self.view_mode == "target" and self.aligner.phase == "success":
            if self.sighting_phase == "evaluation":
                controls.append(
                    (self._stage_evaluation_rect(), "AUSWERTUNG / WEITER", self._advance_sighting)
                )
                controls.append((self.advance_button, "WEITER", self._advance_sighting))
            elif self.sighting_phase == "complete":
                controls.append(
                    (
                        self._complete_evaluation_rect(),
                        "GESAMTAUSWERTUNG / WIEDERHOLEN",
                        self._advance_sighting,
                    )
                )
                controls.append((self.advance_button, "WEITER", self._advance_sighting))
            controls.extend(
                [
                    (self.target_menu_button, "MENÜ", self._show_menu),
                    (self.target_live_button, "KAMERABILD", self._toggle_view),
                    (self.target_align_button, "NEU AUSRICHTEN", self.start_alignment),
                    (self.target_clear_button, "ABLAUF NEU", self.clear_shots),
                ]
            )
        elif self.view_mode == "menu" and self.aligner.phase == "success":
            if self._easter_moorhuhn_rect().collidepoint(point):
                self._register_easter_moorhuhn_shot(now)
                return True
            if self.easter_moorhuhn_armed:
                number = self._menu_entry_number_at(point)
                if number is not None:
                    self._register_easter_moorhuhn_code_shot(number, now)
                else:
                    self._cancel_easter_moorhuhn_sequence(
                        now,
                        reason="falsche Stelle",
                    )
                return True
            # Die Einstellungstafel liegt bewusst in der unteren linken
            # Geheim-Ecke. Sie hat Vorrang, damit der sichtbare Knopf immer
            # zuverlässig das Kamerabild öffnet.
            if (
                self.menu_settings_hit_rect.collidepoint(point)
                and not self.easter_corner_hits
            ):
                LOGGER.info("Laserbedienung: EINSTELLUNGEN bei Bildschirm=%s", point)
                self._toggle_view()
                return True
            current = now if now is not None else time.monotonic()
            if self.easter_corner_hits:
                corner = self._easter_corner_at(point)
                if corner is not None:
                    self._register_easter_corner_shot(corner, now)
                    return True
            if self._hit_menu_mouse(point, current):
                return True
            corner = self._easter_corner_at(point)
            if corner is not None:
                self._register_easter_corner_shot(corner, now)
                return True
            if self._easter_title_rect().collidepoint(point):
                self._register_easter_title_shot(now)
                return True
            page_direction = self._menu_arrow_at(point)
            if page_direction is not None:
                self._change_menu_page(page_direction, now)
                return True
            for rect, name, _, available in self._menu_entries():
                controls.append(
                    (
                        rect,
                        name,
                        lambda game=name, ready=available: self._select_game(game, ready),
                    )
                )
        elif self.view_mode == "coming_soon" and self.aligner.phase == "success":
            controls.append((self._coming_soon_rect(), "ZURÜCK ZUM MENÜ", self._show_menu))
        elif self.view_mode == "diagnostic" and (
            self.aligner.phase == "success" or self.aligner.homography is not None
        ):
            if self.aligner.phase == "success":
                controls.extend(
                    [
                        (self.diagnostic_target_button, "MENÜ", self._show_menu),
                        (
                            self.diagnostic_sighting_button,
                            "EINSCHIEßEN",
                            self._start_sighting,
                        ),
                        (
                            self.diagnostic_close_button,
                            "PROGRAMM BEENDEN",
                            self._request_program_close,
                        ),
                    ]
                )
            controls.extend(
                [
                    (
                        self.diagnostic_settings_button,
                        "KAMERA EINSTELLEN",
                        self._open_camera_settings,
                    ),
                    (self.align_button, "NEU AUSRICHTEN", self.start_alignment),
                ]
            )

        selected = nearest_laser_button(
            point,
            ((index, control[0]) for index, control in enumerate(controls)),
        )
        if selected is not None:
            _, label, action = controls[selected]
            LOGGER.info("Laserbedienung: %s bei Bildschirm=%s", label, point)
            action()
            return True
        return False

    def _easter_title_rect(self) -> pygame.Rect:
        return self.menu_title_rect.inflate(28, 20)

    def _easter_moorhuhn_rect(self) -> pygame.Rect:
        """Dezent in der unteren Bühnenfläche versteckte Feder."""

        return pygame.Rect(112, self.screen.get_height() - 148, 64, 54)

    def _register_easter_moorhuhn_shot(
        self, now: Optional[float] = None
    ) -> None:
        current = now if now is not None else time.monotonic()
        # Die Feder schaltet den geheimen Pfad ein oder aus. Danach müssen die
        # Spielkarten 01–06–02–05 in genau dieser Reihenfolge getroffen werden.
        self.easter_moorhuhn_armed = not self.easter_moorhuhn_armed
        self.easter_moorhuhn_progress = 0
        self.cans_game.sounds.play("button")
        self.tracker.reset_state()
        self.armed_at = current + 0.22
        LOGGER.info(
            "Moorhuhn-Easter-Egg: Geheimfolge %s",
            "aktiv" if self.easter_moorhuhn_armed else "abgebrochen",
        )

    def _register_easter_moorhuhn_code_shot(
        self, number: int, now: Optional[float] = None
    ) -> None:
        if not self.easter_moorhuhn_armed:
            return
        current = now if now is not None else time.monotonic()
        expected = self.MOORHUHN_EASTER_CODE[self.easter_moorhuhn_progress]
        if number == expected:
            self.easter_moorhuhn_progress += 1
            self.cans_game.sounds.play("button")
        else:
            self._cancel_easter_moorhuhn_sequence(
                current,
                reason=f"falsche Karte {number:02d} statt {expected:02d}",
            )
            return
        self.tracker.reset_state()
        self.armed_at = current + 0.22
        LOGGER.info(
            "Moorhuhn-Easter-Egg: Karte %02d, Fortschritt %s/4",
            number,
            self.easter_moorhuhn_progress,
        )
        if self.easter_moorhuhn_progress >= len(self.MOORHUHN_EASTER_CODE):
            self.easter_moorhuhn_armed = False
            self.easter_moorhuhn_progress = 0
            self._start_chicken_game()

    def _cancel_easter_moorhuhn_sequence(
        self,
        now: Optional[float] = None,
        *,
        reason: str,
    ) -> None:
        current = now if now is not None else time.monotonic()
        self.easter_moorhuhn_armed = False
        self.easter_moorhuhn_progress = 0
        self.cans_game.sounds.play("math_wrong")
        self.tracker.reset_state()
        self.armed_at = current + 0.22
        LOGGER.info("Moorhuhn-Easter-Egg deaktiviert: %s", reason)

    def _draw_moorhuhn_easter_egg(self) -> None:
        rect = self._easter_moorhuhn_rect()
        active = self.easter_moorhuhn_armed
        # Fotorealistisch, dunkel und halbtransparent wie ein zufälliges Detail
        # im Schrank. Die großzügige unsichtbare Trefferfläche bleibt bestehen.
        if self.menu_feather_image is not None:
            feather = self.menu_feather_image.copy()
            if active:
                feather.set_alpha(215)
            self.screen.blit(feather, feather.get_rect(center=rect.center))

        # Nach dem ersten Fund wird ein kleines Ei sichtbar. Vier unbeschriftete
        # Kerben zeigen nur den Fortschritt; die Folge 01–06–02–05 bleibt
        # ansonsten verborgen.
        if active:
            egg = pygame.Rect(rect.right + 4, rect.top + 10, 28, 37)
            pygame.draw.ellipse(self.screen, (0, 29, 43), egg)
            pygame.draw.ellipse(self.screen, self.TARGET_GREEN, egg, 2)
            crack_points = (
                (egg.centerx, egg.top + 5),
                (egg.centerx - 4, egg.top + 12),
                (egg.centerx + 3, egg.top + 18),
                (egg.centerx - 2, egg.top + 25),
            )
            visible_crack = crack_points[
                : min(len(crack_points), self.easter_moorhuhn_progress + 1)
            ]
            if len(visible_crack) >= 2:
                pygame.draw.lines(
                    self.screen,
                    self.TARGET_CYAN,
                    False,
                    visible_crack,
                    2,
                )
            elif visible_crack:
                pygame.draw.circle(
                    self.screen,
                    self.TARGET_CYAN,
                    visible_crack[0],
                    2,
                )
            for index in range(4):
                pygame.draw.circle(
                    self.screen,
                    self.TARGET_GREEN if index < self.easter_moorhuhn_progress else self.TARGET_MUTED,
                    (rect.left + 4 + index * 9, rect.bottom + 4),
                    3,
                    0 if index < self.easter_moorhuhn_progress else 1,
                )

    def _easter_corner_rects(self) -> dict[str, pygame.Rect]:
        width, height = self.screen.get_size()
        size = max(86, round(min(width, height) * 0.13))
        return {
            "oben_links": pygame.Rect(0, 0, size, size),
            "oben_rechts": pygame.Rect(width - size, 0, size, size),
            "unten_links": pygame.Rect(0, height - size, size, size),
            "unten_rechts": pygame.Rect(width - size, height - size, size, size),
        }

    def _easter_corner_at(self, point: Tuple[int, int]) -> Optional[str]:
        return next(
            (name for name, rect in self._easter_corner_rects().items() if rect.collidepoint(point)),
            None,
        )

    def _register_easter_corner_shot(
        self, corner: str, now: Optional[float] = None
    ) -> None:
        current = now if now is not None else time.monotonic()
        self.easter_corner_hits.add(corner)
        self.tracker.reset_state()
        self.armed_at = current + 0.22
        LOGGER.info(
            "Geheimes Tobias-Spiel: Ecke %s, Fortschritt %s/4",
            corner,
            len(self.easter_corner_hits),
        )
        if len(self.easter_corner_hits) >= 4:
            self.easter_corner_hits.clear()
            self._start_standard_game(
                "tobia",
                self.tobia_duel_game,
                "Tobias Blitzduell",
            )

    def _register_easter_title_shot(self, now: Optional[float] = None) -> None:
        current = now if now is not None else time.monotonic()
        self.easter_title_hits += 1
        self.tracker.reset_state()
        self.armed_at = current + 0.22
        LOGGER.info(
            "Geheime Meeresmission: Titelschuss %s/4",
            min(4, self.easter_title_hits),
        )
        if self.easter_title_hits >= 4:
            self.easter_title_hits = 0
            self._start_standard_game(
                "ocean",
                self.ocean_cleanup_game,
                "Annas Meeresmission",
            )

    def draw(self, fps: float) -> None:
        self._update_cursor_visibility()
        if self.camera_settings_open:
            self._draw_camera_settings(fps)
            return
        if self.aligner.active:
            self._draw_alignment_pattern()
            return

        if self.view_mode == "menu" and self.aligner.phase == "success":
            self._draw_main_menu()
            return

        standard_game = self._standard_game_for_view()
        if standard_game is not None:
            standard_game.draw()
            if self.arcade_leaderboard.is_active_for(self.view_mode):
                self.arcade_leaderboard.draw()
            elif (
                self.view_mode in {
                    "cans", "clay", "timed", "reaction",
                    "balloons", "aliens", "stars", "math", "colors", "treasure",
                    "tobia",
                }
                and standard_game.state == "ready"
            ):
                self.arcade_leaderboard.draw_ready_preview(self.view_mode)
            return

        if (
            self.view_mode == "chickens"
            and self.aligner.phase == "success"
            and self.chicken_game is not None
        ):
            self.chicken_game.draw()
            if self.arcade_leaderboard.is_active_for("chickens"):
                self.arcade_leaderboard.draw()
            elif self.chicken_game.state == "ready":
                self.arcade_leaderboard.draw_ready_preview("chickens")
            return

        if self.view_mode == "coming_soon" and self.aligner.phase == "success":
            self._draw_game_placeholder()
            return

        if self.view_mode == "target" and self.aligner.phase == "success":
            self._draw_sighting_screen()
            return

        self.screen.fill(self.BG)
        self._draw_header(fps)
        preview_rect = self._draw_live_view()
        self._draw_side_panel()
        self._draw_shot_list()
        self._draw_buttons()
        if preview_rect and self.last_detection:
            self._draw_detection_overlay(preview_rect)
        if self.close_pin_active:
            self._draw_close_pin_overlay()

    def _draw_main_menu(self, now: Optional[float] = None) -> None:
        width, height = self.screen.get_size()
        draw_frame(self.screen, "menu")

        title = self.menu_title_image
        if title is not None:
            title_rect = title.get_rect(center=self.menu_title_rect.center)
            self.screen.blit(title, title_rect)
        else:
            title = self.font_menu_title.render("SCHIEẞKINO", True, self.TARGET_CYAN)
            title_rect = title.get_rect(midtop=(width // 2, 18))
            self.screen.blit(title, title_rect)
        self._draw_moorhuhn_easter_egg()
        if self.easter_title_hits:
            bubble_start = title_rect.right + 12
            for index in range(4):
                radius = 5 + index % 2
                color = self.TARGET_GREEN if index < self.easter_title_hits else self.TARGET_MUTED
                pygame.draw.circle(
                    self.screen,
                    color,
                    (bubble_start + index * 18, 42 + (index % 2) * 10),
                    radius,
                    2,
                )
        if self.easter_corner_hits:
            for name, rect in self._easter_corner_rects().items():
                color = self.TARGET_GREEN if name in self.easter_corner_hits else self.TARGET_MUTED
                x = rect.left + 16 if "links" in name else rect.right - 16
                y = rect.top + 16 if "oben" in name else rect.bottom - 16
                pygame.draw.circle(self.screen, color, (x, y), 8, 2)
        if self.menu_page == 2:
            # Zwei Figuren machen den Zweispielermodus auch ohne Lesen auf
            # einen Blick erkennbar. Die Farben entsprechen den Spielfarben.
            for center_x, color in (
                (width // 2 - 18, self.TARGET_CYAN),
                (width // 2 + 18, self.TARGET_GREEN),
            ):
                pygame.draw.circle(self.screen, color, (center_x, 82), 7)
                pygame.draw.arc(
                    self.screen,
                    color,
                    pygame.Rect(center_x - 11, 89, 22, 14),
                    math.pi,
                    math.tau,
                    3,
                )

        first_number = self.menu_page * len(self._menu_pages()[0]) + 1
        for index, (rect, name, description, available) in enumerate(
            self._menu_entries(), start=first_number
        ):
            self._draw_game_card(rect, index, name, description, available)

        self._draw_menu_navigation()
        self._draw_menu_mice(time.monotonic() if now is None else now)
        self._draw_menu_settings_button()

    def _draw_menu_settings_button(self) -> None:
        """Setzt das fotorealistische Zahnrad bündig in die obere Kulisse."""

        if self.menu_settings_image is not None:
            self.screen.blit(self.menu_settings_image, self.menu_settings_button)

    def _draw_menu_navigation(self) -> None:
        """Kleine Seitenpfeile mit großzügiger, kartenfreier Trefferzone."""

        def draw_arrow(rect: pygame.Rect, direction: int, active: bool) -> None:
            color = self.TARGET_GREEN if active else self.TARGET_MUTED
            draw_vintage_enamel_panel(
                self.screen,
                rect,
                2 if active else 3,
                active=active,
                alpha=224 if active else 128,
                shadow=False,
            )
            pygame.draw.rect(self.screen, color, rect, 2, border_radius=11)
            center_x, center_y = rect.center
            # Zwei gestaffelte Emaille-Chevrons sind aus der Entfernung klarer
            # als die frühere einzelne technische Linie.
            for offset in (-5, 5):
                arrow_x = center_x + offset * direction
                if direction < 0:
                    points = (
                        (arrow_x + 5, center_y - 11),
                        (arrow_x - 5, center_y),
                        (arrow_x + 5, center_y + 11),
                    )
                else:
                    points = (
                        (arrow_x - 5, center_y - 11),
                        (arrow_x + 5, center_y),
                        (arrow_x - 5, center_y + 11),
                    )
                pygame.draw.lines(self.screen, (0, 8, 15), False, points, 6)
                pygame.draw.lines(self.screen, color, False, points, 3)

        draw_arrow(self.menu_previous_button, -1, self.menu_page > 0)
        draw_arrow(
            self.menu_next_button,
            1,
            self.menu_page < self.menu_page_count - 1,
        )

        # Die Seitenplakette sitzt bewusst im freien Raum direkt unter den
        # Spielkarten. So gehört sie optisch zur Übersicht und konkurriert
        # nicht mit den drei großen Bedienfeldern am unteren Rand.
        indicator_y = 620
        navigation_plate = pygame.Rect(
            self.screen.get_width() // 2 - 82,
            indicator_y - 12,
            164,
            50,
        )
        draw_translucent_panel(
            self.screen,
            navigation_plate,
            (0, 8, 18),
            alpha=104,
            border_radius=15,
        )
        pygame.draw.rect(
            self.screen,
            (0, 76, 88),
            navigation_plate,
            1,
            border_radius=15,
        )
        # Kleine Emaille-Ornamente greifen die geschwungenen Messingelemente
        # des Hintergrunds auf, bleiben aber laserneutral und zurückhaltend.
        ornament_color = (0, 108, 112)
        for direction in (-1, 1):
            anchor_x = navigation_plate.centerx + direction * 93
            pygame.draw.arc(
                self.screen,
                ornament_color,
                pygame.Rect(anchor_x - 15, indicator_y - 10, 30, 22),
                math.pi * (0.05 if direction < 0 else 0.95),
                math.pi * (1.05 if direction < 0 else 1.95),
                2,
            )
            tip_x = anchor_x + direction * 13
            pygame.draw.circle(self.screen, ornament_color, (tip_x, indicator_y + 1), 3, 1)
        expected_page = None
        if self.easter_moorhuhn_armed:
            expected = self.MOORHUHN_EASTER_CODE[self.easter_moorhuhn_progress]
            expected_page = (expected - 1) // len(self._menu_pages()[0])
        for index in range(self.menu_page_count):
            x = self.screen.get_width() // 2 + (index - (self.menu_page_count - 1) / 2) * 21
            color = (
                self.TARGET_GREEN
                if index == self.menu_page
                else self.TARGET_CYAN
                if index == expected_page
                else self.TARGET_MUTED
            )
            pygame.draw.circle(self.screen, (0, 6, 14), (round(x), indicator_y), 6)
            pygame.draw.circle(self.screen, color, (round(x), indicator_y), 5, 2)
            if index == self.menu_page:
                pygame.draw.circle(self.screen, color, (round(x), indicator_y), 2)
        page = self.font_small.render(
            f"SEITE {self.menu_page + 1} / {self.menu_page_count}",
            True,
            (0, 174, 184),
        )
        self.screen.blit(
            page,
            page.get_rect(midtop=(self.screen.get_width() // 2, indicator_y + 9)),
        )

    def _draw_game_card(
        self,
        rect: pygame.Rect,
        number: int,
        name: str,
        description: str,
        available: bool,
    ) -> None:
        border = self.TARGET_GREEN if available else self.TARGET_MUTED
        title_color = self.TARGET_CYAN if available else (0, 150, 190)
        # Die Varianten wechseln wie eine Sammlung alter Vereinsschilder,
        # behalten aber exakt dieselbe Anordnung und Bedienlogik.
        draw_vintage_enamel_panel(
            self.screen,
            rect,
            (number - 1) % 6,
            active=available,
            alpha=210,
        )
        self._draw_game_card_background(rect, name)
        self._draw_game_card_art(rect, name, available)

        # Nur der tatsächliche Textbereich erhält eine halbtransparente
        # Emaillelasur. Dadurch bleibt die individuelle Spielwelt auf jeder
        # Karte deutlich sichtbar, ohne lange deutsche Titel zu verschlucken.
        text_backplate = pygame.Rect(
            rect.left + 10,
            rect.top + 52,
            rect.width - 20,
            100,
        )
        draw_translucent_panel(
            self.screen,
            text_backplate,
            (0, 10, 20),
            alpha=42 if available else 86,
            border_radius=9,
        )

        number_badge = pygame.Rect(rect.left + 14, rect.top + 15, 48, 31)
        draw_translucent_panel(
            self.screen,
            number_badge,
            (0, 12, 20),
            alpha=238,
            border_radius=7,
        )
        pygame.draw.rect(self.screen, border, number_badge, 2, border_radius=7)
        number_surface = self.font.render(f"{number:02d}", True, border)
        self.screen.blit(number_surface, number_surface.get_rect(center=number_badge.center))
        self._draw_menu_aim_point(rect, border)
        if (
            self.easter_moorhuhn_armed
            and number == self.MOORHUHN_EASTER_CODE[self.easter_moorhuhn_progress]
        ):
            self._draw_easter_footprint((rect.right - 67, rect.top + 31))

        card_font = self._fitted_card_font(name, rect.width - 36)
        title = card_font.render(name, True, title_color)
        self.screen.blit(title, (rect.left + 18, rect.top + 60))
        # Rechts unten bleibt ein fester freier Bereich für das Spielmotiv.
        description_color = (0, 184, 199) if available else (0, 105, 125)
        for line_index, line in enumerate(textwrap.wrap(description, width=23)[:2]):
            surface = self.font_small.render(line, True, description_color)
            self.screen.blit(surface, (rect.left + 18, rect.top + 101 + line_index * 22))

    def _draw_game_card_background(self, rect: pygame.Rect, name: str) -> None:
        """Legt eine individuelle, ruhige Spielwelt in das Blechschild."""

        theme_map = {
            "WASSER-ALARM": "water",
            "DOSENSCHIEßEN": "cans",
            "ZEITSCHIEßEN": "timed",
            "TONTAUBENSCHIEßEN": "clay",
            "REAKTION": "reaction",
            "ZIELSCHEIBE": "range",
            "BALLONJAGD": "balloons",
            "ALIEN-ALARM": "aliens",
            "STERNEJAGD": "stars",
            "RECHENDUELL": "math",
            "FARBENSPIEL": "colors",
            "SCHATZSUCHE": "treasure",
        }
        duel_map = {
            "TIC-TAC-TOE": "tic_tac_toe_v2.png",
            "4 GEWINNT": "connect_four_v2.png",
            "KÄSEKÄSTCHEN": "dots_boxes_v2.png",
            "MEMORY-DUELL": "memory_v2.png",
            "NIM-DUELL": "nim_v2.png",
            "REVERSI LIGHT": "reversi_v2.png",
        }
        inner = rect.inflate(-12, -12)
        cache_key = name, inner.size
        artwork = self.menu_card_background_cache.get(cache_key)
        if artwork is None:
            if name == "WASSER-ALARM":
                path = (
                    Path(__file__).resolve().parents[1]
                    / "assets"
                    / "water_alarm"
                    / "pool_background_v3.png"
                )
                try:
                    world = pygame.image.load(str(path))
                    if pygame.display.get_surface() is not None:
                        world = world.convert()
                except (FileNotFoundError, pygame.error) as exc:
                    LOGGER.warning("Wasser-Spielkartenmotiv %s fehlt: %s", path, exc)
                    world = build_theme_background((1024, 768), "treasure")
            elif name in theme_map:
                world = build_theme_background((1024, 768), theme_map[name])
            else:
                path = (
                    Path(__file__).resolve().parents[1]
                    / "assets"
                    / "duel_v2"
                    / duel_map[name]
                )
                try:
                    world = pygame.image.load(str(path))
                    if pygame.display.get_surface() is not None:
                        world = world.convert_alpha()
                except (FileNotFoundError, pygame.error) as exc:
                    LOGGER.warning("Spielkartenmotiv %s fehlt: %s", path, exc)
                    world = build_theme_background((1024, 768), "menu")

            source_width, source_height = world.get_size()
            target_ratio = inner.width / max(1, inner.height)
            source_ratio = source_width / max(1, source_height)
            if source_ratio > target_ratio:
                crop_width = max(1, round(source_height * target_ratio))
                crop = pygame.Rect((source_width - crop_width) // 2, 0, crop_width, source_height)
            else:
                crop_height = max(1, round(source_width / target_ratio))
                crop = pygame.Rect(0, (source_height - crop_height) // 2, source_width, crop_height)
            artwork = pygame.transform.smoothscale(world.subsurface(crop), inner.size)
            neutralize_laser_red(artwork)
            # Wasser- und Tontaubenkarte enthalten großflächig Himmel bzw.
            # Spiegelungen. Dort bleibt bewusst mehr Reserve für den roten
            # Laserpuls, damit auch ein Schuss abseits der Schrift sicher als
            # Kartenwahl erkannt wird.
            needs_extra_laser_reserve = name in {
                "WASSER-ALARM",
                "TONTAUBENSCHIEßEN",
            }
            limit_projected_brightness(
                artwork, 132 if needs_extra_laser_reserve else 164
            )
            # Eine leichte Lasur verbindet das Bild mit dem Blechschild. Die
            # frühere großflächige Abdunklung hat die Motive fast vollständig
            # verborgen; Lesbarkeit entsteht nun gezielt im Textbereich.
            shade = pygame.Surface(inner.size, pygame.SRCALPHA)
            shade.fill(
                (
                    0,
                    4,
                    10,
                    52
                    if needs_extra_laser_reserve
                    else 42
                    if name in duel_map
                    else 30,
                )
            )
            artwork.blit(shade, (0, 0))
            pygame.draw.rect(artwork, (0, 88, 103), artwork.get_rect(), 1, border_radius=9)
            self.menu_card_background_cache[cache_key] = artwork
        self.screen.blit(artwork, inner.topleft)

    def _draw_easter_footprint(self, center: Tuple[int, int]) -> None:
        """Dezenter Hühnerfuß markiert nur den nächsten Schritt der Geheimfolge."""

        pygame.draw.line(
            self.screen,
            self.TARGET_CYAN,
            (center[0], center[1] + 9),
            (center[0], center[1] - 2),
            2,
        )
        for offset_x in (-8, 0, 8):
            pygame.draw.line(
                self.screen,
                self.TARGET_GREEN,
                (center[0], center[1] - 1),
                (center[0] + offset_x, center[1] - 10 + abs(offset_x) // 3),
                2,
            )

    def _draw_game_card_art(self, rect: pygame.Rect, name: str, available: bool) -> None:
        """Zeigt jede Spielwelt bereits in der Übersicht als eigenes Motiv."""

        art = pygame.Surface(rect.size, pygame.SRCALPHA)
        cyan = (0, 205, 245, 118 if available else 62)
        green = (0, 225, 120, 104 if available else 54)
        muted = (0, 105, 138, 78 if available else 42)
        # Das Motiv hat seinen eigenen Bereich rechts unten. So überlagert es
        # weder Titel noch Beschreibung, auch nicht bei langen deutschen Texten.
        center = (rect.width - 58, rect.height - 54)
        pygame.draw.circle(art, (0, 3, 14, 174), (center[0] + 3, center[1] + 5), 58)
        pygame.draw.circle(art, (0, 48, 70, 112), center, 54)
        pygame.draw.circle(art, muted, center, 54, 2)
        pygame.draw.arc(
            art,
            cyan,
            pygame.Rect(center[0] - 49, center[1] - 49, 98, 98),
            3.45,
            5.95,
            3,
        )
        pygame.draw.arc(
            art,
            green,
            pygame.Rect(center[0] - 43, center[1] - 43, 86, 86),
            0.25,
            2.55,
            2,
        )
        preview_assets = {
            "TONTAUBENSCHIEßEN": ("clay", (104, 72)),
            "DOSENSCHIEßEN": ("can", (72, 104)),
            "ZEITSCHIEßEN": ("mechanical_target", (104, 104)),
            "WASSER-ALARM": ("water_duck", (112, 78)),
            "REAKTION": ("mechanical_target", (104, 104)),
            "ZIELSCHEIBE": ("mechanical_target", (104, 104)),
            "BALLONJAGD": ("balloon", (76, 110)),
            "ALIEN-ALARM": ("alien", (88, 108)),
            "STERNEJAGD": ("star", (104, 104)),
            "SCHATZSUCHE": ("treasure_chest", (112, 88)),
        }
        preview = preview_assets.get(name)
        if preview is not None:
            sprite = load_target_sprite(preview[0], preview[1], brightness_limit=146).copy()
            sprite.set_alpha(222 if available else 102)
            art.blit(sprite, sprite.get_rect(center=center))
            self.screen.blit(art, rect.topleft)
            return
        duel_preview = {
            "TIC-TAC-TOE": (("tic_tac_toe", 0, (54, 54), (-24, -7)), ("tic_tac_toe", 1, (54, 54), (25, 18))),
            "4 GEWINNT": (("connect_four", 0, (51, 51), (-20, 17)), ("connect_four", 1, (51, 51), (19, -15))),
            "KÄSEKÄSTCHEN": (("dots_boxes", 0, (38, 38), (-34, -24)), ("dots_boxes", 1, (78, 20), (12, -24)), ("dots_boxes", 2, (68, 60), (6, 23))),
            "MEMORY-DUELL": (("memory", 1, (43, 39), (-24, -22)), ("memory", 3, (43, 39), (23, -22)), ("memory", 5, (43, 39), (-24, 22)), ("memory", 7, (43, 39), (23, 22))),
            "NIM-DUELL": (("nim", 0, (26, 67), (-29, 4)), ("nim", 0, (26, 67), (0, -8)), ("nim", 0, (26, 67), (29, 4))),
            "REVERSI LIGHT": (("reversi", 0, (49, 49), (-20, -17)), ("reversi", 1, (49, 49), (20, 17)), ("reversi", 1, (39, 39), (23, -22)), ("reversi", 0, (39, 39), (-23, 22))),
        }.get(name)
        if duel_preview is not None:
            for asset_name, asset_index, size, offset in duel_preview:
                sprite = load_duel_sprite(asset_name, asset_index, size).copy()
                sprite.set_alpha(228 if available else 98)
                position = center[0] + offset[0], center[1] + offset[1]
                art.blit(sprite, sprite.get_rect(center=position))
            self.screen.blit(art, rect.topleft)
            return
        if name == "TONTAUBENSCHIEßEN":
            pygame.draw.arc(
                art,
                muted,
                pygame.Rect(center[0] - 55, center[1] - 30, 110, 60),
                0.15,
                2.75,
                3,
            )
            pygame.draw.ellipse(art, green, pygame.Rect(center[0]-25, center[1]-8, 50, 16))
            pygame.draw.ellipse(art, cyan, pygame.Rect(center[0]-25, center[1]-8, 50, 16), 3)
        elif name == "DOSENSCHIEßEN":
            for offset_x, offset_y in ((-26, 12), (0, 12), (-13, -27)):
                can = pygame.Rect(center[0]+offset_x-10, center[1]+offset_y-22, 20, 44)
                pygame.draw.rect(art, muted, can, border_radius=6)
                pygame.draw.rect(art, cyan, can, 2, border_radius=6)
                pygame.draw.line(art, green, (can.left+3, can.centery), (can.right-3, can.centery), 2)
        elif name == "ZEITSCHIEßEN":
            pygame.draw.circle(art, muted, center, 39)
            pygame.draw.circle(art, cyan, center, 39, 3)
            pygame.draw.line(art, green, center, (center[0]+19, center[1]-19), 4)
            pygame.draw.line(art, cyan, (center[0], center[1]-49), (center[0], center[1]-38), 5)
        elif name == "WASSER-ALARM":
            for offset in (0, 13, 26):
                pygame.draw.arc(art, cyan if offset != 13 else green, pygame.Rect(center[0]-51, center[1]-12+offset, 102, 28), 0, math.pi, 3)
            pygame.draw.arc(art, green, pygame.Rect(center[0]-30, center[1]-42, 60, 54), 0.35, 3.0, 5)
        elif name == "REAKTION":
            for row in range(3):
                for column in range(3):
                    point = (center[0] + (column-1)*25, center[1] + (row-1)*25)
                    pygame.draw.circle(art, green if (row, column) == (1, 1) else muted, point, 8)
                    pygame.draw.circle(art, cyan, point, 8, 2)
        elif name == "ZIELSCHEIBE":
            for radius, color in ((42, muted), (31, cyan), (20, green), (8, cyan)):
                pygame.draw.circle(art, color, center, radius, 3)
            pygame.draw.line(art, green, (center[0]-51, center[1]), (center[0]+51, center[1]), 2)
            pygame.draw.line(art, green, (center[0], center[1]-51), (center[0], center[1]+51), 2)
        elif name == "BALLONJAGD":
            for offset_x, offset_y, radius, color in (
                (-27, 4, 17, cyan),
                (8, -20, 20, green),
                (29, 15, 15, muted),
            ):
                balloon = (center[0] + offset_x, center[1] + offset_y)
                pygame.draw.ellipse(
                    art,
                    color,
                    pygame.Rect(balloon[0] - radius, balloon[1] - radius - 5, radius * 2, radius * 2 + 10),
                    3,
                )
                pygame.draw.line(art, color, (balloon[0], balloon[1] + radius + 5), (center[0], center[1] + 52), 2)
        elif name == "ALIEN-ALARM":
            head = pygame.Rect(center[0] - 40, center[1] - 32, 80, 66)
            pygame.draw.ellipse(art, muted, head)
            pygame.draw.ellipse(art, cyan, head, 3)
            pygame.draw.ellipse(art, green, pygame.Rect(center[0] - 26, center[1] - 7, 19, 27), 3)
            pygame.draw.ellipse(art, green, pygame.Rect(center[0] + 7, center[1] - 7, 19, 27), 3)
            pygame.draw.arc(art, cyan, pygame.Rect(center[0] - 17, center[1] + 11, 34, 18), 0.2, math.pi - 0.2, 2)
        elif name == "STERNEJAGD":
            points = []
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                radius = 43 if index % 2 == 0 else 19
                points.append(
                    (
                        center[0] + round(math.cos(angle) * radius),
                        center[1] + round(math.sin(angle) * radius),
                    )
                )
            pygame.draw.polygon(art, muted, points)
            pygame.draw.lines(art, green, True, points, 3)
        elif name == "RECHENDUELL":
            equation = pygame.font.SysFont("Arial", 30, bold=True).render("7 × 8", True, cyan)
            art.blit(equation, equation.get_rect(center=(center[0], center[1] - 11)))
            pygame.draw.line(art, green, (center[0] - 44, center[1] + 18), (center[0] + 44, center[1] + 18), 3)
            answer = pygame.font.SysFont("Arial", 21, bold=True).render("?", True, green)
            art.blit(answer, answer.get_rect(center=(center[0], center[1] + 36)))
        elif name == "FARBENSPIEL":
            for offset, color in zip(((-24, -24), (24, -24), (-24, 24), (24, 24)), (cyan, green, muted, (0, 145, 205, 92))):
                pygame.draw.circle(art, color, (center[0] + offset[0], center[1] + offset[1]), 17)
                pygame.draw.circle(art, cyan, (center[0] + offset[0], center[1] + offset[1]), 17, 2)
        elif name == "SCHATZSUCHE":
            chest = pygame.Rect(center[0] - 45, center[1] - 10, 90, 50)
            pygame.draw.rect(art, muted, chest, border_radius=7)
            pygame.draw.rect(art, cyan, chest, 3, border_radius=7)
            pygame.draw.arc(art, green, pygame.Rect(center[0] - 45, center[1] - 47, 90, 68), 0, math.pi, 4)
            pygame.draw.rect(art, green, pygame.Rect(center[0] - 8, center[1] + 4, 16, 23), 2, border_radius=3)
        elif name == "TIC-TAC-TOE":
            for offset in (-20, 20):
                pygame.draw.line(art, muted, (center[0]+offset, center[1]-43), (center[0]+offset, center[1]+43), 3)
                pygame.draw.line(art, muted, (center[0]-43, center[1]+offset), (center[0]+43, center[1]+offset), 3)
            pygame.draw.line(art, cyan, (center[0]-36,center[1]-36),(center[0]-24,center[1]-24),5)
            pygame.draw.line(art, cyan, (center[0]-24,center[1]-36),(center[0]-36,center[1]-24),5)
            pygame.draw.circle(art, green, (center[0]+27,center[1]+27), 10, 4)
        elif name == "4 GEWINNT":
            for row in range(4):
                for col in range(5):
                    point = (center[0]-32+col*16, center[1]-25+row*17)
                    color = cyan if (col == 1 and row >= 1) else green if (col == 3 and row >= 2) else muted
                    pygame.draw.circle(art, color, point, 6, 2)
        elif name == "KÄSEKÄSTCHEN":
            for row in range(3):
                for col in range(3):
                    point = (center[0]-32+col*32, center[1]-32+row*32)
                    pygame.draw.circle(art, cyan, point, 5)
            pygame.draw.line(art, green, (center[0]-32,center[1]-32),(center[0],center[1]-32),5)
            pygame.draw.line(art, green, (center[0],center[1]-32),(center[0],center[1]),5)
            pygame.draw.line(art, green, (center[0],center[1]),(center[0]-32,center[1]),5)
        elif name == "MEMORY-DUELL":
            for row in range(2):
                for col in range(3):
                    card = pygame.Rect(center[0]-39+col*29, center[1]-34+row*39, 22, 31)
                    pygame.draw.rect(art, muted, card, border_radius=4)
                    pygame.draw.rect(art, cyan if (row+col)%2==0 else green, card, 2, border_radius=4)
        elif name == "NIM-DUELL":
            for index in range(9):
                row, col = divmod(index, 3)
                stick = pygame.Rect(center[0]-31+col*29, center[1]-36+row*29, 10, 22)
                pygame.draw.rect(art, cyan if index > 2 else green, stick, border_radius=4)
            minus = pygame.font.SysFont("Arial", 19, bold=True).render("−1  −2  −3", True, green)
            art.blit(minus, minus.get_rect(midtop=(center[0], center[1]+16)))
        elif name == "REVERSI LIGHT":
            for row in range(4):
                for col in range(4):
                    cell = pygame.Rect(center[0]-42+col*21, center[1]-42+row*21, 20, 20)
                    pygame.draw.rect(art, muted, cell, 1)
            for point, color in (((center[0]-11,center[1]-11),cyan),((center[0]+10,center[1]-11),green),((center[0]-11,center[1]+10),green),((center[0]+10,center[1]+10),cyan)):
                pygame.draw.circle(art, color, point, 8)
        self.screen.blit(art, rect.topleft)

    def _fitted_card_font(self, text: str, max_width: int) -> pygame.font.Font:
        key = (text, max_width)
        cached = self.card_font_cache.get(key)
        if cached is not None:
            return cached
        for size in range(25, 13, -1):
            candidate = pygame.font.SysFont("Arial", size, bold=True)
            if candidate.size(text)[0] <= max_width:
                self.card_font_cache[key] = candidate
                return candidate
        fallback = pygame.font.SysFont("Arial", 13, bold=True)
        self.card_font_cache[key] = fallback
        return fallback

    def _draw_menu_aim_point(
        self, rect: pygame.Rect, color: Tuple[int, int, int]
    ) -> None:
        point = (rect.right - 24, rect.top + 24)
        pygame.draw.circle(self.screen, color, point, 8, 2)
        pygame.draw.line(
            self.screen, color, (point[0] - 12, point[1]), (point[0] + 12, point[1]), 1
        )
        pygame.draw.line(
            self.screen, color, (point[0], point[1] - 12), (point[0], point[1] + 12), 1
        )

    def _draw_game_placeholder(self) -> None:
        width, height = self.screen.get_size()
        self.screen.fill(self.TARGET_BG)
        card = self._coming_soon_rect()
        draw_translucent_panel(
            self.screen, card, (0, 20, 32), alpha=202, border_radius=16
        )
        pygame.draw.rect(self.screen, self.TARGET_CYAN, card, 3, border_radius=16)
        self._draw_menu_aim_point(card, self.TARGET_GREEN)

        heading = self.font_target.render(self.selected_game, True, self.TARGET_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(width // 2, card.top + 48)))
        status = self.font_large.render("IN VORBEREITUNG", True, self.TARGET_GREEN)
        self.screen.blit(status, status.get_rect(midtop=(width // 2, card.top + 116)))
        explanation = self.font.render(
            "Dieser Spielmodus wird als Nächstes eingebunden.", True, self.TARGET_MUTED
        )
        self.screen.blit(explanation, explanation.get_rect(midtop=(width // 2, card.top + 171)))
        action = self.font.render(
            "AUF DIESES FENSTER SCHIE\u00dfEN", True, self.TARGET_CYAN
        )
        self.screen.blit(action, action.get_rect(midtop=(width // 2, card.top + 232)))
        back = self.font_small.render("ZURÜCK ZUM MENÜ", True, self.TARGET_GREEN)
        self.screen.blit(back, back.get_rect(midtop=(width // 2, card.top + 271)))

    def _draw_alignment_pattern(self) -> None:
        self.screen.fill(self.aligner.display_color)
        if self.aligner.phase == "projector_wait":
            width, height = self.screen.get_size()
            label = self.font_large.render(
                "BEAMER WIRD AUTOMATISCH ERKANNT", True, (18, 46, 58)
            )
            detail = self.font.render(
                "Das Bild darf vollständig erscheinen und in Ruhe warm werden.",
                True,
                (35, 70, 82),
            )
            self.screen.blit(label, label.get_rect(center=(width // 2, height // 2 - 30)))
            self.screen.blit(detail, detail.get_rect(center=(width // 2, height // 2 + 18)))
            progress_width = 440
            progress = 0.0
            if self.aligner.projector_response_history:
                progress = min(
                    1.0,
                    (self.aligner.projector_response_history[-1][0]
                     - self.aligner.projector_response_history[0][0])
                    / self.aligner.PROJECTOR_STABLE_SECONDS,
                )
            track = pygame.Rect(width // 2 - progress_width // 2, height // 2 + 64, progress_width, 14)
            pygame.draw.rect(self.screen, (205, 220, 224), track, border_radius=7)
            if progress > 0:
                fill = track.copy()
                fill.width = max(2, int(track.width * progress))
                pygame.draw.rect(self.screen, (0, 145, 185), fill, border_radius=7)
            return
        if self.aligner.shows_color_test:
            palette = {
                "white": (245, 245, 245),
                "red": (185, 18, 12),
                "green": (8, 190, 45),
                "blue": (8, 75, 225),
                "cyan": (0, 205, 220),
                "gray": (112, 112, 112),
            }
            for name, values in startup_color_rects(self.screen.get_size()):
                pygame.draw.rect(self.screen, palette[name], pygame.Rect(*values))
            return
        if self.aligner.shows_precision_frame:
            width, height = self.screen.get_size()
            # Der sichtbare Außenrahmen bleibt von den runden Messmarken
            # getrennt, damit die Kamera alle zwölf Marker einzeln vermisst.
            inset = max(10, int(min(width, height) * 0.015))
            frame = pygame.Rect(inset, inset, width - inset * 2, height - inset * 2)
            pygame.draw.rect(self.screen, (255, 255, 255), frame, 5)
            marker_radius = max(10, int(min(width, height) * 0.017))
            for point in self.aligner.precision_screen_points:
                pygame.draw.circle(self.screen, (255, 255, 255), point, marker_radius)
            return
        if not self.aligner.shows_markers:
            return

        # Vier kompakte, gut getrennte Flächen liefern eine unabhängige
        # Rückmessung der zuvor berechneten Projektionsabbildung.
        marker_size = max(34, int(min(self.screen.get_size()) * 0.052))
        for point in self.aligner.marker_screen_points:
            marker = pygame.Rect(0, 0, marker_size, marker_size)
            marker.center = point
            pygame.draw.rect(self.screen, (255, 255, 255), marker)

    def _draw_sighting_screen(self) -> None:
        width, height = self.screen.get_size()
        self.screen.fill(self.TARGET_BG)

        title = self.font_target.render("EINSCHIE\u00dfEN", True, self.TARGET_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(width // 2, 22)))

        verification = self.aligner.verification
        max_error = verification.max_error if verification is not None else 0.0
        badge_text = (
            f"✓ 4/4 ECKEN GEPRÜFT   ·   AUSRICHTUNG {self.aligner.confidence:.0%}"
            f"   ·   GRÖßTE ABWEICHUNG {max_error:.0f} px"
        )
        badge = self.font_small.render(badge_text, True, self.TARGET_GREEN)
        badge_bg = badge.get_rect(center=(width // 2, 91)).inflate(28, 12)
        draw_translucent_panel(
            self.screen, badge_bg, (0, 30, 32), alpha=176, border_radius=8
        )
        pygame.draw.rect(self.screen, self.TARGET_GREEN, badge_bg, 1, border_radius=8)
        self.screen.blit(badge, badge.get_rect(center=badge_bg.center))

        for index, point in enumerate(self.aligner.marker_screen_points, start=1):
            self._draw_verified_corner(point, index)

        stages = self._sighting_stages()
        if self.sighting_phase == "complete":
            for _, _, _, point in stages:
                pygame.draw.circle(self.screen, self.TARGET_MUTED, point, 18, 2)
            self._draw_complete_evaluation(stages)
        else:
            name, instruction, required, target_point = stages[self.sighting_step]
            step_text = self.font.render(
                f"SCHRITT {self.sighting_step + 1} VON 5  ·  {name}",
                True,
                self.TARGET_CYAN,
            )
            self.screen.blit(step_text, step_text.get_rect(midtop=(width // 2, 111)))
            prompt = self.font_small.render(instruction, True, self.TARGET_GREEN)
            self.screen.blit(prompt, prompt.get_rect(midtop=(width // 2, 142)))

            if self.sighting_step > 0:
                for index, (_, _, _, point) in enumerate(stages[1:], start=1):
                    if index != self.sighting_step:
                        pygame.draw.circle(self.screen, self.TARGET_MUTED, point, 20, 2)
                        pygame.draw.circle(self.screen, self.TARGET_MUTED, point, 3)

            radius = 178 if self.sighting_step == 0 else 67
            self._draw_target_rings(target_point, radius, active=True)
            self._draw_stage_shots(target_point)

            if self.sighting_phase == "evaluation":
                self._draw_stage_evaluation(stages, target_point)
            else:
                progress = self.font.render(
                    f"{len(self.stage_shots)} VON {required} SCHÜSSEN ERKANNT",
                    True,
                    self.TARGET_CYAN,
                )
                progress_bg = progress.get_rect(
                    center=(width // 2, height - 91)
                ).inflate(24, 10)
                draw_translucent_panel(
                    self.screen,
                    progress_bg,
                    (0, 25, 40),
                    alpha=174,
                    border_radius=7,
                )
                self.screen.blit(progress, progress.get_rect(center=progress_bg.center))

        self._draw_target_button(self.target_menu_button, "Menü", self.TARGET_GREEN)
        self._draw_target_button(self.target_live_button, "Kamerabild", self.TARGET_CYAN)
        self._draw_target_button(
            self.target_align_button, "Neu ausrichten", self.TARGET_GREEN
        )
        self._draw_target_button(
            self.target_clear_button, "Ablauf neu", self.TARGET_CYAN
        )

    def _sighting_stages(
        self,
    ) -> list[tuple[str, str, int, Tuple[int, int]]]:
        width, height = self.screen.get_size()
        corner_x = max(120, int(width * 0.12))
        top_y = max(185, int(height * 0.24))
        bottom_y = min(height - 155, int(height * 0.78))
        return [
            ("MITTE", "FÜNF SCHÜSSE IN DIE MITTE ABGEBEN", 5, (width // 2, height // 2 + 20)),
            ("OBEN LINKS", "DREI SCHÜSSE AUF DIE HERVORGEHOBENE ECKE", 3, (corner_x, top_y)),
            (
                "OBEN RECHTS",
                "DREI SCHÜSSE AUF DIE HERVORGEHOBENE ECKE",
                3,
                (width - corner_x, top_y),
            ),
            (
                "UNTEN RECHTS",
                "DREI SCHÜSSE AUF DIE HERVORGEHOBENE ECKE",
                3,
                (width - corner_x, bottom_y),
            ),
            (
                "UNTEN LINKS",
                "DREI SCHÜSSE AUF DIE HERVORGEHOBENE ECKE",
                3,
                (corner_x, bottom_y),
            ),
        ]

    def _draw_target_rings(
        self, center: Tuple[int, int], radius: int, active: bool = False
    ) -> None:
        pulse = int(3 * np.sin(pygame.time.get_ticks() / 180.0)) if active else 0
        for ring_index, factor in enumerate((1.0, 0.68, 0.37, 0.10)):
            ring_radius = max(7, int(radius * factor) + (pulse if ring_index == 0 else 0))
            color = self.TARGET_CYAN if ring_index in {0, 3} else self.TARGET_GREEN
            pygame.draw.circle(self.screen, color, center, ring_radius, 3 if ring_index == 0 else 2)
        pygame.draw.line(
            self.screen,
            self.TARGET_MUTED,
            (center[0] - radius - 18, center[1]),
            (center[0] + radius + 18, center[1]),
            1,
        )
        pygame.draw.line(
            self.screen,
            self.TARGET_MUTED,
            (center[0], center[1] - radius - 18),
            (center[0], center[1] + radius + 18),
            1,
        )
        pygame.draw.circle(self.screen, self.TARGET_CYAN, center, 3)

    def _draw_stage_shots(self, target_point: Tuple[int, int]) -> None:
        for shot in self.stage_shots:
            if shot.screen_point is None:
                continue
            self._draw_cross(shot.screen_point, self.TARGET_CYAN, 13, 3)
            number = self.font.render(str(shot.number), True, self.TARGET_GREEN)
            self.screen.blit(number, (shot.screen_point[0] + 13, shot.screen_point[1] - 28))

    @staticmethod
    def _group_metrics(
        shots: list[ShotRecord], target_point: Tuple[int, int]
    ) -> tuple[float, float, float]:
        points = np.asarray(
            [shot.screen_point for shot in shots if shot.screen_point is not None],
            dtype=np.float32,
        )
        if not len(points):
            return 0.0, 0.0, 0.0
        mean = points.mean(axis=0)
        delta_x = float(mean[0] - target_point[0])
        delta_y = float(target_point[1] - mean[1])
        spread = 0.0
        for first in points:
            for second in points:
                spread = max(spread, float(np.linalg.norm(first - second)))
        return delta_x, delta_y, spread

    @staticmethod
    def _offset_text(delta_x: float, delta_y: float) -> str:
        horizontal = "mittig" if abs(delta_x) < 1 else (
            f"{abs(delta_x):.0f} px rechts" if delta_x > 0 else f"{abs(delta_x):.0f} px links"
        )
        vertical = "mittig" if abs(delta_y) < 1 else (
            f"{abs(delta_y):.0f} px hoch" if delta_y > 0 else f"{abs(delta_y):.0f} px tief"
        )
        return f"{horizontal}  ·  {vertical}"

    def _draw_stage_evaluation(
        self,
        stages: list[tuple[str, str, int, Tuple[int, int]]],
        target_point: Tuple[int, int],
    ) -> None:
        width, height = self.screen.get_size()
        delta_x, delta_y, spread = self._group_metrics(self.stage_shots, target_point)
        card = self._stage_evaluation_rect()
        draw_translucent_panel(
            self.screen, card, (0, 18, 30), alpha=204, border_radius=14
        )
        pygame.draw.rect(self.screen, self.TARGET_GREEN, card, 3, border_radius=14)
        self._draw_evaluation_aim_point(card)

        heading = self.font_large.render("AUSWERTUNG", True, self.TARGET_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(card.centerx, card.top + 18)))
        count = self.font.render(
            f"✓ {len(self.stage_shots)} SCHÜSSE VOLLSTÄNDIG",
            True,
            self.TARGET_GREEN,
        )
        self.screen.blit(count, count.get_rect(midtop=(card.centerx, card.top + 63)))
        offset = self.font.render(
            f"Treffpunktlage: {self._offset_text(delta_x, delta_y)}",
            True,
            self.TARGET_CYAN,
        )
        self.screen.blit(offset, offset.get_rect(midtop=(card.centerx, card.top + 100)))
        grouping = self.font.render(
            f"Streukreis: {spread:.0f} px", True, self.TARGET_GREEN
        )
        self.screen.blit(grouping, grouping.get_rect(midtop=(card.centerx, card.top + 134)))

        if self.sighting_step < 4:
            next_name = stages[self.sighting_step + 1][0]
            next_text = f"Hierhin schießen: Weiter zu {next_name}"
        else:
            next_text = "Hierhin schießen: Weiter zur Gesamtauswertung"
        next_surface = self.font_small.render(next_text, True, self.TARGET_MUTED)
        self.screen.blit(next_surface, next_surface.get_rect(midtop=(card.centerx, card.top + 174)))
        self._draw_target_button(
            self.advance_button, "Weiter", self.TARGET_GREEN
        )

    def _draw_complete_evaluation(
        self, stages: list[tuple[str, str, int, Tuple[int, int]]]
    ) -> None:
        width, height = self.screen.get_size()
        card = self._complete_evaluation_rect()
        draw_translucent_panel(
            self.screen, card, (0, 18, 30), alpha=204, border_radius=14
        )
        pygame.draw.rect(self.screen, self.TARGET_GREEN, card, 3, border_radius=14)
        self._draw_evaluation_aim_point(card)
        heading = self.font_large.render("GESAMTAUSWERTUNG", True, self.TARGET_CYAN)
        self.screen.blit(heading, heading.get_rect(midtop=(card.centerx, card.top + 18)))
        done = self.font.render("17 SCHÜSSE VOLLSTÄNDIG", True, self.TARGET_GREEN)
        self.screen.blit(done, done.get_rect(midtop=(card.centerx, card.top + 61)))

        for index, (name, _, _, target_point) in enumerate(stages):
            delta_x, delta_y, spread = self._group_metrics(
                self.completed_groups[index], target_point
            )
            line = (
                f"{name:<14}  {self._offset_text(delta_x, delta_y)}"
                f"  ·  Streukreis {spread:.0f} px"
            )
            surface = self.font_small.render(line, True, self.TARGET_CYAN)
            self.screen.blit(surface, (card.left + 34, card.top + 110 + index * 43))

        calibration = self.font_small.render(
            self.weapon_calibration_message,
            True,
            self.TARGET_GREEN if self.weapon_calibration.active else self.TARGET_CYAN,
        )
        self.screen.blit(
            calibration,
            calibration.get_rect(midtop=(card.centerx, card.top + 326)),
        )

        note = self.font_small.render(
            "Auf dieses Fenster schießen: Einschießen wiederholen.",
            True,
            self.TARGET_GREEN,
        )
        self.screen.blit(note, note.get_rect(midtop=(card.centerx, card.bottom - 55)))
        self._draw_target_button(
            self.advance_button, "Einschießen wiederholen", self.TARGET_GREEN
        )

    def _stage_evaluation_rect(self) -> pygame.Rect:
        width, height = self.screen.get_size()
        card = pygame.Rect(0, 0, 610, 244)
        card.center = (width // 2, height // 2 + 25)
        return card

    def _complete_evaluation_rect(self) -> pygame.Rect:
        width, height = self.screen.get_size()
        card = pygame.Rect(0, 0, 660, 410)
        card.center = (width // 2, height // 2 + 32)
        return card

    def _draw_evaluation_aim_point(self, card: pygame.Rect) -> None:
        point = (card.right - 24, card.top + 24)
        pygame.draw.circle(self.screen, self.TARGET_GREEN, point, 8, 2)
        pygame.draw.line(
            self.screen,
            self.TARGET_GREEN,
            (point[0] - 12, point[1]),
            (point[0] + 12, point[1]),
            1,
        )
        pygame.draw.line(
            self.screen,
            self.TARGET_GREEN,
            (point[0], point[1] - 12),
            (point[0], point[1] + 12),
            1,
        )

    def _draw_verified_corner(self, point: Tuple[int, int], number: int) -> None:
        arm = 28
        x, y = point
        left = x < self.screen.get_width() // 2
        top = y < self.screen.get_height() // 2
        horizontal_end = x + arm if left else x - arm
        vertical_end = y + arm if top else y - arm
        pygame.draw.line(self.screen, self.TARGET_GREEN, (x, y), (horizontal_end, y), 4)
        pygame.draw.line(self.screen, self.TARGET_GREEN, (x, y), (x, vertical_end), 4)
        label = self.font_small.render(f"✓ ECKE {number}", True, self.TARGET_GREEN)
        label_rect = label.get_rect()
        label_rect.left = x + 8 if left else x - label_rect.width - 8
        label_rect.top = y + 8 if top else y - label_rect.height - 8
        self.screen.blit(label, label_rect)

    def _draw_target_button(
        self, rect: pygame.Rect, label: str, color: Tuple[int, int, int]
    ) -> None:
        draw_button(self.screen, rect, label, self.font_small, color)

    def _draw_button_aim_point(
        self, rect: pygame.Rect, color: Tuple[int, int, int]
    ) -> None:
        point = (rect.left + 13, rect.centery)
        pygame.draw.circle(self.screen, color, point, 5, 1)
        pygame.draw.line(self.screen, color, (point[0] - 7, point[1]), (point[0] + 7, point[1]), 1)
        pygame.draw.line(self.screen, color, (point[0], point[1] - 7), (point[0], point[1] + 7), 1)

    def _draw_camera_setting_button(
        self,
        rect: pygame.Rect,
        label: str,
        *,
        active: bool = False,
        color: Optional[Tuple[int, int, int]] = None,
        font: Optional[pygame.font.Font] = None,
    ) -> None:
        color = color or (self.TARGET_GREEN if active else self.TARGET_CYAN)
        self.screen.blit(
            build_vintage_enamel_panel(
                rect.size, 2 if active else 1, active=True, alpha=242
            ),
            rect.topleft,
        )
        pygame.draw.rect(self.screen, color, rect, 3 if active else 2, border_radius=9)
        self._draw_button_aim_point(rect, color)
        rendered = (font or self.font_small).render(label, True, color)
        self.screen.blit(rendered, rendered.get_rect(center=rect.center))

    def _draw_camera_settings_preview(self) -> pygame.Rect:
        box = self._camera_settings_preview_box()
        pygame.draw.rect(self.screen, (0, 4, 12), box)
        pygame.draw.rect(self.screen, self.TARGET_MUTED, box, 2)
        if self.last_frame_rgb is None:
            waiting = self.font.render("Warte auf Kamerabild …", True, self.TARGET_CYAN)
            self.screen.blit(waiting, waiting.get_rect(center=box.center))
            return box
        frame = self.last_frame_rgb
        if time.monotonic() >= self.camera_original_preview_until:
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            neutral = np.empty_like(frame)
            neutral[:, :, 0] = 0
            neutral[:, :, 1] = np.clip(gray.astype(np.float32) * 0.82, 0, 255).astype(np.uint8)
            neutral[:, :, 2] = gray
            frame = neutral
        frame_h, frame_w = frame.shape[:2]
        view = self._camera_settings_frame_view()
        surface = pygame.image.frombuffer(frame.tobytes(), (frame_w, frame_h), "RGB").copy()
        surface = pygame.transform.smoothscale(surface, view.size)
        self.screen.blit(surface, view)
        return view

    @staticmethod
    def _quad_point(
        corners: list[Tuple[int, int]], u: float, v: float
    ) -> Tuple[int, int]:
        tl, tr, br, bl = corners
        x = (
            tl[0] * (1.0 - u) * (1.0 - v)
            + tr[0] * u * (1.0 - v)
            + br[0] * u * v
            + bl[0] * (1.0 - u) * v
        )
        y = (
            tl[1] * (1.0 - u) * (1.0 - v)
            + tr[1] * u * (1.0 - v)
            + br[1] * u * v
            + bl[1] * (1.0 - u) * v
        )
        return round(x), round(y)

    def _draw_camera_alignment_settings(self) -> None:
        self._draw_camera_settings_preview()
        if len(self.camera_corner_draft) != 4:
            return
        view_corners = [
            self._camera_point_to_settings_view(point)
            for point in self.camera_corner_draft
        ]
        pygame.draw.lines(self.screen, self.TARGET_GREEN, True, view_corners, 4)
        for division in (0.25, 0.5, 0.75):
            horizontal = [
                self._camera_point_to_settings_view(
                    self._quad_point(self.camera_corner_draft, u, division)
                )
                for u in (0.0, 1.0)
            ]
            vertical = [
                self._camera_point_to_settings_view(
                    self._quad_point(self.camera_corner_draft, division, v)
                )
                for v in (0.0, 1.0)
            ]
            pygame.draw.line(self.screen, self.TARGET_MUTED, horizontal[0], horizontal[1], 2)
            pygame.draw.line(self.screen, self.TARGET_MUTED, vertical[0], vertical[1], 2)
        for index, point in enumerate(view_corners):
            selected = index == self.camera_selected_corner
            color = self.TARGET_GREEN if selected else self.TARGET_CYAN
            pygame.draw.circle(self.screen, (0, 9, 18), point, 22)
            pygame.draw.circle(self.screen, color, point, 22, 4 if selected else 3)
            label = self.font.render(str(index + 1), True, color)
            self.screen.blit(label, label.get_rect(center=point))

        panel = pygame.Rect(724, 132, 276, 552)
        pygame.draw.rect(self.screen, (0, 13, 25), panel, border_radius=12)
        pygame.draw.rect(self.screen, self.TARGET_MUTED, panel, 2, border_radius=12)
        title = self.font.render("ECKE AUSWÄHLEN", True, self.TARGET_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(panel.centerx, panel.top + 13)))
        controls = self._camera_corner_control_rects()
        corner_labels = {
            "corner_0": "1 OBEN L.", "corner_1": "2 OBEN R.",
            "corner_2": "3 UNTEN R.", "corner_3": "4 UNTEN L.",
        }
        for key, label in corner_labels.items():
            index = int(key[-1])
            self._draw_camera_setting_button(
                controls[key], label, active=index == self.camera_selected_corner
            )
        arrows = {"up": "▲", "left": "◀", "right": "▶", "down": "▼"}
        for key, label in arrows.items():
            self._draw_camera_setting_button(controls[key], label, font=self.font_large)
        self._draw_camera_setting_button(
            controls["step"], f"SCHRITT {self.camera_corner_step} PX",
            active=self.camera_corner_step == 5,
        )
        restore = pygame.Rect(742, 560, 216, 44)
        self._draw_camera_setting_button(restore, "ECKEN ZURÜCK")

        angles: list[float] = []
        for start, end in zip(self.camera_corner_draft, self.camera_corner_draft[1:] + self.camera_corner_draft[:1]):
            angles.append(math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])))
        angle_texts = (
            f"Oben {angles[0]:+.1f}° · Rechts {angles[1]:+.1f}°",
            f"Unten {angles[2]:+.1f}° · Links {angles[3]:+.1f}°",
        )
        for index, line in enumerate(angle_texts):
            rendered = self.font_small.render(line, True, self.TARGET_CYAN)
            self.screen.blit(rendered, rendered.get_rect(midtop=(panel.centerx, 620 + index * 23)))

        if self.last_frame_rgb is not None:
            frame_h, frame_w = self.last_frame_rgb.shape[:2]
        else:
            frame_w, frame_h = self.tracker.processing_width, self.tracker.processing_height
        valid, validity = validate_camera_quad(self.camera_corner_draft, (frame_w, frame_h))
        color = self.TARGET_GREEN if valid else self.TARGET_CYAN
        rendered = self.font_small.render(validity, True, color)
        self.screen.blit(rendered, rendered.get_rect(midtop=(364, 526)))
        hint = self.font_small.render(
            "Maus: Ecke ziehen · Pistole: Ecke und Pfeile treffen",
            True,
            self.TARGET_MUTED,
        )
        self.screen.blit(hint, hint.get_rect(midtop=(364, 552)))

    def _draw_camera_detection_guide(self) -> None:
        draft = self.camera_settings_draft
        profile = self._active_camera_draft_profile()
        if draft is None or profile is None:
            return
        heading = self.font.render("FILTERPROFIL", True, self.TARGET_CYAN)
        self.screen.blit(heading, (550, 132))
        active_name = (
            "Rotfilter erkannt"
            if self.tracker.active_filter_profile == "red_filter"
            else "Ohne Filter erkannt"
        )
        active = self.font_small.render(active_name, True, self.TARGET_GREEN)
        self.screen.blit(active, active.get_rect(topright=(1000, 137)))
        for mode, rect in self._camera_settings_filter_buttons().items():
            labels = {"auto": "AUTOMATIK", "normal": "OHNE FILTER", "red_filter": "ROTFILTER"}
            self._draw_camera_setting_button(
                rect, labels[mode], active=draft.filter_mode == mode
            )

        color_heading = self.font.render(
            "AUF JEDE TESTFLÄCHE SCHIEßEN", True, self.TARGET_CYAN
        )
        self.screen.blit(color_heading, (24, 132))
        color_rects = self._camera_detection_color_rects()
        detection = self.last_detection
        current_red_threshold = max(1, detection.red_threshold) if detection else 1
        current_delta_threshold = max(1, detection.delta_threshold) if detection else 1
        for name, color in self.DETECTION_TEST_COLORS:
            rect = color_rects[name]
            samples = self.camera_detection_samples.get(name, [])
            status = "NOCH TESTEN"
            status_color = self.TARGET_BODY
            if samples:
                sample = samples[-1]
                ratio = min(
                    float(sample["red"]) / current_red_threshold,
                    float(sample["delta"]) / current_delta_threshold,
                )
                if bool(sample["detected"]):
                    status, status_color = "SICHER", self.TARGET_GREEN
                elif ratio >= 1.0:
                    status, status_color = "KNAPP", self.TARGET_CYAN
                else:
                    status, status_color = "ZU SCHWACH", self.RED
            pygame.draw.rect(self.screen, color, rect, border_radius=12)
            pygame.draw.rect(self.screen, status_color, rect, 4, border_radius=12)
            name_surface = self.font_small.render(
                name, True, self._camera_test_label_color(color)
            )
            self.screen.blit(name_surface, (rect.left + 10, rect.top + 8))
            status_box = pygame.Rect(rect.left + 5, rect.bottom - 27, rect.width - 10, 22)
            pygame.draw.rect(self.screen, (0, 10, 20), status_box, border_radius=6)
            status_surface = self.font_small.render(status, True, status_color)
            self.screen.blit(status_surface, status_surface.get_rect(center=status_box.center))

        help_panel = pygame.Rect(24, 410, 500, 270)
        draw_translucent_panel(
            self.screen, help_panel, (0, 20, 32), alpha=220, border_radius=12
        )
        pygame.draw.rect(self.screen, self.TARGET_MUTED, help_panel, 2, border_radius=12)
        tested = sum(bool(values) for values in self.camera_detection_samples.values())
        safe = 0
        for values in self.camera_detection_samples.values():
            if not values:
                continue
            sample = values[-1]
            ratio = min(
                float(sample["red"]) / current_red_threshold,
                float(sample["delta"]) / current_delta_threshold,
            )
            safe += int(bool(sample["detected"]))
        instruction_lines = (
            "So geht's:",
            "• Automatik wählen (empfohlen).",
            "• Jede Farbfläche einmal treffen.",
            "• Der Balken soll bis in den grünen Bereich steigen.",
            "• Danach ohne Schuss prüfen.",
        )
        for index, line in enumerate(instruction_lines):
            font = self.font if index == 0 else self.font_small
            color = self.TARGET_CYAN if index == 0 else self.TARGET_BODY
            self.screen.blit(font.render(line, True, color), (40, 424 + index * 26))
        verdict, verdict_color = self._camera_detection_verdict(
            tested,
            safe,
            len(self.DETECTION_TEST_COLORS),
            self.camera_quiet_test_completed,
            self.camera_quiet_false_triggers,
        )
        self.screen.blit(
            self.font.render(verdict, True, verdict_color), (40, 558)
        )
        quiet_text = (
            f"Prüfung ohne Schuss: {max(0, math.ceil(self.camera_quiet_test_until - time.monotonic()))} s · nicht schießen"
            if self.camera_quiet_test_until
            else (
                f"Ohne Schuss: {self.camera_quiet_false_triggers} Fehlauslösung(en)"
                if self.camera_quiet_test_completed
                else "Noch nicht ohne Schuss geprüft"
            )
        )
        self.screen.blit(
            self.font_small.render(quiet_text, True, self.TARGET_BODY), (40, 595)
        )
        self.screen.blit(
            self.font_small.render(f"Getestete Farben: {tested}/6 · sicher: {safe}", True, self.TARGET_GREEN),
            (40, 622),
        )
        if self.camera_settings_message:
            status = self.font_small.render(
                self.camera_settings_message[:55], True, self.TARGET_CYAN
            )
            self.screen.blit(status, (40, 644))

        peak_panel = pygame.Rect(550, 236, 450, 260)
        draw_translucent_panel(
            self.screen, peak_panel, (0, 20, 32), alpha=224, border_radius=12
        )
        pygame.draw.rect(self.screen, self.TARGET_MUTED, peak_panel, 2, border_radius=12)
        peak = self.camera_detection_last_peak
        if peak is None:
            title = self.font_large.render("NOCH KEIN PEAK", True, self.TARGET_CYAN)
            self.screen.blit(title, title.get_rect(midtop=(peak_panel.centerx, 258)))
            hint = self.font_small.render(
                "Auf die Mitte einer Testfarbe schießen", True, self.TARGET_BODY
            )
            self.screen.blit(hint, hint.get_rect(midtop=(peak_panel.centerx, 306)))
        else:
            red_peak = int(peak["red"])
            delta_peak = int(peak["delta"])
            red_needed = current_red_threshold
            delta_needed = current_delta_threshold
            reliable = red_peak >= red_needed and delta_peak >= delta_needed
            title = self.font_large.render(
                "ERKANNT" if bool(peak["detected"]) else "NOCH ZU SCHWACH",
                True,
                self.TARGET_GREEN if bool(peak["detected"]) else self.RED,
            )
            self.screen.blit(title, title.get_rect(midtop=(peak_panel.centerx, 250)))
            color_line = self.font_small.render(
                f"Letzte Fläche: {peak['color']} · Punktgröße {float(peak['area']):.0f}",
                True,
                self.TARGET_BODY,
            )
            self.screen.blit(color_line, color_line.get_rect(midtop=(peak_panel.centerx, 294)))
            for index, (label, measured, needed) in enumerate(
                (("ROT-PEAK", red_peak, red_needed), ("ÄNDERUNGS-PEAK", delta_peak, delta_needed))
            ):
                y = 334 + index * 68
                text_surface = self.font.render(
                    f"{label}: {measured} · benötigt {needed}",
                    True,
                    self.TARGET_GREEN if measured >= needed else self.TARGET_CYAN,
                )
                self.screen.blit(text_surface, (568, y))
                bar = pygame.Rect(568, y + 31, 410, 14)
                pygame.draw.rect(self.screen, (0, 9, 18), bar, border_radius=7)
                maximum = max(needed * 2, measured, 1)
                fill = pygame.Rect(bar.left, bar.top, round(bar.width * measured / maximum), bar.height)
                pygame.draw.rect(
                    self.screen,
                    self.TARGET_GREEN if measured >= needed else self.TARGET_CYAN,
                    fill,
                    border_radius=7,
                )
                needed_x = bar.left + round(bar.width * needed / maximum)
                pygame.draw.line(self.screen, self.TARGET_CYAN, (needed_x, bar.top - 3), (needed_x, bar.bottom + 3), 3)
            if reliable and not bool(peak["detected"]):
                note = "Werte reichen rechnerisch · Schuss zur Kontrolle wiederholen"
                self.screen.blit(
                    self.font_small.render(note, True, self.TARGET_CYAN), (568, 466)
                )

        actions = self._camera_detection_action_buttons()
        self._draw_camera_setting_button(actions["clear"], "TEST NEU", font=self.font_small)
        self._draw_camera_setting_button(actions["quiet"], "OHNE SCHUSS", font=self.font_small, active=bool(self.camera_quiet_test_until))
        self._draw_camera_setting_button(actions["recommend"], "VORSCHLAG", font=self.font_small)
        self._draw_camera_setting_button(actions["advanced"], "ERWEITERTE WERTE")
        self._draw_camera_setting_button(
            actions["original"],
            "ORIGINALFARBE 5 S",
            active=time.monotonic() < self.camera_original_preview_until,
        )

        slider = self._camera_settings_slider_rects()["sensitivity"]
        value = int(profile.sensitivity)
        pygame.draw.rect(self.screen, (0, 22, 36), slider, border_radius=8)
        pygame.draw.rect(self.screen, self.TARGET_MUTED, slider, 2, border_radius=8)
        filled = pygame.Rect(slider.left, slider.top, round(slider.width * value / 100), slider.height)
        pygame.draw.rect(self.screen, self.TARGET_GREEN, filled, border_radius=8)
        knob_x = slider.left + round(slider.width * value / 100)
        pygame.draw.circle(self.screen, self.TARGET_CYAN, (knob_x, slider.centery), 11)
        sensitivity = self.font.render(
            f"EMPFINDLICHKEIT: {value} %", True, self.TARGET_CYAN
        )
        self.screen.blit(sensitivity, (550, 618))
        self.screen.blit(self.font_small.render("STRIKT", True, self.TARGET_BODY), (608, 674))
        sensitive_label = self.font_small.render("EMPFINDLICH", True, self.TARGET_BODY)
        self.screen.blit(sensitive_label, sensitive_label.get_rect(topright=(908, 674)))
        button_lookup = {
            (key, direction): rect
            for key, direction, rect in self._camera_settings_slider_buttons()
        }
        self._draw_camera_setting_button(button_lookup[("sensitivity", -1)], "−", font=self.font_large)
        self._draw_camera_setting_button(button_lookup[("sensitivity", 1)], "+", font=self.font_large)

    def _draw_camera_detection_settings(self) -> None:
        if not self.camera_settings_advanced:
            self._draw_camera_detection_guide()
            return
        preview = self._draw_camera_settings_preview()
        if self.last_detection and self.last_detection.point is not None:
            point = self._camera_point_to_settings_view(self.last_detection.point)
            pygame.draw.circle(self.screen, self.TARGET_GREEN, point, 11, 3)
        mask_rect = pygame.Rect(24, 446, 200, 112)
        pygame.draw.rect(self.screen, (0, 0, 0), mask_rect)
        pygame.draw.rect(self.screen, self.TARGET_MUTED, mask_rect, 2)
        if self.last_mask_rgb is not None:
            mask = self.last_mask_rgb.copy()
            mask[:, :, 0] = 0
            mh, mw = mask.shape[:2]
            surface = pygame.image.frombuffer(mask.tobytes(), (mw, mh), "RGB").copy()
            self.screen.blit(pygame.transform.scale(surface, mask_rect.size), mask_rect)
        label = self.font_small.render("Live-Impulsmaske", True, self.TARGET_CYAN)
        self.screen.blit(label, (24, 565))

        profile = self._active_camera_draft_profile()
        draft = self.camera_settings_draft
        if profile is None or draft is None:
            return
        active_name = (
            "ROTFILTER" if self.tracker.active_filter_profile == "red_filter" else "OHNE FILTER"
        )
        mode_text = self.font.render(
            f"ERKANNT: {active_name}", True, self.TARGET_GREEN
        )
        self.screen.blit(mode_text, mode_text.get_rect(midtop=(774, 132)))
        for mode, rect in self._camera_settings_filter_buttons().items():
            labels = {"auto": "AUTOMATIK", "normal": "OHNE FILTER", "red_filter": "ROTFILTER"}
            self._draw_camera_setting_button(
                rect, labels[mode], active=draft.filter_mode == mode
            )
        advanced_rect = pygame.Rect(550, 230, 214, 42)
        original_rect = pygame.Rect(780, 230, 220, 42)
        self._draw_camera_setting_button(
            advanced_rect,
            "ERWEITERT AUS" if self.camera_settings_advanced else "ERWEITERT AN",
            active=self.camera_settings_advanced,
        )
        original_active = time.monotonic() < self.camera_original_preview_until
        self._draw_camera_setting_button(
            original_rect,
            "ORIGINAL 5 S",
            active=original_active,
        )

        slider_rects = self._camera_settings_slider_rects()
        button_lookup = {
            (key, direction): rect
            for key, direction, rect in self._camera_settings_slider_buttons()
        }
        for key, label_text, minimum, maximum, _ in self._camera_settings_slider_rows():
            rect = slider_rects[key]
            value = int(getattr(profile, key))
            fraction = (value - minimum) / max(1, maximum - minimum)
            pygame.draw.rect(self.screen, (0, 22, 36), rect, border_radius=8)
            pygame.draw.rect(self.screen, self.TARGET_MUTED, rect, 2, border_radius=8)
            filled = pygame.Rect(rect.left, rect.top, round(rect.width * fraction), rect.height)
            pygame.draw.rect(self.screen, self.TARGET_GREEN, filled, border_radius=8)
            knob_x = rect.left + round(rect.width * fraction)
            pygame.draw.circle(self.screen, self.TARGET_CYAN, (knob_x, rect.centery), 11)
            label_surface = self.font_small.render(
                f"{label_text}: {value}", True, self.TARGET_CYAN
            )
            self.screen.blit(label_surface, (rect.left, rect.top - 22))
            self._draw_camera_setting_button(button_lookup[(key, -1)], "−", font=self.font_large)
            self._draw_camera_setting_button(button_lookup[(key, 1)], "+", font=self.font_large)

        detection = self.last_detection
        effective = (
            f"Wirksam: R {detection.red_threshold} · Δ {detection.delta_threshold}"
            if detection else "Wirksame Werte werden gemessen"
        )
        measured = (
            f"Messwert: R {detection.peak_red_excess} · Δ {detection.peak_delta}"
            if detection else "Noch kein Messwert"
        )
        details = (
            f"Fläche {detection.area:.1f} · Profil {detection.filter_confidence:.0%}"
            if detection else "Profil wird bestimmt"
        )
        for index, (line, color) in enumerate(
            ((effective, self.TARGET_GREEN), (measured, self.TARGET_CYAN), (details, self.TARGET_MUTED))
        ):
            rendered = self.font_small.render(line, True, color)
            self.screen.blit(rendered, (246, 464 + index * 29))
        global_hint = self.font_small.render(
            "Diese Werte gelten für alle Spiele", True, self.TARGET_MUTED
        )
        self.screen.blit(global_hint, (246, 551))

    def _draw_camera_settings(self, fps: float) -> None:
        self.screen.fill((0, 6, 15))
        title = self.font_title.render("KAMERA MANUELL EINSTELLEN", True, self.TARGET_CYAN)
        self.screen.blit(title, (24, 18))
        fps_text = self.font_small.render(
            f"Kamera {self.tracker.processing_fps:.0f} fps · Anzeige {fps:.0f} fps",
            True,
            self.TARGET_MUTED,
        )
        self.screen.blit(fps_text, fps_text.get_rect(topright=(1000, 28)))
        for tab, rect in self._camera_settings_tabs().items():
            label = "AUSRICHTUNG" if tab == "alignment" else "SCHUSSERKENNUNG"
            self._draw_camera_setting_button(
                rect, label, active=self.camera_settings_tab == tab
            )
        if self.camera_settings_tab == "alignment":
            self._draw_camera_alignment_settings()
        else:
            self._draw_camera_detection_settings()

        show_footer_message = not (
            self.camera_settings_tab == "detection"
            and not self.camera_settings_advanced
        )
        if self.camera_settings_message and show_footer_message:
            message = self.font_small.render(
                self.camera_settings_message[:76], True, self.TARGET_GREEN
            )
            self.screen.blit(message, message.get_rect(midbottom=(512, 696)))
        footer = self._camera_settings_footer()
        self._draw_camera_setting_button(
            footer["automatic"],
            "STANDARDWERTE" if self.camera_settings_tab == "detection" else "AUTOMATISCH NEU",
        )
        self._draw_camera_setting_button(footer["discard"], "VERWERFEN")
        self._draw_camera_setting_button(
            footer["apply"], "ÜBERNEHMEN", active=self.camera_settings_dirty
        )

    def _draw_header(self, fps: float) -> None:
        title = self.font_title.render("KAMERABILD & EINSTELLUNGEN", True, self.TEXT)
        self.screen.blit(title, (20, 16))
        aligned = self.aligner.homography is not None and self.aligner.phase != "failed"
        status_color = self.GREEN if aligned else self.RED
        if aligned:
            status = (
                "Ausrichtung manuell"
                if self.manual_alignment_active
                else f"Ausrichtung {self.aligner.confidence:.0%}"
            )
        elif self.aligner.phase == "failed":
            status = "Kamera neu ausrichten"
        else:
            status = "Nicht ausgerichtet"
        status_surface = self.font.render(status, True, status_color)
        status_rect = status_surface.get_rect(midright=(994, 36))
        pygame.draw.circle(self.screen, status_color, (status_rect.left - 18, 36), 10)
        self.screen.blit(status_surface, status_rect)
        camera_text = (
            f"C922 {self.tracker.actual_width}×{self.tracker.actual_height} "
            f"@ {self.tracker.actual_fps:.0f} fps | Erkennung {self.tracker.processing_fps:.0f} fps "
            f"| Anzeige {fps:.0f} fps"
        )
        self.screen.blit(self.font_small.render(camera_text, True, self.MUTED), (22, 63))

    def _draw_live_view(self) -> Optional[pygame.Rect]:
        outer = pygame.Rect(20, 92, 740, 416)
        draw_translucent_panel(
            self.screen, outer, self.PANEL, alpha=194, border_radius=8
        )
        pygame.draw.rect(self.screen, self.BORDER, outer, 2, border_radius=8)
        if self.last_frame_rgb is None:
            text = self.font.render("Warte auf Kamerabild …", True, self.MUTED)
            self.screen.blit(text, text.get_rect(center=outer.center))
            return None

        frame = self.last_frame_rgb
        # Die Vorschau wird selbst wieder auf die Leinwand projiziert. Ein
        # Rotfilter darf daraus keine rote optische Rückkopplung erzeugen.
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        neutral = np.empty_like(frame)
        neutral[:, :, 0] = 0
        neutral[:, :, 1] = np.clip(gray.astype(np.float32) * 0.82, 0, 255).astype(np.uint8)
        neutral[:, :, 2] = gray
        frame = neutral
        frame_h, frame_w = frame.shape[:2]
        scale = min((outer.width - 4) / frame_w, (outer.height - 4) / frame_h)
        draw_size = (int(frame_w * scale), int(frame_h * scale))
        view = pygame.Rect(0, 0, *draw_size)
        view.center = outer.center
        surface = pygame.image.frombuffer(frame.tobytes(), (frame_w, frame_h), "RGB").copy()
        surface = pygame.transform.smoothscale(surface, draw_size)
        self.screen.blit(surface, view)
        return view

    def _camera_to_view(self, point: Tuple[int, int], view: pygame.Rect) -> Tuple[int, int]:
        if self.last_frame_rgb is None:
            return view.center
        frame_h, frame_w = self.last_frame_rgb.shape[:2]
        return (
            view.x + int(point[0] * view.width / frame_w),
            view.y + int(point[1] * view.height / frame_h),
        )

    def _draw_detection_overlay(self, view: pygame.Rect) -> None:
        if self.aligner.corners is not None:
            polygon = [
                self._camera_to_view((int(p[0]), int(p[1])), view)
                for p in self.aligner.corners
            ]
            pygame.draw.lines(self.screen, self.GREEN, True, polygon, 3)

        detection = self.last_detection
        if detection and detection.point is not None:
            point = self._camera_to_view(detection.point, view)
            pygame.draw.circle(self.screen, self.YELLOW, point, 7, 2)

        for shot in self.shots:
            point = self._camera_to_view(shot.camera_point, view)
            self._draw_cross(point, self.CYAN, 13, 3)
            label = self.font_small.render(str(shot.number), True, self.TEXT)
            self.screen.blit(label, (point[0] + 9, point[1] - 22))

    def _draw_side_panel(self) -> None:
        panel = pygame.Rect(780, 92, 224, 506)
        draw_translucent_panel(
            self.screen, panel, self.PANEL, alpha=194, border_radius=8
        )
        pygame.draw.rect(self.screen, self.BORDER, panel, 2, border_radius=8)
        for index, (title_text, color) in enumerate(
            (("IMPULSMASKE", self.TEXT), ("LETZTER PEAK", self.CYAN))
        ):
            mask_title = self.font_small.render(title_text, True, color)
            self.screen.blit(
                mask_title, mask_title.get_rect(midtop=(892, 102 + index * 19))
            )

        mask_rect = pygame.Rect(792, 143, 200, 107)
        pygame.draw.rect(self.screen, (0, 0, 0), mask_rect)
        held_mask = self.last_peak_mask_rgb
        if held_mask is not None:
            mask = held_mask
            mh, mw = mask.shape[:2]
            surf = pygame.image.frombuffer(mask.tobytes(), (mw, mh), "RGB").copy()
            surf = pygame.transform.scale(surf, mask_rect.size)
            self.screen.blit(surf, mask_rect)
        else:
            waiting = self.font_small.render("Noch kein Schuss", True, self.MUTED)
            self.screen.blit(waiting, waiting.get_rect(center=mask_rect.center))

        detection = self.last_peak_detection
        lines = [
            f"Punkt: {detection.point if detection else '—'}",
            f"Fläche: {detection.area:.1f}" if detection else "Fläche: —",
            f"Sicherheit: {detection.confidence:.0%}" if detection else "Sicherheit: —",
            f"Rotüberschuss: {detection.peak_red_excess}" if detection else "Rotüberschuss: —",
            f"Bildänderung: {detection.peak_delta}" if detection else "Bildänderung: —",
        ]
        for index, line in enumerate(lines):
            self.screen.blit(self.font_small.render(line, True, self.MUTED), (794, 265 + index * 24))

        target = pygame.Rect(794, 438, 196, 78)
        pygame.draw.rect(self.screen, (9, 12, 18), target)
        pygame.draw.rect(self.screen, self.BORDER, target, 2)
        for index, heading_text in enumerate(("LEINWAND", "LETZTER SCHUSS")):
            target_heading = self.font_small.render(
                heading_text, True, self.TEXT if index else self.CYAN
            )
            self.screen.blit(
                target_heading,
                target_heading.get_rect(midtop=(892, 390 + index * 21)),
            )
        if self.shots and self.shots[-1].screen_point is not None:
            shot = self.shots[-1]
            sw, sh = self.screen.get_size()
            px = target.x + int(shot.screen_point[0] * target.width / sw)
            py = target.y + int(shot.screen_point[1] * target.height / sh)
            self._draw_cross((px, py), self.CYAN, 10, 3)
            coord = self.font_small.render(
                f"x={shot.screen_point[0]}  y={shot.screen_point[1]}", True, self.CYAN
            )
            self.screen.blit(coord, (target.x + 8, target.bottom + 8))

    def _draw_shot_list(self) -> None:
        panel = pygame.Rect(20, 526, 740, 150)
        draw_translucent_panel(
            self.screen, panel, self.PANEL, alpha=194, border_radius=8
        )
        pygame.draw.rect(self.screen, self.BORDER, panel, 2, border_radius=8)
        if self.shots:
            headline_color = self.CYAN
            headline = "SCHUSS ERKANNT"
        elif self.aligner.phase == "failed":
            headline_color = self.RED
            headline = "KAMERA AUSRICHTEN"
        else:
            headline_color = self.TEXT
            headline = "Bereit – auf die Leinwand schießen"
        self.screen.blit(self.font_large.render(headline, True, headline_color), (34, 540))

        recent = list(self.shots)[-3:]
        if not recent:
            if self.aligner.phase == "failed":
                wrapped = textwrap.wrap(self.aligner.message, width=72)
                for index, line in enumerate(wrapped[:3]):
                    self.screen.blit(
                        self.font.render(line, True, self.MUTED), (36, 594 + index * 30)
                    )
                return
            hints = [
                "Auf eine beliebige Stelle der Leinwand schießen.",
                "Der Treffer erscheint rechts unter ‚Letzter Schuss‘.",
            ]
            for index, line in enumerate(hints):
                self.screen.blit(self.font.render(line, True, self.MUTED), (36, 594 + index * 32))
            return

        for index, shot in enumerate(reversed(recent)):
            mapped = shot.screen_point if shot.screen_point is not None else ("—", "—")
            line = (
                f"#{shot.number:02d}  Kamera {shot.camera_point[0]:4d},{shot.camera_point[1]:4d}  "
                f"→ Leinwand {mapped[0]},{mapped[1]}  ({shot.confidence:.0%})"
            )
            color = self.TEXT if index == 0 else self.MUTED
            self.screen.blit(self.font_small.render(line, True, color), (36, 590 + index * 27))

    def _close_pin_card_rect(self) -> pygame.Rect:
        card = pygame.Rect(0, 0, 570, 640)
        card.center = self.screen.get_rect().center
        return card

    def _draw_close_pin_overlay(self) -> None:
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 5, 14, 226))
        self.screen.blit(veil, (0, 0))

        card = self._close_pin_card_rect()
        shadow = card.move(0, 10).inflate(18, 16)
        draw_translucent_panel(
            self.screen, shadow, (0, 2, 9), alpha=208, border_radius=24
        )
        draw_translucent_panel(
            self.screen, card, (0, 18, 32), alpha=244, border_radius=22
        )
        pygame.draw.rect(self.screen, self.TARGET_GREEN, card, 3, border_radius=22)
        pygame.draw.line(
            self.screen,
            self.TARGET_CYAN,
            (card.left + 52, card.top + 8),
            (card.right - 52, card.top + 8),
            3,
        )

        lock_center = (card.centerx, card.top + 55)
        pygame.draw.arc(
            self.screen,
            self.TARGET_CYAN,
            pygame.Rect(lock_center[0] - 18, lock_center[1] - 23, 36, 38),
            0,
            math.pi,
            5,
        )
        lock_body = pygame.Rect(lock_center[0] - 25, lock_center[1] - 1, 50, 38)
        pygame.draw.rect(self.screen, (0, 35, 48), lock_body, border_radius=8)
        pygame.draw.rect(self.screen, self.TARGET_GREEN, lock_body, 3, border_radius=8)
        pygame.draw.circle(self.screen, self.TARGET_CYAN, (lock_center[0], lock_center[1] + 13), 5)
        pygame.draw.line(
            self.screen,
            self.TARGET_CYAN,
            (lock_center[0], lock_center[1] + 17),
            (lock_center[0], lock_center[1] + 25),
            3,
        )

        title = self.font_large.render("PROGRAMM BEENDEN", True, self.TARGET_CYAN)
        self.screen.blit(title, title.get_rect(midtop=(card.centerx, card.top + 101)))
        subtitle = self.font.render(
            "Zum Beenden die vierstellige PIN eingeben", True, self.TEXT
        )
        self.screen.blit(subtitle, subtitle.get_rect(midtop=(card.centerx, card.top + 148)))

        dots_y = card.top + 211
        for index in range(4):
            filled = index < len(self.close_pin_digits)
            pygame.draw.circle(
                self.screen,
                self.TARGET_GREEN if filled else self.TARGET_MUTED,
                (round(card.centerx + (index - 1.5) * 43), dots_y),
                11,
                0 if filled else 2,
            )
        if self.close_pin_message:
            message = self.font_small.render(
                self.close_pin_message, True, self.RED
            )
            self.screen.blit(message, message.get_rect(midtop=(card.centerx, card.top + 236)))

        for label, rect in self._close_pin_buttons():
            color = self.TARGET_CYAN if label == "ABBRECHEN" else self.TARGET_GREEN
            if label == "LÖSCHEN":
                color = self.TARGET_MUTED
            draw_vintage_enamel_panel(
                self.screen,
                rect,
                3 if label == "LÖSCHEN" else 2 if label.isdigit() else 1,
                alpha=242,
            )
            pygame.draw.rect(self.screen, color, rect, 2, border_radius=10)
            self._draw_button_aim_point(rect, color)
            font = self.font_large if label.isdigit() else self.font_small
            text = font.render(label, True, color)
            self.screen.blit(text, text.get_rect(center=rect.center))

        hint = self.font_small.render(
            "Vollständig mit Pistole, Maus oder Tastatur bedienbar",
            True,
            self.TARGET_MUTED,
        )
        self.screen.blit(hint, hint.get_rect(midbottom=(card.centerx, card.bottom - 18)))

    def _draw_buttons(self) -> None:
        self._draw_button(self.diagnostic_target_button, "Menü", self.CYAN)
        self._draw_button(self.diagnostic_settings_button, "Kamera einstellen", self.CYAN)
        self._draw_button(self.diagnostic_sighting_button, "Einschießen", self.GREEN)
        self._draw_button(self.align_button, "Neu ausrichten", self.GREEN)
        self._draw_button(self.diagnostic_close_button, "Programm beenden", self.GREEN)

    def _draw_button(self, rect: pygame.Rect, label: str, color: Tuple[int, int, int]) -> None:
        draw_button(self.screen, rect, label, self.font_small, color)

    def _draw_cross(
        self,
        point: Tuple[int, int],
        color: Tuple[int, int, int],
        radius: int,
        width: int,
    ) -> None:
        pygame.draw.circle(self.screen, color, point, radius, width)
        pygame.draw.line(
            self.screen, color, (point[0] - radius - 5, point[1]), (point[0] + radius + 5, point[1]), width
        )
        pygame.draw.line(
            self.screen, color, (point[0], point[1] - radius - 5), (point[0], point[1] + radius + 5), width
        )
