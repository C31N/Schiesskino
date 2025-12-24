import pygame
pygame.font.init()
# class to draw buttons
class Button:
    def __init__(self, screen):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen
        self.main_menu_buttons = []
        self.pause_buttons = []
        self.best_score_buttons = []
        self.help_buttons = []
        self.exit_buttons = []

    def add_main_menu(self, button):
        self.main_menu_buttons.append(button)

    # draw text on screen
    def draw_text(self, text,size, pos_x, pos_y):

        font = pygame.font.Font('fonts/AA_Magnum.ttf', size)
        button_text = font.render(text, True, (255,255,255))
        button_rect = button_text.get_rect()
        button_rect.center = (pos_x, pos_y)

        self.screen.blit(button_text, button_rect)

    # PAUSE mode buttons
    def draw_pause(self, text, size, pos_x, pos_y):
        font = pygame.font.SysFont('Comic Sans MS', size)
        button_text = font.render(text, True, (0, 1, 1))
        button_rect = button_text.get_rect()
        button_rect.center = (pos_x, pos_y)

        self.pause_buttons.append(button_rect)
        self.screen.blit(button_text, button_rect)

    # BEST SCORE mode buttons
    def draw_best_score(self, text, size, pos_x, pos_y):
        font = pygame.font.SysFont('Comic Sans MS', size)
        button_text = font.render(text, True, ('#FFE80E'))
        button_rect = button_text.get_rect()
        button_rect.center = (pos_x, pos_y)

        self.best_score_buttons.append(button_rect)
        self.screen.blit(button_text, button_rect)


    # HELP mode buttons
    def draw_help(self, text, size, pos_x, pos_y):
        font = pygame.font.SysFont('Comic Sans MS', size)
        button_text = font.render(text, True, (0, 1, 1))
        button_rect = button_text.get_rect()
        button_rect.center = (pos_x, pos_y)

        self.help_buttons.append(button_rect)
        self.screen.blit(button_text, button_rect)

    # EXIT mode buttons
    def draw_exit(self, text, size, pos_x, pos_y):
        font = pygame.font.SysFont('Comic Sans MS', size)
        button_text = font.render(text, True, (0, 1, 1))
        button_rect = button_text.get_rect()
        button_rect.center = (pos_x, pos_y)

        self.exit_buttons.append(button_rect)
        self.screen.blit(button_text, button_rect)


class MainMenuButtons(pygame.sprite.Sprite):
    def __init__(self, screen, index):
        pygame.sprite.Sprite.__init__(self)
        self.screen = screen

        self.position_list = [(100,550), (330,550), (560,550), (710,550)]
        self.name_list = ['start', 'score', 'menu', 'exit']
        self.pos = self.position_list[index]
        self.name = self.name_list[index]

        self.path = 'img/main_menu_background/' + self.name + '_normal.png'
        self.image = pygame.image.load(self.path)
        self.rect = self.image.get_rect(center=self.pos)
        self.mask = pygame.mask.from_surface(self.image)

    def update(self):
        self.screen.blit(self.image, self.rect)

    def check(self, cursor, x, y):
        cursor_mask = pygame.mask.from_surface(cursor.image)
        offset = (x - self.rect.x, y - self.rect.y)
        result = self.mask.overlap(cursor_mask, offset)
        if result:
            return True
        else:
            return False

    def change(self, new_path):
        self.path = new_path
        self.image = pygame.image.load(self.path)
        self.rect = self.image.get_rect(center=self.pos)
        self.screen.blit(self.image, self.rect)
