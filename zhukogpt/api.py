"""Работа с OpenRouter."""
import base64

import requests
from PySide6.QtCore import QThread, Signal

from .config import APP_NAME, SYSTEM_PROMPT, USER_PROMPT

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT = 120

_EXCLUDE_WORDS = ("safety", "guard")


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


def _error_text(resp: requests.Response) -> str:
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = resp.text[:300]
    code = resp.status_code
    if code == 401:
        return "Неверный API-ключ OpenRouter. Проверь его в настройках ⚙."
    if code == 402:
        return "OpenRouter требует пополнить баланс для этой модели. Выбери другую бесплатную модель."
    if code == 429:
        return "Слишком много запросов к бесплатной модели (лимит). Подожди минуту или выбери другую модель."
    if code == 404:
        return f"Модель недоступна. Нажми «Обновить» в настройках и выбери другую.\n\n{detail}"
    return f"Ошибка OpenRouter ({code}): {detail}"


def ask(api_key: str, model: str, png_bytes: bytes) -> str:
    """Отправляет скриншот и возвращает текст ответа. Бросает RuntimeError с понятным текстом."""
    if not api_key:
        raise RuntimeError("Не указан API-ключ OpenRouter. Открой настройки ⚙ и вставь ключ.")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Title": APP_NAME,
        "HTTP-Referer": "https://github.com/lafmine/helloworld",
    }
    try:
        resp = requests.post(API_URL, json=build_payload(model, png_bytes), headers=headers, timeout=TIMEOUT)
    except requests.Timeout:
        raise RuntimeError("Модель слишком долго думает (таймаут). Попробуй ещё раз или выбери другую.")
    except requests.RequestException as e:
        raise RuntimeError(f"Нет соединения с OpenRouter: {e}")
    if resp.status_code != 200:
        raise RuntimeError(_error_text(resp))
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("OpenRouter вернул непонятный ответ.")
    if "error" in data:
        raise RuntimeError(f"Ошибка модели: {data['error'].get('message', data['error'])}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Модель вернула пустой ответ. Попробуй ещё раз.")
    if isinstance(content, list):  # некоторые модели отдают список частей
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    content = (content or "").strip()
    if not content:
        raise RuntimeError("Модель вернула пустой ответ. Попробуй ещё раз.")
    return content


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
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key, model, png_bytes, parent=None):
        super().__init__(parent)
        self._args = (api_key, model, png_bytes)

    def run(self):
        try:
            self.finished_ok.emit(ask(*self._args))
        except RuntimeError as e:
            self.failed.emit(str(e))
        except Exception as e:  # чтобы поток никогда не падал молча
            self.failed.emit(f"Непредвиденная ошибка: {e}")


class ModelsWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)

    def run(self):
        try:
            self.finished_ok.emit(fetch_free_vision_models())
        except Exception as e:
            self.failed.emit(str(e))
