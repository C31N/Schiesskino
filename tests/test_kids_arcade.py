from __future__ import annotations

import hashlib
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import cv2
import numpy as np
import pygame

from laser_arcade.apps.arcade_common import load_target_sprite
from laser_arcade.apps.kids_arcade import (
    AlienAlarmApp,
    Balloon,
    BalloonHuntApp,
    ColorMemoryApp,
    MathDuelApp,
    MovingTarget,
    StarHuntApp,
    TARGET_CYAN,
    TARGET_GREEN,
    TreasureHuntApp,
    TreasureObject,
    _background,
)
from laser_arcade.shot_detector import DetectionConfig, PulseShotDetector


class KidsArcadeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.screen = pygame.Surface((1024, 768))

    def _games(self):
        return (
            BalloonHuntApp(self.screen, audio_enabled=False, random_seed=19),
            AlienAlarmApp(self.screen, audio_enabled=False, random_seed=19),
            StarHuntApp(self.screen, audio_enabled=False, random_seed=19),
            MathDuelApp(self.screen, audio_enabled=False, random_seed=19),
            ColorMemoryApp(self.screen, audio_enabled=False, random_seed=19),
            TreasureHuntApp(self.screen, audio_enabled=False, random_seed=19),
        )

    @staticmethod
    def _playing(game, now: float = 100.0) -> None:
        game._reset_round()
        game.state = "playing"
        game.state_started = now
        game.last_update = now
        game.deadline = now + game.GAME_DURATION if game.GAME_DURATION else 0.0
        game.remaining = game.GAME_DURATION
        game._begin_play(now)

    def test_all_six_games_start_with_shot_and_offer_menu(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self.assertEqual(game.handle_shot(game.start_card.center, 100.0), "handled")
                self.assertEqual(game.state, "countdown")
                game.update(104.0)
                self.assertEqual(game.state, "playing")
                self.assertEqual(game.handle_shot(game.menu_button.center, 104.5), "menu")

    def test_balloon_has_three_lives_accelerates_and_scores(self) -> None:
        early = BalloonHuntApp(self.screen, audio_enabled=False, random_seed=7)
        late = BalloonHuntApp(self.screen, audio_enabled=False, random_seed=7)
        self._playing(early)
        self._playing(late)
        early._spawn(100.0)
        late._spawn(159.0)
        self.assertEqual(early.lives, 3)
        self.assertGreater(abs(late.balloons[0].velocity_y), abs(early.balloons[0].velocity_y))

        game = BalloonHuntApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.balloons = [Balloon(500, 390, 32, 0, -100, 0)]
        self.assertEqual(game.handle_shot((500, 390), 101.0), "hit")
        self.assertEqual(game.hits, 1)
        self.assertGreaterEqual(game.score, 100)

        game.balloons = [Balloon(500, 80, 30, 0, -100, 0)]
        game._update_game(102.0, 0.2)
        self.assertEqual(game.lives, 2)

    def test_alien_and_star_targets_score_and_are_generous(self) -> None:
        alien_game = AlienAlarmApp(self.screen, audio_enabled=False)
        self._playing(alien_game)
        alien = MovingTarget(510, 390, 32, 0, 0, 100.0, 2.0)
        alien_game.aliens = [alien]
        self.assertEqual(alien_game.handle_shot((510 + 32 + alien_game.hit_tolerance - 2, 390), 101.0), "hit")

        star_game = StarHuntApp(self.screen, audio_enabled=False)
        self._playing(star_game)
        star_game.star = MovingTarget(510, 390, 28, 0, 0, 100.0, 2.0)
        self.assertEqual(star_game.handle_shot((510, 390), 101.0), "hit")
        self.assertIsNone(star_game.star)

    def test_every_kids_arcade_target_accepts_visible_edge_and_calibration_margin(self) -> None:
        balloon_game = BalloonHuntApp(self.screen, audio_enabled=False)
        self._playing(balloon_game)
        balloon = Balloon(510, 390, 32, 0, -100, 0)
        balloon_game.balloons = [balloon]
        balloon_rx, _ = balloon_game._balloon_hit_radii(balloon)
        self.assertEqual(
            balloon_game.handle_shot((round(balloon.x + balloon_rx - 2), round(balloon.y)), 101.0),
            "hit",
        )

        alien_game = AlienAlarmApp(self.screen, audio_enabled=False)
        self._playing(alien_game)
        alien = MovingTarget(510, 390, 32, 0, 0, 100.0, 2.0)
        alien_game.aliens = [alien]
        alien_right = (
            round(alien.x + (alien.radius * 2 + 26) / 2 + alien_game._moving_target_margin(alien.radius) - 2),
            round(alien.y),
        )
        self.assertEqual(alien_game.handle_shot(alien_right, 101.0), "hit")

        star_game = StarHuntApp(self.screen, audio_enabled=False)
        self._playing(star_game)
        star = MovingTarget(510, 390, 32, 0, 0, 100.0, 2.0)
        star_game.star = star
        star_right = (
            round(star.x + star.radius + 6 + star_game._moving_target_margin(star.radius) - 2),
            round(star.y),
        )
        self.assertEqual(star_game.handle_shot(star_right, 101.0), "hit")

        math_game = MathDuelApp(self.screen, audio_enabled=False)
        self._playing(math_game)
        correct_rect = math_game.answer_rects[math_game.correct_index]
        self.assertEqual(
            math_game.handle_shot((correct_rect.left - 30, correct_rect.centery), 101.0),
            "correct",
        )

        color_game = ColorMemoryApp(self.screen, audio_enabled=False)
        self._playing(color_game)
        color_game.phase = "input"
        color_game.sequence = [0]
        color_game.input_index = 0
        color_rect = color_game.pad_rects[0]
        self.assertEqual(
            color_game.handle_shot((color_rect.left - 30, color_rect.centery), 101.0),
            "correct",
        )

        treasure_game = TreasureHuntApp(self.screen, audio_enabled=False)
        self._playing(treasure_game)
        target = next(obj for obj in treasure_game.objects if obj.kind == treasure_game.target_kind)
        treasure_edge = (
            target.center[0] + target.radius + treasure_game._moving_target_margin(target.radius) - 2,
            target.center[1],
        )
        self.assertEqual(treasure_game.handle_shot(treasure_edge, 101.0), "found")

    def test_math_duel_uses_four_answers_and_scores_right_and_wrong(self) -> None:
        game = MathDuelApp(self.screen, audio_enabled=False, random_seed=19)
        self._playing(game)
        self.assertEqual(len(game.answer_rects), 4)
        correct = game.correct_index
        self.assertEqual(game.handle_shot(game.answer_rects[correct].center, 101.0), "correct")
        self.assertEqual(game.score, 100)
        self.assertEqual(game.hits, 1)

        wrong = next(index for index in range(4) if index != game.correct_index)
        self.assertEqual(game.handle_shot(game.answer_rects[wrong].center, 102.0), "wrong")
        self.assertEqual(game.wrong, 1)
        self.assertEqual(game.score, 75)

    def test_math_answer_cards_are_visually_neutral_before_the_shot(self) -> None:
        game = MathDuelApp(self.screen, audio_enabled=False, random_seed=19)
        self._playing(game)
        game.draw(101.0)
        borders = [self.screen.get_at((rect.centerx, rect.top))[:3] for rect in game.answer_rects]
        self.assertEqual(borders, [TARGET_CYAN] * 4)
        self.assertNotIn(TARGET_GREEN, borders)

    def test_star_has_no_wide_bright_green_detection_band(self) -> None:
        game = StarHuntApp(self.screen, audio_enabled=False)
        self.screen.fill((0, 0, 0))
        game._draw_star((510, 390), 48)
        rgb = pygame.surfarray.array3d(self.screen)
        crop = rgb[440:580, 320:460]
        exact_green = np.all(crop == np.asarray(TARGET_GREEN), axis=2)
        self.assertEqual(int(exact_green.sum()), 0)

    def test_balloon_surface_keeps_extra_camera_headroom(self) -> None:
        sprite = load_target_sprite("balloon", (104, 147), brightness_limit=108)
        rgb = pygame.surfarray.array3d(sprite)
        self.assertLessEqual(int(rgb.max()), 108)

    def test_color_game_shows_growing_sequence_and_ends_after_three_errors(self) -> None:
        game = ColorMemoryApp(self.screen, audio_enabled=False, random_seed=19)
        self._playing(game)
        self.assertEqual(len(game.sequence), 1)
        self.assertTrue(game.visual_transition_active)
        game.phase = "input"
        game.sequence = [2]
        self.assertEqual(game.handle_shot(game.pad_rects[2].center, 101.0), "correct")
        self.assertEqual(game.completed_rounds, 1)

        for offset in range(3):
            game.phase = "input"
            game.sequence = [0]
            game.input_index = 0
            result = game.handle_shot(game.pad_rects[1].center, 102.0 + offset)
            self.assertEqual(result, "wrong")
        self.assertEqual(game.state, "game_over")
        self.assertEqual(game.errors, 3)

    def test_treasure_hunt_has_nine_objects_and_ten_levels(self) -> None:
        game = TreasureHuntApp(self.screen, audio_enabled=False, random_seed=19)
        self._playing(game)
        self.assertEqual(len(game.objects), 9)
        target = next(obj for obj in game.objects if obj.kind == game.target_kind)
        self.assertEqual(game.handle_shot(target.center, 101.0), "found")
        self.assertEqual(game.found, 1)
        self.assertEqual(game.hits, 1)

        game.found = game.TOTAL_LEVELS - 1
        game._new_level()
        target = next(obj for obj in game.objects if obj.kind == game.target_kind)
        self.assertEqual(game.handle_shot(target.center, 102.0), "complete")
        self.assertEqual(game.state, "game_over")

    def test_every_treasure_graphic_has_a_dark_full_detection_plate(self) -> None:
        game = TreasureHuntApp(self.screen, audio_enabled=False)
        for kind in game.KINDS:
            with self.subTest(kind=kind):
                game._reset_round()
                game.state = "playing"
                game.remaining = 60.0
                game.target_kind = kind
                game.objects = [TreasureObject(kind, (510, 390), 52)]
                game.draw(101.0)
                rgb = pygame.surfarray.array3d(self.screen)
                x, y = game.objects[0].center
                disk = rgb[x - 35:x + 36, y - 35:y + 36]
                self.assertLessEqual(int(disk.max()), 162)

    def test_additive_red_laser_is_detected_across_every_treasure_graphic(self) -> None:
        game = TreasureHuntApp(self.screen, audio_enabled=False)
        for kind in game.KINDS:
            game._reset_round()
            game.state = "playing"
            game.remaining = 60.0
            game.target_kind = kind
            game.objects = [TreasureObject(kind, (510, 390), 52)]
            game.draw(101.0)
            rgb = pygame.surfarray.array3d(self.screen).copy()
            background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
            for point in (
                (510, 390),
                (466, 390), (554, 390),
                (510, 346), (510, 434),
                (479, 359), (541, 359),
                (479, 421), (541, 421),
            ):
                with self.subTest(kind=kind, point=point):
                    detector = PulseShotDetector(DetectionConfig())
                    detector.process(background, 0.0)
                    detector.process(background, 30.0)
                    pulse = background.copy()
                    mask = np.zeros(background.shape[:2], dtype=np.uint8)
                    cv2.circle(mask, point, 3, 255, thickness=-1)
                    pulse[:, :, 2][mask > 0] = 245
                    result = detector.process(pulse, 70.0)
                    self.assertTrue(result.shot)

    def test_all_ready_countdown_play_and_result_screens_are_laser_neutral(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                states = []
                game.start(100.0)
                game.draw(100.0)
                states.append(pygame.surfarray.array3d(self.screen).copy())
                game.begin_countdown(101.0)
                game.draw(102.0)
                states.append(pygame.surfarray.array3d(self.screen).copy())
                game.update(105.0)
                game.update(105.5)
                game.draw(105.5)
                states.append(pygame.surfarray.array3d(self.screen).copy())
                game._finish("Test beendet", 106.0)
                game.draw(106.0)
                states.append(pygame.surfarray.array3d(self.screen).copy())
                for rgb in states:
                    rgb = rgb.astype(np.int16)
                    red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
                    self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_all_six_games_use_distinct_detailed_background_worlds(self) -> None:
        fingerprints = set()
        for game in self._games():
            background = _background(self.screen.get_size(), game.theme)
            rgb = pygame.surfarray.array3d(background)
            fingerprints.add(hashlib.sha256(rgb.tobytes()).hexdigest())
            sampled = rgb[::12, ::12].reshape(-1, 3)
            self.assertGreater(len(np.unique(sampled, axis=0)), 180, game.name)
        self.assertEqual(len(fingerprints), 6)

    def test_real_red_laser_is_detected_on_each_game_target(self) -> None:
        targets = []

        balloon = BalloonHuntApp(self.screen, audio_enabled=False)
        self._playing(balloon)
        balloon.balloons = [Balloon(510, 390, 34, 0, -100, 0)]
        balloon.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (510, 390), "Ballon"))

        alien = AlienAlarmApp(self.screen, audio_enabled=False)
        self._playing(alien)
        alien.aliens = [MovingTarget(510, 390, 34, 0, 0, 100.0, 2.0)]
        alien.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (510, 390), "Alien"))

        star = StarHuntApp(self.screen, audio_enabled=False)
        self._playing(star)
        star.star = MovingTarget(510, 390, 34, 0, 0, 100.0, 2.0)
        star.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (510, 390), "Stern"))

        math_game = MathDuelApp(self.screen, audio_enabled=False)
        self._playing(math_game)
        math_game.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), math_game.answer_rects[0].center, "Antwort"))

        colors = ColorMemoryApp(self.screen, audio_enabled=False)
        self._playing(colors)
        colors.phase = "input"
        colors.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), colors.pad_rects[0].center, "Farbe"))

        treasure = TreasureHuntApp(self.screen, audio_enabled=False)
        self._playing(treasure)
        treasure.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), treasure.objects[0].center, "Schatz"))

        for rgb, point, label in targets:
            background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
            detector = PulseShotDetector(DetectionConfig())
            detector.process(background, 0.0)
            detector.process(background, 30.0)
            pulse = background.copy()
            cv2.circle(pulse, point, 3, (12, 20, 245), thickness=-1)
            result = detector.process(pulse, 70.0)
            self.assertTrue(result.shot, label)
            self.assertLessEqual(abs(result.point[0] - point[0]), 2)
            self.assertLessEqual(abs(result.point[1] - point[1]), 2)

    def test_additive_red_laser_is_detected_near_each_visible_target_edge(self) -> None:
        targets = []

        balloon = BalloonHuntApp(self.screen, audio_enabled=False)
        self._playing(balloon)
        balloon.balloons = [Balloon(510, 390, 34, 0, -100, 0)]
        balloon.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (550, 390), "Ballonrand"))

        alien = AlienAlarmApp(self.screen, audio_enabled=False)
        self._playing(alien)
        alien.aliens = [MovingTarget(510, 390, 34, 0, 0, 100.0, 2.0)]
        alien.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (550, 390), "Alienrand"))

        star = StarHuntApp(self.screen, audio_enabled=False)
        self._playing(star)
        star.star = MovingTarget(510, 390, 34, 0, 0, 100.0, 2.0)
        star.draw(101.0)
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (548, 390), "Sternrand"))

        math_game = MathDuelApp(self.screen, audio_enabled=False)
        self._playing(math_game)
        math_game.draw(101.0)
        math_rect = math_game.answer_rects[0]
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (math_rect.left + 8, math_rect.centery), "Antwortrand"))

        colors = ColorMemoryApp(self.screen, audio_enabled=False)
        self._playing(colors)
        colors.phase = "input"
        colors.draw(101.0)
        color_rect = colors.pad_rects[0]
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (color_rect.left + 8, color_rect.centery), "Farbfeldrand"))

        treasure = TreasureHuntApp(self.screen, audio_enabled=False)
        self._playing(treasure)
        treasure.draw(101.0)
        treasure_obj = treasure.objects[0]
        targets.append((pygame.surfarray.array3d(self.screen).copy(), (treasure_obj.center[0] + 52, treasure_obj.center[1]), "Schatzrand"))

        for rgb, point, label in targets:
            with self.subTest(target=label):
                background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
                detector = PulseShotDetector(DetectionConfig())
                detector.process(background, 0.0)
                detector.process(background, 30.0)
                pulse = background.copy()
                mask = np.zeros(background.shape[:2], dtype=np.uint8)
                cv2.circle(mask, point, 3, 255, thickness=-1)
                pulse[:, :, 2][mask > 0] = 245
                result = detector.process(pulse, 70.0)
                self.assertTrue(result.shot, label)

    def test_additive_red_laser_survives_star_and_math_target_colors(self) -> None:
        star = StarHuntApp(self.screen, audio_enabled=False)
        self._playing(star)
        star.star = MovingTarget(510, 390, 48, 0, 0, 100.0, 2.0)
        star.draw(101.0)
        star_rgb = pygame.surfarray.array3d(self.screen).copy()
        star_points = ((510, 390), (510, 344), (552, 376), (536, 427))

        math_game = MathDuelApp(self.screen, audio_enabled=False)
        self._playing(math_game)
        math_game.draw(101.0)
        math_rgb = pygame.surfarray.array3d(self.screen).copy()
        math_points = tuple(rect.center for rect in math_game.answer_rects)

        for rgb, points, label in (
            (star_rgb, star_points, "Stern"),
            (math_rgb, math_points, "Mathefeld"),
        ):
            background = cv2.cvtColor(np.transpose(rgb, (1, 0, 2)), cv2.COLOR_RGB2BGR)
            for index, point in enumerate(points):
                detector = PulseShotDetector(DetectionConfig())
                detector.process(background, 0.0)
                detector.process(background, 30.0)
                pulse = background.copy()
                # Der Projektoruntergrund bleibt erhalten; nur der rote Kanal
                # wird durch den Laserimpuls angehoben.
                mask = np.zeros(background.shape[:2], dtype=np.uint8)
                cv2.circle(mask, point, 3, 255, thickness=-1)
                pulse[:, :, 2][mask > 0] = 245
                result = detector.process(pulse, 70.0)
                self.assertTrue(result.shot, f"{label} {index}")


if __name__ == "__main__":
    unittest.main()
