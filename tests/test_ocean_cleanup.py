from __future__ import annotations

import hashlib
import math
import os
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np
import pygame
import cv2

from laser_arcade.apps.ocean_cleanup import OceanCleanupApp
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class OceanCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.screen = pygame.Surface((1024, 768))

    def _game(self) -> OceanCleanupApp:
        return OceanCleanupApp(self.screen, audio_enabled=False, random_seed=19)

    def _playing_game(self) -> OceanCleanupApp:
        game = self._game()
        game.state = "playing"
        game.state_started = 100.0
        game.last_update = 100.0
        game.deadline = 160.0
        game.targets.clear()
        return game

    def test_cat_can_asset_is_the_unchanged_supplied_original(self) -> None:
        game = self._game()
        self.assertEqual(game.name, "Annas Meeresmission")
        self.assertFalse(game.leaderboard_enabled)
        path = Path(__file__).resolve().parents[1] / "assets" / "ocean_cleanup" / "cat_can_original.png"
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        self.assertEqual(
            digest,
            "4EBC56ECBA98701C996AB31BA738B45EF1660F14EA173726E66636F2F72275EC",
        )

    def test_cat_can_scores_ten_and_other_trash_scores_five(self) -> None:
        game = self._playing_game()
        cat_can = game._spawn_target(101.0, "cat_can")
        cat_can.x, cat_can.y = 420.0, 360.0
        self.assertEqual(game.handle_shot(cat_can.center, 101.1), "trash")
        self.assertEqual(game.score, 10)
        self.assertEqual(game.cat_cans_collected, 1)

        bottle = game._spawn_target(102.0, "bottle")
        bottle.x, bottle.y = 620.0, 410.0
        self.assertEqual(game.handle_shot(bottle.center, 102.1), "trash")
        self.assertEqual(game.score, 15)
        self.assertEqual(game.trash_collected, 2)

    def test_animals_are_protected_and_cost_ten_points_when_hit(self) -> None:
        game_kinds = OceanCleanupApp.ANIMAL_KINDS
        for kind in game_kinds:
            with self.subTest(kind=kind):
                game = self._playing_game()
                game.score = 25
                animal = game._spawn_target(101.0, kind)
                animal.x, animal.y = 512.0, 390.0
                self.assertEqual(game.handle_shot(animal.center, 101.1), "animal")
                self.assertEqual(game.score, 15)
                self.assertEqual(game.animal_hits, 1)
                self.assertIn(animal, game.targets)
        self.assertEqual(game_kinds, ("dolphin", "fish", "turtle"))

    def test_every_target_moves_through_visible_underwater_lanes(self) -> None:
        game = self._playing_game()
        for kind in (*game.TRASH_KINDS, *game.ANIMAL_KINDS):
            target = game._spawn_target(101.0, kind)
            self.assertGreaterEqual(target.y, 190)
            self.assertLessEqual(target.y, 663)
            self.assertNotEqual(target.velocity_x, 0.0)

    def test_every_visible_target_edge_is_inside_generous_hit_area(self) -> None:
        for kind in (*OceanCleanupApp.TRASH_KINDS, *OceanCleanupApp.ANIMAL_KINDS):
            with self.subTest(kind=kind):
                game = self._playing_game()
                target = game._spawn_target(101.0, kind)
                target.x, target.y = 512.0, 390.0
                width, height = game._target_visual_size(target)
                visible_edge = (
                    round(target.x + width * 0.46),
                    round(target.y + height * 0.42),
                )
                expected = "animal" if target.animal else "trash"
                self.assertEqual(game.handle_shot(visible_edge, 101.1), expected)

    def test_laser_afterglow_on_collected_trash_is_not_counted_as_miss(self) -> None:
        game = self._playing_game()
        target = game._spawn_target(101.0, "bottle")
        target.x, target.y = 512.0, 390.0
        self.assertEqual(game.handle_shot(target.center, 101.1), "trash")
        self.assertEqual(game.handle_shot(target.center, 101.25), "handled")
        self.assertEqual(game.shots, 1)
        self.assertEqual(game.trash_collected, 1)

    def test_animal_nose_and_angle_follow_actual_swimming_direction(self) -> None:
        game = self._playing_game()
        dolphin = game._spawn_target(101.0, "dolphin")
        dolphin.phase = math.pi / 2
        dolphin.velocity_y = 12.0

        dolphin.velocity_x = 70.0
        right, right_angle = game._animal_swim_pose(dolphin, 0.0)
        dolphin.velocity_x = -70.0
        left, left_angle = game._animal_swim_pose(dolphin, 0.0)

        self.assertTrue(right)
        self.assertFalse(left)
        self.assertLess(right_angle, 0.0)
        self.assertGreater(left_angle, 0.0)
        self.assertAlmostEqual(abs(right_angle), abs(left_angle), places=5)

    def test_realistic_animal_assets_are_trimmed_and_transparent(self) -> None:
        game = self._game()
        for sprite in (game.dolphin, game.fish, game.turtle):
            with self.subTest(size=sprite.get_size()):
                self.assertGreater(sprite.get_width(), sprite.get_height())
                self.assertEqual(sprite.get_at((0, 0)).a, 0)
                self.assertGreater(sprite.get_bounding_rect(min_alpha=8).width, 10)

    def test_complete_flow_and_result_buttons_are_pistol_operable(self) -> None:
        game = self._game()
        self.assertEqual(game.handle_shot(game.start_card.center, 100.0), "handled")
        self.assertEqual(game.state, "countdown")
        game.update(104.0)
        self.assertEqual(game.state, "playing")
        game.update(game.deadline + 0.1)
        self.assertEqual(game.state, "game_over")
        self.assertEqual(game.finish_reason, "DIE MISSION IST BEENDET")
        self.assertEqual(game.leaderboard_metrics[0], ("PUNKTE", "0"))
        self.assertIn("MÜLL", game.leaderboard_detail)

        broad_repeat = (game.repeat_button.left - 80, game.repeat_button.top - 65)
        self.assertEqual(game.handle_shot(broad_repeat, game.deadline + 1.0), "handled")
        self.assertEqual(game.state, "countdown")

    def test_all_screens_and_targets_remain_red_laser_neutral(self) -> None:
        game = self._playing_game()
        for kind in (*game.TRASH_KINDS, *game.ANIMAL_KINDS):
            target = game._spawn_target(101.0, kind)
            target.x = 110.0 + len(game.targets) * 108
            target.y = 390.0
        game.draw(101.0)
        rgb = pygame.surfarray.array3d(self.screen).astype(np.int16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_red_laser_is_detected_at_center_of_every_target(self) -> None:
        for kind in (*OceanCleanupApp.TRASH_KINDS, *OceanCleanupApp.ANIMAL_KINDS):
            with self.subTest(kind=kind):
                game = self._playing_game()
                target = game._spawn_target(101.0, kind)
                target.x, target.y = 512.0, 390.0
                game.draw(101.0)
                rgb = np.transpose(pygame.surfarray.array3d(self.screen), (1, 0, 2))
                background = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                width, height = game._target_visual_size(target)
                points = (
                    target.center,
                    (round(target.x - width * 0.30), round(target.y)),
                    (round(target.x + width * 0.30), round(target.y)),
                    (round(target.x), round(target.y + height * 0.24)),
                )
                for point in points:
                    detector = PulseShotDetector(DetectionConfig())
                    detector.process(background, 0.0)
                    detector.process(background, 30.0)
                    pulse = background.copy()
                    cv2.circle(pulse, point, 3, (12, 20, 245), thickness=-1)

                    result = detector.process(pulse, 70.0)

                    self.assertTrue(result.shot, f"{kind} bei {point}")
                    self.assertLessEqual(abs(result.point[0] - point[0]), 2)
                    self.assertLessEqual(abs(result.point[1] - point[1]), 2)


if __name__ == "__main__":
    unittest.main()
