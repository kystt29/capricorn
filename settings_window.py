"""Окно «Настройки» (меню в трее) в духе меню настроек Cyberpunk 2077: вкладки сверху (Q / E),
строки «название — значение», ◀ ▶ для переключателей, справа — описание выбранной строки.
Всё применяется сразу и запоминается в config.json."""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import skins

BG = QColor(11, 9, 16)
RED = QColor(255, 93, 87)
RED_DIM = QColor(120, 40, 44)
CYAN = QColor(94, 246, 255)
YELLOW = QColor(243, 230, 0)
TEXT = QColor(235, 220, 215)
FONTS = ["Bahnschrift SemiCondensed", "Bahnschrift", "Segoe UI"]
CTRL_W = 260

STYLE = """
QWidget { color: #ff5d57; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #78282c; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QLineEdit { background: #160e14; border: 1px solid #78282c; color: #ebdcd7; padding: 4px 8px;
            selection-background-color: #ff5d57; }
QLineEdit:focus { border-color: #5ef6ff; }
QPushButton#tab { background: transparent; border: none; border-bottom: 2px solid transparent;
                  color: #a0444a; padding: 6px 14px; }
QPushButton#tab:hover { color: #ff5d57; }
QPushButton#tab:checked { color: #5ef6ff; border-bottom-color: #5ef6ff; }
QPushButton#close { background: transparent; border: 1px solid #78282c; color: #ff5d57; padding: 2px 10px; }
QPushButton#close:hover { border-color: #5ef6ff; color: #5ef6ff; }
QMessageBox, QInputDialog { background: #0b0910; }
QMessageBox QLabel, QInputDialog QLabel { color: #ebdcd7; }
QMessageBox QPushButton, QInputDialog QPushButton { background: #160e14; border: 1px solid #ff5d57;
                                                     color: #ff5d57; padding: 4px 16px; min-width: 70px; }
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover { border-color: #5ef6ff; color: #5ef6ff; }
"""


def font(size, bold=False, spacing=0.0):
    f = QFont()
    f.setFamilies(FONTS)
    f.setPointSizeF(size)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


# ---------------- элементы управления ----------------
class Control(QWidget):
    """Общее для значений справа в строке: hot — строка выбрана (подсвечиваем голубым)."""
    default = None

    def __init__(self):
        super().__init__()
        self.hot = False
        self.setFixedWidth(CTRL_W)
        self.setMinimumHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def step(self, d):
        pass

    def activate(self):
        self.step(1)

    def reset(self):
        pass


class Choice(Control):
    """◀ значение ▶ — клик слева/справа или стрелки листают варианты."""

    def __init__(self, options, value, on_change, default=None):
        super().__init__()
        self.options, self.on_change, self.default = list(options), on_change, default
        self.idx = next((i for i, (v, _) in enumerate(self.options) if v == value), 0)

    def value(self):
        return self.options[self.idx][0] if self.options else None

    def set_options(self, options, value):
        self.options = list(options)
        self.idx = next((i for i, (v, _) in enumerate(self.options) if v == value), 0)
        self.update()

    def set_value(self, value, notify=True):
        for i, (v, _) in enumerate(self.options):
            if v == value:
                self.idx = i
                self.update()
                if notify:
                    self.on_change(v)
                return

    def step(self, d):
        if len(self.options) < 2:
            return
        self.idx = (self.idx + d) % len(self.options)
        self.update()
        self.on_change(self.value())

    def reset(self):
        if self.default is not None or any(v is None for v, _ in self.options):
            self.set_value(self.default)

    def mousePressEvent(self, e):
        self.step(-1 if e.position().x() < self.width() / 3 else 1)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = CYAN if self.hot else RED
        p.setPen(col)
        p.setFont(font(11, True))
        r = QRectF(self.rect())
        p.drawText(r.adjusted(0, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), "◀")
        p.drawText(r, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), "▶")
        p.setPen(TEXT if self.hot else RED)
        p.setFont(font(11.5, True))
        text = self.options[self.idx][1] if self.options else "—"
        fm = p.fontMetrics()
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(r.width() - 44))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)
        # отметки-«точки» под значением, как в игре: сколько вариантов и какой выбран
        n = len(self.options)
        if 1 < n <= 12:
            w = 10
            x0 = r.center().x() - (n * (w + 3) - 3) / 2
            for i in range(n):
                p.fillRect(QRectF(x0 + i * (w + 3), r.bottom() - 3, w, 2), col if i == self.idx else RED_DIM)
        p.end()


class Toggle(Choice):
    def __init__(self, value, on_change, default=None):
        super().__init__([(False, "ВЫКЛ"), (True, "ВКЛ")], bool(value), on_change, default)


class Slider(Control):
    """Число и полоска. live(v) — пока тянут (без записи в файл), done(v) — отпустили / стрелки / колёсико."""

    def __init__(self, lo, hi, value, fmt, done, live=None, default=None, step=1):
        super().__init__()
        self.lo, self.hi, self.v = lo, hi, max(lo, min(hi, int(value)))
        self.fmt, self.done, self.live, self.default, self.step_size = fmt, done, live, default, step
        self.dragging = False
        self.commit_timer = QTimer(self)
        self.commit_timer.setSingleShot(True)
        self.commit_timer.timeout.connect(lambda: self.done(self.v))

    def value(self):
        return self.v

    def _set(self, v, final):
        v = max(self.lo, min(self.hi, int(round(v))))
        changed = v != self.v
        self.v = v
        self.update()
        if changed and self.live:
            self.live(v)
        if final:
            self.commit_timer.start(350)  # стрелки и колёсико — пишем в файл, когда перестали крутить

    def set_value(self, v, notify=True):
        self.v = max(self.lo, min(self.hi, int(v)))
        self.update()
        if notify:
            if self.live:
                self.live(self.v)
            self.done(self.v)

    def reset(self):
        if self.default is not None:
            self.set_value(self.default)

    def step(self, d):
        self._set(self.v + d * self.step_size, True)

    def _bar(self):
        return QRectF(70, self.height() / 2 - 2, self.width() - 74, 4)

    def _from_x(self, x):
        b = self._bar()
        return self.lo + (self.hi - self.lo) * max(0.0, min(1.0, (x - b.left()) / b.width()))

    def mousePressEvent(self, e):
        self.dragging = True
        self._set(self._from_x(e.position().x()), False)

    def mouseMoveEvent(self, e):
        if self.dragging:
            self._set(self._from_x(e.position().x()), False)

    def mouseReleaseEvent(self, e):
        self.dragging = False
        self.commit_timer.stop()
        self.done(self.v)

    def wheelEvent(self, e):
        self.step(1 if e.angleDelta().y() > 0 else -1)

    def paintEvent(self, _):
        p = QPainter(self)
        col = CYAN if self.hot else RED
        p.setPen(TEXT if self.hot else RED)
        p.setFont(font(11.5, True))
        p.drawText(QRectF(0, 0, 64, self.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                   self.fmt(self.v))
        b = self._bar()
        k = (self.v - self.lo) / ((self.hi - self.lo) or 1)
        p.fillRect(b, RED_DIM)
        p.fillRect(QRectF(b.left(), b.top(), b.width() * k, b.height()), col)
        x = b.left() + b.width() * k
        p.fillRect(QRectF(x - 2, b.top() - 7, 4, b.height() + 14), col)
        p.end()


class Button(Control):
    """Действие: «ОТКРЫТЬ ▶»."""

    def __init__(self, text, on_click):
        super().__init__()
        self.text, self.on_click = text, on_click

    def activate(self):
        self.on_click()

    def step(self, d):
        if d > 0:
            self.on_click()

    def mousePressEvent(self, e):
        self.on_click()

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -3)
        col = CYAN if self.hot else RED
        cut = 8
        poly = QPolygonF([QPointF(r.left(), r.top()), QPointF(r.right() - cut, r.top()),
                          QPointF(r.right(), r.top() + cut), QPointF(r.right(), r.bottom()),
                          QPointF(r.left() + cut, r.bottom()), QPointF(r.left(), r.bottom() - cut)])
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(col, 1.2))
        p.setBrush(QColor(col.red(), col.green(), col.blue(), 40 if self.hot else 18))
        p.drawPolygon(poly)
        p.setPen(TEXT if self.hot else RED)
        p.setFont(font(10.5, True, 1.0))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self.text.upper() + "  ▶")
        p.end()


class Meter(Control):
    """Громкость с микрофона и порог чувствительности (жёлтая отметка): левее неё — тишина."""

    def __init__(self, capri, sens):
        super().__init__()
        self.capri, self.sens = capri, sens
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(50)

    def paintEvent(self, _):
        from voice import GATE_MAX
        lst = self.capri.listener
        level = min(1.0, lst.level if lst else 0.0)
        gate = (100 - self.sens.value()) / 100 * GATE_MAX
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0, 9, 0, -9)
        p.fillRect(r, QColor(24, 12, 18))
        segs = 40
        w = r.width() / segs
        for i in range(segs):
            k = (i + 1) / segs
            if k <= level:
                c = CYAN if level >= gate else RED_DIM
                p.fillRect(QRectF(r.left() + i * w + 1, r.top() + 1, w - 2, r.height() - 2), c)
        p.fillRect(QRectF(r.left() + r.width() * gate - 1, r.top() - 4, 3, r.height() + 8), YELLOW)
        p.end()


# ---------------- строки ----------------
class Row(QFrame):
    """Строка настройки: название слева, значение справа. Наведение / фокус — подсветка и описание справа."""
    hovered = Signal(object)

    def __init__(self, title, desc, control):
        super().__init__()
        self.title, self.desc, self.control = title, desc, control
        self.hot = False
        self.nav = None  # стрелки ↑ ↓ — соседняя строка (ставит окно)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(46)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 4, 14, 4)
        self.label = QLabel(title)
        self.label.setFont(font(12.5))
        lay.addWidget(self.label, 1)
        lay.addWidget(control)
        self._style()

    def _style(self):
        self.label.setStyleSheet(f"color: {(TEXT if self.hot else RED).name()}; background: transparent;")
        self.control.hot = self.hot
        self.control.update()
        self.update()

    def set_hot(self, on):
        if on != self.hot:
            self.hot = on
            self._style()
        if on:
            self.hovered.emit(self)

    def enterEvent(self, e):
        from PySide6.QtWidgets import QApplication
        if not isinstance(QApplication.focusWidget(), QLineEdit):  # не уводим курсор из поля, где печатают
            self.setFocus()
        self.set_hot(True)

    def leaveEvent(self, e):
        if not self.hasFocus():
            self.set_hot(False)

    def focusInEvent(self, e):
        self.set_hot(True)

    def focusOutEvent(self, e):
        if not self.underMouse():
            self.set_hot(False)

    def mousePressEvent(self, e):
        self.setFocus()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Left:
            self.control.step(-1)
        elif k == Qt.Key.Key_Right:
            self.control.step(1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.control.activate()
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.nav:
            self.nav(self, -1 if k == Qt.Key.Key_Up else 1)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        if not self.hot:
            return
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0, 1, -1, -1)
        p.fillRect(r, QColor(94, 246, 255, 22))
        p.setPen(QPen(QColor(94, 246, 255, 110), 1))
        p.drawRect(r)
        p.fillRect(QRectF(r.left(), r.top(), 4, r.height()), CYAN)
        p.end()


class TextRow(Row):
    """Строка с полем ввода (своё имя)."""

    def __init__(self, title, desc, text, placeholder, on_done):
        edit = QLineEdit(text)
        edit.setPlaceholderText(placeholder)
        edit.setFixedWidth(CTRL_W)
        edit.setFont(font(11.5))
        edit.editingFinished.connect(lambda: on_done(edit.text().strip()))
        edit.hot = False
        edit.step = edit.activate = edit.reset = lambda *a: None
        super().__init__(title, desc, edit)
        self.edit = edit


def section(text):
    lbl = QLabel(text.upper())
    lbl.setFont(font(10, True, 2.0))
    lbl.setStyleSheet("color: #f3e600; background: transparent; padding: 14px 0 4px 6px;")
    return lbl


# ---------------- превью скина ----------------
class SkinPreview(QWidget):
    """Позы и анимации скина по очереди — чтобы посмотреть, не применяя."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(260)
        self.items = []  # (подпись, [кадры], fps)
        self.i, self.t = 0, 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)

    @staticmethod
    def _load(files, limit=72):
        frames = []
        for f in files:
            reader = QImageReader(f)
            while len(frames) < limit:
                img = reader.read()
                if img.isNull():
                    break
                frames.append(img.scaled(300, 250, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
                if not reader.supportsAnimation() or reader.imageCount() == 1:
                    break
            if len(frames) >= limit:
                break
        return frames

    def set_skin(self, skin):
        self.items = []
        for n, f in enumerate(skin.poses[:8]):
            frames = self._load([f], 1)
            if frames:
                self.items.append((f"ПОЗА {n + 1}/{len(skin.poses)}", frames, 1))
        for event, a in skin.anims.items():
            frames = self._load(a["files"])
            if frames:
                self.items.append(("АНИМАЦИЯ · " + skins.EVENTS[event][0].split(" —")[0].upper(), frames,
                                   a["fps"]))
        self.i, self.t = 0, 0.0
        self.update()

    def _tick(self):
        self.t += 0.04
        if self.items:
            _, frames, fps = self.items[self.i]
            if self.t > max(2.0, len(frames) / fps):
                self.i, self.t = (self.i + 1) % len(self.items), 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0, 0, -1, -1)
        p.fillRect(r, QColor(16, 10, 18))
        p.setPen(QPen(RED_DIM, 1))
        p.drawRect(r)
        if not self.items:
            p.setPen(RED)
            p.setFont(font(11))
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "НЕТ КАРТИНОК —\nРИСОВАННАЯ КАПРИ")
            p.end()
            return
        caption, frames, fps = self.items[self.i]
        img = frames[int(self.t * fps) % len(frames)]
        p.drawImage(QPointF(r.center().x() - img.width() / 2, r.bottom() - 22 - img.height()), img)
        p.setPen(QColor(0, 0, 0, 70))
        for y in range(int(r.top()), int(r.bottom()), 3):  # строки развёртки
            p.drawLine(int(r.left()), y, int(r.right()), y)
        p.setPen(CYAN)
        p.setFont(font(9.5, True, 1.5))
        p.drawText(r.adjusted(8, 0, -8, -4), int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft), caption)
        p.end()


# ---------------- окно ----------------
TABS = [("general", "ОСНОВНОЕ"), ("sound", "ЗВУК"), ("listen", "ПРОСЛУШИВАНИЕ"), ("look", "ВНЕШНОСТЬ"),
        ("profile", "ПРОФИЛЬ")]
FREQ = {"rare": (40, 90), "normal": (15, 40), "often": (5, 15)}


class SettingsWindow(QWidget):
    def __init__(self, capri, avatar, open_entries, open_config, open_github, tab="general"):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.capri, self.avatar, self.cfg = capri, avatar, capri.cfg
        self.open_entries, self.open_config, self.open_github = open_entries, open_config, open_github
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Капри — настройки")
        self.setStyleSheet(STYLE)
        self.resize(1040, 680)
        self.setMinimumSize(860, 540)
        self._drag = None
        self.rows = {}  # вкладка → строки (для F1 «по умолчанию»)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 22, 34, 18)
        head = QHBoxLayout()
        title = QLabel("НАСТРОЙКИ")
        title.setFont(font(30, True, 3.0))
        title.setStyleSheet("color: #ff5d57; background: transparent;")
        sub = QLabel("CAPRI // SYS.CONFIG")
        sub.setFont(font(9.5, True, 2.0))
        sub.setStyleSheet("color: #5ef6ff; background: transparent; padding-top: 18px;")
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setFont(font(12, True))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.close)
        head.addWidget(title)
        head.addSpacing(14)
        head.addWidget(sub, 0, Qt.AlignmentFlag.AlignTop)
        head.addStretch()
        head.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        tabs.addWidget(self._key("Q"))
        self.tab_buttons = []
        for i, (_, name) in enumerate(TABS):
            b = QPushButton(name)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setFont(font(12.5, True, 1.5))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, i=i: self.show_tab(i))
            tabs.addWidget(b)
            self.tab_buttons.append(b)
        tabs.addWidget(self._key("E"))
        tabs.addStretch()
        outer.addLayout(tabs)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #78282c;")
        outer.addWidget(line)
        outer.addSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(26)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)

        side = QVBoxLayout()
        self.desc_title = QLabel()
        self.desc_title.setFont(font(15, True, 1.0))
        self.desc_title.setStyleSheet("color: #5ef6ff; background: transparent;")
        self.desc_title.setWordWrap(True)
        self.desc_text = QLabel()
        self.desc_text.setFont(font(11.5))
        self.desc_text.setStyleSheet("color: #ebdcd7; background: transparent;")
        self.desc_text.setWordWrap(True)
        self.desc_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.preview = SkinPreview()
        side.addWidget(self.desc_title)
        side.addSpacing(6)
        side.addWidget(self.desc_text)
        side.addSpacing(12)
        side.addWidget(self.preview)
        side.addStretch()
        side_box = QWidget()
        side_box.setLayout(side)
        side_box.setFixedWidth(330)
        side_box.setStyleSheet("background: transparent;")
        body.addWidget(side_box)
        outer.addLayout(body, 1)

        foot = QLabel("[Q] [E]  ВКЛАДКИ      [↑] [↓]  ВЫБОР      [←] [→]  ИЗМЕНИТЬ      "
                      "[F1]  ПО УМОЛЧАНИЮ      [ESC]  ЗАКРЫТЬ")
        foot.setFont(font(9.5, True, 1.2))
        foot.setStyleSheet("color: #a0444a; background: transparent; padding-top: 10px;")
        outer.addWidget(foot)

        for key, _ in TABS:
            self.stack.addWidget(self._page(key, getattr(self, "_tab_" + key)()))
        self.show_tab(next((i for i, (k, _) in enumerate(TABS) if k == tab), 0))

    # ---- оформление ----
    @staticmethod
    def _key(text):
        lbl = QLabel(text)
        lbl.setFont(font(10, True))
        lbl.setFixedSize(24, 22)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #0b0910; background: #ff5d57;")
        return lbl

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        cut = 26
        poly = QPolygonF([QPointF(r.left(), r.top()), QPointF(r.right() - cut, r.top()),
                          QPointF(r.right(), r.top() + cut), QPointF(r.right(), r.bottom()),
                          QPointF(r.left() + cut, r.bottom()), QPointF(r.left(), r.bottom() - cut)])
        p.setPen(QPen(RED, 1.2))
        p.setBrush(QColor(BG.red(), BG.green(), BG.blue(), 248))
        p.drawPolygon(poly)
        p.setClipping(False)
        p.setPen(QColor(255, 93, 87, 9))
        for y in range(int(r.top()) + 2, int(r.bottom()), 3):
            p.drawLine(int(r.left()) + 2, y, int(r.right()) - 2, y)
        p.fillRect(QRectF(r.left() + 14, r.top() + 10, 46, 3), YELLOW)       # жёлтые акценты по углам
        p.fillRect(QRectF(r.right() - 70, r.bottom() - 9, 50, 3), YELLOW)
        p.end()

    def _page(self, key, items):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(2)
        self.rows[key] = []
        scroll = QScrollArea()
        for item in items:
            if isinstance(item, Row):
                item.hovered.connect(self._describe)
                item.nav = lambda row, d, key=key, scroll=scroll: self._nav(key, row, d, scroll)
                self.rows[key].append(item)
            lay.addWidget(item)
        lay.addStretch()
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def show_tab(self, i):
        self.tab = i
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.tab_buttons):
            b.setChecked(j == i)
        key = TABS[i][0]
        self.preview.setVisible(key == "look")
        rows = self.rows.get(key) or []
        if rows:
            rows[0].setFocus()
            self._describe(rows[0])

    def _nav(self, key, row, d, scroll):
        rows = self.rows[key]
        nxt = rows[(rows.index(row) + d) % len(rows)]
        nxt.setFocus()
        scroll.ensureWidgetVisible(nxt)

    def _describe(self, row):
        for rows in self.rows.values():  # подсвечена всегда одна строка
            for r in rows:
                if r is not row and r.hot:
                    r.hot = False
                    r._style()
        self.desc_title.setText(row.title.upper())
        desc = row.desc() if callable(row.desc) else row.desc
        self.desc_text.setText(desc)

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k == Qt.Key.Key_Q:
            self.show_tab((self.tab - 1) % len(TABS))
        elif k == Qt.Key.Key_E:
            self.show_tab((self.tab + 1) % len(TABS))
        elif k == Qt.Key.Key_F1:
            for row in self.rows[TABS[self.tab][0]]:
                row.control.reset()
        else:
            super().keyPressEvent(e)

    # перетаскивание за верхнюю часть, как обычное окно
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 90:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    # ---- вкладки ----
    def _toggle_setting(self, key, default):
        def change(v):
            self.capri.set_setting(key, bool(v))
        return Toggle(self.cfg.get(key, default), change, default)

    def _tab_general(self):
        import autostart
        cfg = self.cfg
        lo, hi = cfg.get("chatter_min_minutes", 15), cfg.get("chatter_max_minutes", 40)
        freq = next((k for k, v in FREQ.items() if v == (lo, hi)), "custom")
        options = [("rare", "РЕДКО"), ("normal", "ОБЫЧНО"), ("often", "ЧАСТО")]
        if freq == "custom":
            options.append(("custom", f"СВОЁ: {lo}–{hi} МИН"))

        def set_freq(v):
            if v in FREQ:
                for key, val in zip(("chatter_min_minutes", "chatter_max_minutes"), FREQ[v]):
                    self.capri.set_setting(key, val)

        def set_hide(v):
            self.avatar.hide_after = float(v)

        return [
            section("Поведение"),
            Row("Появляться самой", "Капри время от времени сама появляется: поболтать, напомнить попить воды "
                                    "или погулять по панели задач. В играх и фильмах на весь экран не мешает.",
                Toggle(cfg.get("chatter", True), self.capri.set_chatter, True)),
            Row("Как часто появляться", "Редко — раз в 40–90 минут, обычно — раз в 15–40, часто — раз в 5–15.",
                Choice(options, freq, set_freq, "normal")),
            Row("Гулять по панели задач", "Иногда вместо обычного появления Капри проходит вдоль панели задач.",
                Choice([(False, "ВЫКЛ"), (True, "ВКЛ")], cfg.get("roam_chance", 0.5) > 0,
                       lambda v: self.capri.set_setting("roam_chance", 0.5 if v else 0.0), True)),
            Row("Прыгать молнией, пока отвечает", "Во время ответа Капри телепортируется по экрану красной "
                                                  "молнией, потом возвращается на место.",
                self._toggle_setting("teleport_on_answer", True)),
            Row("Прятаться через", "Сколько секунд тишины Капри остаётся на экране, прежде чем исчезнуть.",
                Slider(5, 60, cfg.get("hide_after_seconds", 12), lambda v: f"{v} С",
                       lambda v: self.capri.set_setting("hide_after_seconds", v), set_hide, 12)),
            Row("Приветствие при запуске", "Сказать что-нибудь при запуске Капри.",
                self._toggle_setting("greet_on_start", True)),
            section("Браузер и система"),
            Row("Искать в браузере, если не знаю", "Если Капри не поняла команду и не знает ответа — открыть "
                                                   "поиск в браузере. Выключено — просто скажет, что не знает.",
                Toggle(cfg.get("search_when_unsure", True), self.capri.set_search_when_unsure, True)),
            Row("Запускаться вместе с Windows", "Ярлык Капри в папке «Автозагрузка» — она стартует сама после "
                                                "входа в Windows.",
                Toggle(autostart.enabled(), self.capri.set_autostart, False)),
            Row("Мои сайты, программы и ответы", "Свои сайты, программы и ответы на фразы — без правки config.json.",
                Button("Открыть", self.open_entries)),
            Row("Файл настроек", "config.json — все настройки текстом. Правки подхватываются без перезапуска.",
                Button("Открыть", self.open_config)),
            Row("Обновления", "Страница Капри на GitHub: новые версии и описание изменений.",
                Button("Открыть", self.open_github)),
        ]

    def _tab_sound(self):
        from voice import default_input_name, input_devices
        cfg, capri = self.cfg, self.capri

        def live_gain(v):
            if capri.listener:
                capri.listener.gain = v / 100

        def live_sens(v):
            if capri.listener:
                capri.listener.sensitivity = v

        mics = [(None, f"КАК В WINDOWS ({default_input_name()})")]
        try:
            mics += [(n, n) for n, _ in input_devices()]
        except Exception:
            pass
        sens = Slider(0, 100, cfg.get("mic_sensitivity", 100), lambda v: f"{v}%",
                      lambda v: capri.set_mic_tuning(sensitivity=v), live_sens, 100, 5)
        return [
            section("Голос Капри"),
            Row("Громкость", "Громкость только голоса Капри — общая громкость Windows не меняется.",
                Slider(0, 100, cfg.get("voice_volume", 100), lambda v: f"{v}%", capri.set_voice_volume,
                       lambda v: setattr(capri.speaker, "volume", v), 100, 5)),
            Row("Цифровые эффекты", "Металлический «цифровой» оттенок голоса. 0 — чистый голос, ×1.8 — как "
                                    "задумано, больше — сильнее.",
                Slider(0, 250, float(cfg.get("voice_fx", 1.8)) * 100, lambda v: "НЕТ" if v == 0 else f"×{v / 100:.1f}",
                       lambda v: capri.set_voice_fx(v / 100), None, 180, 10)),
            Row("Проверить голос", "Капри скажет пробную фразу с текущей громкостью и эффектами.",
                Button("Сказать", lambda: capri.jobs.put(("say", "Проверка громкости. Так меня хорошо слышно?")))),
            section("Микрофон"),
            Row("Устройство", "Какой микрофон слушает Капри. «Как в Windows» — тот, что выбран в Windows "
                              "по умолчанию.",
                Choice(mics, cfg.get("mic_device"), lambda v: capri.set_mic(v, announce=False), None)),
            Row("Громкость микрофона", "Усиление звука с микрофона только для Капри: ×1 — как есть, ×2 — вдвое "
                                       "громче. Если не слышит — прибавь.",
                Slider(50, 400, float(cfg.get("mic_gain", 1.0)) * 100, lambda v: f"×{v / 100:.1f}",
                       lambda v: capri.set_mic_tuning(gain=v / 100), live_gain, 100, 10)),
            Row("Чувствительность", "100% — слышит всё. Меньше — пропускает тихий шум: убавь, если реагирует на "
                                    "шум или на саму себя.", sens),
            Row("Уровень", "Скажи что-нибудь: полоска должна заходить за жёлтую отметку и становиться голубой.",
                Meter(capri, sens)),
        ]

    def _tab_listen(self):
        cfg, capri = self.cfg, self.capri
        return [
            section("Сколько слушать без имени"),
            Row("После ответа", "Сколько секунд после ответа Капри ждёт следующую команду без «Капри». "
                                "ВЫКЛ — после каждой команды перестаёт слушать, следующую нужно начинать с имени.",
                Slider(0, 30, cfg.get("followup_seconds", 6), lambda v: "ВЫКЛ" if v == 0 else f"{v} С",
                       lambda v: capri.set_listen_times(followup=v), None, 6)),
            Row("После «Капри»", "Сколько секунд Капри ждёт команду, если позвать её по имени и замолчать.",
                Slider(3, 30, cfg.get("listen_window_seconds", 8), lambda v: f"{v} С",
                       lambda v: capri.set_listen_times(window=v), None, 8)),
            section("Распознавание"),
            Row("Перебивать голосом", "Пока Капри говорит, её можно остановить словами «Капри» или «стоп».",
                self._toggle_setting("barge_in", True)),
            Row("Уточнять речь через Google", "Фраза дополнительно распознаётся через Google — точнее на вопросах. "
                                              "Без интернета или через VPN может работать медленнее.",
                self._toggle_setting("use_google_stt", True)),
        ]

    # ---- внешность ----
    def _tab_look(self):
        assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        self.assets_dir = assets
        self.skin_list = skins.list_skins(assets)
        current = self.cfg.get("skin", "")
        if current not in [s.id for s in self.skin_list]:
            current = skins.BUILTIN
        self.skin_choice = Choice([(s.id, s.name.upper()) for s in self.skin_list], current, self._apply_skin,
                                  skins.BUILTIN)
        self.event_choice = Choice([(e, t.split(" —")[0].upper()) for e, (t, _) in skins.EVENTS.items()],
                                   "thinking", lambda v: self._describe(self.event_row))
        self.event_row = Row("Событие", self._event_desc, self.event_choice)
        self.preview.set_skin(self._skin())
        return [
            section("Скин"),
            Row("Скин", self._skin_desc, self.skin_choice),
            Row("Новый скин из картинок", "Выбери одну или несколько картинок (PNG, WebP, JPG или GIF) — они "
                                          "станут позами нового скина. Лучше с прозрачным фоном.",
                Button("Выбрать", self._new_skin)),
            Row("Новый скин на основе текущего", "Копия выбранного скина в папке skins — её можно менять, "
                                                 "не трогая оригинал. Так можно дополнить стандартную Капри.",
                Button("Создать", self._copy_skin)),
            section("Анимации"),
            self.event_row,
            Row("Добавить анимацию", "Для выбранного выше события: одна GIF или несколько PNG-кадров (по порядку "
                                     "имён). Старая анимация этого события в скине заменится.",
                Button("Выбрать", self._add_anim)),
            section("Папка"),
            Row("Папка скинов", "skins в папке Капри. Внутри — ПРОЧТИ.txt: как устроен скин, если хочешь "
                                "собрать его руками.", Button("Открыть", self._open_skins)),
            Row("Перечитать скины", "Если менял(а) файлы в папке skins руками — подхватить изменения.",
                Button("Обновить", self._reload_skins)),
        ]

    def _skin(self):
        sid = self.skin_choice.value()
        return next((s for s in self.skin_list if s.id == sid), self.skin_list[0])

    def _skin_desc(self):
        s = self._skin()
        who = f"Автор: {s.author}. " if s.author else ""
        where = "Папка assets." if s.id == skins.BUILTIN else f"Папка skins\\{s.id}."
        return f"{who}{s.summary().capitalize()}.\n{where}\n\nМеняется сразу. Стрелками ◀ ▶ — следующий скин."

    def _event_desc(self):
        e = self.event_choice.value()
        title, loop = skins.EVENTS[e]
        has = "есть" if e in self._skin().anims else "нет — используется обычная поза"
        return (f"{title}. {'Играет по кругу, пока длится.' if loop else 'Играет один раз.'}\n"
                f"В текущем скине: {has}.\n\nПапка: anim\\{e} (кадры) или anim\\{e}.gif.")

    def _apply_skin(self, sid):
        skin = self._skin()
        try:
            self.avatar.apply_skin(skin)
        except Exception:
            import logging
            logging.getLogger("capri").exception("Скин не загрузился")
            return
        self.capri.set_setting("skin", sid)
        self.preview.set_skin(skin)
        self.avatar.summon()           # показать, как выглядит
        self.avatar.set_mode("idle")
        rows = self.rows.get("look") or []
        if rows:
            self._describe(next(r for r in rows if r.control is self.skin_choice))

    def _refresh_skins(self, select=None):
        self.skin_list = skins.list_skins(self.assets_dir)
        sid = select if select is not None else self.skin_choice.value()
        self.skin_choice.set_options([(s.id, s.name.upper()) for s in self.skin_list], sid)
        self._apply_skin(self.skin_choice.value())

    def _ask_name(self, default):
        name, ok = QInputDialog.getText(self, "Новый скин", "Название скина:", text=default)
        return skins.safe_name(name) if ok and name.strip() else None

    def _new_skin(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Картинки для скина", "",
                                                "Картинки (*.png *.webp *.jpg *.jpeg *.gif)")
        if not files:
            return
        name = self._ask_name("Моя Капри")
        if name:
            self._refresh_skins(skins.create(name, files))

    def _copy_skin(self):
        base = self._skin()
        name = self._ask_name(("Моя Капри" if base.id == skins.BUILTIN else base.name + " копия"))
        if name:
            self._refresh_skins(skins.copy_of(base, name))

    def _add_anim(self):
        event = self.event_choice.value()
        skin = self._skin()
        if skin.id == skins.BUILTIN:
            # стандартную Капри не трогаем (её обновляют вместе с Капри) — анимацию кладём в её копию
            if QMessageBox.question(self, "Стандартная Капри",
                                    "Стандартный скин не меняется — он обновляется вместе с Капри.\n"
                                    "Создать его копию «Моя Капри» и добавить анимацию туда?") \
                    != QMessageBox.StandardButton.Yes:
                return
        files, _ = QFileDialog.getOpenFileNames(self, "GIF или PNG-кадры: " + skins.EVENTS[event][0], "",
                                                "Анимация (*.gif *.png *.webp *.jpg *.jpeg)")
        if not files:
            return
        if skin.id == skins.BUILTIN:
            sid = skins.copy_of(skin, "Моя Капри")
        else:
            sid = skin.id
            if event in skin.anims and QMessageBox.question(
                    self, "Заменить анимацию?", f"В скине уже есть анимация «{skins.EVENTS[event][0]}». "
                                                f"Заменить её?") != QMessageBox.StandardButton.Yes:
                return
        skins.add_anim(sid, event, files)
        self._refresh_skins(sid)

    def _open_skins(self):
        os.startfile(skins.ensure_dir())

    def _reload_skins(self):
        self._refresh_skins()

    # ---- профиль ----
    def _tab_profile(self):
        cfg = self.cfg
        win, pc = os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", "")
        self.name_mode = Choice([("custom", "СВОЁ ИМЯ"), ("windows", f"WINDOWS: {win}"), ("pc", f"ПК: {pc}"),
                                 ("none", "НЕ НАЗЫВАТЬ")], cfg.get("user_name_mode", "none"),
                                lambda v: self._save_profile(), "none")
        self.gender = Choice([("male", "МУЖСКОЙ"), ("female", "ЖЕНСКИЙ")], cfg.get("user_gender", "male"),
                             lambda v: self._save_profile(), "male")
        self.name_row = TextRow("Своё имя", "Как Капри будет тебя называть, если выше выбрано «своё имя».",
                                cfg.get("user_name", ""), "например: Дима", lambda t: self._save_profile(t))
        return [
            section("Как ко мне обращаться"),
            Row("Как называть", "Капри иногда обращается по имени — в обычных фразах и в разговоре через "
                                "нейросеть.", self.name_mode),
            self.name_row,
            Row("Род", "В каком роде Капри говорит с тобой: «ты сказал» или «ты сказала».", self.gender),
        ]

    def _save_profile(self, typed=None):
        name = self.name_row.edit.text().strip()
        mode = self.name_mode.value()
        if typed:  # ввели имя — значит, называть по нему
            self.name_mode.set_value("custom", notify=False)
            mode = "custom"
        if mode == "custom" and not name:
            mode = "none"
        self.capri.set_profile(mode, name, self.gender.value())
