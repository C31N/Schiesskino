import pygame


#  AMMO class
class Ammo(pygame.sprite.Sprite):
    """
    Class is used to hold AMMO count
    """
    def __init__(self, sounds):
        pygame.sprite.Sprite.__init__(self)
        self.sounds = sounds
        self.count = 8
        #self.shot = False

    def update(self, screen, ammo_group):
        self.sounds.update_ammo.play()
        self.count = 8
        for ammo in ammo_group:
            ammo.animation = False
            ammo.end()
        for i in range(0, 8):
            ammo_group.add(AmmoGroup(screen, i))
        return 8


    # change the AMMO amount
    def shot(self):
        if self.count == 0:
            # add EMPTY SHOT SOUND
            self.sounds.empty_shot_sound.play()
            # return 9 just because I want it to be like this))
            # it is needed in AmmoGroup update() method
            # to show that there is no need to show AMMO on the screen
            return False, 9
        else:
            self.count -= 1
            # add SHOT SOUND
            self.sounds.shot_sound.play()
            return True, self.count




# AMMO class
class AmmoGroup(pygame.sprite.Sprite):
    """
    Class is used to show AMMO on the screen
    """
    def __init__(self, screen, index):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen
        self.index = index

        self.count = 8
        self.show = True
        self.animation = False
        self.current_time = 0
        self.max_time = 1
        self.animation_index = 0

        self.img_index = 1
        self.path = 'img/ammo/Ammo' + str(self.img_index) + '.png'
        self.image = pygame.transform.scale(pygame.image.load(self.path), (50,80))
        self.rect = self.image.get_rect(center=(500+self.index*35, 550))
        #self.rect = None


    def update(self, dt, index):
        if index == 8:
            self.show = True

        elif index == 9:
            self.show = False

        elif 7 - self.index == index:
            self.show = False
            self.animation = True

        if self.show:
            self.image = pygame.transform.scale(pygame.image.load(self.path).convert_alpha(), (50, 80))
            self.screen.blit(self.image, self.rect)

        elif self.animation:
            self.screen.blit(self.image, self.rect)
            self.current_time +=1
            if self.current_time == 1:
                self.current_time = 0
                self.animation_index += 1

                if self.animation_index == 18:
                    self.animation = False
                    self.kill()


                elif self.animation_index <= 17:
                    path = 'img/ammo/Ammo' + str(self.animation_index) + '.png'
                    self.image = pygame.transform.scale(pygame.image.load(path).convert_alpha(), (50, 80))
                    self.rect.x += float(0.1*dt)
                    self.rect.y -= float(0.05 * dt)


        # for i in range(0, self.count):
        #     if i == 7:
        #         self.rect = self.image.get_rect(center=(500 + self.index * 10, 500))
        #         self.screen.blit(self.image, self.rect)
        #         self.stop = True
        #     else:
        #         self.rect = self.image.get_rect(center=(500+self.index*10, 500))
        #         self.screen.blit(self.image, self.rect)
    def end(self):
        self.animation = False
        self.index = -1
        self.kill()