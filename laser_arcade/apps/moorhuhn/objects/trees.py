import pygame

class Tree(pygame.sprite.Sprite):
    """
    TREES  class
    """
    def __init__(self, screen, path,x, width):
        super().__init__()
        self.screen = screen

        self.shot = False

        self.image = pygame.transform.scale(pygame.image.load(path),(width,600))
        self.rect = self.image.get_rect(center=(x,300))

    def update(self, move):

        if move == 'move_r':
            self.rect.x -= 40
        elif move == 'move_l':
            self.rect.x += 40

        self.screen.blit(self.image, self.rect)
