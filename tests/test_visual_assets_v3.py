from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np
import pygame

from laser_arcade.apps.arcade_common import (
    THEME_VISUAL_PROFILES,
    load_target_sprite,
    sprite_hit_test,
)
from laser_arcade.apps.duel_games import load_duel_sprite


ROOT = Path(__file__).resolve().parents[1]


class VisualAssetsV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def test_all_regular_theme_profiles_use_versioned_v3_backgrounds(self) -> None:
        expected = {
            "cans", "clay", "timed", "reaction", "range", "balloons",
            "aliens", "stars", "math", "colors", "treasure", "moorhuhn_game",
        }
        self.assertTrue(expected.issubset(THEME_VISUAL_PROFILES))
        for theme in expected:
            with self.subTest(theme=theme):
                profile = THEME_VISUAL_PROFILES[theme]
                self.assertTrue(profile.background_file.endswith("_v3.png"))
                path = ROOT / "assets" / "arcade_themes" / profile.background_file
                self.assertTrue(path.is_file(), path)
                image = pygame.image.load(str(path))
                self.assertEqual(image.get_width() * 3, image.get_height() * 4)

    def test_menu_uses_nostalgic_versioned_v3_background(self) -> None:
        profile = THEME_VISUAL_PROFILES["menu"]
        self.assertEqual(profile.background_file, "menu_background_v4.png")
        path = ROOT / "assets" / "arcade_themes" / profile.background_file
        self.assertTrue(path.is_file(), path)
        image = pygame.image.load(str(path))
        self.assertEqual(image.get_width() * 3, image.get_height() * 4)

    def test_special_game_backgrounds_are_versioned_four_by_three(self) -> None:
        paths = (
            ROOT / "assets" / "water_alarm" / "pool_background_v3.png",
            ROOT / "assets" / "ocean_cleanup" / "underwater_background_v3.png",
            ROOT / "assets" / "tobia_duel" / "reaction_arena_v3.png",
        )
        for path in paths:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                image = pygame.image.load(str(path))
                self.assertEqual(image.get_width() * 3, image.get_height() * 4)

    def test_every_runtime_bitmap_asset_exists_and_decodes(self) -> None:
        """Schließt stille Fallback-Grafiken durch fehlende Produktivdateien aus."""

        paths = [
            *(ROOT / "assets" / "arcade_themes" / profile.background_file
              for profile in THEME_VISUAL_PROFILES.values()),
            *(ROOT / "assets" / "arcade_targets" / filename for filename in (
                "alien_v3.png", "balloon_v3.png", "can_v3.png", "chicken_v3.png",
                "clay_v3.png", "mechanical_target_v3.png", "star_v3.png",
                "treasure_chest_v3.png", "water_ball_v3.png",
                "water_dolphin_v3.png", "water_duck_v3.png", "water_leak_v3.png",
            )),
            *(ROOT / "assets" / "duel_v2" / f"{name}_v2.png" for name in (
                "tic_tac_toe", "connect_four", "dots_boxes", "memory", "nim", "reversi",
            )),
            ROOT / "assets" / "water_alarm" / "pool_background_v3.png",
            ROOT / "assets" / "water_alarm" / "wasserfreunde_dalum_logo.png",
            ROOT / "assets" / "ocean_cleanup" / "underwater_background_v3.png",
            ROOT / "assets" / "ocean_cleanup" / "cat_can_original.png",
            ROOT / "assets" / "ocean_cleanup" / "realistic_dolphin.png",
            ROOT / "assets" / "ocean_cleanup" / "realistic_fish.png",
            ROOT / "assets" / "ocean_cleanup" / "realistic_turtle.png",
            ROOT / "assets" / "tobia_duel" / "reaction_arena_v3.png",
            ROOT / "assets" / "tobia_duel" / "rabbit_original.jpeg",
            ROOT / "assets" / "tobia_duel" / "tobia_original.jpeg",
        ]
        self.assertEqual(len(paths), len(set(paths)), "Asset-Prüfliste enthält Duplikate")
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue(path.is_file(), path)
                image = pygame.image.load(str(path))
                self.assertGreater(image.get_width(), 1)
                self.assertGreater(image.get_height(), 1)

    def test_target_assets_keep_alpha_and_laser_reserve(self) -> None:
        targets = (
            "alien", "balloon", "can", "chicken", "clay",
            "mechanical_target", "star", "treasure_chest", "water_ball",
            "water_dolphin", "water_duck", "water_leak",
        )
        for name in targets:
            with self.subTest(name=name):
                sprite = load_target_sprite(name, (128, 104), brightness_limit=150)
                alpha = pygame.surfarray.array_alpha(sprite)
                self.assertGreater(int((alpha >= 8).sum()), 500)
                self.assertGreater(int((alpha < 8).sum()), 100)
                rgb = pygame.surfarray.array3d(sprite).astype(np.int16)
                opaque = alpha >= 8
                red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
                self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28) & opaque).any()))
                self.assertLessEqual(int(rgb[opaque].max()), 150)

    def test_rotated_and_mirrored_sprite_masks_follow_visible_pixels(self) -> None:
        for flip, angle in ((False, 0.0), (True, 0.0), (False, 31.0), (True, -47.0)):
            with self.subTest(flip=flip, angle=angle):
                sprite = load_target_sprite(
                    "can", (72, 126), flip_x=flip, angle=angle, brightness_limit=146
                )
                mask = pygame.mask.from_surface(sprite, 8)
                rect = sprite.get_rect(center=(320, 260))
                visible = next(
                    (point for point in mask.outline() if mask.get_at(point)),
                    None,
                )
                self.assertIsNotNone(visible)
                assert visible is not None
                point = rect.left + visible[0], rect.top + visible[1]
                self.assertTrue(sprite_hit_test(point, rect, mask))
                self.assertFalse(sprite_hit_test((rect.left - 80, rect.top - 80), rect, mask, margin=12))

    def test_duel_sprite_sheets_are_transparent_laser_neutral_and_brightness_limited(self) -> None:
        layouts = {
            "tic_tac_toe": 2,
            "connect_four": 2,
            "dots_boxes": 3,
            "memory": 8,
            "nim": 1,
            "reversi": 2,
        }
        for name, count in layouts.items():
            path = ROOT / "assets" / "duel_v2" / f"{name}_v2.png"
            self.assertTrue(path.is_file(), path)
            for index in range(count):
                with self.subTest(name=name, index=index):
                    sprite = load_duel_sprite(name, index, (128, 112), brightness_limit=150)
                    alpha = pygame.surfarray.array_alpha(sprite)
                    rgb = pygame.surfarray.array3d(sprite).astype(np.int16)
                    opaque = alpha >= 8
                    self.assertGreater(int(opaque.sum()), 250)
                    red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
                    self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28) & opaque).any()))
                    self.assertLessEqual(int(rgb[opaque].max()), 150)


if __name__ == "__main__":
    unittest.main()
