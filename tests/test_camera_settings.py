from __future__ import annotations

import os
import unittest
from copy import deepcopy
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import cv2
import numpy as np
import pygame

from laser_arcade.calibration import CalibrationData, calculate_homography, validate_camera_quad
from laser_arcade.config import SETTINGS_VERSION, Settings
from laser_arcade.diagnostic_ui import AutomaticAligner, LaserDiagnosticUI
from laser_arcade.laser_tracker import CameraFilterClassifier, LaserDetection
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class CameraSettingsTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_width = 640
    processing_height = 360
    processing_fps = 30.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.active_filter_profile = "normal"
        self.reset_count = 0
        self.reload_count = 0
        self.moorhuhn_filter_enabled = False
        self.calibration_mode = False

    def reset_state(self) -> None:
        self.reset_count += 1

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        self.moorhuhn_filter_enabled = enabled

    def apply_laser_settings(self, laser) -> None:
        self.settings.laser = deepcopy(laser)
        if laser.filter_mode in {"normal", "red_filter"}:
            self.active_filter_profile = laser.filter_mode
        self.reset_state()

    def reload_calibration(self) -> None:
        self.reload_count += 1
        self.reset_state()

    def apply_startup_optical_profile(
        self,
        profile: str,
        confidence: float,
        **_metrics,
    ) -> None:
        self.active_filter_profile = profile
        self.reset_state()

    def set_calibration_mode(self, enabled: bool) -> bool:
        changed = self.calibration_mode != bool(enabled)
        self.calibration_mode = bool(enabled)
        return changed


class ConfigurationMigrationTest(unittest.TestCase):
    def test_version_six_values_are_preserved_as_normal_profile(self) -> None:
        legacy = {
            "settings_version": 6,
            "screen_width": 1280,
            "screen_height": 720,
            "camera": {
                "device_index": 2,
                "width": 800,
                "height": 600,
                "fps": 25,
                "fourcc": "MJPG",
            },
            "laser": {
                "lower1": [0, 18, 72],
                "upper1": [24, 255, 255],
                "lower2": [166, 18, 72],
                "upper2": [180, 255, 255],
                "min_area": 3,
                "max_area": 900,
                "morph_kernel": 5,
                "min_red_excess": 17,
                "min_frame_delta": 13,
                "debounce_ms": 190,
                "background_alpha": 0.04,
            },
        }

        settings = Settings.from_dict(legacy)

        self.assertEqual(settings.settings_version, SETTINGS_VERSION)
        self.assertEqual(settings.camera.device_index, 2)
        self.assertEqual(settings.camera.width, 800)
        self.assertEqual(settings.laser.normal.min_red_excess, 17)
        self.assertEqual(settings.laser.normal.min_frame_delta, 13)
        self.assertEqual(settings.laser.normal.min_value, 72)
        self.assertEqual(settings.laser.normal.min_area, 3)
        self.assertEqual(settings.laser.normal.max_area, 900)
        self.assertEqual(settings.laser.filter_mode, "auto")
        self.assertTrue(settings.laser.red_filter.strict_temporal)
        self.assertEqual(settings.laser.red_filter.max_area, 320)


class ProjectorStartupTest(unittest.TestCase):
    @staticmethod
    def _feed_for(
        aligner: AutomaticAligner,
        frame: np.ndarray,
        start: float,
        duration: float,
        step: float = 0.04,
    ) -> float:
        now = start
        while now <= start + duration:
            aligner.feed(frame, now)
            now += step
        return now

    def test_start_waits_until_projector_is_bright_and_stable(self) -> None:
        aligner = AutomaticAligner((1024, 768))
        dark = np.full((90, 160, 3), 12, dtype=np.uint8)
        bright = np.full((90, 160, 3), 180, dtype=np.uint8)
        aligner.start(0.0)
        now = self._feed_for(aligner, dark, 0.0, 1.7)
        self.assertEqual(aligner.phase, "projector_wait")

        # Ein noch hochfahrender Beamer wird trotz längst verstrichener Zeit
        # nicht als stabil angenommen.
        for value in (45, 75, 110, 145, 170, 180):
            now = self._feed_for(
                aligner, np.full_like(bright, value), now, 0.35
            )
        self.assertEqual(aligner.phase, "projector_wait")

        now = self._feed_for(aligner, bright, now, 3.35)
        self.assertEqual(aligner.phase, "dark_settle")

    def test_manual_alignment_also_waits_for_projector_without_replacing_it(self) -> None:
        aligner = AutomaticAligner((1024, 768))
        corners = [(120, 65), (520, 48), (555, 310), (92, 322)]
        matrix = np.eye(3, dtype=np.float32)
        aligner.start_verification(matrix, corners, now=0.0)

        self.assertEqual(aligner.phase, "projector_dark_settle")
        self.assertTrue(aligner.preserve_alignment_on_verification_failure)
        np.testing.assert_array_equal(aligner.homography, matrix)

    def test_sensitivity_scales_thresholds_but_not_point_area(self) -> None:
        profile = Settings().laser.normal
        profile.sensitivity = 0
        strict = profile.runtime_values()
        profile.sensitivity = 100
        sensitive = profile.runtime_values()
        self.assertGreater(strict[0], sensitive[0])
        self.assertGreater(strict[1], sensitive[1])
        self.assertGreater(strict[2], sensitive[2])
        self.assertEqual(strict[3:], sensitive[3:])


class FilterProfileTest(unittest.TestCase):
    @staticmethod
    def _normal_frame() -> np.ndarray:
        return np.full((80, 120, 3), (70, 72, 74), dtype=np.uint8)

    @staticmethod
    def _filtered_frame() -> np.ndarray:
        return np.full((80, 120, 3), (8, 10, 105), dtype=np.uint8)

    def test_auto_filter_requires_three_stable_seconds(self) -> None:
        classifier = CameraFilterClassifier("auto")
        frame = self._filtered_frame()
        self.assertEqual(classifier.update(frame, 0.0)[0], "normal")
        self.assertEqual(classifier.update(frame, 2990.0)[0], "normal")
        profile, confidence, changed = classifier.update(frame, 3010.0)
        self.assertEqual(profile, "red_filter")
        self.assertTrue(changed)
        self.assertGreater(confidence, 0.5)

    def test_small_red_object_does_not_switch_profile(self) -> None:
        classifier = CameraFilterClassifier("auto")
        frame = self._normal_frame()
        frame[20:45, 30:55] = (5, 8, 180)
        for now in (0.0, 1500.0, 3500.0, 8000.0):
            profile, _, changed = classifier.update(frame, now)
            self.assertEqual(profile, "normal")
            self.assertFalse(changed)

    def test_hysteresis_prevents_flapping_in_ambiguous_scene(self) -> None:
        classifier = CameraFilterClassifier("red_filter")
        classifier.set_mode("auto", 0.0)
        ambiguous = np.full((80, 120, 3), (30, 32, 48), dtype=np.uint8)
        for now in (0.0, 4000.0, 9000.0):
            profile, _, changed = classifier.update(ambiguous, now)
            self.assertEqual(profile, "red_filter")
            self.assertFalse(changed)

    def test_red_filter_profile_rejects_white_text_flicker_but_accepts_laser(self) -> None:
        config = DetectionConfig(
            min_red_excess=55,
            min_frame_delta=110,
            min_value=85,
            max_area=320,
            strict_temporal=True,
        )
        background = np.full((90, 140, 3), (8, 10, 105), dtype=np.uint8)
        detector = PulseShotDetector(config)
        detector.process(background, 0.0)

        white_text = background.copy()
        cv2.rectangle(white_text, (20, 30), (100, 38), (10, 12, 190), thickness=-1)
        self.assertFalse(detector.process(white_text, 40.0).shot)
        detector.process(background, 80.0)

        laser = background.copy()
        cv2.circle(laser, (72, 52), 3, (5, 7, 255), thickness=-1)
        result = detector.process(laser, 220.0)
        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 72), 2)
        self.assertLessEqual(abs(result.point[1] - 52), 2)


class ManualCornerTest(unittest.TestCase):
    def test_valid_quad_calculates_preview_without_writing(self) -> None:
        corners = [(120, 72), (510, 84), (525, 325), (105, 314)]
        valid, _ = validate_camera_quad(corners, (640, 360))
        self.assertTrue(valid)
        data = calculate_homography(corners, [(0, 0), (1023, 0), (1023, 767), (0, 767)])
        self.assertIsNotNone(data.homography)

    def test_crossed_or_tiny_quad_is_rejected(self) -> None:
        crossed = [(100, 80), (500, 300), (500, 80), (100, 300)]
        tiny = [(100, 100), (130, 100), (130, 125), (100, 125)]
        self.assertFalse(validate_camera_quad(crossed, (640, 360))[0])
        self.assertFalse(validate_camera_quad(tiny, (640, 360))[0])


class CameraSettingsUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def _ui(self) -> LaserDiagnosticUI:
        settings = Settings()
        tracker = CameraSettingsTracker(settings)
        ui = LaserDiagnosticUI(
            pygame.Surface((1024, 768)),
            settings,
            tracker,
            weapon_calibration_path=None,
            target_history_path=None,
            water_alarm_leaderboard_path=None,
            arcade_leaderboard_path=None,
        )
        ui.aligner.phase = "success"
        ui.aligner.homography = np.eye(3, dtype=np.float32)
        ui.aligner.corners = np.asarray(
            [(120, 72), (510, 84), (525, 325), (105, 314)], dtype=np.float32
        )
        ui.last_frame_rgb = np.full((360, 640, 3), (155, 40, 30), dtype=np.uint8)
        ui.last_detection = LaserDetection(
            point=None,
            area=0.0,
            confidence=0.0,
            frame_ts=0.0,
            mask_preview=np.zeros((112, 200, 3), dtype=np.uint8),
            frame_preview=ui.last_frame_rgb,
            red_threshold=18,
            delta_threshold=14,
            active_filter_profile="normal",
            filter_confidence=0.9,
        )
        ui._open_camera_settings()
        return ui

    def test_mouse_drag_changes_only_draft_and_cancel_restores_profile(self) -> None:
        ui = self._ui()
        original_h = ui.aligner.homography.copy()
        handle = ui._camera_point_to_settings_view(ui.camera_corner_draft[0])
        ui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=handle))
        ui.handle_event(
            pygame.event.Event(
                pygame.MOUSEMOTION,
                pos=(handle[0] + 12, handle[1] + 8),
                rel=(12, 8),
                buttons=(1, 0, 0),
            )
        )
        ui.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=handle))
        self.assertTrue(ui.camera_corners_dirty)
        np.testing.assert_array_equal(ui.aligner.homography, original_h)

        ui.camera_settings_tab = "detection"
        ui._set_camera_filter_mode("red_filter")
        self.assertEqual(ui.tracker.active_filter_profile, "red_filter")
        ui._close_camera_settings(save=False)
        self.assertEqual(ui.settings.laser.filter_mode, "auto")

    def test_pistol_can_select_corner_nudge_and_change_threshold(self) -> None:
        ui = self._ui()
        ui.camera_selected_corner = 0
        before = ui.camera_corner_draft[0]
        ui._handle_camera_settings_shot(ui._camera_corner_control_rects()["right"].center, 10.0)
        self.assertEqual(ui.camera_corner_draft[0], (before[0] + 1, before[1]))

        ui.camera_settings_tab = "detection"
        ui._handle_camera_settings_shot(ui._camera_settings_filter_buttons()["red_filter"].center, 11.0)
        before_sensitivity = ui.camera_settings_draft.red_filter.sensitivity
        plus = next(
            rect for key, direction, rect in ui._camera_settings_slider_buttons()
            if key == "sensitivity" and direction == 1
        )
        ui._handle_camera_settings_shot(plus.center, 12.0)
        self.assertEqual(
            ui.camera_settings_draft.red_filter.sensitivity,
            before_sensitivity + 5,
        )

    def test_apply_persists_manual_corners_and_reloads_mask(self) -> None:
        ui = self._ui()
        ui._move_camera_corner(3, 0)
        with mock.patch("laser_arcade.diagnostic_ui.save_settings") as settings_save, mock.patch(
            "laser_arcade.diagnostic_ui.save_calibration"
        ) as calibration_save:
            ui._apply_camera_settings()
        self.assertFalse(ui.camera_settings_open)
        self.assertTrue(ui.manual_alignment_active)
        self.assertTrue(ui.alignment_changed_since_sighting)
        self.assertEqual(ui.tracker.reload_count, 1)
        settings_save.assert_called_once()
        calibration_save.assert_called_once()

    def test_threshold_only_apply_does_not_lock_or_replace_alignment(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        ui._set_camera_filter_mode("red_filter")
        with mock.patch("laser_arcade.diagnostic_ui.save_settings") as settings_save, mock.patch(
            "laser_arcade.diagnostic_ui.save_calibration"
        ) as calibration_save:
            ui._apply_camera_settings()
        self.assertFalse(ui.manual_alignment_active)
        self.assertEqual(ui.tracker.reload_count, 0)
        settings_save.assert_called_once()
        calibration_save.assert_not_called()

    def test_detection_guide_records_subthreshold_peaks_for_test_colors(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        color_name, rect = next(iter(ui._camera_detection_color_rects().items()))
        detection = LaserDetection(
            point=None,
            area=0.0,
            confidence=0.0,
            frame_ts=10.0,
            mask_preview=None,
            frame_preview=None,
            shot=False,
            red_threshold=100,
            delta_threshold=120,
            observed_point=rect.center,
            observed_area=9.0,
            observed_peak_red=72,
            observed_peak_delta=88,
            observed_peak_value=190,
        )

        ui._update_camera_detection_test(detection, 10.0)

        self.assertEqual(len(ui.camera_detection_samples[color_name]), 1)
        self.assertEqual(ui.camera_detection_last_peak["color"], color_name)
        self.assertFalse(ui.camera_detection_samples[color_name][0]["detected"])

    def test_detection_guide_ignores_continuous_mains_flicker_peak(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        color_name, rect = next(iter(ui._camera_detection_color_rects().items()))
        flicker = LaserDetection(
            point=None,
            area=0.0,
            confidence=0.0,
            frame_ts=10.0,
            mask_preview=None,
            frame_preview=None,
            shot=False,
            red_threshold=81,
            delta_threshold=12,
            observed_point=rect.center,
            observed_area=4.0,
            observed_peak_red=85,
            observed_peak_delta=11,
            observed_peak_value=238,
        )

        ui._update_camera_detection_test(flicker, 10.0)

        self.assertEqual(ui.camera_detection_samples[color_name], [])
        self.assertIsNone(ui.camera_detection_last_peak)

    def test_detection_guide_ignores_strong_peak_outside_test_centers(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        color_name, rect = next(iter(ui._camera_detection_color_rects().items()))
        outside_center = (rect.left + 5, rect.centery)
        interference = LaserDetection(
            point=None,
            area=0.0,
            confidence=0.0,
            frame_ts=10.0,
            mask_preview=None,
            frame_preview=None,
            shot=False,
            red_threshold=77,
            delta_threshold=18,
            observed_point=outside_center,
            observed_area=3.0,
            observed_peak_red=50,
            observed_peak_delta=89,
            observed_peak_value=240,
        )

        ui._update_camera_detection_test(interference, 10.0)

        self.assertEqual(ui.camera_detection_samples[color_name], [])
        self.assertIsNone(ui.camera_detection_last_peak)
        self.assertEqual(ui.camera_detection_last_capture_at, -1e12)

    def test_detection_guide_can_calculate_and_apply_simple_recommendation(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        before = ui._active_camera_draft_profile().sensitivity
        for index, (_, rect) in enumerate(list(ui._camera_detection_color_rects().items())[:3]):
            detection = LaserDetection(
                point=rect.center,
                area=8.0,
                confidence=0.8,
                frame_ts=20.0 + index,
                mask_preview=None,
                frame_preview=None,
                shot=True,
                peak_red_excess=55,
                peak_delta=62,
                red_threshold=72,
                delta_threshold=80,
                observed_point=rect.center,
                observed_area=8.0,
                observed_peak_red=55,
                observed_peak_delta=62,
                observed_peak_value=180,
            )
            ui._update_camera_detection_test(detection, 20.0 + index)

        recommendation = ui._camera_detection_recommendation()
        self.assertIsNotNone(recommendation)
        ui._apply_camera_detection_recommendation()
        self.assertEqual(ui._active_camera_draft_profile().sensitivity, recommendation)
        self.assertGreaterEqual(ui._active_camera_draft_profile().sensitivity, before)
        self.assertTrue(ui.camera_detection_dirty)

    def test_detection_guide_quiet_test_counts_false_triggers(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        ui._start_camera_quiet_test(30.0)
        false_trigger = LaserDetection(
            point=(100, 100),
            area=5.0,
            confidence=0.8,
            frame_ts=31.0,
            mask_preview=None,
            frame_preview=None,
            shot=True,
        )
        ui._update_camera_detection_test(false_trigger, 31.0)
        ui._update_camera_detection_test(
            LaserDetection(None, 0.0, 0.0, 36.0, None, None), 36.0
        )

        self.assertTrue(ui.camera_quiet_test_completed)
        self.assertEqual(ui.camera_quiet_false_triggers, 1)

    def test_detection_guide_draws_all_six_test_colors(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        ui._draw_camera_settings(30.0)
        for name, color in ui.DETECTION_TEST_COLORS:
            rect = ui._camera_detection_color_rects()[name]
            pixel = tuple(ui.screen.get_at((rect.left + 8, rect.centery))[:3])
            self.assertEqual(pixel, color)

    def test_detection_guide_keeps_aim_centers_in_original_test_color(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        ui._draw_camera_settings(30.0)
        for name, color in ui.DETECTION_TEST_COLORS:
            rect = ui._camera_detection_color_rects()[name]
            aim = (rect.centerx, rect.centery - 2)
            pixel = tuple(ui.screen.get_at(aim)[:3])
            self.assertEqual(pixel, color)

    def test_detection_test_labels_choose_readable_laser_neutral_contrast(self) -> None:
        ui = self._ui()
        for name, background in ui.DETECTION_TEST_COLORS:
            with self.subTest(color=name):
                label = ui._camera_test_label_color(background)
                self.assertEqual(label[0], 0)
                if max(background) >= 100:
                    self.assertEqual(label, ui.TARGET_DARK_TEXT)
                else:
                    self.assertEqual(label, ui.TARGET_CYAN)

    def test_detection_guide_requires_all_colors_and_clean_quiet_test(self) -> None:
        ui = self._ui()
        total = len(ui.DETECTION_TEST_COLORS)

        text, _ = ui._camera_detection_verdict(3, 3, total, False, 0)
        self.assertIn("noch 3 Testflächen", text)
        self.assertNotIn("ZUVERLÄSSIG", text)

        text, _ = ui._camera_detection_verdict(total, total, total, False, 0)
        self.assertIn("ohne schuss", text.lower())
        self.assertNotIn("ZUVERLÄSSIG", text)

        text, color = ui._camera_detection_verdict(total, total, total, True, 0)
        self.assertEqual(text, "EINSTELLUNG ZUVERLÄSSIG")
        self.assertEqual(color, ui.TARGET_GREEN)

        text, color = ui._camera_detection_verdict(total, total - 1, total, True, 1)
        self.assertIn("Empfindlichkeit anpassen", text)
        self.assertEqual(color, ui.RED)

    def test_detection_guide_marks_every_recognized_shot_as_safe(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        color_name, rect = next(iter(ui._camera_detection_color_rects().items()))
        ui.camera_detection_samples[color_name] = [
            {
                "red": 18,
                "delta": 14,
                "value": 180,
                "area": 4.0,
                "red_threshold": 18,
                "delta_threshold": 14,
                "detected": True,
            }
        ]

        ui._draw_camera_settings(30.0)

        border_pixel = tuple(ui.screen.get_at((rect.left + 1, rect.centery))[:3])
        self.assertEqual(border_pixel, ui.TARGET_GREEN)

    def test_manual_alignment_starts_with_verification_instead_of_auto_replacement(self) -> None:
        settings = Settings()
        tracker = CameraSettingsTracker(settings)
        stored = CalibrationData(
            homography=np.eye(3, dtype=np.float32),
            camera_points=[(120, 72), (510, 84), (525, 325), (105, 314)],
            screen_points=[(0, 0), (1023, 0), (1023, 767), (0, 767)],
            alignment_mode="manual",
            source_size=(640, 360),
        )
        with mock.patch("laser_arcade.diagnostic_ui.load_homography", return_value=stored):
            ui = LaserDiagnosticUI(
                pygame.Surface((1024, 768)),
                settings,
                tracker,
                weapon_calibration_path=None,
                target_history_path=None,
                water_alarm_leaderboard_path=None,
                arcade_leaderboard_path=None,
            )
        self.assertTrue(ui.manual_alignment_active)
        self.assertEqual(ui.aligner.phase, "projector_dark_settle")
        self.assertEqual(ui.aligner.after_projector_phase, "verify_dark_settle")
        self.assertTrue(ui.aligner.preserve_alignment_on_verification_failure)
        np.testing.assert_array_equal(ui.aligner.homography, np.eye(3, dtype=np.float32))

    def test_failed_manual_verification_can_still_open_editor_with_pistol(self) -> None:
        ui = self._ui()
        ui._close_camera_settings(save=False)
        ui.aligner.phase = "failed"
        ui.aligner.homography = np.eye(3, dtype=np.float32)
        self.assertTrue(ui._handle_laser_control(ui.diagnostic_settings_button.center, 10.0))
        self.assertTrue(ui.camera_settings_open)

    def test_settings_screen_is_laser_neutral_until_original_preview_requested(self) -> None:
        ui = self._ui()
        ui.camera_settings_tab = "detection"
        ui.draw(60.0)
        rgb = pygame.surfarray.array3d(ui.screen).astype(np.int16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))


if __name__ == "__main__":
    unittest.main()
