"""Выделение области экрана мышкой (как «Ножницы»)."""
import mss
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

HINT = "Выдели область с заданием  •  Esc — отмена"


def grab_virtual_screen():
    """Снимок всех мониторов. Возвращает (QImage в физических пикселях, QRect рабочего стола в пикселях)."""
    with mss.mss() as sct:
        mon = sct.monitors[0]  # весь виртуальный рабочий стол
        shot = sct.grab(mon)
        img = QImage(shot.bgra, shot.width, shot.height, shot.width * 4, QImage.Format_ARGB32).copy()
    return img, QRect(mon["left"], mon["top"], mon["width"], mon["height"])


class RegionSelector(QWidget):
    """Полноэкранный стоп-кадр: пользователь выделяет прямоугольник, получаем PNG."""

    selected = Signal(bytes)
    cancelled = Signal()

    def __init__(self, image: QImage, desktop_rect: QRect):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._image = image
        self._origin = None
        self._current = None

        # Геометрия окна в логических координатах Qt, покрывающая все экраны.
        logical = QRect()
        for screen in QGuiApplication.screens():
            logical = logical.united(screen.geometry())
        if logical.isEmpty():
            logical = QRect(0, 0, image.width(), image.height())
        self.setGeometry(logical)
        self._sx = image.width() / max(1, logical.width())
        self._sy = image.height() / max(1, logical.height())

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()

    # --- рисование ---
    def _selection(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawImage(self.rect(), self._image)
        p.fillRect(self.rect(), QColor(5, 15, 40, 120))
        sel = self._selection()
        if not sel.isEmpty():
            src = QRect(int(sel.x() * self._sx), int(sel.y() * self._sy),
                        int(sel.width() * self._sx), int(sel.height() * self._sy))
            p.drawImage(sel, self._image, src)
            p.setPen(QPen(QColor("#6fd3ff"), 2))
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            label = f"{src.width()} × {src.height()}"
            p.setPen(QColor("white"))
            p.drawText(sel.bottomLeft() + QPoint(4, 18), label)
        else:
            font = p.font()
            font.setPointSize(14)
            p.setFont(font)
            box = QRect(0, 0, 460, 44)
            screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center())) or QGuiApplication.primaryScreen()
            center = self.mapFromGlobal(screen.geometry().center())
            box.moveCenter(QPoint(center.x(), center.y()))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 60, 140, 200))
            p.drawRoundedRect(box, 14, 14)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, HINT)
        p.end()

    # --- мышь и клавиатура ---
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._current = self._origin
            self.update()
        elif e.button() == Qt.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._origin is None:
            return
        self._current = e.position().toPoint()
        sel = self._selection()
        if sel.width() < 5 or sel.height() < 5:
            self._origin = self._current = None
            self.update()
            return
        src = QRect(int(sel.x() * self._sx), int(sel.y() * self._sy),
                    int(sel.width() * self._sx), int(sel.height() * self._sy))
        cropped = self._image.copy(src)
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        cropped.save(buf, "PNG")
        buf.close()
        self._finish()
        self.selected.emit(bytes(data))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self):
        self._finish()
        self.cancelled.emit()

    def _finish(self):
        self.releaseKeyboard()
        self.close()
