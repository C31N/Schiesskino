"""Rendert die kindgerechten Ziel- und Spielerkennzeichnungen der Duellspiele."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from laser_arcade.apps.duel_games import (
    ConnectFourApp,
    DotsBoxesApp,
    MemoryDuelApp,
    NimDuelApp,
    ReversiLightApp,
    TicTacToeApp,
)


def render(game, frame: pygame.Surface, now: float) -> pygame.Surface:
    game.start(now - 1.0)
    game._begin(now - 0.5)
    game.handoff_until = 0.0
    game.draw(now)
    return pygame.transform.smoothscale(frame, (512, 384))


def main() -> None:
    pygame.init()
    pygame.display.set_mode((1024, 768))
    frame = pygame.Surface((1024, 768))
    montage = pygame.Surface((1536, 768))

    tic = TicTacToeApp(frame, audio_enabled=False)
    tic.start(99.0)
    tic._begin(99.5)
    tic.handoff_until = 0.0
    tic.board = [0, 1, -1, -1, 0, -1, 1, -1, 0]
    tic.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (0, 0))

    connect = ConnectFourApp(frame, audio_enabled=False)
    connect.start(99.0)
    connect._begin(99.5)
    connect.handoff_until = 0.0
    connect.board[5][:5] = [0, 1, 0, 1, 0]
    connect.board[4][1:4] = [1, 0, 1]
    connect.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (512, 0))

    memory = MemoryDuelApp(frame, audio_enabled=False, random_seed=1)
    memory.start(99.0)
    memory._begin(99.5)
    memory.handoff_until = 0.0
    memory.revealed = list(range(8))
    memory.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (1024, 0))

    dots = DotsBoxesApp(frame, audio_enabled=False)
    dots.start(99.0)
    dots._begin(99.5)
    dots.handoff_until = 0.0
    dots.horizontal[(0, 0)] = 0
    dots.vertical[(0, 0)] = 1
    dots.scores = [12, 12]
    dots.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (0, 384))

    nim = NimDuelApp(frame, audio_enabled=False)
    nim.start(99.0)
    nim._begin(99.5)
    nim.handoff_until = 0.0
    nim.remaining_items = 1
    nim.scores = [8, 6]
    nim.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (512, 384))

    reversi = ReversiLightApp(frame, audio_enabled=False)
    reversi.start(99.0)
    reversi._begin(99.5)
    reversi.handoff_until = 0.0
    reversi.draw(100.0)
    montage.blit(pygame.transform.smoothscale(frame, (512, 384)), (1024, 384))

    pygame.image.save(montage, Path(__file__).with_name("duel-quality-pass.png"))
    pygame.quit()


if __name__ == "__main__":
    main()
