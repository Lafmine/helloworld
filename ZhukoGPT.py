"""Запуск ZhukoGPT: python ZhukoGPT.py"""
import sys


def _ocr_selftest(png_path, out_path):
    """ZhukoGPT.exe --ocr-selftest in.png out.txt — проверка распознавания текста в собранном exe."""
    from PySide6.QtGui import QGuiApplication

    from zhukogpt.ocr import selftest

    app = QGuiApplication(sys.argv[:1])  # noqa: F841 — нужен для работы QImage
    return selftest(png_path, out_path)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--ocr-selftest":
        sys.exit(_ocr_selftest(sys.argv[2], sys.argv[3]))

    from zhukogpt.main import main

    sys.exit(main())
