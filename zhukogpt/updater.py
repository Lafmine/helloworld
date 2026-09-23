"""Автообновление: проверка релизов на GitHub, скачивание нового exe и перезапуск."""
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from .config import VERSION

REPO = "bobopsya/zhukoGPT"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "ZhukoGPT.exe"
AFTER_UPDATE_FLAG = "--after-update"


def parse_version(text: str) -> tuple:
    """'v1.4.0' → (1, 4, 0). Непонятную строку считаем версией 0."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_newer(remote: str, local: str = VERSION) -> bool:
    return parse_version(remote) > parse_version(local)


def is_frozen() -> bool:
    """Запущены ли мы из собранного ZhukoGPT.exe (а не python ZhukoGPT.py)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def cleanup_old() -> None:
    """После обновления рядом остаётся прошлый exe — удаляем его при следующем запуске."""
    if not is_frozen():
        return
    exe = current_exe()
    for leftover in (exe.with_suffix(".old.exe"), exe.with_suffix(".new.exe")):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass  # старый процесс ещё не закрылся — удалим в следующий раз


def fetch_latest() -> dict:
    """Последний релиз: {'version', 'url', 'notes'}; url — прямая ссылка на ZhukoGPT.exe."""
    resp = requests.get(LATEST_URL, timeout=15, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code == 404:
        raise RuntimeError("релизы не найдены (репозиторий приватный или релизов ещё нет)")
    resp.raise_for_status()
    data = resp.json()
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET_NAME), None)
    return {
        "version": str(data.get("tag_name", "")).lstrip("v"),
        "url": asset["browser_download_url"] if asset else None,
        "size": asset.get("size") if asset else None,
        "notes": data.get("body") or "",
    }


def install(new_exe: Path) -> None:
    """Подменяет запущенный exe новым и запускает его.

    Запущенный exe в Windows нельзя перезаписать, но можно переименовать: старый уходит в
    ZhukoGPT.old.exe (удалится при следующем запуске), новый встаёт на его место.
    """
    exe = current_exe()
    old = exe.with_suffix(".old.exe")
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    try:
        os.replace(new_exe, exe)
    except OSError:
        os.replace(old, exe)  # откат, чтобы не остаться без программы
        raise
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(exe), AFTER_UPDATE_FLAG], close_fds=True, creationflags=flags)


class UpdateChecker(QThread):
    found = Signal(dict)  # есть версия новее
    up_to_date = Signal()
    failed = Signal(str)

    def run(self):
        try:
            info = fetch_latest()
        except Exception as e:
            self.failed.emit(str(e))
            return
        if info["version"] and is_newer(info["version"]):
            self.found.emit(info)
        else:
            self.up_to_date.emit()


class UpdateDownloader(QThread):
    progress = Signal(int)  # проценты
    done = Signal(str)  # путь к скачанному exe
    failed = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        target = current_exe().with_suffix(".new.exe")
        try:
            with requests.get(self._url, stream=True, timeout=(15, 60)) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                with open(target, "wb") as f:
                    for chunk in resp.iter_content(1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        if total:
                            self.progress.emit(int(got * 100 / total))
            if total and got != total:
                raise RuntimeError("файл скачался не полностью")
            with open(target, "rb") as f:
                if f.read(2) != b"MZ":
                    raise RuntimeError("скачался не exe-файл")
        except Exception as e:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            self.failed.emit(str(e))
            return
        self.done.emit(str(target))
