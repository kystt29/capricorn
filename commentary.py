"""Капри комментирует, что ты делаешь, и стебёт, когда ты проигрываешь.

Что она видит и откуда:
- какая программа сейчас в фокусе (раз в 15 секунд) и как давно ты ничего не нажимал;
- запуски и закрытия игр (их ведёт games.py);
- твои фразы «я слил», «меня убили» — по имени и, если включено "react_to_voice", и без имени, пока идёт игра.

Победа или поражение в самой игре ей не видны, поэтому «проигрыш» — это то, что ты сам сказал, и косвенные признаки:
катки подряд по несколько минут, быстрый выход, многочасовой марафон.

Приватность: заголовок окна используется только на месте, чтобы понять, что это (видео, чаты, работа), — он нигде
не сохраняется, не пишется в журнал и никуда не отправляется. Подколки придумывает только локальная нейросеть;
режим Claude для этого не используется. Выключить: "commentary": false, галочка в трее или «Капри, не комментируй»."""
import logging
import ntpath
import random
import re
import threading
import time
from collections import namedtuple

import games

log = logging.getLogger("capri")

POLL_SECONDS = 15
IDLE_SECONDS = 180            # дольше без нажатий — считаем, что ты отошёл
SWITCH_CONFIRM = 2            # столько проверок подряд должно показать новое занятие, чтобы это не было миганием окон
WORK_STREAK_MIN = 10          # сколько минут работы нужно, чтобы уход в видео или чаты считался прокрастинацией
PROCRASTINATE_WINDOW = 120    # и за сколько секунд после работы он должен случиться
DEFAULT_COOLDOWN = 45 * 60
COOLDOWN = {                  # как редко повторяется один и тот же вид подколки (секунды)
    "loss": 120, "retry": 600, "quick_quit": 900, "marathon": 3600,
    "procrastinate": 1800, "night": 3 * 3600, "long_sit": 2 * 3600,
}
LONG_MINUTES = {"video": 60, "social": 45, "work": 120, "office": 120, "web": 90}
LONG_SIT_MINUTES = 180
ENTER_CHANCE = {"video": 0.35, "social": 0.25}
MARATHON_MINUTES = 180
RETRY_WINDOW = 15 * 60        # новая сессия той же игры не позже чем через столько после прошлой…
RETRY_MAX_MINUTES = 25        # …и обе короче стольки минут — похоже на серию проигранных катов
QUICK_QUIT_MINUTES = 6

Sample = namedtuple("Sample", "exe title idle")


# ---------------- фразы ----------------
# Фразы проходят через norm() из actions.py: нижний регистр, «ё» → «е», без знаков препинания.

_VERBS = (r"(проиграл\w*|слил\w*|сдох\w*|умер\w*|вайпнул\w*|зафейлил\w*|продул\w*|слетел\w*|"
          r"не прошел|не прошла|не смог\w* пройти|не справил\w*)")
LOSS_RE = (r"^(ну |блин |да |все |всё |ой |ай )*("
           rf"((я|мы) (опять |снова |только что |уже |все |всё )*{_VERBS})|"
           r"((меня|нас) (опять |снова |только что )*(убили|убил|слили|разнесли|вынесли|уничтожили))|"
           r"((опять|снова) (проиграл\w*|слил\w*|сдох\w*|умер\w*|вайп\w*))|"
           r"(проигрыш|вайп|поражение)"
           r")( |$)")

# То, что можно услышать без имени, пока идёт игра («слили», «убили»): строже, чтобы не реагировать на любой разговор.
OVERHEARD_RE = re.compile(r"\b(слил\w*|проиграл\w*|сдох\w*|убили|вайп\w*|вынесли|разнесли|опять умер|снова умер)\b")

_OFF = (r"\bне (комментируй|коментируй|стебай|подкалывай)|"
        r"(перестань|хватит|прекрати)( меня)? (комментировать|коментировать|стебать|подкалывать)|"
        r"(выключи|отключи) (комментарии|подколки|стеб)|без (комментариев|подколок)")
_ON = (r"^((начни|давай|можешь|продолжай|включи) )?(меня )?(комментируй|коментируй|стебай|подкалывай|"
       r"комментировать|стебать|подкалывать)( меня| мои действия| все| всё)?$|"
       r"включи (комментарии|подколки|стеб)")
TOGGLE_RE = f"{_OFF}|{_ON}"


def toggle_wants_off(c):
    return bool(re.search(_OFF, c))


def heard_loss(text):
    """Похоже ли услышанное без имени на «слили / проиграл» (для react_to_voice)."""
    return bool(OVERHEARD_RE.search((text or "").lower().replace("ё", "е")))


# ---------------- что за занятие ----------------

BROWSERS = {"chrome", "msedge", "firefox", "opera", "opera_gx", "brave", "vivaldi", "yandex", "browser"}
EXE_CATEGORIES = {
    "telegram": "social", "discord": "social", "whatsapp": "social", "viber": "social", "skype": "social",
    "vk": "social", "max": "social",
    "vlc": "video", "mpc-hc": "video", "mpc-hc64": "video", "mpc-be": "video", "mpc-be64": "video",
    "potplayer": "video", "potplayermini": "video", "potplayermini64": "video", "kmplayer": "video",
    "code": "work", "cursor": "work", "pycharm64": "work", "idea64": "work", "webstorm64": "work",
    "clion64": "work", "rider64": "work", "devenv": "work", "sublime_text": "work", "notepad++": "work",
    "windowsterminal": "work", "wt": "work", "cmd": "work", "powershell": "work",
    "winword": "office", "excel": "office", "powerpnt": "office", "onenote": "office", "soffice": "office",
    "spotify": "music", "yandexmusic": "music", "aimp": "music",
}
# Что значит страница в браузере — по заголовку окна. Заголовок дальше не идёт и нигде не хранится.
TITLE_CATEGORIES = [
    (r"youtube|twitch|netflix|кинопоиск|rutube|vk видео|vk video|okko|wink|start\.ru", "video"),
    (r"вконтакте|vk\.com|telegram|whatsapp|instagram|tiktok|reddit|twitter|facebook|одноклассники", "social"),
    (r"github|stack overflow|stackoverflow|документаци|localhost|leetcode|jupyter|codeforces|docs\.", "work"),
]


def classify(exe, title, game_paths=()):
    """«game» / «video» / «social» / «work» / «office» / «web» / «music» или None (не комментируем)."""
    if not exe:
        return None
    if ntpath.normcase(exe) in game_paths or games.detect(exe):
        return "game"
    stem = ntpath.splitext(ntpath.basename(exe))[0].lower()
    if stem in BROWSERS:
        low = (title or "").lower()
        for pattern, cat in TITLE_CATEGORIES:
            if re.search(pattern, low):
                return cat
        return "web"
    return EXE_CATEGORIES.get(stem)


def sample_foreground():
    """Что в фокусе и сколько секунд ты ничего не нажимал. Только Windows; None — нет окна."""
    import ctypes
    from ctypes import wintypes

    import automation

    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    idle = 0.0
    if u.GetLastInputInfo(ctypes.byref(info)):
        idle = ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    return Sample(automation.window_exe(hwnd) or "", automation.window_title(hwnd), idle)


# ---------------- как озвучить ----------------

HINTS = {  # что сказать нейросети, чтобы она придумала свою подколку
    "loss": "он только что проиграл{game}. Постебай его за это.",
    "retry": "он уже несколько раз подряд играет короткие катки в «{t}» — похоже, проигрывает и не сдаётся. Постебай.",
    "quick_quit": "он закрыл игру «{t}» всего через {d} после запуска. Постебай, что сдался.",
    "marathon": "он только что закрыл игру «{t}», просидев в ней {d}. Постебай и намекни, что пора отдохнуть.",
    "procrastinate": "он поработал {d} и сразу ушёл смотреть видео или сидеть в чатах. Постебай за прокрастинацию.",
    "enter_video": "он открыл видео в интернете. Подколи.",
    "enter_social": "он зашёл в чаты и соцсети. Подколи.",
    "long_video": "он уже больше часа подряд смотрит видео. Постебай.",
    "long_social": "он уже долго сидит в соцсетях и чатах. Постебай.",
    "long_work": "он уже два часа подряд сидит за работой. Подколи и намекни на перерыв.",
    "long_office": "он уже два часа подряд сидит в документах и таблицах. Подколи и намекни на перерыв.",
    "long_web": "он уже полтора часа сидит в браузере. Подколи.",
    "long_sit": "он сидит за компьютером три часа без перерыва. Подколи и намекни, что пора встать.",
    "night": "уже глубокая ночь, а он всё за компьютером. Подколи.",
}
STYLE = ("Скажи одну короткую язвительную, но дружескую фразу до пятнадцати слов. Без оскорблений личности и мата, "
         "без эмодзи, не предлагай команд и не говори, что ты что-то сделала на компьютере.")


def compose(situation, detail, quip, line):
    """Текст подколки: придумывает нейросеть (quip — функция «подсказка → фраза» или None),
    а если она недоступна или промахнулась — готовая фраза из persona.LINES."""
    kw = {"t": "", "d": ""}
    kw.update(detail or {})
    text = None
    if quip:
        game = f" в игре «{kw['t']}»" if kw["t"] else ""
        try:
            text = quip(HINTS[situation].format(game=game, **kw) + " " + STYLE)
        except Exception:
            log.exception("Подколка через нейросеть")
    if text:
        return text
    key = "c_" + situation
    if situation == "loss" and kw["t"]:
        key = "c_loss_game"
    return line(key, **kw)


# ---------------- наблюдатель ----------------

class Commentator:
    """Следит за занятиями и решает, когда подколоть. Всё время и случайность подставляются снаружи,
    чтобы логику можно было проверить без Windows.

    emit(ситуация, детали) — из потока наблюдателя; can_speak(strict) — можно ли сейчас говорить
    (strict — ещё и не лезть поверх полноэкранных программ)."""

    def __init__(self, book, emit, can_speak, cfg, sample=sample_foreground, now=time.time,
                 rand=random.random, hour=lambda: time.localtime().tm_hour):
        self.book, self.emit, self.can_speak, self.cfg = book, emit, can_speak, cfg
        self.sample, self.now, self.rand, self.hour = sample, now, rand, hour
        self.cat = None            # чем занят сейчас
        self.since = 0.0           # с какого момента
        self.pending, self.pending_n = None, 0
        self.active_since = None   # с какого момента сидит без перерыва
        self.fired = set()         # какие «долго …» уже сказаны в этом занятии
        self.work_ended = None     # (когда, сколько минут) закончилась работа
        self.last_comment = -1e9
        self.cool = {}
        self.started = {}          # игра → когда запущена
        self.last_end = {}         # игра → (когда закрыта, сколько минут)

    # --- настройки ---
    def enabled(self):
        return bool(self.cfg.get("commentary", True))

    def gap(self):
        try:
            return max(1.0, float(self.cfg.get("commentary_minutes", 10))) * 60
        except (TypeError, ValueError):
            return 600.0

    # --- решение «говорить или нет» ---
    def _gate(self, situation, detail=None, strict=True, global_gap=True):
        if not self.enabled():
            return None
        now = self.now()
        if now - self.cool.get(situation, -1e9) < COOLDOWN.get(situation, DEFAULT_COOLDOWN):
            return None
        if global_gap and now - self.last_comment < self.gap():
            return None
        if not self.can_speak(strict):
            return None
        self.cool[situation] = self.last_comment = now
        return situation, detail or {}

    def _fire(self, situation, detail=None):
        res = self._gate(situation, detail)
        if res:
            self.emit(*res)
        return res

    # --- игры ---
    def on_game_event(self, kind, key, minutes):
        """Событие трекера игр → (ситуация, детали) или None. Говорит вызывающий: игра уже закрыта,
        поверх неё Капри не появится."""
        if not self.enabled():
            return None
        g = self.book.get(key)
        if not g:
            return None
        now = self.now()
        if kind == "start":
            self.started[key] = now
            return None
        if kind != "end":
            return None
        start = self.started.pop(key, now - minutes * 60)
        prev = self.last_end.get(key)
        self.last_end[key] = (now, minutes)
        detail = {"t": g["name"], "d": games.fmt_minutes(minutes)}
        if minutes >= MARATHON_MINUTES:
            situation = "marathon"
        elif prev and start - prev[0] < RETRY_WINDOW and prev[1] < RETRY_MAX_MINUTES and minutes < RETRY_MAX_MINUTES:
            situation = "retry"
        elif minutes < QUICK_QUIT_MINUTES and g["sessions"] > 1 and g["minutes"] > 60:
            situation = "quick_quit"  # не первая встреча с игрой: новичок может просто заглянуть
        else:
            return None
        return self._gate(situation, detail, strict=False, global_gap=False)

    def overheard(self):
        """Услышала «слили» без имени (react_to_voice): подкалываем, только если идёт игра или она закрылась недавно."""
        if not self.enabled():
            return None
        key = self.book.current()
        now = self.now()
        recent = key in self.last_end and now - self.last_end[key][0] < 300
        if not (self.book.active or recent) or key is None:
            return None
        g = self.book.get(key)
        return self._gate("loss", {"t": g["name"] if g else ""}, strict=False, global_gap=False)

    # --- обычные занятия ---
    def step(self):
        if not self.enabled():
            return
        s = self.sample()
        if s is None:
            return
        now = self.now()
        if s.idle > IDLE_SECONDS:  # отошёл — серия прервана, говорить некому
            self.cat, self.pending, self.active_since = None, None, None
            self.fired.clear()
            return
        if self.active_since is None:
            self.active_since = now
        cat = classify(s.exe, s.title, self.book.path_index())
        if cat != self.cat:
            if cat == self.pending:
                self.pending_n += 1
            else:
                self.pending, self.pending_n = cat, 1
            if self.pending_n >= SWITCH_CONFIRM:
                self._switch(cat, now)
        else:
            self.pending, self.pending_n = None, 0
        if self.cat and self.cat != "game":
            self._check_long(now)
            self._check_night()

    def _switch(self, new, now):
        old = self.cat
        if old in ("work", "office") and now - self.since >= WORK_STREAK_MIN * 60:
            self.work_ended = (now, (now - self.since) / 60)
        self.cat, self.since = new, now
        self.pending, self.pending_n = None, 0
        self.fired.discard("long")
        if new not in ("video", "social"):
            return
        if self.work_ended and now - self.work_ended[0] <= PROCRASTINATE_WINDOW:
            minutes = self.work_ended[1]
            self.work_ended = None
            self._fire("procrastinate", {"d": games.fmt_minutes(minutes)})
        elif self.rand() < ENTER_CHANCE[new]:
            self._fire("enter_" + new)

    def _check_long(self, now):
        minutes = (now - self.since) / 60
        limit = LONG_MINUTES.get(self.cat)
        if limit and minutes >= limit and "long" not in self.fired:
            self.fired.add("long")
            self._fire("long_" + self.cat)
        sat = (now - self.active_since) / 60 if self.active_since else 0
        if sat >= LONG_SIT_MINUTES and "sit" not in self.fired:
            self.fired.add("sit")
            self._fire("long_sit")

    def _check_night(self):
        if 1 <= self.hour() < 5:
            self._fire("night")

    # --- поток ---
    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="commentary").start()

    def _loop(self):
        while True:
            time.sleep(POLL_SECONDS)
            try:
                self.step()
            except Exception:
                log.exception("Комментарии: ошибка наблюдателя")
