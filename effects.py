"""Процедурные анимации Капри: подпрыгивание (радость), дрожь (стёб, обида), пульс (удивление), «кувырок»
и «зевок» (скука). Рисуются кодом поверх обычных поз, поэтому новых картинок не нужно.
Модуль чисто математический, без Qt: sample() возвращает смещение, масштаб и наклон на момент t."""
import math
from collections import namedtuple

Pose = namedtuple("Pose", "dx dy sx sy rot glitch flash")
REST = Pose(0.0, 0.0, 1.0, 1.0, 0.0, 0.0, False)

DURATION = {"bounce": 1.3, "shake": 1.0, "pulse": 1.1, "flip": 1.0, "droop": 1.8}

_BOUNCE_SIGN = 1


def set_bounce_direction(sign):
    """Направление инерции для анимации bounce: -1 влево, +1 вправо."""
    global _BOUNCE_SIGN
    _BOUNCE_SIGN = -1 if sign < 0 else 1

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
    if name == "bounce":          # инерция после перетаскивания: смещение с overshoot, затухающее к концу
        sg = _BOUNCE_SIGN
        # 0 → пик к середине → 0: по инерции ещё чуть дальше, потом возврат на место
        ease = math.sin(k * math.pi) * (1.0 - 0.35 * k)
        dx = sg * ease * 38.0
        dy = -abs(math.sin(k * math.pi)) * 12 * (1.0 - k)
        squash = 1 + 0.05 * math.cos(k * math.pi * 2) * (1.0 - k)
        rot = sg * math.sin(k * math.pi) * 0.045 * (1.0 - k)
        glitch = 0.12 * fade
        return Pose(dx, dy, 1.0 / squash, squash, rot, glitch, False)
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
