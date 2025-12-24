import pygame

class Holes(pygame.sprite.Sprite):
    def __init__(self, screen, count):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen

        self.current_time = 0
        self.max_time = 40
        self.show = True

        self.image = pygame.transform.scale(pygame.image.load('img/main_menu_background/hole.png'), (40,40))
        self.rect = None
        self.count = count


    def update(self, sounds):
        self.current_time += 1
        if self.rect == None:
            pass
        else:
            self.screen.blit(self.image, self.rect)

        if self.current_time == self.max_time:
            if self.count == 1:
                self.rect = self.image.get_rect(center=(500, 200))
                sounds.shot_sound.play()

            elif self.count == 2:
                self.rect = self.image.get_rect(center=(450, 300))
                sounds.shot_sound.play()

            elif self.count == 3:
                self.rect = self.image.get_rect(center=(290, 320))
                sounds.shot_sound.play()

            elif self.count == 4:
                self.rect = self.image.get_rect(center=(300, 220))
                sounds.shot_sound.play()

    def shot(self):
        self.kill()