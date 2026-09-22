"""Вызовы WinAPI через ctypes. На других ОС функции ничего не делают."""
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    WDA_NONE = 0x00
    WDA_EXCLUDEFROMCAPTURE = 0x11

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _get_long = user32.GetWindowLongPtrW
        _set_long = user32.SetWindowLongPtrW
        _get_long.restype = ctypes.c_ssize_t
        _set_long.restype = ctypes.c_ssize_t
        _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    else:
        _get_long = user32.GetWindowLongW
        _set_long = user32.SetWindowLongW
        _get_long.restype = ctypes.c_long
        _set_long.restype = ctypes.c_long
        _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]

    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def hide_from_taskbar(hwnd: int) -> None:
    """Убирает окно с панели задач и из Alt+Tab."""
    if not IS_WINDOWS:
        return
    style = _get_long(hwnd, GWL_EXSTYLE)
    style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    _set_long(hwnd, GWL_EXSTYLE, style)


def set_capture_hidden(hwnd: int, hidden: bool) -> bool:
    """Скрывает окно от записи/демонстрации экрана (Windows 10 2004+).

    Возвращает True, если получилось.
    """
    if not IS_WINDOWS:
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE
    return bool(user32.SetWindowDisplayAffinity(hwnd, affinity))
