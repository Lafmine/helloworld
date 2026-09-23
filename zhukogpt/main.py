"""Точка входа ZhukoGPT."""
import copy
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import audio, config, updater
from .api import AskWorker
from .hotkeys import HotkeyManager
from .logo import beetle_icon
from .overlay import RegionSelector, grab_screen_under_cursor, grab_virtual_screen
from .settings_dialog import AccurateWarningDialog, SettingsDialog
from .window import MainWindow


class ZhukoApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.cfg = config.load()
        self.last_images = None  # последний отправленный скриншот (или куски) — для «повторить»
        self.last_answer = None  # ответ с историей — для уточняющих вопросов
        self.batch = []  # собранные куски длинного задания
        self.worker = None
        self._old_workers = []  # остановленные запросы, которые ещё дозакрываются в фоне
        self.selector = None
        self.settings_open = False
        self._was_visible = True
        self._capture_mode = "send"
        self._question = None  # текст уточняющего вопроса, если сейчас идёт он
        self.last_audio = None  # последняя запись Interview-режима — для «повторить»
        self.recorder = audio.Recorder()
        self._listen_timer = QTimer(interval=150)
        self._listen_timer.timeout.connect(self._tick_listen)

        self.window = MainWindow(self.cfg["hide_from_capture"])
        self.window.setWindowIcon(beetle_icon())
        self.window.place(self.cfg.get("geometry"))
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.stop_requested.connect(self.stop_request)
        self.window.prompt_chosen.connect(self._choose_prompt)
        self.window.update_requested.connect(self._start_update)
        self.window.accurate_toggled.connect(self._toggle_accurate)
        self.window.followup_asked.connect(self.ask_followup)
        self.window.batch_send.connect(self._send_batch)
        self.window.batch_clear.connect(self._clear_batch)
        self.window.interview_toggled.connect(self.toggle_interview)
        self.window.listen_clicked.connect(self.toggle_listen)
        self.window.listen_cancel.connect(self.cancel_listen)
        self.window.source_clicked.connect(self._next_source)
        self._update_info = None
        self._update_worker = None
        self._update_prompt_menu()
        self.window.set_accurate(self.cfg.get("accurate", False))
        self.window.set_interview(self.cfg.get("interview", False))
        self.window.set_source(audio.SOURCES.get(self.cfg.get("audio_source"), audio.SOURCES["loopback"]))
        self.window.set_listen_hotkey(self.cfg["hotkeys"].get("listen", ""))
        self.window.geometry_changed.connect(self._save_geometry)

        self.hotkeys = HotkeyManager(app)
        self.hotkeys.triggered.connect(self.on_hotkey)

        self.window.show()
        self._register_hotkeys()
        self._show_welcome()
        if not config.active(self.cfg).get("api_key"):
            # Первый запуск: без ключа сервиса работать нельзя — сразу открываем настройки.
            QTimer.singleShot(300, self.open_settings)
        updater.cleanup_old()
        if self.cfg.get("auto_update", True) and updater.is_frozen():
            QTimer.singleShot(4000, self.check_updates)

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
        {"screenshot": lambda: self.take_screenshot("send"),
         "screenshot_full": self.take_full_screen,
         "screenshot_add": lambda: self.take_screenshot("add"),
         "listen": self.toggle_listen,
         "toggle": self.toggle_window,
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
            lines += [f"Сервис: **{info['name']}**, модель: `{current['model']}`",
                      f"Промпт: **{config.active_prompt(self.cfg)['name']}** (сменить — кнопка 📝)", ""]
        lines += [
            f"- **{hk.get('screenshot', '—')}** — выделить область и решить задание",
            f"- **{hk.get('screenshot_full', '—')}** — весь экран сразу",
            f"- **{hk.get('screenshot_add', '—')}** — добавить кусок длинного задания "
            f"(потом **{hk.get('screenshot', '—')}** — последний кусок и отправить)",
            f"- **{hk.get('toggle', '—')}** — показать / скрыть окно",
            f"- **{hk.get('repeat', '—')}** — повторить последний запрос",
            f"- 🎯 — точный режим, поле под ответом — уточнить у ИИ",
            f"- 🎧 **Interview (бета)**: **{hk.get('listen', '—')}** — слушать звук ПК или микрофон, "
            f"ещё раз — ответить на услышанное",
        ]
        self.window.show_message("\n".join(lines))

    # --- действия ---
    def toggle_window(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self.window.raise_()

    def _need_key(self):
        if config.active(self.cfg).get("api_key"):
            return False
        self._restore_window()
        self.window.show_error(f"Сначала вставь API-ключ {self._provider_name()} в настройках ⚙.")
        self.open_settings()
        return True

    def _hide_for_capture(self, then):
        """Если окно не скрыто от захвата — прячем его на время снимка."""
        self._was_visible = self.window.isVisible()
        if self.window.isVisible() and not self.window.capture_hidden_ok:
            self.window.hide()
            QTimer.singleShot(200, then)
        else:
            then()

    def take_screenshot(self, mode="send"):
        if self.selector is not None or self._need_key():
            return
        self._capture_mode = mode
        self._hide_for_capture(self._grab)

    def take_full_screen(self):
        if self.selector is not None or self._need_key():
            return

        def grab():
            try:
                png = grab_screen_under_cursor()
            except Exception as e:
                self._restore_window()
                self.window.show_error(f"Не удалось сделать скриншот: {e}")
                return
            self._restore_window()
            self._send_with_batch(png)
        self._hide_for_capture(grab)

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
        if self._capture_mode == "add":
            self._add_to_batch(png)
        else:
            self._send_with_batch(png)

    def _on_cancelled(self):
        self.selector = None
        if self._was_visible:
            self._restore_window()
        self.window.set_status("Скриншот отменён")

    # --- куски длинного задания ---
    def _add_to_batch(self, png):
        self.batch.append(png)
        limit = config.MAX_BATCH_PARTS
        if len(self.batch) >= limit:
            self.window.set_status(f"Собрано {limit} куска — отправляю")
            self._send_batch()
            return
        self.window.set_batch(len(self.batch), limit)
        hk = self.cfg["hotkeys"]
        self.window.set_status(f"{hk.get('screenshot_add', '')} — ещё кусок, "
                               f"{hk.get('screenshot', '')} — последний")

    def _send_with_batch(self, png):
        if self.batch:
            self.batch.append(png)
            self._send_batch()
        else:
            self.send([png])

    def _send_batch(self):
        if self.batch:
            images, self.batch = self.batch, []
            self.window.set_batch(0, config.MAX_BATCH_PARTS)
            self.send(images)

    def _clear_batch(self):
        self.batch = []
        self.window.set_batch(0, config.MAX_BATCH_PARTS)
        self.window.set_status("Куски выброшены")

    # --- запросы ---
    def repeat(self):
        if self.last_images is None and self.last_audio is None:
            self.window.set_status("Повторять нечего — сначала сделай скриншот")
            return
        if not self.window.isVisible():
            self.window.show()
        if self.last_images is None:
            self.send_audio(self.last_audio)
        else:
            self.send(self.last_images)

    def _busy(self):
        if self.worker is not None and self.worker.isRunning():
            self.window.set_status("Ещё думаю над прошлым запросом — подожди или нажми «Стоп»")
            return True
        return False

    def _start_worker(self, worker, model):
        self.window.set_busy(model, note="🎯 точный режим" if self.cfg.get("accurate") else "")
        self.worker = worker
        worker.trying.connect(self._on_trying)
        worker.status.connect(self.window.set_busy_status)
        worker.finished_ok.connect(self._on_answer)
        worker.failed.connect(self._on_error)
        worker.cancelled.connect(self._on_cancelled_request)
        worker.start()

    def send(self, images):
        if self._busy():
            return
        self.last_images = images
        self.last_audio = None
        services = config.services_order(self.cfg)
        prompt = config.active_prompt(self.cfg)
        self._question = None
        self._start_worker(AskWorker(services, images, prompt_text=prompt["text"],
                                     accurate=self.cfg.get("accurate", False)), services[0][2][0])

    def send_audio(self, wav):
        if self._busy():
            return
        self.last_audio = wav
        self.last_images = None
        services = config.services_order(self.cfg)
        groq_key = self.cfg["providers"]["groq"].get("api_key", "")
        self._question = None
        self._start_worker(AskWorker(services, audio=(wav, groq_key), accurate=self.cfg.get("accurate", False)),
                           services[0][2][0])

    # --- Interview-режим (бета) ---
    def toggle_interview(self, on):
        if not on and self.recorder.active:
            self.cancel_listen()
        self.cfg["interview"] = on
        config.save(self.cfg)
        self.window.set_interview(on)
        hk = self.cfg["hotkeys"].get("listen", "")
        self.window.set_status(f"🎧 Interview (бета): {hk} или большая кнопка — слушать" if on
                               else "Interview-режим выключен")

    def _next_source(self):
        if self.recorder.active:
            return
        ids = list(audio.SOURCES)
        current = self.cfg.get("audio_source", "loopback")
        new = ids[(ids.index(current) + 1) % len(ids)] if current in ids else ids[0]
        self.cfg["audio_source"] = new
        config.save(self.cfg)
        self.window.set_source(audio.SOURCES[new])
        self.window.set_status("Слушаю звук компьютера (собеседник в Zoom, Discord, браузере)" if new == "loopback"
                               else "Слушаю микрофон")

    def toggle_listen(self):
        if self.settings_open:
            return
        if self.recorder.active:
            self.finish_listen()
        else:
            self.start_listen()

    def start_listen(self):
        if self._busy():
            return
        self._restore_window()
        if not self.cfg["providers"]["groq"].get("api_key"):
            self.window.show_error(
                "Для Interview-режима нужен **бесплатный ключ Groq**: он расшифровывает звук (Whisper).\n\n"
                "Открой ⚙, выбери сервис **Groq**, вставь ключ "
                "([console.groq.com/keys](https://console.groq.com/keys)) и сохрани. "
                "Потом можно вернуть свой основной сервис — ключ Groq останется.")
            return
        if not self.cfg.get("interview"):
            self.toggle_interview(True)
        source = self.cfg.get("audio_source", "loopback")
        try:
            self.recorder.start(source)
        except audio.AudioError as e:
            self.window.show_error(f"Не получилось начать запись: {e}")
            return
        hk = self.cfg["hotkeys"].get("listen", "")
        what = "звук компьютера" if source == "loopback" else "микрофон"
        self.window.show_message(
            f"### 🎧 Слушаю {what}…\n\n"
            f"Когда вопрос прозвучит до конца — нажми **{hk}** или «⏹ Ответить».\n\n"
            f"Устройство: {self.recorder.device_name}\n\n"
            f"Максимум {audio.MAX_SECONDS // 60} минуты, потом запись отправится сама.")
        self.window.set_status("🎧 Идёт запись")
        self.window.set_listening(True)
        self._listen_timer.start()

    def _tick_listen(self):
        if not self.recorder.active:
            self._listen_timer.stop()
            return
        elapsed = self.recorder.elapsed()
        self.window.set_listening(True, elapsed, self.recorder.level)
        if elapsed >= audio.MAX_SECONDS:
            self.finish_listen()

    def finish_listen(self):
        self._listen_timer.stop()
        wav = self.recorder.stop()
        self.window.set_listening(False)
        if wav is None:
            source = self.cfg.get("audio_source", "loopback")
            hint = ("Проверь, что звук собеседника идёт через динамики/наушники по умолчанию, "
                    "или переключи источник на 🎤 микрофон." if source == "loopback" else
                    "Проверь, что выбран нужный микрофон по умолчанию в Windows и он не выключен.")
            self.window.show_error(f"Записалась тишина — отправлять нечего.\n\n{hint}")
            self.window.set_status(self._ready_text())
            return
        self.send_audio(wav)

    def cancel_listen(self):
        self._listen_timer.stop()
        self.recorder.cancel()
        self.window.set_listening(False)
        self.window.show_message("Запись выброшена.")
        self.window.set_status(self._ready_text())

    def ask_followup(self, question):
        if self.last_answer is None or self._busy():
            return
        key = self.cfg["providers"][self.last_answer.provider].get("api_key", "")
        self._question = question
        self._start_worker(AskWorker(followup=(self.last_answer, key, question),
                                     accurate=self.cfg.get("accurate", False)), self.last_answer.model)

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
        self.window.show_ask(self.last_answer is not None)

    def _provider_name(self):
        return config.PROVIDERS[self.cfg["provider"]]["name"]

    def _on_trying(self, model):
        self.window.set_busy(model, note="прошлая модель занята, пробую другую")

    def _on_answer(self, answer):
        self.last_answer = answer
        text = answer.text
        if answer.heard:
            text = f"> 🎧 {answer.heard}\n\n{text}"
        elif self._question:
            text = f"> {self._question}\n\n{text}"
        self.window.show_answer(text)
        service = config.PROVIDERS[answer.provider]["name"].split(" (")[0]
        switched = "" if answer.provider == self.cfg["provider"] else " (основной сервис не ответил)"
        self.window.set_status(f"Готово • {answer.model.split('/')[-1]} • {service}{switched}")
        self.window.show_ask(True)

    def _on_error(self, text):
        self.window.show_error(text)
        self.window.set_status(self._ready_text())
        self.window.show_ask(self.last_answer is not None and self._question is not None)

    # --- точный режим ---
    def _toggle_accurate(self, on):
        if on and not self.cfg.get("accurate_warned"):
            dlg = AccurateWarningDialog(self.window, self.cfg.get("hide_from_capture", True))
            if not dlg.exec():
                return  # передумал — режим остаётся выключенным
            self.cfg["accurate_warned"] = True
        self.cfg["accurate"] = on
        config.save(self.cfg)
        self.window.set_accurate(on)
        self.window.set_status("🎯 Точный режим включён: ответы дольше, но внимательнее" if on
                               else "Точный режим выключен")

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
            self._update_prompt_menu()
            self.window.set_listen_hotkey(self.cfg["hotkeys"].get("listen", ""))
        self._register_hotkeys()
        if accepted and self.last_images is None and self.last_audio is None:
            self._show_welcome()  # обновить подсказки с новыми биндами

    # --- обновление ---
    def check_updates(self):
        self._update_worker = updater.UpdateChecker()
        self._update_worker.found.connect(self._on_update_found)
        self._update_worker.start()  # тихо: ошибки сети при проверке не показываем

    def _on_update_found(self, info):
        self._update_info = info
        self.window.show_update(info["version"])
        if not (self.worker and self.worker.isRunning()):
            self.window.set_status(f"Вышла версия {info['version']} — нажми «⬆ {info['version']}», чтобы обновиться")

    def _start_update(self):
        info = self._update_info
        if not info or not info.get("url") or not updater.is_frozen():
            QDesktopServices.openUrl(QUrl(updater.RELEASES_PAGE))
            return
        self.window.set_update_progress(0)
        self.window.set_status(f"Скачиваю ZhukoGPT {info['version']}…")
        self._update_worker = updater.UpdateDownloader(info["url"])
        self._update_worker.progress.connect(self.window.set_update_progress)
        self._update_worker.done.connect(self._install_update)
        self._update_worker.failed.connect(self._update_failed)
        self._update_worker.start()

    def _install_update(self, path):
        try:
            updater.install(Path(path))
        except Exception as e:
            self._update_failed(str(e))
            return
        self.quit()  # новый exe уже запущен и ждёт, пока этот закроется

    def _update_failed(self, err):
        self.window.show_update(self._update_info["version"])
        self.window.show_error(f"Не удалось обновиться автоматически: {err}\n\n"
                               f"Скачай новую версию вручную: [{updater.RELEASES_PAGE}]({updater.RELEASES_PAGE})")
        self.window.set_status(self._ready_text())

    def _update_prompt_menu(self):
        self.window.set_prompts([p["name"] for p in self.cfg["prompts"]], self.cfg["active_prompt"])

    def _choose_prompt(self, index):
        self.cfg["active_prompt"] = index
        config.save(self.cfg)
        self._update_prompt_menu()
        name = config.active_prompt(self.cfg)["name"]
        self.window.set_status(f"Промпт: {name} • {self.cfg['hotkeys'].get('screenshot', '')} — скриншот")
        if self.last_images is None and self.last_audio is None:
            self._show_welcome()

    def _save_geometry(self, geometry):
        self.cfg["geometry"] = geometry
        config.save(self.cfg)

    def quit(self):
        self.recorder.cancel()
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
    # После обновления прошлый exe ещё пару секунд закрывается — ждём его, а не пишем «уже запущен».
    wait_ms = 10000 if updater.AFTER_UPDATE_FLAG in sys.argv else 100
    if not lock.tryLock(wait_ms):
        QMessageBox.information(None, config.APP_NAME, "ZhukoGPT уже запущен.")
        return 0

    zhuko = ZhukoApp(app)  # noqa: F841 — держим ссылку, пока работает цикл
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
