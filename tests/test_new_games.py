from __future__ import annotations

import os
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import numpy as np
import cv2

from laser_arcade.apps.clay_shooting import Clay, ClayShootingApp
from laser_arcade.apps.arcade_common import (
    build_theme_background,
    build_vintage_enamel_panel,
    distance,
    draw_ambient_background,
    draw_ambient_foreground,
    draw_translucent_panel,
    draw_button,
    nearest_laser_button,
)
from laser_arcade.apps.reaction import ReactionApp
from laser_arcade.apps.target_range import TargetRangeApp
from laser_arcade.apps.timed_shooting import TimedShootingApp
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class NewGamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.screen = pygame.Surface((1024, 768))

    def test_each_game_has_a_distinct_laser_neutral_background_world(self) -> None:
        fingerprints = set()
        for theme in ("menu", "clay", "timed", "reaction", "range", "moorhuhn"):
            background = build_theme_background((1024, 768), theme)
            rgb = pygame.surfarray.array3d(background).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()), theme)
            fingerprints.add(hash(rgb.tobytes()))
        self.assertEqual(len(fingerprints), 6)

    def test_translucent_panels_preserve_the_background(self) -> None:
        surface = pygame.Surface((80, 60))
        background = (80, 120, 160)
        panel_color = (0, 20, 40)
        surface.fill(background)

        draw_translucent_panel(
            surface,
            pygame.Rect(10, 10, 60, 40),
            panel_color,
            alpha=128,
            border_radius=0,
        )

        pixel = tuple(surface.get_at((40, 30))[:3])
        self.assertNotEqual(pixel, background)
        self.assertNotEqual(pixel, panel_color)
        for channel, low, high in zip(pixel, panel_color, background):
            self.assertGreater(channel, low)
            self.assertLess(channel, high)

    def test_vintage_enamel_panels_are_varied_cached_and_laser_neutral(self) -> None:
        variants = [build_vintage_enamel_panel((294, 210), index) for index in range(6)]
        self.assertIs(variants[0], build_vintage_enamel_panel((294, 210), 0))
        fingerprints = set()
        for panel in variants:
            rgb = pygame.surfarray.array3d(panel).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))
            fingerprints.add(hash(rgb.tobytes()))
        self.assertEqual(len(fingerprints), 6)

    def test_shared_game_button_has_a_textured_laser_neutral_enamel_surface(self) -> None:
        rect = pygame.Rect(10, 10, 220, 58)
        self.screen.fill((0, 8, 16))
        draw_button(self.screen, rect, "MENÜ", pygame.font.SysFont("Arial", 22), (0, 205, 245))
        rgb = pygame.surfarray.array3d(self.screen.subsurface(rect)).astype(np.int16)
        self.assertGreater(int(rgb[:, :, 1].std()), 16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_every_game_theme_has_laser_neutral_background_and_foreground_motion(self) -> None:
        themes = (
            "cans", "clay", "timed", "reaction", "range",
            "balloons", "aliens", "stars", "math", "colors", "treasure",
            "moorhuhn", "moorhuhn_game", "ocean", "water", "tobia",
        )
        for theme in themes:
            with self.subTest(theme=theme):
                self.screen.fill((0, 8, 16))
                draw_ambient_background(self.screen, theme, 100.0)
                draw_ambient_foreground(self.screen, theme, 100.0)
                first = pygame.surfarray.array3d(self.screen).copy()

                self.screen.fill((0, 8, 16))
                draw_ambient_background(self.screen, theme, 101.4)
                draw_ambient_foreground(self.screen, theme, 101.4)
                second = pygame.surfarray.array3d(self.screen).copy()

                changed = np.any(first != second, axis=2)
                self.assertGreater(int(changed.sum()), 18, theme)
                # Die Welten sollen leben, die ruhige Bewegung darf aber weder
                # Ziele noch Hinweise optisch dominieren.
                self.assertLess(int(changed.sum()), int(changed.size * 0.06), theme)
                rgb = second.astype(np.int16)
                red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
                self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()), theme)

                detector = PulseShotDetector(DetectionConfig())
                frame_one = cv2.cvtColor(np.transpose(first, (1, 0, 2)), cv2.COLOR_RGB2BGR)
                frame_two = cv2.cvtColor(np.transpose(second, (1, 0, 2)), cv2.COLOR_RGB2BGR)
                detector.process(frame_one, 0.0)
                detection = detector.process(frame_two, 34.0)
                self.assertFalse(detection.shot, theme)

    def test_ocean_ambient_effects_do_not_draw_horizontal_arcs_or_lines(self) -> None:
        """Annas Meeresmission darf keine künstlichen Striche über dem Foto haben."""

        with mock.patch("pygame.draw.arc") as draw_arc, mock.patch("pygame.draw.line") as draw_line:
            draw_ambient_background(self.screen, "ocean", 100.0)
            draw_ambient_foreground(self.screen, "ocean", 100.0)

        draw_arc.assert_not_called()
        draw_line.assert_not_called()

    def test_clay_game_launches_breaks_and_finishes(self) -> None:
        game = ClayShootingApp(self.screen, audio_enabled=False, random_seed=1)
        game.start(100.0)
        self.assertEqual(game.handle_shot(game.start_card.center, 100.0), "handled")
        game.update(104.0)
        game.update(104.1)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.launched, 1)
        clay = game.clays[0]
        self.assertEqual(game.handle_shot((round(clay.x), round(clay.y)), 104.2), "hit")
        self.assertFalse(clay.alive)
        self.assertEqual(game.hits, 1)
        self.assertGreater(len(game.shards), 0)

        game.launched = game.TOTAL_CLAYS
        for target in game.clays:
            target.alive = False
        game.update(105.0)
        self.assertEqual(game.state, "game_over")
        game.draw(105.0)
        self.assertEqual(game.handle_shot(game.result_menu_button.center, 105.1), "menu")

    def test_clay_target_size_changes_before_and_during_round(self) -> None:
        game = ClayShootingApp(self.screen, audio_enabled=False, random_seed=1)
        game.start(100.0)
        self.assertEqual(game.target_scale, 1.2)
        self.assertEqual(
            game.handle_shot(game.ready_size_plus_button.center, 100.1),
            "setting",
        )
        self.assertEqual(game.target_scale, 1.4)

        game.begin_countdown(100.2)
        game.update(104.0)
        game.update(104.1)
        clay = game.clays[0]
        radius_before = clay.radius
        shots_before = game.shots
        self.assertEqual(
            game.handle_shot(game.size_plus_button.center, 104.2),
            "setting",
        )
        self.assertEqual(game.target_scale, 1.6)
        self.assertGreater(clay.radius, radius_before)
        self.assertEqual(game.shots, shots_before)

    def test_clay_menu_button_is_large_dark_and_has_extra_laser_margin(self) -> None:
        game = ClayShootingApp(self.screen, audio_enabled=False)
        self.assertGreaterEqual(game.menu_button.width, 166)
        self.assertGreaterEqual(game.menu_button.height, 54)

        game.state = "playing"
        game.draw(100.0)
        center = tuple(self.screen.get_at(game.menu_button.center)[:3])
        quiet_area = tuple(
            self.screen.get_at((game.menu_button.left + 24, game.menu_button.top + 12))[:3]
        )
        self.assertLess(max(center), 165)
        self.assertLess(max(quiet_area), 70)

        # Auch ein leicht versetzter Laserpunkt oberhalb/links der sichtbaren
        # Taste muss noch eindeutig als Menüwunsch gelten.
        outside = (game.menu_button.centerx, game.menu_button.top - 42)
        self.assertFalse(game.menu_button.collidepoint(outside))
        self.assertEqual(game.handle_shot(outside, 100.1), "menu")

    def test_clay_hit_zone_compensates_camera_and_projector_latency(self) -> None:
        game = ClayShootingApp(self.screen, audio_enabled=False)
        game.state = "playing"
        game.hit_tolerance = 20
        target = Clay(
            x=560.0,
            y=360.0,
            velocity_x=300.0,
            velocity_y=-40.0,
            radius=38,
            born_at=99.0,
        )
        game.clays = [target]

        previously_visible = (
            round(target.x - target.velocity_x * game.MOTION_COMPENSATION_SECONDS),
            round(
                target.y
                - target.velocity_y * game.MOTION_COMPENSATION_SECONDS
                + 0.5
                * game.GRAVITY
                * game.MOTION_COMPENSATION_SECONDS
                * game.MOTION_COMPENSATION_SECONDS
            ),
        )
        self.assertGreater(
            distance((target.x, target.y), previously_visible),
            target.radius + game.hit_tolerance,
        )
        self.assertEqual(game.handle_shot(previously_visible, 100.0), "hit")

    def test_clay_size_label_never_overlaps_plus_or_minus(self) -> None:
        game = ClayShootingApp(self.screen, audio_enabled=False)
        for minus_button, plus_button in (
            (game.ready_size_minus_button, game.ready_size_plus_button),
            (game.size_minus_button, game.size_plus_button),
        ):
            available_width = plus_button.left - minus_button.right
            label_width = game.font_small.size(game.target_scale_label)[0]
            self.assertGreaterEqual(available_width, label_width + 24)
        last_instruction_center = game.start_card.top + 105 + 2 * 48
        instruction_half_height = game.font_small.get_height() // 2
        self.assertGreaterEqual(
            game.ready_size_minus_button.top,
            last_instruction_center + instruction_half_height + 6,
        )
        self.assertLessEqual(
            game.ready_size_minus_button.bottom,
            game.start_button.top - 6,
        )

    def test_timed_game_measures_target_reaction(self) -> None:
        game = TimedShootingApp(self.screen, audio_enabled=False, random_seed=2)
        game.start(100.0)
        game.begin_countdown(100.0)
        game.update(104.0)
        game.update(104.1)
        self.assertIsNotNone(game.target)
        center = game.target.center
        self.assertEqual(game.handle_shot(center, 104.5), "hit")
        self.assertEqual(game.hits, 1)
        self.assertGreater(game.average_time_ms, 0)

        game.completed_targets = game.TOTAL_TARGETS - 1
        game.targets_shown = game.TOTAL_TARGETS - 1
        game.next_target_at = 104.5
        game.update(104.6)
        self.assertIsNotNone(game.target)
        self.assertEqual(game.handle_shot(game.target.center, 104.7), "hit")
        self.assertEqual(game.state, "game_over")
        game.draw(104.7)

    def test_reaction_game_penalizes_early_shot_and_measures_signal(self) -> None:
        game = ReactionApp(self.screen, audio_enabled=False, random_seed=3)
        game.start(100.0)
        game.begin_countdown(100.0)
        game.update(104.0)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.handle_shot(game.pad_centers[0], 104.1), "early")
        self.assertEqual(game.false_starts, 1)

        game.next_signal_at = 104.2
        game.update(104.2)
        self.assertEqual(game.phase, "active")
        center = game.pad_centers[game.active_index]
        self.assertEqual(game.handle_shot(center, 104.6), "hit")
        self.assertEqual(game.hits, 1)
        self.assertGreater(game.average_ms, 0)

        game.completed = game.ROUNDS - 1
        game.phase = "active"
        game.active_index = 0
        game.signal_started = 105.0
        game.handle_shot(game.pad_centers[0], 105.3)
        self.assertEqual(game.state, "game_over")
        game.draw(105.3)

    def test_large_result_zones_choose_nearest_reaction_button(self) -> None:
        game = ReactionApp(self.screen, audio_enabled=False, random_seed=3)
        game.state = "game_over"

        menu_side = (game.result_card.right - 35, game.result_card.top + 40)
        self.assertEqual(game.handle_shot(menu_side, 110.0), "menu")

        game.state = "game_over"
        repeat_side = (game.result_card.left + 35, game.result_card.top + 40)
        self.assertEqual(game.handle_shot(repeat_side, 111.0), "handled")
        self.assertEqual(game.state, "countdown")

    def test_laser_button_margin_is_large_and_overlap_stays_unambiguous(self) -> None:
        left = pygame.Rect(100, 100, 120, 50)
        right = pygame.Rect(240, 100, 120, 50)

        self.assertEqual(
            nearest_laser_button((235, 125), (("left", left), ("right", right))),
            "right",
        )
        self.assertEqual(
            nearest_laser_button((75, 80), (("left", left), ("right", right))),
            "left",
        )

    def test_reaction_menu_accepts_shot_well_outside_visible_button(self) -> None:
        game = ReactionApp(self.screen, audio_enabled=False)
        point = (game.menu_button.left - 45, game.menu_button.bottom + 35)
        self.assertEqual(game.handle_shot(point, 100.0), "menu")

    def test_target_range_cycles_settings_scores_and_persists_five_histories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "target_history.json"
            game = TargetRangeApp(
                self.screen,
                audio_enabled=False,
                history_path=history_path,
            )
            game.start(100.0)
            self.assertEqual(game.shot_limit, 5)
            self.assertEqual(game.handle_shot(game.shot_count_button.center, 100.0), "setting")
            self.assertEqual(game.shot_limit, 10)
            self.assertEqual(len(game.shots), 0)
            self.assertEqual(
                game.handle_shot(game.shot_count_button.center, 100.1),
                "setting_locked",
            )
            self.assertEqual(game.shot_limit, 10)
            game.handle_shot(game.shot_count_button.center, 100.8)
            self.assertEqual(game.shot_limit, 3)
            self.assertEqual(
                game.handle_shot(game.shot_count_button.center, 100.9),
                "setting_locked",
            )
            self.assertEqual(game.shot_limit, 3)
            game.handle_shot(game.shot_count_button.center, 101.6)
            self.assertEqual(game.shot_limit, 5)

            game.shot_count_index = 0
            for index in range(3):
                self.assertEqual(game.handle_shot(game.target_center, 102.0 + index), "hit")
            self.assertEqual(game.state, "result")
            self.assertEqual(game.current_result.display, "30 RINGE")
            self.assertEqual(len(game.histories["whole"]), 1)
            self.assertTrue(history_path.exists())
            game.draw(104.0)
            self.assertEqual(
                game.handle_shot(game.result_card.center, 104.5),
                "handled",
            )
            self.assertEqual(game.state, "result")

            game.update(106.9)
            self.assertEqual(game.state, "result")
            game.update(107.1)
            self.assertEqual(game.state, "playing")
            self.assertEqual(len(game.shots), 0)
            game.handle_shot(game.mode_button.center, 109.0)
            self.assertEqual(game.mode, "decimal")
            self.assertEqual(len(game.histories["decimal"]), 0)
            game.handle_shot(game.mode_button.center, 109.8)
            self.assertEqual(game.mode, "divider")
            game.handle_shot(game.mode_button.center, 110.6)
            self.assertEqual(game.mode, "whole")

            radius_before = game.target_radius
            game.handle_shot(game.size_plus_button.center, 112.0)
            self.assertGreaterEqual(game.target_radius, radius_before)
            game.draw(112.0)

            reloaded = TargetRangeApp(
                self.screen,
                audio_enabled=False,
                history_path=history_path,
            )
            self.assertEqual(len(reloaded.histories["whole"]), 1)
            self.assertEqual(reloaded.histories["whole"][0].display, "30 RINGE")

    def test_target_range_keeps_only_five_results_per_mode(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)
        game.shot_count_index = 0
        now = 100.0
        for _ in range(7):
            for _ in range(3):
                game.handle_shot(game.target_center, now)
                now += 0.1
            game.update(now + 4.1)
            now += 4.2
        self.assertEqual(len(game.histories["whole"]), 5)

    def test_target_range_lists_individual_values_with_german_decimal_comma(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)
        game.mode_index = game.MODES.index("decimal")
        game.shot_count_index = 1

        game.handle_shot(game.target_center, 100.0)
        second_point = (game.target_center[0] + 30, game.target_center[1])
        game.handle_shot(second_point, 100.1)

        self.assertEqual(game.shots[0].display, "10,9")
        self.assertIn(",", game.shots[1].display)
        self.assertEqual(len(game.shots), 2)
        game.draw(100.1)

    def test_target_range_uses_original_air_rifle_ring_proportions(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)

        self.assertAlmostEqual(
            game._ring_radius(4) / game.target_radius,
            30.5 / 45.5,
            places=6,
        )
        self.assertAlmostEqual(
            game._ring_radius(10) / game.target_radius,
            0.5 / 45.5,
            places=6,
        )
        self.assertLess(game._ring_radius(10), game._ring_radius(9))
        self.assertGreater(game._scoring_ring_radius(10), game._ring_radius(10))
        self.assertLess(game._scoring_ring_radius(10), game._ring_radius(9))
        self.assertGreaterEqual(game._perfect_ten_radius(), 5.0)

    def test_target_range_whole_scores_follow_original_ring_boundaries(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)

        self.assertEqual(game._whole_ring_value(0.0), 10)
        practical_ten = game._scoring_ring_radius(10)
        self.assertEqual(game._whole_ring_value(practical_ten), 10)
        self.assertEqual(game._whole_ring_value(practical_ten + 0.01), 9)
        self.assertEqual(game._whole_ring_value(game._ring_radius(4)), 4)
        self.assertEqual(game._whole_ring_value(game._ring_radius(1) + 0.01), 0)

    def test_target_range_decimal_and_divider_use_physical_scale(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)
        game.mode_index = game.MODES.index("decimal")
        self.assertEqual(game._decimal_ring_value(0.0), 10.9)
        self.assertEqual(game._decimal_ring_value(game._perfect_ten_radius()), 10.9)
        self.assertEqual(game._decimal_ring_value(game._scoring_ring_radius(10)), 10.0)
        self.assertEqual(game._decimal_ring_value(game._ring_radius(9)), 9.0)

        game.mode_index = game.MODES.index("divider")
        shot = game._score_shot(
            (game.target_center[0] + game.target_radius, game.target_center[1]),
            float(game.target_radius),
        )
        self.assertEqual(shot.value, 2275)
        self.assertEqual(shot.display, "2275 T")

    def test_target_range_draws_colored_round_target_with_dark_mirror(self) -> None:
        game = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)
        background = (9, 11, 13)
        self.screen.fill(background)
        game._draw_target()

        center = game.target_center
        center_pixel = tuple(self.screen.get_at(center)[:3])
        black_distance = (game._ring_radius(4) + game._ring_radius(5)) / 2.0
        black_offset = round(black_distance / math.sqrt(2.0))
        black_pixel = tuple(
            self.screen.get_at(
                (center[0] + black_offset, center[1] + black_offset)
            )[:3]
        )
        outer_distance = (game._ring_radius(2) + game._ring_radius(3)) / 2.0
        outer_offset = round(outer_distance / math.sqrt(2.0))
        outer_pixel = tuple(
            self.screen.get_at(
                (center[0] + outer_offset, center[1] + outer_offset)
            )[:3]
        )

        self.assertEqual(center_pixel, game.RIFLE_TEN_FILL)
        self.assertEqual(black_pixel, game.RIFLE_INK)
        self.assertEqual(outer_pixel, game.RIFLE_PAPER)
        former_card_corner = (
            center[0] + game.target_radius + 3,
            center[1] + game.target_radius + 3,
        )
        self.assertEqual(
            tuple(self.screen.get_at(former_card_corner)[:3]),
            background,
        )

    def test_all_new_game_screens_are_laser_neutral(self) -> None:
        clay = ClayShootingApp(self.screen, audio_enabled=False)
        clay.begin_countdown(100.0)
        clay.update(104.0)
        clay.update(104.1)
        timed = TimedShootingApp(self.screen, audio_enabled=False)
        timed.begin_countdown(100.0)
        timed.update(104.0)
        timed.update(104.1)
        reaction = ReactionApp(self.screen, audio_enabled=False)
        reaction.begin_countdown(100.0)
        reaction.update(104.0)
        reaction.next_signal_at = 104.1
        reaction.update(104.1)
        target = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)

        for game in (clay, timed, reaction, target):
            game.draw(104.1)
            rgb = pygame.surfarray.array3d(self.screen).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(
                bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()),
                game.name,
            )

    def test_red_laser_is_detected_on_center_and_edge_of_arcade_targets(self) -> None:
        scenes = []

        clay = ClayShootingApp(self.screen, audio_enabled=False)
        clay.state = "playing"
        clay.clays = [Clay(510.0, 390.0, 0.0, 0.0, 42, 100.0)]
        clay.draw(100.0)
        scenes.append((pygame.surfarray.array3d(self.screen).copy(), ((510, 390), (550, 390)), "Tontaube"))

        timed = TimedShootingApp(self.screen, audio_enabled=False, random_seed=2)
        timed.state = "playing"
        timed._spawn_target(100.0)
        timed.draw(100.0)
        center = timed.target.center
        scenes.append((pygame.surfarray.array3d(self.screen).copy(), (center, (center[0] + timed.target.radius - 2, center[1])), "Zeitziel"))

        reaction = ReactionApp(self.screen, audio_enabled=False, random_seed=3)
        reaction.state = "playing"
        reaction.phase = "active"
        reaction.active_index = 0
        reaction.draw(100.0)
        center = reaction.pad_centers[0]
        scenes.append((pygame.surfarray.array3d(self.screen).copy(), (center, (center[0] + reaction.pad_radius - 2, center[1])), "Reaktionsziel"))

        target = TargetRangeApp(self.screen, audio_enabled=False, history_path=None)
        target.draw(100.0)
        center = target.target_center
        scenes.append(
            (
                pygame.surfarray.array3d(self.screen).copy(),
                (
                    center,
                    (center[0] + round(target._ring_radius(6)), center[1] + 7),
                    (center[0] + round(target._ring_radius(2)), center[1] + 7),
                ),
                "Luftgewehrscheibe",
            )
        )

        for rgb, points, label in scenes:
            background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
            for point in points:
                with self.subTest(game=label, point=point):
                    detector = PulseShotDetector(DetectionConfig())
                    detector.process(background, 0.0)
                    detector.process(background, 30.0)
                    pulse = background.copy()
                    mask = np.zeros(background.shape[:2], dtype=np.uint8)
                    cv2.circle(mask, point, 3, 255, thickness=-1)
                    pulse[:, :, 2][mask > 0] = 245
                    result = detector.process(pulse, 70.0)
                    self.assertTrue(result.shot)


if __name__ == "__main__":
    unittest.main()
