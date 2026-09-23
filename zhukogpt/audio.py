"""Запись звука для Interview-режима: звук компьютера (WASAPI loopback) или микрофон.

Запись идёт в фоне через callback PortAudio; на выходе — WAV 16 кГц моно, этого хватает Whisper
и файл получается маленьким (≈32 КБ в секунду).
"""
import array
import io
import math
import sys
import time
import warnings
import wave

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop  # быстрая обработка на C; в Python 3.13+ его нет — тогда медленный путь
    except ImportError:
        audioop = None

SOURCES = {
    "loopback": "🔊 Звук ПК",
    "mic": "🎤 Микрофон",
}
TARGET_RATE = 16000
MAX_SECONDS = 180  # дольше — автоматически останавливаем и отправляем
SILENCE_RMS = 60  # ниже этого уровня (из 32767) считаем, что звука не было


class AudioError(RuntimeError):
    pass


def _pyaudio():
    if sys.platform != "win32":
        raise AudioError("Запись звука работает только в Windows.")
    try:
        import pyaudiowpatch
    except ImportError as e:
        raise AudioError(f"Не найден модуль записи звука ({e}). Переустанови ZhukoGPT.") from e
    return pyaudiowpatch


def _loopback_device(pa, pyaudio):
    """Устройство «то, что сейчас играет в динамиках/наушниках по умолчанию»."""
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as e:
        raise AudioError("В Windows не найден WASAPI — звук компьютера записать нельзя.") from e
    speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if speakers.get("isLoopbackDevice"):
        return speakers
    for dev in pa.get_loopback_device_info_generator():
        if speakers["name"] in dev["name"]:
            return dev
    raise AudioError(f"Не удалось подключиться к звуку устройства «{speakers['name']}». "
                     "Попробуй источник «Микрофон» или другое устройство вывода.")


def _mic_device(pa):
    try:
        return pa.get_default_input_device_info()
    except OSError as e:
        raise AudioError("Микрофон не найден. Подключи его или разреши доступ: "
                         "Параметры → Конфиденциальность → Микрофон.") from e


class Recorder:
    """Запись одного фрагмента: start() → … → stop() возвращает WAV (или None, если была тишина)."""

    def __init__(self):
        self._pa = None
        self._stream = None
        self._chunks = []
        self._channels = 1
        self._rate = TARGET_RATE
        self._started = 0.0
        self.level = 0.0  # 0…1, громкость последнего кусочка — для индикатора
        self.peak = 0  # максимальный RMS за запись
        self.device_name = ""

    @property
    def active(self):
        return self._stream is not None

    def elapsed(self):
        return time.monotonic() - self._started if self.active else 0.0

    def start(self, source="loopback"):
        pyaudio = _pyaudio()
        self._pa = pyaudio.PyAudio()
        try:
            dev = _loopback_device(self._pa, pyaudio) if source == "loopback" else _mic_device(self._pa)
            self._channels = max(1, min(2, int(dev["maxInputChannels"])))
            self._rate = int(dev["defaultSampleRate"])
            self.device_name = dev["name"]
            self._chunks = []
            self.level = 0.0
            self.peak = 0
            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=self._channels, rate=self._rate, input=True,
                input_device_index=dev["index"], frames_per_buffer=int(self._rate / 20),
                stream_callback=self._callback(pyaudio))
        except AudioError:
            self._close()
            raise
        except Exception as e:
            self._close()
            raise AudioError(f"Не удалось начать запись: {e}") from e
        self._started = time.monotonic()

    def _callback(self, pyaudio):
        def callback(data, frame_count, time_info, status):
            # Вызывается из потока PortAudio: только складываем данные, list.append потокобезопасен.
            self._chunks.append(data)
            rms = _rms(data)
            self.peak = max(self.peak, rms)
            self.level = min(1.0, rms / 6000)
            return None, pyaudio.paContinue
        return callback

    def stop(self):
        """Останавливает запись. Возвращает WAV 16 кГц моно или None, если звука не было."""
        self._close()
        data = b"".join(self._chunks)
        self._chunks = []
        if not data or self.peak < SILENCE_RMS:
            return None
        return to_wav(data, self._rate, self._channels)

    def cancel(self):
        self._close()
        self._chunks = []

    def _close(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        pa, self._pa = self._pa, None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


def _rms(data: bytes) -> int:
    if audioop is not None:
        return audioop.rms(data[:len(data) // 2 * 2], 2)
    samples = array.array("h", data[:len(data) // 2 * 2])
    if not samples:
        return 0
    step = max(1, len(samples) // 400)  # для индикатора хватает части сэмплов
    part = samples[::step]
    return int(math.sqrt(sum(s * s for s in part) / len(part)))


def to_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    """PCM int16 (любая частота, 1–2 канала) → WAV 16 кГц моно."""
    pcm = pcm[:len(pcm) // (2 * channels) * 2 * channels]
    if audioop is not None:
        mono = audioop.tomono(pcm, 2, 0.5, 0.5) if channels == 2 else pcm
        if rate != TARGET_RATE:
            mono = audioop.ratecv(mono, 2, 1, rate, TARGET_RATE, None)[0]
        return _wav(mono)
    samples = array.array("h", pcm[:len(pcm) // (2 * channels) * 2 * channels])
    if channels == 2:
        mono = array.array("h", ((a + b) >> 1 for a, b in zip(samples[0::2], samples[1::2])))
    else:
        mono = samples
    if rate != TARGET_RATE and mono:
        # Усреднение по окну и выборка каждого n-го: для речи этого достаточно.
        ratio = rate / TARGET_RATE
        n = len(mono)
        out_len = int(n / ratio)
        width = max(1, int(ratio))
        out = array.array("h", bytes(2 * out_len))
        for i in range(out_len):
            start = int(i * ratio)
            window = mono[start:start + width]
            out[i] = sum(window) // len(window)
        mono = out
    return _wav(mono.tobytes())


def _wav(mono: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(mono)
    return buf.getvalue()


def selftest(out_path) -> int:
    """ZhukoGPT.exe --audio-selftest out.txt — модуль записи звука попал в exe и запускается."""
    lines = []
    code = 0
    try:
        pyaudio = _pyaudio()
        pa = pyaudio.PyAudio()
        try:
            lines.append(f"PortAudio: {pyaudio.get_portaudio_version_text()}")
            lines.append(f"устройств: {pa.get_device_count()}")
            try:
                lines.append(f"loopback: {_loopback_device(pa, pyaudio)['name']}")
            except AudioError as e:
                lines.append(f"loopback: нет ({e})")
        finally:
            pa.terminate()
    except Exception as e:
        lines.append(f"ОШИБКА: {e}")
        code = 1
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code
