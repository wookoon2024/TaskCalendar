from __future__ import annotations

import ctypes
import logging

from taskcalendar.desktop_services import is_startup_enabled, set_startup_enabled
from taskcalendar.paths import runtime_root
from taskcalendar.qt_entry_dialog_bridge import ensure_qt_application
from taskcalendar.qt_main_window import MainWindow, app_icon
from taskcalendar.storage import EncryptedRepository

ERROR_ALREADY_EXISTS = 183
_single_instance_mutex = None


def _set_windows_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("taskcalendar.calendar")
    except Exception:
        pass


def _configure_logging() -> None:
    # Disable runtime logging output/file creation.
    logging.disable(logging.CRITICAL)


def _acquire_single_instance_lock() -> bool:
    global _single_instance_mutex
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, 0, "Local\\TaskCalendar.SingleInstance")
        if not handle:
            return True
        _single_instance_mutex = handle
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        # If lock acquisition fails unexpectedly, do not block startup.
        return True


def run() -> None:
    if not _acquire_single_instance_lock():
        return

    _configure_logging()

    _set_windows_app_id()
    app = ensure_qt_application()
    app.setApplicationName("캘린더")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    db_dir = runtime_root() / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    repository = EncryptedRepository(db_dir / "taskcalendar.db.enc")

    if not repository.get_setting("toggle_shortcut"):
        repository.set_setting("toggle_shortcut", "Ctrl+Alt+S")
    if not repository.get_setting("auto_start"):
        repository.set_setting("auto_start", "1")
    desired_auto_start = repository.get_setting("auto_start", "1") != "0"
    set_startup_enabled(desired_auto_start)
    repository.set_setting("auto_start", "1" if is_startup_enabled() else "0")
    repository.save()

    window = MainWindow(repository)
    window.show()
    app.exec()
