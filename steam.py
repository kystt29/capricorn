"""Сообщения друзьям в Steam голосом: «Капри, напиши Васе в стиме, что я опоздаю».

Как это работает:
1. Друзей берём из файла, который Steam сам хранит на твоём компьютере (userdata\\<аккаунт>\\config\\localconfig.vdf):
   там лежат ники друзей. Ключ Steam API и вход в аккаунт не нужны, ничего не скачивается.
2. Чат открывается официальной ссылкой steam://friends/message/<id> — Steam сам показывает окно переписки.
3. Текст вставляется в активное окно Steam и отправляется Enter — но только если в фокусе действительно окно Steam.
   Иначе Капри ничего не печатает, чтобы не написать не туда.

Перед отправкой Капри читает вслух, кому и что, и ждёт «да» (отключается: "steam_confirm": false): сообщение друзьям
не вернёшь, а распознавание речи иногда ошибается.

Ники друзей обычно латиницей («xX_Vasya_Xx»), а говоришь ты «Вася». Похожие имена Капри находит сама (по звучанию и
падежам: «Васе», «Диме»). Остальных можно привязать в config.json:
    "steam_friends": {"вася": "xX_Vasya_Xx", "брат": "76561198012345678"}
Справа — ник из списка друзей Steam или числовой id (steamid64 либо номер аккаунта)."""
import difflib
import logging
import os
import sys
import re
import time

log = logging.getLogger("capri")

STEAM64_BASE = 76561197960265728
STEAM_PROCS = {"steam.exe", "steamwebhelper.exe"}
# Окна Steam, в которых может быть открыт чат: заголовок окна переписки — это ник друга,
# а встроенный чат живёт в окне «Friends & Chat».
CHAT_TITLES = ("friends", "друзья", "чат", "chat", "steam")
MAX_LEN = 400
MATCH_MIN = 0.72

VERBS = r"(напиши|отправь|отправить|скинь|кинь|пошли|черкани|передай|скажи)"
MARK = r"\b(в|во|через|по)\s+(стим\w*|steam\w*)\b"
# «напиши Васе в стиме привет» — только когда прямо сказано «в стиме»: иначе «напиши привет» остаётся обычной печатью
SEND_RE = rf"^{VERBS}\b(?=.*{MARK})"
FRIEND_WORDS = r"^(другу|другану|товарищу|приятелю|корешу|другом)\s+"
TEXT_FILLER = r"^(сообщение|сообщенье|текст|что|чтобы|ему|ей)\s+"


# ---------------- файлы Steam ----------------

def tokenize_vdf(text):
    """Строки в кавычках и фигурные скобки — всё, что нужно для чтения localconfig.vdf."""
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"|([{}])', text):
        if m.group(2):
            yield m.group(2)
        else:
            yield re.sub(r"\\(.)", r"\1", m.group(1))


def parse_vdf(text):
    """VDF → вложенные словари (повторяющиеся ключи перезаписываются — для списка друзей этого достаточно)."""
    root, stack, key = {}, [], None
    node = root
    for tok in tokenize_vdf(text):
        if tok == "{":
            child = {}
            if key is not None:
                node[key] = child
            stack.append(node)
            node, key = child, None
        elif tok == "}":
            if stack:
                node = stack.pop()
            key = None
        elif key is None:
            key = tok
        else:
            node[key] = tok
            key = None
    return root


def _ci(d, name):
    """Значение по ключу без учёта регистра (в разных версиях Steam регистр ключей разный)."""
    low = name.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return None


def friends_from_config(vdf):
    """{accountid: [ники]} из разобранного localconfig.vdf. Ключи друзей — числовые."""
    store = _ci(vdf, "UserLocalConfigStore") or vdf
    block = _ci(store, "friends")
    out = {}
    if not isinstance(block, dict):
        return out
    for key, val in block.items():
        if not (key.isdigit() and isinstance(val, dict)):
            continue
        names = []
        for field in ("name", "NickName"):  # ник в Steam и своё прозвище друга (регистр ключа не важен)
            v = _ci(val, field)
            if isinstance(v, str) and v.strip() and v.strip() not in names:
                names.append(v.strip())
        if names:
            out[int(key)] = names
    return out


def steam_dir(override=""):
    if override and os.path.isdir(override):
        return override
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            path = winreg.QueryValueEx(k, "SteamPath")[0]
        if path and os.path.isdir(path):
            return os.path.normpath(path)
    except Exception:
        pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(guess):
            return guess
    return None


def active_account(steam):
    """Номер аккаунта, который сейчас вошёл в Steam: из реестра, а если там пусто — самый свежий в userdata."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as k:
            user = int(winreg.QueryValueEx(k, "ActiveUser")[0])
        if user:
            return user
    except Exception:
        pass
    best, best_time = None, -1
    base = os.path.join(steam, "userdata")
    try:
        for d in os.listdir(base):
            f = os.path.join(base, d, "config", "localconfig.vdf")
            if d.isdigit() and os.path.isfile(f) and os.path.getmtime(f) > best_time:
                best, best_time = int(d), os.path.getmtime(f)
    except OSError:
        pass
    return best


_cache = {"key": None, "friends": {}}


def load_friends(override=""):
    """{accountid: [ники]} друзей вошедшего в Steam аккаунта. Пусто — если Steam не найден или список не читается."""
    steam = steam_dir(override)
    if not steam:
        return {}
    account = active_account(steam)
    if not account:
        return {}
    path = os.path.join(steam, "userdata", str(account), "config", "localconfig.vdf")
    try:
        key = (path, os.path.getmtime(path))
        if _cache["key"] == key:
            return _cache["friends"]
        with open(path, encoding="utf-8", errors="replace") as f:
            friends = friends_from_config(parse_vdf(f.read()))
    except OSError:
        return {}
    except Exception:
        log.exception("localconfig.vdf не читается")
        return {}
    _cache.update(key=key, friends=friends)
    return friends


def steamid64(account):
    """Номер аккаунта → steamid64; значения, уже похожие на steamid64, не трогаем."""
    account = int(account)
    return account if account >= STEAM64_BASE else account + STEAM64_BASE


# ---------------- поиск друга по тому, как его назвали голосом ----------------

def _stem(s):
    """«васе» / «диме» / «антону» → корень, чтобы падеж не мешал."""
    return re.sub(r"[аеёиоуыэюяйь]+$", "", s) if len(s) > 3 else s


def _variants(s, translit):
    base = {s, _stem(s)}
    return base | {translit(v) for v in base}


def similar(a, b):
    if a == b:
        return 1.0
    short, long = sorted((a, b), key=len)
    if len(short) >= 3 and short in long:
        return 0.75 + 0.2 * len(short) / len(long)
    return difflib.SequenceMatcher(None, a, b).ratio()


def score(query, name, norm, translit):
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0.0
    return max(similar(a, b) for a in _variants(q, translit) for b in _variants(n, translit))


def find_friend(query, friends, aliases, norm, translit):
    """(оценка, steamid64, как назвать вслух) лучшего друга или None. Свои названия из "steam_friends" важнее:
    справа в них ник из списка друзей или числовой id."""
    best = None

    def consider(s, sid, label):
        nonlocal best
        if s >= MATCH_MIN and (best is None or s > best[0]):
            best = (s, sid, label)

    by_name = {}
    for account, names in friends.items():
        for n in names:
            by_name.setdefault(n.lower(), (account, names[0]))
    by_clean = {}
    for key, value in by_name.items():
        clean = re.sub(r"[^\w]", "", key)
        if clean:
            by_clean.setdefault(clean, value)
    for alias, target in (aliases or {}).items():
        s = score(query, alias, norm, translit)
        if s < MATCH_MIN:
            continue
        target = str(target).strip()
        if target.isdigit():
            consider(s + 0.01, steamid64(target), alias)  # свой список чуть важнее общего
        else:
            account_label = by_name.get(target.lower())
            if account_label is None:
                needle = re.sub(r"[^\w]", "", target.lower())
                if needle:
                    for key, value in by_clean.items():
                        if needle in key or key in needle:
                            account_label = value
                            break
            if account_label is None:
                # target could be a decorated nik (e.g. "xX_Vasya_Xx" vs local "VAS")
                # or remembered with other symbols; try flexible similarity.
                best_friend = None
                for name, value in by_name.items():
                    if score(target, name, norm, translit) >= 0.45 or translit(target.lower()) in translit(name):
                        best_friend = value
                        break
                account_label = best_friend
            if account_label is not None:
                account, label = account_label
                consider(s + 0.01, steamid64(account), label)
    for account, names in friends.items():
        for n in names:
            consider(score(query, n, norm, translit), steamid64(account), names[0])
    return best


# ---------------- разбор фразы ----------------

def parse(c, find):
    """«напиши васе в стиме что я опоздаю» → ("ok", друг, текст). find(запрос) → друг или None.
    Другие исходы: ("no_text", друг, ""), ("no_friend", запрос, текст)."""
    m = re.match(rf"^{VERBS}\s+(?P<rest>.+)$", c.strip())
    if not m:
        return None
    rest = re.sub(r"^(сообщение|сообщенье|текст)\s+", "", m.group("rest"))
    mark = re.search(MARK, rest)
    if not mark:
        return None
    left = re.sub(FRIEND_WORDS, "", rest[:mark.start()].strip())
    right = re.sub(FRIEND_WORDS, "", rest[mark.end():].strip())
    if left and right:                    # «Васе в стиме привет»
        friend = find(left)
        if friend:
            return clean(friend, left, right)
    words = f"{left} {right}".split()     # «в стиме Васе привет», «Васе привет в стиме», «что я буду в стиме Васе»
    best, best_k = None, 0
    for k in (1, 2, 3):
        if len(words) <= k:
            break
        # имя в начале фразы важнее имени в конце; из двух одинаковых оценок берём более длинное имя
        for cand, text in ((" ".join(words[:k]), words[k:]), (" ".join(words[-k:]), words[:-k])):
            friend = find(cand)
            if friend and (best is None or friend[0] > best[0][0] + 1e-9
                           or (abs(friend[0] - best[0][0]) <= 1e-9 and k > best_k)):
                best, best_k = (friend, " ".join(text)), k
    if best:
        return clean(best[0], "", best[1])
    if len(words) == 1:                   # «напиши Васе в стиме» — без текста
        friend = find(words[0])
        return ("no_text", friend, "") if friend else ("no_friend", words[0], "")
    return ("no_friend", " ".join(words[:2]), " ".join(words[2:]))


def clean(friend, who, text):
    text = re.sub(TEXT_FILLER, "", text.strip()).strip(" ,")
    if not friend:
        return ("no_friend", who, text)
    if not text:
        return ("no_text", friend, "")
    text = (text[:1].upper() + text[1:])[:MAX_LEN]
    return ("ok", friend, text)


# ---------------- отправка (только Windows) ----------------

def is_running():
    import psutil
    return any((p.info["name"] or "").lower() == "steam.exe" for p in psutil.process_iter(["name"]))


def _chat_window_ok(automation, name):
    """В фокусе окно Steam, и это чат (или «Friends & Chat»)."""
    if automation.foreground_process() not in STEAM_PROCS:
        return False
    title = automation.foreground_title().lower().replace("‎", "")
    words = [w for w in re.split(r"\W+", name.lower()) if len(w) >= 3]
    return any(w in title for w in words) or any(t in title for t in CHAT_TITLES)


def send_message(steam64, name, text, timeout=12.0):
    """Открыть чат, дождаться окна Steam в фокусе и отправить. True — текст вставлен и Enter нажат.
    Если окно чата не появилось или в фокусе что-то другое, ничего не печатаем."""
    import automation
    from actions import send_keys
    os.startfile(f"steam://friends/message/{steam64}")
    end = time.time() + timeout
    ready = False
    while time.time() < end:
        time.sleep(0.5)
        if not _chat_window_ok(automation, name):
            hwnd = automation.find_window(tuple(w for w in re.split(r"\W+", name.lower()) if len(w) >= 3)
                                          + ("friends", "друзья"))
            if hwnd:
                automation.focus(hwnd)
            continue
        time.sleep(0.8)                  # поле ввода получает фокус не сразу
        ready = _chat_window_ok(automation, name)  # за это время окно могли сменить
        break
    if not ready:
        log.info("Steam: окно чата не появилось (в фокусе «%s»)", automation.foreground_title())
        return False
    automation.paste_text(text)
    time.sleep(0.15)
    send_keys(["enter"])
    return True
