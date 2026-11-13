"""Ответы на вопросы через локальную нейросеть (Ollama) — без ключа и интернета."""
import datetime
import difflib
import json
import logging
import os
import random
import re
import subprocess
import time
import urllib.request

from persona import CHARACTER, SPEECH_RULES, user_context

log = logging.getLogger("capri")
URL = "http://127.0.0.1:11434"
NO_WINDOW = 0x08000000
SEARCH_MARK = "ПОИСК"
NO_THINKING = "Сразу дай готовый ответ от лица Капри. Без рассуждений, без анализа вопроса, без «пользователь хочет»."

SYSTEM = f"""{CHARACTER}

{SPEECH_RULES}
Отвечай на любые вопросы о науке, истории, технике, языке, о себе, советы и просьбы рассказать что-то.
Только если вопрос про текущие события (погода сейчас, свежие новости, курсы валют, цены,
результаты недавних матчей), ответь ровно одним словом: {SEARCH_MARK}"""

FRESH = re.compile(r"погод|новост|курс (доллар|евро|рубл|валют|биткоин)|сколько стоит|цена|цены|"
                   r"счет матча|кто выиграл|расписани|пробк|сегодня в мире")


FOREIGN = re.compile(r"[　-鿿가-힯＀-￯]+")


THIRD_PERSON = re.compile(r"(одн(а|ой|у|у) прекрасн(ая|ой|ую) (я|мне|меня|мной))", re.IGNORECASE)


def drop_third_person(answer):
    """Страховка поверх промпта: Капри не говорит о себе в третьем лице. Такие предложения
    выкидываем целиком (чтобы не ломать согласование глаголов), а если других нет — меняем на «я»."""
    sentences = re.split(r"(?<=[.!?…])\s+", answer)
    clean = [s for s in sentences if not THIRD_PERSON.search(s)]
    if clean:
        return " ".join(clean)
    return THIRD_PERSON.sub("я", answer)


CHATTER_TOPICS = [
    "пожалуйся, что тебе скучно", "подразни пользователя за то, что он долго сидит за компьютером",
    "пошути про то, как тесно жить в компьютере", "сделай комплимент с подколкой",
    "пошути про его рабочий стол или файлы", "скажи что-нибудь загадочное и чуть жутковатое",
    "напомни отдохнуть, но в своём стиле", "похвастайся, что ты быстрее его процессора",
    "вспомни о небе и о том, что хочешь собственное тело",
]


SELF_TALK = re.compile(r"\bя\s*[—–-]?\s*капри\b|меня зовут капри|\bкапри (здесь|тут|на связи)|^капри\b|"
                       r"\bне (просто |какой-то |обычный |обычная )?(бот|ии|программ\w*|голос в)|"
                       r"сильнее (любого |обычного )?(бот|ии)|(называ|зови|счита)\w* меня (просто )?(бот|ии)|"
                       r"каприкорн", re.IGNORECASE)
ASKED_SELF = re.compile(r"кто ты|как тебя зовут|тво[её] имя|\bии\b|бот|программ|нейросет|о себе|про себя|"
                        r"ты кто|кто такая")


def naturalize(answer, question, recent):
    """Страховка поверх промпта — чтобы Капри звучала как собеседница, а не как персонаж,
    зацикленный на своих чертах: без самопредставлений и «я не бот» без повода, без «чумба» в каждой фразе."""
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", answer) if p.strip()]
    if not ASKED_SELF.search(question.lower()):
        parts = [p for p in parts if not SELF_TALK.search(p)] or parts
    parts = [p for p in parts if not re.fullmatch(r"\W*чумба\W*", p.lower())] or parts
    text = " ".join(parts)
    # «чумба» — не чаще раза на несколько ответов
    allow = not any("чумба" in r.lower() for r in recent[-3:])
    used = []

    def drop(m):
        if allow and not used:
            used.append(1)
            return m.group(0)
        return "," if m.group(1) and m.group(2) else ""
    text = re.sub(r"(,\s*)?\bчумба\b(\s*,)?", drop, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.!?…])", r"\1", text)
    text = re.sub(r"(^|[.!?…]\s+)[,\s]+", r"\1", text)
    text = re.sub(r"(^|[.!?…]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip() or answer


def _key(sentence):
    return re.sub(r"[^\w ]", "", sentence.lower().replace("ё", "е")).strip()


def drop_repeats(answer, history):
    """Маленькая модель залипает на своих же фразах из истории («Вздыхаю…», «Иди, проверяй…»):
    выкидываем предложения, которые уже звучали (или почти так же) в прошлых ответах."""
    previous = [_key(s) for m in history if m["role"] == "assistant"
                for s in re.split(r"(?<=[.!?…])\s+", m["content"])]
    previous = [p for p in previous if len(p) >= 8]
    kept = []
    for s in re.split(r"(?<=[.!?…])\s+", answer):
        k = _key(s)
        if re.match(r"(вздыха|вздох)", k):
            continue
        if len(k) >= 8 and any(k == p or difflib.SequenceMatcher(None, k, p).ratio() > 0.8 for p in previous):
            continue
        kept.append(s)
    return " ".join(kept) or answer


# рассуждения без меток: «думающие» модели (qwen3:4b = qwen3:4b-thinking) пишут их прямо в ответ
REASONING = re.compile(r"^\W*(okay|ok,|alright|let me|the user|first,|хорошо,? (пользователь|мне|нужно|давай)|"
                       r"пользователь (спрашива|хочет|проси|пиш|говор)|мне нужно|нужно ответить|"
                       r"итак,? (пользователь|мне|нужно)|сначала (мне |нужно |надо )|давайте? (подума|разбер))",
                       re.IGNORECASE)


def strip_reasoning(text):
    """Убирает «мысли вслух»: блоки <think>…</think>, хвост до одинокого </think>
    (у новых Qwen открывающей метки нет) и незакрытый <think> до конца."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return re.sub(r"<think>.*", "", text, flags=re.S).strip()


# Ollama не запущена / упала / модели нет — это лечится перезапуском Ollama; таймаут — нет
RECOVERABLE = re.compile(r"10061|10054|refused|отверг|разорвал|reset|404|not found|closed|eof", re.IGNORECASE)


def describe_error(e):
    """Понятная причина, почему нейросеть не ответила."""
    s = str(e).lower()
    if re.search(r"404|400|not found|bad request", s):
        return "в Ollama нет нужной модели. Запусти setup_ai.bat."
    if re.search(r"10061|refused|отверг", s):
        return "Ollama не запущена и не запускается. Открой её из меню «Пуск» или переустанови через setup_ai.bat."
    if re.search(r"timed? ?out|время ожидания", s):
        return "она слишком долго думала — компьютеру тяжело. Выбери модель полегче в setup_ai.bat."
    if re.search(r"500|memory|памят", s):
        return "Ollama не хватило памяти. Закрой лишние программы или выбери модель полегче в setup_ai.bat."
    return f"{e}"


def _post(path, payload, timeout):
    req = urllib.request.Request(URL + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# какую модель взять, если указанной в настройках нет: сначала проверенные, «думающие» — в последнюю очередь
PREFERRED = ["qwen3:14b", "qwen3:8b", "qwen3:4b-instruct", "qwen3:4b-instruct-2507", "qwen3:1.7b", "qwen2.5",
             "gemma3", "llama3"]


def has_model(names, model):
    return any(n == model or n.startswith(model + ":") for n in names)  # «qwen3» = «qwen3:latest»


def pick_model(names):
    """Из скачанных моделей — подходящая для болтовни или None."""
    chat = [n for n in names if not re.search(r"embed|vision|coder|ocr", n)]
    for p in PREFERRED:
        for n in chat:
            if n == p or n.startswith(p + ":") or n.startswith(p + "-"):
                return n
    plain = [n for n in chat if "thinking" not in n and n not in ("qwen3:4b", "qwen3:4b-q4_K_M")]
    return (plain or chat or [None])[0]


class LocalAI:
    def __init__(self, cfg):
        self.model = cfg.get("local_model", "qwen3:8b")
        # сколько текста модель держит в памяти. Капри помнит лишь последние реплики, ей хватает 4096; без этого
        # Ollama берёт размер из своих настроек — на максимуме это десятки ГБ памяти, и она падает
        self.num_ctx = int(cfg.get("ai_context", 4096))
        self.history = []
        self.on_model_change = None  # main записывает новую модель в config.json
        self.problem = None          # почему нейросеть не ответила в последний раз — Капри скажет это вслух
        self.last_restart = 0.0

    @staticmethod
    def installed():
        """Названия скачанных моделей или None, если Ollama не отвечает."""
        try:
            with urllib.request.urlopen(URL + "/api/tags", timeout=1.5) as r:
                return [m["name"] for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        except Exception:
            return None

    def running(self):
        return has_model(self.installed() or [], self.model)

    def start_server(self):
        """Запускает Ollama, если она не запущена, и заранее грузит модель в память. Если указанной
        в настройках модели нет, а другая скачана — переключается на неё и запоминает."""
        names = self.installed()
        if names is None:
            exe = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
            try:
                subprocess.Popen([exe if os.path.exists(exe) else "ollama", "serve"],
                                 creationflags=NO_WINDOW)
            except OSError:
                log.info("Ollama не установлена")
                return
            for _ in range(20):  # сервер поднимается пару секунд
                time.sleep(0.5)
                names = self.installed()
                if names is not None:
                    break
            if names is None:
                log.info("Ollama не отвечает")
                return
        if not has_model(names, self.model):
            other = pick_model(names)
            if not other:
                log.info("В Ollama нет ни одной модели — запусти setup_ai.bat или: ollama pull %s", self.model)
                return
            log.info("Модели %s нет, беру скачанную %s", self.model, other)
            self.model = other
            if self.on_model_change:
                self.on_model_change(other)
        try:
            # тот же размер контекста, что и в разговоре — иначе Ollama загрузит модель дважды
            _post("/api/generate", {"model": self.model, "prompt": "", "keep_alive": "10m",
                                    "options": {"num_ctx": self.num_ctx}}, 120)
            log.info("Локальная модель %s загружена", self.model)
        except Exception as e:
            log.info("Не удалось прогреть модель: %s", e)

    def _chat_recovering(self, messages):
        """Ответ модели; если Ollama упала, закрылась или потеряла модель — поднимаем её заново и пробуем ещё раз.
        Раньше Ollama запускалась только при старте Капри, и после её падения разговор не работал до перезапуска."""
        try:
            return self._chat(messages)
        except Exception as e:
            if not RECOVERABLE.search(str(e)) or time.time() - self.last_restart < 60:
                raise
            self.last_restart = time.time()
            log.info("Ollama не ответила (%s) — запускаю её заново", e)
            self.start_server()
            return self._chat(messages)

    def _chat(self, messages, max_tokens=300):
        resp = _post("/api/chat", {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,  # Qwen3 по умолчанию «рассуждает» вслух — для голоса это лишняя задержка
            "keep_alive": "10m",
            "options": {"num_predict": max_tokens, "temperature": 0.6, "repeat_penalty": 1.15,
                        "num_ctx": self.num_ctx},
        }, 120)
        text = strip_reasoning(resp.get("message", {}).get("content", ""))
        if REASONING.search(text):
            # модель всё равно «думает» вслух — переспрашиваем, а если опять, ответа нет
            log.info("Модель %s рассуждает вслух вместо ответа", self.model)
            if messages[-1].get("content", "").startswith(NO_THINKING):
                return ""
            return self._chat(messages + [{"role": "system", "content": NO_THINKING}], max_tokens)
        return text

    def ask(self, question):
        """Текст ответа или None, если лучше открыть поиск."""
        if FRESH.search(question.lower()):
            return None
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        self.history.append({"role": "user", "content": question})
        self.history = self.history[-6:]
        messages = [{"role": "system", "content": SYSTEM + "\n" + user_context() + f"\nСейчас {now}."}] + self.history
        try:
            answer = self._chat_recovering(messages)
            if FOREIGN.search(answer):  # Qwen иногда срывается на китайский — пробуем ещё раз
                answer = self._chat(messages + [{"role": "system", "content":
                                    "Ответь заново, строго только русскими буквами, без иероглифов."}])
            answer = re.sub(r"\s+", " ", FOREIGN.sub("", answer)).strip()
            self.problem = None
        except Exception as e:
            log.info("Локальная модель недоступна: %s", e)
            self.problem = describe_error(e)
            self.history.pop()
            return None
        recent = [m["content"] for m in self.history if m["role"] == "assistant"]
        answer = naturalize(drop_repeats(drop_third_person(answer), self.history), question, recent)
        answer = re.sub(r"\s*\([^)]*\)", "", answer)                      # ремарки в скобках
        answer = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", answer)  # эмодзи
        answer = re.sub(r"\s+", " ", answer).strip()
        answer = answer[:1].upper() + answer[1:]
        broken = len(answer) < 40 and not re.search(r"[.!?…]$", answer)
        if not answer or broken or SEARCH_MARK in answer.upper()[:20]:
            self.history.pop()
            return None
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def chatter(self):
        """Короткая спонтанная реплика, когда Капри появляется сама. None — если модель недоступна."""
        try:
            text = self._chat([
                {"role": "system", "content": SYSTEM + "\n" + user_context()},
                {"role": "user", "content":
                 "Ты сама решила ненадолго появиться на экране. Скажи одну короткую спонтанную фразу, "
                 f"до пятнадцати слов. Тема: {random.choice(CHATTER_TOPICS)}. "
                 "Не спрашивай, чем помочь, и не упоминай команды."},
            ], max_tokens=60)
        except Exception:
            return None
        text = drop_third_person(re.sub(r"\s+", " ", FOREIGN.sub("", text)).strip().strip('"«»'))
        text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", text).strip()
        return text if 5 < len(text) < 160 and re.search(r"[.!?…]$", text) else None

    def summarize_report(self, report):
        try:
            return self._chat([
                {"role": "system", "content": SYSTEM + "\n" + user_context()},
                {"role": "user", "content":
                 "Вот отчёт диагностики моего ПК. Скажи 2–4 предложения: общее состояние, главные "
                 "проблемы и что сделать. Безвредные ошибки вроде DistributedCOM не раздувай.\n\n"
                 + report[:12000]},
            ], max_tokens=400) or None
        except Exception:
            return None
