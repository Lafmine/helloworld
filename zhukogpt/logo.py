"""Логотип-жук, нарисованный QPainter (без внешних картинок)."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap


def draw_beetle(p: QPainter, size: float) -> None:
    s = size / 64.0  # рисуем в координатах 64x64
    p.save()
    p.scale(s, s)
    p.setRenderHint(QPainter.Antialiasing)

    leg_pen = QPen(QColor("#0b1f4a"), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(leg_pen)
    p.setBrush(Qt.NoBrush)
    # лапки
    for y, dx in ((30, 13), (38, 15), (46, 13)):
        p.drawLine(QPointF(32 - 9, y), QPointF(32 - 9 - dx, y + 4))
        p.drawLine(QPointF(32 + 9, y), QPointF(32 + 9 + dx, y + 4))
    # усики
    p.drawLine(QPointF(28, 14), QPointF(21, 5))
    p.drawLine(QPointF(36, 14), QPointF(43, 5))

    # голова
    p.setPen(QPen(QColor("#0b1f4a"), 2))
    p.setBrush(QColor("#1a3a7a"))
    p.drawEllipse(QRectF(24, 9, 16, 13))

    # тело
    grad = QLinearGradient(0, 18, 0, 60)
    grad.setColorAt(0.0, QColor("#7fe3ff"))
    grad.setColorAt(1.0, QColor("#2f6bff"))
    p.setBrush(grad)
    p.drawEllipse(QRectF(15, 18, 34, 42))

    # разделение надкрылий
    p.setPen(QPen(QColor("#0b1f4a"), 2))
    p.drawLine(QPointF(32, 19), QPointF(32, 59))

    # пятнышки
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0b1f4a"))
    for x, y, r in ((24, 30, 3.2), (40, 30, 3.2), (23, 44, 2.8), (41, 44, 2.8)):
        p.drawEllipse(QPointF(x, y), r, r)

    # блик
    p.setBrush(QColor(255, 255, 255, 110))
    p.drawEllipse(QRectF(20, 22, 7, 5))
    p.restore()


def beetle_pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    draw_beetle(p, size)
    p.end()
    return pm


def beetle_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(beetle_pixmap(size))
    return icon
