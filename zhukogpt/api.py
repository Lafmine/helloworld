"""Запросы к ИИ-сервисам (Groq, Gemini, OrcaRouter, NVIDIA, OpenRouter, TeamoRouter) — все совместимы с API OpenAI."""
import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QThread, Qt, Signal
from PySide6.QtGui import QImage

from . import ocr
from .config import (ACCURATE_ADDON, APP_NAME, DEFAULT_PROMPTS, INTERVIEW_PROMPT, PROVIDERS, TRANSCRIBE_PROMPT,
                     USER_PROMPT, USER_PROMPT_SPEECH, USER_PROMPT_TEXT, USER_PROMPT_TRANSCRIPT, WHISPER_MODEL,
                     WHISPER_URL, build_system_prompt)

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
TEAMO_PROBE_MODEL = "deepseek-v4-flash-free"
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 20  # сколько сервер может молчать совсем без байтов
MODEL_DEADLINE = 45  # секунд на одну модель, потом — следующая
ACCURATE_DEADLINE = 90  # в точном режиме модель думает дольше
WHISPER_DEADLINE = 60  # на расшифровку звука
MAX_FALLBACKS = 2  # сколько запасных моделей пробовать после выбранной
MAX_IMAGE_SIDE = 1600

_EXCLUDE_WORDS = ("safety", "guard")
NVIDIA_PROBE_MODEL = "deepseek-ai/deepseek-v4.1-flash"
GROQ_PROBE_MODEL = "qwen/qwen3.8-27b"
ORCA_PROBE_MODEL = "deepseek/deepseek-v4-flash-free"
# glm-5.3-flash по описанию в каталоге OrcaRouter принимает «text + image + video».
_ORCA_VISION_WORDS = ("glm-5.3-flash", "vision", "-vl")
_ORCA_NOT_CHAT = ("orcaverify", "embed", "rerank", "tts", "whisper", "image-gen", "moderation")
_GROQ_VISION_WORDS = ("qwen3.8", "vision", "-vl", "scout", "maverick")
_GROQ_NOT_CHAT = ("whisper", "orpheus", "guard", "safeguard", "allam", "tts", "distil")
_GEMINI_NOT_CHAT = ("embedding", "tts", "image", "live", "audio", "aqa", "robotics", "computer-use")
# Модели каталога NVIDIA, которые принимают картинки (по их карточкам на build.nvidia.com).
_NVIDIA_VISION_WORDS = ("deepseek-v4.1-flash", "gemma-4", "omni", "phi-3-vision", "phi-4-multimodal")
# Всё, что в каталоге NVIDIA не является чат-моделью.
_NVIDIA_NOT_CHAT = ("embed", "rerank", "reward", "retriever", "guard", "safety", "coder", "code-",
                    "parse", "clip", "diffusion", "vlm-embed", "nemoretriever", "pii", "translate")
# Фразы, которые Whisper выдаёт на тишине и шуме (выучил их из субтитров).
_WHISPER_HALLUCINATIONS = ("продолжение следует", "субтитры", "редактор субтитров", "спасибо за просмотр",
                           "thanks for watching", "thank you for watching", "подписывайтесь на канал")
# При этих ошибках имеет смысл попробовать другую модель.
_RETRYABLE = {"upstream", "unavailable", "server", "timeout", "empty"}


class ApiError(RuntimeError):
    """Ошибка сервиса: kind — тип, detail — оригинальный текст от сервера."""

    def __init__(self, kind, message, detail="", status=None, reset_ms=None):
        super().__init__(message)
        self.retry_after = None  # сколько секунд просит подождать сервис (заголовок Retry-After)
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
    if provider == "gemini":
        return name.startswith("gemini")  # все Gemini Flash мультимодальные
    if provider == "groq":
        return any(w in name for w in _GROQ_VISION_WORDS)
    if provider == "orcarouter":
        return any(w in name for w in _ORCA_VISION_WORDS)
    if provider == "nvidia" and any(w in name for w in _NVIDIA_VISION_WORDS):
        return True
    return "vision" in name or "-vl" in name or name.endswith("vl")


def _headers(provider, api_key):
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider == "openrouter":
        headers["X-Title"] = APP_NAME
        headers["HTTP-Referer"] = "https://github.com/bobopsya/zhukoGPT"
    return headers


def build_payload(provider: str, model: str, png_bytes: bytes = None, text: str = None,
                  prompt_text: str = None, accurate: bool = False, images: list = None,
                  transcript: bool = False, speech: bool = False) -> dict:
    """Запрос с картинкой (или несколькими) либо, для моделей без зрения, с распознанным текстом."""
    prompt_text = prompt_text or DEFAULT_PROMPTS[0]["text"]
    if accurate:
        prompt_text = prompt_text.rstrip() + "\n\n" + ACCURATE_ADDON
    if text is not None:
        prefix = USER_PROMPT_SPEECH if speech else (USER_PROMPT_TRANSCRIPT if transcript else USER_PROMPT_TEXT)
        messages = [
            {"role": "system", "content": build_system_prompt(prompt_text, ocr=True, transcript=transcript,
                                                              speech=speech)},
            {"role": "user", "content": prefix + text},
        ]
    else:
        images = images or [png_bytes]
        content = [{"type": "text", "text": USER_PROMPT if len(images) == 1 else
                    f"{USER_PROMPT} Задание разбито на {len(images)} скриншота(ов) — это одно задание, по порядку."}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        messages = [
            {"role": "system", "content": build_system_prompt(prompt_text, ocr=False)},
            {"role": "user", "content": content},
        ]
    payload = {"model": model, "messages": messages}
    _apply_reasoning(payload, provider, model, accurate)
    return payload


def _apply_reasoning(payload: dict, provider: str, model: str, accurate: bool) -> None:
    """Насколько долго модель рассуждает: коротко обычно, подробно в точном режиме."""
    name = model.lower()
    if provider == "groq" and "qwen" in name:
        # Бесплатный Groq: не больше 1000 выходных токенов в минуту, а qwen по умолчанию может запросить
        # 2048 — и получить отказ. С 700 проходят два запроса подряд; если нет — ответит следующая модель.
        payload["max_tokens"] = 700
    if provider == "openrouter":
        # Текст рассуждений не попадает в ответ в обоих режимах.
        payload["reasoning"] = {"effort": "high" if accurate else "low", "exclude": True}
    elif provider == "gemini" and accurate:
        payload["reasoning_effort"] = "high"
    elif provider == "groq" and accurate:
        if "gpt-oss" in name:
            payload["reasoning_effort"] = "high"
        elif "qwen" in name:
            payload["reasoning_effort"] = "default"
            payload["reasoning_format"] = "hidden"  # рассуждения не попадают в текст ответа


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

    code = str(error.get("code") or "").lower()
    reason = str((meta or {}).get("reason") or "").lower()
    if code == "free_rate_limited" and ("access_denied" in reason or "not available to this account" in text):
        return ApiError("free_access", f"Бесплатные модели {name} для твоего аккаунта ещё закрыты. "
                        "Открой настройки профиля на orcarouter.ai и привяжи GitHub-аккаунт, "
                        "который зарегистрирован давно (новый не подойдёт). После этого free-модели заработают.",
                        detail, status)
    if code == "free_quota_exhausted":
        return ApiError("daily_limit", f"Бесплатный лимит {name} на сегодня закончился. "
                        "Попробуй позже или выбери другой сервис в ⚙.", detail, status)
    if _is_balance_error(error, text):
        return ApiError("balance", f"На балансе {name} нет денег. Даже бесплатные (free) модели там работают "
                        f"только при ненулевом балансе. Пополнить: {PROVIDERS[provider]['keys_page']}",
                        detail, status)
    if status == 401 or (status == 400 and "api key" in text and ("valid" in text or "invalid" in text)):
        return ApiError("auth", f"Неверный API-ключ {name}. Проверь его в настройках ⚙ (кнопка «Проверить»).",
                        detail, status)
    if status == 402:
        return ApiError("credits", f"{name} просит пополнить баланс. Для бесплатных моделей это значит, что "
                        "ключ создан с лимитом 0 или модель стала платной. Выбери другую модель или проверь ключ.",
                        detail, status)
    if status == 403:
        return ApiError("other", f"{name} отклонил запрос (модерация или ограничения ключа).", detail, status)
    if status in (400, 415, 422) and any(w in text for w in ("image", "multimodal", "vision",
                                                            "content must be a string")):
        return ApiError("no_vision", "Модель не принимает картинки.", detail, status)
    if status == 404:
        return ApiError("unavailable", "Модель сейчас недоступна.", detail, status)
    if status == 408:
        return ApiError("timeout", "Модель не успела ответить.", detail, status)
    if status == 429:
        err = _classify_429(status, error, headers, provider, name, text, meta, detail, reset_ms)
        try:
            err.retry_after = float(headers.get("retry-after") or headers.get("Retry-After"))
        except (TypeError, ValueError):
            pass
        return err
    return _classify_other(status, name, detail)


def _classify_429(status, error, headers, provider, name, text, meta, detail, reset_ms):
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
    limit = {
        "nvidia": " (бесплатный лимит NVIDIA — около 40 запросов в минуту)",
        "groq": " (бесплатный лимит Groq: 1000 запросов в день и 8000 токенов в минуту — "
                "это несколько скриншотов в минуту)",
        "gemini": " (бесплатный лимит Gemini)",
    }.get(provider, "")
    return ApiError("minute_limit", f"Слишком много запросов подряд{limit}. Подожди минуту.",
                    detail, status, reset_ms)


def _classify_other(status, name, detail):
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


def _read_body(resp, deadline, req, limit=MODEL_DEADLINE) -> bytes:
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
                raise ApiError("timeout", f"Модель не ответила за {limit} с.")
    except ApiError:
        raise
    except Exception:  # RequestException, а при отмене из другого потока — что угодно от закрытого сокета
        _check_cancel(req)
        raise ApiError("timeout", "Модель перестала отвечать (соединение оборвалось или зависло).")
    finally:
        resp.close()
    _check_cancel(req)
    return b"".join(chunks)


def _ask_once(provider: str, api_key: str, payload: dict, req=None, limit=MODEL_DEADLINE) -> str:
    _check_cancel(req)
    deadline = time.monotonic() + limit
    try:
        resp = requests.post(PROVIDERS[provider]["chat_url"], json=payload, headers=_headers(provider, api_key),
                             timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), stream=True)
    except requests.Timeout:
        _check_cancel(req)
        raise ApiError("timeout", f"Модель не ответила за {limit} с.")
    except requests.RequestException as e:
        _check_cancel(req)
        raise ApiError("network", f"Нет соединения с {provider_name(provider)}: {e}")
    if req is not None:
        req.set(resp)
    body = _read_body(resp, deadline, req, limit)
    body_text = body.decode("utf-8", errors="replace").strip()

    try:
        data = json.loads(body_text)
    except ValueError:
        data = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]  # Gemini иногда оборачивает ответ с ошибкой в список
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
    # Некоторые «думающие» модели оставляют рассуждения прямо в тексте — убираем их.
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
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


@dataclass
class Answer:
    """Ответ модели и весь диалог — он нужен, чтобы задавать уточняющие вопросы."""
    text: str
    model: str
    provider: str
    messages: list = field(default_factory=list)
    heard: str = ""  # Interview-режим: расшифровка звука, на которую отвечала модель


def _ask_provider(provider, api_key, models, images, on_try=None, on_status=None, req=None,
                  prompt_text=None, accurate=False, ocr_cache=None, speech=None) -> Answer:
    """Спрашивает модели одного сервиса: первую, при временных сбоях — следующие.

    speech — расшифровка звука (Interview-режим): тогда вместо скриншота модели уходит текст.
    """
    name = provider_name(provider)
    if not api_key:
        raise ApiError("auth", f"Не указан API-ключ {name}. Открой настройки ⚙ и вставь ключ.")
    candidates = []
    for m in models:
        if m and m not in candidates:
            candidates.append(m)
    candidates = candidates[:1 + MAX_FALLBACKS]
    limit = ACCURATE_DEADLINE if accurate else MODEL_DEADLINE
    ocr_cache = ocr_cache if ocr_cache is not None else {}
    small = None
    last_error = None
    for i, model in enumerate(candidates):
        _check_cancel(req)
        if on_try and i > 0:
            on_try(model)

        def text_payload():
            if "text" not in ocr_cache:  # распознаём один раз на весь запрос, даже при смене сервиса
                if on_status:
                    on_status("Распознаю текст на скриншоте…")
                # Оригинал: в высоком разрешении читается лучше. Несколько частей — по порядку.
                parts = [_recognize_text(img) for img in images]
                ocr_cache["text"] = "\n\n".join(parts)
                _check_cancel(req)
            if on_status:
                on_status(f"Модель: {model} (по распознанному тексту)")
            return build_payload(provider, model, text=ocr_cache["text"], prompt_text=prompt_text,
                                 accurate=accurate)

        try:
            payload = None
            if speech is not None:
                if on_status:
                    on_status(f"Модель: {model} (отвечаю на услышанное)")
                payload = build_payload(provider, model, text=speech, prompt_text=prompt_text,
                                        accurate=accurate, speech=True)
                text = _ask_once(provider, api_key, payload, req, limit)
            elif has_vision(provider, model):
                if small is None:
                    small = [downscale_png(img) for img in images]
                payload = build_payload(provider, model, images=small, prompt_text=prompt_text, accurate=accurate)
                try:
                    text = _ask_once(provider, api_key, payload, req, limit)
                except ApiError as e:
                    if e.kind != "no_vision":
                        raise
                    payload = None  # модель не приняла картинку — отправляем ей распознанный текст
            if payload is None:
                payload = text_payload()
                text = _ask_once(provider, api_key, payload, req, limit)
            messages = payload["messages"] + [{"role": "assistant", "content": text}]
            return Answer(text, model, provider, messages)
        except ApiError as e:
            e.source = name
            last_error = e
            per_model_limit = e.kind == "minute_limit" and provider == "groq"  # у Groq лимиты у каждой модели свои
            if e.kind not in _RETRYABLE and not per_model_limit:
                break  # лимит аккаунта / баланс / ключ: другие модели этого сервиса не помогут
    if last_error.kind in _RETRYABLE and len(candidates) > 1:
        last_error.args = (f"{last_error} Пробовал модели: "
                           + ", ".join(m.split('/')[-1] for m in candidates) + ". Попробуй чуть позже.",)
    raise last_error


def _wait(seconds, req, on_status, label):
    """Пауза с обратным отсчётом в статусе; «Стоп» прерывает её сразу."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        if on_status:
            on_status(f"{label}: {int(left) + 1} с")
        if req is not None and req.cancel_event.wait(min(1.0, left)):
            _check_cancel(req)
        elif req is None:
            time.sleep(min(1.0, left))


def _ask_patient(provider, api_key, payload, req, limit, on_status):
    """Запрос, который при минутном лимите один раз ждёт и повторяет (два шага подряд часто в него упираются)."""
    try:
        return _ask_once(provider, api_key, payload, req, limit)
    except ApiError as e:
        if e.kind != "minute_limit":
            raise
        _wait(min(e.retry_after or 15, 25), req, on_status, "🎯 Жду минутный лимит")
        return _ask_once(provider, api_key, payload, req, limit)


def _ask_two_step(provider, api_key, models, images, on_status=None, req=None, prompt_text=None,
                  ocr_cache=None) -> Answer:
    """Точный режим в два шага: модель со зрением переписывает задание, рассуждающая модель решает."""
    reader = next((m for m in models if has_vision(provider, m)), None)
    reasoner = PROVIDERS[provider]["reasoner"]
    if reader:
        if on_status:
            on_status(f"🎯 Читаю задание: {reader.split('/')[-1]}")
        small = [downscale_png(img) for img in images]
        payload = build_payload(provider, reader, images=small, prompt_text="-")
        payload["messages"][0]["content"] = TRANSCRIBE_PROMPT  # без правил «отвечай по-русски»
        if "max_tokens" in payload:
            payload["max_tokens"] = 1000  # длинное задание переписывается целиком; при лимите подождём
        transcript = _ask_patient(provider, api_key, payload, req, MODEL_DEADLINE, on_status)
    else:
        if on_status:
            on_status("🎯 Распознаю текст на скриншоте…")
        if "text" not in (ocr_cache or {}):
            (ocr_cache if ocr_cache is not None else {})["text"] = "\n\n".join(_recognize_text(i) for i in images)
        transcript = ocr_cache["text"]
    _check_cancel(req)
    if on_status:
        on_status(f"🎯 Решаю и проверяю: {reasoner.split('/')[-1]}")
    payload = build_payload(provider, reasoner, text=transcript, prompt_text=prompt_text, accurate=True,
                            transcript=True)
    text = _ask_patient(provider, api_key, payload, req, ACCURATE_DEADLINE, on_status)
    return Answer(text, reasoner, provider, payload["messages"] + [{"role": "assistant", "content": text}])


def ask_services(services, images, on_try=None, on_status=None, on_service=None, req=None,
                 prompt_text=None, accurate=False, speech=None) -> Answer:
    """Спрашивает сервисы по очереди: services = [(provider, api_key, models), …].

    Если сервис упёрся в лимит, не отвечает или не принял ключ — запрос уходит в следующий.
    """
    if isinstance(images, (bytes, bytearray)):
        images = [bytes(images)]
    services = [s for i, s in enumerate(services) if i == 0 or s[1]]  # остальные — только с ключом
    ocr_cache = {}
    failures = []
    last_error = None
    for i, (provider, api_key, models) in enumerate(services):
        _check_cancel(req)
        if i > 0 and on_service:
            on_service(provider)
        try:
            reasoner = PROVIDERS[provider].get("reasoner")
            if speech is not None:
                if accurate and reasoner:  # на текст сразу отвечает рассуждающая модель
                    models = [reasoner] + [m for m in models if m != reasoner]
                return _ask_provider(provider, api_key, models, None, on_try, on_status, req,
                                     prompt_text, accurate, speech=speech)
            if accurate and reasoner:
                # Без отката на однопроходный режим: неверный «точный» ответ хуже ошибки.
                return _ask_two_step(provider, api_key, models, images, on_status, req, prompt_text,
                                     ocr_cache)
            return _ask_provider(provider, api_key, models, images, on_try, on_status, req,
                                 prompt_text, accurate, ocr_cache)
        except ApiError as e:
            if e.kind == "cancelled":
                raise
            if e.source == "сервиса":
                e.source = provider_name(provider)
            last_error = e
            failures.append(f"{provider_name(provider)} — {str(e).split('.')[0]}")
    if len(failures) > 1:
        last_error.args = ("Ни один сервис не ответил:\n" + "\n".join(f"- {f}" for f in failures),)
    raise last_error


def transcribe(api_key: str, wav: bytes, req=None) -> str:
    """Расшифровка звука через Whisper у Groq (бесплатно). Язык определяется сам."""
    if not api_key:
        raise ApiError("auth", "Для Interview-режима нужен бесплатный ключ Groq: он расшифровывает звук "
                       "(Whisper). Открой ⚙, выбери сервис Groq и вставь ключ — основной сервис можно "
                       "потом вернуть, ключ Groq сохранится.")
    _check_cancel(req)
    deadline = time.monotonic() + WHISPER_DEADLINE
    try:
        resp = requests.post(WHISPER_URL, headers=_headers("groq", api_key),
                             files={"file": ("speech.wav", wav, "audio/wav")},
                             data={"model": WHISPER_MODEL, "response_format": "json", "temperature": "0"},
                             timeout=(CONNECT_TIMEOUT, WHISPER_DEADLINE), stream=True)
    except requests.RequestException as e:
        _check_cancel(req)
        raise ApiError("network", f"Нет соединения с Groq: {e}")
    if req is not None:
        req.set(resp)
    body = _read_body(resp, deadline, req, WHISPER_DEADLINE).decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if resp.status_code != 200:
        error = data.get("error") if isinstance(data, dict) else None
        if not isinstance(error, dict):
            error = {"message": body[:300]}
        err = classify_error(resp.status_code, error, resp.headers, "groq")
        err.source = "Groq (расшифровка звука)"
        raise err
    if not isinstance(data, dict):
        raise ApiError("server", "Groq вернул непонятный ответ на расшифровку звука.")
    text = (data.get("text") or "").strip()
    if any(h in text.lower() for h in _WHISPER_HALLUCINATIONS) and len(text) < 80:
        return ""  # так Whisper «слышит» тишину и шум
    return text


def ask_with_fallback(provider: str, api_key: str, models: list, png_bytes: bytes,
                      on_try=None, on_status=None, req=None, prompt_text=None, accurate=False):
    """Один сервис, одна картинка. Возвращает (текст, модель)."""
    answer = _ask_provider(provider, api_key, models, [png_bytes], on_try, on_status, req,
                           prompt_text, accurate)
    return answer.text, answer.model


def ask_followup(answer: Answer, api_key: str, question: str, req=None, accurate=False) -> Answer:
    """Уточняющий вопрос к той же модели: она видит скриншот, свой ответ и новый вопрос."""
    messages = answer.messages + [{"role": "user", "content": question}]
    payload = {"model": answer.model, "messages": messages}
    _apply_reasoning(payload, answer.provider, answer.model, accurate)
    limit = ACCURATE_DEADLINE if accurate else MODEL_DEADLINE
    try:
        text = _ask_once(answer.provider, api_key, payload, req, limit)
    except ApiError as e:
        e.source = provider_name(answer.provider)
        raise
    return Answer(text, answer.model, answer.provider, messages + [{"role": "assistant", "content": text}])


def check_key(provider: str, api_key: str) -> dict:
    """Проверка ключа выбранного сервиса."""
    if not api_key:
        return {"valid": False, "error": "Ключ не указан"}
    if provider == "teamorouter":
        return _check_teamo_key(api_key)
    if provider == "nvidia":
        return _check_nvidia_key(api_key)
    if provider in ("groq", "gemini"):
        return _check_listed_key(provider, api_key)
    if provider == "orcarouter":
        return _check_orca_key(api_key)
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


def _check_listed_key(provider: str, api_key: str) -> dict:
    """Groq и Gemini отдают список моделей только с рабочим ключом — по нему и проверяем."""
    headers = _headers(provider, api_key)
    try:
        resp = requests.get(PROVIDERS[provider]["models_url"], headers=headers, timeout=20)
    except requests.RequestException as e:
        return {"valid": None, "error": f"Нет соединения: {e}"}
    body = resp.text.lower()
    if resp.status_code in (401, 403) or (resp.status_code == 400 and "api key" in body):
        return {"valid": False, "error": "Неверный ключ"}
    if resp.status_code != 200:
        return {"valid": None, "error": f"{provider_name(provider)} ответил {resp.status_code}"}
    if provider != "groq":
        return {"valid": True}
    # Groq показывает дневной остаток в заголовках ответа — делаем крошечный запрос.
    try:
        probe = requests.post(PROVIDERS["groq"]["chat_url"], headers=headers, timeout=30, json={
            "model": GROQ_PROBE_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "1"}]})
    except requests.RequestException:
        return {"valid": True}
    info = {"valid": True}
    try:
        info["limit"] = int(probe.headers["x-ratelimit-limit-requests"])
        info["remaining"] = int(probe.headers["x-ratelimit-remaining-requests"])
    except (KeyError, ValueError):
        pass
    if probe.status_code == 429:
        info["rate_limited"] = True
    return info


def _check_orca_key(api_key: str) -> dict:
    # Список моделей OrcaRouter открыт всем — проверяем ключ пробным запросом к бесплатной DeepSeek.
    try:
        resp = requests.post(PROVIDERS["orcarouter"]["chat_url"], headers=_headers("orcarouter", api_key),
                             timeout=30, json={"model": ORCA_PROBE_MODEL, "max_tokens": 1,
                                               "messages": [{"role": "user", "content": "1"}]})
    except requests.RequestException as e:
        return {"valid": None, "error": f"Нет соединения: {e}"}
    if resp.status_code in (401, 403):
        return {"valid": False, "error": "Неверный ключ"}
    if resp.status_code == 200:
        return {"valid": True, "deepseek_ok": True}
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    if str(err.get("code")) == "free_rate_limited":
        return {"valid": True, "free_locked": True}
    if str(err.get("code")) == "free_quota_exhausted":
        return {"valid": True, "rate_limited": True}
    return {"valid": True, "probe_status": resp.status_code}


def _check_nvidia_key(api_key: str) -> dict:
    # Список моделей NVIDIA открыт всем, поэтому ключ проверяем крошечным запросом к DeepSeek.
    try:
        resp = requests.post(PROVIDERS["nvidia"]["chat_url"], headers=_headers("nvidia", api_key), timeout=30,
                             json={"model": NVIDIA_PROBE_MODEL, "max_tokens": 1,
                                   "messages": [{"role": "user", "content": "1"}]})
    except requests.RequestException as e:
        return {"valid": None, "error": f"Нет соединения: {e}"}
    if resp.status_code in (401, 403):
        return {"valid": False, "error": "Неверный ключ"}
    if resp.status_code == 429:
        return {"valid": True, "rate_limited": True}
    if resp.status_code == 200:
        return {"valid": True, "deepseek_ok": True}
    return {"valid": True, "probe_status": resp.status_code}


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
    elif info.get("free_locked"):
        parts.append("⚠ free-модели закрыты: привяжи давний GitHub-аккаунт в профиле orcarouter.ai")
    elif info.get("deepseek_ok"):
        parts.append("DeepSeek отвечает")
    elif info.get("rate_limited"):
        parts.append("⚠ сейчас лимит запросов — подожди минуту")
    elif info.get("probe_status"):
        parts.append(f"⚠ пробный запрос к DeepSeek вернул код {info['probe_status']}")
    used, limit, remaining = info.get("used"), info.get("limit"), info.get("remaining")
    if remaining is None and used is not None and limit is not None:
        remaining = max(0, limit - used)
    if remaining is not None and limit is not None:
        parts.append(f"запросов на сегодня осталось: {remaining} из {limit}")
    elif limit is not None:
        parts.append(f"лимит бесплатных запросов: {limit} в день")
    if info.get("key_limit_remaining") == 0:
        parts.append("⚠ у ключа исчерпан лимит расходов")
    return " • ".join(parts)


def fetch_models(provider: str, api_key: str = "") -> list:
    """Актуальный список подходящих моделей: бесплатные (и со зрением у TeamoRouter)."""
    info = PROVIDERS[provider]
    if not api_key and provider in ("groq", "gemini", "teamorouter"):
        raise RuntimeError("сначала вставь API-ключ выше")  # список моделей отдают только с ключом
    headers = _headers(provider, api_key) if api_key else {}
    resp = requests.get(info["models_url"], headers=headers, timeout=30)
    if resp.status_code in (401, 403) or (resp.status_code == 400 and "api key" in resp.text.lower()):
        raise RuntimeError("нужен рабочий API-ключ — вставь его выше")
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
        elif provider == "nvidia":
            if not any(w in model_id.lower() for w in _NVIDIA_NOT_CHAT):
                result.append(model_id)
        elif provider == "orcarouter":
            price = m.get("pricing") or {}
            nums = [float(v) for v in price.values() if str(v).replace(".", "", 1).isdigit()]
            free = model_id == "orcarouter/free" or (nums and all(v == 0 for v in nums))
            if free and not any(w in model_id.lower() for w in _ORCA_NOT_CHAT):
                result.append(model_id)
        elif provider == "groq":
            if m.get("active", True) and not any(w in model_id.lower() for w in _GROQ_NOT_CHAT):
                result.append(model_id)
        elif provider == "gemini":
            model_id = model_id.removeprefix("models/")
            if model_id.startswith("gemini") and not any(w in model_id for w in _GEMINI_NOT_CHAT):
                result.append(model_id)
        elif model_id.endswith("-free") or has_vision(provider, model_id):
            result.append(model_id)
    if provider == "openrouter":
        return sorted(result)
    if provider == "orcarouter":  # сначала DeepSeek, потом со зрением, автовыбор в конце
        return sorted(result, key=lambda mid: ("deepseek" not in mid, not has_vision(provider, mid),
                                               mid == "orcarouter/free", mid))
    if provider in ("groq", "gemini"):  # сверху модели со зрением (у Gemini — flash)
        return sorted(result, key=lambda mid: (not has_vision(provider, mid), "flash" not in mid, mid))
    if provider == "nvidia":  # сначала DeepSeek, потом модели со зрением, потом остальные
        return sorted(result, key=lambda mid: ("deepseek" not in mid, not has_vision(provider, mid), mid))
    return sorted(result, key=lambda mid: (not mid.endswith("-free"), mid))  # бесплатные сверху


class AskWorker(QThread):
    """Запрос в фоне: скриншот(ы) по списку сервисов или уточняющий вопрос (followup)."""
    trying = Signal(str)
    status = Signal(str)
    finished_ok = Signal(object)  # Answer
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, services=None, images=None, prompt_text=None, accurate=False,
                 followup=None, audio=None, parent=None):
        super().__init__(parent)
        self._services = list(services or [])
        self._images = images
        self._prompt_text = prompt_text
        self._accurate = accurate
        self._followup = followup  # (answer, api_key, question)
        self._audio = audio  # (wav, ключ Groq) — Interview-режим
        self._req = _Request()

    def cancel(self):
        """Останавливает запрос: закрывает соединение и не пробует запасные модели."""
        self._req.cancel()

    def run(self):
        try:
            if self._followup:
                answer, key, question = self._followup
                result = ask_followup(answer, key, question, self._req, self._accurate)
            elif self._audio:
                wav, groq_key = self._audio
                self.status.emit("🎧 Расшифровываю звук (Whisper)…")
                heard = transcribe(groq_key, wav, self._req)
                if len(heard.strip(" .,!?…")) < 2:
                    raise ApiError("no_speech", "Не расслышал ни одного слова. Проверь источник звука "
                                   "(🔊 звук ПК или 🎤 микрофон) и что собеседника слышно.")
                result = ask_services(
                    self._services, None, on_try=self.trying.emit, on_status=self.status.emit,
                    on_service=lambda p: self.status.emit(f"{provider_name(p)}: пробую этот сервис…"),
                    req=self._req, prompt_text=self._prompt_text or INTERVIEW_PROMPT,
                    accurate=self._accurate, speech=heard)
                result.heard = heard
            else:
                result = ask_services(
                    self._services, self._images, on_try=self.trying.emit, on_status=self.status.emit,
                    on_service=lambda p: self.status.emit(f"{provider_name(p)}: пробую этот сервис…"),
                    req=self._req, prompt_text=self._prompt_text, accurate=self._accurate)
            self.finished_ok.emit(result)
        except ApiError as e:
            if e.kind == "cancelled" or self._req.cancel_event.is_set():
                self.cancelled.emit()
                return
            message = e.full_text()
            first = self._services[0] if self._services else None
            if e.kind == "daily_limit" and first and first[0] == "openrouter":
                info = check_key("openrouter", first[1])
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
