"""Процедурные анимации Капри: подпрыгивание (радость), дрожь (стёб, обида), пульс (удивление), «кувырок»
и «зевок» (скука). Рисуются кодом поверх обычных поз, поэтому новых картинок не нужно.
Модуль чисто математический, без Qt: sample() возвращает смещение, масштаб и наклон на момент t."""
import math
from collections import namedtuple

Pose = namedtuple("Pose", "dx dy sx sy rot glitch flash")
REST = Pose(0.0, 0.0, 1.0, 1.0, 0.0, 0.0, False)

DURATION = {
    "bounce": 1.3, "shake": 1.0, "pulse": 1.1, "flip": 1.0, "droop": 1.8,
    "wave": 1.0, "nod": 0.85, "shiver": 0.9, "tada": 0.95, "jump": 0.95,
    "wiggle": 1.0, "pop": 0.55, "sway": 1.2, "blink2": 0.28, "hover": 0.8,
}

# какая ситуация комментатора какую анимацию запускает (None — без анимации)
FOR_SITUATION = {
    "win": "bounce", "streak_win": "flip", "loss": "shake", "streak_loss": "droop",
    "retry": "shake", "quick_quit": "droop", "marathon": "pulse", "procrastinate": "pulse",
    "night": "droop", "long_sit": "droop", "idle": "droop", "back": "bounce", "video_next": "pulse",
    "wallpaper": "pulse", "screen": "pulse",
}
FOR_MODE = {"cheer": "bounce", "roast": "shake"}   # подколка в режиме «подбодрить» или «стебать»


def for_situation(situation, mode=None):
    if situation in ("win", "loss") and mode in FOR_MODE:
        return FOR_MODE[mode]
    return FOR_SITUATION.get(situation)


def duration(name):
    return DURATION.get(name, 0.0)


def sample(name, t, h=380.0):
    """Поза в момент t секунд от старта; после окончания — REST. h — высота фигуры (для прыжка в пикселях)."""
    d = DURATION.get(name)
    if d is None or t < 0 or t >= d:
        return REST
    k = t / d
    fade = 1 - k
    if name == "bounce":          # три затухающих прыжка с приседанием перед ними
        n = 3
        ph = (k * n) % 1.0
        amp = (1 - k * .65) * h * .09
        up = math.sin(ph * math.pi)
        squash = 1 + .06 * math.cos(ph * 2 * math.pi) * fade
        return Pose(0, -amp * up, 1 / squash, squash, math.sin(k * math.pi * 6) * .03 * fade, 0.0, False)
    if name == "wave":          # затухающее качание всей фигуры (как "приайтер")
        amp = 13.0 * (1 - 0.45 * k)
        dx = amp * math.sin(t * 12) * (1.0 - 0.3 * k)
        rot = (amp / 350.0) * math.sin(t * 12 + 1.2) * (1.0 - 0.3 * k)
        return Pose(dx, 0, 1.0, 1.0, rot, 0.0, False)
    if name == "nod":           # два быстрых кивка вниз-вверх
        n = 2
        ph = (k * n) % 1.0
        dy = amp = h * .08 if False else 16.0 * (1 - 0.35 * k) * math.sin(ph * math.pi * 2 + 0.4)
        rot = 0.07 * math.sin(ph * math.pi * 2) * fade
        return Pose(0, dy, 1.0, 1.0, rot, 0.0, False)
    if name == "shiver":        # мелкая дрожь по вертикали и горизонтали
        dx = math.sin(t * 88) * 9 * fade + math.sin(t * 57) * 3 * fade
        dy = math.sin(t * 63) * 4 * fade
        glitch = .35 * fade
        return Pose(dx, dy, 1.0, 1.0, math.sin(t * 47) * .02 * fade, glitch, k < .1)
    if name == "tada":         # два «виляния» растущего масштаба (как радость)
        beat = abs(math.sin(k * math.pi * 3))
        s = 1.0 + .18 * beat * fade
        dx = 12.0 * math.sin(k * math.pi * 6) * (1 - .5 * k)
        rot = .08 * math.sin(k * math.pi * 6) * fade
        return Pose(dx, -6.0 * (1 - k), s, s, rot, .08 * (1 - k), False)
    if name == "jump":         # короткий прыжок с приседанием в конце
        up = math.sin(min(1.0, k * 1.1) * math.pi)
        dy = -h * .12 * up * (1 - .15 * k)
        squash = 1.0 + .1 * (1 - up) * fade
        return Pose(0, dy, 1.0, squash, 0.0, .05 * (1 - k), False)
    if name == "wiggle":       # вихляние контуром (перетаскивание)
        rot = .09 * math.sin(t * 16) * fade
        sx = 1.0 + .08 * math.sin(t * 14) * fade
        sy = 1.0 - .05 * math.sin(t * 14) * fade
        return Pose(math.sin(t * 12) * 7 * fade, 0, sx, sy, rot, 0.0, False)
    if name == "pop":          # масштабный «всплыв» (восклицание)
        s = 1.0 + .35 * math.sin(k * math.pi) * fade
        return Pose(0, 0, s, s, 0.0, 0.15 * fade, False)
    if name == "sway":         # плавное покачивание (подумал)
        rot = math.sin(k * math.pi * 2) * .06 * (1 - 0.5 * k)
        dx = math.sin(k * math.pi * 2 + 1) * 6 * (1 - 0.5 * k)
        return Pose(dx, 0, 1.0, 1.0, rot, 0.0, False)
    if name == "blink2":       # быстрое мигание глаз
        if k < 0.5:
            return Pose(0, 0, 1.0, 1.0, 0.0, 0.8 * (1 - k), False)
        return Pose(0, 0, 1.0, 1.0, 0.0, 0.0, False)
    if name == "hover":        # слегка приподнялась
        dy = -10.0 * (1.0 - 0.4 * k)
        hatch = math.sin(t * 8) * 2 * (1 - 0.4 * k)
        return Pose(hatch, dy, 1.0, 1.0, math.sin(t * 6) * .015, 0.0, False)
    if name == "shake":           # быстрая дрожь с глитчем и красной вспышкой в начале
        dx = math.sin(t * 70) * 11 * fade
        return Pose(dx, math.sin(t * 53) * 3 * fade, 1.0, 1.0, math.sin(t * 61) * .04 * fade,
                    .5 * fade + .1, k < .12)
    if name == "pulse":           # два «удара сердца»: увеличение и возврат
        beat = max(0.0, math.sin(k * math.pi * 4)) * fade
        s = 1 + .09 * beat
        return Pose(0, 0, s, s, 0.0, .15 * beat, False)
    if name == "flip":            # быстрый разворот по горизонтали (как монетка) и прыжок
        sx = math.cos(k * math.pi * 2)
        sx = math.copysign(max(.04, abs(sx)), sx)
        return Pose(0, -math.sin(k * math.pi) * h * .07, sx, 1.0, 0.0, .2 if abs(sx) < .3 else 0.0, False)
    if name == "droop":           # оседает вниз и покачивается, потом выпрямляется
        sag = math.sin(min(1.0, k * 1.6) * math.pi / 2) if k < .6 else 1 - (k - .6) / .4
        return Pose(math.sin(t * 3) * 3 * sag, h * .025 * sag, 1 + .02 * sag, 1 - .05 * sag,
                    math.sin(t * 2.2) * .025 * sag, 0.0, False)
    return REST


class Player:
    """Хранит текущую анимацию. Время подаётся снаружи — удобно для тестов."""
    def __init__(self, now):
        self.now, self.name, self.start = now, None, 0.0

    def play(self, name, delay=0.0):
        if name in DURATION:
            self.name, self.start = name, self.now() + delay
            return True
        return False

    def pose(self, h=380.0):
        if not self.name:
            return REST
        t = self.now() - self.start
        if t < 0:
            return REST
        if t >= DURATION[self.name]:
            self.name = None
            return REST
        return sample(self.name, t, h)
