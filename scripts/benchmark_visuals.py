#!/usr/bin/env python3
"""Prüft das Renderbudget der V3-Spielwelten bei 1024 × 768."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pygame

from laser_arcade.apps.arcade_common import (
    THEME_VISUAL_PROFILES,
    draw_frame,
    draw_target_sprite,
)


TARGET_FOR_THEME = {
    "cans": "can",
    "clay": "clay",
    "timed": "mechanical_target",
    "reaction": "mechanical_target",
    "range": "mechanical_target",
    "balloons": "balloon",
    "aliens": "alien",
    "stars": "star",
    "math": "mechanical_target",
    "colors": "mechanical_target",
    "treasure": "treasure_chest",
    "moorhuhn_game": "chicken",
}


def main() -> int:
    pygame.init()
    screen = pygame.display.set_mode((1024, 768))
    results: list[tuple[str, float]] = []
    try:
        for theme in TARGET_FOR_THEME:
            # Einmaliges Laden und Skalieren gehört nicht in das Framebudget.
            draw_frame(screen, theme, 0.0)
            draw_target_sprite(screen, TARGET_FOR_THEME[theme], (512, 410), (118, 118))
            pygame.display.flip()
            frames = 90
            started = time.perf_counter()
            for frame in range(frames):
                now = frame / 30.0
                draw_frame(screen, theme, now)
                target = TARGET_FOR_THEME[theme]
                for index in range(6):
                    x = 130 + (index % 3) * 376
                    y = 285 + (index // 3) * 275
                    draw_target_sprite(
                        screen,
                        target,
                        (x, y),
                        (104 + index * 3, 104 + index * 3),
                        flip_x=bool(index % 2),
                        angle=((frame + index * 7) % 15) - 7,
                    )
                pygame.display.flip()
            elapsed = time.perf_counter() - started
            results.append((theme, frames / max(elapsed, 0.0001)))
    finally:
        pygame.quit()

    for theme, fps in results:
        print(f"{theme:15s} {fps:6.1f} FPS")
    minimum = min(fps for _, fps in results)
    print(f"Minimum: {minimum:.1f} FPS")
    return 0 if minimum >= 28.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
