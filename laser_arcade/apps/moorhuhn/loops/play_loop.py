import sys
import time

import pygame.event



from settings.timer import Timer
from objects_imports import *
from objects.background import *
from objects.trees import *


from random import randint
from objects.background import *

# PLAY mode
def play_loop(clock, screen, sounds, buttons, cursor, cursor_group, chickens_small_group, chickens_mid_group, chickens_big_group, ammo, ammo_group, score_manager, scores_group, pumpkin, sign_post, big_chicken_group, mill):

    # background SOUND
    sounds.play_background.play(-1)

    # to check that we are still playing
    running = True
    # to check last 10 sec of the PLAY
    timer = Timer()

    # turn off the image of the REAL 'CURSOR'
    pygame.mouse.set_visible(False)

    # initialize time value
    # to know if we have to start counting time
    init_time = 0

    big_chick_timer = 0

    ammo_count = -1

    # TREES
    tree1 = Tree(screen, 'img/tree/trunkBig1.png', 300, 200)
    tree2 = Tree(screen, 'img/tree/trunkSmall1.png',1900, 100)
    trees = pygame.sprite.Group()
    trees.add(tree1)
    trees.add(tree2)

    # CAMERA
    camera1 = Camera(0, 0, 96)
    camera2 = Camera(0, 80, 1900)
    camera3 = Camera(0, 130, 890)
    camera4 = Camera(0, 160, 1900)




    running = True
    while running:
        # Returns milliseconds between each call to 'tick'. The convert time to seconds
        dt = clock.tick(60)

        # for camera moving
        cursor_x = cursor.rect.x
        if cursor_x >= 750 and cursor_x <= 800:
            if camera1.move(2) and camera2.move(5) and camera3.move(15) and camera4.move(40):
                chickens_small_group.update(dt, 'move_r')
                chickens_mid_group.update(dt, 'move_r')
                chickens_big_group.update(dt, 'move_r')
                big_chicken_group.update('move_r')

                mill.update('move_r')
                pumpkin.update('move_r')
                sign_post.update('move_r')
                trees.update('move_r')

        elif cursor_x <= 20 and cursor_x >= -20:
            if camera1.move(-2) and camera2.move(-5) and camera3.move(-15) and camera4.move(-40):
                chickens_small_group.update(dt, 'move_l')
                chickens_mid_group.update(dt, 'move_l')
                chickens_big_group.update(dt, 'move_l')
                big_chicken_group.update('move_l')

                mill.update('move_l')
                pumpkin.update('move_l')
                sign_post.update('move_l')
                trees.update('move_l')






        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                sounds.play_background.stop()
                running = False
                pygame.quit()
                sys.exit()

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    sounds.play_background.stop()
                    #score_manager.score = 0
                    running = False
                    return 1, 0
                # reload ammo if it is necessary
                elif event.key == pygame.K_SPACE:
                    if ammo.count < 8:
                        ammo_count = ammo.update(screen, ammo_group)

            # add new CHICKEN on the screen0
            elif event.type == pygame.USEREVENT:
                chickens_small_group.add(ChickenSmall(screen, randint(100, 200)))
                chickens_mid_group.add(ChickenMiddle(screen, randint(100, 300)))
                chickens_big_group.add(ChickenBig(screen, randint(100, 500)))


            # checks if we have shot a CHICKEN
            elif event.type == pygame.MOUSEBUTTONDOWN:
                # check for AMMO amount
                check_shot, ammo_count = ammo.shot()
                x, y = event.pos

                # if we shot BIG CHICKEN
                if cursor.shoot_big_chicken(sounds, cursor, big_chicken_group, check_shot, score_manager, scores_group):
                    break
                # if we shot TREE
                elif cursor.shoot_tree(sounds, trees, check_shot):
                    break

                # if we shot the CHICKEN
                elif cursor.shoot_chicken(sounds, chickens_big_group, check_shot, score_manager, scores_group):
                    print('big')
                    break
                elif cursor.shoot_chicken(sounds, chickens_mid_group, check_shot, score_manager, scores_group):
                    print('mid')
                    break
                elif cursor.shoot_chicken(sounds, chickens_small_group, check_shot, score_manager, scores_group):
                    print('small')
                    break

                # if we shot the CHICKENS on the MILL
                elif cursor.shoot_mill(cursor, x, y, sounds, mill, check_shot, score_manager, scores_group):
                    break
                # if we shot SIGN POST
                elif cursor.shoot_sign_post(sounds, sign_post, check_shot, score_manager, scores_group):
                    break
                # if we shot the PUMPKIN MAN
                elif cursor.shoot_pumpkin(sounds, pumpkin, check_shot, score_manager, scores_group):
                    break


        # --------- BIG CHICKEN POP UPS ---------
        big_chick_timer += 1
        if big_chick_timer == 40:
            sounds.big_chicken_pops_up_sound.play()
            x = randint(100, 1700)
            big_chicken_group.add(BigChicken(screen, (x, 450)))
            big_chick_timer = -300



        # --------- COUNT PLAY TIME ---------
        # in purpose to make sure that we start counting only ones
        # when we start the play_loop
        init_time += 1
        if init_time == 1:
            start_time = time.time()
        play_time = round(time.time() - start_time)





        # --------- CHECK LEFT TIME ---------
        # if the timer is got down to 0
        play_time_check = timer.time_check(sounds, play_time)
        if play_time_check == 1:
            sounds.play_background.stop()
            sounds.game_over_sound.play()
            running = False
            # go to the BEST SCORE mode

            return 2, score_manager.return_score()


        # --------------- UPDATE -----------------------
        screen.fill((90, 100, 45))
        screen.blit(sky, (-camera1.rect[0], camera1.rect[1]))
        screen.blit(hills, (-camera2.rect[0], camera2.rect[1]))
        # update FLY CHICKEN SMALL
        chickens_small_group.draw(screen)
        chickens_small_group.update(dt, 'no')
        screen.blit(castle, (-camera3.rect[0], camera3.rect[1]))
        # update FLY CHICKEN MID
        chickens_mid_group.draw(screen)
        chickens_mid_group.update(dt, 'no')
        screen.blit(green, (-camera4.rect[0], camera4.rect[1]))
        # updates PUMPKIN state
        pumpkin.update('no')

        # update MILL
        mill.update('no')
        # update FLY CHICKEN BIG
        chickens_big_group.draw(screen)
        chickens_big_group.update(dt, 'no')




        # updates FLY CHICKEN/S state


        # updates SIGN POST
        sign_post.update('no')

        # updates SCORE progress
        scores_group.update()

        # update TREES
        trees.update('no')

        # shows LEFT PLAY TIME
        buttons.draw_text(f'Time: {90 - play_time}', 30, 70, 20)
        # shows SCORE progress
        buttons.draw_text(f'Score: {score_manager.return_score()}', 30, 710, 20)


        # update BIG CHICKEN
        big_chicken_group.update('no')

        # update AMMO
        ammo_group.update(dt, ammo_count)

        # draw an image instead of REAL CURSOR
        cursor_group.draw(screen)
        cursor_group.update()

        pygame.display.flip()