from __future__ import annotations

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from laser_arcade.apps.arcade_common import SAFE_CYAN
from laser_arcade.apps.arcade_leaderboard import ArcadeLeaderboardOverlay
from laser_arcade.apps.duel_games import (
    ConnectFourApp,
    DotsBoxesApp,
    MemoryDuelApp,
    NimDuelApp,
    ReversiLightApp,
    TicTacToeApp,
    load_duel_sprite,
)


class DuelGamesTest(unittest.TestCase):
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
            TicTacToeApp(self.screen, audio_enabled=False),
            ConnectFourApp(self.screen, audio_enabled=False),
            DotsBoxesApp(self.screen, audio_enabled=False),
            MemoryDuelApp(self.screen, audio_enabled=False),
            NimDuelApp(self.screen, audio_enabled=False),
            ReversiLightApp(self.screen, audio_enabled=False),
        )

    @staticmethod
    def _playing(game) -> None:
        game.start(99.0)
        game._begin(100.0)
        game.handoff_until = 0.0

    @staticmethod
    def _fire(game, point, now):
        game.handoff_until = 0.0
        return game.handle_shot(point, now)

    def test_all_six_duels_start_and_return_to_menu_with_pistol(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self.assertEqual(
                    tuple(label for label, _ in game.result_values),
                    ("SPIELER 1", "SPIELER 2"),
                )
                game.start(100.0)
                self.assertEqual(game.handle_shot(game.start_card.center, 101.0), "handled")
                self.assertEqual(game.state, "playing")
                self.assertEqual(game.handle_shot(game.menu_button.center, 102.0), "menu")

    def test_player_handoff_allows_two_seconds_for_safe_pistol_transfer(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self._playing(game)
                game.current_player = 0
                game._next_player(100.0)
                self.assertEqual(game.current_player, 1)
                self.assertEqual(game.handoff_text, "WAFFE AN SPIELER 2 WEITERGEBEN")
                self.assertGreaterEqual(game.handoff_until - 100.0, 2.0)
                self.assertEqual(game.handle_shot((512, 384), 101.9), "handled")

    def test_bonus_turn_notice_is_shorter_because_pistol_stays_with_player(self) -> None:
        game = DotsBoxesApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.current_player = 0
        game._next_player(100.0, bonus=True)
        self.assertEqual(game.current_player, 0)
        self.assertIn("NOCH EINMAL", game.handoff_text)
        self.assertLess(game.handoff_until - 100.0, game.HANDOFF_SECONDS)

    def test_no_duel_can_open_a_top_ten_or_name_entry(self) -> None:
        overlay = ArcadeLeaderboardOverlay(self.screen, None)
        for game in self._games():
            with self.subTest(game=game.name):
                game.state = "game_over"
                self.assertFalse(overlay.prepare("duel", game))
                self.assertFalse(overlay.active)
                self.assertIsNone(overlay.candidate)

    def test_tic_tac_toe_detects_win_and_occupied_field(self) -> None:
        game = TicTacToeApp(self.screen, audio_enabled=False)
        self._playing(game)
        cells = game._cells()
        for index, now in zip((0, 3, 1, 4, 2), (101, 102, 103, 104, 105)):
            self.assertEqual(self._fire(game, cells[index].center, now), "hit")
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(game.winner, 0)
        self.assertEqual(game.handle_shot(cells[8].center, 106.0), "handled")
        game.update(107.9)
        self.assertEqual(game.state, "celebrating")
        game.update(108.0)
        self.assertEqual(game.state, "game_over")

        game.start(110.0)
        game._begin(111.0)
        self.assertEqual(self._fire(game, cells[0].center, 112.0), "hit")
        self.assertEqual(self._fire(game, cells[0].center, 113.0), "miss")

    def test_tic_tac_toe_full_draw_reaches_result(self) -> None:
        game = TicTacToeApp(self.screen, audio_enabled=False)
        self._playing(game)
        cells = game._cells()
        for turn, index in enumerate((0, 1, 2, 4, 3, 5, 7, 6, 8), start=1):
            self.assertEqual(self._fire(game, cells[index].center, 100.0 + turn), "hit")
        self.assertEqual(game.state, "celebrating")
        self.assertIsNone(game.winner)
        self.assertEqual(game.finish_reason, "UNENTSCHIEDEN")

    def test_connect_four_stacks_and_detects_vertical_four(self) -> None:
        game = ConnectFourApp(self.screen, audio_enabled=False)
        self._playing(game)
        columns = game._column_rects()
        for column, now in zip((0, 1, 0, 1, 0, 1, 0), range(101, 108)):
            self.assertEqual(self._fire(game, columns[column].center, float(now)), "hit")
        self.assertEqual(game.winner, 0)
        self.assertEqual(game.state, "celebrating")
        self.assertEqual([game.board[row][0] for row in range(2, 6)], [0, 0, 0, 0])

    def test_connect_four_full_board_draw_reaches_result(self) -> None:
        game = ConnectFourApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.board = [
            [-1, 1, 1, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1],
            [0, 1, 0, 0, 0, 1, 1],
            [0, 0, 1, 1, 1, 0, 0],
            [0, 1, 1, 0, 0, 0, 1],
            [1, 0, 1, 0, 1, 1, 1],
        ]
        game.current_player = 0
        self.assertEqual(self._fire(game, game._column_rects()[0].center, 101.0), "hit")
        self.assertEqual(game.state, "celebrating")
        self.assertIsNone(game.winner)
        self.assertEqual(game.finish_reason, "UNENTSCHIEDEN")

    def test_dots_boxes_awards_box_and_bonus_turn(self) -> None:
        game = DotsBoxesApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.horizontal[(0, 0)] = 0
        game.horizontal[(1, 0)] = 1
        game.vertical[(0, 0)] = 0
        game.current_player = 1
        target = next(rect for key, rect in game._edge_targets() if key == ("v", 0, 1))
        self.assertEqual(self._fire(game, target.center, 101.0), "hit")
        self.assertEqual(game.boxes[(0, 0)], 1)
        self.assertEqual(game.scores, [0, 1])
        self.assertEqual(game.current_player, 1)
        self.assertIn("NOCH EINMAL", game.handoff_text)

    def test_dots_boxes_final_edge_finishes_complete_board(self) -> None:
        game = DotsBoxesApp(self.screen, audio_enabled=False)
        self._playing(game)
        missing = (0, 0)
        game.horizontal = {
            (row, col): 0
            for row in range(game.rows)
            for col in range(game.cols - 1)
            if (row, col) != missing
        }
        game.vertical = {
            (row, col): 1
            for row in range(game.rows - 1)
            for col in range(game.cols)
        }
        game.boxes = {
            (row, col): (row + col) % 2
            for row in range(game.rows - 1)
            for col in range(game.cols - 1)
            if (row, col) != (0, 0)
        }
        game.scores = [6, 5]
        game.current_player = 0
        target = next(rect for key, rect in game._edge_targets() if key == ("h", 0, 0))
        self.assertEqual(self._fire(game, target.center, 101.0), "hit")
        self.assertEqual(len(game.boxes), 12)
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(game.scores, [7, 5])
        self.assertEqual(game.winner, 0)

    def test_memory_pair_gives_bonus_and_mismatch_switches_player(self) -> None:
        game = MemoryDuelApp(self.screen, audio_enabled=False, random_seed=1)
        self._playing(game)
        game.cards = [0, 0, 1, 2, 3, 4, 5, 6, 7, 1, 2, 3, 4, 5, 6, 7]
        cards = game._card_rects()
        self.assertEqual(self._fire(game, cards[0].center, 101.0), "hit")
        self.assertEqual(self._fire(game, cards[1].center, 102.0), "hit")
        self.assertEqual(game.scores[0], 1)
        game.update(103.0)
        self.assertEqual(game.current_player, 0)

        game.handoff_until = 0.0
        self.assertEqual(self._fire(game, cards[2].center, 104.0), "hit")
        self.assertEqual(self._fire(game, cards[3].center, 105.0), "hit")
        game.update(107.0)
        self.assertEqual(game.current_player, 1)

    def test_memory_all_pairs_finish_without_stuck_cards(self) -> None:
        game = MemoryDuelApp(self.screen, audio_enabled=False, random_seed=1)
        self._playing(game)
        game.cards = [value for value in range(8) for _ in range(2)]
        cards = game._card_rects()
        now = 100.0
        for first in range(0, 16, 2):
            now += 1.0
            self.assertEqual(self._fire(game, cards[first].center, now), "hit")
            now += 1.0
            self.assertEqual(self._fire(game, cards[first + 1].center, now), "hit")
            if game.state == "playing":
                game.update(now + 1.0)
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(len(game.matched), 16)
        self.assertEqual(game.winner, 0)

    def test_nim_disables_choices_larger_than_remaining_items(self) -> None:
        game = NimDuelApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.remaining_items = 1
        game.current_player = 1
        self.assertEqual(self._fire(game, game.choice_rects[2].center, 101.0), "miss")
        self.assertEqual(game.remaining_items, 1)
        self.assertEqual(game.state, "playing")
        self.assertEqual(game.current_player, 1)
        self.assertIn("NUR NOCH 1 STAB", game.handoff_text)
        self.assertEqual(self._fire(game, game.choice_rects[0].center, 102.0), "hit")
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(game.winner, 1)

    def test_nim_complete_game_finishes_on_exact_last_choice(self) -> None:
        game = NimDuelApp(self.screen, audio_enabled=False)
        self._playing(game)
        for turn in range(5):
            self.assertEqual(
                self._fire(game, game.choice_rects[2].center, 101.0 + turn),
                "hit",
            )
        self.assertEqual(game.remaining_items, 0)
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(game.winner, 0)

    def test_all_duels_keep_final_board_visible_for_three_seconds(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self._playing(game)
                game._finish(0, "SPIELER 1 GEWINNT", 200.0)
                self.assertEqual(game.state, "celebrating")
                game.draw(202.99)
                game.update(202.99)
                self.assertEqual(game.state, "celebrating")
                game.update(203.0)
                self.assertEqual(game.state, "game_over")

    def test_long_duel_titles_do_not_overlap_center_player_banner(self) -> None:
        for game_type in (MemoryDuelApp, ReversiLightApp):
            with self.subTest(game=game_type.__name__):
                game = game_type(self.screen, audio_enabled=False)
                game.start(100.0)
                game._begin(101.0)
                game._finish(0, "SPIELER 1 GEWINNT", 102.0)
                title_font = game.font_large
                if title_font.size(game.title)[0] > 290:
                    title_font = game.font
                if title_font.size(game.title)[0] > 290:
                    title_font = game.font_small
                self.assertLessEqual(28 + title_font.size(game.title)[0], 318)

    def test_win_banner_keeps_both_final_score_panels_visible(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self._playing(game)
                game._finish(0, "SPIELER 1 GEWINNT", 200.0)
                banner = pygame.Rect(0, 0, 390, 64)
                banner.midtop = (self.screen.get_width() // 2, 14)
                for player in range(2):
                    self.assertFalse(banner.colliderect(game._score_panel_rect(player)))
                self.assertLess(banner.right, game.menu_button.left)
                title_font = game.font_large
                if title_font.size(game.title)[0] > 290:
                    title_font = game.font
                if title_font.size(game.title)[0] > 290:
                    title_font = game.font_small
                title = title_font.render(game.title, True, SAFE_CYAN)
                self.assertLess(title.get_rect(topleft=(28, 24)).right, banner.left)

    def test_draw_and_score_panels_cover_long_and_tied_results(self) -> None:
        game = DotsBoxesApp(self.screen, audio_enabled=False)
        self._playing(game)
        game.scores = [12, 12]
        for player in range(2):
            panel = game._score_panel_rect(player)
            label = f"SPIELER {player + 1} · KÄSTCHEN 12"
            font = game._score_font(label, panel)
            self.assertLessEqual(font.size(label)[0], panel.width - 18)

        game._finish(None, "UNENTSCHIEDEN", 200.0)
        game.draw(201.0)
        self.assertEqual(self.screen.get_at((self.screen.get_width() // 2, 14))[:3], SAFE_CYAN)

    def test_reversi_flips_enclosed_stone(self) -> None:
        game = ReversiLightApp(self.screen, audio_enabled=False)
        self._playing(game)
        valid = game._valid_moves(0)
        self.assertIn((1, 3), valid)
        rect = game._cell_rects()[1 * 6 + 3]
        self.assertEqual(self._fire(game, rect.center, 101.0), "hit")
        self.assertEqual(game.board[1][3], 0)
        self.assertEqual(game.board[2][3], 0)
        self.assertEqual(game.scores, [4, 1])

    def test_reversi_complete_game_always_reaches_result(self) -> None:
        game = ReversiLightApp(self.screen, audio_enabled=False)
        self._playing(game)
        now = 100.0
        for _ in range(60):
            if game.state != "playing":
                break
            valid = game._valid_moves(game.current_player)
            self.assertTrue(valid)
            row, col = next(iter(valid))
            now += 1.0
            self.assertEqual(
                self._fire(game, game._cell_rects()[row * 6 + col].center, now),
                "hit",
            )
        self.assertEqual(game.state, "celebrating")
        self.assertEqual(sum(game.scores), sum(value >= 0 for row in game.board for value in row))

    def test_all_duel_screens_are_laser_neutral(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self._playing(game)
                if isinstance(game, TicTacToeApp):
                    game.board[:2] = [0, 1]
                elif isinstance(game, ConnectFourApp):
                    game.board[5][:2] = [0, 1]
                elif isinstance(game, DotsBoxesApp):
                    game.horizontal[(0, 0)] = 0
                    game.vertical[(0, 0)] = 1
                    game.boxes[(0, 0)] = 0
                elif isinstance(game, MemoryDuelApp):
                    game.revealed = list(range(8))
                game.draw(101.0)
                rgb = pygame.surfarray.array3d(self.screen).astype("int16")
                red_excess = rgb[:, :, 0] - rgb[:, :, 1:].max(axis=2)
                self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_all_duel_3d_sprites_load_with_visible_alpha_and_are_cached(self) -> None:
        layouts = {
            "tic_tac_toe": 2,
            "connect_four": 2,
            "dots_boxes": 3,
            "memory": 8,
            "nim": 1,
            "reversi": 2,
        }
        for name, count in layouts.items():
            for index in range(count):
                with self.subTest(name=name, index=index):
                    sprite = load_duel_sprite(name, index, (96, 88))
                    alpha = pygame.surfarray.array_alpha(sprite)
                    self.assertGreater(int((alpha >= 8).sum()), 120)
                    self.assertIs(sprite, load_duel_sprite(name, index, (96, 88)))

    def test_every_duel_ready_screen_has_theme_art_and_stays_laser_neutral(self) -> None:
        for game in self._games():
            with self.subTest(game=game.name):
                self.assertTrue(game.ready_art)
                game.start(100.0)
                game.draw(100.0)
                rgb = pygame.surfarray.array3d(self.screen).astype("int16")
                red_excess = rgb[:, :, 0] - rgb[:, :, 1:].max(axis=2)
                self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))


if __name__ == "__main__":
    unittest.main()
