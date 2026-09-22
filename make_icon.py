"""Рисует иконку-жука в assets/zhukogpt.ico (нужна для сборки .exe)."""
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication

from zhukogpt.logo import beetle_pixmap


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # консоль без UTF-8 не должна ронять сборку
    app = QGuiApplication(sys.argv)  # noqa: F841 — QPixmap требует приложение
    out = Path(__file__).parent / "assets"
    out.mkdir(exist_ok=True)
    pixmap = beetle_pixmap(256)
    ok = pixmap.save(str(out / "zhukogpt.ico"), "ICO") and pixmap.save(str(out / "zhukogpt.png"), "PNG")
    print("Иконка сохранена в assets/" if ok else "Не удалось сохранить иконку")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
