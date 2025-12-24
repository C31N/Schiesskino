import pygame


# HELP INFORMATION
def help_loop(screen, sounds, cursor_group, buttons):
    running = True

    sounds.main_theme_sound.play(-1)
    bg = pygame.transform.scale(pygame.image.load('img/help_background/help_back.png'), (800,600))
    bg_rect = bg.get_rect()

    mouse1 = pygame.transform.flip(pygame.transform.scale(pygame.image.load('img/help_background/mouse-right-click.png').convert_alpha(),(150,100)),True,False)
    mouse1_rect = mouse1.get_rect(center = (370,150))

    space = pygame.transform.scale(pygame.image.load('img/help_background/space.png').convert_alpha(),(150, 160))
    space_rect = space.get_rect(center=(370, 330))

    esc = pygame.transform.scale(pygame.image.load('img/help_background/6.jpg').convert_alpha(), (90, 90))
    esc_rect = esc.get_rect(center=(370, 500))

    while running:
        screen.fill((90, 15, 45))
        screen.blit(bg, bg_rect)
        screen.blit(mouse1,mouse1_rect)
        screen.blit(space,space_rect)
        screen.blit(esc,esc_rect)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                sounds.main_theme_sound.stop()
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    sounds.main_theme_sound.stop()
                    running = False
                    return True
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if buttons.help_buttons[0].collidepoint(pygame.mouse.get_pos()):
                    if event.button == 1:
                        sounds.main_theme_sound.stop()
                        sounds.button_click_sound.play()
                        running = False
                        return True

        # just a text
        pygame.font.init()
        font1 = pygame.font.Font(None, 50)
        ier = font1.render(' - SHOOT', True, '#FFE80E')
        ier_rect = ier.get_rect(center=(520, 150))
        screen.blit(ier, ier_rect)

        font2 = pygame.font.Font(None, 50)
        crr = font2.render(' - RECHARGE', True, '#FFE80E')
        crr_rect = crr.get_rect(center=(580, 330))
        screen.blit(crr, crr_rect)

        font3 = pygame.font.Font(None, 50)
        pp = font3.render(' - MAIN MENU', True, '#FFE80E')
        pp_rect = pp.get_rect(center=(580, 500))
        screen.blit(pp, pp_rect)

        # buttons.draw_text(' - SHOOT', 40, 520, 150)
        # buttons.draw_text(' - RECHARGE', 40, 580, 330)
        # buttons.draw_text(' - MAIN MENU',40, 580, 500)
        # buttons.draw_help('Main Menu', 50, 300, 400)

        # draw an image instead of REAL CURSOR
        cursor_group.draw(screen)
        cursor_group.update()

        pygame.display.flip()