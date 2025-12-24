import pygame

class SignPost(pygame.sprite.Sprite):
    """
    SIGN POST  class
    """
    def __init__(self, screen):
        super().__init__()
        self.screen = screen

        self.shot = False

        self.image = pygame.transform.scale(pygame.image.load('img/sign_post/sign_post1.png'),(400,500))
        self.rect = self.image.get_rect(center=(930,450))

    def update(self, move):

        if not self.shot:
            if move == 'move_r':
                self.rect.x -= 40
            elif move == 'move_l':
                self.rect.x += 40
            self.image = pygame.transform.scale(pygame.image.load('img/sign_post/sign_post1.png'),(400,500))
        else:
            if move == 'move_r':
                self.rect.x -= 40
            elif move == 'move_l':
                self.rect.x += 40
            self.image = pygame.transform.scale(pygame.image.load('img/sign_post/sign_post2.png'),(400,500))

        self.screen.blit(self.image, self.rect)
