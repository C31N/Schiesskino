from __future__ import annotations

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import numpy as np
import cv2

from laser_arcade.apps.cans import Can, CansApp
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class CansGameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.font.init()

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    @staticmethod
    def _new_game() -> CansApp:
        return CansApp(pygame.Surface((1024, 768)), audio_enabled=False)

    @staticmethod
    def _enter_first_wave(game: CansApp) -> float:
        game.start(0.0)
        game.handle_shot(game.start_card.center, 0.1)
        now = 0.1 + game.COUNTDOWN_DURATION + 0.01
        game.update(now)
        return now

    def test_start_card_begins_countdown_and_builds_first_wave(self) -> None:
        game = self._new_game()
        game.start(0.0)

        self.assertEqual(game.handle_shot(game.start_card.center, 0.1), "handled")
        self.assertEqual(game.state, "countdown")

        game.update(0.1 + game.COUNTDOWN_DURATION + 0.01)
        self.assertEqual(game.state, "playing")
        self.assertEqual(len(game.cans), 6)
        self.assertTrue(all(can.alive for can in game.cans))

    def test_can_size_changes_before_and_during_round_without_reset(self) -> None:
        game = self._new_game()
        game.start(0.0)
        self.assertEqual(game.target_scale, 1.0)
        self.assertEqual(
            game.handle_shot(game.ready_size_plus_button.center, 0.05),
            "setting",
        )
        self.assertEqual(game.target_scale, 1.2)

        game.handle_shot(game.start_card.center, 0.1)
        now = 0.1 + game.COUNTDOWN_DURATION + 0.01
        game.update(now)
        first = game.cans[0]
        width_before = first.rect.width
        first.alive = False
        shots_before = game.shots

        self.assertEqual(
            game.handle_shot(game.size_plus_button.center, now + 0.1),
            "setting",
        )
        self.assertEqual(game.target_scale, 1.4)
        self.assertGreater(game.cans[0].rect.width, width_before)
        self.assertFalse(game.cans[0].alive)
        self.assertEqual(game.shots, shots_before)

    def test_size_and_level_labels_never_overlap_step_buttons(self) -> None:
        game = self._new_game()
        controls = (
            (
                game.ready_size_minus_button,
                game.ready_size_plus_button,
                game.target_scale_label,
            ),
            (
                game.ready_level_minus_button,
                game.ready_level_plus_button,
                game.level_count_label,
            ),
            (game.size_minus_button, game.size_plus_button, game.target_scale_label),
        )
        for minus_button, plus_button, label in controls:
            available_width = plus_button.left - minus_button.right
            label_width = game.font_small.size(label)[0]
            self.assertGreaterEqual(available_width, label_width + 24)
        last_instruction_center = game.start_card.top + 105 + 2 * 35
        instruction_half_height = game.font_small.get_height() // 2
        self.assertGreaterEqual(
            game.ready_size_minus_button.top,
            last_instruction_center + instruction_half_height + 6,
        )
        self.assertLessEqual(
            game.ready_size_minus_button.bottom,
            game.ready_level_minus_button.top - 6,
        )
        self.assertLessEqual(
            game.ready_level_minus_button.bottom,
            game.start_button.top - 6,
        )

    def test_level_count_changes_from_one_to_five_in_ready_menu(self) -> None:
        game = self._new_game()
        game.start(0.0)

        self.assertEqual((game.level_count, game.game_duration, game.total_cans), (3, 60.0, 31))
        self.assertEqual(
            game.handle_shot(game.ready_level_plus_button.center, 0.05),
            "setting",
        )
        self.assertEqual((game.level_count, game.game_duration, game.total_cans), (4, 80.0, 52))
        self.assertEqual(
            game.handle_shot(game.ready_level_plus_button.center, 0.10),
            "setting",
        )
        self.assertEqual((game.level_count, game.game_duration, game.total_cans), (5, 100.0, 80))

        for index in range(8):
            game.handle_shot(game.ready_level_minus_button.center, 0.2 + index * 0.01)
        self.assertEqual((game.level_count, game.game_duration, game.total_cans), (1, 20.0, 6))

    def test_single_selected_level_finishes_after_first_pyramid(self) -> None:
        game = self._new_game()
        game.start(0.0)
        game.handle_shot(game.ready_level_minus_button.center, 0.02)
        game.handle_shot(game.ready_level_minus_button.center, 0.04)
        game.handle_shot(game.start_card.center, 0.1)
        now = 0.1 + game.COUNTDOWN_DURATION + 0.01
        game.update(now)

        self.assertEqual(game.level_count, 1)
        self.assertEqual(len(game.cans), 6)
        for can in sorted(game.cans, key=lambda item: item.row, reverse=True):
            now += 0.01
            self.assertEqual(game.handle_shot(can.rect.center, now), "hit")

        self.assertEqual(game.state, "wave_clear")
        game.update(now + game.WAVE_CLEAR_DURATION + 0.01)
        self.assertEqual(game.state, "game_over")
        self.assertEqual(game.finish_reason, "Alle Dosen getroffen")
        self.assertEqual(game.total_cans, 6)

    def test_fourth_and_fifth_level_fit_at_largest_target_size(self) -> None:
        game = self._new_game()
        game.target_scale_index = len(game.TARGET_SCALES) - 1

        for wave in (4, 5):
            cans = game._wave_cans(wave)
            self.assertEqual(len(cans), sum(game.WAVE_ROWS[wave - 1]))
            self.assertTrue(all(can.rect.left >= 24 for can in cans))
            self.assertTrue(all(can.rect.right <= game.screen.get_width() - 24 for can in cans))
            self.assertTrue(all(can.rect.top >= 152 for can in cans))
            self.assertTrue(all(can.rect.bottom <= game.screen.get_height() - 100 for can in cans))

    def test_hit_and_miss_update_score_combo_and_accuracy(self) -> None:
        game = self._new_game()
        now = self._enter_first_wave(game)

        self.assertEqual(game.handle_shot(game.cans[0].rect.center, now + 0.1), "hit")
        self.assertEqual((game.shots, game.hits, game.combo), (1, 1, 1))
        self.assertGreaterEqual(game.score, 100)

        self.assertEqual(game.handle_shot((20, 300), now + 0.2), "miss")
        self.assertEqual((game.shots, game.hits, game.combo), (2, 1, 0))
        self.assertEqual(game.accuracy, 50.0)

    def test_light_edge_hit_counts_and_can_falls_immediately(self) -> None:
        game = self._new_game()
        now = self._enter_first_wave(game)
        target = game.cans[0]
        grazing_shot = (
            target.rect.left - game._hit_margin(target) + 2,
            target.rect.centery,
        )

        self.assertEqual(game.handle_shot(grazing_shot, now + 0.1), "hit")
        self.assertFalse(target.alive)
        self.assertGreater(target.velocity_y, 0.0)

    def test_falling_can_awards_bonus_and_changes_direction_by_hit_side(self) -> None:
        game = self._new_game()
        game.state = "wave_clear"
        target = Can(
            pygame.Rect(440, 250, 64, 90),
            alive=False,
            hit_at=10.0,
            velocity_x=-120.0,
            velocity_y=55.0,
            spin=-240.0,
            fall_x=472.0,
            fall_y=295.0,
        )
        game.cans = [target]

        first_time = 10.30
        center_x, center_y, _, _ = game._falling_state(target, first_time)
        left_hit = (round(center_x - target.rect.width * 0.38), round(center_y))
        self.assertEqual(game.handle_shot(left_hit, first_time), "air_hit")
        self.assertGreater(target.velocity_x, 0.0)
        self.assertGreater(target.spin, 0.0)
        self.assertGreater(target.velocity_y, 0.0)
        self.assertEqual(target.air_hits, 1)
        self.assertEqual(game.knocked_down, 0)
        self.assertEqual((game.hits, game.shots, game.combo), (1, 1, 1))
        self.assertEqual(game.score, game.AIR_HIT_BASE_POINTS)
        self.assertTrue(
            any("FLUGTREFFER" in item.text for item in game.floating_scores)
        )

        second_time = first_time + game.AIR_HIT_COOLDOWN + 0.05
        center_x, center_y, _, _ = game._falling_state(target, second_time)
        right_hit = (round(center_x + target.rect.width * 0.38), round(center_y))
        self.assertEqual(game.handle_shot(right_hit, second_time), "air_hit")
        self.assertLess(target.velocity_x, 0.0)
        self.assertLess(target.spin, 0.0)
        self.assertEqual(target.air_hits, 2)
        self.assertEqual(game.knocked_down, 0)
        self.assertEqual((game.hits, game.shots, game.combo), (2, 2, 2))
        self.assertEqual(
            game.score,
            game.AIR_HIT_BASE_POINTS + game.AIR_HIT_BASE_POINTS + 100,
        )

    def test_same_laser_pulse_cannot_score_falling_can_twice(self) -> None:
        game = self._new_game()
        game.state = "playing"
        target = Can(
            pygame.Rect(440, 250, 64, 90),
            alive=False,
            hit_at=10.0,
            velocity_y=55.0,
            fall_x=472.0,
            fall_y=295.0,
        )
        game.cans = [target]
        now = 10.30
        center = tuple(round(value) for value in game._falling_state(target, now)[:2])

        self.assertEqual(game.handle_shot(center, now), "air_hit")
        score = game.score
        self.assertEqual(game.handle_shot(center, now + 0.05), "handled")
        self.assertEqual(game.score, score)
        self.assertEqual(target.air_hits, 1)
        self.assertEqual((game.hits, game.shots, game.combo), (1, 1, 1))

    def test_can_rows_rest_on_each_other_without_floating_gap(self) -> None:
        game = self._new_game()
        self._enter_first_wave(game)
        rows = sorted({can.rect.y for can in game.cans}, reverse=True)

        self.assertEqual(len(rows), 3)
        can_height = game.cans[0].rect.height
        for lower_y, upper_y in zip(rows, rows[1:]):
            overlap = upper_y + can_height - lower_y
            self.assertGreaterEqual(overlap, 5)
            self.assertLessEqual(overlap, 10)

    def test_hit_zone_follows_every_configured_can_size(self) -> None:
        game = self._new_game()
        for scale_index, scale in enumerate(game.TARGET_SCALES):
            game.target_scale_index = scale_index
            cans = game._wave_cans(3)
            target = cans[0]
            margin = game._hit_margin(target)
            self.assertGreaterEqual(margin, game.hit_tolerance)
            self.assertTrue(
                target.rect.inflate(margin * 2, margin * 2).collidepoint(
                    (target.rect.right + margin - 1, target.rect.centery)
                ),
                msg=f"Fangbereich fehlt bei {scale=}",
            )

        game.target_scale_index = 0
        small = game._wave_cans(1)[0]
        game.target_scale_index = len(game.TARGET_SCALES) - 1
        large = game._wave_cans(1)[0]
        self.assertGreater(game._hit_margin(large), game._hit_margin(small))

    def test_all_31_cans_complete_three_waves_and_show_result(self) -> None:
        game = self._new_game()
        now = self._enter_first_wave(game)

        for expected_wave, expected_count in enumerate((6, 10, 15), start=1):
            self.assertEqual(game.wave, expected_wave)
            self.assertEqual(len(game.cans), expected_count)
            # Von oben nach unten treffen, damit keine Dose durch fehlende
            # Stützen automatisch kippt und alle 31 Direkttreffer messbar sind.
            for can in sorted(game.cans, key=lambda item: item.row, reverse=True):
                now += 0.01
                self.assertEqual(game.handle_shot(can.rect.center, now), "hit")
            self.assertEqual(game.state, "wave_clear")
            now += game.WAVE_CLEAR_DURATION + 0.01
            game.update(now)

        self.assertEqual(game.state, "game_over")
        self.assertEqual(game.finish_reason, "Alle Dosen getroffen")
        self.assertEqual((game.hits, game.shots), (31, 31))
        self.assertEqual(game.accuracy, 100.0)
        self.assertGreater(game.score, 31 * 100)

        game.draw(now)
        self.assertEqual(game.handle_shot(game.result_card.center, now + 0.1), "handled")
        self.assertEqual(game.state, "countdown")

    def test_result_overview_has_large_distinct_repeat_and_menu_zones(self) -> None:
        game = self._new_game()
        game.state = "game_over"
        game.finish_reason = "Die Zeit ist abgelaufen"

        menu_side = (game.result_card.right - 30, game.result_card.top + 35)
        self.assertEqual(game.handle_shot(menu_side, 1.0), "menu")

        game.state = "game_over"
        repeat_side = (game.result_card.left + 30, game.result_card.top + 35)
        self.assertEqual(game.handle_shot(repeat_side, 1.1), "handled")
        self.assertEqual(game.state, "countdown")

    def test_unsupported_upper_cans_fall_with_their_support(self) -> None:
        game = self._new_game()
        now = self._enter_first_wave(game)
        bottom_center = next(
            can for can in game.cans if can.row == 0 and can.column == 1
        )

        self.assertEqual(game.handle_shot(bottom_center.rect.center, now + 0.1), "hit")

        fallen = [can for can in game.cans if not can.alive]
        self.assertGreater(len(fallen), 1)
        self.assertEqual(game.knocked_down, len(fallen))
        self.assertTrue(any(can.row > 0 for can in fallen))
        self.assertTrue(all(can.hit_at == now + 0.1 for can in fallen))

    def test_menu_button_is_available_in_every_state(self) -> None:
        game = self._new_game()
        for state in ("ready", "countdown", "playing", "wave_clear", "game_over"):
            game.state = state
            self.assertEqual(game.handle_shot(game.menu_button.center, 1.0), "menu")

    def test_all_states_render_without_error(self) -> None:
        game = self._new_game()
        now = self._enter_first_wave(game)
        for state in ("ready", "countdown", "playing", "wave_clear", "game_over"):
            game.state = state
            game.finish_reason = "Alle Dosen getroffen"
            game.draw(now)

    def test_new_can_design_is_detailed_cached_and_laser_safe(self) -> None:
        game = self._new_game()
        surface = game._can_surface((66, 92))
        pixels = pygame.surfarray.array3d(surface)
        visible = pygame.surfarray.array_alpha(surface) > 0

        self.assertIs(surface, game._can_surface((66, 92)))
        self.assertEqual(int(pixels[:, :, 0].max()), 0)
        self.assertGreater(len(np.unique(pixels[visible], axis=0)), 45)
        self.assertLessEqual(int(pixels[visible].max()), 151)

    def test_red_laser_is_detected_on_can_lid_body_and_bottom(self) -> None:
        game = self._new_game()
        game.state = "playing"
        game.cans = game._wave_cans(1)
        game.draw(100.0)
        can = game.cans[0]
        rgb = pygame.surfarray.array3d(game.screen).copy()
        background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
        points = (
            (can.rect.centerx, can.rect.top + 5),
            can.rect.center,
            (can.rect.centerx, can.rect.bottom - 5),
        )
        for point in points:
            with self.subTest(point=point):
                detector = PulseShotDetector(DetectionConfig())
                detector.process(background, 0.0)
                detector.process(background, 30.0)
                pulse = background.copy()
                mask = np.zeros(background.shape[:2], dtype=np.uint8)
                cv2.circle(mask, point, 3, 255, thickness=-1)
                # Deckel-/Bodenfarbe bleibt bestehen: Dies bildet den echten
                # additiven Laser auf der projizierten Dose deutlich besser ab
                # als das vollständige Überschreiben mit einem roten Pixel.
                pulse[:, :, 2][mask > 0] = 245
                result = detector.process(pulse, 70.0)
                self.assertTrue(result.shot)
                self.assertLessEqual(abs(result.point[0] - point[0]), 2)
                self.assertLessEqual(abs(result.point[1] - point[1]), 2)

    def test_can_lid_and_bottom_are_full_hit_areas(self) -> None:
        for vertical_offset in (3, -4):
            with self.subTest(vertical_offset=vertical_offset):
                game = self._new_game()
                now = self._enter_first_wave(game)
                can = game.cans[0]
                y = can.rect.top + vertical_offset if vertical_offset > 0 else can.rect.bottom + vertical_offset
                self.assertEqual(
                    game.handle_shot((can.rect.centerx, y), now + 0.1),
                    "hit",
                )


if __name__ == "__main__":
    unittest.main()
