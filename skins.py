"""Скины Капри: свои позы (PNG/WebP/JPG/GIF) и анимации на разные события.

Скин — папка в skins/:
    skins/Моя Капри/
        skin.json            необязательно: {"name": "...", "author": "...", "fps": 24, "scale": 1.0}
        poses/*.png          позы, сменяют друг друга (можно класть картинки и прямо в папку скина)
        roam.png             поза для прогулки по панели задач (необязательно)
        anim/thinking/*.png  кадры анимации по порядку имён — или одним файлом: anim/thinking.gif

Встроенная Капри (папка assets) — тоже скин, просто со старыми именами папок."""
import json
import os
import re
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))
SKINS_DIR = os.path.join(BASE, "skins")
IMAGES = (".png", ".webp", ".jpg", ".jpeg", ".gif")
BUILTIN = ""  # id встроенной Капри в config.json ("skin": "")

# событие → (название в настройках, повторять ли по кругу)
EVENTS = {
    "idle": ("Ожидание — вместо позы, пока ничего не делает", True),
    "listening": ("Слушает", True),
    "thinking": ("Думает", True),
    "speaking": ("Говорит", True),
    "roam": ("Гуляет по панели задач", True),
    "intro": ("Появление при запуске", False),
    "wave": ("«Помаши ручкой»", False),
}

README = """Свои скины Капри
================

Самый простой способ — меню в трее → «Настройки…» → «Внешность» → «Новый скин из картинок».
Можно и руками: создай здесь папку с любым названием и положи в неё картинки.

    Моя Капри\\
        skin.json            (необязательно) {"name": "Моя Капри", "author": "ник", "fps": 24, "scale": 1.0}
        poses\\*.png          позы — Капри меняет их каждые 12–30 секунд
                             (картинки можно класть и прямо в папку скина)
        roam.png             поза для прогулки по панели задач (необязательно)
        anim\\<событие>\\*.png кадры анимации, по порядку имён (0000.png, 0001.png…)
        anim\\<событие>.gif   …или одна GIF-анимация

События:
    idle       ожидание — играет по кругу вместо позы
    listening  слушает
    thinking   думает
    speaking   говорит
    roam       гуляет по панели задач (вместо roam.png)
    intro      появление при запуске (один раз)
    wave       «помаши ручкой» (один раз)

Картинки лучше с прозрачным фоном (PNG или GIF). Капри вписывает их в окно 340×380 и ставит на «пол».
"scale" в skin.json уменьшает (0.8) или увеличивает (1.2) анимации; "fps" — скорость PNG-кадров.
Пустые и битые картинки пропускаются. Скин без картинок — значит, рисованная Капри.
"""


class Skin:
    def __init__(self, skin_id, name, path, author="", fps=24, scale=1.0):
        self.id, self.name, self.path, self.author = skin_id, name, path, author
        self.fps, self.scale = fps, scale
        self.poses = []   # файлы поз
        self.roam = None  # файл позы для прогулки
        self.anims = {}   # событие → {"files": [кадры] или [gif], "loop": bool, "fps": int}

    def summary(self):
        parts = [f"поз: {len(self.poses)}"]
        if self.anims:
            parts.append("анимации: " + ", ".join(EVENTS[e][0].split(" —")[0].lower() for e in self.anims))
        return "; ".join(parts)


def _images(folder):
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.lower().endswith(IMAGES) and os.path.isfile(os.path.join(folder, f))]


def _anim(files, event, fps):
    return {"files": files, "loop": EVENTS[event][1], "fps": fps}


def builtin(assets_dir):
    """Стандартная Капри из assets: poses + capri.png, capri_roam.png, anim/capri (машет), anim/eyes (думает)."""
    s = Skin(BUILTIN, "Капри (стандартная)", assets_dir, "kystt29")
    seen = set()
    for f in [os.path.join(assets_dir, n) for n in ("capri.png", "capri.jpg", "capri.webp")] + \
            _images(os.path.join(assets_dir, "poses")):
        low = os.path.basename(f).lower()
        if os.path.isfile(f) and low not in seen and not low.startswith("capri_roam"):
            seen.add(low)
            s.poses.append(f)
    for f in (os.path.join(assets_dir, "capri_roam.png"), os.path.join(assets_dir, "poses", "capri_roam.png")):
        if os.path.isfile(f):
            s.roam = f
            break
    anim = os.path.join(assets_dir, "anim")
    for folder, events in (("capri", ("intro", "wave")), ("eyes", ("thinking",))):
        frames = _images(os.path.join(anim, folder))
        for e in events:
            if frames:
                s.anims[e] = _anim(frames, e, 24)
    # новые события можно добавить и стандартной Капри: assets/anim/<событие>
    _read_anims(s, anim)
    return s


def _read_anims(skin, anim_dir):
    if not os.path.isdir(anim_dir):
        return
    for entry in sorted(os.listdir(anim_dir)):
        path = os.path.join(anim_dir, entry)
        event, ext = os.path.splitext(entry)
        event = event.lower()
        if event not in EVENTS:
            continue
        if os.path.isdir(path):
            files = _images(path)
        elif ext.lower() in IMAGES:
            files = [path]
        else:
            continue
        if files:
            skin.anims[event] = _anim(files, event, skin.fps)


def load(path):
    """Скин из папки skins/<имя>."""
    folder = os.path.basename(path.rstrip("\\/"))
    meta = {}
    try:
        with open(os.path.join(path, "skin.json"), encoding="utf-8-sig") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        pass
    try:
        fps = max(1, min(60, int(meta.get("fps", 24))))
        scale = max(0.2, min(3.0, float(meta.get("scale", 1.0))))
    except (TypeError, ValueError):
        fps, scale = 24, 1.0
    s = Skin(folder, str(meta.get("name") or folder), path, str(meta.get("author") or ""), fps, scale)
    for f in _images(path) + _images(os.path.join(path, "poses")):
        if os.path.splitext(os.path.basename(f))[0].lower() == "roam":
            s.roam = f
        else:
            s.poses.append(f)
    _read_anims(s, os.path.join(path, "anim"))
    if "roam" in s.anims and s.roam is None:
        s.roam = s.anims["roam"]["files"][0]
    return s


def list_skins(assets_dir):
    """Стандартная Капри + все папки из skins/."""
    out = [builtin(assets_dir)]
    if os.path.isdir(SKINS_DIR):
        for name in sorted(os.listdir(SKINS_DIR), key=str.lower):
            path = os.path.join(SKINS_DIR, name)
            if os.path.isdir(path) and not name.startswith((".", "_")):
                out.append(load(path))
    return out


def find(assets_dir, skin_id):
    """Скин по id из config.json; нет такой папки — стандартная Капри."""
    if skin_id:
        path = os.path.join(SKINS_DIR, skin_id)
        if os.path.isdir(path):
            return load(path)
    return builtin(assets_dir)


def ensure_dir():
    os.makedirs(SKINS_DIR, exist_ok=True)
    readme = os.path.join(SKINS_DIR, "ПРОЧТИ.txt")
    if not os.path.isfile(readme):
        with open(readme, "w", encoding="utf-8-sig") as f:
            f.write(README)
    return SKINS_DIR


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return name[:60] or "Мой скин"


def create(name, pose_files):
    """Новый скин из выбранных картинок: копируем их в skins/<имя>/poses. Возвращает id (имя папки)."""
    ensure_dir()
    base = safe_name(name)
    folder, n = base, 2
    while os.path.exists(os.path.join(SKINS_DIR, folder)):
        folder, n = f"{base} {n}", n + 1
    path = os.path.join(SKINS_DIR, folder)
    os.makedirs(os.path.join(path, "poses"))
    for i, f in enumerate(pose_files):
        shutil.copy2(f, os.path.join(path, "poses", f"{i:02d}_{os.path.basename(f)}"))
    with open(os.path.join(path, "skin.json"), "w", encoding="utf-8") as f:
        json.dump({"name": base, "author": "", "fps": 24, "scale": 1.0}, f, ensure_ascii=False, indent=2)
    return folder


def copy_of(skin, name):
    """Свой скин на основе другого (например, стандартной Капри): копируем позы и анимации. Возвращает id."""
    folder = create(name, skin.poses)
    path = os.path.join(SKINS_DIR, folder)
    if skin.roam and "roam" not in skin.anims:
        shutil.copy2(skin.roam, os.path.join(path, "roam" + os.path.splitext(skin.roam)[1].lower()))
    for event, a in skin.anims.items():
        add_anim(folder, event, a["files"])
    return folder


def add_anim(skin_id, event, files):
    """Анимация для события: одна GIF → anim/<событие>.gif, несколько картинок → anim/<событие>/0000.png…
    Старая анимация этого события заменяется."""
    path = os.path.join(SKINS_DIR, skin_id, "anim")
    os.makedirs(path, exist_ok=True)
    folder = os.path.join(path, event)
    for old in [folder] + [os.path.join(path, event + e) for e in IMAGES]:
        if os.path.isdir(old):
            shutil.rmtree(old)
        elif os.path.isfile(old):
            os.remove(old)
    if len(files) == 1:
        ext = os.path.splitext(files[0])[1].lower()
        shutil.copy2(files[0], os.path.join(path, event + ext))
        return
    os.makedirs(folder)
    for i, f in enumerate(sorted(files)):
        shutil.copy2(f, os.path.join(folder, f"{i:04d}{os.path.splitext(f)[1].lower()}"))
