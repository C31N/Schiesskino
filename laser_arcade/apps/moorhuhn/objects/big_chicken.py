import pygame

class BigChicken(pygame.sprite.Sprite):
    """
    BIG CHICKEN class
    """
    def __init__(self, screen, position):
        pygame.sprite.Sprite.__init__(self)

        self.screen = screen
        self.position = position

        # show on the screen properties
        self.alive = True

        self.show = True
        self.show_cycle = False
        self.max_show_time = 3
        self.max_cycle_show_time = 5

        self.wait_before_dis = 0
        self.disappear = False
        self.current_time = 0
        self.index = 0

        # time in CYCLE SHOW to count BLINK time
        self.blink_pause = 6
        self.current_blink_time = 0

        # DEATH animation
        self.current_death_time = 0
        self.death_time = 2
        self.death_index = -1
        self.stop_death = False

        # timer to count till first show
        self.wait_time = 20
        self.current_wait_time = 0

        self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
        self.image = pygame.transform.scale(pygame.image.load(self.path), (300,300))
        self.rect = self.image.get_rect(center=self.position)


    def update(self, move):
        if self.alive:
            if move == 'move_r':
                self.rect.x -= 40
            elif move == 'move_l':
                self.rect.x += 40
            else:
                # when it appears FIRST TIEM
                if self.show:
                    self.current_time += 1
                    self.screen.blit(self.image, self.rect)

                    if self.current_time == self.max_show_time:
                        self.index += 1
                        self.current_time = 0

                        if self.index <= 17:
                            self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                            self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))

                        elif self.index == 18:
                            self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                            self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                            self.show = False
                            self.show_cycle = True
                            self.current_time = 0
                            self.index = 8

                # when it just BLINKS
                if self.show_cycle:
                    self.current_time += 1
                    self.screen.blit(self.image, self.rect)
                    self.wait_before_dis += 1

                    if self.wait_before_dis == 70:
                        self.show_cycle = False
                        self.disappear = True
                        self.current_time = 0
                        self.index = 5
                    else:
                        if self.current_time == self.max_show_time:
                            self.current_time = 0

                            if self.index == 8:
                                self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                                self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                                self.index += 1
                                self.current_blink_time += 1

                            elif self.index == 9:
                                if self.current_blink_time == self.blink_pause:
                                    self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                                    self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                                    self.index += 1
                                    self.current_blink_time = 0
                                else:
                                    self.index -= 1

                            elif self.index > 9 and self.index <= 17:
                                self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                                self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                                self.index += 1

                            elif self.index == 18:
                                self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                                self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                                self.index = 8

                if self.disappear:
                    self.current_time += 1
                    self.screen.blit(self.image, self.rect)
                    if self.current_time == self.max_show_time:
                        self.index -= 1
                        self.current_time = 0
                        if self.index == 0:
                            self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                            self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))
                            self.disappear = False
                            self.kill()
                        else:
                            self.path = 'img/big_chicken/big_chicken' + str(self.index) + '.png'
                            self.image = pygame.transform.scale(pygame.image.load(self.path), (300, 300))


        if not self.alive:
            self.screen.blit(self.image, self.rect)
            self.current_death_time += 1
            if self.current_death_time == 3:
                self.current_death_time = 0
                self.death_index += 1


                if self.death_index == 6:
                    #self.stop_death = True
                    #self.rect = None
                    self.kill()

                elif self.death_index <= 5:
                    self.path = 'img/big_chicken/big_chicken_dead' + str(self.death_index) + '.png'
                    self.image = pygame.transform.scale(pygame.image.load(self.path), (300,300))