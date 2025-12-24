import pygame



class MillChicken(pygame.sprite.Sprite):
    def __init__(self, screen, index):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen

        self.alive = True

        self.max_time = 3
        self.current_time = 0
        self.current_death_time = 0
        self.death_animation_index_list = [55, 1, 21, 39]
        self.death_animation_index = 0


        self.img_index_list = [28, 1, 10, 19]
        self.index = index
        self.animation_index = self.img_index_list[index]
        self.pos_list_x = [2379, 2380, 2381, 2380]
        self.pos_list_y = [310, 309, 310, 311]

        self.path = 'img/mill/chickenwindmil' + str(self.img_index_list[index]) + '.png'
        self.image = pygame.transform.scale(pygame.image.load(self.path), (200,200))
        self.rect = self.image.get_rect(bottomleft=(self.pos_list_x[index],self.pos_list_y[index]))
        self.img_mask = pygame.mask.from_surface(self.image)

        self.bottom_left = self.rect.bottomleft


    def update(self, move):
        if self.alive:
            if move == 'move_r':
                self.rect.x -= 40
            elif move == 'move_l':
                self.rect.x += 40
            else:

                self.screen.blit(self.image, self.rect)
                self.current_time += 1
                if self.current_time == self.max_time:
                    self.current_time = 0
                    self.animation_index += 1
                    if self.animation_index <= 35:
                        self.path = 'img/mill/chickenwindmil' + str(self.animation_index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (200,200))
                        self.img_mask = pygame.mask.from_surface(self.image)


                    elif self.animation_index == 36:
                        self.path = 'img/mill/chickenwindmil' + str(self.animation_index) + '.png'
                        self.image = pygame.transform.scale(pygame.image.load(self.path), (200, 200))
                        self.img_mask = pygame.mask.from_surface(self.image)

                        #self.animation_index = self.img_index_list[self.index]
                        self.animation_index = 1

        if not self.alive:
            #self.kill()
            self.current_death_time += 1
            self.screen.blit(self.image, self.rect)
            if self.current_death_time == 2:
                self.current_death_time = 0
                self.death_animation_index += 1
                if self.death_animation_index == 3:
                    self.death_animation_index = 0
                    self.kill()
                else:
                    self.path = 'img/mill/chickenwindmildead' + str(self.animation_index) + '_' + str(self.death_animation_index) + '.png'
                    self.image = pygame.transform.scale(pygame.image.load(self.path), (200, 200))


    # check the CURSOR position
    def check_shot(self, cursor, x, y):
        cursor_mask = pygame.mask.from_surface(cursor.image)
        offset = (x - self.rect.x, y - self.rect.y)
        result = self.img_mask.overlap(cursor_mask, offset)
        if result:
            return True
        else:
            return False