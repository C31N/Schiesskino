from settings.buttons import *

# BEST SCORE
def best_score_loop(screen, sounds, cursor_group, buttons, user_name, score,game):
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
            # elif event.type == pygame.KEYDOWN:
            #     if event.key == pygame.K_ESCAPE:
            #         running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if buttons.best_score_buttons[0].collidepoint(pygame.mouse.get_pos()):
                    if event.button == 1:
                        sounds.main_theme_sound.stop()
                        sounds.button_click_sound.play()
                        running = False
                        return True

        buttons.draw_text('Best Score Table', 50, 530, 100)
        buttons.draw_text(f'Name:', 30, 350, 200)
        buttons.draw_text(f'Score:', 30, 650, 200)
        game.highscore.print(350,230)
        buttons.draw_best_score('Main Menu', 40, 140, 550)

        # draw an image instead of REAL CURSOR
        cursor_group.draw(screen)
        cursor_group.update()

        pygame.display.flip()