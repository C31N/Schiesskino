import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from laser_arcade.apps.target_range import TargetRangeApp


pygame.init()
screen = pygame.display.set_mode((1024, 768))
game = TargetRangeApp(screen, audio_enabled=False, history_path=None)
game.mode_index = game.MODES.index("decimal")
game.draw(100.0)
pygame.image.save(screen, "/tmp/target-ten-fix.png")
print(
    f"Zehn={game._scoring_ring_radius(10):.1f}px "
    f"10,9={game._perfect_ten_radius():.1f}px "
    f"Neun={game._ring_radius(9):.1f}px"
)
pygame.quit()
