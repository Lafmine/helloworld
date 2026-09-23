"""Настройки ZhukoGPT: хранятся в %APPDATA%\\ZhukoGPT\\config.json."""
import copy
import json
import os
from pathlib import Path

APP_NAME = "ZhukoGPT"
VERSION = "1.1.0"

# Сервисы с API, совместимым с OpenAI. Ключи пользователь вводит в настройках, здесь их нет.
PROVIDERS = {
    "openrouter": {
        "name": "OpenRouter",
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "keys_page": "https://openrouter.ai/keys",
        "key_placeholder": "sk-or-v1-…",
        "default_models": [
            "qwen/qwen3.8-27b:free",
            "google/gemma-4-31b-it:free",
            "thinkingmachines/inkling:free",
            "nex-agi/nex-n2.5-pro:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "google/gemma-4-26b-a4b-it:free",
        ],
    },
    "teamorouter": {
        "name": "TeamoRouter",
        "chat_url": "https://api.teamorouter.com/v1/chat/completions",
        "models_url": "https://api.teamorouter.com/v1/models",
        "keys_page": "https://teamorouter.com/dashboard",
        "key_placeholder": "sk-teamo-…",
        "default_models": [
            "deepseek-v4-flash-free",
            "deepseek-flash-free",
            "glm-5.3-flash-free",
            "deepseek-v4-flash-vision-exp",
        ],
    },
}
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODELS = PROVIDERS["openrouter"]["default_models"]  # для совместимости

_ANSWER_FORMAT = (
    "Формат ответа:\n"
    "1. Первой строкой — **Ответ:** и сам правильный ответ (для теста — номер/буква "
    "и текст варианта; если верных вариантов несколько — перечисли все).\n"
    "2. Затем коротко, в 1–4 строках, поясни почему.\n"
    "Если заданий несколько — ответь на каждое по порядку. "
    "Если задание не видно или его нельзя решить — так и скажи. "
    "Всегда отвечай на русском языке, даже если задание на другом языке."
)

SYSTEM_PROMPT = (
    "Ты — ZhukoGPT, помощник, который решает задачи и тесты по скриншоту. "
    "Внимательно прочитай всё, что изображено на картинке, и реши задание.\n" + _ANSWER_FORMAT
)

SYSTEM_PROMPT_TEXT = (
    "Ты — ZhukoGPT, помощник, который решает задачи и тесты. Тебе дают текст, автоматически "
    "распознанный со скриншота (OCR): в нём возможны опечатки, склеенные слова и перепутанный "
    "порядок строк, а картинки и графики в него не попадают. Восстанови смысл задания и реши его. "
    "Если распознано несколько вариантов текста (помечены языком, например [ru] и [en]), "
    "используй тот, что читается осмысленно.\n" + _ANSWER_FORMAT
)

USER_PROMPT = "Реши задание на скриншоте."
USER_PROMPT_TEXT = "Реши задание. Текст с экрана (распознан автоматически, возможны ошибки):\n\n"

HOTKEY_ACTIONS = {
    "screenshot": "Скриншот области",
    "toggle": "Показать / скрыть окно",
    "repeat": "Повторить последний запрос",
    "quit": "Выход из программы",
}


def _provider_defaults(provider_id):
    models = list(PROVIDERS[provider_id]["default_models"])
    return {"api_key": "", "model": models[0], "models": models}


DEFAULTS = {
    "provider": DEFAULT_PROVIDER,
    "providers": {pid: _provider_defaults(pid) for pid in PROVIDERS},
    "hotkeys": {
        "screenshot": "Alt+Q",
        "toggle": "Alt+W",
        "repeat": "Alt+R",
        "quit": "Ctrl+Alt+Q",
    },
    "hide_from_capture": True,
    "geometry": None,  # [x, y, w, h]
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def active(cfg: dict) -> dict:
    """Настройки выбранного сервиса: {'api_key', 'model', 'models'}."""
    return cfg["providers"][cfg["provider"]]


def _merge_provider(target: dict, saved) -> None:
    if not isinstance(saved, dict):
        return
    for key in ("api_key", "model"):
        if isinstance(saved.get(key), str):
            target[key] = saved[key]
    if isinstance(saved.get("models"), list) and saved["models"]:
        target["models"] = [m for m in saved["models"] if isinstance(m, str)]


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg
    if not isinstance(saved, dict):
        return cfg

    # Версии до 1.1.0 хранили ключ и модели OpenRouter прямо в корне.
    _merge_provider(cfg["providers"]["openrouter"],
                    {k: saved[k] for k in ("api_key", "model", "models") if k in saved})
    for pid, value in (saved.get("providers") or {}).items():
        if pid in cfg["providers"]:
            _merge_provider(cfg["providers"][pid], value)
    if saved.get("provider") in PROVIDERS:
        cfg["provider"] = saved["provider"]
    if isinstance(saved.get("hotkeys"), dict):
        cfg["hotkeys"].update(saved["hotkeys"])
    for key in ("hide_from_capture", "geometry"):
        if key in saved:
            cfg[key] = saved[key]
    return cfg


def save(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
