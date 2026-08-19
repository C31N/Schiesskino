from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import numpy as np

from laser_arcade.apps.arcade_leaderboard import ArcadeLeaderboardOverlay


class ArcadeLeaderboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def setUp(self) -> None:
        self.screen = pygame.Surface((1024, 768))

    def test_every_game_builds_its_own_logical_board(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        cases = (
            (
                "cans",
                SimpleNamespace(
                    score=1200, knocked_down=8, accuracy=80.0, best_combo=4,
                    finish_reason="Fertig",
                ),
                "cans",
            ),
            (
                "clay",
                SimpleNamespace(
                    score=1300, hits=15, TOTAL_CLAYS=20, accuracy=75.0,
                    best_combo=5, finish_reason="Fertig",
                ),
                "clay",
            ),
            (
                "timed",
                SimpleNamespace(
                    score=1400, hits=17, TOTAL_TARGETS=20, average_time_ms=512,
                    best_combo=6, accuracy=85.0, finish_reason="Fertig",
                ),
                "timed",
            ),
            (
                "reaction",
                SimpleNamespace(
                    score=1500, hits=11, ROUNDS=12, average_ms=438,
                    false_starts=1, finish_reason="Fertig",
                ),
                "reaction",
            ),
            (
                "tobia",
                SimpleNamespace(
                    score=650, rabbit_hits=15, person_hits=1, accuracy=75.0,
                    finish_reason="Zeit abgelaufen",
                ),
                "tobia",
            ),
            (
                "chickens",
                SimpleNamespace(
                    score=1919, hits=14, shots=20, accuracy=70.0,
                    best_score=2200, finish_reason="Die Zeit ist abgelaufen",
                ),
                "chickens",
            ),
            (
                "balloons",
                SimpleNamespace(
                    score=1800, hits=14, accuracy=82.0, best_combo=7,
                    leaderboard_detail="14 BALLONS · 82 %",
                    leaderboard_metrics=(("PUNKTE", "1.800"), ("BALLONS", "14")),
                    finish_reason="Zeit abgelaufen",
                ),
                "balloons",
            ),
            (
                "math",
                SimpleNamespace(
                    score=900, hits=10, accuracy=77.0, best_combo=5,
                    leaderboard_detail="10 RICHTIG · 3 FALSCH",
                    leaderboard_metrics=(("PUNKTE", "900"), ("RICHTIG", "10")),
                    finish_reason="Zeit abgelaufen",
                ),
                "math",
            ),
            (
                "range",
                SimpleNamespace(
                    current_result=SimpleNamespace(
                        mode="whole", shot_count=5, result_value=47.0,
                        display="47 RINGE",
                    ),
                    MODE_LABELS={"whole": "GANZE RINGE"},
                ),
                "range:whole:5",
            ),
        )
        for game_key, game, expected_board in cases:
            with self.subTest(game=game_key):
                overlay.clear()
                self.assertTrue(overlay.prepare(game_key, game))
                self.assertEqual(overlay.candidate.board, expected_board)
                self.assertTrue(overlay.qualifies)

    def test_target_boards_are_separate_and_divider_ranks_lower_value_first(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            current_result=SimpleNamespace(
                mode="divider", shot_count=10, result_value=120.0,
                display="Ø 120 TEILER",
            ),
            MODE_LABELS={"divider": "TEILER"},
        )
        overlay.prepare("range", game)
        self.assertEqual(overlay.candidate.board, "range:divider:10")
        self.assertEqual(overlay.candidate.rank_value, -120.0)

    def test_names_have_letters_only_and_use_pin_1919(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        self.assertEqual(overlay.ADMIN_PIN, "1919")
        self.assertFalse(any(key.isdigit() for key, _ in overlay.key_buttons))
        self.assertIn("Ä", [key for key, _ in overlay.key_buttons])
        self.assertEqual(overlay._append_name("4"), "ignored")
        for character in "ÄNNCHENXYZ":
            overlay._append_name(character)
        self.assertEqual(overlay.player_name, "ÄNNCHENX")

    def test_child_friendly_name_keyboard_uses_full_width_large_keys(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        self.assertEqual(len(overlay.key_buttons), 29)
        self.assertEqual(len({rect.top for _, rect in overlay.key_buttons}), 3)
        self.assertGreaterEqual(min(rect.height for _, rect in overlay.key_buttons), 70)
        self.assertLessEqual(overlay.name_keyboard_rect.left, 34)
        self.assertGreaterEqual(overlay.name_keyboard_rect.right, 990)

    def test_name_keyboard_gaps_choose_nearest_letter_and_empty_save_explains(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=2100, knocked_down=8, accuracy=80.0, best_combo=4,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", game)
        overlay.state = "name_entry"
        first, second = overlay.key_buttons[:2]
        gap = ((first[1].right + second[1].left) // 2, first[1].centery)
        self.assertEqual(overlay.handle_shot(gap, 100.0), "leaderboard")
        self.assertIn(overlay.player_name, {first[0], second[0]})

        overlay.player_name = ""
        self.assertEqual(overlay.handle_shot(overlay.save_button.center, 101.0), "name_required")
        self.assertIn("BUCHSTABEN", overlay.name_message)

    def test_physical_keyboard_can_enter_and_remove_name_letters(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=2100, knocked_down=8, accuracy=80.0, best_combo=4,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", game)
        overlay.state = "name_entry"
        self.assertEqual(
            overlay.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_m, unicode="m")),
            "leaderboard",
        )
        self.assertEqual(overlay.player_name, "M")
        overlay.handle_key(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, unicode=""))
        self.assertEqual(overlay.player_name, "")

    def test_pin_1919_resets_only_the_current_game_board(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        cans = SimpleNamespace(
            score=1000, knocked_down=6, accuracy=75.0, best_combo=3,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", cans)
        cans_entry = replace(overlay.candidate, name="MAX")
        clay_entry = replace(overlay.candidate, board="clay", name="LENA")
        overlay.entries = [cans_entry, clay_entry]
        overlay.admin_digits = "1919"
        self.assertEqual(overlay._confirm_admin(), "admin_reset")
        self.assertEqual([entry.board for entry in overlay.entries], ["clay"])

    def test_top_ten_is_persisted_per_board(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "arcade.json"
            overlay = ArcadeLeaderboardOverlay(self.screen, path)
            game = SimpleNamespace(
                score=2100, hits=18, TOTAL_CLAYS=20, accuracy=90.0,
                best_combo=7, finish_reason="Fertig",
            )
            overlay.prepare("clay", game)
            overlay.state = "name_entry"
            overlay._append_name("M")
            self.assertEqual(overlay._save_name(), "saved")
            reloaded = ArcadeLeaderboardOverlay(self.screen, path)
            entries = reloaded._board_entries("clay")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].name, "M")

    def test_all_game_result_buttons_remain_easy_to_shoot_after_saving(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=2100, knocked_down=8, accuracy=80.0, best_combo=4,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", game)
        self.assertEqual(
            overlay.handle_shot(overlay.name_button.center, 100.0),
            "leaderboard",
        )
        overlay.player_name = "MAX"
        self.assertEqual(overlay.handle_shot(overlay.save_button.center, 101.0), "saved")
        self.assertEqual(overlay.handle_shot(overlay.menu_button.center, 101.2), "ignored")

        broad_menu_point = (
            overlay.menu_button.right + 90,
            overlay.menu_button.top - 70,
        )
        self.assertEqual(overlay.handle_shot(broad_menu_point, 101.7), "menu")

    def test_result_keeps_name_repeat_and_menu_available_without_time_limit(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=2100, knocked_down=8, accuracy=80.0, best_combo=4,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", game, now=100.0)

        self.assertEqual(overlay.state, "result")
        self.assertFalse(overlay.skipped)
        self.assertEqual(overlay.handle_shot(overlay.repeat_button.center, 3700.0), "repeat")

        overlay.prepare("cans", game, now=100.0)
        self.assertEqual(overlay.handle_shot(overlay.menu_button.center, 3700.0), "menu")

    def test_selecting_name_entry_remains_open_without_time_limit(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=2100, knocked_down=8, accuracy=80.0, best_combo=4,
            finish_reason="Fertig",
        )
        overlay.prepare("cans", game, now=100.0)

        self.assertEqual(
            overlay.handle_shot(overlay.name_button.center, 3700.0),
            "leaderboard",
        )
        self.assertEqual(overlay.state, "name_entry")
        self.assertEqual(overlay.state, "name_entry")

    def test_result_name_and_admin_views_render(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=1000, hits=10, TOTAL_TARGETS=20, average_time_ms=550,
            best_combo=3, accuracy=50.0, finish_reason="Fertig",
        )
        overlay.prepare("timed", game)
        for state in ("result", "name_entry", "admin"):
            overlay.state = state
            overlay.draw()
            self.assertEqual(self.screen.get_size(), (1024, 768))
            rgb = pygame.surfarray.array3d(self.screen).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))
            self.assertGreater(int(rgb[:, :, 1].std()), 18)
        overlay.clear()
        for game_key in (
            "cans", "clay", "timed", "reaction", "tobia", "chickens",
        ):
            overlay.draw_ready_preview(game_key)
            self.assertEqual(self.screen.get_size(), (1024, 768))

    def test_leaderboard_uses_project_local_nostalgic_background(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        self.assertEqual(overlay.background.get_size(), self.screen.get_size())
        rgb = pygame.surfarray.array3d(overlay.background).astype(np.int16)
        self.assertGreater(int(rgb[:, :, 1].std()), 18)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_each_leaderboard_uses_the_matching_game_world(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        fingerprints = set()
        for game_key in ("cans", "clay", "balloons", "aliens", "treasure"):
            overlay.active_game = game_key
            background = overlay._background_for_active_game()
            rgb = pygame.surfarray.array3d(background).astype(np.int16)
            fingerprints.add(hash(rgb.tobytes()))
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(
                bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()),
                game_key,
            )
        self.assertEqual(len(fingerprints), 5)

    def test_annas_meeresmission_never_opens_top_ten_or_name_entry(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            leaderboard_enabled=False,
            score=175,
            trash_collected=27,
            cat_cans_collected=6,
            animal_hits=0,
            accuracy=90.0,
            leaderboard_detail="27 MÜLL · 6 KATZENDOSEN · 0 TIERE GETROFFEN",
            leaderboard_metrics=(
                ("PUNKTE", "175"),
                ("MÜLL", "27"),
                ("KATZENDOSEN", "6"),
                ("PRÄZISION", "90 %"),
            ),
            finish_reason="Die Mission ist beendet",
        )
        self.assertFalse(overlay.prepare("ocean", game))
        self.assertFalse(overlay.active)
        self.assertIsNone(overlay.candidate)
        overlay.draw_ready_preview("ocean")
        self.assertFalse(overlay.active)

    def test_moorhuhn_has_an_independent_top_ten_board(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        game = SimpleNamespace(
            score=1919, hits=14, shots=20, accuracy=70.0,
            best_score=2200, finish_reason="Die Zeit ist abgelaufen",
        )
        self.assertTrue(overlay.prepare("chickens", game))
        self.assertEqual(overlay.game_title, "MOORHUHN")
        self.assertEqual(overlay.candidate.board, "chickens")
        overlay.state = "name_entry"
        overlay.player_name = "MAX"
        self.assertEqual(overlay._save_name(), "saved")
        self.assertEqual(len(overlay._board_entries("chickens")), 1)
        self.assertEqual(len(overlay._board_entries("cans")), 0)


if __name__ == "__main__":
    unittest.main()
