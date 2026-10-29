"""Собирает capri.zip для публикации (например, в Telegram-канале) — без личных файлов.

    python tools/make_release.py [куда_сохранить.zip]

В архив не попадают: config.json (личные настройки), capri.log (журнал разговоров), reports/
(отчёты о ПК), samples/ (образцы и чужие записи), models/ (распаковывается из bundled/ через setup.bat),
кэш Python."""
import os
import sys
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"models", "reports", "samples", "__pycache__", ".git", ".github", ".claude"}
SKIP_FILES = {"config.json", "capri.log", "games.json", "games.json.bad", "learning.json", "learning.json.bad"}
SKIP_EXT = (".pyc", ".tmp", ".zip", ".log")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Desktop", "capri.zip")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                in_bundle = os.path.basename(root) == "bundled"  # модель распознавания речи — нужна
                if f in SKIP_FILES or (f.lower().endswith(SKIP_EXT) and not in_bundle):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.join("capri", os.path.relpath(full, BASE)))
                count += 1
    print(f"Готово: {out} — файлов: {count}, размер: {os.path.getsize(out) / 2**20:.1f} МБ")


if __name__ == "__main__":
    main()
