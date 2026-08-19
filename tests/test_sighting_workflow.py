from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import cv2
import numpy as np
import pygame

from laser_arcade.config import Settings
from laser_arcade.diagnostic_ui import LaserDiagnosticUI
from laser_arcade.laser_tracker import LaserDetection
from laser_arcade.weapon_calibration import fit_weapon_calibration


class DummyTracker:
    actual_width = 640
    actual_height = 360
    actual_fps = 30.0
    processing_fps = 30.0

    def __init__(self) -> None:
        self.reset_count = 0
        self.moorhuhn_filter_enabled = False

    def reset_state(self) -> None:
        self.reset_count += 1

    def set_moorhuhn_filter(self, enabled: bool) -> None:
        self.moorhuhn_filter_enabled = enabled


class SightingWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1024, 768))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    @staticmethod
    def _new_ui(weapon_calibration_path: Path | None = None) -> LaserDiagnosticUI:
        ui = LaserDiagnosticUI(
            pygame.Surface((1024, 768)),
            Settings(),
            DummyTracker(),
            weapon_calibration_path=weapon_calibration_path,
            target_history_path=None,
            water_alarm_leaderboard_path=None,
            arcade_leaderboard_path=None,
        )
        ui.aligner.phase = "success"
        ui.aligner.homography = np.eye(3, dtype=np.float32)
        ui.view_mode = "target"
        ui.armed_at = 0.0
        return ui

    @staticmethod
    def _fire(
        ui: LaserDiagnosticUI,
        point: tuple[int, int],
        now: float,
        *,
        peak_red_excess: int = 110,
        peak_delta: int = 140,
    ) -> None:
        ui.update(
            LaserDetection(
                point=point,
                area=4.0,
                confidence=0.95,
                frame_ts=now,
                mask_preview=None,
                frame_preview=None,
                shot=True,
                peak_red_excess=peak_red_excess,
                peak_delta=peak_delta,
            ),
            now,
        )

    def test_five_center_and_three_per_corner_reach_final_evaluation(self) -> None:
        ui = self._new_ui()

        expected_counts = [5, 3, 3, 3, 3]
        now = 100.0
        for step, required in enumerate(expected_counts):
            target = ui._sighting_stages()[step][3]
            for shot_index in range(required):
                point = (target[0] + shot_index, target[1] - shot_index)
                self._fire(ui, point, now)
                now += 1.0

            self.assertEqual(len(ui.completed_groups[step]), required)
            if step < 4:
                self.assertEqual(ui.sighting_phase, "evaluation")
                # Das gesamte Auswertungsfenster dient als große Weiter-Fläche.
                self._fire(ui, ui._stage_evaluation_rect().center, now)
                now += 1.0
                ui.armed_at = 0.0
            else:
                # Der letzte Schuss öffnet ohne weiteren Bedientreffer die
                # Gesamtauswertung.
                self.assertEqual(ui.sighting_phase, "complete")

        self.assertEqual(ui.sighting_phase, "complete")
        self.assertEqual([len(group) for group in ui.completed_groups], expected_counts)
        self.assertEqual(len(ui.shots), 17)
        self.assertTrue(ui.weapon_calibration.active)

        self._fire(ui, ui._complete_evaluation_rect().center, now)
        self.assertEqual(ui.sighting_phase, "shooting")
        self.assertEqual(len(ui.shots), 0)

    def test_sighting_calibrates_weapon_offset_and_persists_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weapon_calibration.json"
            ui = self._new_ui(path)
            deviation = (80, -45)
            now = 100.0

            for step, required in enumerate((5, 3, 3, 3, 3)):
                target = ui._sighting_stages()[step][3]
                for _ in range(required):
                    self._fire(
                        ui,
                        (target[0] + deviation[0], target[1] + deviation[1]),
                        now,
                    )
                    now += 1.0
                if step < 4:
                    self._fire(ui, ui._stage_evaluation_rect().center, now)
                    now += 1.0
                    ui.armed_at = 0.0

            self.assertTrue(path.exists())
            self.assertAlmostEqual(ui.weapon_calibration.offset_x, -80.0)
            self.assertAlmostEqual(ui.weapon_calibration.offset_y, 45.0)

            reloaded = self._new_ui(path)
            self.assertTrue(reloaded.weapon_calibration.active)
            self.assertEqual(
                reloaded.weapon_calibration.apply((580, 339), (1024, 768)),
                (500, 384),
            )

            # Die gespeicherte Korrektur wird vor der Bedienlogik angewendet:
            # Der rohe Laserpunkt darf abweichen und trifft trotzdem die Karte.
            reloaded._show_menu()
            reloaded.armed_at = 0.0
            cans_card = reloaded._menu_entries()[1][0]
            raw_aim = (
                cans_card.centerx + deviation[0],
                cans_card.centery + deviation[1],
            )
            self._fire(reloaded, raw_aim, now + 1.0)
            self.assertEqual(reloaded.view_mode, "cans")

            # Beim erneuten Einschießen bleiben dagegen die Rohkoordinaten in
            # der Auswertung erhalten, damit nicht die alte Korrektur vermessen wird.
            reloaded._start_sighting()
            reloaded.armed_at = 0.0
            raw_target = (600, 420)
            self._fire(reloaded, raw_target, now + 2.0)
            self.assertEqual(reloaded.stage_shots[-1].screen_point, raw_target)

    def test_inconsistent_sighting_groups_are_rejected(self) -> None:
        targets = [(512, 404), (122, 184), (902, 184), (902, 599), (122, 599)]
        contradictory_offsets = [(0, 0), (180, 0), (-180, 0), (0, 170), (0, -170)]
        groups = [
            [(target[0] - offset[0], target[1] - offset[1])] * 3
            for target, offset in zip(targets, contradictory_offsets)
        ]

        with self.assertRaisesRegex(ValueError, "zu unterschiedlich"):
            fit_weapon_calibration(groups, targets, (1024, 768))

    def test_every_visible_control_can_be_operated_by_laser(self) -> None:
        ui = self._new_ui()
        target = ui._sighting_stages()[0][3]
        self._fire(ui, target, 100.0)
        self.assertEqual(len(ui.shots), 1)

        self._fire(ui, ui.target_clear_button.center, 101.0)
        self.assertEqual(len(ui.shots), 0)
        ui.armed_at = 0.0

        self._fire(ui, ui.target_menu_button.center, 102.0)
        self.assertEqual(ui.view_mode, "menu")
        ui.armed_at = 0.0

        self._fire(ui, ui.menu_settings_button.center, 103.0)
        self.assertEqual(ui.view_mode, "diagnostic")
        ui.armed_at = 0.0

        self._fire(ui, ui.diagnostic_sighting_button.center, 103.5)
        self.assertEqual(ui.view_mode, "target")
        ui.armed_at = 0.0

        self._fire(ui, ui.target_live_button.center, 104.0)
        self.assertEqual(ui.view_mode, "diagnostic")
        ui.armed_at = 0.0

        self._fire(ui, ui.diagnostic_target_button.center, 105.0)
        self.assertEqual(ui.view_mode, "menu")
        ui.armed_at = 0.0

        self._fire(ui, ui.menu_settings_button.center, 106.0)
        ui.armed_at = 0.0
        self._fire(ui, ui.diagnostic_sighting_button.center, 106.5)
        ui.armed_at = 0.0
        self._fire(ui, ui.target_align_button.center, 107.0)
        self.assertTrue(ui.aligner.active)

    def test_camera_view_program_close_can_be_operated_by_pistol(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        self._fire(ui, ui.menu_settings_button.center, 100.0)
        self.assertEqual(ui.view_mode, "diagnostic")
        ui.armed_at = 0.0

        self._fire(ui, ui.diagnostic_close_button.center, 101.0)
        self.assertTrue(ui.close_pin_active)
        self.assertFalse(ui.close_requested)

        buttons = dict(ui._close_pin_buttons())
        ui.armed_at = 0.0
        for index, digit in enumerate("1919", start=1):
            self._fire(ui, buttons[digit].center, 101.0 + index)

        self.assertFalse(ui.close_pin_active)
        self.assertTrue(ui.close_requested)
        self.assertFalse(ui.handle_event(pygame.event.Event(pygame.NOEVENT)))

        self.assertEqual(ui.diagnostic_close_button.top, ui.align_button.top)
        self.assertLessEqual(ui.diagnostic_close_button.bottom, ui.screen.get_height())

    def test_main_menu_only_shows_one_settings_entry_for_camera_tools(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._draw_main_menu()

        self.assertGreater(ui.menu_settings_button.width, 0)
        self.assertEqual(ui.menu_sighting_button.size, (0, 0))
        self.assertEqual(ui.menu_camera_button.size, (0, 0))
        self.assertEqual(ui.menu_align_button.size, (0, 0))
        self.assertFalse(ui.menu_settings_button.colliderect(ui._menu_entries()[0][0]))

        ui.armed_at = 0.0
        self._fire(ui, ui.menu_settings_button.center, 100.0)
        self.assertEqual(ui.view_mode, "diagnostic")
        ui.armed_at = 0.0
        self._fire(ui, ui.diagnostic_settings_button.center, 101.0)
        self.assertTrue(ui.camera_settings_open)

    def test_main_menu_uses_laser_neutral_photorealistic_title_sign(self) -> None:
        ui = self._new_ui()
        self.assertIsNotNone(ui.menu_title_image)
        self.assertEqual(ui.menu_title_image.get_size(), ui.menu_title_rect.size)
        rgba = pygame.surfarray.array3d(ui.menu_title_image).astype(np.int16)
        alpha = pygame.surfarray.array_alpha(ui.menu_title_image)
        red_excess = rgba[:, :, 0] - np.maximum(rgba[:, :, 1], rgba[:, :, 2])
        self.assertFalse(bool(((rgba[:, :, 0] >= 70) & (red_excess >= 28) & (alpha >= 10)).any()))
        self.assertTrue(ui._easter_title_rect().contains(ui.menu_title_rect))

    def test_camera_hub_buttons_form_one_large_even_bottom_row(self) -> None:
        ui = self._new_ui()
        buttons = (
            ui.diagnostic_target_button,
            ui.diagnostic_settings_button,
            ui.diagnostic_sighting_button,
            ui.align_button,
            ui.diagnostic_close_button,
        )
        self.assertTrue(all(button.width == 188 for button in buttons))
        self.assertTrue(all(button.height == 58 for button in buttons))
        self.assertTrue(all(ui.screen.get_rect().contains(button) for button in buttons))
        self.assertEqual(len({button.top for button in buttons}), 1)
        for first, second in zip(buttons, buttons[1:]):
            self.assertLess(first.right, second.left)

    def test_camera_hub_records_and_draws_last_laser_shot(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._toggle_view()
        ui.armed_at = 0.0
        point = (420, 360)

        self._fire(ui, point, 100.0)

        self.assertEqual(ui.view_mode, "diagnostic")
        self.assertEqual(len(ui.shots), 1)
        self.assertEqual(ui.shots[-1].screen_point, point)
        ui._draw_side_panel()
        target = pygame.Rect(794, 438, 196, 78)
        px = target.x + int(point[0] * target.width / ui.screen.get_width())
        py = target.y + int(point[1] * target.height / ui.screen.get_height())
        neighborhood = [
            tuple(ui.screen.get_at((px + dx, py + dy))[:3])
            for dx in range(-12, 13)
            for dy in range(-12, 13)
            if ui.screen.get_rect().collidepoint(px + dx, py + dy)
        ]
        self.assertIn(ui.CYAN, neighborhood)

    def test_camera_hub_impulse_mask_holds_last_confirmed_peak(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._toggle_view()
        ui.armed_at = 0.0
        peak_mask = np.zeros((112, 200, 3), dtype=np.uint8)
        peak_mask[44:68, 82:118, :] = 255
        ui.update(
            LaserDetection(
                point=(420, 300), area=17.0, confidence=0.93, frame_ts=100.0,
                mask_preview=peak_mask, frame_preview=None, shot=True,
                peak_red_excess=123, peak_delta=147,
            ),
            100.0,
        )
        quiet_mask = np.zeros_like(peak_mask)
        ui.update(
            LaserDetection(
                point=None, area=0.0, confidence=0.0, frame_ts=100.1,
                mask_preview=quiet_mask, frame_preview=None, shot=False,
                peak_red_excess=0, peak_delta=0,
            ),
            100.1,
        )

        self.assertEqual(ui.last_peak_detection.peak_red_excess, 123)
        self.assertEqual(ui.last_peak_detection.peak_delta, 147)
        np.testing.assert_array_equal(ui.last_peak_mask_rgb, peak_mask)
        np.testing.assert_array_equal(ui.last_mask_rgb, quiet_mask)
        ui._draw_side_panel()
        self.assertEqual(tuple(ui.screen.get_at((892, 194))[:3]), (255, 255, 255))

        ui.clear_shots()
        self.assertIsNone(ui.last_peak_detection)
        self.assertIsNone(ui.last_peak_mask_rgb)

    def test_menu_mice_use_real_alpha_frames_and_remain_laser_neutral(self) -> None:
        ui = self._new_ui()
        self.assertEqual(len(ui.menu_mouse_source_frames), 4)
        self.assertEqual(len(ui.menu_mouse_behavior_frames), 4)
        for frame in ui.menu_mouse_source_frames + ui.menu_mouse_behavior_frames:
            alpha = pygame.surfarray.array_alpha(frame)
            rgb = pygame.surfarray.array3d(frame).astype(np.int16)
            self.assertGreater(int((alpha >= 10).sum()), 500)
            self.assertGreater(int((alpha < 10).sum()), 500)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(
                bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28) & (alpha >= 10)).any())
            )

    def test_menu_mouse_hit_uses_visible_shape_and_respawns_elsewhere(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        mouse = ui.menu_mice[0]
        ui._spawn_menu_mouse(mouse, 100.0)
        mouse.x = 420.0
        mouse.y = 730.0
        mouse.target_x = 620.0
        mouse.target_y = 700.0
        ui._draw_menu_mice(100.1)
        self.assertTrue(mouse.active)
        self.assertIsNotNone(mouse.current_rect)

        mask = mouse.current_mask
        self.assertIsNotNone(mask)
        largest = max(mask.get_bounding_rects(), key=lambda rect: rect.width * rect.height)
        point = (
            mouse.current_rect.left + largest.centerx,
            mouse.current_rect.top + largest.centery,
        )
        self.assertTrue(ui._hit_menu_mouse(point, 100.2))
        self.assertFalse(mouse.active)
        self.assertGreater(mouse.spawn_at, 100.2)
        self.assertEqual(ui.menu_mouse_hits, 1)

    def test_menu_mice_move_in_depth_pause_and_exit_behind_furniture(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        mouse = ui.menu_mice[0]
        ui._spawn_menu_mouse(mouse, 100.0)
        mouse.x, mouse.y = 500.0, 742.0
        mouse.target_x, mouse.target_y = 360.0, 686.0
        mouse.last_update = 100.0

        ui._update_menu_mice(100.1)
        self.assertLess(mouse.x, 500.0)
        self.assertLess(mouse.y, 742.0)

        mouse.state = "sitting"
        mouse.state_until = 105.0
        still = mouse.x, mouse.y
        ui._update_menu_mice(101.0)
        self.assertEqual((mouse.x, mouse.y), still)

        mouse.state = "moving"
        mouse.exiting = True
        mouse.x, mouse.y = 30.0, 744.0
        mouse.target_x, mouse.target_y = -40.0, 744.0
        mouse.speed = 1000.0
        mouse.last_update = 102.0
        ui._update_menu_mice(102.1)
        self.assertFalse(mouse.active)
        self.assertGreaterEqual(mouse.spawn_at, 120.1)

    def test_menu_mouse_is_a_rare_single_background_detail(self) -> None:
        ui = self._new_ui()
        self.assertEqual(len(ui.menu_mice), 1)
        mouse = ui.menu_mice[0]
        ui._spawn_menu_mouse(mouse, 100.0)
        mouse.current_rect = pygame.Rect(400, 680, 80, 45)
        mouse.current_mask = pygame.mask.Mask((80, 45), fill=True)
        self.assertTrue(ui._hit_menu_mouse(mouse.current_rect.center, 101.0))
        self.assertGreaterEqual(mouse.spawn_at, 121.0)

    def test_menu_mouse_destinations_stay_inside_perspective_floor(self) -> None:
        ui = self._new_ui()
        for _ in range(100):
            x, y = ui._random_menu_floor_point()
            left, right = ui._menu_floor_bounds(y)
            self.assertGreaterEqual(y, 684.0)
            self.assertLessEqual(y, 747.0)
            self.assertGreater(x, left)
            self.assertLess(x, right)

    def test_left_mouse_exit_leads_to_visible_mouse_hole(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        self.assertIsNone(ui.menu_mouse_hole_image)
        mouse = ui.menu_mice[0]
        ui._spawn_menu_mouse(mouse, 100.0)
        mouse.x, mouse.y = 260.0, 710.0
        mouse.pauses_used = 1
        mouse.depart_at = 99.0
        ui._choose_menu_mouse_destination(mouse, 101.0)
        self.assertTrue(mouse.exiting)
        self.assertEqual(mouse.state, "approaching_hole")
        self.assertEqual((mouse.target_x, mouse.target_y), (190.0, 730.0))
        self.assertEqual(ui.menu_mouse_hole_rect, pygame.Rect(130, 678, 33, 52))
        self.assertEqual(mouse.target_y, ui.menu_mouse_hole_rect.bottom)

        mouse.x, mouse.y = mouse.target_x, mouse.target_y
        mouse.last_update = 102.0
        ui._update_menu_mice(102.1)
        self.assertEqual(mouse.state, "entering")
        self.assertEqual((mouse.target_x, mouse.target_y), (82.0, 730.0))
        self.assertLess(mouse.target_x, ui.menu_mouse_hole_rect.left)

    def test_menu_mouse_tunnel_hides_pixels_outside_real_opening(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        mouse = ui.menu_mice[0]
        mouse.active = True
        mouse.state = "entering"
        mouse.x, mouse.y = 142.0, 730.0
        mouse.target_x, mouse.target_y = 82.0, 730.0
        mouse.last_update = 100.0
        ui._draw_menu_mice(100.0)

        self.assertFalse(ui._menu_mouse_visible_at(mouse, (132, 681)))
        self.assertTrue(ui._menu_mouse_visible_at(mouse, (146, 700)))
        self.assertTrue(ui._menu_mouse_visible_at(mouse, (170, 710)))
        self.assertFalse(ui._menu_mouse_visible_at(mouse, (120, 710)))
        # Zwischen innerer Öffnung und freiem Boden darf keine senkrechte,
        # unsichtbare Pixelspalte mehr liegen.
        self.assertTrue(
            all(ui._menu_mouse_visible_at(mouse, (x, 710)) for x in range(154, 168))
        )

    def test_program_close_rejects_wrong_pin_and_can_be_cancelled(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._request_program_close()
        buttons = dict(ui._close_pin_buttons())
        ui.armed_at = 0.0

        for index in range(4):
            self._fire(ui, buttons["1"].center, 100.0 + index)
        self.assertTrue(ui.close_pin_active)
        self.assertFalse(ui.close_requested)
        self.assertEqual(ui.close_pin_digits, "")
        self.assertIn("PIN FALSCH", ui.close_pin_message)

        self._fire(ui, buttons["ABBRECHEN"].center, 105.0)
        self.assertFalse(ui.close_pin_active)
        self.assertFalse(ui.close_requested)
        self.assertEqual(ui.view_mode, "menu")

    def test_escape_opens_pin_instead_of_bypassing_program_close(self) -> None:
        ui = self._new_ui()
        ui._show_menu()

        self.assertTrue(
            ui.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode=""))
        )
        self.assertTrue(ui.close_pin_active)
        self.assertFalse(ui.close_requested)

    def test_all_six_game_cards_are_available_and_fit(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        entries = ui._menu_entries()
        self.assertEqual(entries[0][1], "WASSER-ALARM")
        self.assertEqual(entries[3][1], "TONTAUBENSCHIEßEN")
        for rect, name, _, available in entries:
            fitted = ui._fitted_card_font(name, rect.width - 36)
            self.assertLessEqual(fitted.size(name)[0], rect.width - 36)
            self.assertTrue(available)

    def test_small_arrows_switch_between_all_three_game_pages_with_pistol(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        self.assertEqual(ui.menu_page, 0)
        self.assertEqual(ui.menu_page_count, 3)
        page_one_rects = [rect for rect, *_ in ui._menu_entries()]
        self.assertFalse(any(ui.menu_next_hit_rect.colliderect(rect) for rect in page_one_rects))

        self._fire(ui, ui.menu_next_button.center, 100.0)
        self.assertEqual(ui.view_mode, "menu")
        self.assertEqual(ui.menu_page, 1)
        self.assertEqual(
            [name for _, name, _, _ in ui._menu_entries()],
            [
                "BALLONJAGD",
                "ALIEN-ALARM",
                "STERNEJAGD",
                "RECHENDUELL",
                "FARBENSPIEL",
                "SCHATZSUCHE",
            ],
        )
        for rect, name, _, available in ui._menu_entries():
            self.assertTrue(available)
            fitted = ui._fitted_card_font(name, rect.width - 36)
            self.assertLessEqual(fitted.size(name)[0], rect.width - 36)

        ui.armed_at = 0.0
        self._fire(ui, ui.menu_next_button.center, 101.0)
        self.assertEqual(ui.menu_page, 2)
        self.assertEqual(
            [name for _, name, _, _ in ui._menu_entries()],
            [
                "TIC-TAC-TOE",
                "4 GEWINNT",
                "KÄSEKÄSTCHEN",
                "MEMORY-DUELL",
                "NIM-DUELL",
                "REVERSI LIGHT",
            ],
        )

        ui.armed_at = 0.0
        self._fire(ui, ui.menu_previous_button.center, 102.0)
        self.assertEqual(ui.menu_page, 1)
        ui.armed_at = 0.0
        self._fire(ui, ui.menu_previous_button.center, 103.0)
        self.assertEqual(ui.menu_page, 0)

    def test_all_third_page_duels_open_and_have_no_leaderboard(self) -> None:
        ui = self._new_ui()
        ui.menu_page = 2
        self.assertEqual(ui._menu_page_heading(), "")
        expected = (
            (0, "tictactoe", ui.tic_tac_toe_game),
            (1, "connect4", ui.connect_four_game),
            (2, "dots", ui.dots_boxes_game),
            (3, "memory_duel", ui.memory_duel_game),
            (4, "nim", ui.nim_duel_game),
            (5, "reversi", ui.reversi_light_game),
        )
        now = 200.0
        for card_index, view_mode, game in expected:
            ui._show_menu()
            ui.menu_page = 2
            ui.armed_at = 0.0
            self._fire(ui, ui._menu_entries()[card_index][0].center, now)
            self.assertEqual(ui.view_mode, view_mode)
            self.assertEqual(game.state, "ready")
            self.assertFalse(ui.arcade_leaderboard.is_active_for(view_mode))
            self.assertFalse(ui.arcade_leaderboard.prepare(view_mode, game))
            self.assertFalse(ui.arcade_leaderboard.active)

            game.state = "game_over"
            game.finish_reason = "TESTERGEBNIS"
            ui.standard_game_states[id(game)] = "playing"
            ui.update(
                LaserDetection(
                    point=None,
                    area=0.0,
                    confidence=0.0,
                    frame_ts=now + 0.2,
                    mask_preview=None,
                    frame_preview=None,
                    shot=False,
                ),
                now + 0.2,
            )
            self.assertFalse(ui.arcade_leaderboard.is_active_for(view_mode))
            now += 1.0

    def test_menu_pages_have_no_redundant_instruction_heading(self) -> None:
        ui = self._new_ui()
        for page in range(ui.menu_page_count):
            ui.menu_page = page
            self.assertEqual(ui._menu_page_heading(), "")

    def test_camera_hits_beside_projection_switch_pages_only_in_overview(self) -> None:
        ui = self._new_ui()
        camera_quad = np.float32(
            ((100, 40), (540, 40), (540, 320), (100, 320))
        )
        screen_quad = np.float32(
            ((0, 0), (1023, 0), (1023, 767), (0, 767))
        )
        ui.aligner.homography = cv2.getPerspectiveTransform(camera_quad, screen_quad)
        ui._show_menu()
        ui.armed_at = 0.0

        # Rechts neben der Leinwand öffnet Seite zwei.
        self._fire(ui, (600, 180), 100.0)
        self.assertEqual(ui.menu_page, 1)

        # Ein Treffer oberhalb der Leinwand ist keine Seitengeste.
        ui.armed_at = 0.0
        self._fire(ui, (320, 10), 101.0)
        self.assertEqual(ui.menu_page, 1)

        # Links neben der Leinwand geht es zurück auf Seite eins.
        ui.armed_at = 0.0
        self._fire(ui, (40, 180), 102.0)
        self.assertEqual(ui.menu_page, 0)

        # Außerhalb eines Spiels darf derselbe Kameratreffer nichts auslösen.
        ui._select_game("BALLONJAGD", True)
        ui.armed_at = 0.0
        self._fire(ui, (600, 180), 103.0)
        self.assertEqual(ui.view_mode, "balloons")
        self.assertEqual(ui.balloon_game.state, "ready")
        self.assertEqual(ui.balloon_game.shots, 0)

    def test_game_page_arrow_and_new_game_work_with_mouse_and_keep_page(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.handle_event(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN,
                button=1,
                pos=ui.menu_next_button.center,
            )
        )
        self.assertEqual(ui.menu_page, 1)

        card = ui._menu_entries()[0][0]
        ui.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=card.center)
        )
        self.assertEqual(ui.view_mode, "balloons")
        self.assertEqual(ui.balloon_game.state, "ready")

        ui.handle_event(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN,
                button=1,
                pos=ui.balloon_game.menu_button.center,
            )
        )
        self.assertEqual(ui.view_mode, "menu")
        self.assertEqual(ui.menu_page, 1)

    def test_all_second_page_games_can_be_selected_and_left_with_pistol(self) -> None:
        ui = self._new_ui()
        expected = (
            (0, "balloons", ui.balloon_game),
            (1, "aliens", ui.alien_game),
            (2, "stars", ui.star_game),
            (3, "math", ui.math_game),
            (4, "colors", ui.color_game),
            (5, "treasure", ui.treasure_game),
        )
        now = 100.0
        for card_index, view_mode, game in expected:
            ui._show_menu()
            ui.menu_page = 1
            ui.armed_at = 0.0
            self._fire(ui, ui._menu_entries()[card_index][0].center, now)
            self.assertEqual(ui.view_mode, view_mode)
            self.assertEqual(game.state, "ready")
            ui.armed_at = 0.0
            self._fire(ui, game.menu_button.center, now + 0.5)
            self.assertEqual(ui.view_mode, "menu")
            self.assertEqual(ui.menu_page, 1)
            now += 1.0

    def test_all_menu_pages_and_arrows_are_laser_neutral(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        for page in range(ui.menu_page_count):
            ui.menu_page = page
            ui._draw_main_menu()
            rgb = pygame.surfarray.array3d(ui.screen).astype(np.int16)
            red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
            self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))
            self.assertTrue(ui.screen.get_rect().contains(ui.menu_previous_button))
            self.assertTrue(ui.screen.get_rect().contains(ui.menu_next_button))

    def test_every_game_selection_card_has_its_own_visible_background_art(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        fingerprints = set()
        for page in range(ui.menu_page_count):
            ui.menu_page = page
            ui._draw_main_menu()
            for rect, name, _, _ in ui._menu_entries():
                inner = rect.inflate(-12, -12)
                rgb = pygame.surfarray.array3d(ui.screen.subsurface(inner)).astype(np.int16)
                self.assertGreater(int(rgb[:, :, 1].std()), 12, name)
                fingerprints.add(hash(rgb.tobytes()))
        self.assertEqual(len(fingerprints), 18)

    def test_bright_water_and_clay_cards_keep_extra_laser_reserve(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.menu_page = 0
        ui._draw_main_menu()
        for rect, name, _, _ in ui._menu_entries():
            if name not in {"WASSER-ALARM", "TONTAUBENSCHIEßEN"}:
                continue
            artwork = ui.menu_card_background_cache[(name, rect.inflate(-12, -12).size)]
            rgb = pygame.surfarray.array3d(artwork)
            self.assertLessEqual(int(rgb.max()), 132, name)

    def test_four_laser_shots_on_title_open_hidden_ocean_game(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0
        title = ui._easter_title_rect().center

        for index in range(3):
            self._fire(ui, title, 100.0 + index)
            self.assertEqual(ui.view_mode, "menu")
            self.assertEqual(ui.easter_title_hits, index + 1)

        self._fire(ui, title, 103.0)
        self.assertEqual(ui.view_mode, "ocean")
        self.assertEqual(ui.ocean_cleanup_game.state, "ready")
        self.assertNotIn(
            "ANNAS MEERESMISSION",
            [name for _, name, _, _ in ui._menu_entries()],
        )

    def test_annas_meeresmission_finishes_without_top_ten_or_name_entry(self) -> None:
        ui = self._new_ui()
        ui.view_mode = "ocean"
        game = ui.ocean_cleanup_game
        game.state = "playing"
        game.score = 125
        game.shots = 20
        game.trash_collected = 17
        game.cat_cans_collected = 4
        game.animal_hits = 0
        game.last_update = 100.0
        game.deadline = 100.0
        ui.standard_game_states[id(game)] = "playing"

        ui.update(
            LaserDetection(
                point=None,
                area=0.0,
                confidence=0.0,
                frame_ts=101.0,
                mask_preview=None,
                frame_preview=None,
                shot=False,
            ),
            101.0,
        )

        self.assertEqual(game.state, "game_over")
        self.assertFalse(ui.arcade_leaderboard.is_active_for("ocean"))
        self.assertFalse(ui.arcade_leaderboard.active)
        self.assertIsNone(ui.arcade_leaderboard.candidate)

    def test_each_screen_corner_once_opens_hidden_tobia_game(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0
        corners = list(ui._easter_corner_rects().items())

        duplicate = corners[0]
        self._fire(ui, duplicate[1].center, 100.0)
        self._fire(ui, duplicate[1].center, 101.0)
        self.assertEqual(ui.view_mode, "menu")
        self.assertEqual(ui.easter_corner_hits, {duplicate[0]})

        for index, (name, rect) in enumerate(corners[1:], start=1):
            self._fire(ui, rect.center, 101.0 + index)
            if index < 3:
                self.assertEqual(ui.view_mode, "menu")
                self.assertIn(name, ui.easter_corner_hits)

        self.assertEqual(ui.view_mode, "tobia")
        self.assertEqual(ui.tobia_duel_game.state, "ready")
        self.assertNotIn(
            "TOBIAS BLITZDUELL",
            [name for _, name, _, _ in ui._menu_entries()],
        )

    def test_tobias_ready_screen_draws_its_single_player_top_ten(self) -> None:
        ui = self._new_ui()
        ui.view_mode = "tobia"
        ui.tobia_duel_game.start(100.0)
        with mock.patch.object(
            ui.arcade_leaderboard,
            "draw_ready_preview",
            wraps=ui.arcade_leaderboard.draw_ready_preview,
        ) as draw_preview:
            ui.draw(100.0)
        draw_preview.assert_called_once_with("tobia")

    def test_feather_and_01060205_card_sequence_open_hidden_moorhuhn(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0
        ui.chicken_game = mock.Mock()
        feather = ui._easter_moorhuhn_rect().center

        self._fire(ui, feather, 100.0)
        self.assertTrue(ui.easter_moorhuhn_armed)
        self.assertEqual(ui.easter_moorhuhn_progress, 0)

        # 01, 06, 02 und 05 liegen auf der ersten Menüseite.
        sequence = ((0, 0), (0, 5), (0, 1), (0, 4))
        now = 101.0
        for step, (page, card_index) in enumerate(sequence, start=1):
            ui.armed_at = 0.0
            ui._change_menu_page(page - ui.menu_page, now)
            ui.armed_at = 0.0
            self._fire(ui, ui._menu_entries()[card_index][0].center, now + 0.1)
            if step < len(sequence):
                self.assertEqual(ui.view_mode, "menu")
                self.assertEqual(ui.easter_moorhuhn_progress, step)
            now += 1.0

        self.assertEqual(ui.view_mode, "chickens")
        ui.chicken_game.start.assert_called_once()
        self.assertNotIn(
            "MOORHUHN",
            [name for _, name, _, _ in ui._menu_entries()],
        )

    def test_wrong_moorhuhn_card_deactivates_sequence_without_starting_game(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.chicken_game = mock.Mock()
        ui._register_easter_moorhuhn_shot(100.0)
        ui._register_easter_moorhuhn_code_shot(1, 101.0)
        ui._register_easter_moorhuhn_code_shot(8, 102.0)
        self.assertFalse(ui.easter_moorhuhn_armed)
        self.assertEqual(ui.easter_moorhuhn_progress, 0)
        self.assertEqual(ui.view_mode, "menu")
        ui.chicken_game.start.assert_not_called()

    def test_shot_away_from_cards_deactivates_moorhuhn_sequence(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._register_easter_moorhuhn_shot(100.0)
        self.assertTrue(ui.easter_moorhuhn_armed)

        ui.armed_at = 0.0
        self._fire(ui, (500, 650), 101.0)

        self.assertFalse(ui.easter_moorhuhn_armed)
        self.assertEqual(ui.easter_moorhuhn_progress, 0)
        self.assertEqual(ui.view_mode, "menu")

    def test_close_pin_and_moorhuhn_feather_render_laser_neutral(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.easter_moorhuhn_armed = True
        ui.easter_moorhuhn_progress = 2
        ui._draw_main_menu()
        ui._request_program_close()
        ui.draw(60.0)

        rgb = pygame.surfarray.array3d(ui.screen).astype(np.int16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28)).any()))

    def test_moorhuhn_feather_is_subtly_visible_low_in_the_scene(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui._draw_main_menu()
        rect = ui._easter_moorhuhn_rect()
        self.assertIsNotNone(ui.menu_feather_image)
        alpha = pygame.surfarray.array_alpha(ui.menu_feather_image)
        self.assertGreater(int((alpha >= 20).sum()), 100)
        rgb = pygame.surfarray.array3d(ui.menu_feather_image).astype(np.int16)
        red_excess = rgb[:, :, 0] - np.maximum(rgb[:, :, 1], rgb[:, :, 2])
        self.assertFalse(bool(((rgb[:, :, 0] >= 70) & (red_excess >= 28) & (alpha >= 10)).any()))
        self.assertGreater(rect.top, ui.screen.get_height() * 0.70)

    def test_active_moorhuhn_feather_draws_every_progress_stage(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.easter_moorhuhn_armed = True
        for progress in range(len(ui.MOORHUHN_EASTER_CODE)):
            with self.subTest(progress=progress):
                ui.easter_moorhuhn_progress = progress
                ui._draw_main_menu()

    def test_moorhuhn_finish_opens_its_own_top_ten_overlay(self) -> None:
        ui = self._new_ui()
        game = mock.Mock()
        game.state = "playing"
        game.score = 1919
        game.hits = 14
        game.shots = 20
        game.accuracy = 70.0
        game.best_score = 2200
        game.finish_reason = "Die Zeit ist abgelaufen"
        game.visual_transition_active = False
        game.camera = 0.0
        game.camera_target = 0.0

        def finish(_now: float) -> None:
            game.state = "game_over"

        game.update.side_effect = finish
        ui.chicken_game = game
        ui.view_mode = "chickens"
        ui.armed_at = 0.0
        self._fire(ui, (20, 300), 100.0)

        self.assertTrue(ui.arcade_leaderboard.is_active_for("chickens"))
        self.assertEqual(ui.arcade_leaderboard.candidate.board, "chickens")
        ui.draw(60.0)
        game.draw.assert_called_once()

    def test_moorhuhn_top_ten_name_entry_accepts_keyboard_without_opening_menu(self) -> None:
        ui = self._new_ui()
        game = mock.Mock()
        game.state = "game_over"
        game.score = 1919
        game.hits = 14
        game.shots = 20
        game.accuracy = 70.0
        game.best_score = 2200
        game.finish_reason = "Die Zeit ist abgelaufen"
        ui.chicken_game = game
        ui.view_mode = "chickens"
        ui.arcade_leaderboard.prepare("chickens", game)
        ui.arcade_leaderboard.state = "name_entry"

        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_m, unicode="m")
        with mock.patch.object(ui, "_show_menu") as show_menu:
            self.assertTrue(ui.handle_event(event))

        self.assertEqual(ui.arcade_leaderboard.player_name, "M")
        show_menu.assert_not_called()

    def test_secret_title_sequence_has_no_hidden_time_limit(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0
        title = ui._easter_title_rect().center
        self._fire(ui, title, 100.0)
        ui.armed_at = 0.0
        self._fire(ui, title, 107.0)
        self.assertEqual(ui.view_mode, "menu")
        self.assertEqual(ui.easter_title_hits, 2)

    def test_cans_game_can_be_selected_started_and_left_with_pistol(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        cans_card = ui._menu_entries()[1][0]
        self._fire(ui, cans_card.center, 100.0)
        self.assertEqual(ui.view_mode, "cans")
        self.assertEqual(ui.cans_game.state, "ready")
        ui.armed_at = 0.0

        self._fire(ui, ui.cans_game.start_card.center, 101.0)
        self.assertEqual(ui.cans_game.state, "countdown")
        ui.update(
            LaserDetection(None, 0.0, 0.0, 101.1, None, None),
            101.1,
        )
        ui.armed_at = 0.0

        self._fire(ui, ui.cans_game.menu_button.center, 102.0)
        self.assertEqual(ui.view_mode, "menu")

    def test_new_games_can_be_selected_and_left_with_pistol(self) -> None:
        ui = self._new_ui()
        expected = (
            (3, "clay", ui.clay_game),
            (2, "timed", ui.timed_game),
            (4, "reaction", ui.reaction_game),
            (5, "range", ui.target_range_game),
        )
        now = 100.0
        for card_index, view_mode, game in expected:
            ui._show_menu()
            ui.armed_at = 0.0
            self._fire(ui, ui._menu_entries()[card_index][0].center, now)
            self.assertEqual(ui.view_mode, view_mode)
            ui.armed_at = 0.0
            self._fire(ui, game.menu_button.center, now + 0.5)
            self.assertEqual(ui.view_mode, "menu")
            now += 1.0

    def test_standard_game_state_transition_resets_detector_and_arms_later(self) -> None:
        ui = self._new_ui()
        ui._start_standard_game("range", ui.target_range_game, "Zielscheibe")
        reset_before = ui.tracker.reset_count

        ui.target_range_game.state = "result"
        ui.target_range_game.result_until = 200.0
        ui.update(
            LaserDetection(None, 0.0, 0.0, 120.0, None, None),
            120.0,
        )

        self.assertEqual(ui.tracker.reset_count, reset_before + 1)
        self.assertEqual(ui.armed_at, 120.4)
        self.assertEqual(
            ui.standard_game_states[id(ui.target_range_game)],
            "result",
        )
        self.assertFalse(ui.arcade_leaderboard.is_active_for("range"))

    def test_target_result_stays_visible_three_seconds_before_leaderboard(self) -> None:
        ui = self._new_ui()
        ui._start_standard_game("range", ui.target_range_game, "Zielscheibe")
        game = ui.target_range_game
        game.shot_count_index = 0
        for index in range(3):
            self.assertEqual(
                game.handle_shot(game.target_center, 100.0 + index),
                "hit",
            )
        self.assertEqual(game.state, "result")
        self.assertEqual(game.result_until, 105.0)

        ui.update(LaserDetection(None, 0.0, 0.0, 102.0, None, None), 102.0)
        self.assertFalse(ui.arcade_leaderboard.is_active_for("range"))
        ui.update(LaserDetection(None, 0.0, 0.0, 104.99, None, None), 104.99)
        self.assertFalse(ui.arcade_leaderboard.is_active_for("range"))
        self.assertEqual(game.state, "result")

        ui.update(LaserDetection(None, 0.0, 0.0, 105.0, None, None), 105.0)
        self.assertTrue(ui.arcade_leaderboard.is_active_for("range"))
        self.assertEqual(game.state, "result")

    def test_water_alarm_starts_without_name_and_can_be_left_with_pistol(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        water_card = ui._menu_entries()[0][0]
        self._fire(ui, water_card.center, 100.0)
        self.assertEqual(ui.view_mode, "water")
        self.assertEqual(ui.water_alarm_game.state, "ready")
        ui.armed_at = 0.0

        self._fire(ui, ui.water_alarm_game.ready_start_button.center, 102.0)
        self.assertEqual(ui.water_alarm_game.state, "countdown")
        ui.update(LaserDetection(None, 0.0, 0.0, 106.0, None, None), 106.0)
        self.assertEqual(ui.water_alarm_game.state, "playing")
        self.assertFalse(ui.tracker.moorhuhn_filter_enabled)
        ui.armed_at = 0.0

        self._fire(ui, ui.water_alarm_game.menu_button.center, 107.0)
        self.assertEqual(ui.view_mode, "menu")

    def test_water_alarm_optional_post_game_name_accepts_weaker_real_laser(self) -> None:
        ui = self._new_ui()
        ui._show_menu()
        ui.armed_at = 0.0

        self._fire(ui, ui._menu_entries()[0][0].center, 100.0)
        game = ui.water_alarm_game
        game.score = 1000
        game.hits = 5
        game.shots = 6
        game._finish(100.5)
        ui.standard_game_states[id(game)] = "result"
        ui.armed_at = 0.0

        self._fire(ui, game.result_name_button.center, 101.0)
        self.assertEqual(game.state, "name_entry")
        ui.standard_game_states[id(game)] = "name_entry"
        ui.armed_at = 0.0

        key, rect = game.key_buttons[1]
        self._fire(
            ui,
            rect.center,
            101.0,
            peak_red_excess=40,
            peak_delta=45,
        )
        self.assertEqual(game.player_name, key)

    def test_water_alarm_logo_appearance_cannot_be_counted_as_shot(self) -> None:
        ui = self._new_ui()
        ui._start_standard_game("water", ui.water_alarm_game, "Wasser-Alarm")
        game = ui.water_alarm_game
        game.state = "playing"
        game.player_name = "TEST"
        game.play_started = 100.0
        game.last_update = 100.0
        game.deadline = 160.0
        logo = game._spawn_target("logo", 100.0)
        ui.standard_game_states[id(game)] = "playing"
        ui.armed_at = 0.0

        self._fire(ui, logo.center, 100.1)

        self.assertEqual(game.shots, 0)
        self.assertTrue(ui.standard_visual_transitions[id(game)])

    def test_cursor_hides_after_inactivity_and_returns_on_mouse_activity(self) -> None:
        ui = self._new_ui()
        ui.xfixes_cursor = mock.Mock()
        ui.physical_mouse_connected = True
        ui._mark_mouse_activity(now=10.0)
        ui.xfixes_cursor.show.assert_called_once()
        ui._update_cursor_visibility(now=11.0)
        self.assertFalse(ui.cursor_hidden)

        ui._update_cursor_visibility(now=11.6)
        self.assertTrue(ui.cursor_hidden)
        ui.xfixes_cursor.hide.assert_called_once_with()

        ui._mark_mouse_activity(now=12.0)
        self.assertFalse(ui.cursor_hidden)
        self.assertEqual(ui.xfixes_cursor.show.call_count, 2)

        ui._update_cursor_visibility(now=14.0)
        self.assertTrue(ui.cursor_hidden)
        position = ui.last_mouse_position
        ui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=position))
        self.assertTrue(ui.cursor_hidden)

        moved = (position[0] + 3, position[1])
        ui.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=moved))
        self.assertFalse(ui.cursor_hidden)

    def test_mouse_hotplug_releases_cursor_and_unplug_hides_it(self) -> None:
        ui = self._new_ui()
        ui.physical_mouse_connected = False
        ui.cursor_hidden = True
        ui.last_mouse_probe = 0.0
        old_xfixes = mock.Mock()
        ui.xfixes_cursor = old_xfixes
        new_xfixes = mock.Mock()

        with (
            mock.patch.object(ui, "_physical_mouse_connected", return_value=True),
            mock.patch(
                "laser_arcade.diagnostic_ui.XFixesCursorController",
                return_value=new_xfixes,
            ),
        ):
            ui._update_cursor_visibility(now=3.0)

        self.assertTrue(ui.physical_mouse_connected)
        self.assertFalse(ui.cursor_hidden)
        self.assertEqual(ui.last_mouse_activity, 3.0)
        old_xfixes.close.assert_called_once_with()
        self.assertIs(ui.xfixes_cursor, new_xfixes)

        with mock.patch.object(ui, "_physical_mouse_connected", return_value=False):
            ui._update_cursor_visibility(now=6.0)

        self.assertFalse(ui.physical_mouse_connected)
        self.assertTrue(ui.cursor_hidden)

    def test_real_mouse_motion_forces_pygame_cursor_visible(self) -> None:
        ui = self._new_ui()
        ui.physical_mouse_connected = True
        ui.cursor_hidden = False
        ui.xfixes_cursor = mock.Mock()

        with mock.patch("pygame.mouse.set_visible") as set_visible:
            ui._mark_mouse_activity(now=20.0)

        set_visible.assert_called_once_with(True)
        ui.xfixes_cursor.show.assert_called_once_with()
        self.assertFalse(ui.cursor_hidden)

    def test_every_pistol_shot_hides_cursor_until_real_mouse_moves(self) -> None:
        ui = self._new_ui()
        ui.physical_mouse_connected = True
        ui.cursor_hidden = False
        ui.xfixes_cursor = mock.Mock()

        with mock.patch("pygame.mouse.set_visible") as set_visible:
            self._fire(ui, ui._sighting_stages()[0][3], 100.0)

        set_visible.assert_called_with(False)
        ui.xfixes_cursor.hide.assert_called_once_with()
        self.assertTrue(ui.cursor_hidden)

        ui._mark_mouse_activity(now=101.0)
        self.assertFalse(ui.cursor_hidden)

    def test_bluetooth_keyboard_mouse_channel_is_detected_without_by_id_link(self) -> None:
        bluetooth_combo = """
N: Name="Bluetooth 5.1 Keyboard"
H: Handlers=sysrq kbd leds event5

N: Name="Bluetooth 5.1 Keyboard Mouse"
H: Handlers=mouse0 event6
"""
        keyboard_and_hdmi_only = """
N: Name="vc4-hdmi-0"
H: Handlers=kbd event0
B: REL=3

N: Name="Bluetooth Keyboard"
H: Handlers=sysrq kbd leds event5
"""

        self.assertTrue(
            LaserDiagnosticUI._input_devices_include_mouse(bluetooth_combo)
        )
        self.assertFalse(
            LaserDiagnosticUI._input_devices_include_mouse(keyboard_and_hdmi_only)
        )

    def test_arcade_top_ten_name_entry_is_fully_operable_with_pistol(self) -> None:
        ui = self._new_ui()
        ui.view_mode = "cans"
        game = ui.cans_game
        game.state = "game_over"
        game.score = 2400
        game.knocked_down = 12
        game.shots = 15
        game.hits = 12
        game.best_combo = 5
        game.finish_reason = "Alle Runden abgeschlossen"
        ui.standard_game_states[id(game)] = "playing"

        ui.update(
            LaserDetection(
                point=None,
                area=0.0,
                confidence=0.0,
                frame_ts=100.0,
                mask_preview=None,
                frame_preview=None,
                shot=False,
            ),
            100.0,
        )
        self.assertTrue(ui.arcade_leaderboard.is_active_for("cans"))
        self.assertTrue(ui.arcade_leaderboard.qualifies)

        self._fire(ui, ui.arcade_leaderboard.name_button.center, 101.0)
        self.assertEqual(ui.arcade_leaderboard.state, "name_entry")
        letter_button = next(
            rect for key, rect in ui.arcade_leaderboard.key_buttons if key == "A"
        )
        self._fire(ui, letter_button.center, 102.0)
        self._fire(ui, ui.arcade_leaderboard.save_button.center, 103.0)
        self.assertTrue(ui.arcade_leaderboard.saved)
        self.assertEqual(ui.arcade_leaderboard.entries[0].name, "A")

    def test_program_close_restores_default_cursor_before_display_closes(self) -> None:
        ui = self._new_ui()
        ui.cursor_hidden = True
        ui.xfixes_cursor = mock.Mock()

        with (
            mock.patch("pygame.mouse.set_cursor") as set_cursor,
            mock.patch("pygame.mouse.set_visible") as set_visible,
            mock.patch("pygame.event.pump") as pump,
        ):
            ui.close()

        set_cursor.assert_called_once_with(ui.default_cursor)
        set_visible.assert_called_once_with(True)
        pump.assert_called_once_with()
        ui.xfixes_cursor.close.assert_called_once_with()
        self.assertFalse(ui.cursor_hidden)


if __name__ == "__main__":
    unittest.main()
