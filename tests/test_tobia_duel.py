from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import cv2
import numpy as np
import pygame

from laser_arcade.apps.tobia_duel import TobiaDuelApp
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class TobiaDuelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.screen = pygame.Surface((1024, 768))

    def _game(self) -> TobiaDuelApp:
        return TobiaDuelApp(self.screen, audio_enabled=False, random_seed=1919)

    def _playing_game(self) -> TobiaDuelApp:
        game = self._game()
        game.state = "playing"
        game.state_started = 100.0
        game.last_update = 100.0
        game.deadline = 145.0
        game.next_target_at = 999.0
        return game

    def test_supplied_photos_are_stored_byte_for_byte(self) -> None:
        root = Path(__file__).resolve().parents[1] / "assets" / "tobia_duel"
        expected = {
            "rabbit_original.jpeg": "E7F822474D792440D31E37D11AF665697C3D3621A500FD49E1CFD595FE3353D1",
            "tobia_original.jpeg": "C25D60759CA724A74779A591C328D5588B4631C98B8C5F2D491071F760E87ECE",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((root / filename).read_bytes()).hexdigest().upper(), digest)

    def test_rabbit_scores_plus_fifty_and_person_minus_one_hundred(self) -> None:
        game = self._playing_game()
        rabbit = game._spawn_target(101.0, "rabbit")
        self.assertEqual(game.handle_shot(rabbit.rect.center, 101.1), "rabbit")
        self.assertEqual(game.score, 50)
        self.assertEqual(game.rabbit_hits, 1)

        person = game._spawn_target(102.0, "person")
        self.assertEqual(game.handle_shot(person.rect.center, 102.1), "person")
        self.assertEqual(game.score, -50)
        self.assertEqual(game.person_hits, 1)

    def test_target_uses_its_variable_lifetime(self) -> None:
        game = self._playing_game()
        target = game._spawn_target(101.0, "rabbit")
        self.assertGreaterEqual(target.lifetime, game.TARGET_LIFETIME_MIN)
        self.assertLessEqual(target.lifetime, game.TARGET_LIFETIME_MAX)
        game.update(target.spawned_at + target.lifetime - 0.001)
        self.assertIs(game.target, target)
        game.update(target.spawned_at + target.lifetime)
        self.assertIsNone(game.target)
        self.assertEqual(game.rabbit_misses, 1)

    def test_target_sizes_speeds_and_lifetimes_really_vary(self) -> None:
        game = self._playing_game()
        targets = [game._spawn_target(101.0 + index, "rabbit") for index in range(24)]
        sizes = {target.rect.width for target in targets}
        speeds = {round(target.speed) for target in targets}
        lifetimes = {round(target.lifetime, 2) for target in targets}
        self.assertGreaterEqual(len(sizes), 8)
        self.assertGreaterEqual(len(speeds), 8)
        self.assertGreaterEqual(len(lifetimes), 8)
        self.assertGreater(max(sizes) - min(sizes), 45)
        self.assertGreater(max(speeds) - min(speeds), 55)

    def test_target_moves_and_bounces_inside_the_visible_playfield(self) -> None:
        game = self._playing_game()
        target = game._spawn_target(101.0, "rabbit")
        start = target.rect.center
        game._move_target(target, 0.25)
        self.assertNotEqual(target.rect.center, start)
        self.assertTrue(game.play_bounds.contains(target.rect))

        target.x = game.play_bounds.right - target.rect.width / 2.0
        target.velocity_x = abs(target.velocity_x)
        game._move_target(target, 0.20)
        self.assertLess(target.velocity_x, 0.0)
        self.assertTrue(game.play_bounds.contains(target.rect))

    def test_camera_latency_corridor_counts_visible_old_position(self) -> None:
        game = self._playing_game()
        target = game._spawn_target(101.0, "rabbit")
        target.rect.center = (560, 390)
        target.x, target.y = target.rect.center
        target.velocity_x = 240.0
        target.velocity_y = 0.0
        camera_position = (target.rect.left - 34, target.rect.centery)
        self.assertFalse(target.rect.collidepoint(camera_position))
        self.assertTrue(game._target_hit_rect(target).collidepoint(camera_position))
        self.assertEqual(game.handle_shot(camera_position, 101.1), "rabbit")

    def test_complete_flow_ends_with_game_over(self) -> None:
        game = self._game()
        self.assertEqual(game.handle_shot(game.start_card.center, 100.0), "handled")
        self.assertEqual(game.state, "countdown")
        game.update(104.0)
        self.assertEqual(game.state, "playing")
        game.update(game.deadline + 0.01)
        self.assertEqual(game.state, "game_over")
        self.assertEqual(game.finish_reason, "Zeit abgelaufen")

    def test_ready_rules_keep_clear_space_between_label_and_points(self) -> None:
        game = self._game()
        content_right = game.start_card.right - 210
        label_left = game.start_card.left + 72
        label_right = label_left + game.font.size("PERSON NICHT TREFFEN")[0]
        value_left = content_right - 2 - game.font_large.size("−100 PUNKTE")[0]
        self.assertGreaterEqual(value_left - label_right, 20)

    def test_full_photo_card_and_generous_edge_are_shootable(self) -> None:
        for kind in ("rabbit", "person"):
            with self.subTest(kind=kind):
                game = self._playing_game()
                target = game._spawn_target(101.0, kind)
                hit_rect = game._target_hit_rect(target)
                edge = (hit_rect.right - 2, target.rect.centery)
                expected = "rabbit" if kind == "rabbit" else "person"
                self.assertEqual(game.handle_shot(edge, 101.1), expected)

    def test_photo_brightness_keeps_headroom_for_real_red_laser(self) -> None:
        game = self._playing_game()
        for kind in ("rabbit", "person"):
            target = game._spawn_target(101.0, kind)
            photo = pygame.surfarray.array3d(game._target_photo(target))
            self.assertLessEqual(int(photo[:, :, 0].max()), 145)
            self.assertGreater(int(photo[:, :, 2].max()), int(photo[:, :, 0].max()))

    def test_laser_afterglow_is_not_counted_twice(self) -> None:
        game = self._playing_game()
        target = game._spawn_target(101.0, "rabbit")
        self.assertEqual(game.handle_shot(target.rect.center, 101.1), "rabbit")
        self.assertEqual(game.handle_shot(target.rect.center, 101.25), "handled")
        self.assertEqual(game.shots, 1)
        self.assertEqual(game.score, 50)

    def test_every_screen_remains_red_laser_neutral(self) -> None:
        game = self._playing_game()
        for kind in ("rabbit", "person"):
            game._spawn_target(101.0, kind)
            game.draw(101.1)
            rgb = pygame.surfarray.array3d(self.screen).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_red_laser_is_detected_across_both_photo_cards(self) -> None:
        for kind in ("rabbit", "person"):
            with self.subTest(kind=kind):
                game = self._playing_game()
                target = game._spawn_target(101.0, kind)
                game.draw(101.1)
                rgb = np.transpose(pygame.surfarray.array3d(self.screen), (1, 0, 2))
                background = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                points = (
                    target.rect.center,
                    (target.rect.left + 18, target.rect.centery),
                    (target.rect.right - 18, target.rect.centery),
                    (target.rect.centerx, target.rect.bottom - 18),
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
