"""Настройки ZhukoGPT: хранятся в %APPDATA%\\ZhukoGPT\\config.json."""
import copy
import json
import os
from pathlib import Path

APP_NAME = "ZhukoGPT"
VERSION = "1.6.0"

# Сервисы с API, совместимым с OpenAI. Ключи пользователь вводит в настройках, здесь их нет.
PROVIDERS = {
    "groq": {
        "name": "Groq (бесплатно)",
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "keys_page": "https://console.groq.com/keys",
        "key_placeholder": "gsk_…",
        # Точный режим: qwen3.8 читает скриншот, а gpt-oss-120b (настоящие рассуждения) решает.
        "reasoner": "openai/gpt-oss-120b",
        "default_models": [
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
        ],
    },
    "gemini": {
        "name": "Google Gemini (бесплатно)",
        "chat_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/openai/models",
        "keys_page": "https://aistudio.google.com/apikey",
        "key_placeholder": "AIza…",
        "default_models": [
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ],
    },
    "orcarouter": {
        "name": "OrcaRouter",
        "chat_url": "https://api.orcarouter.ai/v1/chat/completions",
        "models_url": "https://api.orcarouter.ai/v1/models",
        "keys_page": "https://www.orcarouter.ai/console",
        "key_placeholder": "sk-orca-…",
        "default_models": [
            "deepseek/deepseek-v4-flash-free",
            "z-ai/glm-5.3-flash-free",
            "tencent/hy3-free",
            "orcarouter/free",
        ],
    },
    "nvidia": {
        "name": "NVIDIA (бесплатно)",
        "chat_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "models_url": "https://integrate.api.nvidia.com/v1/models",
        "keys_page": "https://build.nvidia.com/settings/api-keys",
        "key_placeholder": "nvapi-…",
        "default_models": [
            "deepseek-ai/deepseek-v4.1-flash",
            "google/gemma-4-31b-it",
            "meta/llama-3.2-90b-vision-instruct",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ],
    },
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
DEFAULT_PROVIDER = "groq"
DEFAULT_MODELS = PROVIDERS["openrouter"]["default_models"]  # для совместимости

# Правила, которые добавляются к любому промпту — и к встроенным, и к своим.
COMMON_RULES = (
    "Всегда отвечай на русском языке, даже если задание на другом языке.\n"
    "Не используй LaTeX и знаки $: формулы пиши обычным текстом и символами Unicode, "
    "например 17 × 3 + 9 = 60, x² − 4 = 0, √16 = 4, 1/2, 90°."
)
IMAGE_INTRO = "Ты — ZhukoGPT, помощник. Тебе присылают скриншот части экрана — внимательно прочитай всё, что на нём."
TEXT_INTRO = (
    "Ты — ZhukoGPT, помощник. Тебе дают текст, автоматически распознанный со скриншота (OCR): "
    "в нём возможны опечатки, склеенные слова и перепутанный порядок строк, а картинки и графики "
    "в него не попадают. Восстанови смысл. Если распознано несколько вариантов текста "
    "(помечены языком, например [ru] и [en]), используй тот, что читается осмысленно."
)

# Промпты по умолчанию. Пользователь может их менять, удалять и добавлять свои.
DEFAULT_PROMPTS = [
    {
        "name": "Решить задание",
        "text": (
            "Реши задание.\n"
            "Формат ответа:\n"
            "1. Первой строкой — **Ответ:** и сам правильный ответ (для теста — номер/буква "
            "и текст варианта; если верных вариантов несколько — перечисли все).\n"
            "2. Затем коротко, в 1–4 строках, поясни почему.\n"
            "Если заданий несколько — ответь на каждое по порядку. "
            "Если задание не видно или его нельзя решить — так и скажи."
        ),
    },
    {
        "name": "Только ответ",
        "text": ("Реши задание и напиши только ответ, без пояснений. "
                 "Если заданий несколько — по одному ответу на строку, с номером задания."),
    },
    {
        "name": "Подробно объяснить",
        "text": ("Объясни решение подробно, как учитель ученику: по шагам, простыми словами, "
                 "с промежуточными вычислениями. В конце отдельной строкой — **Ответ:**."),
    },
    {
        "name": "Перевести",
        "text": ("Переведи весь текст со скриншота на русский язык. Сохрани структуру: абзацы, "
                 "списки, варианты ответов. Выведи только перевод — без решения, пояснений "
                 "и вступительных фраз."),
    },
]


def build_system_prompt(prompt_text: str, ocr: bool, transcript: bool = False, speech: bool = False) -> str:
    """Системный промпт: вступление (скриншот, OCR-текст или переписанное задание) + задача + правила."""
    if speech:
        return f"{SPEECH_INTRO}\n\nЗадача:\n{prompt_text.strip()}\n\n{SPEECH_RULES}"
    intro = TRANSCRIPT_INTRO if transcript else (TEXT_INTRO if ocr else IMAGE_INTRO)
    return f"{intro}\n\nЗадача:\n{prompt_text.strip()}\n\n{COMMON_RULES}"


# Точный режим 🎯: модель дольше думает и перепроверяет себя.
ACCURATE_ADDON = (
    "Точный режим: решай очень внимательно. Про себя реши задание пошагово, затем проверь каждое "
    "вычисление ещё раз (другим способом или подстановкой) и сверь итог с вариантами ответа. "
    "Если нашлась ошибка — исправь. Черновик и проверку в ответ НЕ выписывай: выведи только "
    "итог строго в формате задачи выше."
)
# Шаг 1 точного режима: модель со зрением только переписывает задание, ничего не решая.
TRANSCRIBE_PROMPT = (
    "Перепиши дословно всё задание со скриншота(ов): условие, все числа, единицы, формулы "
    "(обычным текстом, например x² − 4 = 0), варианты ответов с их буквами/номерами. "
    "Если есть рисунок, график, схема или таблица — опиши их словами со всеми числами и подписями. "
    "Если скриншотов несколько — это одно задание, перепиши части по порядку. "
    "Сохраняй язык оригинала. Ничего не решай и не добавляй от себя."
)
TRANSCRIPT_INTRO = (
    "Ты — ZhukoGPT, помощник. Тебе дают задание, аккуратно переписанное со скриншота "
    "(рисунки и графики описаны словами)."
)
# Interview-режим (бета): звук → Whisper у Groq → ответ.
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3-turbo"
SPEECH_INTRO = (
    "Ты — ZhukoGPT, помощник на собеседовании или в разговоре. Тебе дают автоматическую "
    "расшифровку того, что сейчас прозвучало (возможны ошибки распознавания, нет пунктуации "
    "и не указано, кто говорит). Восстанови смысл."
)
INTERVIEW_PROMPT = (
    "Найди в расшифровке последний вопрос или задание, адресованные собеседнику, и помоги ответить.\n"
    "Формат ответа:\n"
    "1. Первой строкой — **Вопрос:** коротко, как ты его понял.\n"
    "2. Затем **Ответ:** — готовый ответ, который можно сразу сказать вслух от первого лица: "
    "3–6 предложений, по делу, живым разговорным языком.\n"
    "3. Если вопрос технический — ниже 2–4 ключевых пункта списком или короткий пример кода.\n"
    "Если вопроса нет — в одной-двух строках скажи, о чём речь, и что можно ответить."
)
SPEECH_RULES = (
    "Готовый ответ пиши на том языке, на котором идёт разговор; всё остальное — по-русски.\n"
    "Не используй LaTeX и знаки $: формулы пиши обычным текстом и символами Unicode."
)
USER_PROMPT_SPEECH = "Расшифровка звука:\n\n"

MAX_BATCH_PARTS = 3  # сколько кусков длинного задания можно собрать в один запрос

USER_PROMPT = "Вот скриншот. Выполни задачу."
USER_PROMPT_TEXT = "Выполни задачу. Текст с экрана (распознан автоматически, возможны ошибки):\n\n"
USER_PROMPT_TRANSCRIPT = "Выполни задачу. Задание со скриншота:\n\n"

HOTKEY_ACTIONS = {
    "screenshot": "Скриншот области",
    "screenshot_full": "Скриншот всего экрана",
    "screenshot_add": "Добавить кусок задания",
    "listen": "Interview: слушать / ответить",
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
        "screenshot_full": "Alt+S",
        "screenshot_add": "Alt+D",
        "listen": "Alt+E",
        "toggle": "Alt+W",
        "repeat": "Alt+R",
        "quit": "Ctrl+Alt+Q",
    },
    "hide_from_capture": True,
    "geometry": None,  # [x, y, w, h]
    "auto_update": True,
    "fallback_services": True,  # при лимите/ошибке пробовать другие сервисы с ключами
    "accurate": False,
    "accurate_warned": False,
    "interview": False,  # Interview-режим (бета): большая кнопка «Слушать» в окне
    "audio_source": "loopback",  # loopback — звук компьютера, mic — микрофон
    "prompts": copy.deepcopy(DEFAULT_PROMPTS),
    "active_prompt": 0,
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
    elif saved.get("api_key"):
        cfg["provider"] = "openrouter"  # у 1.0.x был только OpenRouter — оставляем его
    if isinstance(saved.get("hotkeys"), dict):
        cfg["hotkeys"].update(saved["hotkeys"])
    for key in ("hide_from_capture", "geometry", "auto_update", "fallback_services", "accurate",
                "accurate_warned", "interview", "audio_source"):
        if key in saved:
            cfg[key] = saved[key]
    prompts = saved.get("prompts")
    if isinstance(prompts, list):
        prompts = [{"name": str(p.get("name") or "Без названия"), "text": str(p.get("text") or "")}
                   for p in prompts if isinstance(p, dict) and str(p.get("text") or "").strip()]
        if prompts:
            cfg["prompts"] = prompts
    if isinstance(saved.get("active_prompt"), int):
        cfg["active_prompt"] = saved["active_prompt"]
    cfg["active_prompt"] = min(max(cfg["active_prompt"], 0), len(cfg["prompts"]) - 1)
    return cfg


def services_order(cfg: dict) -> list:
    """Выбранный сервис первым, затем остальные с ключами: [(provider, api_key, models), …]."""
    def entry(pid):
        p = cfg["providers"][pid]
        return pid, p.get("api_key", ""), [p["model"]] + [m for m in p.get("models", []) if m != p["model"]]
    order = [entry(cfg["provider"])]
    if cfg.get("fallback_services", True):
        order += [entry(pid) for pid in PROVIDERS if pid != cfg["provider"] and cfg["providers"][pid].get("api_key")]
    return order


def active_prompt(cfg: dict) -> dict:
    """Выбранный промпт: {'name', 'text'}."""
    return cfg["prompts"][cfg["active_prompt"]]


def save(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
