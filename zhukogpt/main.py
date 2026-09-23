"""Точка входа ZhukoGPT."""
import copy
import os
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config
from .api import AskWorker
from .hotkeys import HotkeyManager
from .logo import beetle_icon
from .overlay import RegionSelector, grab_virtual_screen
from .settings_dialog import SettingsDialog
from .window import MainWindow


class ZhukoApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.cfg = config.load()
        self.last_png = None
        self.worker = None
        self._old_workers = []  # остановленные запросы, которые ещё дозакрываются в фоне
        self.selector = None
        self.settings_open = False
        self._was_visible = True

        self.window = MainWindow(self.cfg["hide_from_capture"])
        self.window.setWindowIcon(beetle_icon())
        self.window.place(self.cfg.get("geometry"))
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.stop_requested.connect(self.stop_request)
        self.window.geometry_changed.connect(self._save_geometry)

        self.hotkeys = HotkeyManager(app)
        self.hotkeys.triggered.connect(self.on_hotkey)

        self.window.show()
        self._register_hotkeys()
        self._show_welcome()
        if not config.active(self.cfg).get("api_key"):
            # Первый запуск: без ключа сервиса работать нельзя — сразу открываем настройки.
            QTimer.singleShot(300, self.open_settings)

    # --- хоткеи ---
    def _register_hotkeys(self):
        errors = self.hotkeys.register_all(self.cfg["hotkeys"])
        if errors:
            self.window.set_status("Бинды: " + "; ".join(errors))
        else:
            self.window.set_status(self._ready_text())

    def _ready_text(self):
        return f"Готов • {self.cfg['hotkeys'].get('screenshot', '')} — скриншот"

    def on_hotkey(self, action):
        if self.settings_open:
            return
        {"screenshot": self.take_screenshot, "toggle": self.toggle_window,
         "repeat": self.repeat, "quit": self.quit}.get(action, lambda: None)()

    def _show_welcome(self):
        hk = self.cfg["hotkeys"]
        lines = ["### Привет! Я ZhukoGPT 🪲", ""]
        info = config.PROVIDERS[self.cfg["provider"]]
        current = config.active(self.cfg)
        if not current.get("api_key"):
            lines += [f"**Сначала вставь API-ключ {info['name']}** в настройках ⚙ "
                      f"([{info['keys_page'].split('://', 1)[-1]}]({info['keys_page']})).", ""]
        else:
            lines += [f"Сервис: **{info['name']}**, модель: `{current['model']}`", ""]
        lines += [
            f"- **{hk.get('screenshot', '—')}** — выделить область и решить задание",
            f"- **{hk.get('toggle', '—')}** — показать / скрыть окно",
            f"- **{hk.get('repeat', '—')}** — повторить последний запрос",
            f"- **{hk.get('quit', '—')}** — выход",
        ]
        self.window.show_message("\n".join(lines))

    # --- действия ---
    def toggle_window(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()

    def take_screenshot(self):
        if self.selector is not None:
            return
        if not config.active(self.cfg).get("api_key"):
            self._restore_window()
            self.window.show_error(f"Сначала вставь API-ключ {self._provider_name()} в настройках ⚙.")
            self.open_settings()
            return
        # Если окно не скрыто от захвата — прячем его на время снимка.
        must_hide = self.window.isVisible() and not self.window.capture_hidden_ok
        self._was_visible = self.window.isVisible()
        if must_hide:
            self.window.hide()
            QTimer.singleShot(200, self._grab)
        else:
            self._grab()

    def _grab(self):
        try:
            image, rect = grab_virtual_screen()
        except Exception as e:
            self.selector = None
            self._restore_window()
            self.window.show_error(f"Не удалось сделать скриншот: {e}")
            return
        self.selector = RegionSelector(image, rect)
        self.selector.selected.connect(self._on_selected)
        self.selector.cancelled.connect(self._on_cancelled)
        self.selector.start()

    def _restore_window(self):
        self.window.show()
        self.window.raise_()

    def _on_selected(self, png):
        self.selector = None
        self._restore_window()
        self.send(png)

    def _on_cancelled(self):
        self.selector = None
        if self._was_visible:
            self._restore_window()
        self.window.set_status("Скриншот отменён")

    def repeat(self):
        if self.last_png is None:
            self.window.set_status("Повторять нечего — сначала сделай скриншот")
            return
        if not self.window.isVisible():
            self.window.show()
        self.send(self.last_png)

    def send(self, png):
        if self.worker is not None and self.worker.isRunning():
            self.window.set_status("Ещё думаю над прошлым скриншотом — подожди или нажми «Стоп»")
            return
        self.last_png = png
        current = config.active(self.cfg)
        model = current["model"]
        # Выбранная модель первой, остальные из списка — запасные при перегрузке провайдера.
        models = [model] + [m for m in current.get("models", []) if m != model]
        self.window.set_busy(model)
        self.worker = AskWorker(self.cfg["provider"], current.get("api_key", ""), models, png)
        self.worker.trying.connect(self._on_trying)
        self.worker.status.connect(self.window.set_busy_status)
        self.worker.finished_ok.connect(self._on_answer)
        self.worker.failed.connect(self._on_error)
        self.worker.cancelled.connect(self._on_cancelled_request)
        self.worker.start()

    def stop_request(self):
        worker = self.worker
        if worker is None or not worker.isRunning():
            return
        # Отвязываем поток сразу: окно освобождается мгновенно, а соединение закрывается в фоне.
        worker.cancel()
        for sig in (worker.trying, worker.status, worker.finished_ok, worker.failed, worker.cancelled):
            sig.disconnect()
        self._old_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._old_workers.remove(w))
        self.worker = None
        self._on_cancelled_request()

    def _on_cancelled_request(self):
        self.window.show_message("Запрос остановлен. Можно сделать новый скриншот или нажать "
                                 f"**{self.cfg['hotkeys'].get('repeat', '')}**, чтобы повторить.")
        self.window.set_status(self._ready_text())

    def _provider_name(self):
        return config.PROVIDERS[self.cfg["provider"]]["name"]

    def _on_trying(self, model):
        self.window.set_busy(model, note="прошлая модель занята, пробую другую")

    def _on_answer(self, text, model):
        self.window.show_answer(text)
        self.window.set_status(f"Готово • {model.split('/')[-1]}")

    def _on_error(self, text):
        self.window.show_error(text)
        self.window.set_status(self._ready_text())

    # --- настройки ---
    def open_settings(self):
        if self.settings_open:
            return
        self.settings_open = True
        self.hotkeys.unregister_all()  # чтобы можно было нажать бинд в поле ввода
        new_cfg = copy.deepcopy(self.cfg)
        dlg = SettingsDialog(new_cfg, self.window)
        accepted = dlg.exec()
        self.settings_open = False
        if accepted:
            self.cfg = new_cfg
            config.save(self.cfg)
            self.window.hide_from_capture = self.cfg["hide_from_capture"]
            self.window.apply_window_flags()
        self._register_hotkeys()
        if accepted and self.last_png is None:
            self._show_welcome()  # обновить подсказки с новыми биндами

    def _save_geometry(self, geometry):
        self.cfg["geometry"] = geometry
        config.save(self.cfg)

    def quit(self):
        self.hotkeys.unregister_all()
        g = self.window.geometry()
        self.cfg["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        config.save(self.cfg)
        if any(w.isRunning() for w in [self.worker, *self._old_workers] if w is not None):
            # Запрос к ИИ ещё висит — не ждём его, а сразу завершаем процесс.
            os._exit(0)
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setWindowIcon(beetle_icon())
    app.setQuitOnLastWindowClosed(False)
    palette = app.palette()
    palette.setColor(QPalette.Link, QColor("#8fd8ff"))  # ссылки читаемы на синем фоне
    app.setPalette(palette)

    config.config_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.config_dir() / "zhukogpt.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, config.APP_NAME, "ZhukoGPT уже запущен.")
        return 0

    zhuko = ZhukoApp(app)  # noqa: F841 — держим ссылку, пока работает цикл
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
