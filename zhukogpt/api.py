"""Работа с OpenRouter."""
import base64
from datetime import datetime

import requests
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QThread, Qt, Signal
from PySide6.QtGui import QImage

from .config import APP_NAME, SYSTEM_PROMPT, USER_PROMPT

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
KEY_URL = "https://openrouter.ai/api/v1/key"
TIMEOUT = 120
MAX_FALLBACKS = 2  # сколько запасных моделей пробовать после выбранной
MAX_IMAGE_SIDE = 1600

_EXCLUDE_WORDS = ("safety", "guard")
# При этих ошибках имеет смысл попробовать другую модель.
_RETRYABLE = {"upstream", "unavailable", "server", "timeout", "empty"}


class ApiError(RuntimeError):
    """Ошибка OpenRouter: kind — тип, detail — оригинальный текст от сервера."""

    def __init__(self, kind, message, detail="", status=None, reset_ms=None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.status = status
        self.reset_ms = reset_ms

    def full_text(self):
        text = str(self)
        if self.detail:
            safe = "".join("\\" + ch if ch in "*_`[]<>#" else ch for ch in self.detail)
            text += f"\n\n*Подробности от OpenRouter: {safe}*"
        return text


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Title": APP_NAME,
        "HTTP-Referer": "https://github.com/lafmine/helloworld",
    }


def build_payload(model: str, png_bytes: bytes) -> dict:
    image_b64 = base64.b64encode(png_bytes).decode("ascii")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            },
        ],
    }


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


def classify_error(status, error: dict, headers=None) -> ApiError:
    """Превращает ошибку OpenRouter в ApiError с понятным русским текстом."""
    headers = headers or {}
    detail = _error_detail(error)
    text = detail.lower()
    meta = error.get("metadata") or {}
    reset_ms = headers.get("X-RateLimit-Reset")

    if status == 401:
        return ApiError("auth", "Неверный API-ключ OpenRouter. Проверь его в настройках ⚙ (кнопка «Проверить ключ»).",
                        detail, status)
    if status == 402:
        return ApiError("credits", "OpenRouter просит пополнить баланс. Для бесплатных моделей это значит, что "
                        "ключ создан с лимитом 0 или модель стала платной. Выбери другую модель или проверь ключ.",
                        detail, status)
    if status == 403:
        return ApiError("other", "OpenRouter отклонил запрос (модерация или ограничения ключа).", detail, status)
    if status == 404:
        return ApiError("unavailable", "Модель сейчас недоступна.", detail, status)
    if status == 408:
        return ApiError("timeout", "Модель не успела ответить.", detail, status)
    if status == 429:
        if "per-day" in text or "per day" in text or "daily" in text or "free-models-per-day" in text:
            reset = _format_reset(reset_ms)
            when = f" Лимит обновится в {reset}." if reset else " Лимит обновляется раз в сутки (в 03:00 по МСК)."
            return ApiError("daily_limit", "Закончились бесплатные запросы на сегодня для твоего аккаунта OpenRouter "
                            "(без пополнения даётся 50 запросов в день на все бесплатные модели)." + when +
                            " Если один раз пополнить OpenRouter на $10, лимит станет 1000 запросов в день.",
                            detail, status, reset_ms)
        if "upstream" in text or meta.get("provider_name"):
            return ApiError("upstream", "Провайдер этой бесплатной модели сейчас перегружен (это общий лимит "
                            "для всех пользователей OpenRouter, а не твой).", detail, status)
        return ApiError("minute_limit", "Слишком много запросов за минуту (лимит 20 в минуту). Подожди минуту.",
                        detail, status, reset_ms)
    if status is not None and status >= 500:
        return ApiError("server", "Сбой на стороне провайдера модели.", detail, status)
    return ApiError("other", f"Ошибка OpenRouter ({status}).", detail, status)


def _ask_once(api_key: str, model: str, png_bytes: bytes) -> str:
    try:
        resp = requests.post(API_URL, json=build_payload(model, png_bytes), headers=_headers(api_key),
                             timeout=TIMEOUT)
    except requests.Timeout:
        raise ApiError("timeout", "Модель слишком долго думает (таймаут).")
    except requests.RequestException as e:
        raise ApiError("network", f"Нет соединения с OpenRouter: {e}")

    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code != 200:
        error = (data or {}).get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            error = {"message": resp.text[:300]}
        raise classify_error(resp.status_code, error, resp.headers)
    if not isinstance(data, dict):
        raise ApiError("server", "OpenRouter вернул непонятный ответ.")
    # OpenRouter иногда отдаёт 200, но с ошибкой провайдера внутри.
    if isinstance(data.get("error"), dict):
        err = data["error"]
        code = err.get("code")
        raise classify_error(code if isinstance(code, int) else 502, err, resp.headers)
    try:
        choice = data["choices"][0]
        if isinstance(choice.get("error"), dict):
            raise classify_error(502, choice["error"], resp.headers)
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ApiError("empty", "Модель вернула пустой ответ.")
    if isinstance(content, list):  # некоторые модели отдают список частей
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = (content or "").strip()
    if not content:
        raise ApiError("empty", "Модель вернула пустой ответ.")
    return content


def ask_with_fallback(api_key: str, models: list, png_bytes: bytes, on_try=None):
    """Спрашивает первую модель, при временных сбоях — следующие. Возвращает (текст, модель)."""
    if not api_key:
        raise ApiError("auth", "Не указан API-ключ OpenRouter. Открой настройки ⚙ и вставь ключ.")
    candidates = []
    for m in models:
        if m and m not in candidates:
            candidates.append(m)
    candidates = candidates[:1 + MAX_FALLBACKS]
    png_bytes = downscale_png(png_bytes)

    last_error = None
    for i, model in enumerate(candidates):
        if on_try and i > 0:
            on_try(model)
        try:
            return _ask_once(api_key, model, png_bytes), model
        except ApiError as e:
            last_error = e
            if e.kind not in _RETRYABLE:
                break  # лимит аккаунта / ключ: другие модели не помогут
    if last_error.kind in _RETRYABLE and len(candidates) > 1:
        last_error.args = (f"{last_error} Пробовал модели: "
                           + ", ".join(m.split('/')[-1] for m in candidates) + ". Попробуй чуть позже.",)
    raise last_error


def ask(api_key: str, model: str, png_bytes: bytes) -> str:
    """Один запрос к одной модели (без запасных)."""
    return ask_with_fallback(api_key, [model], png_bytes)[0]


def check_key(api_key: str) -> dict:
    """Проверка ключа: {'valid', 'is_free_tier', 'used', 'limit', 'remaining'}."""
    if not api_key:
        return {"valid": False, "error": "Ключ не указан"}
    try:
        resp = requests.get(KEY_URL, headers=_headers(api_key), timeout=20)
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


def describe_key(info: dict) -> str:
    if info.get("valid") is False:
        return f"✗ {info.get('error', 'Неверный ключ')}"
    if info.get("valid") is None:
        return f"? Не удалось проверить: {info.get('error', '')}"
    parts = ["✓ Ключ работает"]
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


def fetch_free_vision_models() -> list:
    """Список бесплатных моделей OpenRouter, которые понимают картинки."""
    resp = requests.get(MODELS_URL, timeout=30)
    resp.raise_for_status()
    result = []
    for m in resp.json().get("data", []):
        model_id = m.get("id", "")
        modalities = (m.get("architecture") or {}).get("input_modalities") or []
        if not model_id.endswith(":free") or "image" not in modalities:
            continue
        if any(word in model_id.lower() for word in _EXCLUDE_WORDS):
            continue
        result.append(model_id)
    return sorted(result)


class AskWorker(QThread):
    trying = Signal(str)
    finished_ok = Signal(str, str)  # текст, модель которая ответила
    failed = Signal(str)

    def __init__(self, api_key, models, png_bytes, parent=None):
        super().__init__(parent)
        self._args = (api_key, list(models), png_bytes)

    def run(self):
        api_key, models, png = self._args
        try:
            text, model = ask_with_fallback(api_key, models, png, on_try=self.trying.emit)
            self.finished_ok.emit(text, model)
        except ApiError as e:
            message = e.full_text()
            if e.kind == "daily_limit":
                info = check_key(api_key)
                if info.get("valid"):
                    message += f"\n\n{describe_key(info)}"
            self.failed.emit(message)
        except Exception as e:  # чтобы поток никогда не падал молча
            self.failed.emit(f"Непредвиденная ошибка: {e}")


class KeyWorker(QThread):
    finished_ok = Signal(dict)

    def __init__(self, api_key, parent=None):
        super().__init__(parent)
        self._api_key = api_key

    def run(self):
        try:
            self.finished_ok.emit(check_key(self._api_key))
        except Exception as e:
            self.finished_ok.emit({"valid": None, "error": str(e)})


class ModelsWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def run(self):
        try:
            self.finished_ok.emit(fetch_free_vision_models())
        except Exception as e:
            self.failed.emit(str(e))
