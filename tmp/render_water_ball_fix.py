import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.water_alarm import WaterAlarmApp

pygame.init()
screen = pygame.display.set_mode((1024, 768))
game = WaterAlarmApp(screen, audio_enabled=False, leaderboard_path=None, random_seed=7)
game.state = "playing"
game.play_started = 100.0
game.last_update = 125.0
game.deadline = 160.0
ball = game._spawn_target("ball", 125.0)
ball.x = 512
ball.y = 360
ball.velocity_x = 115.0
ball.velocity_y = 22.0
game.targets = [ball]
game.draw(125.0)
pygame.image.save(screen, "/tmp/water-ball-fix.png")
pygame.quit()
