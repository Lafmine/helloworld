"""Настройки ZhukoGPT: хранятся в %APPDATA%\\ZhukoGPT\\config.json."""
import json
import os
from pathlib import Path

APP_NAME = "ZhukoGPT"
VERSION = "1.0.2"

DEFAULT_MODELS = [
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
    "thinkingmachines/inkling:free",
    "nex-agi/nex-n2.5-pro:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "google/gemma-4-26b-a4b-it:free",
]

SYSTEM_PROMPT = (
    "Ты — ZhukoGPT, помощник, который решает задачи и тесты по скриншоту. "
    "Внимательно прочитай всё, что изображено на картинке, и реши задание.\n"
    "Формат ответа:\n"
    "1. Первой строкой — **Ответ:** и сам правильный ответ (для теста — номер/буква "
    "и текст варианта; если верных вариантов несколько — перечисли все).\n"
    "2. Затем коротко, в 1–4 строках, поясни почему.\n"
    "Если на скриншоте несколько заданий — ответь на каждое по порядку. "
    "Если задание не видно или его нельзя решить — так и скажи. "
    "Всегда отвечай на русском языке, даже если задание на другом языке."
)

USER_PROMPT = "Реши задание на скриншоте."

HOTKEY_ACTIONS = {
    "screenshot": "Скриншот области",
    "toggle": "Показать / скрыть окно",
    "repeat": "Повторить последний запрос",
    "quit": "Выход из программы",
}

DEFAULTS = {
    "api_key": "",
    "model": DEFAULT_MODELS[0],
    "models": list(DEFAULT_MODELS),
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


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # глубокая копия
    try:
        with open(config_path(), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key == "hotkeys" and isinstance(value, dict):
                cfg["hotkeys"].update(value)
            elif key in cfg:
                cfg[key] = value
    if not cfg.get("models"):
        cfg["models"] = list(DEFAULT_MODELS)
    return cfg


def save(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
