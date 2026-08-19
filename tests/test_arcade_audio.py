from __future__ import annotations

import hashlib
import inspect
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.cans import CanGameSounds


class ArcadeAudioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def test_every_game_family_has_a_distinct_short_event_sound(self) -> None:
        bank = CanGameSounds(enabled=True)
        expected = {
            "can_hit",
            "clay_break",
            "reaction_hit",
            "target_hit",
            "water_hit",
            "photo_hit",
            "balloon_pop",
            "alien_hit",
            "star_hit",
            "math_correct",
            "math_wrong",
            "color1",
            "color2",
            "color3",
            "color4",
            "color_level",
            "treasure_found",
            "treasure_wrong",
        }
        self.assertTrue(bank.enabled)
        self.assertTrue(expected.issubset(bank.sounds))

        fingerprints = set()
        for name in expected:
            sound = bank.sounds[name]
            self.assertGreater(sound.get_length(), 0.07, name)
            self.assertLess(sound.get_length(), 1.5, name)
            samples = pygame.sndarray.array(sound)
            fingerprints.add(hashlib.sha256(samples.tobytes()).hexdigest())
        self.assertEqual(len(fingerprints), len(expected))
        bank.stop_all()

    def test_arcade_bank_contains_no_background_loop(self) -> None:
        bank = CanGameSounds(enabled=True)
        self.assertNotIn("music", bank.sounds)
        self.assertNotIn("ambient", bank.sounds)
        self.assertTrue(all(sound.get_length() < 1.5 for sound in bank.sounds.values()))
        bank.stop_all()

    def test_runtime_moorhuhn_never_starts_continuous_music(self) -> None:
        # Die Originaldateien dürfen für die Herkunftsdokumentation erhalten
        # bleiben, der tatsächlich verwendete Spielcode startet sie aber nie.
        from laser_arcade.apps.chickens import ChickenApp

        source = inspect.getsource(ChickenApp)
        self.assertNotIn("play_music(", source)
        self.assertIn("stop_music()", source)


if __name__ == "__main__":
    unittest.main()
