from __future__ import annotations

import unittest

import cv2
import numpy as np

from laser_arcade.auto_alignment import (
    analyze_startup_color_response,
    detect_projection_quad,
    detect_verification_markers,
    refine_homography_from_precision_markers,
    startup_color_rects,
)
from laser_arcade.light_adaptation import AmbientLightController
from laser_arcade.laser_tracker import build_camera_detection_masks
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class PulseShotDetectorTest(unittest.TestCase):
    def test_overview_mask_detects_only_left_and_right_of_projection(self) -> None:
        projection, overview = build_camera_detection_masks(
            [(120, 48), (520, 58), (542, 314), (102, 304)],
            (640, 360),
            (640, 360),
        )

        self.assertEqual(int(projection[180, 60]), 0)
        self.assertEqual(int(projection[180, 590]), 0)
        self.assertEqual(int(overview[180, 60]), 255)
        self.assertEqual(int(overview[180, 590]), 255)
        self.assertEqual(int(overview[15, 320]), 0)

        background = np.full((360, 640, 3), 35, dtype=np.uint8)
        pulse = background.copy()
        cv2.circle(pulse, (60, 180), 3, (5, 8, 245), thickness=-1)

        game_detector = PulseShotDetector(DetectionConfig())
        game_detector.process(background, 0.0, projection)
        self.assertFalse(game_detector.process(pulse, 40.0, projection).shot)

        overview_detector = PulseShotDetector(DetectionConfig())
        overview_detector.process(background, 0.0, overview)
        result = overview_detector.process(pulse, 40.0, overview)
        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 60), 2)
        self.assertLessEqual(abs(result.point[1] - 180), 2)

    def test_short_red_pulse_triggers_exactly_once(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((120, 160, 3), 45, dtype=np.uint8)
        detector.process(background, 0.0)
        detector.process(background, 20.0)

        pulse = background.copy()
        cv2.circle(pulse, (92, 61), 3, (0, 10, 255), thickness=-1)
        first = detector.process(pulse, 40.0)
        held = detector.process(pulse, 56.0)

        self.assertTrue(first.shot)
        self.assertIsNotNone(first.point)
        self.assertLessEqual(abs(first.point[0] - 92), 2)
        self.assertLessEqual(abs(first.point[1] - 61), 2)
        self.assertFalse(held.shot)

    def test_weak_filtered_single_pixel_pulse_is_detected_precisely(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((80, 120, 3), 35, dtype=np.uint8)
        detector.process(background, 0.0)

        pulse = background.copy()
        pulse[42, 73] = (28, 38, 122)
        result = detector.process(pulse, 40.0)

        self.assertTrue(result.shot)
        self.assertEqual(result.point, (73, 42))

    def test_fading_tail_rearms_next_pistol_pulse_without_held_duplicates(self) -> None:
        detector = PulseShotDetector(DetectionConfig(debounce_ms=100))
        background = np.full((80, 120, 3), 35, dtype=np.uint8)
        detector.process(background, 0.0)

        pulse = background.copy()
        cv2.circle(pulse, (60, 40), 2, (8, 18, 230), thickness=-1)
        self.assertTrue(detector.process(pulse, 20.0).shot)
        self.assertFalse(detector.process(pulse, 140.0).shot)

        tail = background.copy()
        cv2.circle(tail, (60, 40), 2, (18, 28, 120), thickness=-1)
        self.assertFalse(detector.process(tail, 160.0).shot)
        self.assertFalse(detector.process(tail, 180.0).shot)

        second = background.copy()
        cv2.circle(second, (60, 40), 2, (8, 18, 230), thickness=-1)
        self.assertTrue(detector.process(second, 280.0).shot)

    def test_weak_pulse_survives_bright_ambient_light(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((100, 140, 3), (184, 188, 192), dtype=np.uint8)
        detector.process(background, 0.0)

        pulse = background.copy()
        pulse[54:56, 82:84] = (180, 184, 211)
        result = detector.process(pulse, 40.0)

        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 82), 1)
        self.assertLessEqual(abs(result.point[1] - 54), 1)

    def test_nearly_white_center_still_detects_low_saturation_laser(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((90, 140, 3), (246, 247, 248), dtype=np.uint8)
        detector.process(background, 0.0)
        detector.process(background, 30.0)

        pulse = background.copy()
        pulse[43:46, 68:71] = (246, 247, 254)
        result = detector.process(pulse, 70.0)

        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 69), 1)
        self.assertLessEqual(abs(result.point[1] - 44), 1)

    def test_cyan_object_motion_is_not_mistaken_for_colored_surface_laser(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((90, 140, 3), (25, 28, 30), dtype=np.uint8)
        detector.process(background, 0.0)

        moved_cyan = background.copy()
        moved_cyan[42:48, 67:73] = (172, 151, 58)
        result = detector.process(moved_cyan, 40.0)

        self.assertFalse(result.shot)
        self.assertIsNone(result.point)

    def test_strict_profile_still_detects_temporal_red_rise_on_cool_white(self) -> None:
        detector = PulseShotDetector(
            DetectionConfig(
                min_red_excess=46,
                min_frame_delta=18,
                min_value=72,
            )
        )
        background = np.full((90, 140, 3), (190, 200, 185), dtype=np.uint8)
        detector.process(background, 0.0)

        pulse = background.copy()
        pulse[43:46, 68:71] = (190, 200, 212)
        result = detector.process(pulse, 40.0)

        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 69), 1)
        self.assertLessEqual(abs(result.point[1] - 44), 1)

    def test_repeated_shots_on_same_bright_center_are_all_detected(self) -> None:
        detector = PulseShotDetector(DetectionConfig(debounce_ms=120))
        background = np.full((90, 140, 3), (239, 241, 243), dtype=np.uint8)
        detector.process(background, 0.0)

        pulse = background.copy()
        cv2.circle(pulse, (70, 45), 2, (239, 241, 254), thickness=-1)
        self.assertTrue(detector.process(pulse, 30.0).shot)
        self.assertFalse(detector.process(background, 60.0).shot)
        detector.process(background, 100.0)
        self.assertTrue(detector.process(pulse, 180.0).shot)

    def test_bright_non_red_changes_never_count_as_shots(self) -> None:
        background = np.full((80, 120, 3), (210, 210, 210), dtype=np.uint8)
        non_red_colors = (
            (255, 255, 255),  # Weiß
            (255, 245, 210),  # Blau/Cyan
            (210, 255, 230),  # Grün
        )

        for color in non_red_colors:
            with self.subTest(color=color):
                detector = PulseShotDetector(DetectionConfig())
                detector.process(background, 0.0)
                changed = background.copy()
                changed[38:43, 58:63] = color

                self.assertFalse(detector.process(changed, 40.0).shot)

    def test_dense_persistent_hit_marks_do_not_mask_next_center_shot(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((100, 150, 3), 38, dtype=np.uint8)
        detector.process(background, 0.0)

        marked = background.copy()
        for offset in range(-18, 19, 3):
            cv2.line(marked, (75 + offset, 30), (75 + offset, 70), (245, 205, 0), 2)
            cv2.line(marked, (55, 50 + offset), (95, 50 + offset), (120, 225, 0), 2)
        self.assertFalse(detector.process(marked, 40.0).shot)
        self.assertFalse(detector.process(marked, 80.0).shot)

        pulse = marked.copy()
        pulse[48:52, 73:77] = (240, 242, 255)
        result = detector.process(pulse, 220.0)

        self.assertTrue(result.shot)
        self.assertLessEqual(abs(result.point[0] - 75), 2)
        self.assertLessEqual(abs(result.point[1] - 50), 2)

    def test_adaptive_noise_floor_rejects_broad_sunlight_color_shift(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((80, 120, 3), (175, 177, 179), dtype=np.uint8)
        detector.process(background, 0.0)

        shifted = background.copy()
        shifted[:, :, 2] = 190
        result = detector.process(shifted, 40.0)

        self.assertFalse(result.shot)
        self.assertGreater(result.red_threshold, detector.config.min_red_excess)

    def test_room_light_switch_is_suppressed_and_detector_recovers(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        dark = np.full((80, 120, 3), 38, dtype=np.uint8)
        detector.process(dark, 0.0)

        lit = np.full((80, 120, 3), 142, dtype=np.uint8)
        # Kleine rote Reflexion während des Einschaltens: Sie wäre für sich
        # ein gültiger Kandidat, gehört aber zum großflächigen Lichtwechsel.
        lit[31:34, 47:50] = (92, 96, 238)
        switched = detector.process(lit, 40.0)

        self.assertFalse(switched.shot)
        detector.process(lit, 500.0)
        pulse = lit.copy()
        cv2.circle(pulse, (78, 52), 2, (5, 8, 255), thickness=-1)
        recovered = detector.process(pulse, 560.0)
        self.assertTrue(recovered.shot)

    def test_single_borderline_light_flicker_pixel_is_not_a_shot(self) -> None:
        detector = PulseShotDetector(
            DetectionConfig(
                min_red_excess=51,
                min_frame_delta=5,
                morph_kernel=1,
            )
        )
        background = np.full((80, 120, 3), (150, 150, 225), dtype=np.uint8)
        detector.process(background, 0.0)

        flicker = background.copy()
        flicker[40:42, 60:62, 2] = 235
        result = detector.process(flicker, 40.0)

        self.assertIsNotNone(result.point)
        self.assertFalse(result.shot)
        self.assertLess(result.confidence, detector.MIN_SHOT_CONFIDENCE)

    def test_mains_flicker_with_falling_blue_channel_is_not_a_shot(self) -> None:
        detector = PulseShotDetector(
            DetectionConfig(min_red_excess=51, min_frame_delta=5, morph_kernel=1)
        )
        background = np.full((80, 120, 3), (118, 173, 226), dtype=np.uint8)
        detector.process(background, 0.0)

        flicker = background.copy()
        flicker[38:40, 58:60] = (116, 172, 238)
        result = detector.process(flicker, 40.0)

        self.assertFalse(result.shot)
        self.assertIsNone(result.point)
        self.assertIsNone(result.observed_point)

    def test_red_edge_from_blue_channel_drop_is_not_a_shot(self) -> None:
        detector = PulseShotDetector(
            DetectionConfig(min_red_excess=51, min_frame_delta=5, morph_kernel=1)
        )
        background = np.full((80, 120, 3), (90, 175, 252), dtype=np.uint8)
        detector.process(background, 0.0)

        flicker = background.copy()
        flicker[38:40, 58:60] = (76, 171, 254)
        result = detector.process(flicker, 40.0)

        self.assertFalse(result.shot)
        self.assertIsNone(result.point)

    def test_probe_peak_is_visible_even_when_threshold_is_too_strict(self) -> None:
        detector = PulseShotDetector(
            DetectionConfig(min_red_excess=150, min_frame_delta=180)
        )
        background = np.full((80, 120, 3), 42, dtype=np.uint8)
        detector.process(background, 0.0)
        pulse = background.copy()
        cv2.circle(pulse, (73, 41), 2, (25, 34, 118), thickness=-1)

        result = detector.process(pulse, 40.0)

        self.assertFalse(result.shot)
        self.assertIsNone(result.point)
        self.assertIsNotNone(result.observed_point)
        self.assertLessEqual(abs(result.observed_point[0] - 73), 2)
        self.assertLessEqual(abs(result.observed_point[1] - 41), 2)
        self.assertGreater(result.observed_peak_red, 0)
        self.assertGreater(result.observed_peak_delta, 0)
        self.assertGreater(result.observed_area, 0)

    def test_projection_mask_ignores_red_motion_outside_screen(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        background = np.full((80, 120, 3), 45, dtype=np.uint8)
        region = np.zeros((80, 120), dtype=np.uint8)
        region[10:70, 20:100] = 255
        detector.process(background, 0.0, region)

        outside = background.copy()
        cv2.circle(outside, (8, 40), 3, (0, 0, 255), thickness=-1)
        result = detector.process(outside, 40.0, region)

        self.assertFalse(result.shot)

    def test_static_red_area_is_absorbed_by_background(self) -> None:
        detector = PulseShotDetector(DetectionConfig(background_alpha=0.5))
        frame = np.full((80, 120, 3), 40, dtype=np.uint8)
        cv2.rectangle(frame, (10, 10), (45, 35), (0, 0, 220), thickness=-1)
        detector.process(frame, 0.0)
        result = detector.process(frame, 20.0)
        self.assertFalse(result.shot)
        self.assertIsNone(result.point)

    def test_moorhuhn_animation_does_not_block_following_real_laser(self) -> None:
        detector = PulseShotDetector(DetectionConfig())
        detector.set_signature_filter(True)
        background = np.zeros((80, 120, 3), dtype=np.uint8)
        detector.process(background, 0.0)

        animation = background.copy()
        animation[30:36, 40:48] = (20, 45, 108)  # Rotüberschuss 63, Delta 108
        animated = detector.process(animation, 40.0)
        self.assertFalse(animated.shot)
        self.assertFalse(detector.previous_active)

        strong_animation = background.copy()
        strong_animation[18:24, 20:28] = (16, 24, 108)  # Rot 84, Delta 108
        animated = detector.process(strong_animation, 60.0)
        self.assertFalse(animated.shot)
        self.assertFalse(detector.previous_active)

        laser = background.copy()
        laser[45:49, 72:76] = (15, 20, 220)
        real = detector.process(laser, 80.0)
        self.assertTrue(real.shot)
        self.assertIsNotNone(real.point)


class AmbientLightControllerTest(unittest.TestCase):
    @staticmethod
    def _scene(screen_value: int, outside_value: int) -> tuple[np.ndarray, np.ndarray]:
        frame = np.full((100, 160, 3), outside_value, dtype=np.uint8)
        mask = np.zeros((100, 160), dtype=np.uint8)
        mask[15:85, 25:135] = 255
        frame[mask.astype(bool)] = screen_value
        return frame, mask

    def test_bright_wintergarden_reduces_exposure(self) -> None:
        controller = AmbientLightController(settle_ms=0.0, interval_ms=0.0)
        frame, mask = self._scene(245, 170)

        decision = controller.update(frame, 0.0, mask)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.changed)
        self.assertLess(decision.exposure, 160)
        self.assertLess(decision.metrics.screen_to_ambient_ratio, 1.5)

    def test_dark_room_increases_exposure(self) -> None:
        controller = AmbientLightController(settle_ms=0.0, interval_ms=0.0)
        frame, mask = self._scene(120, 18)

        decision = controller.update(frame, 0.0, mask)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.changed)
        self.assertGreater(decision.exposure, 160)

    def test_bright_projection_reduces_exposure_to_preserve_laser_headroom(self) -> None:
        controller = AmbientLightController(settle_ms=0.0, interval_ms=0.0)
        frame, mask = self._scene(250, 20)

        decision = controller.update(frame, 0.0, mask)

        self.assertIsNotNone(decision)
        self.assertTrue(decision.changed)
        self.assertLess(decision.exposure, 160)

class AutomaticAlignmentTest(unittest.TestCase):
    def test_projection_quad_from_black_white_difference(self) -> None:
        shape = (480, 640, 3)
        dark = np.full(shape, 20, dtype=np.uint8)
        bright = dark.copy()
        expected = np.array([[54, 42], [590, 62], [562, 431], [72, 417]], dtype=np.int32)
        cv2.fillConvexPoly(bright, expected, (235, 235, 235))

        result = detect_projection_quad([dark] * 10, [bright] * 10)

        self.assertGreater(result.confidence, 0.7)
        for actual, target in zip(result.corners, expected):
            self.assertLess(float(np.linalg.norm(actual - target)), 18.0)

    def test_clipped_projection_is_rejected(self) -> None:
        shape = (240, 320, 3)
        dark = np.full(shape, 20, dtype=np.uint8)
        bright = dark.copy()
        clipped = np.array([[70, 0], [250, 0], [270, 210], [50, 210]], dtype=np.int32)
        cv2.fillConvexPoly(bright, clipped, (235, 235, 235))

        with self.assertRaisesRegex(RuntimeError, "abgeschnitten"):
            detect_projection_quad([dark] * 8, [bright] * 8)

    def test_four_corner_markers_verify_homography(self) -> None:
        shape = (360, 640, 3)
        dark = np.full(shape, 15, dtype=np.uint8)
        markers = dark.copy()
        camera_points = np.array(
            [[120, 65], [520, 48], [555, 310], [92, 322]], dtype=np.float32
        )
        screen_points = np.array(
            [[48, 48], [975, 48], [975, 719], [48, 719]], dtype=np.float32
        )
        homography = cv2.getPerspectiveTransform(camera_points, screen_points)
        for point in camera_points.astype(int):
            cv2.rectangle(
                markers,
                (point[0] - 7, point[1] - 7),
                (point[0] + 7, point[1] + 7),
                (245, 245, 245),
                thickness=-1,
            )

        result = detect_verification_markers(
            [dark] * 8,
            [markers] * 8,
            homography,
            [tuple(point) for point in screen_points.astype(int)],
        )

        self.assertLess(result.max_error, 5.0)

    def test_precision_frame_refines_from_twelve_edge_markers(self) -> None:
        shape = (360, 640, 3)
        screen_size = (1024, 768)
        dark = np.full(shape, 16, dtype=np.uint8)
        markers = dark.copy()
        camera_quad = np.array(
            [[118, 52], [530, 66], [566, 318], [88, 310]], dtype=np.float32
        )
        screen_quad = np.array(
            [[0, 0], [1023, 0], [1023, 767], [0, 767]], dtype=np.float32
        )
        camera_to_screen = cv2.getPerspectiveTransform(camera_quad, screen_quad)
        screen_to_camera = np.linalg.inv(camera_to_screen)
        inset = max(38, int(min(screen_size) * 0.055))
        left, right = inset, screen_size[0] - 1 - inset
        top, bottom = inset, screen_size[1] - 1 - inset
        x1 = int(round(left + (right - left) / 3.0))
        x2 = int(round(left + 2.0 * (right - left) / 3.0))
        y1 = int(round(top + (bottom - top) / 3.0))
        y2 = int(round(top + 2.0 * (bottom - top) / 3.0))
        expected = np.asarray(
            [
                (left, top), (x1, top), (x2, top), (right, top),
                (right, y1), (right, y2),
                (right, bottom), (x2, bottom), (x1, bottom), (left, bottom),
                (left, y2), (left, y1),
            ],
            dtype=np.float32,
        )
        camera_markers = cv2.perspectiveTransform(
            expected.reshape(-1, 1, 2), screen_to_camera
        ).reshape(-1, 2)
        for point in camera_markers.astype(int):
            cv2.circle(markers, tuple(point), 7, (245, 245, 245), thickness=-1)
        initial = camera_to_screen.copy()
        initial[0, 2] += 9.0
        initial[1, 2] -= 7.0

        result = refine_homography_from_precision_markers(
            [dark] * 8,
            [markers] * 8,
            initial,
            [tuple(point) for point in expected.astype(int)],
        )

        self.assertGreaterEqual(len(result.errors), 10)
        self.assertLess(result.mean_error, 3.0)
        self.assertLess(result.max_error, 6.0)

    def test_startup_color_test_detects_normal_and_red_filtered_camera(self) -> None:
        screen_size = (1024, 768)
        homography = np.eye(3, dtype=np.float32)
        dark = np.full((768, 1024, 3), 18, dtype=np.uint8)
        normal = dark.copy()
        palette = {
            "white": (238, 238, 238), "red": (190, 20, 15),
            "green": (10, 190, 45), "blue": (10, 70, 220),
            "cyan": (0, 200, 220), "gray": (112, 112, 112),
        }
        for name, rect in startup_color_rects(screen_size):
            x, y, width, height = rect
            cv2.rectangle(normal, (x, y), (x + width, y + height), palette[name], -1)

        normal_result = analyze_startup_color_response(
            [dark] * 8, [normal] * 8, homography, screen_size
        )
        filtered = normal.copy().astype(np.float32)
        filtered[:, :, 0] = np.clip(filtered[:, :, 0] * 1.7 + 25, 0, 255)
        filtered[:, :, 1] *= 0.35
        filtered[:, :, 2] *= 0.35
        filtered_result = analyze_startup_color_response(
            [dark] * 8, [filtered.astype(np.uint8)] * 8, homography, screen_size
        )

        self.assertEqual(normal_result.active_filter_profile, "normal")
        self.assertEqual(filtered_result.active_filter_profile, "red_filter")
        self.assertGreater(normal_result.laser_headroom, 10.0)


if __name__ == "__main__":
    unittest.main()
