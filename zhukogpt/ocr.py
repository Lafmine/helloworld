"""Распознавание текста со скриншота встроенным OCR Windows (Windows.Media.Ocr)."""
import asyncio
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

MAX_LANGUAGES = 3  # на скольких языках Windows прогонять OCR
MIN_HEIGHT_FOR_OCR = 900  # мелкие картинки увеличиваем: OCR лучше читает крупный текст


class OcrUnavailable(RuntimeError):
    """OCR не может работать на этом компьютере (не Windows, нет пакетов или языков)."""


class OcrModuleMissing(OcrUnavailable):
    """Нет пакетов winrt — значит, ошибка сборки, а не настроек Windows."""


class OcrNoLanguages(OcrUnavailable):
    """В Windows не установлен ни один язык для распознавания."""


def _winrt():
    if sys.platform != "win32":
        raise OcrUnavailable("Распознавание текста работает только в Windows.")
    try:
        from winrt.windows.globalization import Language  # noqa: F401
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except ImportError as e:
        raise OcrModuleMissing(f"Не найден модуль распознавания текста Windows ({e}).")
    return OcrEngine, SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode, DataWriter


def available_languages() -> list:
    """Теги языков OCR, установленных в Windows, например ['ru', 'en-US']."""
    OcrEngine = _winrt()[0]
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def _prepare(png_bytes: bytes, max_dim: int) -> QImage:
    img = QImage.fromData(png_bytes, "PNG")
    if img.isNull():
        raise OcrUnavailable("Не удалось прочитать скриншот.")
    if img.height() < MIN_HEIGHT_FOR_OCR:
        scale = min(2.0, max_dim / max(img.width(), img.height()))
        if scale > 1.05:
            img = img.scaled(int(img.width() * scale), int(img.height() * scale),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if max(img.width(), img.height()) > max_dim:
        img = img.scaled(max_dim, max_dim, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Premultiplied ARGB32 в памяти лежит как BGRA — ровно то, что ждёт SoftwareBitmap.
    return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)


async def _recognize_async(img: QImage) -> list:
    OcrEngine, SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode, DataWriter = _winrt()
    languages = list(OcrEngine.available_recognizer_languages)
    if not languages:
        raise OcrNoLanguages(
            "В Windows не установлен ни один язык для распознавания текста. Добавь язык: "
            "Параметры → Время и язык → Язык и регион → Добавить язык (например, русский).")

    width, height = img.width(), img.height()
    raw = bytes(img.constBits())  # у 32-битных форматов строка всегда ровно width * 4 байт
    writer = DataWriter()
    writer.write_bytes(raw)
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.PREMULTIPLIED)

    results = []
    for lang in languages[:MAX_LANGUAGES]:
        engine = OcrEngine.try_create_from_language(lang)
        if engine is None:
            continue
        result = await engine.recognize_async(bitmap)
        text = "\n".join(line.text for line in result.lines).strip()
        results.append((lang.language_tag, text))
    return results


def recognize(png_bytes: bytes) -> str:
    """Текст со скриншота. Если языки дали разный текст — все варианты с пометкой языка."""
    OcrEngine = _winrt()[0]
    try:
        max_dim = int(OcrEngine.max_image_dimension)
    except Exception:
        max_dim = 4000
    img = _prepare(png_bytes, max_dim)
    try:
        results = asyncio.run(_recognize_async(img))
    except OcrUnavailable:
        raise
    except Exception as e:
        raise OcrUnavailable(f"Ошибка распознавания текста Windows: {e}") from e

    variants = []
    for tag, text in results:
        if text and all(text != other for _, other in variants):
            variants.append((tag, text))
    if not variants:
        return ""
    if len(variants) == 1:
        return variants[0][1]
    return "\n\n".join(f"[{tag}]\n{text}" for tag, text in variants)


def selftest(png_path: str, out_path: str) -> int:
    """Режим проверки для CI: ZhukoGPT.exe --ocr-selftest in.png out.txt."""
    lines = []
    code = 0
    try:
        lines.append("LANGS: " + ", ".join(available_languages()))
        with open(png_path, "rb") as f:
            text = recognize(f.read())
        lines.append("TEXT:")
        lines.append(text)
        code = 0 if text.strip() else 3
    except OcrModuleMissing as e:
        lines.append(f"MODULE MISSING: {e}")
        code = 4
    except OcrNoLanguages as e:
        lines.append(f"NO LANGUAGES: {e}")
        code = 2
    except Exception as e:  # отчёт должен появиться в любом случае
        lines.append(f"ERROR: {type(e).__name__}: {e}")
        lines.append(traceback.format_exc())
        code = 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code
