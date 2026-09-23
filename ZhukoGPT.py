"""Запуск ZhukoGPT: python ZhukoGPT.py"""
import sys


def _ocr_selftest(png_path, out_path):
    """ZhukoGPT.exe --ocr-selftest in.png out.txt — проверка распознавания текста в собранном exe."""
    from PySide6.QtGui import QGuiApplication

    from zhukogpt.ocr import selftest

    app = QGuiApplication(sys.argv[:1])  # noqa: F841 — нужен для работы QImage
    return selftest(png_path, out_path)


def _audio_selftest(out_path):
    """ZhukoGPT.exe --audio-selftest out.txt — модуль записи звука попал в exe и запускается."""
    from zhukogpt.audio import selftest

    return selftest(out_path)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--ocr-selftest":
        sys.exit(_ocr_selftest(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 3 and sys.argv[1] == "--audio-selftest":
        sys.exit(_audio_selftest(sys.argv[2]))

    from zhukogpt.main import main

    sys.exit(main())
