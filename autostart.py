"""Автозапуск Капри вместе с Windows: ярлык в папке «Автозагрузка», который запускает Капри из её папки.
Люди часто копируют туда сам run.bat — а он ищет main.py рядом с собой и молча ничего не запускает."""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
STARTUP = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
LINK = os.path.join(STARTUP, "Капри.lnk")


def pythonw():
    """pythonw.exe того же Python, на котором сейчас работает Капри (без окна консоли)."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.isfile(w) else exe


def enabled():
    return os.path.isfile(LINK)


def enable():
    import comtypes.client
    shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
    link = shell.CreateShortcut(LINK)
    link.TargetPath = pythonw()
    link.Arguments = f'"{os.path.join(BASE, "main.py")}"'
    link.WorkingDirectory = BASE
    link.Description = "Капри — голосовой ассистент"
    link.Save()


def disable():
    if os.path.isfile(LINK):
        os.remove(LINK)


def stray_run_bat():
    """Скопированный в автозагрузку run.bat (так не работает) — путь к нему или None."""
    p = os.path.join(STARTUP, "run.bat")
    return p if os.path.isfile(p) else None
