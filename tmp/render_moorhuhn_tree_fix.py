import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.chickens import ChickenApp


pygame.init()
screen = pygame.display.set_mode((1024, 768))
game = ChickenApp(screen, audio_enabled=False, persist_scores=False, random_seed=1919)
game.start(100.0)
game.begin_countdown(100.0)
game.update(104.0)
game.camera = game.camera_target = 0.0
for step in range(90):
    game.update(104.0 + (step + 1) / 30.0)
game.draw(107.0)
pygame.image.save(screen, "/tmp/moorhuhn-tree-fix.png")
game.stop()
pygame.quit()
