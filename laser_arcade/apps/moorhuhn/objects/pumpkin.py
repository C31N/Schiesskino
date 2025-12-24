import pygame

class Pumpkin(pygame.sprite.Sprite):
    """
    PUMPKIN class
    """
    def __init__(self, screen):
        super().__init__()

        self.screen = screen

        self.alive = True
        self.stop = False

        self.image = pygame.transform.scale(pygame.image.load('img/pumpkin/pumpkin1.png'),(100,100))
        self.rect = self.image.get_rect(center=(2110,410))
        self.de_index = 0
        self.max_time = 2
        self.de_time = 0

    # update current PUMPKIN state
    def update(self, move):
        if self.alive:
            if move == 'move_r':
                self.rect.x -= 40
            elif move == 'move_l':
                self.rect.x += 40
            self.screen.blit(self.image, self.rect)

        else:
            if not self.stop:
                if move == 'move_r':
                    self.rect.x -= 40
                elif move == 'move_l':
                    self.rect.x += 40
                self.de_time += 1
                if self.de_time == self.max_time:
                    self.de_time = 0
                    self.de_index += 1
                    if self.de_index <= 8:
                        path = 'img/pumpkin/pumpkin' + str(self.de_index) + '.png'
                        self.image = pygame.image.load(path)
                    elif self.de_index == 9:
                        self.stop = True
                        path = 'img/pumpkin/pumpkin9.png'
                        self.image = pygame.image.load(path)

            else:
                if move == 'move_r':
                    self.rect.x -= 40
                elif move == 'move_l':
                    self.rect.x += 40
                path = 'img/pumpkin/pumpkin9.png'
                self.image = pygame.image.load(path)
            self.screen.blit(self.image, self.rect)

