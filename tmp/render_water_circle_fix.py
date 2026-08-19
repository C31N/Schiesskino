import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from laser_arcade.apps.water_alarm import WaterAlarmApp


pygame.init()
screen = pygame.display.set_mode((1024, 768))
game = WaterAlarmApp(screen, audio_enabled=False, random_seed=7)
game.state = "playing"
game.play_started = 100.0
game.last_update = 125.0
game.deadline = 160.0
game.targets.clear()
for kind, x, y in (("ball", 220, 410), ("ring", 510, 410), ("gold", 800, 410)):
    target = game._spawn_target(kind, 125.0)
    target.x = x
    target.y = y
    target.velocity_x = 0
    target.velocity_y = 0
game.draw(125.0)
pygame.image.save(screen, "/tmp/water-chaos-circle-fix.png")
pygame.quit()
