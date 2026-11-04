"""Капри комментирует, что ты делаешь: стебёт, подбадривает, радуется и учится на твоей реакции.

Что она видит и откуда:
- какая программа сейчас в фокусе (раз в 15 секунд) и как давно ты ничего не нажимал;
- запуски и закрытия игр (их ведёт games.py);
- твои слова: «я слил», «меня убили», «ура, прошёл», «получилось» — по имени и, если включено "react_to_voice",
  и без имени, пока идёт игра;
- по желанию ("screen_reactions": true) — надписи на экране в игре: «YOU DIED», «Победа», «Game over» (screen.py).

Победа или поражение в самой игре ей напрямую не видны, поэтому исход — это то, что ты сказал, надпись на экране
и косвенные признаки: каты подряд по несколько минут, быстрый выход, многочасовой марафон, серии побед и поражений.

Тон: на поражение чаще стёб, на серию поражений — подбадривание, на победу — похвала (иногда с подколом).
Что тебе заходит, она запоминает по твоей реакции (learning.py) и подстраивает частоту и тон.

Приватность: заголовок окна используется только на месте, он нигде не сохраняется и не пишется в журнал. Название
видео (только видео, и не с «чувствительных» страниц: банки, почта, пароли, инкогнито) получает лишь локальная
нейросеть, чтобы отреагировать по делу: "commentary_titles": false отключает и это. Подколки придумывает только
локальная нейросеть, режим Claude для этого не используется. Выключить всё: "commentary": false, галочка в трее или
«Капри, не комментируй»."""
import logging
import ntpath
import random
import re
import threading
import time
from collections import namedtuple

import freestyle
import games
import persona

log = logging.getLogger("capri")

POLL_SECONDS = 15
IDLE_SECONDS = 180            # дольше без нажатий — считаем, что ты отошёл
SWITCH_CONFIRM = 2            # столько проверок подряд должно показать новое занятие, чтобы это не было миганием окон
WORK_STREAK_MIN = 10          # сколько минут работы нужно, чтобы уход в видео или чаты считался прокрастинацией
PROCRASTINATE_WINDOW = 120    # и за сколько секунд после работы он должен случиться
DEFAULT_COOLDOWN = 45 * 60
COOLDOWN = {                  # как редко повторяется один и тот же вид реплики (секунды)
    "loss": 120, "win": 120, "streak_loss": 900, "streak_win": 900, "retry": 600, "quick_quit": 900,
    "marathon": 3600, "procrastinate": 1800, "night": 3 * 3600, "long_sit": 2 * 3600, "video_next": 900,
    "idle": 1800, "back": 1800, "wallpaper": 600, "free": 60, "screen": 0,
}
LONG_MINUTES = {"video": 60, "social": 45, "work": 120, "office": 120, "web": 90}
LONG_SIT_MINUTES = 180
ENTER_CHANCE = {"video": 0.6, "social": 0.4, "music": 0.25, "work": 0.2, "office": 0.2, "create": 0.3}
VIDEO_NEXT_CHANCE = 0.5
MARATHON_MINUTES = 180
RETRY_WINDOW = 15 * 60        # новая сессия той же игры не позже чем через столько после прошлой…
RETRY_MAX_MINUTES = 25        # …и обе короче стольки минут — похоже на серию проигранных катов
QUICK_QUIT_MINUTES = 6
STREAK_WINDOW = 20 * 60       # столько подряд побед или поражений за это время — серия
STREAK_N = 3
IDLE_COMMENT_SECONDS = 300   # безделие: столько секунд без нажатий (но не больше IDLE_GONE_SECONDS — тогда тебя нет)
IDLE_GONE_SECONDS = 1200
BACK_IDLE_SECONDS = 1800     # вернулся после такого простоя — поздороваться
FREE_MIN_MINUTES, FREE_MAX_MINUTES = 4, 9   # свободный режим: случайная реплика раз в столько минут
WALLPAPER_SHARE = 0.2        # доля случайных реплик свободного режима, когда она смотрит на обои
WALL_CHECK_SECONDS = 60
ANYTIME_GAP_FACTOR = 0.4     # свободный режим: лимит между репликами во столько раз короче

Sample = namedtuple("Sample", "exe title idle")

# Тон по умолчанию и из чего выбираем (с базовыми шансами). Дальше шансы сдвигает learning.py по твоей реакции.
DEFAULT_MODE = {"loss": "roast", "win": "cheer"}
MODES = {"loss": (("roast", "cheer"), (0.7, 0.3)), "win": (("cheer", "roast"), (0.6, 0.4))}


# ---------------- фразы ----------------
# Фразы проходят через norm() из actions.py: нижний регистр, «ё» → «е», без знаков препинания.

_LOSE = (r"(проиграл\w*|слил\w*|сдох\w*|умер\w*|вайпнул\w*|зафейлил\w*|продул\w*|слетел\w*|"
         r"не прошел|не прошла|не смог\w* пройти|не справил\w*)")
LOSS_RE = (r"^(ну |блин |да |все |всё |ой |ай )*("
           rf"((я|мы) (опять |снова |только что |уже |все |всё )*{_LOSE})|"
           r"((меня|нас) (опять |снова |только что )*(убили|убил|слили|разнесли|вынесли|уничтожили))|"
           r"((опять|снова) (проиграл\w*|слил\w*|сдох\w*|умер\w*|вайп\w*))|"
           r"(проигрыш|вайп|поражение)"
           r")( |$)")

_WIN = r"(выиграл\w*|победил\w*|прошел|прошла|прошли|выбил\w*|поймал\w*|справил\w*|затащил\w*|залутал\w*)"
WIN_RE = (r"^(ну |блин |да |ура |наконец |наконец-то |все |всё |ой )*("
          rf"((я|мы) (опять |снова |только что |наконец-то |наконец |все-таки |таки |уже )*{_WIN})|"
          r"((у меня|у нас) (наконец-то |наконец |все |всё )?(получилось|вышло))|"
          r"(получилось|победа|выиграли|затащили|затащил)|"
          r"((мне|нам) (наконец-то |наконец )?(повезло|выпал\w*))"
          r")( |$)")

# То, что можно услышать без имени, пока идёт игра: строже, чтобы не реагировать на любой разговор.
OVERHEARD_LOSS = re.compile(r"\b(слил\w*|проиграл\w*|сдох\w*|убили|вайп\w*|вынесли|разнесли|опять умер|снова умер)\b")
OVERHEARD_WIN = re.compile(r"\b(выиграл\w*|победил\w*|получилось|затащил\w*|победа|ура|повезло|выбил\w*)\b")
LAUGH_RE = re.compile(r"\b(ха ?ха\w*|ахах\w*|хаха\w*|ржу)\b")

_OFF = (r"\bне (комментируй|коментируй|стебай|подкалывай)|"
        r"(перестань|хватит|прекрати)( меня)? (комментировать|коментировать|стебать|подкалывать)|"
        r"(выключи|отключи) (комментарии|подколки|стеб)|без (комментариев|подколок)")
_ON = (r"^((начни|давай|можешь|продолжай|включи) )?(меня )?(комментируй|коментируй|стебай|подкалывай|"
       r"комментировать|стебать|подкалывать)( меня| мои действия| все| всё)?$|"
       r"включи (комментарии|подколки|стеб)")
TOGGLE_RE = f"{_OFF}|{_ON}"


def toggle_wants_off(c):
    return bool(re.search(_OFF, c))


def _plain(text):
    return (text or "").lower().replace("ё", "е")


def heard_loss(text):
    """Похоже ли услышанное без имени на «слили / проиграл» (для react_to_voice)."""
    return bool(OVERHEARD_LOSS.search(_plain(text)))


def heard_outcome(text):
    """«loss» / «win» / None: что за исход услышали без имени."""
    t = _plain(text)
    if OVERHEARD_LOSS.search(t):
        return "loss"
    if OVERHEARD_WIN.search(t):
        return "win"
    return None


def heard_laugh(text):
    return bool(LAUGH_RE.search(_plain(text)))


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
    "photoshop": "create", "blender": "create", "premiere pro": "create", "afterfx": "create",
    "illustrator": "create", "figma": "create", "davinciresolve": "create", "krita": "create", "obs64": "create",
}
# Что значит страница в браузере — по заголовку окна. Заголовок дальше не идёт и нигде не хранится.
TITLE_CATEGORIES = [
    (r"youtube|twitch|netflix|кинопоиск|rutube|vk видео|vk video|okko|wink|start\.ru", "video"),
    (r"вконтакте|vk\.com|telegram|whatsapp|instagram|tiktok|reddit|twitter|facebook|одноклассники", "social"),
    (r"github|stack overflow|stackoverflow|документаци|localhost|leetcode|jupyter|codeforces|docs\.", "work"),
]
# Страницы, о которых Капри не должна говорить, даже если это видео-сайт или вкладка рядом: банки, почта, пароли…
SENSITIVE_TITLE = re.compile(
    r"парол|password|\bбанк|\bbank|сбер|тинькофф|tinkoff|\bвход\b|\blog ?in\b|\bsign ?in\b|авторизац|private|"
    r"инкогнито|incognito|inprivate|оплат|payment|checkout|\bкарт[аыуе]\b|\bcard\b|паспорт|госуслуг|"
    r"\bпочт(а|ы|у|е|ой)\b|mail|inbox|\bписьм|кошел|wallet|2fa|код подтвержд|verification", re.IGNORECASE)
TITLE_NOISE = re.compile(
    r"\s*[-–—|]\s*(youtube|ютуб|twitch|rutube|vk видео|vk video|яндекс\.?\s*браузер|google chrome|chrome|"
    r"microsoft\s*edge|mozilla firefox|firefox|opera|brave|vivaldi|netflix|кинопоиск).*$", re.IGNORECASE)


def clean_title(title):
    """Название видео для локальной нейросети или None, если страницу лучше не обсуждать. Нигде не сохраняется."""
    t = (title or "").replace("‎", "").strip()
    t = re.sub(r"^\(\d+\)\s*", "", t)          # «(3) …» — счётчик уведомлений в начале заголовка
    t = TITLE_NOISE.sub("", t).strip()
    if len(t) < 3 or SENSITIVE_TITLE.search(title or ""):
        return None
    return t[:80]


def classify(exe, title, game_paths=()):
    """«game» / «video» / «social» / «work» / «office» / «create» / «web» / «music» или None (не комментируем)."""
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

HINTS = {  # что сказать нейросети, чтобы она придумала свою фразу (ключ: ситуация или ситуация_тон)
    "loss": "он только что проиграл{game}. Постебай его за это.",
    "loss_cheer": "он только что проиграл{game}. Подбодри его: скажи, что бывает, и подтолкни попробовать ещё.",
    "win": "у него только что получилось, он выиграл{game}. Похвали его от души.",
    "win_roast": "у него только что получилось, он выиграл{game}. Похвали с ехидцей: намекни, что ему просто повезло.",
    "streak_loss": "он проигрывает несколько раз подряд{game}. Подбодри его и предложи собраться или передохнуть.",
    "streak_win": "у него серия побед подряд{game}. Похвали и подначь, что дальше будет сложнее.",
    "retry": "он уже несколько раз подряд играет короткие катки в «{t}» — похоже, проигрывает и не сдаётся. Постебай.",
    "quick_quit": "он закрыл игру «{t}» всего через {d} после запуска. Постебай, что сдался.",
    "marathon": "он только что закрыл игру «{t}», просидев в ней {d}. Постебай и намекни, что пора отдохнуть.",
    "procrastinate": "он поработал {d} и сразу ушёл смотреть видео или сидеть в чатах. Постебай за прокрастинацию.",
    "enter_video": "он открыл видео в интернете. Подколи.",
    "enter_social": "он зашёл в чаты и соцсети. Подколи.",
    "enter_music": "он включил музыку. Подколи его вкус или похвали, как получится.",
    "enter_work": "он сел работать. Подбодри его с ехидцей.",
    "enter_office": "он открыл документы или таблицы. Посочувствуй и подбодри с иронией.",
    "enter_create": "он занялся творчеством: рисует, монтирует или делает 3D. Подбодри его.",
    "video_next": "он переключился на другое видео. Подколи.",
    "long_video": "он уже больше часа подряд смотрит видео. Постебай.",
    "long_social": "он уже долго сидит в соцсетях и чатах. Постебай.",
    "long_work": "он уже два часа подряд сидит за работой. Подколи и намекни на перерыв.",
    "long_office": "он уже два часа подряд сидит в документах и таблицах. Подколи и намекни на перерыв.",
    "long_web": "он уже полтора часа сидит в браузере. Подколи.",
    "long_sit": "он сидит за компьютером три часа без перерыва. Подколи и намекни, что пора встать.",
    "night": "уже глубокая ночь, а он всё за компьютером. Подколи.",
    "idle": "он уже несколько минут сидит и ничего не делает. Подколи за безделие.",
    "back": "он вернулся к компьютеру после долгого отсутствия. Подколи.",
    "wallpaper": "у него на рабочем столе обои: {what}. Прокомментируй обои.",
    "screen": "у него сейчас на экране: {what}. Прокомментируй, что видишь.",
    "free": "он сейчас {act}. Скажи по этому поводу что угодно: подколи, удиви, подбодри.",
}
TITLE_HINTS = {  # то же, если известно название видео (его видит только локальная нейросеть)
    "enter_video": "он включил видео «{title}». Отреагируй на название: подколи или покажи интерес.",
    "video_next": "он переключился на другое видео — «{title}». Отреагируй на название.",
}
STYLE = ("Скажи одну короткую фразу до пятнадцати слов, живо и по-дружески, с лёгким стёбом там, где он уместен. "
         "Без оскорблений личности и мата, без эмодзи, не предлагай команд и не говори, что ты что-то сделала "
         "на компьютере.")
STYLE_RUDE = ("Скажи одну короткую фразу до пятнадцати слов, живо, как друг, который стебёт по-доброму. Можно и нужно "
              "материться: используй русский мат уместно и в тему. Ругай ситуацию и поступки, а не саму личность; не трогай "
              "национальность, внешность, пол, здоровье и семью. Без эмодзи, не предлагай команд и не говори, что ты "
              "что-то сделала на компьютере.")


def _key(situation, mode):
    """Ключ подсказки и фразы: тон по умолчанию — просто ситуация, другой тон — «ситуация_тон»."""
    if not mode or mode == DEFAULT_MODE.get(situation):
        return situation
    return f"{situation}_{mode}"


def compose(situation, detail, quip, line, learn=None, rude=False):
    """Текст реплики: придумывает нейросеть (quip — функция «подсказка → фраза» или None), а если она недоступна
    или промахнулась — готовая фраза из persona.LINES. learn добавляет к подсказке примеры того, что тебе нравилось,
    rude — с матом (подсказка и запасные фразы «r_…»)."""
    kw = {"t": "", "d": "", "what": "", "act": ""}
    kw.update({k: v for k, v in (detail or {}).items() if k not in ("mode", "title")})
    mode = (detail or {}).get("mode")
    title = (detail or {}).get("title")
    key = _key(situation, mode)
    text = None
    if quip:
        game = f" в игре «{kw['t']}»" if kw["t"] else ""
        if title and situation in TITLE_HINTS:
            hint = TITLE_HINTS[situation].format(title=title)
        else:
            hint = (HINTS.get(key) or HINTS[situation]).format(game=game, **kw)
        hint += " " + (STYLE_RUDE if rude else STYLE)
        if learn:
            liked, disliked = learn.examples()
            if liked:
                hint += (" Ему нравились такие реплики (говори в таком духе, но не повторяй дословно): "
                         + "; ".join(f"«{x}»" for x in liked) + ".")
            if disliked:
                hint += " Такое ему не понравилось, так не говори: " + "; ".join(f"«{x}»" for x in disliked) + "."
        try:
            text = quip(hint)
        except Exception:
            log.exception("Подколка через нейросеть")
    if text:
        return text
    normal = ["c_" + key, "c_" + situation]
    if situation == "loss" and kw["t"] and normal[0] == "c_loss":
        normal.insert(0, "c_loss_game")
    wanted = []
    if rude:
        if situation == "loss" and kw["t"] and key == "loss":
            wanted.append("r_loss_game")
        wanted += ["r_" + key, "r_" + situation]
    for k in wanted + normal:
        if k in persona.LINES:
            return line(k, **kw)
    return line("c_free", **kw)


# ---------------- наблюдатель ----------------

class Commentator:
    """Следит за занятиями и исходами и решает, когда что сказать. Время и случайность подставляются снаружи, чтобы
    логику можно было проверить без Windows.

    emit(ситуация, детали) — из потока наблюдателя; can_speak(strict) — можно ли сейчас говорить
    (strict — ещё и не лезть поверх полноэкранных программ); learn — learning.Learning (необязательно)."""

    def __init__(self, book, emit, can_speak, cfg, sample=sample_foreground, now=time.time,
                 rand=random.random, hour=lambda: time.localtime().tm_hour, learn=None, looker=None,
                 wall=freestyle.wallpaper_signature, spawn=None):
        self.book, self.emit, self.can_speak, self.cfg, self.learn = book, emit, can_speak, cfg, learn
        self.sample, self.now, self.rand, self.hour = sample, now, rand, hour
        self.looker = looker       # looker("wallpaper" | "screen") → описание картинки или None (модель с «глазами»)
        self.wall = wall           # отпечаток текущих обоев — чтобы заметить смену
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="look").start())
        self.idle_cat, self.idle_fired, self.idle_peak = None, False, 0.0
        self.next_free = None      # когда следующая случайная реплика свободного режима
        self.wall_sig, self.wall_checked = None, -1e9
        self.cat = None            # чем занят сейчас
        self.since = 0.0           # с какого момента
        self.pending, self.pending_n = None, 0
        self.active_since = None   # с какого момента сидит без перерыва
        self.fired = set()         # какие «долго …» уже сказаны в этом занятии
        self.work_ended = None     # (когда, сколько минут) закончилась работа
        self.vtitle, self.vpending, self.vpending_n = None, None, 0   # какое видео смотрит (только в памяти)
        self.last_comment = -1e9
        self.cool = {}
        self.started = {}          # игра → когда запущена
        self.last_end = {}         # игра → (когда закрыта, сколько минут)
        self.outcomes = []         # (когда, «win»/«loss») — для серий

    # --- настройки и обучение ---
    def enabled(self):
        return bool(self.cfg.get("commentary", True))

    def titles_on(self):
        return bool(self.cfg.get("commentary_titles", True))

    def anytime(self):
        """Свободный режим: реплики чаще, без оглядки на полноэкранные программы, плюс случайные комментарии."""
        return bool(self.cfg.get("commentary_anytime", False))

    def rude(self):
        return bool(self.cfg.get("profanity", False))

    def gap(self):
        try:
            minutes = max(1.0, float(self.cfg.get("commentary_minutes", 6)))
        except (TypeError, ValueError):
            minutes = 6.0
        return minutes * 60 * (self.learn.tempo if self.learn else 1.0) * (ANYTIME_GAP_FACTOR if self.anytime() else 1.0)

    def _w(self, situation):
        return self.learn.weight(situation) if self.learn else 1.0

    def render(self, situation, detail, quip, line):
        """Готовый текст реплики; заодно запоминает, что и каким тоном сказано, чтобы понять, как ты на это ответишь."""
        text = compose(situation, detail, quip, line, self.learn, rude=self.rude())
        if self.learn:
            self.learn.record_comment(situation, (detail or {}).get("mode"), text)
        return text

    # --- решение «говорить или нет» ---
    def _gate(self, situation, detail=None, strict=True, global_gap=True, force=False):
        if not self.enabled():
            return None
        now = self.now()
        if not force:
            if now - self.cool.get(situation, -1e9) < COOLDOWN.get(situation, DEFAULT_COOLDOWN):
                return None
            if global_gap and now - self.last_comment < self.gap():
                return None
            if not self.can_speak(strict and not self.anytime()):
                return None
        self.cool[situation] = self.last_comment = now
        return situation, detail or {}

    def _fire(self, situation, detail=None):
        res = self._gate(situation, detail)
        if res:
            self.emit(*res)
        return res

    # --- исходы: победа и поражение ---
    def outcome(self, kind, detail=None, explicit=False):
        """kind — «win» или «loss» (слова, надпись на экране, фраза без имени). Возвращает (ситуация, детали) или None.
        Серия из трёх одинаковых исходов подряд даёт подбадривание (или похвалу и подначку); explicit — ты сам
        сказал Капри, тогда она отвечает всегда."""
        if not self.enabled() or kind not in ("win", "loss"):
            return None
        now = self.now()
        self.outcomes = [(t, k) for t, k in self.outcomes if now - t < STREAK_WINDOW] + [(now, kind)]
        tail = [k for _, k in self.outcomes[-STREAK_N:]]
        candidates = [kind]
        if len(tail) == STREAK_N and set(tail) == {kind}:
            candidates.insert(0, "streak_" + kind)
        for situation in candidates:
            info = dict(detail or {})
            if situation in MODES:
                modes, base = MODES[situation]
                info["mode"] = self.learn.pick_mode(modes, base) if self.learn else modes[0]
            res = self._gate(situation, info, strict=False, global_gap=False, force=explicit)
            if res:
                return res
        return None

    def overheard(self, kind="loss"):
        """Услышала «слили» или «ура» без имени (react_to_voice): реагируем, только если идёт игра или она закрылась
        недавно."""
        if not self.enabled():
            return None
        key = self.book.current()
        now = self.now()
        recent = key in self.last_end and now - self.last_end[key][0] < 300
        if not (self.book.active or recent) or key is None:
            return None
        g = self.book.get(key)
        return self.outcome(kind, {"t": g["name"] if g else ""})

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

    # --- обычные занятия ---
    def step(self):
        if not self.enabled():
            return
        s = self.sample()
        if s is None:
            return
        now = self.now()
        if s.idle < 60:
            if self.idle_peak >= BACK_IDLE_SECONDS:
                self._fire("back")
            self.idle_peak, self.idle_fired, self.idle_cat = 0.0, False, None
        else:
            self.idle_peak = max(self.idle_peak, s.idle)
        if s.idle > IDLE_SECONDS:  # отошёл — серия прервана
            if self.cat:
                self.idle_cat = self.cat
            # безделие: сидит и ничего не жмёт, а не смотрит видео, играет или слушает музыку (там тоже не жмут)
            if (IDLE_COMMENT_SECONDS <= s.idle < IDLE_GONE_SECONDS and not self.idle_fired
                    and self.idle_cat not in ("video", "game", "music")):
                self.idle_fired = True
                self._fire("idle")
            self.cat, self.pending, self.active_since = None, None, None
            self.vtitle = None
            self.fired.clear()
            return
        if self.active_since is None:
            self.active_since = now
        cat = classify(s.exe, s.title, self.book.path_index())
        if self.learn and cat:
            self.learn.note_activity(cat, self.hour(), POLL_SECONDS / 60)
        if cat != self.cat:
            if cat == self.pending:
                self.pending_n += 1
            else:
                self.pending, self.pending_n = cat, 1
            if self.pending_n >= SWITCH_CONFIRM:
                self._switch(cat, now, s.title)
        else:
            self.pending, self.pending_n = None, 0
        if self.cat == "video":
            self._watch_video(s.title)
        if self.cat and self.cat != "game":
            self._check_long(now)
            self._check_night()
        self._check_wallpaper(now)
        if self.anytime():
            self._check_free(now)

    def _switch(self, new, now, title=""):
        old = self.cat
        if old in ("work", "office") and now - self.since >= WORK_STREAK_MIN * 60:
            self.work_ended = (now, (now - self.since) / 60)
        self.cat, self.since = new, now
        self.pending, self.pending_n = None, 0
        self.fired.discard("long")
        self.vtitle, self.vpending, self.vpending_n = (clean_title(title) if new == "video" else None), None, 0
        if new in ("video", "social") and self.work_ended and now - self.work_ended[0] <= PROCRASTINATE_WINDOW:
            minutes = self.work_ended[1]
            self.work_ended = None
            self._fire("procrastinate", {"d": games.fmt_minutes(minutes)})
            return
        chance = ENTER_CHANCE.get(new)
        if chance and self.rand() < chance * self._w("enter_" + new):
            detail = {}
            if new == "video" and self.titles_on() and self.vtitle:
                detail["title"] = self.vtitle
            self._fire("enter_" + new, detail)

    def _watch_video(self, title):
        """Следующее видео в той же вкладке: комментируем, но не на каждое."""
        if not self.titles_on():
            return
        t = clean_title(title)
        if not t:
            return
        if not self.vtitle:
            self.vtitle = t
        elif t != self.vtitle:
            if t == self.vpending:
                self.vpending_n += 1
            else:
                self.vpending, self.vpending_n = t, 1
            if self.vpending_n >= SWITCH_CONFIRM:
                self.vtitle, self.vpending, self.vpending_n = t, None, 0
                if self.rand() < VIDEO_NEXT_CHANCE * self._w("video_next"):
                    self._fire("video_next", {"title": t})
        else:
            self.vpending, self.vpending_n = None, 0

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

    # --- свободный режим, обои ---
    def _free_delay(self):
        minutes = FREE_MIN_MINUTES + (FREE_MAX_MINUTES - FREE_MIN_MINUTES) * self.rand()
        return minutes * 60 * (self.learn.tempo if self.learn else 1.0)

    def _check_free(self, now):
        """Свободный режим: раз в несколько минут сказать что угодно о том, чем ты занят, или об обоях."""
        if self.next_free is None:
            self.next_free = now + self._free_delay()
            return
        if now < self.next_free:
            return
        self.next_free = now + self._free_delay()
        if self.looker and self.cfg.get("commentary_wallpaper") and self.rand() < WALLPAPER_SHARE:
            self._look_async("wallpaper", "wallpaper", {}, fallback=False)
            return
        running = self.book.running()
        act = freestyle.activity_text(self.cat, self.vtitle if self.cat == "video" else None,
                                      running[0] if running else None)
        self._fire("free", {"act": act})

    def _check_wallpaper(self, now):
        """Обои сменились — отреагировать (если включено "commentary_wallpaper")."""
        if not self.cfg.get("commentary_wallpaper") or now - self.wall_checked < WALL_CHECK_SECONDS:
            return
        self.wall_checked = now
        sig = self.wall()
        if sig and self.wall_sig and sig != self.wall_sig:
            self._look_async("wallpaper", "wallpaper", {}, fallback=True)
        if sig:
            self.wall_sig = sig

    def _look_async(self, situation, kind, detail, fallback):
        """Описание картинки может занять секунды (модель с «глазами»), поэтому ждём его в отдельном потоке.
        fallback — сказать что-то и без описания (например, «сменил обои»)."""
        def job():
            desc = None
            try:
                desc = self.looker(kind) if self.looker else None
            except Exception:
                log.exception("Комментарии: не удалось посмотреть (%s)", kind)
            if desc:
                self._fire(situation, {**detail, "what": desc})
            elif fallback:
                self._fire(situation, detail)
        self.spawn(job)

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
