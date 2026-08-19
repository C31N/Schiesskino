from __future__ import annotations

import inspect
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import numpy as np

from laser_arcade.apps import chickens as chickens_module
from laser_arcade.apps.chickens import ChickenApp, PopupChicken


class MoorhuhnGameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.font.init()
        cls.screen = pygame.display.set_mode((1024, 768))
        cls.game = ChickenApp(
            cls.screen,
            audio_enabled=False,
            persist_scores=False,
            random_seed=17,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.game.start(0.0)

    def _enter_game(self) -> float:
        self.assertEqual(
            self.game.handle_shot(self.game.ready_start_button.center, 0.1),
            "handled",
        )
        now = 0.1 + self.game.COUNTDOWN_DURATION + 0.01
        self.game.update(now)
        self.assertEqual(self.game.state, "playing")
        return now

    def test_original_animation_sequences_and_event_sounds_are_loaded(self) -> None:
        self.assertEqual(len(self.game.flight_source), 12)
        self.assertEqual(len(self.game.death_source), 8)
        self.assertEqual(len(self.game.big_frames), 19)
        self.assertEqual(len(self.game.big_death_frames), 6)
        self.assertEqual(len(self.game.pumpkin_frames), 9)
        self.assertEqual(len(self.game.mill_frames), 36)
        self.assertEqual(len(self.game.mill_death), 72)
        self.assertEqual(len(self.game.sounds.items), 18)
        # Flug-, Auftauch- und Windradziele stammen aus den vorhandenen
        # Originalfolgen und werden gemeinsam laserneutral vorbereitet.
        source = inspect.getsource(chickens_module)
        self.assertIn("_load_original_moorhuhn_sequence", source)
        self.assertIn('"chicken_flight"', source)
        self.assertIn('"big_chicken"', source)
        self.assertIn('"mill"', source)
        self.assertIn("_load_tree_asset", source)
        self.assertIn("neutralize_laser_red", source)
        self.assertNotIn("def _sign_surface", source)
        self.assertNotIn("def _shoot_sign", source)

    def test_start_creates_original_three_chicken_classes_without_ammo(self) -> None:
        self._enter_game()

        self.assertEqual(self.game.time_left, 90.0)
        self.assertEqual({chicken.kind for chicken in self.game.flying}, {"small", "middle", "big"})
        self.assertFalse(hasattr(self.game, "ammo"))

    def test_projected_red_animation_is_not_accepted_as_laser(self) -> None:
        # Auf dem echten 1024×768-Projektionsbild gemessene Fehlkandidaten.
        self.assertFalse(self.game.is_laser_signature(63, 79))
        self.assertFalse(self.game.is_laser_signature(51, 88))
        self.assertFalse(self.game.is_laser_signature(84, 107))
        self.assertTrue(self.game.is_laser_signature(160, 70))
        self.assertTrue(self.game.is_laser_signature(60, 130))

    def test_menu_background_moves_while_buttons_remain_laser_safe(self) -> None:
        self.game.state = "ready"
        self.game.draw(100.0)
        initial_frame = pygame.surfarray.array3d(self.game.screen).copy()
        self.game.draw(102.0)
        updated_frame = pygame.surfarray.array3d(self.game.screen)
        self.assertFalse(np.array_equal(initial_frame, updated_frame))
        self.assertGreater(int(np.any(initial_frame != updated_frame, axis=2).sum()), 18)

        sample = self.game.screen.get_at(
            (self.game.ready_start_button.right - 18, self.game.ready_start_button.top + 14)
        )
        self.assertLessEqual(sample.r, sample.g)
        self.assertLessEqual(sample.r, sample.b)
        self.assertLess(sample.r - max(sample.g, sample.b), self.game.LASER_FALLBACK_RED_EXCESS)

    def test_world_layers_have_no_visible_magenta_matte_seams(self) -> None:
        for layer, _, _ in self.game.background_layers:
            rgb = pygame.surfarray.array3d(layer).astype(np.int16)
            alpha = pygame.surfarray.array_alpha(layer)
            red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
            magenta = (
                (alpha > 0)
                & (red >= 70)
                & (blue >= 70)
                & ((np.minimum(red, blue) - green) >= 28)
            )
            self.assertFalse(bool(magenta.any()))

    def test_moon_clouds_and_sky_keep_strong_laser_headroom(self) -> None:
        sky = self.game.background_layers[0][0]
        rgb = pygame.surfarray.array3d(sky)
        alpha = pygame.surfarray.array_alpha(sky) > 0
        self.assertTrue(bool(alpha.any()))
        # Selbst der hellste Mond-/Wolkenpixel lässt mindestens 123 Stufen
        # Rotreserve bis zum Ausbrennen des Projektorbilds.
        self.assertLessEqual(int(rgb[alpha].max()), 132)
        self.assertGreaterEqual(255 - int(rgb[alpha].max()), 123)

    def test_foreground_tree_reads_as_textured_photo_not_black_screen_seam(self) -> None:
        tree = self.game.tree_surfaces[0]
        rgb = pygame.surfarray.array3d(tree).astype(np.int16)
        alpha = pygame.surfarray.array_alpha(tree) > 0

        opaque_pixels = rgb[alpha]
        self.assertGreater(len(opaque_pixels), 1000)
        self.assertLessEqual(tree.get_width(), round(self.game.screen.get_width() * 0.18))
        # Der Stamm darf nicht mehr als nahezu schwarzer Balken erscheinen.
        self.assertGreater(float(opaque_pixels.mean(axis=1).mean()), 28.0)
        # Die fotografische Borke muss deutlich mehr Abstufungen als eine
        # gezeichnete Vollfläche besitzen.
        self.assertGreater(len(np.unique(opaque_pixels, axis=0)), 100)
        red_excess = opaque_pixels[:, 0] - np.maximum(
            opaque_pixels[:, 1], opaque_pixels[:, 2]
        )
        self.assertLess(int(red_excess.max()), self.game.LASER_FALLBACK_RED_EXCESS)

    def test_moving_frames_keep_color_but_remove_bright_red_trigger_pixels(self) -> None:
        sample = self.game.flight_source[0]
        rgb = pygame.surfarray.array3d(sample).astype(np.int16)
        alpha = pygame.surfarray.array_alpha(sample) > 0
        self.assertGreater(int(rgb[alpha].max(axis=0).max()), 100)
        saturated_red = (
            alpha
            & (rgb[:, :, 0] >= 150)
            & (rgb[:, :, 1] <= 95)
            & (rgb[:, :, 2] <= 110)
            & ((rgb[:, :, 0] - rgb[:, :, 1]) >= 70)
        )
        self.assertFalse(bool(saturated_red.any()))

    def test_every_shootable_sprite_keeps_laser_brightness_reserve(self) -> None:
        groups = (
            self.game.flight_source,
            self.game.death_source,
            self.game.big_frames,
            self.game.big_death_frames,
            self.game.pumpkin_frames,
            self.game.mill_frames,
            tuple(self.game.mill_death.values()),
        )
        for group in groups:
            for surface in group:
                rgb = pygame.surfarray.array3d(surface)
                alpha = pygame.surfarray.array_alpha(surface) > 0
                if bool(alpha.any()):
                    self.assertLessEqual(int(rgb[alpha].max()), 162)

    def test_complete_scene_is_laser_neutral(self) -> None:
        now = self._enter_game()
        self.game.camera = self.game.camera_target = 1.0
        self.game.draw(now)
        rgb = pygame.surfarray.array3d(self.game.screen).astype(np.int16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(
            bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any())
        )

    def test_original_flying_scores_and_unlimited_shots(self) -> None:
        now = self._enter_game()
        self.game.popups.clear()
        self.game.flying = [chicken for chicken in self.game.flying if chicken.kind == "small"]
        target = self.game.flying[0]
        target.x = 760.0
        target.y = 180.0

        for index in range(20):
            self.assertEqual(self.game.handle_shot((700, 740), now + index * 0.01), "miss")
        self.assertEqual(self.game.handle_shot(target.rect.center, now + 0.3), "hit")

        self.assertEqual(self.game.score, 20)
        self.assertEqual(self.game.hits, 1)
        self.assertEqual(self.game.shots, 21)

    def test_removed_sign_is_a_miss_and_other_special_targets_keep_values(self) -> None:
        now = self._enter_game()
        self.game.flying.clear()
        self.game.popups.clear()

        self.game.camera = self.game.camera_target = 0.10
        former_sign_position = (
            self.game._world_screen_x(930),
            round(450 * self.game.scale),
        )
        self.assertEqual(self.game.handle_shot(former_sign_position, now + 0.1), "miss")
        self.assertEqual(self.game.score, 0)
        self.assertFalse(hasattr(self.game, "sign_shot"))
        self.assertFalse(hasattr(self.game, "sign_frames"))

        self.game.camera = self.game.camera_target = 1.0
        self.assertEqual(self.game.handle_shot(self.game._pumpkin_rect().center, now + 0.2), "hit")
        self.assertEqual(self.game.score, 15)

        mill_chicken = self.game.mill[0]
        mill_image = self.game.mill_frames[mill_chicken.phase]
        mill_rect = self.game._mill_rect(mill_image)
        opaque = pygame.mask.from_surface(mill_image).outline()[0]
        mill_point = (mill_rect.left + opaque[0], mill_rect.top + opaque[1])
        self.assertEqual(self.game.handle_shot(mill_point, now + 0.3), "hit")
        self.assertEqual(self.game.score, 40)

        self.game.camera = self.game.camera_target = 0.5
        popup = PopupChicken(world_x=1300.0, state="holding", frame_index=8)
        self.game.popups = [popup]
        popup_rect = self.game._popup_rect(popup, self.game.big_frames[8])
        self.assertEqual(self.game.handle_shot(popup_rect.center, now + 0.4), "hit")
        self.assertEqual(self.game.score, 65)

        self.game.popups.clear()
        self.game.camera = self.game.camera_target = 0.0
        self.assertEqual(self.game.handle_shot(self.game._tree_rects()[0].center, now + 0.5), "tree")
        self.assertEqual(self.game.score, 65)

    def test_original_left_sprite_is_flipped_only_for_rightward_flight(self) -> None:
        left_flight, left_death = self.game._flight_frames(60, -1)
        right_flight, right_death = self.game._flight_frames(60, 1)
        self.assertEqual(len(left_flight), 12)
        self.assertEqual(len(left_death), 8)
        for left, right in zip(left_flight, right_flight):
            expected = pygame.transform.flip(left, True, False)
            self.assertTrue(
                np.array_equal(
                    pygame.surfarray.array3d(expected),
                    pygame.surfarray.array3d(right),
                )
            )
        for left, right in zip(left_death, right_death):
            expected = pygame.transform.flip(left, True, False)
            self.assertTrue(
                np.array_equal(
                    pygame.surfarray.array3d(expected),
                    pygame.surfarray.array3d(right),
                )
            )

    def test_alpha_hit_shape_rejects_far_transparent_corners(self) -> None:
        now = self._enter_game()
        target = self.game.flying[0]
        target.x = 400.0
        target.y = 250.0
        visible = pygame.mask.from_surface(target.image).outline()
        self.assertTrue(visible)
        visible_point = (
            target.rect.left + visible[len(visible) // 2][0],
            target.rect.top + visible[len(visible) // 2][1],
        )
        self.assertTrue(target.hit_test(visible_point, margin=0))
        corner = target.rect.topleft
        if pygame.mask.from_surface(target.image).get_at((0, 0)):
            corner = target.rect.bottomright
        self.assertFalse(target.hit_test(corner, margin=0))

    def test_popup_transition_is_guarded_then_becomes_stable_target(self) -> None:
        now = self._enter_game()
        self.game.popups = [PopupChicken(world_x=700.0)]
        self.assertTrue(self.game.visual_transition_active)

        for step in range(30):
            self.game._update_popup_chickens(0.055)
            if self.game.popups[0].state == "holding":
                break
        popup = self.game.popups[0]
        self.assertEqual(popup.state, "holding")
        self.assertFalse(self.game.visual_transition_active)
        held_frame = popup.frame_index
        self.game._update_popup_chickens(0.5)
        self.assertEqual(popup.frame_index, held_frame)

        point = self.game._popup_rect(popup, self.game.big_frames[held_frame]).center
        self.assertEqual(self.game.handle_shot(point, now + 0.8), "hit")
        self.assertTrue(self.game.visual_transition_active)

    def test_panorama_controls_and_every_visible_screen_are_shootable(self) -> None:
        now = self._enter_game()
        self.assertEqual(
            self.game.handle_shot(self.game.pan_right_button.center, now + 0.1),
            "pan",
        )
        self.assertGreater(self.game.camera_target, 0.0)

        self.game.update(now + 0.4)
        self.assertGreater(self.game.camera, 0.0)

        self.game.state = "game_over"
        self.assertEqual(self.game.handle_shot(self.game.result_card.center, now + 0.5), "handled")
        self.assertEqual(self.game.state, "countdown")

        self.game.state = "game_over"
        menu_side = (self.game.result_card.right - 30, self.game.result_card.top + 35)
        self.assertEqual(self.game.handle_shot(menu_side, now + 0.55), "menu")

        self.game.state = "ready"
        self.assertEqual(self.game.handle_shot(self.game.ready_score_button.center, now + 0.6), "handled")
        self.assertEqual(self.game.state, "scores")
        self.assertEqual(self.game.handle_shot((512, 384), now + 0.7), "handled")
        self.assertEqual(self.game.state, "ready")

    def test_large_original_target_also_starts_game_with_pistol(self) -> None:
        self.assertEqual(
            self.game.handle_shot(self.game.ready_target_button.center, 0.2),
            "handled",
        )
        self.assertEqual(self.game.state, "countdown")

    def test_90_seconds_finish_automatically(self) -> None:
        now = self._enter_game()
        self.game.update(now + self.game.GAME_DURATION + 0.01)

        self.assertEqual(self.game.state, "game_over")
        self.assertEqual(self.game.time_left, 0.0)
        self.assertEqual(self.game.finish_reason, "Die Zeit ist abgelaufen")

    def test_all_states_render_without_error(self) -> None:
        now = self._enter_game()
        for state in ("ready", "scores", "countdown", "playing", "game_over"):
            self.game.state = state
            self.game.draw(now)

    def test_game_screen_has_no_decorative_outer_frame_or_corner_brackets(self) -> None:
        source = inspect.getsource(ChickenApp._draw_world)
        self.assertNotIn("self.screen.get_rect(), 7", source)
        self.assertNotIn("pygame.Rect(14, 14", source)


if __name__ == "__main__":
    unittest.main()
