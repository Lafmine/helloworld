"""Глобальные горячие клавиши через RegisterHotKey (без перехвата клавиатуры)."""
from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence

from .winapi import IS_WINDOWS

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_SPECIAL_KEYS = {
    Qt.Key_Space: 0x20, Qt.Key_Tab: 0x09, Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D,
    Qt.Key_Backspace: 0x08, Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
    Qt.Key_Home: 0x24, Qt.Key_End: 0x23, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    Qt.Key_Print: 0x2C, Qt.Key_Pause: 0x13,
    Qt.Key_Semicolon: 0xBA, Qt.Key_Equal: 0xBB, Qt.Key_Plus: 0xBB, Qt.Key_Comma: 0xBC,
    Qt.Key_Minus: 0xBD, Qt.Key_Period: 0xBE, Qt.Key_Slash: 0xBF, Qt.Key_QuoteLeft: 0xC0,
    Qt.Key_BracketLeft: 0xDB, Qt.Key_Backslash: 0xDC, Qt.Key_BracketRight: 0xDD,
    Qt.Key_Apostrophe: 0xDE,
}


def parse_hotkey(text: str):
    """'Ctrl+Alt+Q' -> (модификаторы WinAPI, виртуальный код клавиши) или None."""
    if not text:
        return None
    seq = QKeySequence.fromString(text, QKeySequence.PortableText)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key = combo.key()
    mods = combo.keyboardModifiers()

    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        vk = int(key)  # коды Qt для букв и цифр совпадают с VK
    elif Qt.Key_F1 <= key <= Qt.Key_F24:
        vk = 0x70 + (int(key) - int(Qt.Key_F1))
    elif key in _SPECIAL_KEYS:
        vk = _SPECIAL_KEYS[key]
    else:
        return None

    win_mods = 0
    if mods & Qt.AltModifier:
        win_mods |= MOD_ALT
    if mods & Qt.ControlModifier:
        win_mods |= MOD_CONTROL
    if mods & Qt.ShiftModifier:
        win_mods |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        win_mods |= MOD_WIN
    return win_mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, manager):
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.hWnd is None:
                self._manager._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    """Регистрирует бинды и испускает triggered(имя_действия)."""

    triggered = Signal(str)

    def __init__(self, app):
        super().__init__()
        self._ids = {}  # id -> action
        self._next_id = 1
        self._filter = None
        if IS_WINDOWS:
            self._filter = _Filter(self)
            app.installNativeEventFilter(self._filter)

    def _on_hotkey(self, hotkey_id):
        action = self._ids.get(hotkey_id)
        if action:
            self.triggered.emit(action)

    def unregister_all(self):
        if IS_WINDOWS:
            for hotkey_id in self._ids:
                _user32.UnregisterHotKey(None, hotkey_id)
        self._ids.clear()

    def register_all(self, hotkeys: dict) -> list:
        """Перерегистрирует все бинды. Возвращает список ошибок (текстом)."""
        self.unregister_all()
        errors = []
        for action, text in hotkeys.items():
            if not text:
                continue
            parsed = parse_hotkey(text)
            if parsed is None:
                errors.append(f"«{text}» — такую клавишу нельзя назначить")
                continue
            hotkey_id = self._next_id
            self._next_id += 1
            if IS_WINDOWS:
                mods, vk = parsed
                if not _user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk):
                    errors.append(f"«{text}» уже занято другой программой")
                    continue
            self._ids[hotkey_id] = action
        return errors
