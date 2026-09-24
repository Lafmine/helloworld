"""Собирает web/config.json для веб-версии из zhukogpt/config.py — промпты и сервисы в одном месте."""
import json
import sys
from pathlib import Path

from zhukogpt import config

# NVIDIA не пускает запросы из браузера (нет CORS) — в веб-версию её не берём.
WEB_PROVIDERS = ("groq", "gemini", "orcarouter", "openrouter", "teamorouter")
PROVIDER_FIELDS = ("name", "chat_url", "models_url", "keys_page", "key_placeholder", "default_models", "reasoner")


def build() -> dict:
    return {
        "version": config.VERSION,
        "default_provider": config.DEFAULT_PROVIDER,
        "providers": {pid: {k: v for k, v in config.PROVIDERS[pid].items() if k in PROVIDER_FIELDS}
                      for pid in WEB_PROVIDERS},
        "prompts": config.DEFAULT_PROMPTS,
        "common_rules": config.COMMON_RULES,
        "image_intro": config.IMAGE_INTRO,
        "transcript_intro": config.TRANSCRIPT_INTRO,
        "accurate_addon": config.ACCURATE_ADDON,
        "transcribe_prompt": config.TRANSCRIBE_PROMPT,
        "user_prompt": config.USER_PROMPT,
        "user_prompt_transcript": config.USER_PROMPT_TRANSCRIPT,
    }


def main():
    out = Path(__file__).parent / "web" / "config.json"
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"web/config.json: версия {config.VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
