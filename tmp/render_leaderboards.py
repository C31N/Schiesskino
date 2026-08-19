from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.arcade_common import draw_frame
from laser_arcade.apps.arcade_leaderboard import ArcadeLeaderboardOverlay
from laser_arcade.apps.ocean_cleanup import OceanCleanupApp
from laser_arcade.apps.water_alarm import WaterAlarmApp


OUTPUT = Path(os.environ.get("LEADERBOARD_RENDER_DIR", "/tmp/schiesskino-leaderboards"))


def save(screen: pygame.Surface, name: str) -> None:
    pygame.image.save(screen, OUTPUT / name)


def sample_entries(overlay: ArcadeLeaderboardOverlay) -> None:
    candidate = overlay.candidate
    if candidate is None:
        return
    names = ("GAST", "MAX", "LENA", "TOM", "EMMA", "NOAH")
    for index, name in enumerate(names):
        overlay.entries.append(
            replace(
                candidate,
                name=name,
                rank_value=candidate.rank_value + (len(names) - index) * 100,
                value_text=str(round(abs(candidate.rank_value) + (len(names) - index) * 100)),
                date=f"2026-08-{index + 1:02d}T12:00:00+02:00",
            )
        )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pygame.init()
    pygame.display.set_mode((1024, 768))
    screen = pygame.Surface((1024, 768))
    overlay = ArcadeLeaderboardOverlay(screen, None)

    cases = (
        ("cans", "cans", SimpleNamespace(score=4850, knocked_down=31, accuracy=88.0, best_combo=9, finish_reason="Alle Runden abgeschlossen")),
        ("clay", "clay", SimpleNamespace(score=3920, hits=17, TOTAL_CLAYS=20, accuracy=85.0, best_combo=6, finish_reason="Alle Tontauben abgeschlossen")),
        ("timed", "timed", SimpleNamespace(score=4270, hits=18, TOTAL_TARGETS=20, average_time_ms=486, best_combo=8, accuracy=90.0, finish_reason="Alle Ziele abgeschlossen")),
        ("reaction", "reaction", SimpleNamespace(score=3180, hits=11, ROUNDS=12, average_ms=421, false_starts=1, finish_reason="Alle Signale abgeschlossen")),
        ("range", "range", SimpleNamespace(current_result=SimpleNamespace(mode="decimal", shot_count=5, result_value=51.8, display="51,8 RINGE"), MODE_LABELS={"decimal": "ZEHNTELRINGE"})),
    )
    for game_key, theme, game in cases:
        overlay.clear()
        overlay.prepare(game_key, game)
        sample_entries(overlay)
        draw_frame(screen, theme)
        overlay.draw()
        save(screen, f"{game_key}-result.png")

    overlay.clear()
    overlay.prepare("clay", cases[1][2])
    sample_entries(overlay)
    overlay.state = "name_entry"
    overlay.current_rank = 4
    draw_frame(screen, "clay")
    overlay.draw()
    save(screen, "name-entry.png")

    overlay.player_name = "ANNA"
    draw_frame(screen, "clay")
    overlay.draw()
    save(screen, "name-entry-filled.png")

    overlay.player_name = ""
    overlay._save_name()
    draw_frame(screen, "clay")
    overlay.draw()
    save(screen, "name-entry-help.png")

    overlay.state = "admin"
    overlay.admin_digits = "19"
    draw_frame(screen, "clay")
    overlay.draw()
    save(screen, "admin-pin.png")

    ocean = OceanCleanupApp(screen, audio_enabled=False)
    ocean.start(100.0)
    ocean.score = 175
    ocean.shots = 30
    ocean.trash_collected = 24
    ocean.cat_cans_collected = 5
    ocean.animal_hits = 1
    ocean.finish_reason = "DIE MISSION IST BEENDET"
    ocean.state = "game_over"
    overlay.clear()
    ocean.draw(160.0)
    save(screen, "ocean-result.png")

    overlay.clear()
    ocean.start(170.0)
    ocean.draw(170.0)
    save(screen, "ocean-ready.png")

    water = WaterAlarmApp(screen, audio_enabled=False, leaderboard_path=None)
    water.state = "name_entry"
    water.score = 3450
    water.current_rank = 3
    water.result_qualifies = True
    water.draw()
    save(screen, "water-name-entry.png")

    water.player_name = "ANNA"
    water.draw()
    save(screen, "water-name-entry-filled.png")

    water.player_name = ""
    water._submit_name()
    water.draw()
    save(screen, "water-name-entry-help.png")

    water.state = "playing"
    water.play_started = 100.0
    water.deadline = 160.0
    water.last_update = 125.0
    water.targets.clear()
    ring = water._spawn_target("ring", 125.0)
    ring.x, ring.y = 350, 430
    gold = water._spawn_target("gold", 125.0)
    gold.x, gold.y = 670, 430
    water.draw(125.0)
    save(screen, "water-rings.png")

    water.targets.clear()
    water.last_update = 150.0
    water.draw(150.0)
    save(screen, "water-final-rings.png")
    pygame.quit()


if __name__ == "__main__":
    main()
