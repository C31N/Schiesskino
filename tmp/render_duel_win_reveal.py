"""Rendert die dreisekündige Gewinnanzeige der beiden Reihenspiele."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from laser_arcade.apps.duel_games import ConnectFourApp, TicTacToeApp


def fire(game, point: tuple[int, int], now: float) -> None:
    game.handoff_until = 0.0
    game.handle_shot(point, now)


def main() -> None:
    pygame.init()
    pygame.display.set_mode((1024, 768))
    frame = pygame.Surface((1024, 768))
    montage = pygame.Surface((1024, 768))

    tic = TicTacToeApp(frame, audio_enabled=False)
    tic.start(100.0)
    tic._begin(100.0)
    for index, now in zip((0, 3, 1, 4, 2), (101, 102, 103, 104, 105)):
        fire(tic, tic._cells()[index].center, float(now))
    tic.draw(106.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (0, 192))

    tic.update(108.0)
    tic.draw(108.0)
    pygame.image.save(frame, Path(__file__).with_name("duel-result.png"))

    connect = ConnectFourApp(frame, audio_enabled=False)
    connect.start(200.0)
    connect._begin(200.0)
    for column, now in zip((0, 1, 0, 1, 0, 1, 0), range(201, 208)):
        fire(connect, connect._column_rects()[column].center, float(now))
    connect.draw(208.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (512, 192))

    output = Path(__file__).with_name("duel-win-reveal.png")
    pygame.image.save(montage, output)
    pygame.quit()


if __name__ == "__main__":
    main()
