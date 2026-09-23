"""Главное окно: полупрозрачная синяя закруглённая панель поверх всех окон."""
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextBrowser, QVBoxLayout, QWidget,
)

from . import winapi
from .logo import beetle_pixmap
from .mathfmt import keep_line_breaks, latex_to_plain

RADIUS = 18
BG_COLOR = QColor(18, 52, 120, 205)
BORDER_COLOR = QColor(120, 190, 255, 140)

STYLE = """
QWidget { color: #eaf4ff; font-family: 'Segoe UI', sans-serif; font-size: 10pt; }
QLabel#title { font-size: 12pt; font-weight: 600; }
QLabel#status { color: #a9c8f0; font-size: 9pt; }
QPushButton#icon, QPushButton#close {
    background: transparent; border: none; border-radius: 8px;
    font-size: 12pt; min-width: 28px; min-height: 28px;
}
QPushButton#icon:hover { background: rgba(255,255,255,0.15); }
QPushButton#close:hover { background: rgba(255,80,80,0.55); }
QPushButton#copy {
    background: rgba(255,255,255,0.12); border: 1px solid rgba(160,210,255,0.35);
    border-radius: 9px; padding: 5px 12px;
}
QPushButton#copy:hover { background: rgba(255,255,255,0.22); }
QPushButton#stop {
    background: rgba(255,90,90,0.25); border: 1px solid rgba(255,150,150,0.5);
    border-radius: 9px; padding: 5px 12px;
}
QPushButton#stop:hover { background: rgba(255,90,90,0.45); }
QPushButton#copy:disabled { color: rgba(234,244,255,0.4); }
QTextBrowser {
    background: rgba(5,20,55,0.35); border: 1px solid rgba(160,210,255,0.2);
    border-radius: 12px; padding: 8px; selection-background-color: #3a7bff;
}
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.3); border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class MainWindow(QWidget):
    settings_requested = Signal()
    quit_requested = Signal()
    stop_requested = Signal()
    geometry_changed = Signal(list)

    def __init__(self, hide_from_capture=True):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("ZhukoGPT")
        self.setMinimumSize(280, 220)
        self.setStyleSheet(STYLE)
        self.hide_from_capture = hide_from_capture
        self.capture_hidden_ok = False
        self._drag_offset = None
        self._answer_text = ""

        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self._emit_geometry)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # --- шапка ---
        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(beetle_pixmap(26))
        title = QLabel("ZhukoGPT", objectName="title")
        btn_settings = QPushButton("⚙", objectName="icon", toolTip="Настройки")
        btn_close = QPushButton("✕", objectName="close", toolTip="Выход")
        for b in (btn_settings, btn_close):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
        btn_settings.clicked.connect(self.settings_requested)
        btn_close.clicked.connect(self.quit_requested)
        header.addWidget(logo)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(btn_settings)
        header.addWidget(btn_close)
        self.header.setCursor(Qt.SizeAllCursor)
        root.addWidget(self.header)

        # --- ответ ---
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setFocusPolicy(Qt.NoFocus)
        root.addWidget(self.view, 1)

        # --- низ ---
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.status = QLabel(objectName="status")
        self.status.setWordWrap(True)
        self.btn_copy = QPushButton("Копировать", objectName="copy")
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.setFocusPolicy(Qt.NoFocus)
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_stop = QPushButton("Стоп", objectName="stop", toolTip="Остановить запрос")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setFocusPolicy(Qt.NoFocus)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_stop.hide()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.btn_copy)
        bottom.addWidget(self.btn_stop)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(bottom)

        self._dots = 0
        self._busy_timer = QTimer(self, interval=400)
        self._busy_timer.timeout.connect(self._tick_busy)

    # --- размещение ---
    def place(self, geometry):
        if geometry and len(geometry) == 4:
            x, y, w, h = geometry
            for screen in QGuiApplication.screens():
                if screen.availableGeometry().contains(QPoint(x + 20, y + 20)):
                    self.setGeometry(x, y, w, h)
                    return
        area = QGuiApplication.primaryScreen().availableGeometry()
        w, h = 380, 500
        self.setGeometry(area.right() - w - 20, area.bottom() - h - 20, w, h)

    def _emit_geometry(self):
        g = self.geometry()
        self.geometry_changed.emit([g.x(), g.y(), g.width(), g.height()])

    def moveEvent(self, e):
        self._save_timer.start()
        super().moveEvent(e)

    def resizeEvent(self, e):
        self._save_timer.start()
        super().resizeEvent(e)

    # --- WinAPI флаги ---
    def showEvent(self, e):
        super().showEvent(e)
        self.apply_window_flags()

    def apply_window_flags(self):
        hwnd = int(self.winId())
        winapi.hide_from_taskbar(hwnd)
        self.capture_hidden_ok = winapi.set_capture_hidden(hwnd, self.hide_from_capture)

    # --- отрисовка стекла ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS, RADIUS)
        p.fillPath(path, BG_COLOR)
        p.setPen(QPen(BORDER_COLOR, 1.2))
        p.drawPath(path)
        p.end()

    # --- перетаскивание за шапку ---
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.header.geometry().contains(e.position().toPoint()):
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

    # --- состояние ---
    def set_status(self, text):
        self.status.setText(text)

    def set_busy(self, model, note=""):
        self._busy_model = model.split("/")[-1] + (f" ({note})" if note else "")
        self._dots = 0
        self._busy_since = time.monotonic()
        self._busy_status = None
        self._set_busy_buttons(True)
        self._busy_timer.start()
        self._tick_busy()

    def _set_busy_buttons(self, busy):
        self.btn_copy.setVisible(not busy)
        self.btn_stop.setVisible(busy)
        if busy:
            self.btn_copy.setEnabled(False)

    def _tick_busy(self):
        self._dots = (self._dots + 1) % 4
        seconds = int(time.monotonic() - self._busy_since)
        self.view.setMarkdown(f"### 🪲 Думаю{'.' * self._dots}  {seconds} с")
        self.set_status(self._busy_status or f"Модель: {self._busy_model}")

    def set_busy_status(self, text):
        """Текст в строке состояния, пока идёт запрос (например, «Распознаю текст…»)."""
        self._busy_status = text
        if self._busy_timer.isActive():
            self.set_status(text)

    def show_answer(self, text):
        self._busy_timer.stop()
        self._set_busy_buttons(False)
        text = latex_to_plain(text)  # модели любят писать формулы как $17 \times 3$
        self._answer_text = text
        self.view.setMarkdown(keep_line_breaks(text))
        self.btn_copy.setEnabled(True)

    def show_error(self, text):
        self._busy_timer.stop()
        self._set_busy_buttons(False)
        self._answer_text = ""
        self.view.setMarkdown(f"**⚠ Ошибка**\n\n{text}")
        self.btn_copy.setEnabled(False)

    def show_message(self, markdown):
        self._busy_timer.stop()
        self._set_busy_buttons(False)
        self.view.setMarkdown(markdown)

    def _copy(self):
        if self._answer_text:
            QGuiApplication.clipboard().setText(self._answer_text)
            self.btn_copy.setText("Скопировано ✓")
            QTimer.singleShot(1500, lambda: self.btn_copy.setText("Копировать"))
