
class Timer():
    """
    class to check last 10 sec of PLAY mode
    """
    def __init__(self):
        self.timer_10 = True
        self.timer_9 = True
        self.timer_8 = True
        self.timer_7 = True
        self.timer_6 = True
        self.timer_5 = True
        self.timer_4 = True
        self.timer_3 = True
        self.timer_2 = True
        self.timer_1 = True

    def time_check(self, sounds, play_time):
        # if time is OUT
        if play_time == 90:
            return 1

        # if we are on the last 10 seconds
        elif 90 - play_time == 10 and self.timer_10:
            sounds.time_running.play()
            self.timer_10 = False

        elif 90 - play_time == 9 and self.timer_9:
            sounds.time_running.play()
            self.timer_9 = False

        elif 90 - play_time == 8 and self.timer_8:
            sounds.time_running.play()
            self.timer_8 = False

        elif 90 - play_time == 7 and self.timer_7:
            sounds.time_running.play()
            self.timer_7 = False

        elif 90 - play_time == 6 and self.timer_6:
            sounds.time_running.play()
            self.timer_6 = False

        elif 90 - play_time == 5 and self.timer_5:
            sounds.time_running.play()
            self.timer_5 = False

        elif 90 - play_time == 4 and self.timer_4:
            sounds.time_running.play()
            self.timer_4 = False

        elif 90 - play_time == 3 and self.timer_3:
            sounds.time_running.play()
            self.timer_3 = False

        elif 90 - play_time == 2 and self.timer_2:
            sounds.time_running.play()
            self.timer_2 = False

        elif 90 - play_time == 1 and self.timer_1:
            sounds.time_running.play()
            self.timer_1 = False
