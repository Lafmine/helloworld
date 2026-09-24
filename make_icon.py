"""Рисует иконку-жука: assets/zhukogpt.ico для .exe и web/icons/*.png для веб-версии на iPhone."""
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPixmap

from zhukogpt.logo import beetle_pixmap, draw_beetle


def app_icon(size: int) -> QPixmap:
    """Иконка для экрана «Домой»: жук на непрозрачном синем фоне (iOS не любит прозрачность)."""
    pixmap = QPixmap(size, size)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    bg = QLinearGradient(0, 0, 0, size)
    bg.setColorAt(0, QColor("#bfe3ff"))
    bg.setColorAt(1, QColor("#5aa8f5"))
    p.fillRect(0, 0, size, size, bg)
    p.translate(size * 0.14, size * 0.14)
    draw_beetle(p, size * 0.72)
    p.end()
    return pixmap


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # консоль без UTF-8 не должна ронять сборку
    app = QGuiApplication(sys.argv)  # noqa: F841 — QPixmap требует приложение
    out = Path(__file__).parent / "assets"
    out.mkdir(exist_ok=True)
    pixmap = beetle_pixmap(256)
    ok = pixmap.save(str(out / "zhukogpt.ico"), "ICO") and pixmap.save(str(out / "zhukogpt.png"), "PNG")
    web = Path(__file__).parent / "web" / "icons"
    web.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        ok = app_icon(size).save(str(web / f"icon-{size}.png"), "PNG") and ok
    print("Иконки сохранены в assets/ и web/icons/" if ok else "Не удалось сохранить иконку")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
