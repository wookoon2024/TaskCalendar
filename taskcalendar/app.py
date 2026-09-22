from __future__ import annotations

import ctypes
import json
import logging
import sys
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from taskcalendar.desktop_services import (
    default_shortcut,
    default_memo_shortcut,
    is_startup_enabled,
    set_startup_enabled,
)
from taskcalendar.paths import runtime_root
from taskcalendar.qt_entry_dialog_bridge import ensure_qt_application
from taskcalendar.qt_main_window import MainWindow, app_icon
from taskcalendar.storage import EncryptedRepository

logger = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
_single_instance_mutex = None

IPC_SERVER_NAME = "TaskCalendar_IPC"
IPC_TIMEOUT_MS = 3000


def _set_windows_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("taskcalendar.calendar")
    except Exception:
        pass


def _configure_logging() -> None:
    handlers: list[logging.Handler] = []
    # stderr 로만 기록한다. (파일 로그는 생성하지 않음)
    # windowed 빌드(pythonw / --windowed exe)는 stderr 가 없어 로그가 버려진다.
    if sys.stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


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
        return True


def _parse_protocol_url(argv: list[str]) -> dict | None:
    """Parse taskcalendar:// URL from command-line arguments."""
    for arg in argv[1:]:
        if arg.startswith("taskcalendar://"):
            try:
                parsed = urlparse(arg)
                params = parse_qs(parsed.query, keep_blank_values=True)
                data = {
                    "action": parsed.netloc or "add",
                    "type": params.get("type", ["schedule"])[0],
                    "title": unquote(params.get("title", [""])[0]),
                    "department": unquote(params.get("department", [""])[0] or params.get("dept", [""])[0]),
                    "author": unquote(params.get("author", [""])[0]),
                    "category": unquote(params.get("category", [""])[0]),
                    "status": unquote(params.get("status", [""])[0]),
                    "desc": unquote(params.get("desc", [""])[0]),
                    "url": unquote(params.get("url", [""])[0]),
                    "date": params.get("date", [""])[0],
                }
                logger.info(f"[_parse_protocol_url] Parsed: {data}")
                return data
            except Exception as e:
                logger.error(f"[_parse_protocol_url] Failed to parse '{arg}': {e}")
                return None
    return None


def _send_to_existing_instance(data: dict) -> bool:
    """Send data to the already-running primary instance via QLocalSocket."""
    try:
        # Explicitly permit the target process to set foreground window
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass

        # Need a minimal QApplication for QLocalSocket to work
        app = ensure_qt_application()

        socket = QLocalSocket()
        socket.connectToServer(IPC_SERVER_NAME)
        if not socket.waitForConnected(IPC_TIMEOUT_MS):
            logger.warning(f"[IPC] Could not connect to server: {socket.errorString()}")
            return False

        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        socket.write(payload)
        socket.flush()
        socket.waitForBytesWritten(IPC_TIMEOUT_MS)
        socket.disconnectFromServer()
        logger.info(f"[IPC] Sent data to existing instance: {data}")
        return True
    except Exception as e:
        logger.error(f"[IPC] Send failed: {e}")
        return False


def _start_local_server(window: MainWindow) -> QLocalServer:
    """Start QLocalServer to receive data from new instances."""
    # Remove any stale server
    QLocalServer.removeServer(IPC_SERVER_NAME)

    server = QLocalServer(window)

    def _on_new_connection():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                continue
            # Read data when it arrives
            conn.waitForReadyRead(IPC_TIMEOUT_MS)
            raw = bytes(conn.readAll())
            conn.disconnectFromServer()
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                    logger.info(f"[IPC Server] Received: {data}")
                    # Use QTimer to safely call UI from the main thread
                    QTimer.singleShot(0, lambda d=data: window.receive_external_entry(d))
                except Exception as e:
                    logger.error(f"[IPC Server] Failed to process: {e}")

    server.newConnection.connect(_on_new_connection)

    if not server.listen(IPC_SERVER_NAME):
        logger.error(f"[IPC Server] Failed to start: {server.errorString()}")
    else:
        logger.info(f"[IPC Server] Listening on '{IPC_SERVER_NAME}'")

    return server


def run() -> None:
    url_data = _parse_protocol_url(sys.argv)

    if not _acquire_single_instance_lock():
        # Another instance is already running
        if url_data:
            _configure_logging()
            _send_to_existing_instance(url_data)
        return

    _configure_logging()

    _set_windows_app_id()
    app = ensure_qt_application()
    app.setApplicationName("K캘린더")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    db_dir = runtime_root() / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    repository = EncryptedRepository(db_dir / "taskcalendar.db.enc")

    if not repository.get_setting("toggle_shortcut"):
        repository.set_setting("toggle_shortcut", default_shortcut())
    if not repository.get_setting("memo_toggle_shortcut"):
        repository.set_setting("memo_toggle_shortcut", default_memo_shortcut())
    if not repository.get_setting("auto_start"):
        repository.set_setting("auto_start", "1")
    desired_auto_start = repository.get_setting("auto_start", "1") != "0"
    set_startup_enabled(desired_auto_start)
    repository.set_setting("auto_start", "1" if is_startup_enabled() else "0")
    repository.save()

    window = MainWindow(repository)

    # Start IPC server for receiving data from Chrome extension
    ipc_server = _start_local_server(window)

    # Register protocol handler if not already registered
    from taskcalendar.desktop_services import register_protocol_handler
    register_protocol_handler()

    # Process URL data if this is the first instance launched with a protocol URL
    if url_data:
        QTimer.singleShot(500, lambda: window.receive_external_entry(url_data))

    window.show()
    try:
        from taskcalendar.desktop_services import set_native_window_icon
        set_native_window_icon(int(window.winId()))
    except Exception:
        pass
    window.raise_()
    window.activateWindow()
    app.exec()
