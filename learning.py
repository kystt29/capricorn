"""Как Капри учится на тебе: что заходит, что нет, и когда ты обычно чем занят.

Это «саморазвитие» без переписывания собственного кода (так программа остаётся целой и предсказуемой). Учится она на:
- твоей реакции на её реплику. Сказал «ха-ха», «в точку» — запомнила, что такое заходит; «не смешно», «перебор» — что нет.
  От этого зависят: как часто она подкалывает именно в таких ситуациях, стёб это будет или подбадривание, и общий темп
  (чаще или реже она вообще лезет);
- удачных и неудачных фразах: пару последних она показывает локальной нейросети как примеры («в таком духе» и
  «так не надо»), поэтому подколки со временем становятся похожи на те, что тебе нравятся;
- привычках: по каким частям суток ты чем занят (игры, видео, работа…), чтобы замечать «ты сегодня необычно рано в игре».

Хранится в learning.json рядом с программой: счётчики, несколько её собственных реплик и минуты по частям суток.
Названия окон, программы и содержимое экрана туда не попадают. Сбросить: удалить файл."""
import json
import logging
import os
import random
import re
import threading
import time

log = logging.getLogger("capri")

FEEDBACK_WINDOW = 30        # сколько секунд после реплики твои слова считаются реакцией на неё
SAVE_EVERY_SECONDS = 120
MAX_EXAMPLES = 10
TEMPO_MIN, TEMPO_MAX = 0.5, 3.0
HABIT_MIN_TOTAL = 120       # сколько минут наблюдений нужно, чтобы говорить о привычках
HABIT_MIN_PERIOD = 30

# Реакция голосом. Работает только сразу после реплики Капри — в остальное время такие слова просто разговор.
# Фразы проходят через norm() из actions.py: нижний регистр, «ё» → «е», без знаков препинания.
LIKE_RE = (r"^(ну )?(ха( ха)*|ахах\w*|хаха\w*|ржу|смешно|круто|класс|клево|огонь|топ|в точку|точно|красава|"
           r"молодец|метко|хорошая шутка|смешная шутка|мне нравится)( капри| шутка)?$|"
           r"^это (было )?(смешно|круто|класс|клево|в точку|огонь|метко)$")
DISLIKE_RE = (r"^(ну )?(не смешно|скучно|тупо|так себе|плоско|не надо так|слишком (грубо|резко|жестко)|перебор|мимо|"
              r"не в точку|не нравится|плохая шутка|обидно|грубо)( капри)?$|"
              r"^это (было )?(не смешно|грубо|тупо|обидно|перебор|скучно)$")

PERIODS = (("ночью", 0, 5), ("утром", 5, 12), ("днём", 12, 18), ("вечером", 18, 24))
CATEGORY_RU = {"game": "играет", "video": "смотрит видео", "social": "сидит в чатах", "work": "программирует",
               "office": "возится с документами", "web": "сидит в браузере", "music": "слушает музыку",
               "create": "творит"}


def _smooth(like, dislike):
    """Доля «нравится» со сглаживанием: без данных 0.5, с каждой реакцией уверенность растёт."""
    return (like + 1.0) / (like + dislike + 2.0)


def period_of(hour):
    for name, lo, hi in PERIODS:
        if lo <= hour < hi:
            return name
    return PERIODS[0][0]


class Learning:
    def __init__(self, path, now=time.time, rand=random.random):
        self.path, self.now, self.rand = path, now, rand
        self._lock = threading.RLock()
        self.data = {"version": 1, "sit": {}, "mode": {}, "liked": [], "disliked": [], "tempo": 1.0, "habits": {}}
        self.last = None            # (когда, ситуация, тон, текст) последней реплики
        self._dirty = False
        self._saved_at = now()
        self.load()

    # --- хранение ---
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, default in self.data.items():
                    value = data.get(key, default)
                    if type(value) is type(default):
                        self.data[key] = value
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("learning.json повреждён, начинаю заново (старый — learning.json.bad)")
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=1)
                os.replace(tmp, self.path)
                self._dirty = False
                self._saved_at = self.now()
            except OSError:
                log.exception("Не смогла сохранить learning.json")

    def save_if_due(self):
        if self._dirty and self.now() - self._saved_at >= SAVE_EVERY_SECONDS:
            self.save()

    # --- реплики и реакция ---
    def record_comment(self, situation, mode, text):
        with self._lock:
            self.last = (self.now(), situation, mode, text)

    def recent(self):
        """(ситуация, тон, текст) последней реплики, если она прозвучала только что."""
        with self._lock:
            if self.last and self.now() - self.last[0] <= FEEDBACK_WINDOW:
                return self.last[1:]
        return None

    def feedback(self, positive):
        """Реакция на последнюю реплику. Возвращает (ситуация, тон, текст) или None, если реплики не было."""
        with self._lock:
            last = self.recent()
            if not last:
                return None
            situation, mode, text = last
            self.last = None  # одна реплика — одна оценка
            field = "like" if positive else "dislike"
            for group, key in (("sit", situation), ("mode", mode)):
                if key:
                    stat = self.data[group].setdefault(key, {"like": 0, "dislike": 0})
                    stat[field] += 1
            key = "liked" if positive else "disliked"
            other = "disliked" if positive else "liked"
            self.data[other] = [t for t in self.data[other] if t != text]
            if text not in self.data[key]:
                self.data[key].append(text)
            self.data[key] = self.data[key][-MAX_EXAMPLES:]
            tempo = self.data["tempo"] * (0.92 if positive else 1.35)
            self.data["tempo"] = round(min(TEMPO_MAX, max(TEMPO_MIN, tempo)), 3)
            self.save()
            return last

    # --- как это влияет на поведение ---
    @property
    def tempo(self):
        """Во сколько раз реже (>1) или чаще (<1) обычного ей стоит говорить."""
        return float(self.data.get("tempo", 1.0))

    def weight(self, situation):
        """Множитель шанса подколки в этой ситуации: 0.4 (не заходит) … 1 (нет данных) … 1.6 (заходит)."""
        stat = self.data["sit"].get(situation)
        if not stat:
            return 1.0
        return 0.4 + 1.2 * _smooth(stat["like"], stat["dislike"])

    def pick_mode(self, modes, base):
        """Выбрать тон из modes (например, ("roast", "cheer")) с базовыми весами base и поправкой на то, что заходит."""
        weights = []
        for mode, w in zip(modes, base):
            stat = self.data["mode"].get(mode)
            weights.append(w * (0.4 + 1.2 * _smooth(stat["like"], stat["dislike"])) if stat else w)
        total = sum(weights)
        point = self.rand() * total
        for mode, w in zip(modes, weights):
            point -= w
            if point <= 0:
                return mode
        return modes[-1]

    def examples(self):
        """(понравившиеся, не понравившиеся) реплики — для подсказки нейросети."""
        with self._lock:
            return list(self.data["liked"][-3:]), list(self.data["disliked"][-2:])

    # --- привычки ---
    def note_activity(self, category, hour, minutes):
        if not category:
            return
        with self._lock:
            period = self.data["habits"].setdefault(period_of(hour), {})
            period[category] = round(period.get(category, 0.0) + minutes, 2)
            self._dirty = True
            self.save_if_due()

    def _top(self, period):
        cats = self.data["habits"].get(period, {})
        if sum(cats.values()) < HABIT_MIN_PERIOD:
            return None
        return max(cats, key=cats.get)

    def habits_summary(self):
        """Для нейросети: чем ты обычно занят в разное время суток. Пусто, пока данных мало."""
        with self._lock:
            total = sum(sum(c.values()) for c in self.data["habits"].values())
            if total < HABIT_MIN_TOTAL:
                return ""
            parts = [f"{name} он обычно {CATEGORY_RU[top]}" for name, _, _ in PERIODS
                     if (top := self._top(name)) in CATEGORY_RU]
            if not parts:
                return ""
            return ("Что известно о его привычках: " + "; ".join(parts) + ". Упоминай это редко и только к месту, "
                    "например когда он занят необычным для этого времени делом.")

    def habit_topics(self):
        """Темы для спонтанных реплик про привычки."""
        with self._lock:
            topics = []
            for name, _, _ in PERIODS:
                top = self._top(name)
                if top in CATEGORY_RU:
                    topics.append(f"заметь вслух, что {name} он почти всегда {CATEGORY_RU[top]}")
            return topics[:2]

    def unusual(self, category, hour):
        """True, если в это время суток ты обычно занят другим (для «необычно рано в игре»)."""
        with self._lock:
            top = self._top(period_of(hour))
            return bool(top and category and top != category)
