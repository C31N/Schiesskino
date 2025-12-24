import pygame

from settings.buttons import *

def user_name_loop(screen, sounds):
    running = True
    user_name = '|'
    user_tick = 30
    box_width = True
    bg4 = pygame.transform.scale(pygame.image.load('img/help_background/help_back.png'), (800, 600))
    bg_rect = bg4.get_rect()
    # turn off CURSOR
    pygame.mouse.set_visible(False)
    while running:
        screen.fill((90, 22, 45))
        screen.blit(bg4, bg_rect)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            # enter USER NAME
            elif event.type == pygame.KEYDOWN:
                user_name = user_name.replace('|', '')
                user_tick = 150
                if event.key == pygame.K_RETURN:
                    running = False

                    if len(user_name) == 0:
                        user_name = 'NO NAME'
                    return True, user_name
                elif event.key == pygame.K_ESCAPE:
                    running = False
                    if len(user_name) == 0:
                        user_name = 'NO NAME'
                    return False, user_name
                elif event.key == pygame.K_BACKSPACE:
                    # переписываем user_name от начала до предпоследнего символа
                    user_name = user_name[0:-1]
                else:
                    # если длина в пределах нормы
                    if box_width:
                        # type text SOUND
                        sounds.type_sound.play()
                        if len(user_name) != 12:
                            user_name += event.unicode
                user_name += '|'

        b = Button(screen)
        font2 = pygame.font.Font(None, 30)
        b_d = font2.render('ENTER USER NAME', True, '#FFE80E')
        b_surf_rect = b_d.get_rect(center=(540, 250))
        screen.blit(b_d, b_surf_rect)

        user_tick -= 1
        if user_tick == 0:
            user_name = user_name[: - 1]
        if user_tick == -150:
            user_name += '|'
            user_tick = 150

        pygame.font.init()
        font = pygame.font.Font(None, 40)
        user_name_surf = font.render(user_name, True, '#FFE80E')
        user_name_rect = pygame.Rect(440, 294, 400, 50)


        # для регулирования длины
        # user_name_rect.w = max(200, user_name_surf.get_width() + 30)
        # if user_name_rect.w >= 700:
        #     box_width = False
        # pygame.draw.rect(screen, (230,100,230), user_name_rect, 2)

        screen.blit(user_name_surf, (user_name_rect.x + 10, user_name_rect.y + 10))

        #b.draw_text('Press ENTER to continue', 15, 400,400)
        font1 = pygame.font.Font(None, 30)
        b_surf = font1.render('Press ENTER to continue', True, '#FFE80E')
        b_surf_rect = b_surf.get_rect(center=(140,550))
        screen.blit(b_surf,b_surf_rect)
        pygame.display.flip()