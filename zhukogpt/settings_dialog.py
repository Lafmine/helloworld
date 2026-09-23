"""Окно настроек в том же «стеклянном» стиле."""
import copy
import html

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from . import winapi
from .api import KeyWorker, ModelsWorker, describe_key, has_vision
from .config import HOTKEY_ACTIONS, PROVIDERS, VERSION
from .hotkeys import parse_hotkey
from .window import BORDER_COLOR, RADIUS

DIALOG_STYLE = """
QWidget { color: #eaf4ff; font-family: 'Segoe UI', sans-serif; font-size: 10pt; }
QLabel#title { font-size: 13pt; font-weight: 600; }
QLabel#hint { color: #a9c8f0; font-size: 9pt; }
QLabel#error { color: #ffb4b4; font-size: 9pt; }
QLabel a { color: #8fd8ff; }
QLineEdit, QComboBox, QKeySequenceEdit QLineEdit {
    background: rgba(5,20,55,0.55); border: 1px solid rgba(160,210,255,0.35);
    border-radius: 8px; padding: 5px 8px; selection-background-color: #3a7bff;
}
QComboBox QAbstractItemView {
    background: #10306e; border: 1px solid rgba(160,210,255,0.35);
    selection-background-color: #3a7bff; outline: none;
}
QComboBox::drop-down { border: none; width: 22px; }
QPushButton {
    background: rgba(255,255,255,0.12); border: 1px solid rgba(160,210,255,0.35);
    border-radius: 8px; padding: 5px 12px;
}
QPushButton:hover { background: rgba(255,255,255,0.22); }
QPushButton#primary { background: #2f6bff; border-color: #6f9bff; }
QPushButton#primary:hover { background: #4a80ff; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""


def short_name(provider):
    return PROVIDERS[provider]["name"].split(" (")[0]  # «NVIDIA (бесплатно)» → «NVIDIA»


def key_hint(provider):
    info = PROVIDERS[provider]
    url = info["keys_page"]
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return f'Где взять ключ: <a style="color:#8fd8ff" href="{url}">{html.escape(host)}</a>'


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Настройки ZhukoGPT")
        self.setStyleSheet(DIALOG_STYLE)
        self.setMinimumWidth(600)
        self._cfg = cfg
        # Черновики ключа и моделей каждого сервиса, пока диалог открыт.
        self._drafts = copy.deepcopy(cfg["providers"])
        self._provider = cfg["provider"]
        self._drag_offset = None
        self._models_worker = None
        self._key_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        root.addWidget(QLabel(f"⚙  Настройки  <span style='font-size:9pt;color:#a9c8f0'>v{VERSION}</span>", objectName="title"))

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Сервис
        self.provider_combo = QComboBox()
        for pid, info in PROVIDERS.items():
            self.provider_combo.addItem(info["name"], pid)
        form.addRow("Сервис:", self.provider_combo)

        # API-ключ
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        btn_eye = QPushButton("👁")
        btn_eye.setCheckable(True)
        btn_eye.setFixedWidth(38)
        btn_eye.toggled.connect(
            lambda on: self.key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.btn_check = QPushButton("Проверить")
        self.btn_check.setToolTip("Проверить ключ (и остаток бесплатных запросов / баланс)")
        self.btn_check.clicked.connect(self._check_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(btn_eye)
        key_row.addWidget(self.btn_check)
        self.key_label = QLabel()
        form.addRow(self.key_label, key_row)
        self.key_hint = QLabel(objectName="hint")
        self.key_hint.setOpenExternalLinks(True)
        self.key_hint.setWordWrap(True)
        form.addRow("", self.key_hint)

        # Модель
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumContentsLength(22)
        self.model_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.currentTextChanged.connect(self._update_model_hint)
        self.btn_refresh = QPushButton("Обновить")
        self.btn_refresh.setToolTip("Загрузить актуальный список бесплатных моделей")
        self.btn_refresh.clicked.connect(self._refresh_models)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.btn_refresh)
        form.addRow("Модель:", model_row)
        self.models_hint = QLabel(objectName="hint")
        self.models_hint.setWordWrap(True)
        form.addRow("", self.models_hint)

        # Бинды
        self.hotkey_edits = {}
        for action, title in HOTKEY_ACTIONS.items():
            edit = QKeySequenceEdit(QKeySequence.fromString(cfg["hotkeys"].get(action, ""),
                                                            QKeySequence.PortableText))
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            self.hotkey_edits[action] = edit
            form.addRow(f"{title}:", edit)

        root.addLayout(form)

        self.capture_check = QCheckBox("Скрывать окно от записи и демонстрации экрана (Discord, OBS…)")
        self.capture_check.setChecked(cfg.get("hide_from_capture", True))
        root.addWidget(self.capture_check)

        self.error_label = QLabel(objectName="error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        btn_cancel = QPushButton("Отмена")
        btn_save = QPushButton("Сохранить", objectName="primary")
        btn_save.setDefault(True)
        btn_cancel.clicked.connect(self.reject)
        btn_save.clicked.connect(self._save)
        buttons.addWidget(btn_cancel)
        buttons.addWidget(btn_save)
        root.addLayout(buttons)

        self.provider_combo.setCurrentIndex(self.provider_combo.findData(self._provider))
        self._load_provider(self._provider)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)

    # --- сервисы ---
    def _stash(self):
        """Запоминает то, что введено для текущего сервиса, перед переключением или сохранением."""
        draft = self._drafts[self._provider]
        draft["api_key"] = self.key_edit.text().strip()
        draft["model"] = self.model_combo.currentText()
        draft["models"] = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]

    def _load_provider(self, provider):
        info = PROVIDERS[provider]
        draft = self._drafts[provider]
        self.key_label.setText(f"API-ключ {short_name(provider)}:")
        self.key_edit.setText(draft.get("api_key", ""))
        self.key_edit.setPlaceholderText(info["key_placeholder"])
        self.key_hint.setText(key_hint(provider))
        self._fill_models(draft.get("models") or info["default_models"], draft.get("model", ""))
        self._update_model_hint()
        if not self.key_edit.text():
            self.key_edit.setFocus()

    def _provider_changed(self, _index):
        self._stash()
        self._provider = self.provider_combo.currentData()
        self._load_provider(self._provider)

    def _update_model_hint(self, *_):
        model = self.model_combo.currentText()
        if not model:
            self.models_hint.setText("")
        elif has_vision(self._provider, model):
            self.models_hint.setText("👁 Модель видит картинку — скриншот отправляется как есть")
        else:
            self.models_hint.setText("🔤 Модель не видит картинки — текст со скриншота распознаёт Windows "
                                     "и отправляет его. Картинки и графики модель не увидит.")

    # --- проверка ключа ---
    def _check_key(self):
        self.btn_check.setEnabled(False)
        self.key_hint.setText("Проверяю ключ…")
        self._key_worker = KeyWorker(self._provider, self.key_edit.text().strip(), self)
        self._key_worker.finished_ok.connect(self._key_checked)
        self._key_worker.start()

    def _key_checked(self, info):
        self.btn_check.setEnabled(True)
        color = {True: "#9df0b0", False: "#ffb4b4"}.get(info.get("valid"), "#ffe08a")
        if info.get("balance_needed"):
            color = "#ffe08a"
        self.key_hint.setText(f'<span style="color:{color}">{html.escape(describe_key(info))}</span>')

    # --- модели ---
    def _fill_models(self, models, current):
        self.model_combo.clear()
        items = list(models)
        if current and current not in items:
            items.insert(0, current)
        self.model_combo.addItems(items)
        if current in items:
            self.model_combo.setCurrentText(current)

    def _refresh_models(self):
        self.btn_refresh.setEnabled(False)
        self.models_hint.setText("Загружаю список моделей…")
        provider = self._provider
        self._models_worker = ModelsWorker(provider, self.key_edit.text().strip(), self)
        self._models_worker.finished_ok.connect(lambda models: self._models_loaded(provider, models))
        self._models_worker.failed.connect(self._models_failed)
        self._models_worker.start()

    def _models_loaded(self, provider, models):
        self.btn_refresh.setEnabled(True)
        if not models:
            self._update_model_hint()
            self.models_hint.setText("Подходящих моделей сейчас не нашлось — оставил старый список")
            return
        if provider != self._provider:  # пока грузилось, переключили сервис
            self._drafts[provider]["models"] = models
            return
        current = self.model_combo.currentText()
        self._fill_models(models, current if current in models else models[0])
        self._update_model_hint()
        self.models_hint.setText(f"Загружено моделей: {len(models)}. " + self.models_hint.text())

    def _models_failed(self, err):
        self.btn_refresh.setEnabled(True)
        self.models_hint.setText(f"Не удалось загрузить список: {err[:80]}")

    # --- сохранение ---
    def _save(self):
        hotkeys = {}
        seen = {}
        for action, edit in self.hotkey_edits.items():
            text = edit.keySequence().toString(QKeySequence.PortableText)
            if text and parse_hotkey(text) is None:
                return self._show_error(f"Клавишу «{text}» нельзя назначить глобальным биндом.")
            if text and text in seen:
                return self._show_error(
                    f"«{text}» назначено и на «{HOTKEY_ACTIONS[seen[text]]}», и на «{HOTKEY_ACTIONS[action]}».")
            if text:
                seen[text] = action
            hotkeys[action] = text
        if not hotkeys.get("screenshot"):
            return self._show_error("Бинд для скриншота обязателен.")

        self._stash()
        self._cfg["provider"] = self._provider
        self._cfg["providers"] = self._drafts
        self._cfg["hotkeys"] = hotkeys
        self._cfg["hide_from_capture"] = self.capture_check.isChecked()
        self.accept()

    def _show_error(self, text):
        self.error_label.setText(text)
        self.error_label.show()

    # --- вид и перетаскивание ---
    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId())
        winapi.set_capture_hidden(hwnd, self._cfg.get("hide_from_capture", True))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS, RADIUS)
        p.fillPath(path, QColor(16, 46, 108, 240))  # плотнее, чтобы поля читались
        p.setPen(QPen(BORDER_COLOR, 1.2))
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None
