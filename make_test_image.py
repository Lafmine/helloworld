"""Рисует тестовую картинку с заданием для проверки OCR на CI: python make_test_image.py out.png"""
import sys

from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter


def main():
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 — QPainter требует приложение
    img = QImage(900, 220, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    font = QFont("Segoe UI")
    font.setPixelSize(40)
    p.setFont(font)
    p.setPen(QColor("black"))
    p.drawText(30, 80, "Question 3: What is 17 x 3 + 9?")
    p.drawText(30, 160, "A) 51    B) 60    C) 69    D) 42")
    p.end()
    return 0 if img.save(sys.argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main())
