import pygame
from settings.states import Game

# MAIN GAME
game = Game()

if __name__ == '__main__':
    print('START GAME')
    game.start_game()
pygame.quit()

