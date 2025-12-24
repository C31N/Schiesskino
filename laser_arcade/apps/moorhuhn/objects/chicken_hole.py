import pygame

class ChickenHole(pygame.sprite.Sprite):
    def __init__(self, screen):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen

        # time on the SCREEN
        self.show = False
        self.show_cycle = False
        self.max_show_time = 7
        # time on the SCREEN when the CHICKEN just blinks
        self.max_show_cycle_time = 9
        self.blink_pause = 6
        self.current_blink_time = 0
        self.current_time = 0

        # choose picture
        self.index = 0
        self.path = 'img/main_menu_background/chickenhole1.png'
        self.image = pygame.transform.scale(pygame.image.load(self.path), (50,55))
        self.rect = self.image.get_rect(center=(380,200))
        self.start = 0

    def start(self):
        self.rect = self.image.get_rect(center=(500,200))

    def update(self):
        if self.rect != None:
            # CHICKEN animation
            if self.show:
                self.current_time += 1
                self.screen.blit(self.image, self.rect)
                if self.current_time == self.max_show_time:
                    self.index += 1
                    if self.index <= 14:
                        self.current_time = 0
                        self.path = 'img/main_menu_background/chickenhole' + str(self.index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (65, 85))

                    elif self.index == 15:
                        self.current_time = 0
                        self.index = 6
                        self.path = 'img/main_menu_background/chickenhole' + str(self.index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (65, 85))
                        self.show = False
                        self.show_cycle = True

            # CHICKEN only BLINKS
            elif self.show_cycle:
                self.current_time += 1
                self.screen.blit(self.image, self.rect)

                if self.current_time == self.max_show_cycle_time:

                    if self.index == 6:
                        self.current_time = 0
                        self.path = 'img/main_menu_background/chickenhole' + str(self.index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (65, 85))
                        self.current_blink_time += 1
                        self.index += 1

                    elif self.index == 7:
                        self.current_time = 0
                        if self.current_blink_time == self.blink_pause:
                            self.path = 'img/main_menu_background/chickenhole' + str(self.index) + '.png'
                            self.image = pygame.transform.scale(pygame.image.load(self.path), (65, 85))
                            self.index += 1
                        else:
                            self.index -= 1

                    elif self.index == 8:
                        self.current_time = 0
                        self.path = 'img/main_menu_background/chickenhole' + str(self.index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (65, 85))
                        self.current_blink_time = 0
                        self.index = 6
            else:
                self.show = True