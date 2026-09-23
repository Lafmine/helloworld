"""Запросы к ИИ-сервисам (OpenRouter, TeamoRouter) — оба совместимы с API OpenAI."""
import base64
import json
import threading
import time
from datetime import datetime

import requests
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QThread, Qt, Signal
from PySide6.QtGui import QImage

from . import ocr
from .config import (APP_NAME, PROVIDERS, SYSTEM_PROMPT, SYSTEM_PROMPT_TEXT, USER_PROMPT,
                     USER_PROMPT_TEXT)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
TEAMO_PROBE_MODEL = "deepseek-v4-flash-free"
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 20  # сколько сервер может молчать совсем без байтов
MODEL_DEADLINE = 45  # секунд на одну модель, потом — следующая
MAX_FALLBACKS = 2  # сколько запасных моделей пробовать после выбранной
MAX_IMAGE_SIDE = 1600

_EXCLUDE_WORDS = ("safety", "guard")
# При этих ошибках имеет смысл попробовать другую модель.
_RETRYABLE = {"upstream", "unavailable", "server", "timeout", "empty"}


class ApiError(RuntimeError):
    """Ошибка сервиса: kind — тип, detail — оригинальный текст от сервера."""

    def __init__(self, kind, message, detail="", status=None, reset_ms=None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.status = status
        self.reset_ms = reset_ms
        self.source = "сервиса"

    def full_text(self):
        text = str(self)
        if self.detail:
            safe = "".join("\\" + ch if ch in "*_`[]<>#" else ch for ch in self.detail)
            text += f"\n\n*Подробности от {self.source}: {safe}*"
        return text


def provider_name(provider: str) -> str:
    return PROVIDERS[provider]["name"]


def has_vision(provider: str, model: str) -> bool:
    """Видит ли модель картинки. Иначе ей отправляется текст, распознанный OCR."""
    if provider == "openrouter":
        return True  # в списке OpenRouter только модели с картинками
    name = model.lower()
    return "vision" in name or "-vl" in name or name.endswith("vl")


def _headers(provider, api_key):
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        headers["X-Title"] = APP_NAME
        headers["HTTP-Referer"] = "https://github.com/lafmine/helloworld"
    return headers


def build_payload(provider: str, model: str, png_bytes: bytes = None, text: str = None) -> dict:
    """Запрос с картинкой или, для моделей без зрения, с распознанным текстом."""
    if text is not None:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_TEXT},
            {"role": "user", "content": USER_PROMPT_TEXT + text},
        ]
    else:
        image_b64 = base64.b64encode(png_bytes).decode("ascii")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ]
    payload = {"model": model, "messages": messages}
    if provider == "openrouter":
        # «Думающие» модели рассуждают короче, а текст рассуждений не попадает в ответ.
        payload["reasoning"] = {"effort": "low", "exclude": True}
    return payload


def downscale_png(png_bytes: bytes, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    """Уменьшает картинку, если она больше max_side: меньше токенов — реже упираемся в лимиты."""
    img = QImage.fromData(png_bytes, "PNG")
    if img.isNull() or max(img.width(), img.height()) <= max_side:
        return png_bytes
    img = img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def _format_reset(reset_ms):
    """Время сброса лимита в местном времени, например «03:00»."""
    try:
        return datetime.fromtimestamp(int(reset_ms) / 1000).strftime("%H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _error_detail(error: dict) -> str:
    parts = []
    if error.get("message"):
        parts.append(str(error["message"]))
    meta = error.get("metadata") or {}
    if meta.get("provider_name"):
        parts.append(f"провайдер: {meta['provider_name']}")
    if meta.get("raw"):
        parts.append(str(meta["raw"])[:300])
    return " • ".join(parts)


def _is_balance_error(error: dict, text: str) -> bool:
    markers = f"{error.get('type', '')} {error.get('code', '')}".lower()
    return "insufficient_balance" in markers or "balance is insufficient" in text


def classify_error(status, error: dict, headers=None, provider="openrouter") -> ApiError:
    """Превращает ошибку сервиса в ApiError с понятным русским текстом."""
    headers = headers or {}
    name = provider_name(provider)
    detail = _error_detail(error)
    text = detail.lower()
    meta = error.get("metadata") or {}
    reset_ms = headers.get("X-RateLimit-Reset")

    if _is_balance_error(error, text):
        return ApiError("balance", f"На балансе {name} нет денег. Даже бесплатные (free) модели там работают "
                        f"только при ненулевом балансе. Пополнить: {PROVIDERS[provider]['keys_page']}",
                        detail, status)
    if status == 401:
        return ApiError("auth", f"Неверный API-ключ {name}. Проверь его в настройках ⚙ (кнопка «Проверить»).",
                        detail, status)
    if status == 402:
        return ApiError("credits", f"{name} просит пополнить баланс. Для бесплатных моделей это значит, что "
                        "ключ создан с лимитом 0 или модель стала платной. Выбери другую модель или проверь ключ.",
                        detail, status)
    if status == 403:
        return ApiError("other", f"{name} отклонил запрос (модерация или ограничения ключа).", detail, status)
    if status == 404 or "does not support image" in text:
        return ApiError("unavailable", "Модель сейчас недоступна.", detail, status)
    if status == 408:
        return ApiError("timeout", "Модель не успела ответить.", detail, status)
    if status == 429:
        if provider == "openrouter" and ("per-day" in text or "per day" in text or "daily" in text):
            reset = _format_reset(reset_ms)
            when = f" Лимит обновится в {reset}." if reset else " Лимит обновляется раз в сутки (в 03:00 по МСК)."
            return ApiError("daily_limit", "Закончились бесплатные запросы на сегодня для твоего аккаунта OpenRouter "
                            "(без пополнения даётся 50 запросов в день на все бесплатные модели)." + when +
                            " Если один раз пополнить OpenRouter на $10, лимит станет 1000 запросов в день.",
                            detail, status, reset_ms)
        if "upstream" in text or meta.get("provider_name"):
            return ApiError("upstream", f"Провайдер этой модели сейчас перегружен (это общий лимит "
                            f"для всех пользователей {name}, а не твой).", detail, status)
        return ApiError("minute_limit", "Слишком много запросов подряд. Подожди минуту.",
                        detail, status, reset_ms)
    if status is not None and status >= 500:
        return ApiError("server", "Сбой на стороне провайдера модели.", detail, status)
    return ApiError("other", f"Ошибка {name} ({status}).", detail, status)


class _Request:
    """Текущий HTTP-ответ, чтобы его можно было закрыть из другого потока при отмене."""

    def __init__(self):
        self.cancel_event = threading.Event()
        self._resp = None
        self._lock = threading.Lock()

    def set(self, resp):
        with self._lock:
            self._resp = resp
        if self.cancel_event.is_set():
            self.close()

    def close(self):
        with self._lock:
            resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def cancel(self):
        self.cancel_event.set()
        self.close()


def _check_cancel(req):
    if req is not None and req.cancel_event.is_set():
        raise ApiError("cancelled", "Остановлено.")


def _read_body(resp, deadline, req) -> bytes:
    """Читает тело ответа, но не дольше deadline.

    Обычный таймаут requests сбрасывается от каждого байта, а OpenRouter, пока модель думает,
    шлёт пробелы-keepalive, поэтому без общего дедлайна ответ можно ждать бесконечно.
    """
    chunks = []
    try:
        for chunk in resp.iter_content(chunk_size=None):  # куски по мере прихода, без ожидания 4 КБ
            _check_cancel(req)
            if chunk:
                chunks.append(chunk)
            if time.monotonic() > deadline:
                raise ApiError("timeout", f"Модель не ответила за {MODEL_DEADLINE} с.")
    except ApiError:
        raise
    except Exception:  # RequestException, а при отмене из другого потока — что угодно от закрытого сокета
        _check_cancel(req)
        raise ApiError("timeout", "Модель перестала отвечать (соединение оборвалось или зависло).")
    finally:
        resp.close()
    _check_cancel(req)
    return b"".join(chunks)


def _ask_once(provider: str, api_key: str, payload: dict, req=None) -> str:
    _check_cancel(req)
    deadline = time.monotonic() + MODEL_DEADLINE
    try:
        resp = requests.post(PROVIDERS[provider]["chat_url"], json=payload, headers=_headers(provider, api_key),
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True)
    except requests.Timeout:
        _check_cancel(req)
        raise ApiError("timeout", f"Модель не ответила за {MODEL_DEADLINE} с.")
    except requests.RequestException as e:
        _check_cancel(req)
        raise ApiError("network", f"Нет соединения с {provider_name(provider)}: {e}")
    if req is not None:
        req.set(resp)
    body = _read_body(resp, deadline, req)
    body_text = body.decode("utf-8", errors="replace").strip()

    try:
        data = json.loads(body_text)
    except ValueError:
        data = None
    if resp.status_code != 200:
        error = (data or {}).get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            error = {"message": body_text[:300]}
        raise classify_error(resp.status_code, error, resp.headers, provider)
    if not isinstance(data, dict):
        raise ApiError("server", f"{provider_name(provider)} вернул непонятный ответ.")
    # Сервис иногда отдаёт 200, но с ошибкой провайдера внутри.
    if isinstance(data.get("error"), dict):
        err = data["error"]
        code = err.get("code")
        raise classify_error(code if isinstance(code, int) else 502, err, resp.headers, provider)
    try:
        choice = data["choices"][0]
        if isinstance(choice.get("error"), dict):
            raise classify_error(502, choice["error"], resp.headers, provider)
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ApiError("empty", "Модель вернула пустой ответ.")
    if isinstance(content, list):  # некоторые модели отдают список частей
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = (content or "").strip()
    if not content:
        raise ApiError("empty", "Модель вернула пустой ответ.")
    return content


def _recognize_text(png_bytes: bytes) -> str:
    try:
        text = ocr.recognize(png_bytes)
    except ocr.OcrUnavailable as e:
        raise ApiError("ocr", f"Эта модель не видит картинки, а распознать текст не получилось: {e}")
    if not text.strip():
        raise ApiError("ocr_empty", "На выделенной области не найден текст. Выдели задание крупнее "
                       "или выбери модель, которая видит картинки (👁 в настройках).")
    return text


def ask_with_fallback(provider: str, api_key: str, models: list, png_bytes: bytes,
                      on_try=None, on_status=None, req=None):
    """Спрашивает первую модель, при временных сбоях — следующие. Возвращает (текст, модель)."""
    name = provider_name(provider)
    if not api_key:
        raise ApiError("auth", f"Не указан API-ключ {name}. Открой настройки ⚙ и вставь ключ.")
    candidates = []
    for m in models:
        if m and m not in candidates:
            candidates.append(m)
    candidates = candidates[:1 + MAX_FALLBACKS]

    small_png = None
    ocr_text = None  # распознаём один раз и только если понадобится
    last_error = None
    for i, model in enumerate(candidates):
        _check_cancel(req)
        if on_try and i > 0:
            on_try(model)
        try:
            if has_vision(provider, model):
                if small_png is None:
                    small_png = downscale_png(png_bytes)
                payload = build_payload(provider, model, png_bytes=small_png)
            else:
                if ocr_text is None:
                    if on_status:
                        on_status("Распознаю текст на скриншоте…")
                    ocr_text = _recognize_text(png_bytes)  # оригинал: в высоком разрешении читается лучше
                    _check_cancel(req)
                    if on_status:
                        on_status(f"Модель: {model} (по распознанному тексту)")
                payload = build_payload(provider, model, text=ocr_text)
            return _ask_once(provider, api_key, payload, req), model
        except ApiError as e:
            e.source = name
            last_error = e
            if e.kind not in _RETRYABLE:
                break  # лимит аккаунта / баланс / ключ: другие модели не помогут
    if last_error.kind in _RETRYABLE and len(candidates) > 1:
        last_error.args = (f"{last_error} Пробовал модели: "
                           + ", ".join(m.split('/')[-1] for m in candidates) + ". Попробуй чуть позже.",)
    raise last_error


def check_key(provider: str, api_key: str) -> dict:
    """Проверка ключа выбранного сервиса."""
    if not api_key:
        return {"valid": False, "error": "Ключ не указан"}
    if provider == "teamorouter":
        return _check_teamo_key(api_key)
    try:
        resp = requests.get(OPENROUTER_KEY_URL, headers=_headers(provider, api_key), timeout=20)
    except requests.RequestException as e:
        return {"valid": None, "error": f"Нет соединения: {e}"}
    if resp.status_code == 401:
        return {"valid": False, "error": "Неверный ключ"}
    if resp.status_code != 200:
        return {"valid": None, "error": f"OpenRouter ответил {resp.status_code}"}
    try:
        data = resp.json().get("data") or {}
    except ValueError:
        return {"valid": None, "error": "Непонятный ответ OpenRouter"}
    daily = data.get("free_model_daily_requests") or {}
    is_free_tier = data.get("is_free_tier")
    limit = daily.get("limit")
    if limit is None and is_free_tier is not None:
        limit = 50 if is_free_tier else 1000
    return {
        "valid": True,
        "is_free_tier": is_free_tier,
        "used": daily.get("used"),
        "limit": limit,
        "remaining": daily.get("remaining"),
        "key_limit_remaining": data.get("limit_remaining"),
    }


def _check_teamo_key(api_key: str) -> dict:
    headers = _headers("teamorouter", api_key)
    try:
        resp = requests.get(PROVIDERS["teamorouter"]["models_url"], headers=headers, timeout=20)
    except requests.RequestException as e:
        return {"valid": None, "error": f"Нет соединения: {e}"}
    if resp.status_code == 401:
        return {"valid": False, "error": "Неверный ключ"}
    if resp.status_code != 200:
        return {"valid": None, "error": f"TeamoRouter ответил {resp.status_code}"}
    # Пробный крошечный запрос к бесплатной модели: так видно, пускает ли сервис без баланса.
    try:
        probe = requests.post(PROVIDERS["teamorouter"]["chat_url"], headers=headers, timeout=30, json={
            "model": TEAMO_PROBE_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "1"}]})
        error = probe.json().get("error") if probe.status_code != 200 else None
    except (requests.RequestException, ValueError):
        return {"valid": True}
    if isinstance(error, dict) and _is_balance_error(error, str(error.get("message", "")).lower()):
        return {"valid": True, "balance_needed": True}
    return {"valid": True, "free_ok": probe.status_code == 200}


def describe_key(info: dict) -> str:
    if info.get("valid") is False:
        return f"✗ {info.get('error', 'Неверный ключ')}"
    if info.get("valid") is None:
        return f"? Не удалось проверить: {info.get('error', '')}"
    parts = ["✓ Ключ работает"]
    if info.get("balance_needed"):
        parts.append("⚠ на балансе 0 — даже free-модели не ответят, пока баланс не пополнен")
    elif info.get("free_ok"):
        parts.append("бесплатные модели отвечают")
    used, limit, remaining = info.get("used"), info.get("limit"), info.get("remaining")
    if remaining is None and used is not None and limit is not None:
        remaining = max(0, limit - used)
    if remaining is not None and limit is not None:
        parts.append(f"бесплатных запросов сегодня осталось: {remaining} из {limit}")
    elif limit is not None:
        parts.append(f"лимит бесплатных запросов: {limit} в день")
    if info.get("key_limit_remaining") == 0:
        parts.append("⚠ у ключа исчерпан лимит расходов")
    return " • ".join(parts)


def fetch_models(provider: str, api_key: str = "") -> list:
    """Актуальный список подходящих моделей: бесплатные (и со зрением у TeamoRouter)."""
    info = PROVIDERS[provider]
    headers = _headers(provider, api_key) if api_key else {}
    resp = requests.get(info["models_url"], headers=headers, timeout=30)
    if resp.status_code == 401:
        raise RuntimeError("нужен рабочий API-ключ")
    resp.raise_for_status()
    result = []
    for m in resp.json().get("data", []):
        model_id = m.get("id", "")
        if any(word in model_id.lower() for word in _EXCLUDE_WORDS):
            continue
        if provider == "openrouter":
            modalities = (m.get("architecture") or {}).get("input_modalities") or []
            if model_id.endswith(":free") and "image" in modalities:
                result.append(model_id)
        elif model_id.endswith("-free") or has_vision(provider, model_id):
            result.append(model_id)
    if provider == "openrouter":
        return sorted(result)
    return sorted(result, key=lambda mid: (not mid.endswith("-free"), mid))  # бесплатные сверху


class AskWorker(QThread):
    trying = Signal(str)
    status = Signal(str)
    finished_ok = Signal(str, str)  # текст, модель которая ответила
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, provider, api_key, models, png_bytes, parent=None):
        super().__init__(parent)
        self._args = (provider, api_key, list(models), png_bytes)
        self._req = _Request()

    def cancel(self):
        """Останавливает запрос: закрывает соединение и не пробует запасные модели."""
        self._req.cancel()

    def run(self):
        provider, api_key, models, png = self._args
        try:
            text, model = ask_with_fallback(provider, api_key, models, png, on_try=self.trying.emit,
                                            on_status=self.status.emit, req=self._req)
            self.finished_ok.emit(text, model)
        except ApiError as e:
            if e.kind == "cancelled" or self._req.cancel_event.is_set():
                self.cancelled.emit()
                return
            message = e.full_text()
            if e.kind == "daily_limit":
                info = check_key(provider, api_key)
                if info.get("valid"):
                    message += f"\n\n{describe_key(info)}"
            self.failed.emit(message)
        except Exception as e:  # чтобы поток никогда не падал молча
            if self._req.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(f"Непредвиденная ошибка: {e}")


class KeyWorker(QThread):
    finished_ok = Signal(dict)

    def __init__(self, provider, api_key, parent=None):
        super().__init__(parent)
        self._args = (provider, api_key)

    def run(self):
        try:
            self.finished_ok.emit(check_key(*self._args))
        except Exception as e:
            self.finished_ok.emit({"valid": None, "error": str(e)})


class ModelsWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def __init__(self, provider, api_key="", parent=None):
        super().__init__(parent)
        self._args = (provider, api_key)

    def run(self):
        try:
            self.finished_ok.emit(fetch_models(*self._args))
        except Exception as e:
            self.failed.emit(str(e))
