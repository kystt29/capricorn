"""Реакции по экрану (экспериментально, по умолчанию выключено): Капри замечает надписи «YOU DIED», «Победа»,
«Game over» в игре и реагирует — стёбом или похвалой.

Как это работает: пока запущена игра из games.py, раз в несколько секунд делается снимок экрана, на нём
распознаётся текст встроенным распознавателем Windows (Windows.Media.Ocr, как «Копировать текст из картинки»), и в нём
ищутся ключевые слова победы и поражения. Всё локально:
- снимок не сохраняется на диск и никуда не отправляется, после распознавания его нет;
- распознанный текст тоже не сохраняется и не попадает в журнал — в журнале только факт «увидела победу/поражение»;
- без запущенной игры экран не снимается вообще.

Нужны библиотеки из requirements.txt (winrt-Windows.Media.Ocr и др.; setup.bat поставит их) и установленный в Windows
язык распознавания (Параметры → Время и язык → Язык). Если чего-то не хватает, Капри один раз пишет об этом в журнал
и перестаёт пробовать. Включить: "screen_reactions": true в config.json или галочка в меню трея.

Надписи на экране бывают в меню, подсказках и обучении («Победа — это…»), поэтому ложные срабатывания возможны; чаще
всего ошибка — одна лишняя реплика, а повтор не чаще раза в минуту."""
import logging
import re
import threading
import time

log = logging.getLogger("capri")

POLL_SECONDS = 6
REPEAT_SECONDS = 60       # одну и ту же надпись не чаще раза в минуту
MAX_WIDTH = 1280          # снимок уменьшаем: надписи в играх крупные, а распознавание быстрее

LOSS_PATTERNS = [
    r"you died", r"you are dead", r"you have died", r"\bwasted\b", r"game over", r"\bdefeat\b", r"you lose",
    r"mission failed", r"\bbusted\b", r"you were killed", r"killed by", r"eliminated", r"respawn in",
    r"ты умер", r"вы умерли", r"вы погибли", r"ты погиб", r"поражение", r"вы проиграли", r"ты проиграл",
    r"миссия провалена", r"задание провалено", r"вас убили", r"возрождение через", r"игра окончена",
]
WIN_PATTERNS = [
    r"\bvictory\b", r"you win", r"you won", r"level complete", r"mission complete", r"mission accomplished",
    r"boss defeated", r"enemy felled", r"achievement unlocked", r"congratulations", r"stage clear",
    r"победа", r"вы победили", r"ты победил", r"уровень пройден", r"миссия выполнена", r"задание выполнено",
    r"поздравляем", r"достижение получено", r"босс повержен",
]
_LOSS = re.compile("|".join(LOSS_PATTERNS))
_WIN = re.compile("|".join(WIN_PATTERNS))


def classify_screen_text(text):
    """Какие исходы видны в распознанном тексте: подмножество {"win", "loss"}."""
    t = re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е"))
    found = set()
    if _LOSS.search(t):
        found.add("loss")
    if _WIN.search(t):
        found.add("win")
    return found


def grab_screen():
    """Снимок основного экрана (уменьшенный). Только Windows."""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)))
    return img


class OcrUnavailable(Exception):
    pass


_engines = []


def _make_engines():
    """Распознаватели: по языкам профиля Windows и отдельно английский (в играх надписи чаще английские)."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError as e:
        raise OcrUnavailable("нет библиотек winrt для распознавания текста: запусти setup.bat") from e
    engines = []
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine:
        engines.append(engine)
    try:
        from winrt.windows.globalization import Language
        english = OcrEngine.try_create_from_language(Language("en-US"))
        if english and all(english.recognizer_language.language_tag != e.recognizer_language.language_tag
                           for e in engines):
            engines.append(english)
    except Exception:
        pass
    if not engines:
        raise OcrUnavailable("в Windows не установлен язык распознавания текста")
    return engines


def ocr_text(img):
    """Текст на картинке (нижний регистр) через Windows.Media.Ocr."""
    import asyncio

    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    if not _engines:
        _engines.extend(_make_engines())
    rgba = img.convert("RGBA")
    writer = DataWriter()
    writer.write_bytes(bytearray(rgba.tobytes("raw", "BGRA")))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, rgba.width, rgba.height, BitmapAlphaMode.PREMULTIPLIED)

    async def run():
        parts = []
        for engine in _engines:
            parts.append((await engine.recognize_async(bitmap)).text)
        return " ".join(parts)
    return asyncio.run(run()).lower()


class ScreenWatcher:
    """Пока идёт игра, ищет на экране надписи победы и поражения. on_outcome(kind) — «win» или «loss», в потоке наблюдателя."""

    def __init__(self, book, on_outcome, cfg, grab=grab_screen, ocr=ocr_text, now=time.time):
        self.book, self.on_outcome, self.cfg = book, on_outcome, cfg
        self.grab, self.ocr, self.now = grab, ocr, now
        self.seen = set()      # что было видно в прошлый раз: реагируем на появление надписи, а не на её висение
        self.last = {}         # kind → когда реагировали
        self.broken = False

    def enabled(self):
        return bool(self.cfg.get("screen_reactions", False)) and not self.broken

    def step(self):
        if not self.enabled():
            return
        if not self.book.active:    # игры нет — экран не снимаем
            self.seen = set()
            return
        try:
            kinds = classify_screen_text(self.ocr(self.grab()))
        except OcrUnavailable as e:
            log.warning("Реакции по экрану отключены: %s", e)
            self.broken = True
            return
        except Exception:
            log.exception("Реакции по экрану: ошибка распознавания")
            self.broken = True      # не мучаем систему повторными ошибками
            return
        now = self.now()
        for kind in sorted(kinds - self.seen):
            if now - self.last.get(kind, -1e9) >= REPEAT_SECONDS:
                self.last[kind] = now
                log.info("Экран: увидела %s", "победу" if kind == "win" else "поражение")  # только факт, не текст
                self.on_outcome(kind)
        self.seen = kinds

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="screen-watcher").start()

    def _loop(self):
        while True:
            time.sleep(POLL_SECONDS)
            try:
                self.step()
            except Exception:
                log.exception("Реакции по экрану")
