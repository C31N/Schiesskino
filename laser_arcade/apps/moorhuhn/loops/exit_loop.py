import pygame


# EXIT
def exit_loop(screen, sounds, cursor_group, buttons):
    running = True

    sounds.main_theme_sound.play(-1)

    bg = pygame.transform.scale(pygame.image.load('img/help_background/help_back.png'), (800, 600))
    bg_rect = bg.get_rect()

    while running:
        screen.fill((90, 22, 45))
        screen.blit(bg, bg_rect)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                sounds.main_theme_sound.stop()
                running = False
            elif event.type == pygame.KEYDOWN:
                # -> back to Main Menu
                if event.key == pygame.K_ESCAPE:
                    sounds.main_theme_sound.stop()
                    running = False
                    return 0


            elif event.type == pygame.MOUSEBUTTONDOWN:
                # EXIT the GAME
                if buttons.exit_buttons[0].collidepoint(pygame.mouse.get_pos()):
                    if event.button == 1:
                        sounds.main_theme_sound.stop()
                        sounds.button_click_sound.play()
                        running = False
                        return 1
                # back to Main Menu
                elif buttons.exit_buttons[1].collidepoint(pygame.mouse.get_pos()):
                    if event.button == 1:
                        sounds.main_theme_sound.stop()
                        sounds.button_click_sound.play()
                        running = False
                        return 2

        font = pygame.font.Font(None, 60)
        top = font.render('Are you sure?', True, '#FFE80E')
        top_rect = top.get_rect(center=(540, 200))
        screen.blit(top, top_rect)

        font2 = pygame.font.Font(None, 60)
        top1 = font2.render('Yes', True, '#FFE80E')
        top1_rect = top1.get_rect(center=(530, 280))
        buttons.exit_buttons.append(top1_rect)
        screen.blit(top1, top1_rect)

        font3 = pygame.font.Font(None, 60)
        top2 = font3.render('No', True, '#FFE80E')
        top2_rect = top2.get_rect(center=(530, 350))
        buttons.exit_buttons.append(top2_rect)
        screen.blit(top2, top2_rect)


        # draw an image instead of REAL CURSOR
        cursor_group.draw(screen)
        cursor_group.update()

        pygame.display.flip()