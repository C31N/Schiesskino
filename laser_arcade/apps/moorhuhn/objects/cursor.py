import pygame
import random

from settings.score_manager import ScoreImgManager

# CURSOR class
class Cursor(pygame.sprite.Sprite):
    def __init__(self, screen, img_path):
        super().__init__()
        self.screen = screen
        self.simple = 'img/cursor/cursor.png'
        self.target = 'img/cursor/cursorred.png'
        self.image = pygame.image.load(self.simple)
        self.rect = self.image.get_rect()

    # updates the position
    def update(self):
        self.rect.center = pygame.mouse.get_pos()


    # shot the CHICKEN
    def shoot_chicken(self, sounds, chickens_group, check_shot, score_manager, scores_group):
        for chicken in chickens_group:
            # looking for a shot chicken
            if self.rect.colliderect(chicken.rect) and chicken.alive:
                if check_shot:
                    # add random SHOT CHICKEN SOUND
                    index = random.randint(0, 2)
                    sounds.return_chick_hits(index).play()

                    # update SCORE
                    #scores_group.add(ScoreManager(self.screen))
                    #scores_group.shot(chicken)
                    score1 = ScoreImgManager(self.screen, score_manager)
                    score1.show = True
                    scores_group.add(score1)
                    for score in scores_group:
                        if score.show:
                            score.shot(chicken)

                    # score.shot(chicken)

                    # CHICKEN is DEAD
                    chicken.alive = False
                    # break
                    return True

    # shot the PUMPKIN MAN
    def shoot_pumpkin(self, sounds, pumpkin, check_shot, score_manager, scores_group):
        # looking for a shot chicken
        if self.rect.colliderect(pumpkin.rect) and pumpkin.alive:
            if check_shot:
                sounds.pumpkin_shot_sound.play()


                # update SCORE
                score1 = ScoreImgManager(self.screen, score_manager)
                scores_group.add(score1)
                score1.show = True
                for score in scores_group:
                    if score.show:
                        score.shot(pumpkin)

                # CHICKEN is DEAD
                pumpkin.alive = False

                # break
                return True

    # shot the SIGN POST
    def shoot_sign_post(self, sounds, sign_post, check_shot, score_manager, scores_group):
        # looking for a shot chicken
        if self.rect.colliderect(sign_post.rect):
            if check_shot:
                sounds.sign_post_sound.play()

                # update SCORE
                score1 = ScoreImgManager(self.screen, score_manager)
                scores_group.add(score1)
                score1.show = True
                for score in scores_group:
                    if score.show:
                        score.shot(sign_post)

                # shot the SIGH POST
                if sign_post.shot:
                    sign_post.shot = False
                else:
                    sign_post.shot = True

                # break
                return True

    def shoot_big_chicken(self, sounds, cursor, big_chicken_group, check_shot, score_manager, scores_group):
        for big_chicken in big_chicken_group:
            # looking for a shot chicken
            if self.rect.colliderect(big_chicken.rect):
                if check_shot:
                    index = random.randint(0, 2)
                    sounds.return_chick_hits(index).play()

                    # update SCORE
                    score1 = ScoreImgManager(self.screen, score_manager)
                    scores_group.add(score1)
                    score1.show = True
                    for score in scores_group:
                        if score.show:
                            score.shot(big_chicken)

                    # shot the SIGH POST
                    if big_chicken.alive:
                        big_chicken.alive = False
                        big_chicken.current_time = 0


                # break
                return True

    def shoot_mill(self, cursor, x, y, sounds, mill, check_shot, score_manager, scores_group):
        for chicken in mill:
            # looking for a shot chicken
            k = chicken.check_shot(cursor,x,y)
            if k:
                if check_shot:
                    index = random.randint(0, 2)
                    sounds.return_chick_hits(index).play()

                    # update SCORE
                    score1 = ScoreImgManager(self.screen, score_manager)
                    scores_group.add(score1)
                    score1.show = True
                    for score in scores_group:
                        if score.show:
                            score.shot(chicken)

                    # shot the SIGH POST
                    if chicken.alive:
                        chicken.alive = False
                        chicken.current_time = 0


                # break
                return True

    def shoot_tree(self, sounds, trees, check_shot):
        for tree in trees:
            if self.rect.colliderect(tree.rect):
                if check_shot:
                    sounds.tree_hit_sound.play()
                # break
                return True
        return False




    def check_main_buttons(self, cursor, x, y, main_buttons, name):
        for button in main_buttons:
            if button.check(cursor, x, y) and button.name == name:
                return True
        return False

    def change_main_button(self, cursor, x, y, main_buttons, name):
        for button in main_buttons:
            if button.check(cursor, x, y) and button.name == name:
                new_button = 'img/main_menu_background/' + name + '_h.png'
                button.change(new_button)
                return True
            else:
                if button.name == name:
                    new_button = 'img/main_menu_background/' + name + '_normal.png'
                    button.change(new_button)
        return False

    def change_pressed_button(self, cursor, x, y, main_buttons, name):
        for button in main_buttons:
            if button.check(cursor, x, y) and button.name == name:
                new_button = 'img/main_menu_background/' + name + '_pressed.png'

                button.change(new_button)
                print('it is', new_button)
                return True
            else:
                if button.name == name:
                    new_button = 'img/main_menu_background/' + name + '_normal.png'
                    button.change(new_button)
        return False