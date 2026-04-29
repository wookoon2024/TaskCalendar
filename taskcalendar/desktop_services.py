from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import winreg
from ctypes import wintypes
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from taskcalendar.models import AlertType, CalendarEntry, EntryType
from taskcalendar.storage import EncryptedRepository

logger = logging.getLogger(__name__)
LRESULT = ctypes.c_ssize_t

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "TaskCalendar"
HOTKEY_ID = 0xB001
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 1
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
TPM_RIGHTBUTTON = 0x0002
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
TRAY_CMD_TOGGLE = 1001
TRAY_CMD_EXIT = 1002


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


def default_shortcut() -> str:
    return "F3"


def normalize_shortcut(shortcut: str) -> str:
    text = (shortcut or "").split(",")[0].strip().replace("Meta", "Win")
    if not text:
        return default_shortcut()
    parts = [part.strip() for part in text.split("+") if part.strip()]
    modifiers: list[str] = []
    key = ""
    for part in parts:
        lowered = part.lower()
        if lowered in {"ctrl", "control"} and "Ctrl" not in modifiers:
            modifiers.append("Ctrl")
        elif lowered == "shift" and "Shift" not in modifiers:
            modifiers.append("Shift")
        elif lowered == "alt" and "Alt" not in modifiers:
            modifiers.append("Alt")
        elif lowered in {"win", "meta"} and "Win" not in modifiers:
            modifiers.append("Win")
        elif not key:
            key = part.upper() if len(part) == 1 else part.upper()
    return "+".join(modifiers + ([key] if key else []))


def _parse_hotkey(shortcut: str) -> tuple[int, int] | None:
    normalized = normalize_shortcut(shortcut)
    parts = [part for part in normalized.split("+") if part]
    modifiers = 0
    key_token = ""
    for part in parts:
        if part == "Ctrl":
            modifiers |= MOD_CONTROL
        elif part == "Shift":
            modifiers |= MOD_SHIFT
        elif part == "Alt":
            modifiers |= MOD_ALT
        elif part == "Win":
            modifiers |= MOD_WIN
        elif not key_token:
            key_token = part
    if not key_token:
        return None
    if not modifiers:
        # Allow function keys as standalone hotkeys (F1~F12 only).
        if key_token.startswith("F") and key_token[1:].isdigit():
            fn = int(key_token[1:])
            if 1 <= fn <= 12:
                return 0, 0x6F + fn
        return None
    if len(key_token) == 1 and key_token.isalpha():
        return modifiers, ord(key_token)
    if len(key_token) == 1 and key_token.isdigit():
        return modifiers, ord(key_token)
    if key_token.startswith("F") and key_token[1:].isdigit():
        number = int(key_token[1:])
        if 1 <= number <= 24:
            return modifiers, 0x6F + number
    special = {
        "SPACE": 0x20,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "RETURN": 0x0D,
        "ESC": 0x1B,
        "ESCAPE": 0x1B,
        "LEFT": 0x25,
        "UP": 0x26,
        "RIGHT": 0x27,
        "DOWN": 0x28,
    }
    vk = special.get(key_token.upper())
    if vk is None:
        return None
    return modifiers, vk


def get_startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    gui_python = Path(sys.executable).with_name("pythonw.exe")
    executable = gui_python if gui_python.exists() else Path(sys.executable)
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{executable}" "{main_path}"'


def _startup_script_path() -> Path:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "TaskCalendar.cmd"


def _read_startup_registry_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("failed to read startup setting")
        return None


def is_startup_enabled() -> bool:
    if _read_startup_registry_value():
        return True
    return _startup_script_path().exists()


def set_startup_enabled(enabled: bool) -> bool:
    desired_command = get_startup_command()
    existing_registry_value = _read_startup_registry_value()
    startup_script = _startup_script_path()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, desired_command)
                if startup_script.exists():
                    startup_script.unlink()
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
                if startup_script.exists():
                    startup_script.unlink()
        return True
    except PermissionError:
        logger.warning("registry startup setting is not writable, falling back to startup folder")
    except OSError:
        logger.exception("failed to update startup setting")

    try:
        startup_script.parent.mkdir(parents=True, exist_ok=True)
        if enabled:
            if existing_registry_value:
                return True
            startup_script.write_text(f"@echo off\nstart \"\" {desired_command}\n", encoding="utf-8")
        else:
            if existing_registry_value:
                return False
            if startup_script.exists():
                startup_script.unlink()
        return True
    except OSError:
        logger.exception("failed to update startup startup-folder shortcut")
        return False


class GlobalHotkeyManager:
    def __init__(self, root: tk.Tk, shortcut: str, callback: Callable[[], None]) -> None:
        self.root = root
        self.callback = callback
        self.shortcut = normalize_shortcut(shortcut)
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._request_queue: queue.Queue = queue.Queue()
        self._event_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._after_id: str | None = None
        self._thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=2)
        self.update_shortcut(self.shortcut)
        self._drain_events()

    def update_shortcut(self, shortcut: str) -> bool:
        normalized = normalize_shortcut(shortcut)
        binding = _parse_hotkey(normalized)
        if binding is None:
            logger.warning("invalid hotkey format: %s", shortcut)
            return False
        previous_shortcut = self.shortcut
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        self._request_queue.put(
            {
                "cmd": "update",
                "shortcut": normalized,
                "binding": binding,
                "response": response_queue,
            }
        )
        try:
            success = bool(response_queue.get(timeout=2))
        except queue.Empty:
            logger.warning("hotkey worker did not respond for: %s", normalized)
            self.shortcut = previous_shortcut
            return False
        if success:
            self.shortcut = normalized
            return True
        self.shortcut = previous_shortcut
        return False

    def _hotkey_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        current_binding: tuple[int, int] | None = None
        self._ready_event.set()

        while True:
            msg = MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._event_queue.put("trigger")

            try:
                request = self._request_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            command = str(request.get("cmd", ""))
            if command == "update":
                response_queue = request["response"]
                normalized = str(request["shortcut"])
                modifiers, vk = request["binding"]
                previous_binding = current_binding
                if current_binding is not None:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    current_binding = None
                if user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
                    current_binding = (modifiers, vk)
                    logger.info("global hotkey registered: %s", normalized)
                    response_queue.put(True)
                    continue
                logger.warning("failed to register hotkey: %s", normalized)
                if previous_binding is not None and user32.RegisterHotKey(None, HOTKEY_ID, previous_binding[0], previous_binding[1]):
                    current_binding = previous_binding
                response_queue.put(False)
                continue

            if command == "close":
                if current_binding is not None:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    current_binding = None
                response_queue = request.get("response")
                if response_queue is not None:
                    response_queue.put(True)
                return

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "trigger":
                logger.info("global hotkey triggered")
                self.callback()
        if self.root.winfo_exists():
            self._after_id = self.root.after(50, self._drain_events)

    def close(self) -> None:
        if self._after_id is not None and self.root.winfo_exists():
            self.root.after_cancel(self._after_id)
            self._after_id = None
        if self._thread is not None and self._thread.is_alive():
            response_queue: queue.Queue = queue.Queue(maxsize=1)
            self._request_queue.put({"cmd": "close", "response": response_queue})
            try:
                response_queue.get(timeout=2)
            except queue.Empty:
                pass
            self._thread.join(timeout=2)
        self._thread = None


class SystemTrayManager:
    def __init__(self, root: tk.Tk, on_toggle: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self.root = root
        self.on_toggle = on_toggle
        self.on_exit = on_exit
        self._thread: threading.Thread | None = None
        self._after_id: str | None = None
        self._event_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._ready_event = threading.Event()
        self._hwnd: int = 0
        self._hicon = None
        self._menu = None
        self._wndproc = None
        self._class_name = "TaskCalendarTrayWindow"
        self._thread = threading.Thread(target=self._run_message_loop, daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=3)
        self._drain_events()

    def _run_message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user32.LoadIconW.restype = wintypes.HICON
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        hinstance = kernel32.GetModuleHandleW(None)

        @WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._event_queue.put("toggle")
                    return 0
                if lparam == WM_RBUTTONUP:
                    self._show_menu(hwnd)
                    return 0
            if msg == WM_COMMAND:
                command = int(wparam) & 0xFFFF
                if command == TRAY_CMD_TOGGLE:
                    self._event_queue.put("toggle")
                    return 0
                if command == TRAY_CMD_EXIT:
                    self._event_queue.put("exit")
                    return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinstance
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            logger.warning("failed to register tray window class, winerr=%s", ctypes.WinError(ctypes.get_last_error()))
            self._ready_event.set()
            return

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            logger.warning("failed to create tray window, winerr=%s", ctypes.WinError(ctypes.get_last_error()))
            self._ready_event.set()
            return
        self._hwnd = hwnd

        icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.ico"
        if icon_path.exists():
            self._hicon = user32.LoadImageW(None, str(icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self._hicon:
            self._hicon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))

        self._menu = user32.CreatePopupMenu()
        user32.AppendMenuW(self._menu, MF_STRING, TRAY_CMD_TOGGLE, "  캘린더 열기 / 숨기기  ")
        user32.AppendMenuW(self._menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(self._menu, MF_STRING, TRAY_CMD_EXIT, "  종료  ")

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self._hicon
        nid.szTip = "TaskCalendar"
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            logger.warning("failed to add tray icon, winerr=%s", ctypes.WinError(ctypes.get_last_error()))
        else:
            logger.info("system tray icon ready")
        self._ready_event.set()

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if self._menu:
            user32.DestroyMenu(self._menu)
            self._menu = None
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = 0
        user32.UnregisterClassW(self._class_name, hinstance)

    def _show_menu(self, hwnd: int) -> None:
        if not self._menu:
            return
        user32 = ctypes.windll.user32
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        user32.TrackPopupMenu(self._menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, 0, 0, 0)

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break
            if event == "toggle":
                self.on_toggle()
            elif event == "exit":
                self.on_exit()
        if self.root.winfo_exists():
            self._after_id = self.root.after(50, self._drain_events)

    def close(self) -> None:
        if self._after_id is not None and self.root.winfo_exists():
            self.root.after_cancel(self._after_id)
            self._after_id = None
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None


class AlertManager:
    def __init__(self, root: tk.Tk, repository: EncryptedRepository, open_day_callback: Callable[[date], None]) -> None:
        self.root = root
        self.repository = repository
        self.open_day_callback = open_day_callback
        self._after_id: str | None = None
        self._last_check = datetime.now() - timedelta(seconds=70)
        self._shown_keys: dict[str, datetime] = {}
        self._toasts: list[tk.Toplevel] = []
        self._after_id = self.root.after(1000, self._poll)

    def _schedule(self) -> None:
        if self.root.winfo_exists():
            self._after_id = self.root.after(30000, self._poll)

    def _poll(self) -> None:
        now = datetime.now()
        check_from = self._last_check
        self._last_check = now
        self._prune_shown_keys(now)
        for target_day in sorted({now.date(), (now + timedelta(days=1)).date()}):
            for entry in self.repository.list_entries_for_day(target_day):
                if entry.entry_type == EntryType.MEMO:
                    continue
                if entry.alert_type != AlertType.POPUP:
                    continue
                if self._is_completed(entry, target_day):
                    continue
                due_at = self._due_datetime(entry, target_day)
                if due_at is None:
                    continue
                alert_key = f"{entry.source_entry_id or entry.entry_id}:{target_day.isoformat()}:{entry.alert_offset}"
                if alert_key in self._shown_keys:
                    continue
                if check_from < due_at <= now:
                    self._shown_keys[alert_key] = now
                    self._show_toast(entry, target_day)
        self._schedule()

    def _show_toast(self, entry: CalendarEntry, target_day: date) -> None:
        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg="#dbe3ec")

        shell = tk.Frame(toast, bg="#ffffff", padx=14, pady=12)
        shell.pack(fill="both", expand=True)

        tk.Label(shell, text="일정 알림", bg="#ffffff", fg="#1f7a67", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        detail = target_day.strftime("%Y.%m.%d")
        if entry.start_time:
            detail += f"  {entry.start_time}"
        tk.Label(shell, text=detail, bg="#ffffff", fg="#667085", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 6))
        tk.Label(shell, text=entry.title, bg="#ffffff", fg="#1f2328", font=("Segoe UI", 11)).pack(anchor="w")
        if entry.description:
            preview = entry.description.strip().splitlines()[0][:36]
            tk.Label(shell, text=preview, bg="#ffffff", fg="#667085", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 8))

        button_row = tk.Frame(shell, bg="#ffffff")
        button_row.pack(fill="x")
        tk.Button(
            button_row,
            text="열기",
            relief="flat",
            bg="#1f7a67",
            fg="#ffffff",
            activebackground="#236f60",
            activeforeground="#ffffff",
            padx=12,
            pady=4,
            command=lambda d=target_day, t=toast: self._open_from_toast(d, t),
        ).pack(side="left")
        tk.Button(
            button_row,
            text="닫기",
            relief="flat",
            bg="#eef2f7",
            fg="#344054",
            activebackground="#dde5ef",
            activeforeground="#344054",
            padx=12,
            pady=4,
            command=lambda t=toast: self._close_toast(t),
        ).pack(side="right")

        toast.update_idletasks()
        self._toasts.append(toast)
        self._reposition_toasts()
        toast.after(12000, lambda t=toast: self._close_toast(t))

    def _open_from_toast(self, target_day: date, toast: tk.Toplevel) -> None:
        self._close_toast(toast)
        self.open_day_callback(target_day)

    def _close_toast(self, toast: tk.Toplevel) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        if toast.winfo_exists():
            toast.destroy()
        self._reposition_toasts()

    def _reposition_toasts(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        bottom_margin = 48
        right_margin = 18
        offset = 0
        for toast in reversed(self._toasts):
            if not toast.winfo_exists():
                continue
            toast.update_idletasks()
            width = toast.winfo_reqwidth()
            height = toast.winfo_reqheight()
            x = max(0, screen_width - width - right_margin)
            y = max(0, screen_height - height - bottom_margin - offset)
            toast.geometry(f"+{x}+{y}")
            offset += height + 10

    @staticmethod
    def _due_datetime(entry: CalendarEntry, target_day: date) -> datetime | None:
        if entry.all_day or not entry.start_time:
            start_at = datetime.combine(target_day, time.min)
        else:
            try:
                hour, minute = (int(part) for part in entry.start_time.split(":", 1))
            except ValueError:
                return None
            start_at = datetime.combine(target_day, time(hour=hour, minute=minute))
        offset_map = {
            "at_start": timedelta(),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "1d": timedelta(days=1),
        }
        return start_at - offset_map.get(entry.alert_offset, timedelta())

    @staticmethod
    def _is_completed(entry: CalendarEntry, target_day: date) -> bool:
        if entry.recurrence_enabled:
            return target_day.isoformat() in entry.completed_dates
        return entry.status == "완료"

    def _prune_shown_keys(self, now: datetime) -> None:
        keep_after = now - timedelta(days=2)
        self._shown_keys = {key: stamp for key, stamp in self._shown_keys.items() if stamp >= keep_after}

    def close(self) -> None:
        if self._after_id is not None and self.root.winfo_exists():
            self.root.after_cancel(self._after_id)
            self._after_id = None
        for toast in list(self._toasts):
            self._close_toast(toast)
