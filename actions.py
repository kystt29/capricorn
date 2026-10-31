"""Команды: сайты, приложения, громкость, яркость, диагностика ПК и т.д."""
import ctypes
import datetime
import difflib
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass

import psutil

import automation
import commentary
import freestyle
import games
import learning
import media
import persona
import steam
from persona import line

log = logging.getLogger("capri")
NO_WINDOW = 0x08000000


@dataclass
class Reply:
    text: str
    action: str | None = None    # "hide" / "quit"


def norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s\.\-:/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


TR = dict(zip("абвгдезийклмнопрстуфхцы", "abvgdeziyklmnoprstufhcy"))
TR.update({"ё": "e", "ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ь": "", "э": "e",
           "ю": "yu", "я": "ya"})


def translit(s):
    return "".join(TR.get(c, c) for c in s)


NUM = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
       "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
       "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
       "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
       "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
       "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}


def parse_number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total = None
    for w in text.split():
        if w in NUM:
            total = (total or 0) + NUM[w]
        elif total is not None:
            break
    if total is not None:
        return total
    if re.search(r"максимум|на полную|на всю", text):
        return 100
    if re.search(r"половин", text):
        return 50
    if re.search(r"минимум", text):
        return 0
    return None


def run_ps(cmd, timeout=60):
    full = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference='SilentlyContinue'; " + cmd)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
                       capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


def ps_json(cmd):
    out = run_ps(cmd + " | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = json.loads(out) if out else []
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else [data]


# ---------------- громкость / яркость ----------------

def _endpoint():
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume():
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume(v):
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(max(0, min(100, v)) / 100, None)
    if v > 0:
        ep.SetMute(0, None)


def set_mute(on):
    _endpoint().SetMute(1 if on else 0, None)


def get_brightness():
    import screen_brightness_control as sbc
    return sbc.get_brightness()[0]


def set_brightness(v):
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, v)))


# ---------------- поиск приложений ----------------

PROTECTED_PROCS = {"explorer", "python", "pythonw", "svchost", "csrss", "winlogon", "dwm", "lsass", "services",
                   "system", "smss", "wininit", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "runtimebroker",
                   "searchhost", "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "ollama",
                   "ollama app", "conhost", "audiodg"}
SKIP_WORDS = ("uninstall", "удален", "удалить", "деинсталл", "readme", "help", "справка", "documentation",
              "license", "support", "поддержк", "feedback", "release notes", "website")


# как браузеры называют вслух → по чему узнать в списке браузеров Windows (название ключа или exe)
BROWSER_WORDS = [
    (r"(гугл )?хром\w*|chrome", "chrome"),
    (r"яндекс\w*|yandex", "yandex"),
    (r"опер\w*|opera", "opera"),
    (r"(фаер|файр|файер)фокс\w*|мозил+\w*|firefox", "firefox"),
    (r"(майкрософт )?эдж\w*|edge", "edge"),
    (r"брейв\w*|brave", "brave"),
    (r"вивальди|vivaldi", "vivaldi"),
]
BROWSER_NAMES = {"chrome": "Google Chrome", "yandex": "Яндекс Браузер", "opera": "Opera", "firefox": "Firefox",
                 "edge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi"}
# «… в хроме», «… через яндекс браузер», «… в браузере опера» — только в конце фразы
BROWSER_SUFFIX = re.compile(r"\s+(в|через)\s+(браузер\w*\s+)?(?P<b>" + "|".join(p for p, _ in BROWSER_WORDS)
                            + r")(\s+браузер\w*)?$")


def installed_browsers():
    """{"chrome": путь к exe, ...} — браузеры из списка Windows («Приложения по умолчанию»)."""
    import winreg
    found = {}
    for hive, path in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Clients\StartMenuInternet"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Clients\StartMenuInternet")):
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                cmd = winreg.QueryValue(key, name + r"\shell\open\command")
            except OSError:
                continue
            m = re.match(r'^\s*"?([^"]+?\.exe)', cmd)
            if not m or not os.path.isfile(m.group(1)):
                continue
            label = (name + " " + os.path.basename(m.group(1))).lower()
            for _, k in BROWSER_WORDS:
                if k in label or (k == "edge" and "msedge" in label):
                    found.setdefault(k, m.group(1))
    return found


def find_browser(name):
    """«хром» / «яндекс браузер» / путь к exe → путь к exe браузера или None, если такого нет."""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    n = norm(name)
    for pattern, k in BROWSER_WORDS:
        if re.fullmatch(rf"({pattern})( браузер\w*)?|браузер\w* ({pattern})", n):
            return installed_browsers().get(k)
    return None


def installed_programs():
    """[(название, путь к exe)] из «Программ и компонентов» Windows: значок программы обычно и есть её exe."""
    import winreg
    out = []
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
            except OSError:
                continue
            values = {}
            for field in ("DisplayIcon", "InstallLocation", "UninstallString"):
                try:
                    values[field] = str(winreg.QueryValueEx(sub, field)[0])
                except OSError:
                    values[field] = ""
            exe = re.sub(r",\s*-?\d+$", "", values["DisplayIcon"]).strip().strip('"')
            base = os.path.basename(exe).lower()
            if exe.lower().endswith(".exe") and "unins" not in base and "setup" not in base and os.path.isfile(exe):
                out.append((norm(name), exe))
                continue
            # значок — деинсталлятор (так у Steam): ищем рядом exe с названием программы, steam → steam.exe
            folder = values["InstallLocation"].strip().strip('"')
            if not os.path.isdir(folder):
                m = re.match(r'^"?([^"]+?\.exe)', values["UninstallString"] or exe)
                folder = os.path.dirname(m.group(1)) if m else ""
            guess = os.path.join(folder, re.sub(r"[^\w\-]", "", name) + ".exe") if folder else ""
            if guess and os.path.isfile(guess):
                out.append((norm(name), guess))
    return out


class AppIndex:
    def __init__(self):
        self.items = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()

    def build(self):
        items = {}
        try:
            for app in ps_json("Get-StartApps | Select-Object Name,AppID"):
                name = norm(app.get("Name") or "")
                if name and not any(w in name for w in SKIP_WORDS):
                    items[name] = ("appid", app.get("AppID"))
        except Exception:
            log.exception("Get-StartApps")
        home = os.path.expanduser("~")
        dirs = [os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
                os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"),
                os.path.join(os.environ.get("PUBLIC", ""), "Desktop")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith((".lnk", ".url", ".appref-ms")):
                        name = norm(os.path.splitext(f)[0])
                        if name and not any(w in name for w in SKIP_WORDS):
                            # ярлык надёжнее идентификатора из Get-StartApps: у программ вроде Discord
                            # (установщик Squirrel) запуск по идентификатору ничего не открывает
                            if items.get(name, ("appid",))[0] != "file":
                                items[name] = ("file", os.path.join(root, f))
        # установленные программы из реестра — на случай, если ярлыка в «Пуске» нет (бывает у Steam)
        for name, exe in installed_programs():
            if name not in items and not any(w in name for w in SKIP_WORDS):
                items[name] = ("file", exe)
        with self.lock:
            self.items = items
        self.ready.set()
        log.info("Найдено приложений: %d", len(items))

    @staticmethod
    def _sound(s):
        s = re.sub(r"\s", "", translit(s.lower()))
        for a, b in (("ph", "f"), ("ck", "k"), ("c", "k"), ("q", "k"), ("w", "v"), ("x", "ks"), ("z", "s")):
            s = s.replace(a, b)
        return s

    @staticmethod
    def _skeleton(s):
        """Согласные без гласных: «cloud», «claude», «клод» → «kld». Ловит созвучные названия."""
        return re.sub(r"[aeiouy]", "", AppIndex._sound(s))

    @staticmethod
    def sounds_like(a, b):
        """Созвучны, если совпадают согласные и первый звук: «роблокс» = «roblox», но «запрет» ≠ «aseprite»."""
        sk = AppIndex._skeleton(a)
        return len(sk) >= 3 and sk == AppIndex._skeleton(b) and AppIndex._sound(a)[:1] == AppIndex._sound(b)[:1]

    def candidates(self, query, aliases, limit=4):
        """Подходящие приложения, лучшие первыми: [(оценка, название, цель)]."""
        q = norm(query)
        variants = {q, translit(q)}
        if q in aliases:
            variants.add(norm(aliases[q]))
        with self.lock:
            items = list(self.items.items())
        scored = {}
        for name, target in items:
            best = 0.0
            for v in variants:
                if v == name:
                    score = 1.0
                elif len(v) >= 3 and re.search(r"(^|\s)" + re.escape(v), name):
                    score = 0.93 - 0.003 * len(name)
                else:
                    score = difflib.SequenceMatcher(None, v, name).ratio()
                    first = name.split()[0] if name.split() else name
                    if self.sounds_like(v, name):
                        score = max(score, 0.97)  # звучит как всё название: «роблокс» = «roblox»
                    elif self.sounds_like(v, first):
                        score = max(score, 0.86)  # звучит так же: «клод/cloud» ~ «claude»
                best = max(best, score)
            if best >= 0.72 and best > scored.get(name, (0,))[0]:
                scored[name] = (best, target)
        ranked = sorted(((s, n, t) for n, (s, t) in scored.items()), key=lambda x: -x[0])
        return ranked[:limit]

    def find(self, query, aliases):
        found = self.candidates(query, aliases, 1)
        return (found[0][1], found[0][2]) if found else None

    @staticmethod
    def launch(target):
        kind, value = target
        if kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"], creationflags=NO_WINDOW)
            return
        os.startfile(value)

    def launch_best(self, query, options):
        """Запустить лучший вариант; если Windows его не открыла (например, Roblox.lnk — «операция
        отменена»), пробуем похожие ярлыки с тем же началом (Roblox Player), а в конце — через проводник,
        как при двойном клике. options: [(оценка, имя, цель)]. Возвращает имя запущенного или None."""
        first = options[0]
        q = first[1].split()[0] if first[1].split() else ""  # «roblox» из найденного «roblox», а не «роблокс»
        with self.lock:
            similar = [(1.0, n, t) for n, t in self.items.items() if n != first[1] and q and n.startswith(q)
                       and not re.search(r"studio|uninstall|update", n)]
        for _, name, target in [first] + sorted(similar, key=lambda x: len(x[1])):
            try:
                self.launch(target)
                return name
            except OSError as e:
                log.info("%s не запустился (%s)", name, e)
        if first[2][0] == "file":
            subprocess.Popen(["explorer.exe", first[2][1]], creationflags=NO_WINDOW)
            return first[1]
        return None


# ---------------- диагностика ----------------

def _dir_size(path, limit=200000):
    total, n = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit:
                return total
    return total


def analyze_pc():
    """Возвращает (список проблем, полный текстовый отчёт)."""
    issues, lines = [], []
    add = lines.append
    now = datetime.datetime.now()
    add(f"ДИАГНОСТИКА ПК — {now:%d.%m.%Y %H:%M}")
    add("=" * 60)

    cpu = psutil.cpu_percent(interval=1.5)
    mem = psutil.virtual_memory()
    add(f"Процессор: загрузка {cpu:.0f}% ({psutil.cpu_count()} потоков)")
    add(f"Память: занято {mem.percent:.0f}% ({mem.used / 2**30:.1f} из {mem.total / 2**30:.1f} ГБ)")
    if cpu > 85:
        issues.append(f"процессор загружен на {cpu:.0f} процентов")
    if mem.percent > 85:
        issues.append(f"оперативная память занята на {mem.percent:.0f} процентов")

    up = now - datetime.datetime.fromtimestamp(psutil.boot_time())
    add(f"Время работы без перезагрузки: {up.days} д {up.seconds // 3600} ч")
    if up.days >= 7:
        issues.append(f"компьютер не перезагружался {up.days} дней")

    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        add(f"Батарея: {bat.percent:.0f}% {'(заряжается)' if bat.power_plugged else ''}")

    add("\nДИСКИ:")
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        free_gb = u.free / 2**30
        add(f"  {part.mountpoint} {part.fstype}: свободно {free_gb:.1f} ГБ из {u.total / 2**30:.0f} ГБ ({100 - u.percent:.0f}%)")
        if u.percent > 90 or free_gb < 10:
            issues.append(f"на диске {part.mountpoint[0]} мало места — {free_gb:.0f} гигабайт")

    for d in ps_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus"):
        add(f"  Накопитель {d.get('FriendlyName')}: здоровье {d.get('HealthStatus')}, состояние {d.get('OperationalStatus')}")
        if str(d.get("HealthStatus")) not in ("Healthy", "0"):
            issues.append(f"накопитель {d.get('FriendlyName')} сообщает о проблемах со здоровьем")

    tmp = os.environ.get("TEMP", "")
    if tmp:
        size = _dir_size(tmp) / 2**30
        add(f"  Временные файлы (%TEMP%): {size:.1f} ГБ")
        if size > 3:
            issues.append(f"временные файлы занимают {size:.0f} гигабайт")

    add("\nУСТРОЙСТВА С ОШИБКАМИ:")
    bad = ps_json("Get-PnpDevice -PresentOnly | Where-Object {$_.Status -eq 'Error'} | Select-Object FriendlyName,Class")
    for d in bad:
        add(f"  {d.get('FriendlyName')} ({d.get('Class')})")
    if not bad:
        add("  нет")
    else:
        issues.append(f"устройств с ошибкой драйвера: {len(bad)}")

    add("\nОШИБКИ В ЖУРНАЛАХ WINDOWS ЗА 24 ЧАСА (по источникам):")
    groups = ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} | Group-Object ProviderName | "
                     "Sort-Object Count -Descending | Select-Object -First 12 Count,Name")
    total_err = sum(int(g.get("Count", 0)) for g in groups)
    for g in groups:
        add(f"  {g.get('Count'):>4}  {g.get('Name')}")
    if not groups:
        add("  нет")
    if total_err > 30:
        issues.append(f"в журналах {total_err} ошибок за сутки")

    add("\nПОСЛЕДНИЕ ОШИБКИ:")
    for e in ps_json("Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                     "StartTime=(Get-Date).AddDays(-1)} -MaxEvents 8 | Select-Object "
                     "@{n='T';e={$_.TimeCreated.ToString('dd.MM HH:mm')}},ProviderName,Id,"
                     "@{n='M';e={if($_.Message){$_.Message.Substring(0,[Math]::Min(220,$_.Message.Length))}}}"):
        msg = re.sub(r"\s+", " ", str(e.get("M") or ""))
        add(f"  [{e.get('T')}] {e.get('ProviderName')} #{e.get('Id')}: {msg}")

    crashes = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=41;"
                     "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    bsod = run_ps("(Get-WinEvent -FilterHashtable @{LogName='System';Id=1001;"
                  "ProviderName='Microsoft-Windows-WER-SystemErrorReporting';"
                  "StartTime=(Get-Date).AddDays(-7)} | Measure-Object).Count")
    crashes = int(crashes) if crashes.isdigit() else 0
    bsod = int(bsod) if bsod.isdigit() else 0
    add(f"\nАварийные выключения за 7 дней: {crashes}; синие экраны (BSOD): {bsod}")
    if crashes:
        issues.append(f"аварийных выключений за неделю: {crashes}")
    if bsod:
        issues.append(f"синих экранов за неделю: {bsod}")

    add("\nТОП ПРОЦЕССОВ ПО ПАМЯТИ:")
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    for rss, name in sorted(procs, reverse=True)[:8]:
        add(f"  {rss / 2**20:>7.0f} МБ  {name}")

    add("\nПРОГРАММЫ В АВТОЗАГРУЗКЕ:")
    for s in ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location"):
        add(f"  {s.get('Name')}  [{s.get('Location')}]")

    add("\n" + "=" * 60)
    add("НАЙДЕННЫЕ ПРОБЛЕМЫ:" if issues else "Серьёзных проблем не найдено.")
    for i in issues:
        add(f"  • {i}")
    return issues, "\n".join(lines)


# ---------------- ютуб ----------------

BROWSERS = ("firefox", "chrome", "msedge", "opera", "browser", "brave", "vivaldi", "yandex")


def browser_playing():
    """True, если из браузера сейчас реально идёт звук (по индикатору громкости в микшере Windows)."""
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
    for s in AudioUtilities.GetAllSessions():
        try:
            name = (s.Process.name() if s.Process else "").lower()
            if any(b in name for b in BROWSERS):
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                if meter.GetPeakValue() > 0.001:
                    return True
        except Exception:
            continue
    return False


def youtube_window():
    """Окно браузера с открытым YouTube (по заголовку активной вкладки)."""
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, buf, 512)
            if "youtube" in buf.value.lower():
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


_ensure_token = [0]  # номер последней проверки «заиграло ли видео»; новая команда отменяет старую


def cancel_ensure():
    _ensure_token[0] += 1


def ensure_playing(wait_load=7.0):
    """Браузеры блокируют автозапуск со звуком у вкладок, открытых программой. Ждём звук;
    если тишина — переключаемся на окно YouTube и жмём K (пуск/пауза в плеере), затем проверяем.
    Если за это время пришла новая команда — тихо выходим, чтобы не кликнуть посреди её работы."""
    _ensure_token[0] += 1
    token = _ensure_token[0]
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    deadline = time.time() + wait_load
    while time.time() < deadline:  # может, запустилось само (или идёт реклама)
        if _ensure_token[0] != token:
            return
        if browser_playing():
            log.info("Видео играет само")
            return
        time.sleep(0.5)
    if _ensure_token[0] != token:
        return
    hwnd = None
    for _ in range(10):  # заголовок вкладки может ещё не смениться на «… - YouTube»
        hwnd = youtube_window()
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        log.info("Окно YouTube не найдено, не нажимаю пуск")
        return
    u = ctypes.windll.user32
    u.keybd_event(0x12, 0, 0, 0)       # «пустой» Alt — без него Windows не даёт сменить активное окно
    u.keybd_event(0x12, 0, 2, 0)
    u.SetForegroundWindow(hwnd)
    time.sleep(0.4)

    # Клик мышью по плееру — настоящее действие пользователя: и окно активирует, и фокус ставит в плеер,
    # и браузер считает это разрешением на звук. Точка (30% ширины, 35% высоты) попадает в плеер
    # и в обычном, и в широком режиме YouTube.
    from ctypes import wintypes
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = r.left + int((r.right - r.left) * 0.30), r.top + int((r.bottom - r.top) * 0.35)
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos(x, y)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # левая кнопка вниз
    u.mouse_event(0x0004, 0, 0, 0, 0)  # вверх
    time.sleep(0.05)
    u.SetCursorPos(old.x, old.y)
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено кликом по плееру")
            return
    send_keys(["k"])  # запасной вариант: фокус уже в плеере
    for _ in range(6):
        time.sleep(0.5)
        if browser_playing():
            log.info("Видео запущено клавишей K")
            return
    log.info("Не удалось запустить видео: звука нет")

def youtube_first(query):
    """(id, название) первого обычного видео (не шортс) в поиске YouTube или None."""
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as e:
        log.info("Поиск на YouTube не удался: %s", e)
        return None
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
    if not m:
        return None
    title = ""
    t = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', page[m.end():m.end() + 6000])
    if t:
        try:
            title = json.loads(f'"{t.group(1)}"')
        except ValueError:
            title = t.group(1)
    return m.group(1), title


_desc_cache = {}


def file_description(path):
    """Описание программы из свойств exe-файла (то, что показывает диспетчер задач),
    например «Steam Client WebHelper». Пустая строка, если описания нет."""
    if not path:
        return ""
    if path in _desc_cache:
        return _desc_cache[path]
    desc = ""
    try:
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if size:
            buf = ctypes.create_string_buffer(size)
            if v.GetFileVersionInfoW(path, 0, size, buf):
                ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
                if v.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                    lang, cp = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort * 2)).contents
                    key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
                    sptr = ctypes.c_wchar_p()
                    if v.VerQueryValueW(buf, key, ctypes.byref(sptr), ctypes.byref(ln)) and ln.value:
                        desc = sptr.value or ""
    except Exception:
        pass
    _desc_cache[path] = desc
    return desc


def stem_name(name):
    """«с мамой» → «мам», «шахтеров» → «шахтер»: поиск Telegram ищет по началу слова, а падежные
    окончания из речи мешают найти «Мама» или «Шахтёры»."""
    out = []
    for w in name.split():
        if len(w) >= 5:
            s = re.sub(r"(ами|ями|ого|его|ому|ему|ов|ев|ей|ой|ом|ем|ым|им|ою|ею|ах|ях|ам|ям|ую|юю|ая|яя|ые|ие|"
                       r"ы|и|а|я|у|ю|е)$", "", w)
            w = s if len(s) >= 3 else w
        out.append(w)
    return " ".join(out)


def youtube_search_in_tab(text):
    """Поиск на YouTube в текущей вкладке через адресную строку (Ctrl+L): срабатывает, даже если страница
    ещё грузится и поле поиска не в фокусе."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    before = automation.window_title(hwnd)
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(text)
    for attempt in range(2):
        # адрес вводится, только если адресная строка действительно получила фокус (иначе буквы ушли бы
        # в страницу YouTube, где это горячие клавиши)
        if not media.navigate(hwnd, url):
            time.sleep(0.6)
            continue
        for _ in range(10):  # ждём, что вкладка ушла на страницу результатов
            time.sleep(0.4)
            title = automation.window_title(hwnd)
            if title != before and "youtube" in title.lower() and title.lower().startswith(text.lower()[:4]):
                return True
        log.info("Поиск на YouTube не сработал с %d-й попытки, заголовок: %s", attempt + 1, title)
        if not automation.is_foreground(hwnd):
            break  # окно потеряло фокус — второй раз вслепую не печатаем
    return False


def wiki_summary(query, sentences=2):
    """Первые предложения статьи русской Википедии по запросу, подготовленные для озвучки. None — не нашлось."""
    headers = {"User-Agent": "CapriAssistant/1.0 (personal voice assistant)"}

    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        found = get("https://ru.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=1&srsearch="
                    + urllib.parse.quote(query))["query"]["search"]
        if not found:
            return None
        page = get("https://ru.wikipedia.org/api/rest_v1/page/summary/"
                   + urllib.parse.quote(found[0]["title"].replace(" ", "_")))
    except Exception as e:
        log.info("Википедия недоступна: %s", e)
        return None
    if page.get("type") == "disambiguation":
        return None
    text = page.get("extract", "").replace("́", "")  # знаки ударения
    text = re.sub(r"\s*\([^()]*\)", "", text)       # транскрипции, даты и пр. в скобках — не для озвучки
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text.strip())
    out = " ".join(parts[:sentences]).strip()
    if len(out) > 300:
        out = out[:300].rsplit(" ", 1)[0] + "…"
    return out or None


# ---------------- папки ----------------

SHELL_FOLDERS = {  # имена значений в реестре User Shell Folders (учитывают перенос в OneDrive)
    "desktop": "Desktop", "documents": "Personal", "pictures": "My Pictures", "music": "My Music",
    "videos": "My Video", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}
KNOWN_FOLDERS = [  # (регулярка по названию, ключ папки или shell:-путь, как назвать вслух)
    (r"фото\w*|фотк\w*|изображени\w*|картинк\w*|снимк\w*", "pictures", "Изображения"),
    (r"документ\w*", "documents", "Документы"),
    (r"загрузк\w*|скачанн\w*|закачк\w*", "downloads", "Загрузки"),
    (r"рабоч\w+ стол\w*", "desktop", "Рабочий стол"),
    (r"музык\w*", "music", "Музыка"),
    (r"видео\w*", "videos", "Видео"),
    (r"скриншот\w*", "screenshots", "Скриншоты"),
    (r"(этот|мой) компьютер|компьютер", "shell:MyComputerFolder", "Этот компьютер"),
    (r"корзин\w*", "shell:RecycleBinFolder", "Корзина"),
]
DRIVE_LETTERS = {"ц": "C", "си": "C", "с": "C", "д": "D", "ди": "D", "де": "D", "е": "E", "и": "E",
                 "ф": "F", "эф": "F", "джи": "G", "г": "G", "же": "G"}


def known_dir(key):
    if key == "screenshots":
        return os.path.join(known_dir("pictures"), "Screenshots")
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, SHELL_FOLDERS[key])[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), {"documents": "Documents", "pictures": "Pictures",
                                                      "music": "Music", "videos": "Videos",
                                                      "downloads": "Downloads"}.get(key, "Desktop"))


def find_folder(name, search=False):
    """(путь, название) по сказанному имени папки или None. search=True — искать и среди своих папок."""
    name = norm(name)
    drive = re.fullmatch(r"(диск|раздел) (?P<l>\S+)", name)
    if drive:
        letter = DRIVE_LETTERS.get(drive.group("l"), drive.group("l").upper())
        if len(letter) == 1 and os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\", f"диск {letter}"
    for pattern, key, label in KNOWN_FOLDERS:
        if re.fullmatch(pattern, name):
            path = key if key.startswith("shell:") else known_dir(key)
            if key.startswith("shell:") or os.path.isdir(path):
                return path, label
    if not search:
        return None
    return search_folder(name)


def search_folder(name, limit=40000):
    """Ищем папку по имени (с транслитом и нечётким сравнением) в домашних папках, до 4 уровней вглубь."""
    targets = {name, translit(name)}
    roots = [known_dir(k) for k in ("desktop", "documents", "downloads", "pictures", "videos", "music")]
    roots.append(os.path.expanduser("~"))
    best, best_score, seen = None, 0.0, 0
    for root in dict.fromkeys(roots):
        base_depth = root.rstrip("\\").count("\\")
        for cur, dirs, _ in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith((".", "$")) and d.lower() not in
                       ("appdata", "node_modules", "__pycache__", "site-packages", "windows")]
            if cur.count("\\") - base_depth >= 4:
                dirs[:] = []
            for d in dirs:
                seen += 1
                dn = norm(d)
                for t in targets:
                    score = 1.0 if dn == t else (0.9 if t in dn.split() or dn.startswith(t)
                                                 else difflib.SequenceMatcher(None, t, dn).ratio())
                    if score > best_score:
                        best, best_score = (os.path.join(cur, d), d), score
            if best_score == 1.0 or seen > limit:
                break
        if best_score == 1.0:
            break
    return best if best_score >= 0.8 else None


# ---------------- клавиши ----------------

VK = {"win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "tab": 0x09, "enter": 0x0D, "esc": 0x1B,
      "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      "printscreen": 0x2C, "capslock": 0x14, "menu": 0x5D}
EXTENDED = {"win", "delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down", "menu"}
KEY_WORDS = {  # как клавиши произносят (Vosk пишет кириллицей, Google — часто латиницей)
    "win": "вин винд винда виндовс виндоус виндоуз виндос виндус виндо windows window win wind винды "
           "виндоуса виндовса",
    "ctrl": "контрол контроль контрл кнтрл ктрл контр ctrl control ctl",
    "alt": "альт алт alt", "shift": "шифт shift", "tab": "таб табуляция tab",
    "enter": "энтер ентер enter ввод return", "esc": "эскейп искейп эскейф эск esc escape",
    "space": "пробел space спейс", "backspace": "бэкспейс бекспейс backspace",
    "delete": "делит дилит делейт delete del дел", "insert": "инсерт insert", "home": "хоум home",
    "end": "энд end", "printscreen": "принтскрин printscreen prtsc", "capslock": "капслок capslock",
    "left": "влево лево налево left", "right": "вправо право направо right",
    "up": "вверх наверх up", "down": "вниз down", "menu": "меню",
}
WORD_TO_KEY = {w: k for k, ws in KEY_WORDS.items() for w in ws.split()}
LETTER_NAMES = {"эй": "a", "би": "b", "си": "c", "ди": "d", "и": "e", "эф": "f", "джи": "g", "эйч": "h",
                "ай": "i", "джей": "j", "кей": "k", "эл": "l", "эм": "m", "эн": "n", "оу": "o", "пи": "p",
                "кью": "q", "ар": "r", "эс": "s", "ти": "t", "ю": "u", "ви": "v", "дабл ю": "w",
                "дабл-ю": "w", "экс": "x", "икс": "x", "уай": "y", "игрек": "y", "зед": "z", "зет": "z",
                # по-русски: «контрол цэ», «вин дэ», и одиночные буквы «вин д»
                "а": "a", "бэ": "b", "б": "b", "цэ": "c", "ц": "c", "дэ": "d", "д": "d", "е": "e",
                "гэ": "g", "г": "g", "ха": "h", "ка": "k", "к": "k", "эль": "l", "л": "l", "м": "m",
                "н": "n", "о": "o", "пэ": "p", "п": "p", "эр": "r", "р": "r", "с": "s", "тэ": "t", "т": "t",
                "у": "u", "вэ": "v", "в": "v", "зэ": "z", "з": "z"}
SKIP_WORDS_KEYS = {"и", "плюс", "кнопку", "кнопка", "кнопки", "клавишу", "клавиши", "клавиша", "клавиш",
                   "сочетание", "сочетания", "комбинацию", "комбинация", "стрелку", "стрелка", "стрелочку",
                   "на", "клавиатуре", "одновременно", "мне", "пожалуйста", "plus", "and"}


def split_glued(word):
    """Google иногда склеивает сочетание в одно слово: «wintab», «альттаб», «ctrlshiftesc».
    Режем на известные названия клавиш (одиночная буква/цифра допустима только последней)."""
    names = sorted((w for w in WORD_TO_KEY if len(w) > 1), key=len, reverse=True)

    def go(rest):
        if not rest:
            return []
        for n in names:
            if rest.startswith(n):
                tail = go(rest[len(n):])
                if tail is not None:
                    return [WORD_TO_KEY[n]] + tail
        f = re.match(r"[fф](1[0-2]|[1-9])", rest)
        if f:
            tail = go(rest[f.end():])
            if tail is not None:
                return ["f" + f.group(1)] + tail
        if len(rest) == 1 and re.fullmatch(r"[a-z0-9]", rest):
            return [rest]
        return None
    return go(word)


def parse_keys(text):
    """«вин таб» / «ctrl shift esc» / «эф пять» → ['win', 'tab'] ... Возвращает (клавиши, нераспознанное)."""
    t = re.sub(r"[+\-_,.]", " ", norm(text))
    for a, b in (("пейдж ап", "pageup"), ("page up", "pageup"), ("пейдж даун", "pagedown"),
                 ("page down", "pagedown"), ("принт скрин", "printscreen"), ("print screen", "printscreen"),
                 ("капс лок", "capslock"), ("caps lock", "capslock"), ("дабл ю", "дабл-ю"),
                 ("бэк спейс", "backspace"), ("back space", "backspace")):
        t = t.replace(a, b)
    words = t.split()
    keys, i = [], 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if w in WORD_TO_KEY or w in VK:
            keys.append(WORD_TO_KEY.get(w, w))
        elif re.fullmatch(r"[fф]\d{1,2}", w):
            keys.append("f" + w[1:])
        elif w in ("эф", "ф", "f") and (nxt.isdigit() or nxt in NUM):
            keys.append(f"f{int(nxt) if nxt.isdigit() else NUM[nxt]}")
            i += 1
        elif w in LETTER_NAMES and not (w == "и" and keys and i + 1 < len(words)):
            keys.append(LETTER_NAMES[w])
        elif re.fullmatch(r"[a-z0-9]", w):
            keys.append(w)
        elif w in NUM and NUM[w] < 10:
            keys.append(str(NUM[w]))
        elif w not in SKIP_WORDS_KEYS:
            glued = split_glued(w)
            if not glued:
                return [], w
            keys += glued
        i += 1
    return keys, None


def send_keys(keys):
    """Зажимает клавиши по порядку и отпускает в обратном — так работают сочетания вроде Win+Tab."""
    u = ctypes.windll.user32
    codes = []
    for k in keys:
        if k in VK:
            code = VK[k]
        elif re.fullmatch(r"f\d{1,2}", k) and 1 <= int(k[1:]) <= 12:
            code = 0x6F + int(k[1:])
        else:
            code = ord(k.upper())
        codes.append((code, 1 if k in EXTENDED else 0))
    for code, ext in codes:
        u.keybd_event(code, 0, ext, 0)
        time.sleep(0.02)
    for code, ext in reversed(codes):
        u.keybd_event(code, 0, ext | 2, 0)  # 2 = KEYEVENTF_KEYUP
        time.sleep(0.02)


# ---------------- обработчик команд ----------------

REMEMBER_RE = (r"^(запомни|сохрани|добавь)( мне| себе)? (?P<what>(этот |эту |это |текущ\w+ |открыт\w+ )?"
               r"(сайт\w*|страниц\w*|вкладк\w*|ссылк\w*|программ\w*|приложени\w*|игр\w*))\s*(?P<name>.*)$")
FORGET_RE = (r"^(забудь|удали|убери)( мой| мою| из памяти)? (?P<what>сайт\w*|страниц\w*|ссылк\w*|программ\w*|"
             r"приложени\w*|игр\w*) (?P<name>.+)$")


CAPRI_URL = "https://github.com/kystt29/capricorn"
# «есть обновления?», «где скачать новую версию», «твой гитхаб» — страница Капри (не «обнови страницу»)
UPDATES_RE = (r"^(есть |проверь |проверить |какие |когда |а )?(новые )?обновлени\w*( есть| вышли| для тебя| капри)?$|"
              r"(есть|проверь|проверить|вышл\w*|появил\w*|скачать|где|какие|нет ли)\b.*\bобновлени|"
              r"(что|как)( там| же| у тебя| у нас)? (по|с) обновлени|"
              r"обнови (себя|капри)|обновись|"
              # «новая версия вышла?» — да, «новая версия windows» — нет: только без уточнения или про Капри
              r"^(есть |вышла |появилась |где )?(нов\w*|последн\w*) верси\w*( капри| тебя)?( вышла| есть| появилась)?$|"
              r"(скачать|где) (\S+ )?(нов\w*|последн\w*) верси\w*( капри| тебя)?$|"
              r"(твой|свой|твою|свою) (гитхаб|страниц\w*|репозитори\w*)|гитхаб капри|где тебя (скачать|взять)")


# «когда я скажу X, отвечай Y», «на фразу X отвечай Y» — свой ответ; «забудь ответ на X»
REPLY_RE = (r"^((когда|если) (я )?(говорю|скажу|произнесу|спрошу)|на (фразу|слова|вопрос))\s+(?P<p>.+?)\s*,?\s+"
            r"(ты )?(отвечай|ответь|говори|скажи)( мне)?\s+(?P<r>.+)$")
FORGET_REPLY_RE = r"^(забудь|удали|убери) (свой |мой |этот )?ответ (на|для) (фразу |слова |вопрос )?(?P<p>.+)$"


class Actions:
    def __init__(self, cfg, base_dir, ai, save=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.ai = ai
        self.save = save  # save(раздел, название, значение или None) → текст ошибки или None
        self.browser_once = None  # браузер, названный в текущей команде («открой ютуб в хроме»)
        self.set_lists(cfg)
        self.index = AppIndex()
        threading.Thread(target=self.index.build, daemon=True).start()
        self.pending = None
        self.choice = None  # варианты приложений, из которых пользователь сейчас выбирает
        self.youtube_opened_at = 0.0  # когда Капри последний раз открывала YouTube
        self.media_paused = None      # какой плеер Капри поставила на паузу
        self.local = None
        self.commentator = None   # наблюдатель commentary.Commentator: его ставит main
        self.games = games.GameBook(os.path.join(base_dir, "games.json"), norm, translit)
        self.game_announce = set()   # игры, о которых Капри скажет, когда их закроют
        self.game_spoke_at = 0.0
        persona.CONTEXT_PROVIDERS.append(self.games.summary)
        persona.TOPIC_PROVIDERS.append(self.games.chatter_topics)
        self.routes = [
            (r"^(выход|выключись|отключись|заверши работу|завершить работу)$", self.quit),
            (freestyle.ANYTIME_RE, self.anytime_toggle),    # раньше «комментируй» / «не комментируй»
            (freestyle.PROFANITY_RE, self.profanity_toggle),
            (freestyle.WALLPAPER_RE, lambda c, m: self.look_cmd("wallpaper")),
            (freestyle.SCREEN_RE, lambda c, m: self.look_cmd("screen")),
            (commentary.TOGGLE_RE, self.commentary_toggle),
            (learning.LIKE_RE, self.feedback_like),        # «ха-ха», «в точку» — сразу после реплики Капри
            (learning.DISLIKE_RE, self.feedback_dislike),  # «не смешно», «перебор»
            (commentary.WIN_RE, self.win_cmd),
            (commentary.LOSS_RE, self.loss_cmd),
            (steam.SEND_RE, self.steam_message),  # до обычной печати «напиши …»
            # --- игры: раньше «запомни …», иначе игра запомнилась бы как программа ---
            (games.TEACH_RE, self.game_teach),
            (games.FORGET_RE, self.game_forget),
            (games.NOTE_RE, self.game_note),
            (games.INFO_RE, self.game_info),
            (games.TIME_RE, self.game_time),
            (games.LIST_RE, self.game_list),
            (r"^(спрячься|скройся|исчезни|пока|отбой|свободна)\b|"
             r"^(уйди|уходи|убирайся|свали|сгинь|испарись|скрой себя|убери себя)\b|"
             r"(уйди|уходи|убирайся|свали|пропади|исчезни) (с|из) (экрана|монитора|глаз)", self.hide),
            (REMEMBER_RE, self.remember_cmd),
            (FORGET_RE, self.forget_cmd),
            (UPDATES_RE, self.updates),
            (REPLY_RE, self.reply_cmd),
            (FORGET_REPLY_RE, self.forget_reply_cmd),
            (r"помаши|помахай|махни|помаши ручкой|поздоровайся", self.wave),
            (r"погуляй|прогуляйся|пройдись|прогулк", self.roam),
            (r"что ты (умеешь|можешь)|как\w* (у тебя )?команд|(назови|перечисли|скажи) (\S+ )?команд|"
             r"список команд|помощь$", self.help),
            (r"(перемест|подвин|сдвин|переди|встань|иди|отойди|переберись)\w*\s.*(угол|влево|вправо|лев|прав|"
             r"верх|низ|центр|середин|другую сторону)", self.move),
            (r"(выключи|перезагрузи|перезапусти) (компьютер|пк|комп|систему)", self.power),
            (r"заблокируй (компьютер|пк|комп|экран)", self.lock),
            (r"громкост|громче|тише|(вкл|выкл|отключ|убер|верни|включ)\w* звук|звук (на|в) |без звука|замуть", self.volume),
            (r"яркост|ярче|темнее", self.brightness),
            (r"(проанализ|анализ|диагност|провер|просканир|сканир)\w*.*(пк|компьютер|комп|систем|ошибк)", self.analyze),
            # --- микрофон (выполняет main: там живёт слушатель) ---
            (r"(какой|какое) (сейчас )?(микрофон|устройство ввода)|что за микрофон|(через )?какой микрофон",
             lambda c, m: Reply("", "mic_info")),
            (r"(смени|поменяй|переключи|следующий|другой) (\S+ )?(микрофон|устройство ввода)",
             lambda c, m: Reply("", "mic_next")),
            # --- прокрутка страниц ---
            (r"\b(прокрут|пролист|листай|листани|полистай|скролл|скроль|спустись|поднимись|крути (вниз|вверх))\w*|"
             r"^(в |на |до )?(самый |самую |самого )?(низ|верх|конец страницы|начало страницы|низа|верха)$|"
             r"^(еще |ещё )?(ниже|выше)$", self.scroll),
            # --- мультимедиа: что сейчас играет (музыка, YouTube, плееры) ---
            (r"^(поставь |поставить )?(на )?пауз\w*( музык\w*| видео| трек| песн\w*| ролик)?$|"
             r"^(останови|приостанови|тормозни|стопни)( музык\w*| видео| трек| песн\w*| воспроизведение| ролик)?$|"
             r"^(выключи|стоп) (музык\w*|видео|трек|песн\w*|ролик)$", self.media_pause),
            (r"^(продолжи|продолжай|играй|воспроизведи|сними( с)? пауз\w*|сними)( музык\w*| видео| трек| песн\w*|"
             r" воспроизведение| ролик)?$|^(включи|запусти) (дальше|обратно|снова)$|"
             r"^(включи|запусти) (музык\w*|видео|трек|песн\w*) (обратно|снова|дальше)$", self.media_play),
            (r"^(следующ\w*|дальше|переключи|скипни|пропусти)( трек| песн\w*| видео| композици\w*| ролик)?$|"
             r"^(включи|давай) следующ\w*( трек| песн\w*| видео| ролик)?$", self.media_next),
            (r"^(предыдущ\w*|прошл\w*)( трек| песн\w*| видео| композици\w*| ролик)?$|"
             r"^(включи|верни) (предыдущ|прошл)\w*( трек| песн\w*| видео| ролик)?$", self.media_prev),
            (r"что (сейчас )?(играет|звучит|за (песн|трек|музык|видео)\w*)", self.media_info),
            (r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи)( видео| музык\w*| трек| песн\w*| ролик)? "
             r"(на|с|со) "
             r"(\d|полминут|полтор|начал|сначала|момент|отметк|тайминг|(\S+ ){0,2}(секунд|минут|час)|"
             r"(\S+ )?(ноль|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|\S+дцать|сорок|"
             r"пятьдесят))", self.media_seek),
            (r"^(перемотай|перемотать|промотай|мотни|отмотай|прокрути|верни\w*|вернись|пропусти|скипни|"
             r"перемотка|поставь|включи)( видео| музык\w*| трек| песн\w*| ролик)?( на| в| к| с)?"
             r"( тайминг\w*| отметк\w*| момент\w*| время| позици\w*)?\s*"
             r"(\d|полминут|полтор|начал|сначала|заново|вперед|назад|(\S+ ){0,2}(секунд|минут|час))", self.media_seek),
            (r"^(включи|начни|поставь|запусти|давай|перемотай|верни|играй)( видео| музык\w*| трек| песн\w*| ролик)?"
             r"( с| на| в)?( самого)? ?(сначала|начало|начала|заново|снова)$", self.media_seek),
            (r"^(нажми на поиск|нажми поиск|перейди в поиск|в поиск|поиск|поставь курсор в поиск)$",
             self.search_field),
            (r"^(нажми|нажать|зажми|жми|набери сочетание) (?P<k>.+)$", self.press),
            (rf"^({automation.TYPE_VERBS})\s+\S", self.type_cmd),
            (r"^(открой |зайди в |перейди в |выбери |найди )?(в )?(телеграм\w*|telegram|телег\w*|тг )?\s*"
             r"((?P<ord>{}) )?(чат\w*|chat|диалог\w*|переписк\w*|контакт\w*|беседу|беседа|группу|канал)"
             r"( с| со)?( (?P<name>.+))?$".format("|".join(automation.ORDINALS)), self.chat),
            # «открой телеграм папа» — запустить Telegram и сразу открыть чат с этим именем
            (r"^(открой|зайди в|перейди в|напиши в|запусти) (телеграм\w*|telegram|телег\w*|тг) (?P<name>(?!и |"
             r"напиши|первый|второй|третий).+)$", self.chat),
            (r"сверни (все|всё)|покажи рабочий стол", self.minimize_all),
            (r"\bсверни\b", self.minimize),
            (r"(закрой|закрыть|убери) (эту |текущую |\S+ )?вкладк", self.tab),
            (r"нов(ую|ая) вкладк|\b(верни|восстанови) (закрыт\w* )?вкладк|(следующ|предыдущ)\w* вкладк", self.tab),
            (r"^(создай|сделай|создать) (?P<what>.*(папк|файл|документ|таблиц).*)$", self.create),
            (r"^(найди|покажи|открой|загугли|погугли|поищи)( мне)?( в интернете| в гугле)? (фото\w*|фотк\w*|"
             r"картинк\w*|изображени\w*|пикч\w*|обои)( (с|со|про|о|об|на тему))? (?P<q>.+)$", self.images),
            (r"^(найди|поищи|загугли|погугли|расскажи|узнай|посмотри)( мне)?( в интернете| в гугле| в сети)? "
             r"(информаци\w*|инфу|инфо|что-нибудь|что нибудь|всё|все|сведения|данные|статью)( (о|об|про|по))? "
             r"(?P<q>.+)$", self.info),
            (r"^(открой|включи|покажи|найди|запусти|поставь|врубай|вруби)( мне)?( какое-нибудь| какой-нибудь| "
             r"какое нибудь| любое| любой| одно| одно)? (?P<kind>видео\w*|видос\w*|"
             r"ролик\w*|клип\w*|песн\w*|трек\w*|музык\w*)( на ютуб\w*| в ютуб\w*)?( (с|со|про|о|об|где|на тему))? "
             r"(?P<q>.+)$", self.video),
            (r"^(открой|включи|покажи|запусти|поставь)( мне)? (на|в) (ютуб\w*|youtube) (?P<q>.+)$", self.video),
            (r"^(найди|поищи|загугли|погугли|ищи)( в интернете| в гугле| в google)? (?P<q>.+)$", self.search),
            (games.LAUNCH_RE, self.game_launch),  # знакомая игра; иначе None, и команда идёт дальше
            (r"^(открой|запусти|включи|зайди на|перейди на|покажи) (?P<t>.+)$", self.open),
            (r"^((сними|снять) (задачу|процесс\w*)|(заверши|завершить|убей|убить|останови|закрой) "
             r"(все )?(процесс\w*|задачу|задачи)|кильни|килльни|грохни|прибей) (?P<t>.+)$", self.kill_process),
            (r"что (сейчас )?(грузит|нагружает|жрет|ест|тормозит)|(какие|какой) процесс\w* (грузят|нагружают|жрут|"
             r"тормозят)|самые (тяжелые|прожорливые) процессы", self.top_processes),
            (r"^(закрой|заверши|выключи|убей) (?P<t>.+)$", self.close),
            (r"который час|сколько времени|сколько сейчас времени|время сейчас", self.time),
            (r"какое (сегодня )?число|какая (сегодня )?дата|какой сегодня день", self.date),
            (r"скриншот|скрин экрана|сделай скрин", self.screenshot),
        ]

    def handle(self, cmd):
        c = norm(cmd)
        cancel_ensure()  # новая команда — старая проверка «заиграло ли видео» больше не нужна
        if self.pending:
            action, self.pending = self.pending, None
            if re.match(r"^(да|давай|подтверждаю|конечно|ага)\b", c):
                return action()
            if re.match(r"^(нет|не надо|отмена|стоп)\b", c):
                return Reply(line("cancel"))
        if self.stop_background():  # любая новая команда останавливает автопрокрутку
            if re.match(r"^(стоп|хватит|стой|остановись|достаточно|перестань|харэ|все|всё)\b", c):
                return Reply(line("scroll_stop"))
        if self.choice:  # ждём ответ на «какое открыть / закрыть?»
            options, self.choice = self.choice, None
            pick = self.resolve_choice(c, options)
            if pick is not None:
                name, act = options[pick]
                if callable(act):
                    return act()
                started = self.index.launch_best(name, [(1.0, name, act)])
                return Reply(line("launch", t=started) if started else line("app_missing", t=name))
            if re.match(r"^(никак\w*|ни одно|ни то|отмена|не надо|нет|ничего)\b", c):
                return Reply(line("cancel"))
            # ответ не про выбор — значит, это новая команда
        custom = self.custom_reply(c)  # своя фраза целиком — не делим её на цепочку команд
        if custom:
            return custom
        # «запомни … как X и Y» — название целиком, не цепочка команд
        whole = any(re.match(p, c) for p in (REMEMBER_RE, FORGET_RE, REPLY_RE, FORGET_REPLY_RE, *games.WHOLE,
                                             steam.SEND_RE))
        steps = None if whole else automation.split_chain(c)
        if steps:
            try:
                return self.run_chain(steps)
            except Exception as e:
                log.exception("Цепочка %s", steps)
                return Reply(line("error", e=e))
        return self.handle_one(c)

    @staticmethod
    def resolve_choice(c, options):
        """«второе», «два», «клод», «первую» → номер варианта или None."""
        ords = [(r"перв\w*|один|одну|1", 0), (r"втор\w*|два|две|2", 1), (r"трет\w*|три|3", 2),
                (r"последн\w*", len(options) - 1)]
        for pattern, idx in ords:
            if re.search(rf"(^|\s)({pattern})(\s|$)", c) and idx < len(options):
                return idx
        words = re.sub(r"^(открой|запусти|давай|вот|это|ну)\s+", "", c)
        best, best_i = 0.0, None
        for i, (name, _) in enumerate(options):
            for v in {words, translit(words)}:
                s = 1.0 if v and v in name else difflib.SequenceMatcher(None, v, name).ratio()
                if AppIndex.sounds_like(v, name.split()[0] if name.split() else name):
                    s = max(s, 0.86)
                if s > best:
                    best, best_i = s, i
        return best_i if best >= 0.6 else None

    def web(self, url):
        """Открыть адрес: в браузере из команды («… в хроме»), из настройки "browser" или в браузере
        по умолчанию Windows."""
        exe = find_browser(self.browser_once) or find_browser(self.cfg.get("browser"))
        if exe:
            subprocess.Popen([exe, url])
        else:
            webbrowser.open(url)

    def handle_one(self, c):
        m = BROWSER_SUFFIX.search(c)
        self.browser_once, missing = None, None
        if m:  # «открой ютуб в хроме» — браузер только для этой команды
            if find_browser(m.group("b")):
                self.browser_once = m.group("b")
            else:
                missing = next((BROWSER_NAMES[k] for p, k in BROWSER_WORDS if re.fullmatch(p, m.group("b"))),
                               m.group("b"))
            c = c[:m.start()].strip()
        try:
            r = self._handle_one(c)
        finally:
            self.browser_once = None
        if r and missing:
            r.text = line("browser_missing", b=missing) + " " + r.text
        return r

    def _handle_one(self, c):
        r = self.custom_reply(c)  # свои ответы — раньше команд и нейросети
        if r:
            return r
        for pattern, fn in self.routes:
            m = re.search(pattern, c)
            if m:
                try:
                    r = fn(c, m)
                except Exception as e:
                    log.exception("Команда %s", c)
                    return Reply(line("error", e=e))
                if r:
                    return r
        return None

    # --- свои сайты и программы ---
    def set_lists(self, cfg):
        """Сайты, программы и названия из настроек — при запуске и когда config.json поменяли."""
        self.sites = {norm(k): v for k, v in cfg.get("sites", {}).items()}
        self.apps = {norm(k): v for k, v in cfg.get("apps", {}).items()}
        self.aliases = {norm(k): v for k, v in cfg.get("app_aliases", {}).items()}
        self.replies = {norm(k): v for k, v in cfg.get("replies", {}).items() if v}

    def custom_reply(self, c):
        """Свой ответ на фразу из config.json → "replies" (точно или почти дословно) или None."""
        if not self.replies:
            return None
        if c in self.replies:
            return Reply(self.replies[c])
        close = difflib.get_close_matches(c, list(self.replies), n=1, cutoff=0.85)
        if close:
            return Reply(self.replies[close[0]])
        # фраза целиком внутри команды и лишнего немного: «ну как дела» → «как дела»
        for phrase in sorted(self.replies, key=len, reverse=True):
            if len(c) - len(phrase) <= 12 and re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", c):
                return Reply(self.replies[phrase])
        return None

    def remember(self, section, name, target):
        """Записать сайт / программу / свой ответ (section "sites" / "apps" / "replies") в config.json
        и сразу начать её узнавать. target=None — забыть. Возвращает текст ошибки или None."""
        name = norm(name)
        err = self.save(section, name, target) if self.save else None
        if err:
            return err
        book = {"sites": self.sites, "apps": self.apps, "replies": self.replies}[section]
        stored = self.cfg.setdefault(section, {})
        if target is None:
            book.pop(name, None)
            stored.pop(name, None)
        else:
            book[name] = target
            stored[name] = target
        return None

    def remember_cmd(self, c, m):
        """«запомни этот сайт как музыка» — адрес открытой вкладки; «запомни эту программу как запрет» —
        программа, окно которой сейчас на экране."""
        name = re.sub(r"^(как|под названием|под именем|с названием|назови)\s+", "", (m.group("name") or "").strip())
        name = name.strip(" «»\"")
        site = re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what"))
        if not name:
            return Reply(line("remember_noname", what="этот сайт" if site else "эту программу"))
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if site:
            url = media.page_url(hwnd)
            if not url:
                return Reply(line("remember_nosite"))
            err = self.remember("sites", name, url)
            short = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
            return Reply(line("remember_fail", e=err) if err else line("remember_site", t=name, url=short))
        exe = automation.window_exe(hwnd)
        base = os.path.splitext(os.path.basename(exe or ""))[0].lower()
        if not exe or base in PROTECTED_PROCS or base == "applicationframehost":
            return Reply(line("remember_noapp"))
        err = self.remember("apps", name, exe)
        return Reply(line("remember_fail", e=err) if err else line("remember_app", t=name, app=base))

    def forget_cmd(self, c, m):
        name = norm(m.group("name"))
        section = "sites" if re.search(r"(сайт|страниц|вкладк|ссылк)", m.group("what")) else "apps"
        book = self.sites if section == "sites" else self.apps
        if name not in book:
            return Reply(line("forget_missing", t=name))
        err = self.remember(section, name, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=name))

    # --- сообщения друзьям в Steam (поиск друга и отправка — в steam.py) ---
    def steam_message(self, c, m):
        """«напиши Васе в стиме, что я опоздаю» — находит друга, читает текст вслух и после «да» отправляет."""
        friends = steam.load_friends(self.cfg.get("steam_path", ""))
        aliases = self.cfg.get("steam_friends") or {}

        def find(query):
            return steam.find_friend(query, friends, aliases, norm, translit)
        if not friends and not aliases:
            return Reply(line("steam_no_friends"))
        parsed = steam.parse(c, find)
        if parsed is None:
            return None
        kind, who, text = parsed
        if kind == "no_friend":
            return Reply(line("steam_no_friend", t=who or "без имени"))
        _, steam64, label = who
        if kind == "no_text":
            return Reply(line("steam_no_text", who=label))
        if not steam.is_running():
            return Reply(line("steam_not_running"))

        def do():
            if not steam.send_message(steam64, label, text):
                return Reply(line("steam_chat_fail", who=label))
            return Reply(line("steam_sent", who=label, t=text))
        if self.cfg.get("steam_confirm", True):
            self.pending = do  # дальше handle() ждёт «да»
            return Reply(line("steam_ask", who=label, t=text))
        return do()

    # --- подколки (наблюдатель — в commentary.py) ---
    def commentary_toggle(self, c, m):
        """«не комментируй» / «комментируй меня»: включает и выключает подколки (само переключение делает main)."""
        off = commentary.toggle_wants_off(c)
        return Reply(line("commentary_off" if off else "commentary_on"), "commentary:" + ("off" if off else "on"))

    def anytime_toggle(self, c, m):
        """«комментируй всё» / «выключи свободный режим» (само переключение делает main)."""
        off = bool(re.search(freestyle.ANYTIME_OFF, c))
        return Reply(line("anytime_off" if off else "anytime_on"), "anytime:" + ("off" if off else "on"))

    def profanity_toggle(self, c, m):
        """«ругайся» / «не матерись» (само переключение делает main)."""
        off = bool(re.search(freestyle.PROFANITY_OFF, c))
        return Reply(line("profanity_off" if off else "profanity_on"), "profanity:" + ("off" if off else "on"))

    def look_cmd(self, kind):
        """«прокомментируй обои» / «прокомментируй экран»: посмотреть (локальная модель с «глазами») и сказать своё.
        По просьбе — всегда, независимо от настроек комментариев."""
        if not self.local:
            return Reply(line("look_no_model"))
        try:
            what = freestyle.describe(self.local, kind)
        except freestyle.NoVision:
            return Reply(line("look_no_model"))
        except Exception:
            log.exception("Не смогла посмотреть (%s)", kind)
            return Reply(line("look_fail"))
        if not what:
            return Reply(line("look_fail"))
        detail = {"what": what}
        if self.commentator:
            return Reply(self.commentator.render(kind, detail, self.local.quip, line))
        return Reply(commentary.compose(kind, detail, self.local.quip, line, rude=bool(self.cfg.get("profanity"))))

    def _outcome_cmd(self, kind):
        """«я слил» / «ура, прошёл»: стёб, подбадривание или похвала — по тому, что заходит. Если подколки выключены,
        фраза идёт дальше, как обычный разговор."""
        if not self.cfg.get("commentary", True):
            return None
        key = self.games.current()
        g = self.games.get(key) if key else None
        detail = {"t": g["name"] if g else ""}
        quip = self.local.quip if self.local else None
        if self.commentator:
            found = self.commentator.outcome(kind, detail, explicit=True)  # сама позвала — отвечаем всегда
            if found:
                return Reply(self.commentator.render(found[0], found[1], quip, line))
            return None
        return Reply(commentary.compose(kind, detail, quip, line, rude=bool(self.cfg.get("profanity"))))

    def loss_cmd(self, c, m):
        return self._outcome_cmd("loss")

    def win_cmd(self, c, m):
        return self._outcome_cmd("win")

    def _feedback(self, positive):
        """Твоя реакция на только что сказанную реплику Капри. Без реплики это обычный разговор, и фраза идёт дальше."""
        learn = self.commentator.learn if self.commentator else None
        if not learn or not learn.feedback(positive):
            return None
        return Reply(line("feedback_like" if positive else "feedback_dislike"))

    def feedback_like(self, c, m):
        return self._feedback(True)

    def feedback_dislike(self, c, m):
        return self._feedback(False)

    # --- игры пользователя (учёт и память — в games.py) ---
    def _learn_about(self, key):
        """Фоном узнаёт об игре из Википедии, чтобы потом рассказать о ней. Чужую статью не берём."""
        g = self.games.get(key)
        if not g or g.get("about"):
            return
        text = wiki_summary(g["name"] + " игра", 2)
        if text and games.about_matches(g["name"], text):
            self.games.set_about(key, text)

    def on_game_event(self, kind, key, minutes):
        """Событие трекера → что сказать вслух (или None). Говорим только когда игру закрыли."""
        g = self.games.get(key)
        if not g:
            return None
        if kind == "new":
            self.game_announce.add(key)
            threading.Thread(target=self._learn_about, args=(key,), daemon=True).start()
            return None
        if kind != "end":
            return None
        first = key in self.game_announce
        self.game_announce.discard(key)
        now = time.time()
        if first:
            text = line("game_new", t=g["name"], d=games.fmt_minutes(max(minutes, 1)))
        elif minutes >= 30 and now - self.game_spoke_at > 3 * 3600:
            text = line("game_end", t=g["name"], d=games.fmt_minutes(minutes))
        else:
            return None
        self.game_spoke_at = now
        return text

    def game_teach(self, c, m):
        """«запомни эту игру как терария» — игра, окно которой сейчас на экране."""
        name = re.sub(r"^(как|под названием|под именем|с названием|назови)\s+", "", (m.group("name") or "").strip())
        name = name.strip(" «»\"")
        if not name:
            return Reply(line("game_noname"))
        exe = automation.window_exe(ctypes.windll.user32.GetForegroundWindow())
        base = os.path.splitext(os.path.basename(exe or ""))[0].lower()
        if not exe or base in PROTECTED_PROCS or base in games.GENERIC_HOSTS:
            return Reply(line("game_no_window"))
        key, _ = self.games.teach(name, exe)
        threading.Thread(target=self._learn_about, args=(key,), daemon=True).start()
        return Reply(line("game_taught", t=name))

    def game_forget(self, c, m):
        name = m.group("name").strip()
        key = self.games.find(name)
        if key is None:
            return None  # не игра из списка — пусть решает обычное «забудь программу»
        return Reply(line("game_forgot", t=self.games.forget(name)))

    def game_note(self, c, m):
        """«запиши про игру: на третьем боссе нужен лук» — заметка к игре, которая запущена или была последней."""
        key = self.games.current()
        if key is None:
            return Reply(line("game_note_none"))
        note = m.group("note").strip()
        self.games.add_note(key, note)
        return Reply(line("game_note", t=self.games.get(key)["name"], n=note[:80]))

    def game_list(self, c, m):
        top = self.games.top(3)
        if not top:
            return Reply(line("game_none"))
        parts = [line("game_top", t=top[0][1]["name"], d=games.fmt_minutes(top[0][1]["minutes"]))]
        if len(top) > 1:
            parts.append(line("game_others", t=", ".join(f"«{g['name']}»" for _, g in top[1:])))
        if self.games.running():
            parts.append(line("game_running", t=", ".join(f"«{n}»" for n in self.games.running())))
        return Reply(" ".join(parts))

    def game_time(self, c, m):
        key = self.games.find(m.group("name"))
        if key is None:
            return None  # «сколько я играл сегодня» — не про игру из списка
        g = self.games.get(key)
        text = line("game_time", t=g["name"], d=games.fmt_minutes(g["minutes"]), n=g["sessions"])
        if g["last_played"] and key not in self.games.active:
            text += " " + line("game_last", w=games.ago(g["last_played"]))
        return Reply(text)

    def game_info(self, c, m):
        """«расскажи про игру террария» — что Капри о ней узнала и что ты сам записал."""
        name = m.group("name").strip()
        key = self.games.find(name)
        g = self.games.get(key) if key else None
        about = g.get("about") if g else None
        if not about:
            about = wiki_summary(name + " игра", 2)
            if not (about and games.about_matches(name, about)):
                about = None
            elif key:
                self.games.set_about(key, about)
        if not about and not g:
            return None  # ничего не знаю — пусть ответит нейросеть
        parts = [about] if about else []
        if g:
            parts.append(line("game_time", t=g["name"], d=games.fmt_minutes(g["minutes"]), n=g["sessions"]))
            if g["notes"]:
                parts.append("Твоя заметка: " + g["notes"][-1]["text"] + ".")
        return Reply(line("game_info", t=" ".join(parts)))

    def game_launch(self, c, m):
        """«запусти террарию» — только для игр из списка и только если это не свой сайт или программа."""
        name = m.group("name").strip()
        n = norm(name)
        if n in self.sites or n in self.apps or n in self.aliases:
            return None
        key = self.games.find(name, min_score=0.8)
        if key is None:
            return None
        g = self.games.get(key)
        if key in self.games.active:
            return Reply(line("game_already", t=g["name"]))
        if not os.path.isfile(g["exe"]):
            return Reply(line("game_launch_fail", t=g["name"]))
        subprocess.Popen([g["exe"]], cwd=os.path.dirname(g["exe"]))
        return Reply(line("game_launch", t=g["name"]))

    def updates(self, c, m):
        """Страница Капри на GitHub — там новые версии."""
        self.web(CAPRI_URL)
        return Reply(line("updates"))

    def reply_cmd(self, c, m):
        """«Когда я скажу „как дела“, отвечай „лучше всех“» — свой ответ на фразу."""
        phrase = m.group("p").strip(" ,")
        text = m.group("r").strip(" ,")
        text = text[:1].upper() + text[1:]
        if not re.search(r"[.!?…]$", text):
            text += "."
        err = self.remember("replies", phrase, text)
        return Reply(line("remember_fail", e=err) if err else line("reply_saved", p=phrase, r=text))

    def forget_reply_cmd(self, c, m):
        phrase = norm(m.group("p"))
        close = phrase if phrase in self.replies else next(
            iter(difflib.get_close_matches(phrase, list(self.replies), n=1, cutoff=0.85)), None)
        if not close:
            return Reply(line("forget_missing", t=phrase))
        err = self.remember("replies", close, None)
        return Reply(line("remember_fail", e=err) if err else line("forget_ok", t=close))

    def fallback(self, cmd, force_search=False):
        """Без ИИ: вопрос ищем в Google (если search_when_unsure не выключен), остальное — «не знаю команды»."""
        c = norm(cmd)
        if force_search or re.match(r"^(что|кто|как|какой|какая|какое|какие|почему|зачем|сколько|где|когда|куда|"
                     r"откуда|чем|чей|расскажи|объясни|скажи)\b", c):
            if not self.cfg.get("search_when_unsure", True):
                return Reply(line("dont_know"))
            self.web("https://www.google.com/search?q=" + urllib.parse.quote(cmd))
            return Reply(line("search_q"))
        return Reply(line("unknown"))

    # --- команды ---
    def quit(self, c, m):
        return Reply(line("quit"), "quit")

    def hide(self, c, m):
        return Reply(line("hide"), "hide")

    def wave(self, c, m):
        return Reply(line("wave"), "wave")

    def roam(self, c, m):
        return Reply(line("roam"), "roam")

    # --- прокрутка ---
    def scroll(self, c, m):
        up = bool(re.search(r"\b(вверх|наверх|выше|верх\w*|начал\w*|поднимись|назад)\b", c))
        down = bool(re.search(r"\b(вниз|ниже|низ\w*|конец|дальше|спустись|вперед)\b", c))
        auto = re.search(r"\b(медленно|плавно|автоматически|сама|постепенно|прокручивай|листай сама)\b", c) \
            or re.fullmatch(r"(листай|полистай|прокручивай|скроль|скролль)( медленно| плавно)?", c)
        if not (up or down or auto) or re.search(r"секунд|минут|\bчас|\d[:.]\d\d|полминут", c):
            return None  # «прокрути на 30 секунд вперёд» — это перемотка видео, не страницы
        direction = -1 if up and not down else 1
        self.stop_background()
        if auto:
            fast = bool(re.search(r"быстр", c))
            self._autoscroll_stop = threading.Event()
            threading.Thread(target=self._autoscroll, args=(direction, 0.25 if fast else 0.6,
                                                             self._autoscroll_stop), daemon=True).start()
            return Reply(line("scroll_auto"))
        if re.search(r"\b(самый|самую|самого|до конца|в конец|в начало|до низа|до верха|конец страницы|"
                     r"начало страницы)\b|^(в |на |до )?(низ|верх|низа|верха)$", c):
            automation.scroll(200 * direction, delay=0.008)  # слишком быстро — щелчки колеса теряются
            return Reply(line("scroll_end" if direction > 0 else "scroll_top"))
        n = parse_number(c)
        screen = automation.screen_notches()
        if re.search(r"\b(немного|чуть|чуть-чуть|слегка|капельку)\b|^(еще )?(ниже|выше)$", c):
            notches = 3
        elif n and re.search(r"экран|страниц|раз", c):
            notches = min(n, 20) * screen
        else:
            notches = screen
        automation.scroll(notches * direction)
        return Reply(line("scroll"))

    def _autoscroll(self, direction, interval, stop):
        """Плавная прокрутка в окне, которое было активно в момент команды, — пока не скажут «стоп»."""
        hwnd = automation.target_window()
        end = time.time() + 300
        while not stop.is_set() and time.time() < end:
            if automation.target_window() != hwnd:
                break  # пользователь ушёл в другое окно — не крутим чужое
            automation.scroll(direction, hwnd=hwnd)
            stop.wait(interval)

    def autoscroll_active(self):
        ev = getattr(self, "_autoscroll_stop", None)
        return bool(ev and not ev.is_set())

    def stop_background(self):
        """Остановить фоновые действия (автопрокрутку). Вызывается и на «стоп» из main."""
        ev = getattr(self, "_autoscroll_stop", None)
        if ev and not ev.is_set():
            ev.set()
            return True
        return False

    # --- мультимедиа ---
    def _youtube_keys(self, keys_list):
        """Нажать горячие клавиши плеера YouTube в его окне (k — пауза, j/l — ±10 с, стрелки — ±5 с)."""
        hwnd = automation.find_window(("youtube",))
        if not hwnd or not automation.focus(hwnd):
            return False
        for keys in keys_list:
            send_keys(keys)
            time.sleep(0.12)
        return True

    def media_pause(self, c, m):
        if browser_playing() and automation.find_window(("youtube",)):
            # YouTube в браузере: системная пауза не всегда доходит — проверяем по звуку и жмём K
            media.control("pause")
            time.sleep(0.8)
            if browser_playing():
                self._youtube_keys([["k"]])
                time.sleep(0.8)
            if not browser_playing():
                self.media_paused = "youtube"
                return Reply(line("media_paused"))
        ok, desc, src = media.control("pause")
        if not ok:
            return Reply(line("media_none"))
        self.media_paused = src  # «продолжи» вернёт именно это, а не другой плеер
        return Reply(line("media_paused"))

    def media_play(self, c, m):
        if self.media_paused == "youtube" or (not self.media_paused and automation.find_window(("youtube",))
                                              and not media.now_playing()):
            def started():
                for _ in range(8):  # у трансляций звук появляется не сразу
                    time.sleep(0.5)
                    if browser_playing():
                        return True
                return False
            for b in BROWSERS:  # сначала системной командой — без переключения окон
                if media.control("play", source=f"{b}.exe")[0]:
                    break
            if started() or (self._youtube_keys([["k"]]) and started()):
                return Reply(line("media_play"))
            if self.media_paused == "youtube":  # не включаем вместо YouTube какой-то другой плеер
                return Reply(line("media_play_fail"))
        ok, desc, src = media.control("play", source=self.media_paused)
        if not ok:
            return Reply(line("media_none"))
        return Reply(line("media_play"))

    def media_next(self, c, m):
        ok, desc, _ = media.control("next")
        if not ok:
            return Reply(line("media_none"))
        time.sleep(0.8)  # даём плееру переключиться, чтобы назвать новый трек
        info = media.now_playing()
        return Reply(line("media_next", t=info[0]) if info and info[0] else line("done"))

    def media_prev(self, c, m):
        ok, desc, _ = media.control("prev")
        return Reply(line("done") if ok else line("media_none"))

    def media_info(self, c, m):
        info = media.now_playing()
        if not info or not info[0]:
            return Reply(line("media_none"))
        return Reply(line("media_info", t=info[0]))

    def media_seek(self, c, m):
        back = re.search(r"\bназад\b|\bотмотай|\bверни|\bвернись", c)
        fwd = re.search(r"\bвперед|\bпромотай|\bпропусти|\bскипни|\bмотни", c)
        text = re.sub(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи|перемотай|перемотать|промотай|"
                      r"мотни|отмотай|прокрути|верни\w*|вернись|пропусти|скипни|перемотка)\s+", "", c)
        text = re.sub(r"\b(вперед|назад|на|с|со|в|к|видео|музык\w*|трек|песн\w*|ролик|момент\w*|отметк\w*|"
                      r"тайминг)\b", " ", text)
        secs = media.parse_time(text)
        groups = media.number_groups(text)
        to_start = re.search(r"начал|сначала|заново", c)
        if to_start:
            back = fwd = None  # «верни сначала» — это не «назад», а в самое начало
        absolute = to_start or not (back or fwd) and (
            re.match(r"^(поставь|включи|начни|запусти|давай|играй|воспроизведи)", c)
            or re.search(r"\d[:.]\d\d|момент|отметк|тайминг|позици|\bс (\S+ )?\S+ (минут|секунд|час)", c)
            or len(groups) >= 2
            or (groups and groups[0] >= 100 and not re.search(r"секунд|минут|час", c)))  # «на 314» = 3:14
        if secs is None:
            if not (back or fwd):
                return None
            secs = 10  # «перемотай вперёд» без числа — на 10 секунд
        delta = None if absolute else (-secs if back else secs)
        yt = automation.find_window(("youtube",))
        np = media.now_playing()
        if yt and (browser_playing() or not np or any(b in (np[1] or "").lower() for b in BROWSERS)):
            # YouTube в браузере: Firefox не даёт перематывать через систему — клавиши плеера или ссылка с &t=
            if absolute:
                if media.youtube_url_seek(secs):
                    threading.Thread(target=ensure_playing, args=(5.0,), daemon=True).start()
                    return Reply(line("media_seek", t=media.fmt(secs)))
                return Reply(line("media_seek_fail"))
            n10, rest = divmod(int(abs(delta)), 10)
            keys = [["l" if delta > 0 else "j"]] * n10 + [["right" if delta > 0 else "left"]] * round(rest / 5)
            if self._youtube_keys(keys or [["right" if delta > 0 else "left"]]):
                return Reply(line("media_skip", t=media.fmt(abs(delta)), d="вперёд" if delta > 0 else "назад"))
            return Reply(line("media_seek_fail"))
        ok, target, src = media.seek(seconds=secs) if absolute else media.seek(delta=delta)
        if target is None:
            return Reply(line("media_none"))
        return Reply(line("media_seek", t=media.fmt(target)) if ok else line("media_seek_fail"))

    # --- печать и цепочки ---
    def type_cmd(self, c, m):
        """«напиши привет» — печатает в активное окно и жмёт Enter (в чате это отправка)."""
        parsed = automation.parse_type(c)
        if not parsed:
            return None  # «напиши стих» — это просьба к нейросети, а не печать
        text, send = parsed
        title = automation.foreground_title().lower()
        recent_yt = time.time() - self.youtube_opened_at < 120
        if "youtube" in title or (recent_yt and automation.find_window(("youtube",))):
            # на YouTube «напиши X» = поиск X (печатать в саму страницу бессмысленно)
            if "youtube" not in title and not automation.wait_and_focus(("youtube",), timeout=4):
                return Reply(line("focus_fail"))
            youtube_search_in_tab(text)
            return Reply(line("yt", q=text))
        if send is None:
            send = self.cfg.get("type_press_enter", True)
        automation.input_text(text)
        if send:
            time.sleep(0.1)
            send_keys(["enter"])
        return Reply(line("sent" if send else "typed", t=text))

    def search_field(self, c, m):
        automation.focus_search(automation.foreground_title())
        return Reply(line("search_ready"))

    def chat(self, c, m):
        """«первый чат», «чат мама» — открывает чат в Telegram (запускает его, если надо)."""
        if not automation.find_window(("telegram",)):
            found = self.index.find("телеграм", {"телеграм": "telegram"}) if self.index.ready.wait(8) else None
            if not found:
                return Reply(line("app_missing", t="Telegram"), "fail")
            self.index.launch(found[1])
        index = automation.ORDINALS.get(m.groupdict().get("ord") or "", None)
        name = (m.groupdict().get("name") or "").strip() or None
        if name:
            name = re.sub(r"^(с|со|контакт\w*|чат\w*|chat|с контактом)\s+", "", name)
            name = stem_name(name)
        if not automation.telegram_open_chat(index=index or (None if name else 1), name=name):
            # чат не открылся — дальше по цепочке ничего не печатаем, чтобы не написать не туда
            return Reply(line("chat_not_found", t=name or f"номер {index or 1}"), "fail")
        return Reply(line("chat_open", t=name or f"чат номер {index or 1}"))

    def run_chain(self, steps):
        """Выполняет шаги по очереди, держа в уме, в каком окне мы сейчас (Telegram, YouTube, браузер…)."""
        log.info("Цепочка: %s", steps)
        context = None   # ключевые слова заголовка окна, с которым работаем
        kind = None      # telegram / youtube / browser / None
        said = []
        in_search = False  # курсор уже стоит в поле поиска
        for step in steps:
            s = norm(step)
            typed = automation.parse_type(s)
            if typed:
                text, send = typed
                if context and not automation.wait_and_focus(context, timeout=6):
                    # не смогли переключиться — ничего не печатаем, чтобы не написать в чужое окно
                    return Reply(" ".join(said + [line("focus_fail")]))
                if kind == "youtube":
                    youtube_search_in_tab(text)
                    said.append(line("yt", q=text))
                    continue
                if kind in ("youtube", "browser"):
                    # на сайте «напиши X» = поиск X: сначала курсор в строку поиска, потом Enter
                    if not in_search:
                        automation.focus_search(automation.foreground_title())
                        time.sleep(0.4)
                    automation.input_text(text)
                    if send is not False:
                        time.sleep(0.1)
                        send_keys(["enter"])
                    said.append(line("web", q=text))
                    continue
                if send is None:
                    send = self.cfg.get("type_press_enter", True)
                automation.input_text(text)
                if send:
                    time.sleep(0.1)
                    send_keys(["enter"])
                said.append(line("sent" if send else "typed", t=text))
                continue
            q = re.match(r"^(найди|поищи)\s+(?P<q>.+)$", s)
            if q and kind in ("youtube", "telegram"):
                if kind == "telegram":  # поиск в Telegram = открыть чат по имени
                    if not automation.telegram_open_chat(name=q.group("q")):
                        return Reply(" ".join(said + [line("focus_fail")]))
                else:
                    if not automation.wait_and_focus(context, timeout=6):
                        return Reply(" ".join(said + [line("focus_fail")]))
                    youtube_search_in_tab(q.group("q"))
                    said.append(line("yt", q=q.group("q")))
                continue
            if re.match(r"^(нажми на поиск|нажми поиск|поиск|в поиск|перейди в поиск)$", s):
                if context and not automation.wait_and_focus(context, timeout=6):
                    return Reply(" ".join(said + [line("focus_fail")]))
                automation.focus_search(automation.foreground_title())
                time.sleep(0.3)
                in_search = True
                continue
            r = self.handle_one(s)
            if r is None:
                said.append(line("chain_skip", t=step))
                continue
            if r.action in ("fail", "ask"):  # шаг не удался или нужно уточнить — дальше не идём
                return Reply(" ".join(said + [r.text]))
            words = s.split()
            for w in words:
                if w in automation.APP_WINDOWS:
                    context = automation.APP_WINDOWS[w]
                    kind = {"telegram": "telegram", "телеграм": "telegram", "телега": "telegram",
                            "ютуб": "youtube", "youtube": "youtube"}.get(w, "browser" if w == "браузер" else None)
            if re.search(r"\b(чат|диалог|переписк)", s):
                context, kind = ("telegram",), "telegram"
            if context and re.match(r"^(открой|запусти|зайди|перейди)\b", s):
                automation.wait_and_focus(context, timeout=10)
                time.sleep(2.5 if kind in ("youtube", "browser") else 0.4)  # сайту нужно загрузиться
                in_search = False
        return Reply(" ".join(said) if said else line("done"))

    def help(self, c, m):
        return Reply(line("help"))

    def move(self, c, m):
        """Куда переместиться: «в левый нижний угол», «вправо», «в центр», «в другую сторону»."""
        if re.search(r"другую сторону|в другую", c):
            where = "other"
        elif re.search(r"центр|середин", c):
            where = "center"
        else:
            h = "left" if re.search(r"лев|влево", c) else "right" if re.search(r"прав|вправо", c) else ""
            v = "top" if re.search(r"верх|наверх", c) else "bottom" if re.search(r"низ|вниз", c) else ""
            where = "-".join(x for x in (v, h) if x) or "other"
        return Reply(line("move"), "move:" + where)

    def power(self, c, m):
        restart = m.group(1).startswith("перезаг") or m.group(1).startswith("перезап")
        flag = "/r" if restart else "/s"

        def do():
            subprocess.Popen(["shutdown", flag, "/t", "10"], creationflags=NO_WINDOW)
            return Reply(line("power_do", what_noun="перезагрузка" if restart else "выключение"))
        self.pending = do
        verb = "перезагрузить" if restart else "выключить"
        return Reply(line("power_ask", what=verb, what_cap=verb.capitalize()))

    def lock(self, c, m):
        ctypes.windll.user32.LockWorkStation()
        return Reply(line("lock"))

    def volume(self, c, m):
        if re.search(r"(выключи|выключить|отключи|убери|убрать)\w* звук|без звука|замуть", c):
            set_mute(True)
            return Reply(line("mute"))
        if re.search(r"(включи|включить|верни)\w* звук", c) and parse_number(c) is None:
            set_mute(False)
            return Reply(line("unmute"))
        n = parse_number(c)
        cur = get_volume()
        if re.search(r"громче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 10)
        elif re.search(r"тише|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 10)
        elif n is not None:
            new = n
        else:
            return Reply(line("vol_now", n=cur))
        new = max(0, min(100, new))
        set_volume(new)
        return Reply(line("vol_set", n=new))

    def brightness(self, c, m):
        try:
            cur = get_brightness()
        except Exception:
            return Reply(line("bright_fail"))
        n = parse_number(c)
        if re.search(r"ярче|прибав|увелич|повыс|выше", c):
            new = cur + (n or 15)
        elif re.search(r"темнее|убав|уменьш|пониз|ниже", c):
            new = cur - (n or 15)
        elif n is not None:
            new = n
        else:
            return Reply(line("bright_now", n=cur))
        new = max(0, min(100, new))
        try:
            set_brightness(new)
        except Exception:
            return Reply(line("bright_reject"))
        return Reply(line("bright_set", n=new))

    def analyze(self, c, m):
        issues, report = analyze_pc()
        folder = os.path.join(self.base_dir, "reports")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        os.startfile(path)
        use_ai = self.cfg.get("enable_ai") and self.ai and self.ai.available
        spoken = self.ai.summarize_report(report) if use_ai else None
        if not spoken and self.local:
            spoken = self.local.summarize_report(report)
        if spoken:
            return Reply(spoken)
        if not issues:
            return Reply(line("scan_ok"))
        head = "; ".join(issues[:4])
        return Reply(line("scan_bad", n=len(issues), head=head))

    def search(self, c, m):
        q = m.group("q")
        yt = re.search(r"(на|в) (ютубе|ютуб|youtube)", q)
        if yt:
            q = q.replace(yt.group(0), "").strip()
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return Reply(line("yt", q=q))
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        return Reply(line("web", q=q))

    def open(self, c, m):
        if re.search(r"ютуб|youtube", c):
            self.youtube_opened_at = time.time()  # следующее «напиши …» будет поиском на YouTube
        t = re.sub(r"^(мне |пожалуйста |программу |приложение )", "", m.group("t"))
        t = re.sub(r" пожалуйста$", "", t).strip()
        if t in ("звук",):
            return None
        site_mode = t.startswith("сайт ")
        if site_mode:
            t = t[5:].strip()
        else:
            explicit = re.match(r"^(папк\w*|каталог\w*|директори\w*)\s+(?P<n>.+)$", t)
            found = find_folder(explicit.group("n") if explicit else t, search=bool(explicit))
            if found:
                os.startfile(found[0])
                return Reply(line("folder_open", t=found[1]))
            if explicit:
                return Reply(line("folder_missing", t=explicit.group("n")))

        if not site_mode and t in self.apps:
            self._run(self.apps[t])
            return Reply(line("open", t=t))

        if t in self.sites:
            self.web(self.sites[t])
            return Reply(line("open", t=t))
        close = difflib.get_close_matches(t, list(self.sites), n=1, cutoff=0.82)
        if close:
            self.web(self.sites[close[0]])
            return Reply(line("open", t=close[0]))

        domain = re.sub(r"\s*(точка|dot)\s*", ".", t).replace(" ", "")
        if re.fullmatch(r"[\w\-]+(\.[\w\-]+)*\.[a-zа-я]{2,}", domain):
            self.web("https://" + domain)
            return Reply(line("open", t=domain))

        if not site_mode:
            self.index.ready.wait(timeout=8)
            options = self.index.candidates(t, self.aliases)
            if not options:
                return Reply(line("app_missing", t=t))
            top = options[0]
            close = [o for o in options[1:] if top[0] - o[0] < 0.1]
            if top[0] >= 1.0 or not close:  # одно явно подходит — запускаем
                started = self.index.launch_best(t, [top])
                return Reply(line("launch", t=started) if started else line("app_missing", t=t))
            # несколько похожих — спрашиваем, какое открыть
            self.choice = [(n, tg) for _, n, tg in [top] + close[:2]]
            names = [n.title() for n, _ in self.choice]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {nm}" for i, nm in enumerate(names))
            return Reply(line("choose_app", t=listed), "ask")
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(t))
        return Reply(line("site_unknown", t=t))

    def kill_process(self, c, m):
        """«сними задачу steam client helper», «заверши процесс хром принудительно», «кильни дискорд»."""
        t = re.sub(r"^(все |всех )?(процесс\w*|задач\w*)\s+", "", m.group("t").strip())
        return self.close(c, None, target=t, force=bool(re.search(r"принудительн|насильн|жестк|совсем", c)))

    def close(self, c, m, target=None, force=False):
        t = target if target is not None else m.group("t").strip()
        force = force or bool(re.search(r"принудительн|насильн|жестк", c))
        t = re.sub(r"\s+(принудительно|насильно|жестко|совсем)$", "", t).strip()
        if re.fullmatch(r"(окно|это|это окно)", t):
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x73, 0, 0, 0)
            u.keybd_event(0x73, 0, 2, 0)
            u.keybd_event(0x12, 0, 2, 0)
            return Reply(line("closed_window"))
        q = norm(t)
        variants = {q, translit(q)}
        if q in self.aliases:
            variants.add(norm(self.aliases[q]))
        if q in self.apps:
            variants.add(os.path.splitext(os.path.basename(self.apps[q]))[0].lower())
        if self.index.ready.is_set():  # «закрой клод» → приложение Claude → процесс claude
            for _, app_name, _ in self.index.candidates(q, self.aliases, 2):
                variants.add(app_name.split()[0])
        variants = {v.replace(" ", "") for v in variants if len(v.replace(" ", "")) >= 3}

        words = [w for w in translit(q).split() if len(w) >= 3]  # для сравнения с описанием из диспетчера задач
        me = psutil.Process().username()
        best = {}  # имя процесса → (оценка, [процессы])
        for p in psutil.process_iter(["name", "pid", "username", "exe"]):
            name = (p.info["name"] or "").lower().removesuffix(".exe")
            # только свои программы: системные процессы и службы не трогаем никогда
            if not name or name in PROTECTED_PROCS or p.info["username"] != me or p.pid == os.getpid():
                continue
            score = 0.0
            for v in variants:
                if name == v:
                    s = 1.0
                elif name.startswith(v) or (len(name) >= 4 and v.startswith(name) and len(name) >= len(v) * 0.6):
                    s = 0.9  # «steam» не считается совпадением для «steamclienthelper»
                else:
                    s = difflib.SequenceMatcher(None, v, name).ratio()
                    if AppIndex.sounds_like(v, name):
                        s = max(s, 0.86)
                score = max(score, s)
            if score < 0.95 and words:
                # «steam client helper» → описание «Steam Client WebHelper» у steamwebhelper.exe
                desc = norm(file_description(p.info["exe"]))
                if desc:
                    if desc == " ".join(words):
                        score = 1.0
                    elif len(words) >= 2 and all(any(w in d for d in desc.split()) for w in words):
                        score = max(score, 0.92)
            if score >= 0.85:
                prev = best.get(name, (0.0, []))
                best[name] = (max(prev[0], score), prev[1] + [p])
        if not best:
            return Reply(line("not_running", t=t))
        ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
        top_name, (top_score, _) = ranked[0]
        close = [kv for kv in ranked[1:] if top_score - kv[1][0] < 0.1]
        if top_score < 1.0 and close:  # несколько похожих — спрашиваем, что закрыть
            self.choice = [(n, (lambda procs=procs, n=n: self._kill(n, procs, force))) for n, (_, procs) in
                           [ranked[0]] + close[:2]]
            ords = ["первое", "второе", "третье"]
            listed = ", ".join(f"{ords[i]} — {n}" for i, (n, _) in enumerate(self.choice))
            return Reply(line("choose_close", t=listed), "ask")
        return self._kill(top_name, best[top_name][1], force)

    @staticmethod
    def _kill(name, procs, force=False):
        """Завершить процессы: сначала вежливо (как «Снять задачу»), а если не закрылись за 3 секунды
        или просили принудительно — жёстко."""
        alive = []
        for p in procs:
            try:
                p.kill() if force else p.terminate()
                alive.append(p)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                return Reply(line("kill_denied", t=name))
        _, still = psutil.wait_procs(alive, timeout=3)
        if still:
            for p in still:
                try:
                    p.kill()
                except Exception:
                    pass
            _, still = psutil.wait_procs(still, timeout=2)
        if still:
            return Reply(line("kill_failed", t=name))
        return Reply(line("closed", names=name))

    def top_processes(self, c, m):
        """«Что грузит компьютер» — три процесса, которые больше всего едят процессор и память."""
        procs = list(psutil.process_iter(["name", "memory_info"]))
        for p in procs:
            try:
                p.cpu_percent(None)
            except Exception:
                pass
        time.sleep(1.0)
        cpu, mem = {}, {}
        for p in procs:
            try:
                n = (p.info["name"] or "").removesuffix(".exe")
                if n.lower() in ("system idle process", "idle", ""):
                    continue
                cpu[n] = cpu.get(n, 0) + p.cpu_percent(None) / psutil.cpu_count()
                mem[n] = mem.get(n, 0) + p.info["memory_info"].rss / 2**20
            except Exception:
                pass
        top_cpu = sorted(cpu.items(), key=lambda kv: -kv[1])[:3]
        top_mem = sorted(mem.items(), key=lambda kv: -kv[1])[:3]
        c_txt = ", ".join(f"{n} {v:.0f} процентов" for n, v in top_cpu if v >= 1) or "ничего особо не грузит"
        m_txt = ", ".join(f"{n} {v / 1024:.1f} гигабайта" if v >= 1024 else f"{n} {v:.0f} мегабайт"
                          for n, v in top_mem)
        return Reply(line("top_procs", cpu=c_txt, mem=m_txt))

    def create(self, c, m):
        what = m.group("what")
        place_m = re.search(r"\s(на|в|во)\s+(рабоч\w+ стол\w*|документ\w*|загрузк\w*|фото\w*|изображени\w*|"
                            r"картинк\w*|музык\w*|видео|папк\w* \S+)$", what)
        place, place_name = known_dir("desktop"), "на рабочем столе"
        if place_m:
            spot = find_folder(re.sub(r"^папк\w* ", "", place_m.group(2)), search=True)
            if not spot:
                return Reply(line("folder_missing", t=place_m.group(2)))
            place, place_name = spot[0], f"в папке «{spot[1]}»"
            what = what[:place_m.start()]
        kind = re.match(r"(нов\w+ )?(?P<kind>папк\w*|текстов\w+ файл\w*|текстов\w+ документ\w*|"
                        r"документ\w*|файл\w*|таблиц\w*)( (с )?(именем|названием))?\s*(?P<name>.*)$", what)
        if not kind:
            return None
        k, name = kind.group("kind"), kind.group("name").strip()
        ext = ""
        dot = re.search(r"\s*(точка|dot|\.)\s*(?P<e>[a-zа-я0-9]+)$", name)
        if dot:  # «файл заметки точка txt»
            ext, name = "." + translit(dot.group("e")), name[:dot.start()].strip()
        if k.startswith("папк"):
            default = "Новая папка"
        elif k.startswith("таблиц"):
            default, ext = "Новая таблица", ext or ".xlsx"
        elif k.startswith("документ") and not k.startswith("текст"):
            default, ext = "Новый документ", ext or ".docx"
        else:
            default, ext = "Новый файл", ext or ".txt"
        name = re.sub(r'[<>:"/\\|?*]', "", name).strip() or default
        name = name[:1].upper() + name[1:]
        path = os.path.join(place, name + ext)
        n = 2
        while os.path.exists(path):  # ничего не перезаписываем
            path = os.path.join(place, f"{name} ({n}){ext}")
            n += 1
        if k.startswith("папк"):
            os.makedirs(path)
        else:
            open(path, "x", encoding="utf-8").close()  # пустые .docx/.xlsx Office открывает как новые
        return Reply(line("created", t=os.path.basename(path), where=place_name))

    def images(self, c, m):
        q = m.group("q").strip()
        if re.search(r"\bобои\b", c):
            q = "обои " + q
        self.web("https://www.google.com/search?tbm=isch&q=" + urllib.parse.quote(q))
        return Reply(line("images", q=q))

    def info(self, c, m):
        """Открывает поиск Google и вслух пересказывает начало статьи из Википедии, если она нашлась."""
        q = m.group("q").strip()
        self.web("https://www.google.com/search?q=" + urllib.parse.quote(q))
        summary = wiki_summary(q)
        if summary:
            return Reply(line("info", t=summary))
        return Reply(line("web", q=q))

    def video(self, c, m):
        q = m.group("q").strip()
        q = re.sub(r"\s+(на|в) (ютуб\w*|youtube)$", "", q)  # «включи песню X на ютубе»
        q = re.sub(r"^(на|в) (ютуб\w*|youtube)\s*", "", q).strip()
        if not q or q in ("на", "в", "с", "про", "о"):  # «открой какое-нибудь видео на ютубе»
            q = random.choice(["интересное видео", "смешные моменты", "залипательное видео", "лучшие клипы"])
        kind = m.groupdict().get("kind") or ""
        search = q + (" песня" if re.match(r"песн|трек", kind) else "")
        found = youtube_first(search)
        if not found:  # не вышло достать первое видео — хотя бы откроем поиск
            self.web("https://www.youtube.com/results?search_query=" + urllib.parse.quote(search))
            return Reply(line("yt", q=q))
        vid, title = found
        self.web(f"https://www.youtube.com/watch?v={vid}")
        threading.Thread(target=ensure_playing, daemon=True).start()
        title = re.sub(r"\s+", " ", title).strip()
        if len(title) > 70:
            title = title[:67].rsplit(" ", 1)[0] + "…"
        return Reply(line("video", t=title) if title else line("video_untitled"))

    def tab(self, c, m):
        if re.search(r"нов(ую|ая) вкладк", c):
            send_keys(["ctrl", "t"])
            return Reply(line("tab_new"))
        if re.search(r"\b(верни|восстанови)", c):
            send_keys(["ctrl", "shift", "t"])
            return Reply(line("tab_restore"))
        if re.search(r"следующ", c):
            send_keys(["ctrl", "tab"])
            return Reply(line("done"))
        if re.search(r"предыдущ", c):
            send_keys(["ctrl", "shift", "tab"])
            return Reply(line("done"))
        n = max(1, min(20, parse_number(c) or 1))
        for _ in range(n):
            send_keys(["ctrl", "w"])
            time.sleep(0.12)
        return Reply(line("tab_closed", n=n) if n > 1 else line("tab_closed_one"))

    def minimize_all(self, c, m):
        send_keys(["win", "d"])
        return Reply(line("done"))

    def minimize(self, c, m):
        """«Сверни окно / вкладку / браузер» — сворачивает активное окно."""
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if hwnd:
            u.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        return Reply(line("done"))

    def press(self, c, m):
        text = m.group("k")
        times = 1
        rep = re.search(r"\s(\S+) раз\w*$", text)
        if rep and parse_number(rep.group(1)):
            times, text = min(30, parse_number(rep.group(1))), text[:rep.start()]
        keys, unknown = parse_keys(text)
        if unknown:
            return Reply(line("key_unknown", t=unknown))
        if not keys:
            return None
        for _ in range(times):
            send_keys(keys)
            time.sleep(0.08)
        return Reply(line("pressed", t=" + ".join(k.upper() if len(k) == 1 else k.capitalize() for k in keys)))

    def time(self, c, m):
        return Reply(line("time", t=f"{datetime.datetime.now():%H:%M}"))

    def date(self, c, m):
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                  "сентября", "октября", "ноября", "декабря"]
        days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
        d = datetime.date.today()
        return Reply(line("date", t=f"{days[d.weekday()]}, {d.day} {months[d.month - 1]}"))

    def screenshot(self, c, m):
        from PIL import ImageGrab
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "Capri")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"screen_{datetime.datetime.now():%Y%m%d_%H%M%S}.png")
        ImageGrab.grab(all_screens=True).save(path)
        return Reply(line("screenshot"))

    def _run(self, target):
        if target.startswith(("http://", "https://")):
            self.web(target)
            return
        try:
            os.startfile(target)
        except OSError:
            subprocess.Popen(target, shell=True, creationflags=NO_WINDOW)
