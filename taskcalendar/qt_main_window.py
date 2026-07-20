from __future__ import annotations

import calendar
import ctypes
import json
import logging
import queue
import shutil
import threading
import sys
import time
from datetime import date, datetime, timedelta, time as datetime_time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QObject, QMimeData, Signal, QEvent
from PySide6.QtGui import QAction, QColor, QDrag, QGuiApplication, QIcon, QKeySequence, QPainter, QPageLayout, QPalette, QPixmap, QShortcut, QTransform
from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrintPreviewWidget, QPrinter
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QLineEdit,
    QPushButton,
    QSlider,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QSystemTrayIcon,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.desktop_services import (
    HOTKEY_ID,
    MSG,
    PM_REMOVE,
    WM_HOTKEY,
    _parse_hotkey,
    default_shortcut,
    is_startup_enabled,
    normalize_shortcut,
    set_startup_enabled,
)
from taskcalendar.excel_io import export_entries_to_excel, import_entries_from_excel
from taskcalendar.backup_io import backup_to_zip, restore_from_zip
from taskcalendar.models import AlertType, CalendarEntry, EntryType, Alarm, calculate_next_alarm_trigger
from taskcalendar.paths import asset_path, data_path
from taskcalendar.qt_dialogs import EntryDialog, EntryViewDialog, SettingsDialog, AlarmManagerDialog, BackupRestoreFormatDialog
from taskcalendar.storage import EncryptedRepository
from taskcalendar.themes import THEMES

logger = logging.getLogger(__name__)
ALERT_BOX_WIDTH = 300
ALERT_BOX_HEIGHT = 300
CALENDAR_ENTRY_DRAG_MIME = "application/x-taskcalendar-entry-id"


def app_icon() -> QIcon:
    ico = asset_path("app_icon.ico")
    if ico.exists():
        return QIcon(str(ico))
    png = asset_path("app_icon.png")
    if png.exists():
        return QIcon(str(png))
    return QIcon()


class DraggableFrame(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_active = False
        self._drag_position = QPoint()
        self.main_window = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            self.setCursor(Qt.SizeAllCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_active and event.buttons() == Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_position
            parent = self.parentWidget()
            if parent:
                rect = parent.rect()
                new_pos.setX(max(0, min(new_pos.x(), rect.width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), rect.height() - self.height())))
            self.move(new_pos)
            if self.main_window is not None:
                self.main_window._sticker_toolbar_manually_moved = True
                self.main_window._sticker_toolbar_pos = new_pos
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_active = False
            self.unsetCursor()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class _HotkeySignal(QObject):
    triggered = Signal()


class QtGlobalHotkeyManager:
    def __init__(self, shortcut: str, callback) -> None:
        self.shortcut = normalize_shortcut(shortcut)
        self._signal = _HotkeySignal()
        self._signal.triggered.connect(callback)
        self._running = True
        self._request_queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)
        self.update_shortcut(self.shortcut)

    def update_shortcut(self, shortcut: str) -> bool:
        normalized = normalize_shortcut(shortcut)
        binding = _parse_hotkey(normalized)
        if binding is None:
            return False
        response: queue.Queue = queue.Queue(maxsize=1)
        self._request_queue.put(("register", binding, normalized, response))
        try:
            ok = bool(response.get(timeout=1.2))
        except queue.Empty:
            ok = False
        if ok:
            self.shortcut = normalized
        return ok

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._request_queue.put(("stop", None, None, None))
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int

        registered = False
        self._ready.set()
        while self._running:
            try:
                while True:
                    action, binding, _shortcut, response = self._request_queue.get_nowait()
                    if action == "stop":
                        self._running = False
                        if response is not None:
                            response.put(True)
                        break
                    if action == "register":
                        if registered:
                            user32.UnregisterHotKey(None, HOTKEY_ID)
                            registered = False
                        if binding is None:
                            if response is not None:
                                response.put(False)
                            continue
                        modifiers, vk = binding
                        ok = bool(user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk))
                        registered = ok
                        if response is not None:
                            response.put(ok)
            except queue.Empty:
                pass

            msg = MSG()
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self._signal.triggered.emit()
            time.sleep(0.03)

        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)


def app_stylesheet(p: dict[str, str]) -> str:
    return f"""
    QMainWindow {{
        background: {p['bg']};
        color: {p['text']};
        font-family: 'Segoe UI', 'Segoe UI Emoji';
        font-size: 13px;
    }}
    QFrame#panel, QFrame#calendarPanel, QFrame#sidebarPanel {{
        background: {p['panel']};
        border: 1px solid {p['line']};
        border-radius: 10px;
    }}
    QFrame#softPanel {{
        background: {p['panel_alt']};
        border: 1px solid {p['line_soft']};
        border-radius: 10px;
    }}
    QFrame#donePanel {{
        background: {p.get('done_panel_qt', '#d7dce2')};
        border: 1px solid {p['line_soft']};
        border-radius: 10px;
    }}
    QLabel#title {{
        font-size: 28px;
        font-weight: 700;
        color: {p['text']};
    }}
    QLabel#muted {{
        color: {p['muted']};
    }}
    QPushButton#topbarButton {{
        background: {p['panel']};
        color: {p['text']};
        border: 1px solid {p['line']};
        border-radius: 6px;
        padding: 5px 10px;
    }}
    QPushButton#primary {{
        background: {p['accent']};
        color: #fff;
        border: 1px solid {p['accent']};
        border-radius: 6px;
        padding: 5px 10px;
    }}
    QPushButton:focus, QToolButton:focus {{
        outline: none;
    }}
    QToolTip {{
        background-color: #202428;
        color: #ffffff;
        border: 1px solid #4b5563;
        padding: 4px 6px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 2px 4px 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['line']};
        min-height: 24px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {p['muted']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """


def day_cell_style(p: dict[str, str], in_month: bool, is_today: bool, is_selected: bool) -> str:
    bg = p["panel"] if in_month else p["panel_alt"]
    border_color = p["line"]
    if is_selected:
        bg = p.get("accent_soft", "#FFF7CC")
        border_color = p["accent"]
    return f"QFrame {{ background: {bg}; border: 1px solid {border_color}; }}"


def badge_style(p: dict[str, str], selected: bool) -> str:
    if selected:
        return f"background: {p['badge_selected_bg']}; color: {p['badge_selected_fg']}; border-radius: 8px; padding: 1px 6px;"
    return f"background: {p['badge_today_bg']}; color: {p['badge_today_fg']}; border-radius: 8px; padding: 1px 6px;"


def entry_chip_style(p: dict[str, str]) -> str:
    return "background: transparent; border: none; text-align: left; padding: 0px; margin: 0px;"


def text_view_style(p: dict[str, str]) -> str:
    return (
        f"background: {p['panel']};"
        f"color: {p['text']};"
        f"border: 1px solid {p['line_soft']};"
        "border-radius: 6px; padding: 5px;"
    )

def text_editor_style(p: dict[str, str]) -> str:
    return (
        f"QTextEdit {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['line_soft']}; border-radius: 6px; padding: 5px; }}"
        f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px 4px 0px; }}"
        f"QScrollBar::handle:vertical {{ background: {p['line']}; min-height: 24px; border-radius: 4px; }}"
        f"QScrollBar::handle:vertical:hover {{ background: {p['muted']}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
    )


class DayCell(QFrame):
    def __init__(self, parent, callback_select, callback_add, callback_move_entry) -> None:
        super().__init__(parent)
        self.callback_select = callback_select
        self.callback_add = callback_add
        self.callback_move_entry = callback_move_entry
        self.day_value: date | None = None

        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 1, 0, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.number_label = QLabel("")
        self.number_label.setStyleSheet("font-size: 11pt; background: transparent;")
        header.addWidget(self.number_label)
        header.addStretch(1)
        self.badge_label = QLabel("")
        self.badge_label.hide()
        header.addWidget(self.badge_label)
        layout.addLayout(header)

        self.items_layout = QVBoxLayout()
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(1)
        layout.addLayout(self.items_layout)
        layout.addStretch(1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.day_value is not None:
            self.callback_select(self.day_value)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.day_value is not None:
            self.callback_add(self.day_value)
        super().mouseDoubleClickEvent(event)

    def clear_items(self) -> None:
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        payload = self._parse_drag_payload(event.mimeData())
        if payload is not None and self.day_value is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        payload = self._parse_drag_payload(event.mimeData())
        if payload is not None and self.day_value is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        payload = self._parse_drag_payload(event.mimeData())
        if payload is None or self.day_value is None:
            event.ignore()
            return
        entry_id = int(payload.get("entry_id", 0))
        source_day_text = str(payload.get("source_day", "")).strip()
        try:
            source_day = date.fromisoformat(source_day_text)
        except ValueError:
            event.ignore()
            return
        moved = bool(self.callback_move_entry(entry_id, source_day, self.day_value))
        if moved:
            event.acceptProposedAction()
            return
        event.ignore()

    @staticmethod
    def _parse_drag_payload(mime_data: QMimeData) -> dict[str, object] | None:
        if not mime_data.hasFormat(CALENDAR_ENTRY_DRAG_MIME):
            return None
        raw = bytes(mime_data.data(CALENDAR_ENTRY_DRAG_MIME)).decode("utf-8", errors="ignore").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if "entry_id" not in payload or "source_day" not in payload:
            return None
        return payload


class DraggableCalendarEntryChip(QFrame):
    def __init__(self, parent, entry: CalendarEntry, source_day: date, title_text: str, time_text: str, entry_fg: str, time_fg: str, on_edit, completed: bool = False, bg_color: str = "") -> None:
        super().__init__(parent)
        self.entry = entry
        self.source_day = source_day
        self._on_edit = on_edit
        self._press_pos: QPoint | None = None
        self.setCursor(Qt.PointingHandCursor)
        chip_bg = str(bg_color or "").strip()
        if chip_bg:
            self.setStyleSheet(f"background: {chip_bg}; border: none; border-radius: 4px;")
        else:
            self.setStyleSheet("background: transparent; border: none;")

        chip_layout = QHBoxLayout(self)
        chip_layout.setContentsMargins(3, 0, 3, 0)
        chip_layout.setSpacing(0)
        if time_text:
            time_label = QLabel(time_text)
            time_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            strike = " text-decoration: line-through;" if completed else ""
            time_label.setStyleSheet(f"color: {time_fg}; background: transparent; border: none;{strike}")
            chip_layout.addWidget(time_label)
        chip = QLabel(title_text)
        chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        chip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        strike = " text-decoration: line-through;" if completed else ""
        chip.setStyleSheet(f"color: {entry_fg}; background: transparent; border: none; padding: 0px; margin: 0px;{strike}")
        chip_layout.addWidget(chip, 1, Qt.AlignLeft)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is not None and (event.buttons() & Qt.LeftButton):
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                if self._start_drag():
                    self._press_pos = None
                    event.accept()
                    return
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if callable(self._on_edit):
            self._on_edit()
        event.accept()

    def _start_drag(self) -> bool:
        if self.entry.entry_id is None:
            return False
        payload = {
            "entry_id": int(self.entry.entry_id),
            "source_day": self.source_day.isoformat(),
        }
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(CALENDAR_ENTRY_DRAG_MIME, json.dumps(payload).encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        return True


class EntryLabel(QLabel):
    def __init__(self, text: str, on_double_click, parent=None) -> None:
        super().__init__(text, parent)
        self._on_double_click = on_double_click
        self.setCursor(Qt.PointingHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if callable(self._on_double_click):
            self._on_double_click()
        event.accept()


class ClickableLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ClickableTextEdit(QTextEdit):
    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.viewport().setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StickerItem(QLabel):
    def __init__(self, parent, sticker_id: str, on_press, on_drag) -> None:
        super().__init__(parent)
        self.sticker_id = sticker_id
        self._on_press = on_press
        self._on_drag = on_drag
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.OpenHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.ClosedHandCursor)
        self._on_press(event, self.sticker_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._on_drag(event, self.sticker_id)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = max(8, self.contentsRect().width())
        elided = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        QLabel.setText(self, elided)


class MemoDragCard(QFrame):
    reordered = Signal(int, int, bool)
    _MIME_TYPE = "application/x-taskcalendar-memo-id"

    def __init__(self, parent, memo_id: int, drag_enabled: bool) -> None:
        super().__init__(parent)
        self.memo_id = int(memo_id)
        self.drag_enabled = bool(drag_enabled)
        self._press_pos: QPoint | None = None
        self.setAcceptDrops(self.drag_enabled)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.drag_enabled and event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.drag_enabled and self._press_pos is not None and (event.buttons() & Qt.LeftButton):
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_drag()
                self._press_pos = None
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._is_valid_drag(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._is_valid_drag(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        source_id = self._drag_source_id(event)
        if source_id is None or source_id == self.memo_id:
            event.ignore()
            return
        before = event.position().y() < (self.height() / 2.0)
        self.reordered.emit(int(source_id), int(self.memo_id), bool(before))
        event.acceptProposedAction()

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._MIME_TYPE, str(self.memo_id).encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.exec(Qt.MoveAction)

    def _is_valid_drag(self, event) -> bool:
        if not self.drag_enabled:
            return False
        source_id = self._drag_source_id(event)
        return source_id is not None and source_id != self.memo_id

    def _drag_source_id(self, event) -> int | None:
        data = event.mimeData().data(self._MIME_TYPE)
        if not data:
            return None
        try:
            return int(bytes(data).decode("utf-8"))
        except (TypeError, ValueError):
            return None


class TodayScheduleButton(QPushButton):
    def __init__(self, parent=None) -> None:
        super().__init__("오늘 일정", parent)
        self._count = 0

    def set_count(self, count: int) -> None:
        self._count = max(0, int(count))
        self.updateGeometry()
        self.update()

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        metrics = self.fontMetrics()
        prefix = "오늘 일정"
        suffix = f" {self._count}개" if self._count > 0 else ""
        text_w = metrics.horizontalAdvance(prefix + suffix)
        # keep horizontal padding generous to avoid clipping on different DPI
        min_w = text_w + 24
        if hint.width() < min_w:
            hint.setWidth(min_w)
        return hint

    def minimumSizeHint(self):  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""

        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)

        rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        metrics = painter.fontMetrics()

        prefix = "오늘 일정"
        count_text = f"{self._count}" if self._count > 0 else ""
        unit_text = "개" if self._count > 0 else ""
        gap = " "

        prefix_w = metrics.horizontalAdvance(prefix)
        gap_w = metrics.horizontalAdvance(gap) if count_text else 0

        bold_font = painter.font()
        bold_font.setBold(True)
        painter.setFont(bold_font)
        bold_metrics = painter.fontMetrics()
        count_w = bold_metrics.horizontalAdvance(count_text) if count_text else 0

        painter.setFont(self.font())
        unit_w = metrics.horizontalAdvance(unit_text) if unit_text else 0
        total_w = prefix_w + gap_w + count_w + unit_w

        x = rect.x() + max(0, (rect.width() - total_w) // 2)
        y = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) // 2

        normal_color = self.palette().color(self.foregroundRole())
        painter.setPen(normal_color)
        painter.drawText(x, y, prefix)

        if count_text:
            painter.setFont(bold_font)
            painter.setPen(QColor("#2E5AAC"))
            painter.drawText(x + prefix_w + gap_w, y, count_text)

            painter.setFont(self.font())
            painter.setPen(normal_color)
            painter.drawText(x + prefix_w + gap_w + count_w, y, unit_text)


class AlertToast(QWidget):
    closed = Signal(object)

    def __init__(self, palette: dict[str, str], title: str, detail: str) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedSize(ALERT_BOX_WIDTH, ALERT_BOX_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        panel = QFrame(self)
        panel.setObjectName("alertPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        app_label = QLabel("캘린더")
        app_label.setStyleSheet(f"color: {palette['accent']}; font-size: 11pt; font-weight: 700; background: transparent; border: none;")
        header.addWidget(app_label)
        header.addStretch(1)
        layout.addLayout(header)

        title_label = QLabel(title.strip() or "일정 알림")
        title_label.setWordWrap(True)
        title_label.setStyleSheet(f"color: {palette['text']}; font-size: 15pt; font-weight: 700; background: transparent; border: none;")
        layout.addWidget(title_label)

        detail_label = QLabel(detail.strip())
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(f"color: {palette['muted']}; font-size: 12pt; background: transparent; border: none;")
        layout.addWidget(detail_label)
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        ok_btn = QPushButton("확인")
        ok_btn.setObjectName("topbarButton")
        ok_btn.clicked.connect(self.close)
        actions.addWidget(ok_btn)
        layout.addLayout(actions)

        root.addWidget(panel)
        self.setStyleSheet(
            f"QFrame#alertPanel {{ background: {palette['panel']}; border: 1px solid {palette['line']}; border-radius: 12px; }}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit(self)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, repository: EncryptedRepository) -> None:
        super().__init__()
        self.repository = repository
        self.theme_name = self.repository.get_setting("theme", "light")
        self.palette = THEMES[self.theme_name]

        today = date.today()
        self.current_year = today.year
        self.current_month = today.month
        self.selected_day = today
        self.sidebar_mode = "day"
        self.day_cells: list[DayCell] = []
        self._sticker_store: dict[str, list[dict[str, object]]] = self._load_sticker_store()
        self._sticker_assets: dict[str, QPixmap] = self._load_sticker_assets()
        self._sticker_favorites: list[str] = self._load_sticker_pref_list("sticker_favorites_v1")
        self._sticker_recent: list[str] = self._load_sticker_pref_list("sticker_recent_v1")
        self._sticker_animation_assets: dict[str, dict[str, object]] = self._load_sticker_animation_assets()
        self._sticker_animation_state: dict[str, dict[str, float | int]] = {}
        self._sticker_anim_timer: QTimer | None = None
        self._sticker_animation_enabled = self.repository.get_setting("sticker_animation_enabled", "1") == "1"
        self.hide_completed_on_calendar = self.repository.get_setting("hide_completed_on_calendar", "1") == "1"
        self._action_icons: dict[str, QIcon] = self._load_action_icons()
        self._holidays_fixed, self._holidays_yearly = self._load_holidays()
        self.memo_title_only = self.repository.get_setting("memo_title_only", "0") == "1"
        self.search_query = ""
        self.search_results: list[CalendarEntry] = []
        self._memo_card_widgets: dict[int, QWidget] = {}
        self._pending_scroll_memo_id: int | None = None
        self._sticker_widgets: dict[str, StickerItem] = {}
        self._sticker_edit_mode = False
        self._selected_sticker_id: str | None = None
        self._sticker_snapshot: dict[str, list[dict[str, object]]] | None = None
        self._drag_offset = QPoint(0, 0)
        self._pending_sticker_rebase_size: tuple[int, int, int, int] | None = None
        self._sticker_rebase_scheduled = False
        self._sticker_rebase_last_size: tuple[int, int] | None = None
        self._save_stickers_after_rebase = False
        self._sticker_toolbar_manually_moved = False
        self._sticker_toolbar_pos = QPoint(0, 0)
        self._last_calendar_item_capacity = 2
        self._did_initial_sticker_sync = False
        self._did_onboarding_check = False
        self._band_baseline_cell_h: int | None = None
        self._band_baseline_cell_w: int | None = None
        self._alert_timer: QTimer | None = None
        self._last_alert_check = datetime.now() - timedelta(seconds=70)
        self._shown_alert_keys: dict[str, datetime] = {}
        self._active_alert_boxes: list[AlertToast] = []
        self._force_exit = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.hotkey_manager: QtGlobalHotkeyManager | None = None
        self._sticker_nudge_shortcuts: list[QShortcut] = []
        self._calendar_nav_shortcuts: list[QShortcut] = []
        self._last_window_was_maximized = False
        self._last_normal_geometry = QRect(120, 120, 1024, 640)
        self._suspend_window_state_tracking = False
        self._calendar_rerender_pending = False
        self._window_state_dirty = False

        self.setWindowTitle("캘린더")
        self.setWindowIcon(app_icon())
        self.resize(1024, 640)
        self.setMinimumSize(980, 620)
        self._load_window_state()
        self._apply_initial_window_state()

        self._build()
        QApplication.instance().installEventFilter(self)
        self._setup_calendar_navigation_shortcuts()
        self._setup_sticker_nudge_shortcuts()
        self._sanitize_sticker_preferences()
        self._setup_tray_icon()
        self._setup_global_hotkey()
        self._setup_sticker_animation_timer()
        self._setup_alert_timer()
        self.refresh()
        self._perform_auto_backup()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and self._handle_calendar_navigation_key(event):
            event.accept()
            return True
        if event.type() == QEvent.Type.Wheel and self._is_calendar_wheel_target(watched):
            delta_y = event.angleDelta().y()
            if delta_y > 0:
                self._change_month(-1)
                event.accept()
                return True
            if delta_y < 0:
                self._change_month(1)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _build(self) -> None:
        root = QWidget()
        self.root_widget = root
        self.setCentralWidget(root)
        self.setStyleSheet(app_stylesheet(self.palette))
        self._apply_tooltip_palette()

        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        topbar = QFrame()
        self.topbar = topbar
        topbar.setObjectName("panel")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(10, 10, 10, 10)
        topbar_layout.setSpacing(6)

        left = QHBoxLayout()
        left.setSpacing(6)
        self.prev_button = self._top_button("<", 32)
        self.prev_button.clicked.connect(lambda: self._change_month(-1))
        left.addWidget(self.prev_button)

        self.year_button = self._top_button("")
        self.year_button.clicked.connect(self._show_year_menu)
        left.addWidget(self.year_button)

        self.month_button = self._top_button("")
        self.month_button.clicked.connect(self._show_month_menu)
        left.addWidget(self.month_button)

        today_button = self._top_button("오늘")
        today_button.clicked.connect(self._go_today)
        left.addWidget(today_button)
        self.next_button = self._top_button(">", 32)
        self.next_button.clicked.connect(lambda: self._change_month(1))
        left.addWidget(self.next_button)
        topbar_layout.addStretch(1)
        topbar_layout.addLayout(left)
        topbar_layout.addStretch(1)

        right = QHBoxLayout()
        right.setSpacing(6)
        self.schedule_button = TodayScheduleButton(self)
        self.schedule_button.setObjectName("topbarButton")
        self.schedule_button.clicked.connect(self._go_today)
        right.addWidget(self.schedule_button)

        self.memo_button = self._top_button("메모")
        self.memo_button.clicked.connect(lambda: self._set_sidebar_mode("memo"))
        right.addWidget(self.memo_button)

        self.alarm_button = self._top_button("알림")
        self.alarm_button.clicked.connect(self._open_alarm_settings)
        right.addWidget(self.alarm_button)

        settings_button = self._top_button("환경설정")
        settings_button.clicked.connect(self._open_settings)
        right.addWidget(settings_button)
        topbar_layout.addLayout(right)
        outer.addWidget(topbar)

        body = QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, 1)

        calendar_panel = QFrame()
        self.calendar_panel = calendar_panel
        calendar_panel.setObjectName("calendarPanel")
        calendar_layout = QVBoxLayout(calendar_panel)
        calendar_layout.setContentsMargins(12, 12, 12, 12)
        calendar_layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        self.calendar_title = QLabel("")
        self.calendar_title.setObjectName("title")
        title_box.addWidget(self.calendar_title)
        header.addLayout(title_box)
        header.addStretch(1)
        search_wrap = QHBoxLayout()
        search_wrap.setContentsMargins(0, 0, 0, 0)
        search_wrap.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색어 입력")
        self.search_input.setFixedWidth(140)
        self.search_input.returnPressed.connect(self._run_search)
        search_wrap.addWidget(self.search_input)
        self.search_button = QPushButton("검색")
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self._run_search)
        search_wrap.addWidget(self.search_button)
        header.addLayout(search_wrap)
        self.print_button = self._top_button("인쇄")
        self.print_button.clicked.connect(self._print_calendar_view)
        header.addWidget(self.print_button)
        self.decorate_button = self._top_button("꾸미기")
        self.decorate_button.clicked.connect(self._enter_sticker_edit_mode)
        header.addWidget(self.decorate_button)
        self.decorate_done_button = self._top_button("완료")
        self.decorate_done_button.clicked.connect(self._complete_sticker_edit_mode)
        self.decorate_done_button.hide()
        header.addWidget(self.decorate_done_button)
        self.decorate_cancel_button = self._top_button("취소")
        self.decorate_cancel_button.clicked.connect(self._cancel_sticker_edit_mode)
        self.decorate_cancel_button.hide()
        header.addWidget(self.decorate_cancel_button)
        calendar_layout.addLayout(header)

        self.sticker_toolbar = DraggableFrame()
        self.sticker_toolbar.main_window = self
        self.sticker_toolbar.setObjectName("stickerToolbar")
        sticker_toolbar_layout = QHBoxLayout(self.sticker_toolbar)
        sticker_toolbar_layout.setContentsMargins(10, 8, 10, 8)
        sticker_toolbar_layout.setSpacing(6)
        
        # Drag Handle
        drag_handle = QLabel("||")
        drag_handle.setObjectName("stickerDragHandle")
        drag_handle.setCursor(Qt.SizeAllCursor)
        drag_handle.setFixedWidth(16)
        drag_handle.setFixedHeight(20)
        drag_handle.setAlignment(Qt.AlignCenter)
        sticker_toolbar_layout.addWidget(drag_handle)
        
        sticker_label = QLabel("스티커")
        sticker_label.setObjectName("stickerToolbarLabel")
        sticker_toolbar_layout.addWidget(sticker_label)
        self.sticker_palette_host = QWidget()
        self.sticker_palette_layout = QHBoxLayout(self.sticker_palette_host)
        self.sticker_palette_layout.setContentsMargins(0, 0, 0, 0)
        self.sticker_palette_layout.setSpacing(4)
        self.sticker_palette_buttons: list[QToolButton] = []
        sticker_toolbar_layout.addWidget(self.sticker_palette_host)
        palette_divider = QFrame()
        palette_divider.setObjectName("stickerDivider")
        palette_divider.setFrameShape(QFrame.VLine)
        sticker_toolbar_layout.addWidget(palette_divider)
        self.sticker_library_button = self._top_button("설정")
        self.sticker_library_button.setObjectName("stickerLibraryButton")
        self.sticker_library_button.setFixedWidth(74)
        self.sticker_library_button.setFixedHeight(28)
        self.sticker_library_button.clicked.connect(self._open_sticker_library)
        sticker_toolbar_layout.addWidget(self.sticker_library_button)
        controls_divider = QFrame()
        controls_divider.setObjectName("stickerDivider")
        controls_divider.setFrameShape(QFrame.VLine)
        sticker_toolbar_layout.addWidget(controls_divider)
        scale_label = QLabel("크기")
        scale_label.setObjectName("stickerToolbarLabel")
        sticker_toolbar_layout.addWidget(scale_label)
        self.sticker_scale_slider = QSlider(Qt.Horizontal)
        self.sticker_scale_slider.setObjectName("stickerScaleSlider")
        self.sticker_scale_slider.setRange(60, 360)
        self.sticker_scale_slider.setValue(100)
        self.sticker_scale_slider.valueChanged.connect(self._on_sticker_scale_changed)
        self.sticker_scale_slider.setFixedWidth(120)
        sticker_toolbar_layout.addWidget(self.sticker_scale_slider)
        rotate_left = QPushButton("↺")
        rotate_left.setObjectName("stickerMiniButton")
        rotate_left.setFixedWidth(30)
        rotate_left.setFixedHeight(28)
        rotate_left.clicked.connect(lambda: self._rotate_selected_sticker(-15))
        sticker_toolbar_layout.addWidget(rotate_left)
        rotate_right = QPushButton("↻")
        rotate_right.setObjectName("stickerMiniButton")
        rotate_right.setFixedWidth(30)
        rotate_right.setFixedHeight(28)
        rotate_right.clicked.connect(lambda: self._rotate_selected_sticker(15))
        sticker_toolbar_layout.addWidget(rotate_right)
        delete_button = QPushButton("삭제")
        delete_button.setObjectName("stickerDangerButton")
        delete_button.setFixedHeight(28)
        delete_button.clicked.connect(self._delete_selected_sticker)
        sticker_toolbar_layout.addWidget(delete_button)
        
        # Stretch to push close button to the right
        sticker_toolbar_layout.addStretch(1)
        
        # Close Button
        self.sticker_close_button = QPushButton("✕")
        self.sticker_close_button.setObjectName("stickerCloseButton")
        self.sticker_close_button.setFixedWidth(24)
        self.sticker_close_button.setFixedHeight(24)
        self.sticker_close_button.clicked.connect(self._on_sticker_close_clicked)
        sticker_toolbar_layout.addWidget(self.sticker_close_button)
        
        self.sticker_toolbar.hide()
        self._rebuild_sticker_palette_buttons()

        self.calendar_grid_widget = QWidget()
        self.calendar_grid_widget.setAttribute(Qt.WA_StyledBackground, True)
        self.calendar_grid_widget.setStyleSheet("background: transparent;")
        self.calendar_grid_widget.installEventFilter(self)
        self.calendar_grid = QGridLayout(self.calendar_grid_widget)
        self.calendar_grid.setContentsMargins(0, 0, 0, 0)
        self.calendar_grid.setHorizontalSpacing(0)
        self.calendar_grid.setVerticalSpacing(0)
        self.weekday_labels: list[QLabel] = []
        for col, text in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.installEventFilter(self)
            self.weekday_labels.append(label)
            self.calendar_grid.addWidget(label, 0, col)
            self.calendar_grid.setColumnStretch(col, 1)
        self.calendar_grid.setRowMinimumHeight(0, 28)
        for row in range(6):
            self.calendar_grid.setRowStretch(row + 1, 1)
            for col in range(7):
                cell = DayCell(self, self._select_day_by_date, self._open_add_for_day, self._move_calendar_entry)
                cell.installEventFilter(self)
                self.day_cells.append(cell)
                self.calendar_grid.addWidget(cell, row + 1, col)
        self.sticker_overlay = QWidget(self.calendar_grid_widget)
        self.sticker_overlay.setAttribute(Qt.WA_StyledBackground, True)
        self.sticker_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.sticker_overlay.setStyleSheet("background: transparent;")
        self.sticker_toolbar.setParent(self.root_widget)
        self.sticker_toolbar.setAttribute(Qt.WA_StyledBackground, True)
        self.sticker_toolbar.setStyleSheet(
            "background: #FFF6CC;"
            "border: 1px solid #E6D79A;"
            "border-radius: 10px;"
        )
        self.sticker_toolbar.hide()
        self.sticker_overlay.raise_()
        calendar_layout.addWidget(self.calendar_grid_widget, 1)
        body.addWidget(calendar_panel, 1)

        self.sidebar_panel = QFrame()
        self.sidebar_panel.setObjectName("sidebarPanel")
        self.sidebar_panel.setFixedWidth(290)
        sidebar_layout = QVBoxLayout(self.sidebar_panel)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        info_card = QFrame()
        info_card.setObjectName("panel")
        info_layout = QHBoxLayout(info_card)
        info_layout.setContentsMargins(10, 10, 10, 10)
        info_layout.setSpacing(8)
        self.info_title = QLabel("")
        info_layout.addWidget(self.info_title, 1)
        self.info_export_button = QPushButton("엑셀저장")
        self.info_export_button.setObjectName("topbarButton")
        self.info_export_button.setFixedWidth(78)
        self.info_export_button.clicked.connect(self._export_search_results_to_excel)
        self.info_export_button.hide()
        info_layout.addWidget(self.info_export_button)
        self.info_add_button = QPushButton("일정 추가")
        self.info_add_button.setObjectName("primary")
        self.info_add_button.clicked.connect(self._handle_add_button)
        info_layout.addWidget(self.info_add_button)
        sidebar_layout.addWidget(info_card)
        self.memo_title_only_check = QCheckBox("제목만보기")
        self.memo_title_only_check.setChecked(self.memo_title_only)
        self.memo_title_only_check.toggled.connect(self._on_memo_title_only_toggled)
        self.memo_title_only_check.hide()
        sidebar_layout.addWidget(self.memo_title_only_check, 0, Qt.AlignRight)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar_scroll.setFrameShape(QFrame.NoFrame)
        self.sidebar_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self.sidebar_content = QWidget()
        self.sidebar_content.setAttribute(Qt.WA_StyledBackground, True)
        self.sidebar_layout = QVBoxLayout(self.sidebar_content)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(8)
        self.sidebar_layout.addStretch(1)
        self.sidebar_scroll.setWidget(self.sidebar_content)
        sidebar_layout.addWidget(self.sidebar_scroll, 1)
        body.addWidget(self.sidebar_panel)
        self._sync_sticker_overlay()
        self._apply_clickable_cursor()

    def _apply_clickable_cursor(self, root: QWidget | None = None) -> None:
        target = root or self
        for button in target.findChildren(QAbstractButton):
            button.setCursor(Qt.PointingHandCursor)

    def _apply_tooltip_palette(self) -> None:
        palette = QToolTip.palette()
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(group, QPalette.ToolTipBase, QColor("#202428"))
            palette.setColor(group, QPalette.ToolTipText, QColor("#ffffff"))
            palette.setColor(group, QPalette.Window, QColor("#202428"))
            palette.setColor(group, QPalette.WindowText, QColor("#ffffff"))
        QToolTip.setPalette(palette)

    def _setup_sticker_nudge_shortcuts(self) -> None:
        bindings = [
            ("Left", -1, 0),
            ("Right", 1, 0),
            ("Up", 0, -1),
            ("Down", 0, 1),
        ]
        for key, dx, dy in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda dx=dx, dy=dy: self._nudge_selected_sticker(dx, dy))
            self._sticker_nudge_shortcuts.append(shortcut)

    def _setup_calendar_navigation_shortcuts(self) -> None:
        bindings = [
            ("Left", lambda: self._change_month(-1)),
            ("Right", lambda: self._change_month(1)),
            ("Up", lambda: self._change_year(1)),
            ("Down", lambda: self._change_year(-1)),
        ]
        for key, callback in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda cb=callback: self._handle_calendar_navigation(cb))
            self._calendar_nav_shortcuts.append(shortcut)

    def _handle_calendar_navigation(self, callback) -> None:
        if self._sticker_edit_mode and self._selected_sticker_id:
            return
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QComboBox)):
            return
        if callable(callback):
            callback()

    def _handle_calendar_navigation_key(self, event) -> bool:
        if not self.isActiveWindow():
            return False
        if self._sticker_edit_mode and self._selected_sticker_id:
            return False
        focus_widget = QApplication.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QComboBox)):
            return False
        if event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        if event.key() == Qt.Key_Left:
            self._change_month(-1)
            return True
        if event.key() == Qt.Key_Right:
            self._change_month(1)
            return True
        if event.key() == Qt.Key_Up:
            self._change_year(1)
            return True
        if event.key() == Qt.Key_Down:
            self._change_year(-1)
            return True
        return False

    def _top_button(self, text: str, width: int | None = None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("topbarButton")
        if width is not None:
            button.setFixedWidth(width)
        return button

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self)
        tray.setIcon(app_icon())
        tray.setToolTip("캘린더")
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                padding: 6px 0;
            }
            QMenu::item {
                padding: 8px 20px;
            }
            """
        )
        open_action = menu.addAction("열기 / 숨기기")
        open_action.triggered.connect(self._toggle_window_visibility)
        add_action = menu.addAction("새 일정 추가")
        add_action.triggered.connect(self._open_add_from_tray)
        settings_action = menu.addAction("환경설정")
        settings_action.triggered.connect(self._open_settings_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("종료")
        quit_action.triggered.connect(self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray_icon = tray

    def _quit_from_tray(self) -> None:
        self._force_exit = True
        self.close()

    def _open_settings_from_tray(self) -> None:
        self._show_from_tray()
        self._open_settings()

    def _open_add_from_tray(self) -> None:
        self._show_from_tray()
        self._edit_entry(EntryType.SCHEDULE, None)

    def _setup_alert_timer(self) -> None:
        timer = QTimer(self)
        timer.setInterval(30000)
        timer.timeout.connect(self._poll_alerts)
        self._alert_timer = timer
        # Initial pass shortly after startup so immediate-due items are caught.
        QTimer.singleShot(1200, self._poll_alerts)
        timer.start()

    def _poll_alerts(self) -> None:
        now = datetime.now()
        check_from = self._last_alert_check
        self._last_alert_check = now

        self._poll_alarms(check_from, now)

        keep_after = now - timedelta(days=2)
        self._shown_alert_keys = {k: v for k, v in self._shown_alert_keys.items() if v >= keep_after}
        targets = sorted({now.date(), (now + timedelta(days=1)).date()})
        for target_day in targets:
            for entry in self.repository.list_entries_for_day(target_day):
                if entry.entry_type == EntryType.MEMO:
                    continue
                if entry.alert_type != AlertType.POPUP:
                    continue
                if self._is_entry_completed_on_day(entry, target_day):
                    continue
                due_at = self._alert_due_datetime(entry, target_day)
                if due_at is None:
                    continue
                key = f"{entry.source_entry_id or entry.entry_id}:{target_day.isoformat()}:{entry.alert_offset}"
                if key in self._shown_alert_keys:
                    continue
                if check_from < due_at <= now:
                    self._shown_alert_keys[key] = now
                    self._show_alert(entry, target_day)

    @staticmethod
    def _alert_due_datetime(entry: CalendarEntry, target_day: date) -> datetime | None:
        if entry.all_day or not entry.start_time:
            start_at = datetime.combine(target_day, datetime.min.time())
        else:
            try:
                hour, minute = (int(part) for part in entry.start_time.split(":", 1))
            except ValueError:
                return None
            start_at = datetime(target_day.year, target_day.month, target_day.day, hour, minute)
        offset_map = {
            "at_start": timedelta(),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "1d": timedelta(days=1),
        }
        return start_at - offset_map.get(entry.alert_offset, timedelta())

    def _show_alert(self, entry: CalendarEntry, target_day: date) -> None:
        detail = target_day.strftime("%Y.%m.%d")
        if entry.start_time:
            detail = f"{detail} {entry.start_time}"
        box = AlertToast(
            self.palette,
            f"일정 알림: {entry.title}",
            detail,
        )
        box.closed.connect(self._on_alert_box_closed)
        self._active_alert_boxes.append(box)
        box.show()
        self._reposition_alert_boxes()
        box.raise_()

    def _poll_alarms(self, check_from: datetime, now: datetime) -> None:
        targets = []
        d = check_from.date()
        while d <= now.date():
            targets.append(d)
            d += timedelta(days=1)

        try:
            alarms = self.repository.list_alarms()
        except Exception:
            return

        for alarm in alarms:
            if not alarm.enabled:
                continue

            for target_day in targets:
                trigger_dts = self._get_alarm_trigger_on_date(alarm, target_day)
                for trigger_dt in trigger_dts:
                    if check_from < trigger_dt <= now:
                        offset_map = {
                            "at_start": timedelta(),
                            "5m": timedelta(minutes=5),
                            "10m": timedelta(minutes=10),
                            "30m": timedelta(minutes=30),
                            "1h": timedelta(hours=1),
                        }
                        offset_delta = offset_map.get(alarm.alert_offset, timedelta())
                        occurrence_time = (trigger_dt + offset_delta).strftime("%H:%M")
                        
                        self._show_alarm_toast(alarm, target_day, occurrence_time)
                        
                        if not alarm.start_date and not alarm.repeat_days:
                            next_trig = calculate_next_alarm_trigger(alarm, now)
                            if next_trig is None:
                                alarm.enabled = False
                                self.repository.upsert_alarm(alarm)

    def _get_alarm_trigger_on_date(self, alarm: Alarm, d: date) -> list[datetime]:
        def parse_time(time_str: str) -> datetime_time | None:
            try:
                h, m = map(int, time_str.split(":"))
                return datetime_time(h, m)
            except Exception:
                return None

        def get_occurrence_times() -> list[datetime_time]:
            st = parse_time(alarm.alarm_time)
            if not st:
                return []
            if not alarm.hourly_repeat:
                return [st]
            et = parse_time(alarm.hourly_end_time)
            if not et:
                return [st]
            
            occurrences = []
            curr_dt = datetime.combine(date.today(), st)
            end_dt = datetime.combine(date.today(), et)
            interval_hours = max(1, alarm.hourly_interval)
            while curr_dt <= end_dt:
                occurrences.append(curr_dt.time())
                curr_dt += timedelta(hours=interval_hours)
            return occurrences

        occurrence_times = get_occurrence_times()
        if not occurrence_times:
            return []

        offset_map = {
            "at_start": timedelta(),
            "5m": timedelta(minutes=5),
            "10m": timedelta(minutes=10),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
        }
        offset_delta = offset_map.get(alarm.alert_offset, timedelta())

        if not alarm.start_date and not alarm.repeat_days:
            created_at = alarm.created_at or datetime.now()
            today_date = created_at.date()
            tomorrow_date = today_date + timedelta(days=1)
            
            if d not in (today_date, tomorrow_date):
                return []
                
            triggers = []
            for t in occurrence_times:
                alarm_dt = datetime.combine(d, t)
                trigger_dt = alarm_dt - offset_delta
                if created_at < trigger_dt <= created_at + timedelta(days=1):
                    triggers.append(trigger_dt)
            return triggers

        if alarm.start_date and d < alarm.start_date:
            return []
        if alarm.end_date and d > alarm.end_date:
            return []

        if alarm.repeat_days:
            py_weekday = d.weekday()
            alarm_weekday = (py_weekday + 1) % 7
            if alarm_weekday not in alarm.repeat_days:
                return []

        triggers = []
        for t in occurrence_times:
            alarm_dt = datetime.combine(d, t)
            trigger_dt = alarm_dt - offset_delta
            triggers.append(trigger_dt)
        return triggers

    def _show_alarm_toast(self, alarm: Alarm, target_day: date, trigger_time: str) -> None:
        detail = target_day.strftime("%Y.%m.%d")
        if trigger_time:
            detail = f"{detail} {trigger_time}"
        box = AlertToast(
            self.palette,
            f"알람: {alarm.title}",
            detail,
        )
        box.closed.connect(self._on_alert_box_closed)
        self._active_alert_boxes.append(box)
        box.show()
        self._reposition_alert_boxes()
        box.raise_()

    def _open_alarm_settings(self) -> None:
        dialog = AlarmManagerDialog(self, self.repository)
        dialog.exec()

    def _on_alert_box_closed(self, box: AlertToast) -> None:
        if box in self._active_alert_boxes:
            self._active_alert_boxes.remove(box)
        self._reposition_alert_boxes()

    def _reposition_alert_boxes(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        gap = 8
        offset = 0
        for box in reversed(self._active_alert_boxes):
            if not box.isVisible():
                continue
            size = box.frameGeometry().size()
            width = max(size.width(), ALERT_BOX_WIDTH)
            height = max(size.height(), ALERT_BOX_HEIGHT)
            x = area.x() + area.width() - width
            y = area.y() + area.height() - height - offset
            box.setGeometry(x, y, width, height)
            offset += height + gap

    def _setup_global_hotkey(self) -> None:
        fallback = "Ctrl+Alt+S"
        stored_raw = self.repository.get_setting("toggle_shortcut", "").strip()
        stored_norm = normalize_shortcut(stored_raw) if stored_raw else ""
        migrated = self.repository.get_setting("toggle_shortcut_migrated_v1", "0") == "1"
        default_norm = normalize_shortcut(default_shortcut())
        fallback_norm = normalize_shortcut(fallback)

        # One-time migration: legacy default Ctrl+Alt+S -> new default F3.
        if stored_raw and stored_norm == fallback_norm and not migrated:
            candidates = [default_norm, fallback_norm]
        elif stored_raw:
            candidates = [stored_norm, fallback_norm]
        else:
            candidates = [default_norm, fallback_norm]

        try:
            self.hotkey_manager = QtGlobalHotkeyManager(fallback, self._toggle_window_visibility)
            applied: str | None = None
            for candidate in candidates:
                if self.hotkey_manager.update_shortcut(candidate):
                    applied = candidate
                    break
            if applied is None:
                # keep manager alive but with its initial binding attempt
                applied = fallback_norm

            if stored_raw != applied:
                self.repository.set_setting("toggle_shortcut", applied)
            if not migrated:
                self.repository.set_setting("toggle_shortcut_migrated_v1", "1")
            if stored_raw != applied or not migrated:
                self.repository.save()
        except Exception:  # pragma: no cover
            logger.exception("failed to initialize global hotkey")
            self.hotkey_manager = None

    def _toggle_window_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
            return
        self._restore_window_state()

    def _show_from_tray(self) -> None:
        self._restore_window_state()

    def _schedule_calendar_rerender(self) -> None:
        if self._calendar_rerender_pending:
            return
        self._calendar_rerender_pending = True
        QTimer.singleShot(0, self._rerender_calendar_after_resize)
        QTimer.singleShot(80, self._rerender_calendar_after_resize)

    def _rerender_calendar_after_resize(self) -> None:
        self._render_calendar()
        self._sync_sticker_overlay()
        if self._pending_sticker_rebase_size is not None:
            self._schedule_sticker_rebase()
        else:
            self._render_stickers()
        self._calendar_rerender_pending = False

    def _remember_window_state(self) -> None:
        if self._suspend_window_state_tracking or self.isMinimized():
            return
        self._last_window_was_maximized = self.isMaximized()
        self._window_state_dirty = True
        if self._last_window_was_maximized:
            normal_geometry = self.normalGeometry()
            if normal_geometry.isValid():
                self._last_normal_geometry = QRect(normal_geometry)
            return
        current_geometry = self.geometry()
        if current_geometry.isValid():
            self._last_normal_geometry = QRect(current_geometry)

    def _restore_window_state(self) -> None:
        self._suspend_window_state_tracking = True
        if self._last_window_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if self._last_normal_geometry.isValid():
                self.setGeometry(self._last_normal_geometry)
        self.activateWindow()
        self.raise_()
        QTimer.singleShot(0, self._finish_window_restore)

    def _finish_window_restore(self) -> None:
        self._suspend_window_state_tracking = False
        self._remember_window_state()

    def _load_window_state(self) -> None:
        raw = self.repository.get_setting("window_state_v1", "")
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        try:
            x = int(data.get("x", self._last_normal_geometry.x()))
            y = int(data.get("y", self._last_normal_geometry.y()))
            w = int(data.get("w", self._last_normal_geometry.width()))
            h = int(data.get("h", self._last_normal_geometry.height()))
            maximized = bool(data.get("maximized", False))
        except (TypeError, ValueError):
            return
        if w < 980 or h < 620:
            return
        self._last_normal_geometry = QRect(x, y, w, h)
        self._last_window_was_maximized = maximized
        self._window_state_dirty = False

    def _apply_initial_window_state(self) -> None:
        if self._last_normal_geometry.isValid():
            self.setGeometry(self._last_normal_geometry)
        if self._last_window_was_maximized:
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

    def _persist_window_state(self) -> None:
        if not self._window_state_dirty:
            return
        geo = self._last_normal_geometry if self._last_normal_geometry.isValid() else self.geometry()
        payload = {
            "x": int(geo.x()),
            "y": int(geo.y()),
            "w": int(geo.width()),
            "h": int(geo.height()),
            "maximized": bool(self._last_window_was_maximized),
        }
        self.repository.set_setting("window_state_v1", json.dumps(payload, ensure_ascii=False))
        self._window_state_dirty = False

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible() and not self.isMinimized():
                self.showMinimized()
            else:
                self._show_from_tray()

    def _sync_sticker_overlay(self) -> None:
        self.sticker_overlay.setGeometry(self.calendar_grid_widget.rect())
        self._position_sticker_toolbar()
        self.sticker_overlay.raise_()
        if self.sticker_toolbar.isVisible():
            self.sticker_toolbar.raise_()

    def _position_sticker_toolbar(self) -> None:
        if self.sticker_toolbar.parentWidget() is not self.root_widget:
            return
        desired_w = self.sticker_toolbar.sizeHint().width()
        height = self.sticker_toolbar.sizeHint().height()
        if self._sticker_toolbar_manually_moved:
            rect = self.root_widget.rect()
            x = max(0, min(self._sticker_toolbar_pos.x(), rect.width() - desired_w))
            y = max(0, min(self._sticker_toolbar_pos.y(), rect.height() - height))
            self.sticker_toolbar.setGeometry(x, y, desired_w, height)
            return
        topbar_geo = self.topbar.geometry()
        anchor = self.prev_button.mapTo(self.root_widget, QPoint(0, 0))
        margin = 10
        x = anchor.x()
        y = topbar_geo.y() + max(0, (topbar_geo.height() - height) // 2)
        avail_w = max(120, topbar_geo.right() - x - margin)
        width = max(120, min(avail_w, desired_w))
        self.sticker_toolbar.setGeometry(x, y, width, height)

    def _sticker_metrics(self) -> tuple[int, int, int, int]:
        overlay_w = max(1, self.sticker_overlay.width())
        overlay_h = max(1, self.sticker_overlay.height())
        if self.day_cells:
            tops: list[int] = []
            bottoms: list[int] = []
            for cell in self.day_cells:
                geo = cell.geometry()
                if not geo.isValid():
                    continue
                tops.append(geo.top())
                bottoms.append(geo.bottom())
            if tops and bottoms:
                body_top = max(1, min(tops))
                body_bottom = min(overlay_h - 1, max(bottoms))
                body_h = max(1, body_bottom - body_top + 1)
                return overlay_w, overlay_h, body_top, body_h
        header_h = 28
        if self.weekday_labels:
            header_h = max(1, self.weekday_labels[0].height())
        header_h = max(1, min(header_h, overlay_h - 1))
        body_h = max(1, overlay_h - header_h)
        return overlay_w, overlay_h, header_h, body_h

    def _cell_size_near_point(self, x: int, y: int) -> tuple[int, int]:
        """Return day-cell width/height near a given overlay point."""
        geo = self._cell_geometry_near_point(x, y)
        if geo is not None:
            return max(1, geo.width()), max(1, geo.height())
        overlay_w, _, _, body_h = self._sticker_metrics()
        return max(1, overlay_w // 7), max(1, body_h // 6)

    def _cell_geometry_near_point(self, x: int, y: int) -> QRect | None:
        if not self.day_cells:
            return None
        candidates = [cell.geometry() for cell in self.day_cells if cell.geometry().isValid()]
        if not candidates:
            return None
        px = int(x)
        py = int(y)
        for geo in candidates:
            if geo.contains(px, py):
                return geo
        return min(
            candidates,
            key=lambda g: abs(g.center().x() - px) + abs(g.center().y() - py),
        )

    def _capture_sticker_rebase_size(self) -> None:
        self._pending_sticker_rebase_size = self._sticker_metrics()

    def _apply_pending_sticker_rebase(self) -> None:
        if self._pending_sticker_rebase_size is None:
            return
        self._pending_sticker_rebase_size = None

    def _schedule_sticker_rebase(self) -> None:
        if self._sticker_rebase_scheduled:
            return
        self._sticker_rebase_scheduled = True
        self._sticker_rebase_last_size = None
        QTimer.singleShot(0, self._finalize_sticker_rebase)

    def _finalize_sticker_rebase(self) -> None:
        self._enforce_equal_calendar_cells()
        self._sync_sticker_overlay()
        current_size = (max(1, self.sticker_overlay.width()), max(1, self.sticker_overlay.height()))
        if self._sticker_rebase_last_size != current_size:
            self._sticker_rebase_last_size = current_size
            QTimer.singleShot(0, self._finalize_sticker_rebase)
            return
        self._sticker_rebase_scheduled = False
        self._sticker_rebase_last_size = None
        self._apply_pending_sticker_rebase()
        if self._save_stickers_after_rebase:
            self._save_stickers_after_rebase = False
            self._save_sticker_store()
        self._render_stickers()

    def _enforce_equal_calendar_cells(self) -> None:
        rect = self.calendar_grid_widget.contentsRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        header_h = 28
        cell_w = max(1, rect.width() // 7)
        cell_h = max(1, (rect.height() - header_h) // 6)
        for col in range(7):
            self.calendar_grid.setColumnMinimumWidth(col, cell_w)
        self.calendar_grid.setRowMinimumHeight(0, header_h)
        for row in range(6):
            self.calendar_grid.setRowMinimumHeight(row + 1, cell_h)

    def _calendar_item_capacity(self) -> int:
        if not self.day_cells:
            return 2
        _, _, _body_top, body_h = self._sticker_metrics()
        cell_h = max(1, body_h // 6)
        # Top area for day number/badge + bottom padding
        usable = max(0, cell_h - 34)
        slots = usable // 20
        return max(1, min(8, int(slots)))

    def _month_key(self) -> str:
        return f"{self.current_year:04d}-{self.current_month:02d}"

    def _load_sticker_assets(self) -> dict[str, QPixmap]:
        assets: dict[str, QPixmap] = {}
        root = asset_path("stickers")
        for path in sorted(root.glob("*.png")):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                assets[f"builtin:{path.stem}"] = pixmap
        user_root = self._user_sticker_dir()
        user_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(user_root.glob("*.png")):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                assets[f"user:{path.stem}"] = pixmap
        return assets

    @staticmethod
    def _sticker_display_name(asset_name: str) -> str:
        name = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if name == "blue_box":
            return "파란 박스"
        if name == "mint_box":
            return "민트 박스"
        if name == "gold_box":
            return "골드 박스"
        if name == "orange_line":
            return "주황선"
        if name == "chevron_left_red":
            return "<"
        if name == "chevron_right_red":
            return ">"
        return name

    @staticmethod
    def _compact_name(text: str, max_len: int = 12) -> str:
        value = str(text or "").strip()
        if len(value) <= max_len:
            return value
        return value[: max(1, max_len - 3)] + "..."

    @staticmethod
    def _base_builtin_sticker_names() -> list[str]:
        return [
            "bunny",
            "cat",
            "circle_red",
            "clover",
            "heart_pink",
            "coffee_time",
            "rice_bowl",
            "x_red",
            "leave_day",
            "medicine_pill",
            "chevron_left_red",
            "chevron_right_red",
            "blue_box",
            "orange_line",
        ]

    def _default_sticker_keys(self) -> list[str]:
        return [f"builtin:{name}" for name in self._base_builtin_sticker_names()]

    def _user_sticker_dir(self) -> Path:
        return data_path("stickers_user")

    def _load_sticker_pref_list(self, key: str) -> list[str]:
        raw = self.repository.get_setting(key, "[]")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            cleaned.append(value)
        return cleaned

    def _save_sticker_preferences(self) -> None:
        self.repository.set_setting("sticker_favorites_v1", json.dumps(self._sticker_favorites, ensure_ascii=False))
        self.repository.set_setting("sticker_recent_v1", json.dumps(self._sticker_recent, ensure_ascii=False))
        self.repository.save()

    def _sanitize_sticker_preferences(self) -> None:
        valid = set(self._sticker_assets.keys())
        seen_fav: set[str] = set()
        self._sticker_favorites = [name for name in self._sticker_favorites if name in valid and not (name in seen_fav or seen_fav.add(name))]
        seen_recent: set[str] = set()
        self._sticker_recent = [name for name in self._sticker_recent if name in valid and not (name in seen_recent or seen_recent.add(name))]

    def _resolve_sticker_asset_name(self, asset_name: str) -> str | None:
        name = str(asset_name or "").strip()
        if not name:
            return None
        if name in self._sticker_assets:
            return name
        if ":" not in name:
            builtin = f"builtin:{name}"
            if builtin in self._sticker_assets:
                return builtin
            user = f"user:{name}"
            if user in self._sticker_assets:
                return user
        return None

    def _quick_sticker_keys(self, limit: int = 8) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        ordered = self._sticker_favorites + self._sticker_recent + self._default_sticker_keys()
        for name in ordered:
            if name not in self._sticker_assets or name in seen:
                continue
            seen.add(name)
            result.append(name)
            if len(result) >= limit:
                return result
        for name in sorted(self._sticker_assets.keys()):
            if name in seen:
                continue
            result.append(name)
            if len(result) >= limit:
                break
        return result

    def _clear_layout_widgets(self, layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_sticker_palette_buttons(self) -> None:
        if not hasattr(self, "sticker_palette_layout"):
            return
        self._clear_layout_widgets(self.sticker_palette_layout)
        self.sticker_palette_buttons = []
        for name in self._quick_sticker_keys():
            pix = self._sticker_assets.get(name)
            if pix is None:
                continue
            preview = self._quick_palette_preview_pixmap(name, pix)
            button = QToolButton()
            button.setObjectName("stickerChip")
            button.setIcon(QIcon(preview))
            button.setIconSize(preview.size())
            button.setFixedSize(26, 26)
            button.setToolTip(self._sticker_display_name(name))
            button.clicked.connect(lambda checked=False, n=name: self._add_sticker(n))
            self.sticker_palette_buttons.append(button)
            self.sticker_palette_layout.addWidget(button)
        self.sticker_palette_layout.addStretch(1)

    def _quick_palette_preview_pixmap(self, asset_name: str, source: QPixmap) -> QPixmap:
        size = 18
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name

        # Dedicated simplified set for small quick palette chips.
        if key in {"blue_box", "mint_box", "gold_box", "orange_line", "chevron_left_red", "chevron_right_red"}:
            pix = QPixmap(size, size)
            pix.fill(Qt.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.Antialiasing, False)
            if key == "orange_line":
                color = QColor(225, 66, 66, 255)
                painter.fillRect(2, size - 4, size - 4, 2, color)
            elif key == "chevron_left_red":
                color = QColor(225, 66, 66, 255)
                for i in range(6):
                    painter.fillRect(10 - i, 4 + i, 3, 3, color)
                    painter.fillRect(5 + i, 9 + i, 3, 3, color)
            elif key == "chevron_right_red":
                color = QColor(225, 66, 66, 255)
                for i in range(6):
                    painter.fillRect(5 + i, 4 + i, 3, 3, color)
                    painter.fillRect(10 - i, 9 + i, 3, 3, color)
            else:
                if key == "blue_box":
                    color = QColor(0, 146, 230, 255)
                elif key == "gold_box":
                    color = QColor(232, 151, 44, 255)
                else:
                    color = QColor(34, 165, 123, 255)
                painter.fillRect(2, 2, size - 4, 1, color)
                painter.fillRect(2, size - 3, size - 4, 1, color)
                painter.fillRect(2, 2, 1, size - 4, color)
                painter.fillRect(size - 3, 2, 1, size - 4, color)
            painter.end()
            return pix

        return source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _toggle_sticker_favorite(self, name: str, checked: bool) -> bool:
        if checked:
            if name not in self._sticker_favorites and len(self._sticker_favorites) >= 8:
                return False
            self._sticker_favorites = [name] + [item for item in self._sticker_favorites if item != name]
        else:
            self._sticker_favorites = [item for item in self._sticker_favorites if item != name]
        self._sanitize_sticker_preferences()
        self._save_sticker_preferences()
        self._rebuild_sticker_palette_buttons()
        return True

    def _add_stickers_from_png_files(self) -> int:
        filenames, _ = QFileDialog.getOpenFileNames(self, "PNG 가져오기", "", "PNG 이미지 (*.png)")
        if not filenames:
            return 0
        user_root = self._user_sticker_dir()
        user_root.mkdir(parents=True, exist_ok=True)
        imported = 0
        for source in filenames:
            src = Path(source)
            if src.suffix.lower() != ".png":
                continue
            target = user_root / src.name
            index = 2
            while target.exists():
                target = user_root / f"{src.stem}_{index}.png"
                index += 1
            try:
                shutil.copy2(src, target)
            except OSError:
                continue
            imported += 1
        if imported:
            self._sticker_assets = self._load_sticker_assets()
            self._sanitize_sticker_preferences()
            self._rebuild_sticker_palette_buttons()
            self._render_stickers()
            self._save_sticker_preferences()
        return imported

    @staticmethod
    def _is_user_sticker_asset(asset_name: str) -> bool:
        return str(asset_name or "").startswith("user:")

    def _remove_user_sticker_asset(self, asset_name: str) -> bool:
        if not self._is_user_sticker_asset(asset_name):
            return False
        try:
            sticker_name = asset_name.split(":", 1)[1]
            target = self._user_sticker_dir() / f"{sticker_name}.png"
            if target.exists():
                target.unlink()

            for month_key, month_items in list(self._sticker_store.items()):
                if not isinstance(month_items, list):
                    self._sticker_store[month_key] = []
                    continue
                cleaned: list[dict[str, object]] = []
                for item in month_items:
                    if not isinstance(item, dict):
                        continue
                    raw_asset = str(item.get("asset", "")).strip()
                    resolved = self._resolve_sticker_asset_name(raw_asset)
                    if resolved == asset_name:
                        continue
                    cleaned.append(item)
                self._sticker_store[month_key] = cleaned

            if self._selected_sticker() is None:
                self._selected_sticker_id = None

            self._sticker_assets.pop(asset_name, None)
            self._sticker_favorites = [name for name in self._sticker_favorites if name != asset_name]
            self._sticker_recent = [name for name in self._sticker_recent if name != asset_name]
            self._sanitize_sticker_preferences()
            self._save_sticker_preferences()
            self._save_sticker_store()
            self._rebuild_sticker_palette_buttons()
            self._render_stickers()
            return True
        except Exception:
            logger.exception("remove user sticker failed: %s", asset_name)
            return False

    def _sticker_library_order(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in self._sticker_favorites + self._sticker_recent + self._default_sticker_keys() + sorted(self._sticker_assets.keys()):
            if name in seen or name not in self._sticker_assets:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    def _open_sticker_library(self) -> None:
        if not self._sticker_assets:
            QMessageBox.information(self, "안내", "스티커 이미지가 없습니다.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("스티커 설정")
        dialog.setModal(True)
        dialog.resize(600, 420)
        dialog.setStyleSheet(
            """
            QToolButton#favoriteToggle {
                background: #edf1f5;
                color: #5f6b78;
                border: 1px solid #c8d2de;
                border-radius: 7px;
                padding: 2px 8px;
                font-weight: 700;
            }
            QToolButton#favoriteToggle:hover {
                background: #e4eaf1;
            }
            QToolButton#favoriteToggle:checked {
                background: #ffd0e2;
                border-color: #d98fb3;
                color: #6f2f4f;
            }
            QPushButton#stickerDeleteButton {
                background: #fff1f1;
                color: #9a2f2f;
                border: 1px solid #e8b2b2;
                border-radius: 6px;
                padding: 2px 8px;
                font-weight: 700;
            }
            QPushButton#stickerDeleteButton:hover {
                background: #ffe3e3;
            }
            """
        )

        root = QVBoxLayout(dialog)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel("즐겨찾기/최근은 상단 퀵 팔레트에 우선 표시됩니다."))
        top.addStretch(1)
        import_btn = QPushButton("PNG 가져오기")
        top.addWidget(import_btn)
        root.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        columns = 4

        def _rebuild_library_cards() -> None:
            while grid.count():
                item = grid.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

            for idx, name in enumerate(self._sticker_library_order()):
                pix = self._sticker_assets[name]
                card = QFrame()
                card.setObjectName("softPanel")
                card.setFixedWidth(116)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(8, 8, 8, 8)
                card_layout.setSpacing(4)

                btn = QToolButton()
                btn.setIcon(QIcon(pix))
                btn.setIconSize(pix.size().boundedTo(QSize(56, 56)))
                btn.setToolTip(self._sticker_display_name(name))
                btn.clicked.connect(lambda checked=False, n=name: (self._add_sticker(n), dialog.accept()))
                card_layout.addWidget(btn, 0, Qt.AlignHCenter)

                label = QLabel(self._sticker_display_name(name))
                label.setToolTip(self._sticker_display_name(name))
                label.setAlignment(Qt.AlignCenter)
                label.setFixedWidth(92)
                label.setWordWrap(False)
                label.setText(self._compact_name(self._sticker_display_name(name), 11))
                card_layout.addWidget(label)

                fav = QToolButton()
                fav.setObjectName("favoriteToggle")
                fav.setCheckable(True)
                fav.setChecked(name in self._sticker_favorites)
                fav.setText("선택")
                fav.setFixedHeight(24)
                fav.setFixedWidth(44)

                def _on_fav_toggled(checked: bool, n: str = name, b: QToolButton = fav) -> None:
                    if checked:
                        ok = self._toggle_sticker_favorite(n, True)
                        if not ok:
                            b.blockSignals(True)
                            b.setChecked(False)
                            b.blockSignals(False)
                            b.setText("선택")
                            QMessageBox.information(self, "안내", "선택은 최대 8개까지 가능합니다.")
                            return
                        b.setText("선택")
                        return
                    self._toggle_sticker_favorite(n, False)
                    b.setText("선택")

                fav.toggled.connect(_on_fav_toggled)

                action_row = QHBoxLayout()
                action_row.setContentsMargins(0, 0, 0, 0)
                action_row.setSpacing(4)
                action_row.addStretch(1)
                action_row.addWidget(fav)

                if self._is_user_sticker_asset(name):
                    delete_btn = QPushButton("삭제")
                    delete_btn.setObjectName("stickerDeleteButton")
                    delete_btn.setFixedHeight(24)
                    delete_btn.setFixedWidth(44)

                    def _delete_user_sticker(checked: bool = False, n: str = name) -> None:
                        display = self._sticker_display_name(n)
                        reply = QMessageBox.question(
                            dialog,
                            "삭제 확인",
                            f"'{display}' 스티커를 삭제할까요?\n달력에 배치한 항목도 함께 제거됩니다.",
                            QMessageBox.Yes | QMessageBox.No,
                            QMessageBox.No,
                        )
                        if reply != QMessageBox.Yes:
                            return
                        if not self._remove_user_sticker_asset(n):
                            QMessageBox.warning(self, "실패", "스티커 삭제에 실패했습니다.")
                            return
                        _rebuild_library_cards()
                        QMessageBox.information(self, "완료", "스티커를 삭제했습니다.")

                    delete_btn.clicked.connect(_delete_user_sticker)
                    action_row.addWidget(delete_btn)

                action_row.addStretch(1)
                card_layout.addLayout(action_row)

                row = idx // columns
                col = idx % columns
                grid.addWidget(card, row, col)

        def _import_and_refresh() -> None:
            imported = self._add_stickers_from_png_files()
            if imported <= 0:
                return
            _rebuild_library_cards()
            QMessageBox.information(self, "완료", f"{imported}개 PNG를 추가했습니다.")

        import_btn.clicked.connect(_import_and_refresh)
        _rebuild_library_cards()

        scroll.setWidget(panel)
        root.addWidget(scroll, 1)

        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(dialog.reject)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._apply_clickable_cursor(dialog)
        dialog.exec()

    def _load_sticker_animation_assets(self) -> dict[str, dict[str, object]]:
        animations: dict[str, dict[str, object]] = {}
        sheet_path = asset_path("stickers", "sprites", "bunny_flap", "bunny_flap_sheet.png")
        sheet = QPixmap(str(sheet_path))
        if not sheet.isNull():
            frame_count = 8
            frame_w = sheet.width() // frame_count if frame_count else 0
            frame_h = sheet.height()
            if frame_w > 0 and frame_h > 0:
                animations["bunny"] = {
                    "sheet": sheet,
                    "frame_count": frame_count,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "frame_ms": 120,
                    "pause_ms": 5000,
                    "pause_frame": 0,
                    "restart_frame": 0,
                }
        cat_sheet_path = asset_path("stickers", "sprites", "cat_blink", "cat_blink_sheet.png")
        cat_sheet = QPixmap(str(cat_sheet_path))
        if not cat_sheet.isNull():
            frame_count = 8
            frame_w = cat_sheet.width() // frame_count if frame_count else 0
            frame_h = cat_sheet.height()
            if frame_w > 0 and frame_h > 0:
                animations["cat"] = {
                    "sheet": cat_sheet,
                    "frame_count": frame_count,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "frame_ms": 110,
                    "pause_ms": 5000,
                    "pause_frame": 0,
                    "restart_frame": 0,
                }
        clover_sheet_path = asset_path("stickers", "sprites", "clover_join", "clover_join_sheet.png")
        clover_sheet = QPixmap(str(clover_sheet_path))
        if not clover_sheet.isNull():
            frame_count = 12
            frame_w = clover_sheet.width() // frame_count if frame_count else 0
            frame_h = clover_sheet.height()
            if frame_w > 0 and frame_h > 0:
                animations["clover"] = {
                    "sheet": clover_sheet,
                    "frame_count": frame_count,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "frame_ms": 110,
                    "pause_ms": 8000,
                    "pause_frame": frame_count - 1,
                    "restart_frame": 0,
                }
        heart_sheet_path = asset_path("stickers", "sprites", "heart_beat", "heart_beat_sheet.png")
        heart_sheet = QPixmap(str(heart_sheet_path))
        if not heart_sheet.isNull():
            frame_count = 10
            frame_w = heart_sheet.width() // frame_count if frame_count else 0
            frame_h = heart_sheet.height()
            if frame_w > 0 and frame_h > 0:
                animations["heart_pink"] = {
                    "sheet": heart_sheet,
                    "frame_count": frame_count,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "frame_ms": 95,
                    "pause_ms": 5000,
                    "pause_frame": frame_count - 1,
                    "restart_frame": 0,
                }
        coffee_sheet_path = asset_path("stickers", "sprites", "coffee_steam", "coffee_steam_sheet.png")
        coffee_sheet = QPixmap(str(coffee_sheet_path))
        if not coffee_sheet.isNull():
            frame_count = 10
            frame_w = coffee_sheet.width() // frame_count if frame_count else 0
            frame_h = coffee_sheet.height()
            if frame_w > 0 and frame_h > 0:
                animations["coffee_time"] = {
                    "sheet": coffee_sheet,
                    "frame_count": frame_count,
                    "frame_w": frame_w,
                    "frame_h": frame_h,
                    "frame_ms": 120,
                    "pause_ms": 2600,
                    "pause_frame": frame_count - 1,
                    "restart_frame": 0,
                }
        return animations

    def _setup_sticker_animation_timer(self) -> None:
        if not self._sticker_animation_assets:
            return
        timer = QTimer(self)
        timer.setInterval(100)
        timer.timeout.connect(self._tick_sticker_animations)
        self._sticker_anim_timer = timer
        timer.start()

    def _tick_sticker_animations(self) -> None:
        if not self._sticker_animation_enabled:
            return
        if not self.isVisible():
            return
        if not self._current_stickers():
            return
        self._render_stickers()

    def _animated_sticker_base(self, sticker_id: str, asset_name: str) -> QPixmap | None:
        if not self._sticker_animation_enabled:
            return None
        animation_key = asset_name.split(":", 1)[1] if asset_name.startswith("builtin:") else asset_name
        config = self._sticker_animation_assets.get(animation_key)
        if config is None:
            return None
        sheet = config["sheet"]
        frame_count = int(config["frame_count"])
        frame_w = int(config["frame_w"])
        frame_h = int(config["frame_h"])
        frame_ms = float(config["frame_ms"]) / 1000.0
        pause_ms = float(config["pause_ms"]) / 1000.0
        pause_frame = int(config.get("pause_frame", 0))
        restart_frame = int(config.get("restart_frame", 0))
        pause_frame = max(0, min(frame_count - 1, pause_frame))
        restart_frame = max(0, min(frame_count - 1, restart_frame))
        now = time.monotonic()
        state = self._sticker_animation_state.get(sticker_id)
        if state is None:
            state = {"frame": restart_frame, "next_at": now + frame_ms, "paused_until": 0.0}
            self._sticker_animation_state[sticker_id] = state
        else:
            frame = int(state.get("frame", 0))
            next_at = float(state.get("next_at", now + frame_ms))
            paused_until = float(state.get("paused_until", 0.0))
            while now >= next_at:
                if paused_until > now:
                    next_at = paused_until
                    break
                if paused_until > 0.0 and now >= paused_until:
                    paused_until = 0.0
                    frame = restart_frame
                    next_at = now + frame_ms
                    break
                frame += 1
                if frame >= frame_count:
                    frame = pause_frame
                    paused_until = now + pause_ms
                    next_at = paused_until
                    break
                next_at = now + frame_ms
            state["frame"] = frame
            state["next_at"] = next_at
            state["paused_until"] = paused_until
        frame_index = max(0, min(frame_count - 1, int(state["frame"])))
        x = frame_index * frame_w
        return sheet.copy(x, 0, frame_w, frame_h)

    @staticmethod
    def _is_band_sticker(asset_name: str) -> bool:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        return key in {"blue_box", "mint_box", "gold_box", "orange_line"}

    @staticmethod
    def _is_box_sticker(asset_name: str) -> bool:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        return key in {"blue_box", "mint_box", "gold_box"}

    @staticmethod
    def _is_outline_only_box(asset_name: str) -> bool:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        return key in {"blue_box", "mint_box", "gold_box"}

    @staticmethod
    def _is_bottom_line_band(asset_name: str) -> bool:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        return key == "orange_line"

    @staticmethod
    def _outline_box_edge_color(asset_name: str) -> QColor:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "blue_box":
            return QColor(0, 146, 230, 255)
        if key == "gold_box":
            return QColor(232, 151, 44, 255)
        return QColor(34, 165, 123, 255)

    @staticmethod
    def _is_left_anchor_sticker(asset_name: str) -> bool:
        return MainWindow._is_band_sticker(asset_name)

    @staticmethod
    def _band_height_ratio(asset_name: str) -> float:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "orange_line":
            return 0.34
        return 1.0

    def _band_target_height_px(self, asset_name: str, cell_h: int) -> int:
        if self._is_outline_only_box(asset_name):
            # Keep mint outline box height exactly aligned with calendar cell height.
            return cell_h
        if self._is_bottom_line_band(asset_name):
            # Keep orange line band height same as box stickers.
            return cell_h
        ratio = self._band_height_ratio(asset_name)
        baseline = self._band_baseline_cell_h or cell_h
        # Startup window cell height is the baseline.
        if cell_h <= baseline:
            return int(cell_h * ratio)
        base = baseline * ratio
        extra = (cell_h - baseline) * ratio * 0.82
        return int(base + extra)

    def _band_target_day_width_px(self, asset_name: str, cell_w: int) -> int:
        if self._is_outline_only_box(asset_name):
            # Keep mint outline box aligned to real calendar cell width while resizing.
            return cell_w
        if self._is_bottom_line_band(asset_name):
            # Keep orange line width behavior same as box stickers.
            return cell_w
        # Box stickers should grow with window size, but a bit less aggressively.
        if not self._is_box_sticker(asset_name):
            return cell_w
        baseline = self._band_baseline_cell_w or cell_w
        if cell_w <= baseline:
            return cell_w
        return int(baseline + (cell_w - baseline) * 0.68)

    @staticmethod
    def _band_cap_width_px(asset_name: str, cell_h: int) -> int:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "mint_box":
            return max(2, min(8, int(cell_h * 0.06)))
        if key == "orange_line":
            return max(8, min(16, int(cell_h * 0.22)))
        return max(12, min(24, int(cell_h * 0.34)))

    @staticmethod
    def _band_width_days(asset_name: str, scale: int) -> float:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "orange_line":
            # Match box sticker scaling range.
            max_days = 7.0
        else:
            max_days = 7.0
        t = (max(60, min(360, int(scale))) - 100.0) / 260.0
        t = max(-0.2, min(1.0, t))
        days = 1.0 + (max_days - 1.0) * t
        return max(0.8, min(max_days, days))

    def _band_vertical_lift_px(self, cell_h: int) -> int:
        baseline = self._band_baseline_cell_h or cell_h
        threshold = baseline + 10
        # Lift only after growing beyond baseline.
        if cell_h <= threshold:
            return 0
        return max(0, min(24, int((cell_h - threshold) * 0.26)))

    @staticmethod
    def _box_band_colors(asset_name: str) -> tuple[QColor, QColor, QColor]:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "blue_box":
            return QColor(38, 150, 255, 34), QColor(0, 146, 230, 220), QColor(170, 225, 255, 180)
        if key == "mint_box":
            return QColor(52, 199, 152, 34), QColor(34, 165, 123, 220), QColor(189, 245, 226, 180)
        # gold_box
        return QColor(255, 191, 66, 34), QColor(232, 151, 44, 220), QColor(255, 234, 174, 180)

    def _render_box_band_pixmap(self, asset_name: str, target_w: int, target_h: int) -> QPixmap:
        pix = QPixmap(max(1, target_w), max(1, target_h))
        pix.fill(Qt.transparent)
        fill_color, border_color, hi_color = self._box_band_colors(asset_name)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        radius = max(8.0, min(16.0, target_h * 0.22))
        if self._is_outline_only_box(asset_name):
            # Mint box: transparent interior + crisp 1px rectangular outline.
            # Structure is effectively left / center / right:
            # - left, right: vertical 1px edges
            # - center: only top/bottom 1px lines stretch horizontally
            border = QColor(34, 165, 123, 255)
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setBrush(Qt.NoBrush)
            w = pix.width()
            h = pix.height()
            painter.fillRect(0, 0, w, 1, border)          # top
            painter.fillRect(0, h - 1, w, 1, border)      # bottom
            painter.fillRect(0, 0, 1, h, border)          # left
            painter.fillRect(w - 1, 0, 1, h, border)      # right
            painter.end()
            return pix

        border_w = 2.0
        inset = border_w / 2.0
        rect = pix.rect().adjusted(int(inset), int(inset), -int(inset) - 1, -int(inset) - 1)
        painter.setPen(QColor(border_color))
        painter.setBrush(QColor(fill_color))
        painter.drawRoundedRect(rect, radius, radius)

        inner_margin = max(2.0, min(6.0, target_h * 0.10))
        inner_rect = rect.adjusted(int(inner_margin), int(inner_margin), -int(inner_margin), -int(inner_margin))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(hi_color)
        painter.drawRoundedRect(inner_rect, max(4.0, radius - inner_margin), max(4.0, radius - inner_margin))
        painter.end()
        return pix

    @staticmethod
    def _band_src_cap_width_px(asset_name: str, src_w: int, src_h: int) -> int:
        key = asset_name.split(":", 1)[1] if ":" in asset_name else asset_name
        if key == "mint_box":
            # Keep a very thin edge region so center stretch preserves crisp 1px lines.
            return max(2, min(src_w // 2 - 1, 6))
        if key in {"blue_box", "mint_box", "gold_box"}:
            # 640x150 base images: straight edge starts around this split.
            return max(16, min(src_w // 2 - 1, 36))
        if key == "orange_line":
            # 640x90 base image with small rounded ends.
            return max(10, min(src_w // 2 - 1, 26))
        return max(12, min(src_w // 2 - 1, int(src_h * 0.30)))

    def _stretch_band_pixmap(self, base: QPixmap, target_w: int, target_h: int, cap_dst_w: int, asset_name: str) -> QPixmap:
        if target_w <= 0 or target_h <= 0:
            return QPixmap()
        if self._is_bottom_line_band(asset_name):
            result = QPixmap(target_w, target_h)
            result.fill(Qt.transparent)
            painter = QPainter(result)
            edge = QColor(225, 66, 66, 255)
            line_h = min(2, max(1, target_h))
            painter.fillRect(0, target_h - line_h, target_w, line_h, edge)
            painter.end()
            return result
        if base.isNull():
            return QPixmap()
        if self._is_outline_only_box(asset_name):
            result = QPixmap(target_w, target_h)
            result.fill(Qt.transparent)
            painter = QPainter(result)
            edge = self._outline_box_edge_color(asset_name)
            painter.fillRect(0, 0, target_w, 1, edge)
            painter.fillRect(0, target_h - 1, target_w, 1, edge)
            painter.fillRect(0, 0, 1, target_h, edge)
            painter.fillRect(target_w - 1, 0, 1, target_h, edge)
            painter.end()
            return result
        src_w = base.width()
        src_h = base.height()
        if src_w < 6 or src_h < 6:
            return base.scaled(target_w, target_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        src_cap = self._band_src_cap_width_px(asset_name, src_w, src_h)
        src_cap = max(2, min(src_w // 2 - 1, src_cap))
        mid_w = src_w - (src_cap * 2)
        if mid_w <= 0:
            return base.scaled(target_w, target_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        # Keep end-caps visually stable by matching horizontal scale to vertical scale.
        uniform_cap = int(round(src_cap * (target_h / max(1, src_h))))
        cap_dst = max(2, uniform_cap)
        cap_dst = min(cap_dst, cap_dst_w)
        cap_dst = min(cap_dst, max(1, (target_w // 2) - 1))
        # Keep enough center width so right cap does not get visually squeezed.
        min_mid = 8
        if target_w - (cap_dst * 2) < min_mid:
            cap_dst = max(1, (target_w - min_mid) // 2)
        mid_dst_w = target_w - (cap_dst * 2)
        if mid_dst_w <= 0:
            return base.scaled(target_w, target_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        result = QPixmap(target_w, target_h)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        left_src = QRect(0, 0, src_cap, src_h)
        mid_src = QRect(src_cap, 0, mid_w, src_h)
        right_src = QRect(src_w - src_cap, 0, src_cap, src_h)

        left_dst = QRect(0, 0, cap_dst, target_h)
        mid_dst = QRect(cap_dst, 0, mid_dst_w, target_h)
        right_dst = QRect(target_w - cap_dst, 0, cap_dst, target_h)

        painter.drawPixmap(left_dst, base, left_src)
        painter.drawPixmap(mid_dst, base, mid_src)
        painter.drawPixmap(right_dst, base, right_src)
        painter.end()
        return result

    def _load_action_icons(self) -> dict[str, QIcon]:
        mapping = {
            "complete": asset_path("action_complete.png"),
            "cancel": asset_path("action_cancel.png"),
            "edit": asset_path("action_edit.png"),
            "save": asset_path("action_save.png"),
            "delete": asset_path("action_delete.png"),
        }
        icons: dict[str, QIcon] = {}
        for key, path in mapping.items():
            if not path.exists():
                continue
            icon = QIcon(str(path))
            if not icon.isNull():
                icons[key] = icon
        return icons

    def _load_holidays(self) -> tuple[dict[str, str], dict[str, str]]:
        path = data_path("holidays_kr.json")
        should_copy = not path.exists()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    yearly = raw.get("yearly", {})
                    # "2030-02-03" (Lunar New Year 2030) is the indicator for 2030 holiday completeness
                    if "2030-02-03" not in yearly:
                        should_copy = True
            except Exception:
                should_copy = True

        if should_copy:
            path.parent.mkdir(parents=True, exist_ok=True)
            copied = False
            try:
                if getattr(sys, "frozen", False):
                    meipass = getattr(sys, "_MEIPASS", "")
                    if meipass:
                        packaged_path = Path(meipass) / "data" / "holidays_kr.json"
                        if packaged_path.exists():
                            shutil.copy2(packaged_path, path)
                            copied = True
            except Exception:
                pass
            
            if not copied and not path.exists():
                sample = {
                    "fixed": {
                        "01-01": "신정",
                        "03-01": "삼일절",
                        "05-05": "어린이날",
                        "06-06": "현충일",
                        "08-15": "광복절",
                        "10-03": "개천절",
                        "10-09": "한글날",
                        "12-25": "성탄절",
                    },
                    "yearly": {
                        "2026-03-02": "삼일절 대체공휴일",
                    },
                }
                path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, {}
        fixed: dict[str, str] = {}
        yearly: dict[str, str] = {}
        if isinstance(raw, dict):
            if isinstance(raw.get("fixed"), dict) or isinstance(raw.get("yearly"), dict):
                fixed_raw = raw.get("fixed", {})
                yearly_raw = raw.get("yearly", {})
                if isinstance(fixed_raw, dict):
                    for key, value in fixed_raw.items():
                        if isinstance(key, str) and isinstance(value, str) and len(key.strip()) == 5:
                            fixed[key.strip()] = value.strip()
                if isinstance(yearly_raw, dict):
                    for key, value in yearly_raw.items():
                        if isinstance(key, str) and isinstance(value, str) and len(key.strip()) == 10:
                            yearly[key.strip()] = value.strip()
            else:
                # Backward compatibility: flat map with either YYYY-MM-DD or MM-DD keys.
                for key, value in raw.items():
                    if not (isinstance(key, str) and isinstance(value, str)):
                        continue
                    token = key.strip()
                    if len(token) == 10:
                        yearly[token] = value.strip()
                    elif len(token) == 5:
                        fixed[token] = value.strip()
        elif isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                day = str(item.get("date", "")).strip()
                name = str(item.get("name", "")).strip()
                if day and name:
                    if len(day) == 10:
                        yearly[day] = name
                    elif len(day) == 5:
                        fixed[day] = name
        return fixed, yearly

    def _holiday_name_for_day(self, target_day: date) -> str:
        iso = target_day.isoformat()
        if iso in self._holidays_yearly:
            return self._holidays_yearly[iso]
        mmdd = target_day.strftime("%m-%d")
        return self._holidays_fixed.get(mmdd, "")

    def _load_sticker_store(self) -> dict[str, list[dict[str, object]]]:
        raw = self.repository.get_setting("sticker_layout_v1", "{}")
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                clean: dict[str, list[dict[str, object]]] = {}
                for key, value in parsed.items():
                    if isinstance(key, str) and isinstance(value, list):
                        clean[key] = [item for item in value if isinstance(item, dict)]
                return clean
        except json.JSONDecodeError:
            pass
        return {}

    def _save_sticker_store(self) -> None:
        self.repository.set_setting("sticker_layout_v1", json.dumps(self._sticker_store, ensure_ascii=False))
        self.repository.save()

    def _current_stickers(self) -> list[dict[str, object]]:
        return self._sticker_store.setdefault(self._month_key(), [])

    def _enter_sticker_edit_mode(self) -> None:
        if not self._sticker_assets:
            QMessageBox.information(self, "안내", "스티커 이미지가 없습니다.")
            return
        self._capture_sticker_rebase_size()
        self._sticker_snapshot = json.loads(json.dumps(self._sticker_store, ensure_ascii=False))
        self._sticker_edit_mode = True
        self._selected_sticker_id = None
        self.print_button.hide()
        self.decorate_button.hide()
        self.decorate_done_button.show()
        self.decorate_cancel_button.show()
        self.sticker_toolbar.show()
        self.sticker_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._schedule_sticker_rebase()

    def _complete_sticker_edit_mode(self) -> None:
        self._capture_sticker_rebase_size()
        self._sticker_edit_mode = False
        self._sticker_snapshot = None
        self._selected_sticker_id = None
        self.decorate_done_button.hide()
        self.decorate_cancel_button.hide()
        self.print_button.show()
        self.decorate_button.show()
        self.sticker_toolbar.hide()
        self.sticker_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Persist immediately so scale/rotation/position is not lost on quick app exit.
        self._save_sticker_store()
        self._save_stickers_after_rebase = False
        self._schedule_sticker_rebase()

    def _cancel_sticker_edit_mode(self) -> None:
        self._capture_sticker_rebase_size()
        if self._sticker_snapshot is not None:
            self._sticker_store = json.loads(json.dumps(self._sticker_snapshot, ensure_ascii=False))
        self._sticker_edit_mode = False
        self._selected_sticker_id = None
        self._sticker_snapshot = None
        self.decorate_done_button.hide()
        self.decorate_cancel_button.hide()
        self.print_button.show()
        self.decorate_button.show()
        self.sticker_toolbar.hide()
        self.sticker_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._schedule_sticker_rebase()

    def _on_sticker_close_clicked(self) -> None:
        has_changes = False
        if self._sticker_snapshot is not None:
            has_changes = (json.dumps(self._sticker_store, sort_keys=True) != 
                           json.dumps(self._sticker_snapshot, sort_keys=True))
        
        if has_changes:
            msg = QMessageBox(self)
            msg.setWindowTitle("변경사항 저장")
            msg.setText("스티커 변경사항이 있습니다. 저장하시겠습니까?")
            msg.setIcon(QMessageBox.Question)
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            
            yes_btn = msg.button(QMessageBox.Yes)
            no_btn = msg.button(QMessageBox.No)
            cancel_btn = msg.button(QMessageBox.Cancel)
            if yes_btn: yes_btn.setText("저장")
            if no_btn: no_btn.setText("저장 안 함")
            if cancel_btn: cancel_btn.setText("취소")
            
            ret = msg.exec()
            if ret == QMessageBox.Yes:
                self._complete_sticker_edit_mode()
            elif ret == QMessageBox.No:
                self._cancel_sticker_edit_mode()
            else:
                return
        else:
            self._complete_sticker_edit_mode()

    def _add_sticker(self, name: str) -> None:
        resolved_name = self._resolve_sticker_asset_name(name)
        if not self._sticker_edit_mode or resolved_name is None:
            return
        self._sticker_recent = [resolved_name] + [item for item in self._sticker_recent if item != resolved_name]
        self._sticker_recent = self._sticker_recent[:20]
        self._sanitize_sticker_preferences()
        self._save_sticker_preferences()
        self._rebuild_sticker_palette_buttons()
        items = self._current_stickers()
        safe_id_key = resolved_name.replace(":", "_")
        item = {
            "id": f"{safe_id_key}_{len(items)+1}_{self.current_year}{self.current_month}",
            "asset": resolved_name,
            "x": 0.5,
            "y": 0.5,
            "scale": 100,
            "angle": 0,
        }
        if self._is_left_anchor_sticker(resolved_name):
            item["anchor"] = "left"
        items.append(item)
        self._selected_sticker_id = str(item["id"])
        self.sticker_scale_slider.setValue(100)
        self._render_stickers()

    def _selected_sticker(self) -> dict[str, object] | None:
        if not self._selected_sticker_id:
            return None
        for item in self._current_stickers():
            if str(item.get("id", "")) == self._selected_sticker_id:
                return item
        return None

    def _on_sticker_scale_changed(self, value: int) -> None:
        if not self._sticker_edit_mode:
            return
        item = self._selected_sticker()
        if item is None:
            return
        item["scale"] = max(60, min(360, int(value)))
        self._render_stickers()

    def _rotate_selected_sticker(self, delta: int) -> None:
        if not self._sticker_edit_mode:
            return
        item = self._selected_sticker()
        if item is None:
            return
        angle = int(item.get("angle", 0))
        item["angle"] = (angle + delta) % 360
        self._render_stickers()

    def _delete_selected_sticker(self) -> None:
        if not self._sticker_edit_mode or not self._selected_sticker_id:
            return
        self._sticker_store[self._month_key()] = [
            item for item in self._current_stickers() if str(item.get("id", "")) != self._selected_sticker_id
        ]
        self._selected_sticker_id = None
        self._render_stickers()

    def _nudge_selected_sticker(self, dx: int, dy: int) -> bool:
        if not self._sticker_edit_mode:
            return False
        item = self._selected_sticker()
        if item is None:
            return False
        width = max(1, self.sticker_overlay.width())
        height = max(1, self.sticker_overlay.height())
        asset_name = self._resolve_sticker_asset_name(str(item.get("asset", "")))
        left_anchor = bool(asset_name and self._is_left_anchor_sticker(asset_name) and str(item.get("anchor", "")) == "left")

        if left_anchor:
            current_x = int(round(max(0.0, min(1.0, float(item.get("x", 0.0)))) * width))
            widget = self._sticker_widgets.get(str(item.get("id", "")))
            widget_w = widget.width() if widget is not None else 0
            max_left = max(0, width - widget_w)
            next_x = max(0, min(max_left, current_x + int(dx)))
            item["x"] = next_x / width
        else:
            current_x = int(round(max(0.0, min(1.0, float(item.get("x", 0.5)))) * width))
            next_x = max(0, min(width, current_x + int(dx)))
            item["x"] = next_x / width

        current_y = int(round(max(0.0, min(1.0, float(item.get("y", 0.5)))) * height))
        next_y = max(0, min(height, current_y + int(dy)))
        item["y"] = next_y / height
        if asset_name and self._is_bottom_line_band(asset_name):
            geo = self._cell_geometry_near_point(next_x, next_y)
            if geo is not None:
                rel = (next_y - geo.top()) / max(1, geo.height())
                item["cell_y_ratio"] = max(0.0, min(1.0, float(rel)))
        self._render_stickers()
        return True

    def _on_sticker_press(self, event, sticker_id: str) -> None:
        if not self._sticker_edit_mode:
            return
        self._selected_sticker_id = sticker_id
        widget = self._sticker_widgets.get(sticker_id)
        if widget is not None:
            self._drag_offset = event.position().toPoint()
        item = self._selected_sticker()
        if item is not None:
            self.sticker_scale_slider.blockSignals(True)
            self.sticker_scale_slider.setValue(int(item.get("scale", 100)))
            self.sticker_scale_slider.blockSignals(False)
        self._render_stickers()

    def _on_sticker_drag(self, event, sticker_id: str) -> None:
        if not self._sticker_edit_mode or sticker_id != self._selected_sticker_id:
            return
        item = self._selected_sticker()
        if item is None:
            return
        widget = self._sticker_widgets.get(sticker_id)
        if widget is None:
            return
        width = max(1, self.sticker_overlay.width())
        height = max(1, self.sticker_overlay.height())
        asset_name = self._resolve_sticker_asset_name(str(item.get("asset", "")))
        left_anchor = bool(asset_name and self._is_left_anchor_sticker(asset_name) and str(item.get("anchor", "")) == "left")
        if left_anchor:
            pos = widget.mapToParent(event.position().toPoint() - self._drag_offset)
            max_left = max(0, width - widget.width())
            px = max(0, min(max_left, pos.x()))
            item["x"] = px / width
            py = pos.y() + (widget.height() // 2)
        else:
            pos = widget.mapToParent(event.position().toPoint() - self._drag_offset + QPoint(widget.width() // 2, widget.height() // 2))
            px = max(0, min(width, pos.x()))
            item["x"] = px / width
            py = pos.y()
        py = max(0, min(height, py))
        item["y"] = py / height
        if asset_name and self._is_bottom_line_band(asset_name):
            geo = self._cell_geometry_near_point(px, py)
            if geo is not None:
                rel = (py - geo.top()) / max(1, geo.height())
                item["cell_y_ratio"] = max(0.0, min(1.0, float(rel)))
        self._render_stickers()

    def _render_stickers(self) -> None:
        overlay_w = max(1, self.sticker_overlay.width())
        overlay_h = max(1, self.sticker_overlay.height())
        current_ids: set[str] = set()
        month_items = self._current_stickers()
        _, _, _body_top, body_h = self._sticker_metrics()
        cell_w = max(1, overlay_w // 7)
        cell_h = max(1, body_h // 6)
        cell_min = max(1, min(cell_w, cell_h))
        base_icon = max(28, min(200, int(cell_min * 0.52)))

        for item in month_items:
            sticker_id = str(item.get("id", ""))
            asset_name = self._resolve_sticker_asset_name(str(item.get("asset", "")))
            if not sticker_id or asset_name is None:
                continue
            item["asset"] = asset_name
            current_ids.add(sticker_id)
            base = self._animated_sticker_base(sticker_id, asset_name) or self._sticker_assets[asset_name]
            scale = max(60, min(360, int(item.get("scale", 100))))
            angle = int(item.get("angle", 0)) % 360
            x = int(round(max(0.0, min(1.0, float(item.get("x", 0.5)))) * overlay_w))
            y = int(round(max(0.0, min(1.0, float(item.get("y", 0.5)))) * overlay_h))
            cell_geo = self._cell_geometry_near_point(x, y)
            if cell_geo is None:
                local_cell_w, local_cell_h = self._cell_size_near_point(x, y)
            else:
                local_cell_w, local_cell_h = max(1, cell_geo.width()), max(1, cell_geo.height())

            if self._is_band_sticker(asset_name):
                width_days = self._band_width_days(asset_name, scale)
                day_w = self._band_target_day_width_px(asset_name, local_cell_w)
                band_w = int(round(day_w * width_days))
                band_h = self._band_target_height_px(asset_name, local_cell_h)
                target_w = max(24, min(overlay_w, band_w))
                target_h = max(18, min(overlay_h, band_h))
                if self._is_box_sticker(asset_name):
                    if self._is_outline_only_box(asset_name):
                        cap_dst_w = self._band_cap_width_px(asset_name, local_cell_h)
                        pix = self._stretch_band_pixmap(
                            base,
                            target_w,
                            target_h,
                            cap_dst_w,
                            asset_name,
                        )
                    else:
                        pix = self._render_box_band_pixmap(asset_name, target_w, target_h)
                else:
                    cap_dst_w = self._band_cap_width_px(asset_name, local_cell_h)
                    pix = self._stretch_band_pixmap(
                        base,
                        target_w,
                        target_h,
                        cap_dst_w,
                        asset_name,
                    )
            else:
                icon_size = max(20, min(600, int(base_icon * (scale / 100))))
                pix = base.scaled(icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if angle:
                    pix = pix.transformed(QTransform().rotate(float(angle)), Qt.SmoothTransformation)

            widget = self._sticker_widgets.get(sticker_id)
            if widget is None:
                widget = StickerItem(self.sticker_overlay, sticker_id, self._on_sticker_press, self._on_sticker_drag)
                self._sticker_widgets[sticker_id] = widget
            widget.setPixmap(pix)
            widget.resize(pix.size())
            left_anchor = bool(self._is_left_anchor_sticker(asset_name) and str(item.get("anchor", "")) == "left")
            if self._is_left_anchor_sticker(asset_name) and "anchor" not in item:
                # Backward compatibility: legacy band stickers stored center anchor.
                center_x = max(0.0, min(1.0, float(item.get("x", 0.5))))
                left_x = max(0.0, min(1.0, center_x - (widget.width() / 2.0) / max(1, overlay_w)))
                item["x"] = left_x
                item["anchor"] = "left"
                left_anchor = True
            if self._is_band_sticker(asset_name):
                if self._is_outline_only_box(asset_name):
                    # Keep outline boxes locked to calendar row boundaries on resize.
                    if cell_geo is not None:
                        y = cell_geo.top() + (widget.height() // 2)
                    else:
                        y -= (self._band_vertical_lift_px(local_cell_h) // 2) + 1
                elif self._is_bottom_line_band(asset_name):
                    # Orange line: keep Y anchored to row-relative position while resizing.
                    if cell_geo is not None:
                        raw_ratio = item.get("cell_y_ratio")
                        if raw_ratio is None:
                            inferred = (y - cell_geo.top()) / max(1, local_cell_h)
                            y_ratio = max(0.0, min(1.0, float(inferred)))
                            item["cell_y_ratio"] = y_ratio
                        else:
                            y_ratio = max(0.0, min(1.0, float(raw_ratio)))
                        y = cell_geo.top() + int(round(local_cell_h * y_ratio))
                    else:
                        y -= (self._band_vertical_lift_px(local_cell_h) // 2) + 1
                else:
                    y -= self._band_vertical_lift_px(local_cell_h)
            if left_anchor:
                max_left = max(0, overlay_w - widget.width())
                widget.move(max(0, min(max_left, x)), y - widget.height() // 2)
            else:
                widget.move(x - widget.width() // 2, y - widget.height() // 2)
            if self._sticker_edit_mode and sticker_id == self._selected_sticker_id:
                if self._is_outline_only_box(asset_name) or self._is_bottom_line_band(asset_name):
                    widget.setStyleSheet("background: transparent; border: none;")
                else:
                    widget.setStyleSheet(f"background: transparent; border: 1px dashed {self.palette['accent']};")
            else:
                widget.setStyleSheet("background: transparent; border: none;")
            widget.show()
            widget.raise_()

        for sticker_id, widget in list(self._sticker_widgets.items()):
            if sticker_id in current_ids:
                continue
            widget.deleteLater()
            self._sticker_widgets.pop(sticker_id, None)
            self._sticker_animation_state.pop(sticker_id, None)

    def refresh(self) -> None:
        self.palette = THEMES[self.theme_name]
        if self.sidebar_mode == "search" and self.search_query:
            self.search_results = self.repository.search_entries(self.search_query)
        self.setStyleSheet(app_stylesheet(self.palette))
        self.sticker_toolbar.setStyleSheet(
            """
            QFrame#stickerToolbar {
                background: #f8efc5;
                border: 1px solid #d5c68a;
                border-radius: 11px;
            }
            QLabel#stickerDragHandle {
                color: #a6965b;
                background: transparent;
                font-weight: bold;
                font-size: 13px;
                padding: 0px;
                margin-bottom: 2px;
            }
            QLabel#stickerToolbarLabel {
                color: #6e5f2d;
                background: transparent;
                font-weight: 700;
                padding: 0 2px;
            }
            QFrame#stickerDivider {
                background: #dbcfa0;
                border: none;
                min-width: 1px;
                max-width: 1px;
                margin: 3px 4px;
            }
            QToolButton#stickerChip {
                background: #f7edbf;
                border: 1px solid #ddcc8e;
                border-radius: 13px;
                padding: 0px;
            }
            QToolButton#stickerChip:hover {
                background: #fff4cd;
                border-color: #ccb66d;
            }
            QPushButton#stickerLibraryButton {
                background: #f6eab8;
                color: #4f4422;
                border: 1px solid #ccb874;
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QPushButton#stickerLibraryButton:hover {
                background: #fff1bf;
            }
            QSlider#stickerScaleSlider::groove:horizontal {
                background: #c7b877;
                height: 4px;
                border-radius: 2px;
            }
            QSlider#stickerScaleSlider::sub-page:horizontal {
                background: #86723a;
                border-radius: 2px;
            }
            QSlider#stickerScaleSlider::handle:horizontal {
                background: #fff8dd;
                border: 1px solid #9e894b;
                width: 14px;
                margin: -6px 0;
                border-radius: 7px;
            }
            QPushButton#stickerMiniButton {
                background: #fff7d3;
                color: #5b4f2b;
                border: 1px solid #cfbe7f;
                border-radius: 8px;
                font-weight: 700;
            }
            QPushButton#stickerMiniButton:hover {
                background: #fffbe8;
            }
            QPushButton#stickerDangerButton {
                background: #fff2e0;
                color: #8f402f;
                border: 1px solid #dfb39b;
                border-radius: 8px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QPushButton#stickerDangerButton:hover {
                background: #ffe8db;
            }
            QPushButton#stickerCloseButton {
                background: transparent;
                color: #8f7f4f;
                border: none;
                font-size: 14px;
                font-weight: bold;
                padding: 0px;
                border-radius: 4px;
            }
            QPushButton#stickerCloseButton:hover {
                color: #c93b2b;
                background: #ffebd2;
            }
            """
        )
        self.sidebar_scroll.setStyleSheet(
            f"QScrollArea {{ background: {self.palette['panel']}; border: none; }}"
            f"QScrollArea > QWidget > QWidget {{ background: {self.palette['panel']}; }}"
        )
        self.sidebar_content.setStyleSheet(f"background: {self.palette['panel']};")
        self.calendar_grid_widget.setStyleSheet("background: transparent;")
        for idx, label in enumerate(self.weekday_labels):
            color = self.palette["danger"] if idx == 0 else self.palette["info"] if idx == 6 else self.palette["muted"]
            label.setStyleSheet(
                f"background: {self.palette['panel_alt']}; color: {color};"
                f"border: 1px solid {self.palette['line']}; padding: 4px 0 3px 0;"
            )
        self.year_button.setText(f"{self.current_year}년")
        self.month_button.setText(f"{self.current_month}월")
        self.calendar_title.setText(f"{self.current_year}년 {self.current_month}월")
        summary = self.repository.day_summary_for_today()
        today_count = summary.schedules + summary.tasks
        self.schedule_button.set_count(today_count)
        self._enforce_equal_calendar_cells()
        self._render_calendar()
        self._render_sidebar()
        self._sync_sticker_overlay()
        self._render_stickers()
        self._apply_clickable_cursor()

    def _render_calendar(self) -> None:
        entries = self.repository.list_entries_for_month(self.current_year, self.current_month)
        item_capacity = self._calendar_item_capacity()
        self._last_calendar_item_capacity = item_capacity
        grouped: dict[date, list[CalendarEntry]] = {}
        for entry in entries:
            if entry.day and self.hide_completed_on_calendar and self._is_entry_completed_on_day(entry, entry.day):
                continue
            if entry.day:
                grouped.setdefault(entry.day, []).append(entry)

        cal = calendar.Calendar(firstweekday=6)
        weeks = cal.monthdatescalendar(self.current_year, self.current_month)
        while len(weeks) < 6:
            last_day = weeks[-1][-1]
            weeks.append([last_day.fromordinal(last_day.toordinal() + offset + 1) for offset in range(7)])
        flat_days = [item for week in weeks[:6] for item in week]

        for index, current_day in enumerate(flat_days):
            cell = self.day_cells[index]
            cell.clear_items()
            cell.day_value = current_day
            in_month = current_day.month == self.current_month
            is_today = current_day == date.today()
            is_selected = current_day == self.selected_day
            holiday_name = self._holiday_name_for_day(current_day)
            cell.setStyleSheet(day_cell_style(self.palette, in_month, is_today, is_selected))
            cell.number_label.setText(str(current_day.day))

            if holiday_name:
                cell.number_label.setStyleSheet(f"font-size: 11pt; color: {self.palette['danger']}; background: transparent; border: none;")
            elif current_day.weekday() == 6:
                cell.number_label.setStyleSheet(f"font-size: 11pt; color: {self.palette['danger']}; background: transparent; border: none;")
            elif current_day.weekday() == 5:
                cell.number_label.setStyleSheet(f"font-size: 11pt; color: {self.palette['info']}; background: transparent; border: none;")
            else:
                color = self.palette.get("badge_selected_fg", self.palette["text"]) if is_selected else (self.palette["text"] if in_month else self.palette["muted"])
                cell.number_label.setStyleSheet(f"font-size: 11pt; color: {color}; background: transparent; border: none;")

            badge_text = "오늘" if is_today else "선택" if is_selected else ""
            if badge_text:
                cell.badge_label.setText(badge_text)
                cell.badge_label.setStyleSheet(badge_style(self.palette, badge_text == "선택"))
                cell.badge_label.show()
            else:
                cell.badge_label.hide()

            day_entries = grouped.get(current_day, [])
            slots_for_entries = item_capacity
            entry_fg = self.palette.get("badge_selected_fg", "#1f2328") if is_selected else self.palette.get("entry_text", "#1f2328")
            more_fg = self.palette.get("badge_selected_fg", "#111111") if is_selected else self.palette.get("more_text", "#111111")
            if holiday_name:
                holiday_label = QLabel(holiday_name)
                holiday_label.setStyleSheet(f"color: {self.palette['danger']}; background: transparent; border: none;")
                cell.items_layout.addWidget(holiday_label, 0, Qt.AlignLeft)
                slots_for_entries = max(0, item_capacity - 1)
            # Prefer showing one more real item instead of a lone "+1건" marker.
            if len(day_entries) == slots_for_entries + 1:
                slots_for_entries += 1
            for entry in day_entries[:slots_for_entries]:
                edit_entry = lambda e=entry: self._edit_entry(e.entry_type, e)
                completed_on_day = self._is_entry_completed_on_day(entry, current_day)
                chip = DraggableCalendarEntryChip(
                    cell,
                    entry,
                    current_day,
                    self._entry_title_text(entry),
                    f"[{entry.start_time}] " if entry.start_time else "",
                    entry_fg,
                    self.palette["info"],
                    edit_entry,
                    completed_on_day,
                    entry.bg_color,
                )
                cell.items_layout.addWidget(chip, 0, Qt.AlignLeft)
            if len(day_entries) > slots_for_entries:
                more = QLabel(f"+{len(day_entries) - slots_for_entries}건")
                more.setStyleSheet(
                    f"background: transparent; border: none; color: {more_fg};"
                )
                cell.items_layout.addWidget(more)

    def _render_sidebar(self) -> None:
        while self.sidebar_layout.count() > 1:
            item = self.sidebar_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._memo_card_widgets.clear()

        if self.sidebar_mode == "search":
            self.memo_title_only_check.hide()
            self.info_add_button.hide()
            self.info_export_button.show()
            self.info_title.setText(f"검색결과 {len(self.search_results)}건")
            if not self.search_results:
                self.sidebar_layout.insertWidget(0, self._empty_label("검색 결과가 없습니다."))
                return
            for entry in self.search_results:
                self.sidebar_layout.insertWidget(self.sidebar_layout.count() - 1, self._search_result_row(entry))
            return

        if self.sidebar_mode == "memo":
            self.info_export_button.hide()
            self.memo_title_only_check.show()
            self.info_add_button.show()
            self.memo_title_only_check.blockSignals(True)
            self.memo_title_only_check.setChecked(self.memo_title_only)
            self.memo_title_only_check.blockSignals(False)
            items = self._ordered_memos(self.repository.list_memos())
            self.info_title.setText(f"메모 {len(items)}개")
            self.info_add_button.setText("메모 추가")
            if not items:
                self.sidebar_layout.insertWidget(0, self._empty_label("등록된 메모가 없습니다."))
                return
            for entry in items:
                card = self._sidebar_card(entry)
                self.sidebar_layout.insertWidget(self.sidebar_layout.count() - 1, card)
                if entry.entry_id is not None:
                    self._memo_card_widgets[int(entry.entry_id)] = card
            if self._pending_scroll_memo_id is not None:
                target = self._memo_card_widgets.get(self._pending_scroll_memo_id)
                self._pending_scroll_memo_id = None
                if target is not None:
                    QTimer.singleShot(0, lambda w=target: self.sidebar_scroll.ensureWidgetVisible(w, 0, 8))
            return
        self.info_export_button.hide()
        self.memo_title_only_check.hide()
        self.info_add_button.show()

        self.info_title.setText(self.selected_day.strftime("%Y.%m.%d"))
        self.info_add_button.setText("일정 추가")
        items = self.repository.list_entries_for_day(self.selected_day)
        if not items:
            self.sidebar_layout.insertWidget(0, self._empty_label("등록된 일정이 없습니다."))
            return
        for entry in items:
            self.sidebar_layout.insertWidget(self.sidebar_layout.count() - 1, self._sidebar_card(entry))

    def _search_result_row(self, entry: CalendarEntry) -> QWidget:
        row = QFrame()
        row.setObjectName("panel")
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(4)

        if entry.entry_type == EntryType.MEMO:
            tag_color = self.palette.get("icon_important", "#ffd166")
            tag_html = f'<span style="color: {tag_color}; font-weight: bold;">[메모]</span>'
            headline_html = f"{tag_html} {(entry.title or '메모').strip()}"
        else:
            anchor = entry.start_date or entry.day or date.today()
            if entry.entry_type == EntryType.SCHEDULE:
                tag_color = self.palette.get("info", "#8ab6ff")
                tag_text = "[일정]"
            else:
                tag_color = self.palette.get("work", "#8b78f1")
                tag_text = "[업무]"
            tag_html = f'<span style="color: {tag_color}; font-weight: bold;">{tag_text}</span>'
            headline_html = f"{tag_html} {anchor.strftime('%Y.%m.%d')}"
            
        title_lbl = ClickableLabel()
        title_lbl.setText(headline_html)
        title_lbl.setStyleSheet(f"color: {self.palette['text']}; background: transparent; border: none; font-size: 13px; font-weight: 600;")
        title_lbl.clicked.connect(lambda e=entry: self._open_search_result(e))
        row_layout.addWidget(title_lbl)

        if entry.description:
            preview = " ".join(entry.description.splitlines()).strip()
            if len(preview) > 70:
                preview = preview[:70].rstrip() + "..."
            detail = QLabel(preview)
            detail.setObjectName("muted")
            detail.setWordWrap(True)
            detail.setStyleSheet(f"color: {self.palette['muted']}; background: transparent; border: none;")
            row_layout.addWidget(detail)

        return row

    def _open_search_result(self, entry: CalendarEntry) -> None:
        if entry.entry_type == EntryType.MEMO:
            self.sidebar_mode = "memo"
            self._pending_scroll_memo_id = entry.entry_id
            self.refresh()
            return
        target = entry.start_date or entry.day or date.today()
        self.current_year = target.year
        self.current_month = target.month
        self.selected_day = target
        self.sidebar_mode = "day"
        self.refresh()

    def _sidebar_card(self, entry: CalendarEntry) -> QWidget:
        is_completed = self._is_entry_completed_on_day(entry, self.selected_day)
        hide_memo_body = entry.entry_type == EntryType.MEMO and self.memo_title_only
        card: QFrame
        drag_enabled = (
            self.sidebar_mode == "memo"
            and entry.entry_type == EntryType.MEMO
            and self.memo_title_only
            and entry.entry_id is not None
        )
        if drag_enabled and entry.entry_id is not None:
            card = MemoDragCard(self.sidebar_content, int(entry.entry_id), True)
            card.reordered.connect(self._on_memo_card_reordered)
        else:
            card = QFrame()
        card.setObjectName("donePanel" if is_completed else "panel")
        card.setAttribute(Qt.WA_StyledBackground, True)
        if is_completed:
            card.setStyleSheet(
                f"background: {self.palette.get('done_panel_qt', '#d7dce2')}; border: 1px solid {self.palette['line_soft']}; border-radius: 10px;"
            )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)

        lead = f"⭐ [{entry.start_time}]" if entry.start_time else ("[종일]" if entry.all_day else "")
        details: list[str] = []
        if entry.start_time:
            pass
        elif entry.all_day:
            pass
        if entry.recurrence_enabled:
            details.append("반복")
        if entry.assignee:
            details.append(f"담당: {entry.assignee}")
        if entry.status and entry.status != "완료":
            details.append(f"상태: {entry.status}")
        meta_row = QWidget()
        meta_row.setStyleSheet("background: transparent; border: none;")
        meta_layout = QHBoxLayout(meta_row)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        if entry.entry_type == EntryType.MEMO:
            left_text = (entry.title or "메모").strip()
            left_label = ClickableLabel(left_text)
            left_label.clicked.connect(lambda e=entry: self._open_entry_view(e))
            left_label.setStyleSheet(f"color: {self.palette['text']}; background: transparent; border: none;")
        else:
            left_text = "  ".join([part for part in [lead, *details] if part]).strip()
            left_label = ClickableLabel(left_text)
            left_label.clicked.connect(lambda e=entry: self._open_entry_view(e))
            left_label.setObjectName("muted")
            left_label.setStyleSheet(f"color: {self.palette['muted']}; background: transparent; border: none;")
        meta_layout.addWidget(left_label, 1)

        actions_wrap = QWidget()
        actions_wrap.setStyleSheet("background: transparent;")
        actions = QHBoxLayout(actions_wrap)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        if entry.entry_type != EntryType.MEMO:
            complete = QToolButton()
            complete.setAutoRaise(False)
            complete.setToolTip("완료 취소" if is_completed else "완료")
            complete.setText("")
            icon_key = "cancel" if is_completed else "complete"
            if icon_key in self._action_icons:
                complete.setIcon(self._action_icons[icon_key])
                complete.setIconSize(QSize(46, 20))
            complete.setFixedSize(46, 20)
            complete.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
            complete.clicked.connect(lambda _checked=False, e=entry: self._toggle_complete(e))
            actions.addWidget(complete)
        edit = QToolButton()
        edit.setAutoRaise(False)
        edit.setToolTip("수정")
        edit.setText("")
        if "edit" in self._action_icons:
            edit.setIcon(self._action_icons["edit"])
            edit.setIconSize(QSize(46, 20))
        edit.setFixedSize(46, 20)
        edit.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
        edit.clicked.connect(lambda _checked=False, e=entry: self._edit_entry(e.entry_type, e))
        actions.addWidget(edit)
        delete = QToolButton()
        delete.setAutoRaise(False)
        delete.setToolTip("삭제")
        delete.setText("")
        if "delete" in self._action_icons:
            delete.setIcon(self._action_icons["delete"])
            delete.setIconSize(QSize(46, 20))
        delete.setFixedSize(46, 20)
        delete.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
        delete.clicked.connect(lambda _checked=False, e=entry: self._delete_entry(e))
        actions.addWidget(delete)
        meta_layout.addWidget(actions_wrap, 0, Qt.AlignRight)
        layout.addWidget(meta_row)

        if entry.description and not hide_memo_body:
            desc = ClickableTextEdit()
            desc.setReadOnly(True)
            desc.setPlainText(entry.description)
            desc.setLineWrapMode(QTextEdit.WidgetWidth)
            desc.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            desc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            desc.setMaximumHeight(190)
            line_h = max(14, desc.fontMetrics().lineSpacing())
            line_count = max(1, entry.description.count("\n") + 1)
            target_h = min(190, line_h * line_count + 16)
            desc.setFixedHeight(target_h)
            desc.setStyleSheet(text_editor_style(self.palette))
            desc.clicked.connect(lambda e=entry: self._open_entry_view(e))
            layout.addWidget(desc)

        if entry.attachments and not hide_memo_body:
            attach_block = QWidget()
            attach_block.setStyleSheet("background: transparent; border: none;")
            attach_block_layout = QVBoxLayout(attach_block)
            attach_block_layout.setContentsMargins(0, 0, 0, 0)
            attach_block_layout.setSpacing(1)

            attach_head = QLabel(f"첨부파일 {len(entry.attachments)}건")
            attach_head.setObjectName("muted")
            attach_head.setContentsMargins(0, 0, 0, 0)
            attach_head.setStyleSheet("background: transparent; border: none;")
            attach_block_layout.addWidget(attach_head)
            for attachment in entry.attachments:
                row_widget = QWidget()
                row_widget.setStyleSheet("background: transparent; border: none;")
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(2)
                full_name = self._attachment_display_name(attachment)
                name_label = QLabel(self._attachment_elided_text(full_name))
                name_label.setToolTip(full_name)
                name_label.setWordWrap(False)
                name_label.setStyleSheet("background: transparent; border: none;")
                name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                name_label.setMinimumWidth(0)
                row_layout.addWidget(name_label, 1)
                down = QToolButton()
                down.setToolTip("save")
                down.setText("")
                if "save" in self._action_icons:
                    down.setIcon(self._action_icons["save"])
                    down.setIconSize(QSize(46, 20))
                down.setFixedSize(46, 20)
                down.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
                down.clicked.connect(lambda _checked=False, a=attachment: self._download_attachment(a))
                row_layout.addWidget(down)
                remove = QToolButton()
                remove.setToolTip("del")
                remove.setText("")
                if "delete" in self._action_icons:
                    remove.setIcon(self._action_icons["delete"])
                    remove.setIconSize(QSize(46, 20))
                remove.setFixedSize(46, 20)
                remove.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; }")
                remove.clicked.connect(lambda _checked=False, e=entry, a=attachment: self._remove_attachment(e, a))
                row_layout.addWidget(remove)
                attach_block_layout.addWidget(row_widget)

            layout.addWidget(attach_block)

        return card

    def _empty_label(self, text: str) -> QLabel:
        label = QLabel((text or "").replace("업습니다", "없습니다").rstrip("."))
        label.setObjectName("muted")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            f"border: 1px dashed {self.palette['line_soft']};"
            f"background: {self.palette['panel_alt']}; padding: 18px 10px; border-radius: 8px;"
        )
        return label

    def _handle_add_button(self) -> None:
        if self.sidebar_mode == "memo":
            self._edit_entry(EntryType.MEMO, None)
        else:
            self._edit_entry(EntryType.SCHEDULE, None)

    @staticmethod
    def _entry_chip_text(entry: CalendarEntry) -> str:
        icon = MainWindow._entry_icon(entry)
        base = f"{entry.start_time} {entry.title}".strip() if entry.start_time else entry.title
        return f"{icon} {base}".strip() if icon else base

    @staticmethod
    def _entry_title_text(entry: CalendarEntry) -> str:
        icon = MainWindow._entry_icon(entry)
        return f"{icon} {entry.title}".strip() if icon else entry.title

    @staticmethod
    def _entry_icon(entry: CalendarEntry) -> str:
        if entry.icon_type == "anniversary":
            return "🎂"
        if entry.icon_type == "important":
            return "⭐"
        if entry.icon_type == "coffee":
            return "☕"
        if entry.icon_type == "meal":
            return "🍚"
        if entry.icon_type == "meeting":
            return "👥"
        return ""

    @staticmethod
    def _is_entry_completed_on_day(entry: CalendarEntry, target_day: date) -> bool:
        if entry.recurrence_enabled:
            return target_day.isoformat() in entry.completed_dates
        return entry.status == "완료"

    def _set_sidebar_mode(self, mode: str) -> None:
        self.sidebar_mode = mode
        self.refresh()

    def _run_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.search_query = ""
            self.search_results = []
            self.sidebar_mode = "day"
            self.refresh()
            return
        self.search_query = query
        self.search_results = self.repository.search_entries(query)
        self.sidebar_mode = "search"
        self.refresh()

    def _print_calendar_view(self) -> None:
        if self._sticker_edit_mode:
            QMessageBox.information(self, "인쇄", "꾸미기 모드를 종료한 뒤 인쇄해 주세요.")
            return
        button_states = (
            (self.print_button, self.print_button.isVisible()),
            (self.decorate_button, self.decorate_button.isVisible()),
        )
        for button, _visible in button_states:
            button.hide()
        self.calendar_panel.update()
        QApplication.processEvents()
        try:
            printer = QPrinter(QPrinter.HighResolution)
            printer.setDocName(f"taskcalendar_{self.current_year}_{self.current_month:02d}")
            printer.setPageOrientation(QPageLayout.Orientation.Landscape)
            preview = QPrintPreviewDialog(printer, self)
            preview.setWindowTitle("인쇄 미리보기")
            preview.paintRequested.connect(self._render_calendar_for_print)
            self._configure_print_preview_dialog(preview)
            preview.exec()
        finally:
            for button, was_visible in button_states:
                if was_visible:
                    button.show()
                else:
                    button.hide()
            self.calendar_panel.update()

    def _configure_print_preview_dialog(self, preview: QPrintPreviewDialog) -> None:
        width = max(980, int(self.width() * 0.9))
        height = max(720, int(self.height() * 0.9))
        preview.setMinimumSize(900, 650)
        preview.resize(width, height)

        def _apply_default_zoom() -> None:
            widget = preview.findChild(QPrintPreviewWidget)
            if widget is None:
                return
            try:
                widget.setViewMode(QPrintPreviewWidget.ViewMode.SinglePageView)
            except Exception:
                pass
            try:
                widget.setZoomMode(QPrintPreviewWidget.ZoomMode.FitToWidth)
                return
            except Exception:
                pass
            try:
                widget.fitInView()
            except Exception:
                pass

        QTimer.singleShot(0, _apply_default_zoom)
        QTimer.singleShot(120, _apply_default_zoom)

    def _render_calendar_for_print(self, printer: QPrinter) -> None:
        source = self.calendar_panel
        if source.width() <= 0 or source.height() <= 0:
            return

        painter = QPainter(printer)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            viewport = painter.viewport()
            painter.fillRect(viewport, Qt.white)

            margin = 12
            target_rect = QRect(
                viewport.x() + margin,
                viewport.y() + margin,
                max(1, viewport.width() - (margin * 2)),
                max(1, viewport.height() - (margin * 2)),
            )

            try:
                scale = min(
                    target_rect.width() / max(1, source.width()),
                    target_rect.height() / max(1, source.height()),
                )
                target_w = int(source.width() * scale)
                target_h = int(source.height() * scale)
                x = target_rect.x() + max(0, (target_rect.width() - target_w) // 2)
                y = target_rect.y() + max(0, (target_rect.height() - target_h) // 2)
                painter.save()
                painter.translate(x, y)
                painter.scale(scale, scale)
                source.render(painter, QPoint(0, 0))
                painter.restore()
            except Exception:
                # Fallback: raster snapshot draw with simple target rect mapping.
                painter.resetTransform()
                snapshot = source.grab()
                if snapshot.isNull():
                    return
                sw = max(1.0, snapshot.width() / max(1.0, snapshot.devicePixelRatio()))
                sh = max(1.0, snapshot.height() / max(1.0, snapshot.devicePixelRatio()))
                scale = min(target_rect.width() / sw, target_rect.height() / sh)
                draw_w = int(sw * scale)
                draw_h = int(sh * scale)
                x = target_rect.x() + max(0, (target_rect.width() - draw_w) // 2)
                y = target_rect.y() + max(0, (target_rect.height() - draw_h) // 2)
                painter.drawPixmap(QRect(x, y, draw_w, draw_h), snapshot)
        finally:
            painter.end()

    def _default_export_dir(self) -> Path:
        output_dir = data_path("exports")
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _ask_export_path(self, default_filename: str) -> Path | None:
        default_path = self._default_export_dir() / default_filename
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "엑셀 저장",
            str(default_path),
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return None
        target = Path(file_path)
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        return target

    def _export_entries_with_picker(self, entries: list[CalendarEntry], default_filename: str) -> None:
        if not entries:
            QMessageBox.information(self, "엑셀 저장", "저장할 데이터가 없습니다.")
            return
        file_path = self._ask_export_path(default_filename)
        if file_path is None:
            return
        try:
            count = export_entries_to_excel(file_path, entries)
        except RuntimeError as exc:
            QMessageBox.warning(self, "엑셀 기능", str(exc))
            return
        except Exception as exc:
            logger.exception("excel export failed")
            QMessageBox.critical(self, "엑셀 저장 실패", f"엑셀 저장 중 오류가 발생했습니다.\n{exc}")
            return
        QMessageBox.information(self, "엑셀 저장", f"{count}건을 저장했습니다.\n{file_path}")

    def _export_search_results_to_excel(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        self._export_entries_with_picker(self.search_results, f"taskcalendar_search_{stamp}.xlsx")

    def _export_all_entries_to_excel(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        entries = self.repository.list_all_entries()
        self._export_entries_with_picker(entries, f"taskcalendar_all_{stamp}.xlsx")

    def _import_all_entries_from_excel(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "엑셀 불러오기",
            str(self._default_export_dir()),
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return
        if QMessageBox.question(
            self,
            "엑셀 불러오기",
            "현재 일정/업무/메모 데이터를 엑셀 파일 내용으로 교체합니다.\n계속할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            imported = import_entries_from_excel(Path(file_path))
            count = self.repository.replace_all_entries(imported)
            self.repository.set_setting("memo_order_v1", "[]")
            self.repository.save()
        except RuntimeError as exc:
            QMessageBox.warning(self, "엑셀 기능", str(exc))
            return
        except Exception as exc:
            logger.exception("excel import failed")
            QMessageBox.critical(self, "엑셀 불러오기 실패", f"엑셀 불러오기 중 오류가 발생했습니다.\n{exc}")
            return

        self.search_query = ""
        self.search_results = []
        self.sidebar_mode = "day"
        self.refresh()
        QMessageBox.information(self, "엑셀 불러오기", f"{count}건을 불러왔습니다.")

    def _export_data_flow(self) -> None:
        dialog = BackupRestoreFormatDialog(self, mode="export")
        if not dialog.exec():
            return

        fmt = dialog.selected_format
        if fmt == "xlsx":
            self._export_all_entries_to_excel()
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            default_path = self._default_export_dir() / f"taskcalendar_backup_{stamp}.zip"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "데이터 내보내기 (ZIP)",
                str(default_path),
                "ZIP Backup Files (*.zip)",
            )
            if not file_path:
                return
            try:
                backup_to_zip(self.repository.db_path, self.repository.attachments_root, Path(file_path))
                QMessageBox.information(self, "데이터 내보내기", f"백업 파일이 성공적으로 저장되었습니다.\n{file_path}")
            except Exception as exc:
                logger.exception("zip backup failed")
                QMessageBox.critical(self, "데이터 내보내기 실패", f"백업 중 오류가 발생했습니다.\n{exc}")

    def _import_data_flow(self) -> None:
        dialog = BackupRestoreFormatDialog(self, mode="import")
        if not dialog.exec():
            return

        fmt = dialog.selected_format
        if fmt == "xlsx":
            self._import_all_entries_from_excel()
        else:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "데이터 가져오기 (ZIP)",
                str(self._default_export_dir()),
                "ZIP Backup Files (*.zip)",
            )
            if not file_path:
                return

            reply = QMessageBox.warning(
                self,
                "데이터 가져오기 경고",
                "경고: 정말 데이터를 복원하시겠습니까?\n\n이 작업은 현재 캘린더에 있는 모든 일정, 메모, 설정 및 첨부파일을 덮어씁니다. 이 작업은 되돌릴 수 없습니다.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

            try:
                restore_from_zip(Path(file_path), self.repository.db_path, self.repository.attachments_root)
                self.repository.reload_database()
                self.search_query = ""
                self.search_results = []
                self.sidebar_mode = "day"
                self.refresh()
                QMessageBox.information(self, "데이터 가져오기", "백업 데이터가 성공적으로 복원되었습니다.")
            except Exception as exc:
                logger.exception("zip restore failed")
                QMessageBox.critical(self, "데이터 가져오기 실패", f"복원 중 오류가 발생했습니다.\n{exc}")


    def _restore_auto_backup_flow(self) -> None:
        backup_dir = self.repository.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "복원할 백업 파일 선택 (taskcalendar_backup_*.db.enc)",
            str(backup_dir),
            "Backup Files (taskcalendar_backup_*.db.enc);;All Files (*.*)",
        )
        if not file_path:
            return

        try:
            from taskcalendar.storage import unprotect_bytes
            import sqlite3
            import shutil

            db_path = Path(file_path)
            raw = db_path.read_bytes()

            # 1. Try Decrypt
            try:
                plain = unprotect_bytes(raw)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "복원 실패",
                    f"파일 복호화에 실패했습니다.\n"
                    f"다른 PC/계정에서 생성된 백업 파일이거나 암호화 키가 다릅니다.\n\n"
                    f"상세 오류: {exc}"
                )
                return

            # 2. Test SQLite structure and count entries
            conn = sqlite3.connect(":memory:")
            try:
                conn.deserialize(plain)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM entries")
                count = cur.fetchone()[0]
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "복원 실패",
                    f"유효한 SQLite 데이터베이스가 아닙니다.\n"
                    f"파일 내용이 손상되었을 수 있습니다.\n\n"
                    f"상세 오류: {exc}"
                )
                return

            # 3. Confirmation dialog
            reply = QMessageBox.warning(
                self,
                "백업 복원",
                f"선택한 백업 파일에서 복원을 진행하시겠습니까?\n\n"
                f"- 복원 대상: {db_path.name}\n"
                f"- 일정/메모 건수: {count}건\n\n"
                f"※ 주의: 현재 입력되어 있는 모든 데이터가 덮어씌워집니다.\n"
                f"(기존 데이터는 복원 직전 백업본으로 저장됩니다.)",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

            # 4. Backup current DB and apply restored DB
            current_db = self.repository.db_path
            current_bak = current_db.with_name("taskcalendar.db.enc.backup_before_restore")

            if current_db.exists():
                shutil.copy2(current_db, current_bak)

            shutil.copy2(db_path, current_db)
            shutil.copy2(db_path, current_db.with_suffix(current_db.suffix + ".bak"))

            # Reload repository
            self.repository.reload_database()
            self.search_query = ""
            self.search_results = []
            self.sidebar_mode = "day"
            self.refresh()

            QMessageBox.information(self, "복원 완료", f"성공적으로 데이터를 복구했습니다!\n일정/메모 {count}건을 로드했습니다.")

        except Exception as exc:
            logger.exception("auto backup restore failed")
            QMessageBox.critical(self, "오류", f"데이터 복원 중 예상치 못한 오류가 발생했습니다.\n{exc}")


    def _on_memo_title_only_toggled(self, checked: bool) -> None:
        self.memo_title_only = bool(checked)
        self.repository.set_setting("memo_title_only", "1" if checked else "0")
        self.repository.save()
        if self.sidebar_mode == "memo":
            self._render_sidebar()

    def _load_memo_order_ids(self) -> list[int]:
        raw = self.repository.get_setting("memo_order_v1", "[]")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        result: list[int] = []
        seen: set[int] = set()
        for item in parsed:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _save_memo_order_ids(self, memo_ids: list[int], persist: bool) -> None:
        clean: list[int] = []
        seen: set[int] = set()
        for item in memo_ids:
            value = int(item)
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            clean.append(value)
        self.repository.set_setting("memo_order_v1", json.dumps(clean, ensure_ascii=False))
        if persist:
            self.repository.save()

    def _ordered_memos(self, items: list[CalendarEntry]) -> list[CalendarEntry]:
        ordered_ids = self._load_memo_order_ids()
        by_id = {int(entry.entry_id): entry for entry in items if entry.entry_id is not None}
        result: list[CalendarEntry] = []
        used: set[int] = set()
        for memo_id in ordered_ids:
            entry = by_id.get(int(memo_id))
            if entry is None:
                continue
            result.append(entry)
            used.add(int(memo_id))
        for entry in items:
            if entry.entry_id is None:
                continue
            entry_id = int(entry.entry_id)
            if entry_id in used:
                continue
            result.append(entry)
            used.add(entry_id)
        return result

    def _on_memo_card_reordered(self, source_id: int, target_id: int, before: bool) -> None:
        items = self._ordered_memos(self.repository.list_memos())
        ids = [int(entry.entry_id) for entry in items if entry.entry_id is not None]
        if source_id not in ids or target_id not in ids:
            return
        ids.remove(source_id)
        target_index = ids.index(target_id)
        insert_index = target_index if before else target_index + 1
        ids.insert(insert_index, source_id)
        self._save_memo_order_ids(ids, persist=True)
        if self.sidebar_mode == "memo":
            self._render_sidebar()

    def _select_day_by_date(self, selected_day: date) -> None:
        self.selected_day = selected_day
        self.sidebar_mode = "day"
        self.refresh()

    def _open_add_for_day(self, selected_day: date) -> None:
        self.selected_day = selected_day
        self.sidebar_mode = "day"
        self.refresh()
        self._edit_entry(EntryType.SCHEDULE, None)

    def _move_calendar_entry(self, entry_id: int, source_day: date, target_day: date) -> bool:
        if target_day == source_day:
            return False
        entry = self.repository.get_entry(int(entry_id))
        if entry is None:
            return False
        if entry.entry_type == EntryType.MEMO:
            return False
        if entry.recurrence_enabled or (entry.source_entry_id is not None and entry.source_entry_id != entry.entry_id):
            QMessageBox.information(self, "안내", "반복 일정은 아직 드래그 이동을 지원하지 않습니다.")
            return False

        delta_days = (target_day - source_day).days
        if delta_days == 0:
            return False

        if entry.day is not None:
            entry.day = entry.day + timedelta(days=delta_days)
        if entry.start_date is not None:
            entry.start_date = entry.start_date + timedelta(days=delta_days)
        if entry.end_date is not None:
            entry.end_date = entry.end_date + timedelta(days=delta_days)

        self.repository.upsert_entry(entry)
        self.repository.save()
        self.selected_day = target_day
        self.sidebar_mode = "day"
        self.refresh()
        return True

    def _change_month(self, delta: int) -> None:
        month = self.current_month + delta
        year = self.current_year
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        self.current_year = year
        self.current_month = month
        self.selected_day = date(year, month, 1)
        self.refresh()

    def _change_year(self, delta: int) -> None:
        year = self.current_year + delta
        self.current_year = year
        self.selected_day = date(year, self.current_month, 1)
        self.refresh()

    def _is_calendar_wheel_target(self, widget: QObject | None) -> bool:
        current = widget
        while current is not None:
            if current is self.calendar_grid_widget:
                return True
            current = current.parent()
        return False

    def _show_year_menu(self) -> None:
        menu = QMenu(self)
        for year in range(self.current_year - 3, self.current_year + 3):
            action = QAction(f"{year}년", self)
            action.triggered.connect(lambda checked=False, y=year: self._set_year(y))
            menu.addAction(action)
        menu.exec(self.year_button.mapToGlobal(QPoint(0, self.year_button.height())))

    def _show_month_menu(self) -> None:
        menu = QMenu(self)
        for month in range(1, 13):
            action = QAction(f"{month}월", self)
            action.triggered.connect(lambda checked=False, m=month: self._set_month(m))
            menu.addAction(action)
        menu.exec(self.month_button.mapToGlobal(QPoint(0, self.month_button.height())))

    def _set_year(self, year: int) -> None:
        self.current_year = year
        self.selected_day = date(self.current_year, self.current_month, 1)
        self.refresh()

    def _set_month(self, month: int) -> None:
        self.current_month = month
        self.selected_day = date(self.current_year, self.current_month, 1)
        self.refresh()

    def _go_today(self) -> None:
        today = date.today()
        self.current_year = today.year
        self.current_month = today.month
        self.selected_day = today
        self.sidebar_mode = "day"
        self.refresh()

    def _open_settings(self) -> None:
        current_auto_start = is_startup_enabled()
        self.repository.set_setting("auto_start", "1" if current_auto_start else "0")
        dialog = SettingsDialog(
            self,
            self.theme_name,
            self.repository.get_setting("toggle_shortcut", default_shortcut()),
            current_auto_start,
            self._sticker_animation_enabled,
            self.hide_completed_on_calendar,
            self.repository.get_setting("auto_backup_enabled", "1") != "0",
            int(self.repository.get_setting("auto_backup_interval_days", "1")),
            int(self.repository.get_setting("auto_backup_keep_count", "5")),
            self.repository.db_path,
        )
        if dialog.exec() and dialog.result is not None:
            action = str(dialog.result.get("action", "apply"))
            if action == "export_data":
                self._export_data_flow()
                return
            if action == "import_data":
                self._import_data_flow()
                return
            if action == "restore_auto_backup":
                self._restore_auto_backup_flow()
                return
            if action == "reload_holidays":
                self._holidays_fixed, self._holidays_yearly = self._load_holidays()
                self.refresh()
                return
            new_shortcut = str(dialog.result["shortcut"])
            if self.hotkey_manager is not None and not self.hotkey_manager.update_shortcut(new_shortcut):
                QMessageBox.warning(self, "단축키 오류", "해당 단축키를 다른 프로그램에서 사용 중이오니, 다른 단축키로 변경해 주세요.")
                return
            self.theme_name = str(dialog.result["theme"])
            self.repository.set_setting("theme", self.theme_name)
            self.repository.set_setting("toggle_shortcut", new_shortcut)
            requested_auto_start = bool(dialog.result["auto_start"])
            applied = set_startup_enabled(requested_auto_start)
            current_auto_start = is_startup_enabled()
            self.repository.set_setting("auto_start", "1" if current_auto_start else "0")
            self._sticker_animation_enabled = bool(dialog.result.get("sticker_animation_enabled", True))
            self.repository.set_setting("sticker_animation_enabled", "1" if self._sticker_animation_enabled else "0")
            self.hide_completed_on_calendar = bool(dialog.result.get("hide_completed_on_calendar", True))
            self.repository.set_setting("hide_completed_on_calendar", "1" if self.hide_completed_on_calendar else "0")
            
            auto_bk_enabled = bool(dialog.result.get("auto_backup_enabled", True))
            self.repository.set_setting("auto_backup_enabled", "1" if auto_bk_enabled else "0")
            self.repository.set_setting("auto_backup_interval_days", str(dialog.result.get("auto_backup_interval_days", 1)))
            self.repository.set_setting("auto_backup_keep_count", str(dialog.result.get("auto_backup_keep_count", 5)))

            if not self._sticker_animation_enabled:
                self._sticker_animation_state.clear()
            self.repository.save()
            if not applied or current_auto_start != requested_auto_start:
                QMessageBox.warning(
                    self,
                    "자동 시작 설정",
                    "윈도우 자동 시작 설정 적용에 실패했습니다.\n보안 정책 또는 권한을 확인해 주세요.",
                )
            self._holidays_fixed, self._holidays_yearly = self._load_holidays()
            self.refresh()

    def _edit_entry(self, entry_type: EntryType, entry: CalendarEntry | None) -> None:
        try:
            if entry_type == EntryType.TASK:
                entry_type = EntryType.SCHEDULE
            edit_entry = entry
            if entry and entry.source_entry_id and entry.source_entry_id != entry.entry_id:
                edit_entry = self.repository.get_entry(entry.source_entry_id)
            dialog = EntryDialog(self, entry_type, self.selected_day, edit_entry)
            if not dialog.exec() or dialog.result is None:
                return
            if edit_entry and edit_entry.entry_id:
                dialog.result.entry_id = edit_entry.entry_id
            saved = self.repository.upsert_entry(dialog.result)
            if entry_type == EntryType.MEMO and saved.entry_id is not None:
                ids = [int(entry.entry_id) for entry in self._ordered_memos(self.repository.list_memos()) if entry.entry_id is not None]
                if int(saved.entry_id) not in ids:
                    ids.insert(0, int(saved.entry_id))
                self._save_memo_order_ids(ids, persist=False)
            self.repository.save()
            self.refresh()
        except Exception as exc:  # pragma: no cover
            logger.exception("edit_entry failed")
            QMessageBox.critical(self, "오류", f"일정 처리 중 오류가 발생했습니다.\n{exc}")

    def _open_entry_view(self, entry: CalendarEntry) -> None:
        try:
            view_entry = entry
            if entry.source_entry_id and entry.source_entry_id != entry.entry_id:
                source = self.repository.get_entry(entry.source_entry_id)
                if source is not None:
                    view_entry = source
            entry_type = EntryType.SCHEDULE if view_entry.entry_type == EntryType.TASK else view_entry.entry_type
            dialog = EntryViewDialog(
                self,
                entry_type,
                view_entry,
                on_download_attachment=self._download_attachment,
                on_edit_entry=lambda e: self._edit_entry(e.entry_type, e),
            )
            dialog.exec()
        except Exception as exc:  # pragma: no cover
            logger.exception("open_entry_view failed")
            QMessageBox.critical(self, "오류", f"보기 창을 여는 중 오류가 발생했습니다.\n{exc}")

    def _delete_entry(self, entry: CalendarEntry) -> None:
        target_id = entry.source_entry_id or entry.entry_id
        if not target_id:
            return
        if QMessageBox.question(self, "삭제 확인", f"'{entry.title}' 항목을 삭제할까요?") != QMessageBox.Yes:
            return
        self.repository.delete_entry(target_id)
        if entry.entry_type == EntryType.MEMO:
            ids = [memo_id for memo_id in self._load_memo_order_ids() if memo_id != int(target_id)]
            self._save_memo_order_ids(ids, persist=False)
        self.repository.save()
        self.refresh()

    def _toggle_complete(self, entry: CalendarEntry) -> None:
        target_id = entry.source_entry_id or entry.entry_id
        if not target_id:
            return
        base_entry = self.repository.get_entry(target_id)
        if base_entry is None:
            return
        if base_entry.recurrence_enabled and entry.day:
            day_key = entry.day.isoformat()
            completed = set(base_entry.completed_dates)
            if day_key in completed:
                completed.remove(day_key)
            else:
                completed.add(day_key)
            base_entry.completed_dates = sorted(completed)
        else:
            base_entry.status = "" if base_entry.status == "완료" else "완료"
        self.repository.upsert_entry(base_entry)
        self.repository.save()
        self.refresh()

    def _remove_attachment(self, entry: CalendarEntry, stored_path: str) -> None:
        if QMessageBox.question(self, "첨부 삭제", "선택한 첨부파일을 일정에서 제거할까요?") != QMessageBox.Yes:
            return
        target_id = entry.source_entry_id or entry.entry_id
        if not target_id:
            return
        base_entry = self.repository.get_entry(target_id)
        if base_entry is None:
            return
        base_entry.attachments = [item for item in base_entry.attachments if item != stored_path]
        self.repository.upsert_entry(base_entry)
        self.repository.save()
        self.refresh()

    def _download_attachment(self, stored_path: str) -> None:
        source = self.repository.resolve_attachment_path(stored_path)
        if not source.exists() or not source.is_file():
            QMessageBox.critical(self, "오류", "첨부파일 원본을 찾을 수 없습니다.")
            return
        target, _ = QFileDialog.getSaveFileName(self, "첨부파일 저장", source.name)
        if not target:
            return
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            QMessageBox.critical(self, "오류", f"파일 저장 중 오류가 발생했습니다.\n{exc}")
            return
        QMessageBox.information(self, "완료", "첨부파일을 저장했습니다.")

    def _attachment_display_name(self, stored_path: str) -> str:
        filename = self.repository.resolve_attachment_path(stored_path).name
        parts = filename.split("_", 2)
        if len(parts) == 3 and len(parts[0]) == 6 and len(parts[1]) == 8:
            return parts[2]
        return filename

    @staticmethod
    def _attachment_elided_text(name: str, max_len: int = 20) -> str:
        text = str(name or "")
        if len(text) <= max_len:
            return text
        return text[: max(1, max_len - 3)] + "..."

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._remember_window_state()
        self._enforce_equal_calendar_cells()
        self._schedule_calendar_rerender()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._did_onboarding_check:
            self._did_onboarding_check = True
            QTimer.singleShot(120, self._maybe_show_onboarding_tip)
        if self._did_initial_sticker_sync:
            return
        self._did_initial_sticker_sync = True
        # First-show pass: ensure layout is settled before final sticker scaling/render.
        QTimer.singleShot(0, self._stabilize_first_layout)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self._remember_window_state)
            self._schedule_calendar_rerender()
        QTimer.singleShot(80, self._stabilize_first_layout)

    def _maybe_show_onboarding_tip(self) -> None:
        if self.repository.get_setting("onboarding_seen_v1", "0") == "1":
            return

        self.repository.set_setting("onboarding_seen_v1", "1")
        self.repository.save()

        dialog = QDialog(self)
        dialog.setWindowTitle("처음 사용 안내")
        dialog.setModal(False)
        dialog.setWindowModality(Qt.NonModal)
        dialog.resize(360, 180)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        title = QLabel("[캘린더 프로그램 안내]")
        title.setStyleSheet("font-weight: 700;")
        root.addWidget(title)

        line1 = QLabel("1) 기본 단축키는 F3입니다.")
        line2 = QLabel("2) 오른쪽 위 '꾸미기'에서 스티커를 추가하고 배치할 수 있습니다.")
        for label in (line1, line2):
            label.setWordWrap(True)
            root.addWidget(label)

        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("확인")
        ok.clicked.connect(dialog.close)
        row.addWidget(ok)
        root.addLayout(row)

        dialog.show()

    def _stabilize_first_layout(self) -> None:
        self._enforce_equal_calendar_cells()
        self._sync_sticker_overlay()
        self._capture_band_baseline()
        self._render_stickers()

    def _capture_band_baseline(self, force: bool = False) -> None:
        if self._band_baseline_cell_h is not None and self._band_baseline_cell_w is not None and not force:
            return
        body_w, _, _, body_h = self._sticker_metrics()
        self._band_baseline_cell_w = max(1, body_w // 7)
        self._band_baseline_cell_h = max(1, body_h // 6)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.tray_icon is not None and not self._force_exit:
            self._remember_window_state()
            self._persist_window_state()
            self.repository.save()
            self.hide()
            event.ignore()
            return
        self._remember_window_state()
        self._persist_window_state()
        self.repository.save()
        if self._alert_timer is not None:
            self._alert_timer.stop()
            self._alert_timer = None
        if self._sticker_anim_timer is not None:
            self._sticker_anim_timer.stop()
            self._sticker_anim_timer = None
        for box in list(self._active_alert_boxes):
            try:
                box.close()
            except Exception:
                pass
        self._active_alert_boxes.clear()
        if self.hotkey_manager is not None:
            self.hotkey_manager.stop()
            self.hotkey_manager = None
        if self.tray_icon is not None:
            self.tray_icon.hide()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._sticker_edit_mode and self._selected_sticker_id:
            step = 1
            if event.key() == Qt.Key_Left and self._nudge_selected_sticker(-step, 0):
                event.accept()
                return
            if event.key() == Qt.Key_Right and self._nudge_selected_sticker(step, 0):
                event.accept()
                return
            if event.key() == Qt.Key_Up and self._nudge_selected_sticker(0, -step):
                event.accept()
                return
            if event.key() == Qt.Key_Down and self._nudge_selected_sticker(0, step):
                event.accept()
                return
        super().keyPressEvent(event)

    def _perform_auto_backup(self) -> None:
        try:
            enabled = self.repository.get_setting("auto_backup_enabled", "1") != "0"
            if not enabled:
                return

            interval_days = int(self.repository.get_setting("auto_backup_interval_days", "1"))
            if interval_days <= 0:
                interval_days = 1
            keep_count = int(self.repository.get_setting("auto_backup_keep_count", "5"))
            last_backup = self.repository.get_setting("last_auto_backup_time", "")

            db_path = self.repository.db_path
            backup_dir = db_path.parent / "backups"

            from taskcalendar.backup_io import run_auto_backup_db

            new_stamp = run_auto_backup_db(db_path, backup_dir, interval_days, keep_count, last_backup)
            if new_stamp:
                self.repository.set_setting("last_auto_backup_time", new_stamp)
                self.repository.save()
        except Exception as exc:
            logger.exception("auto_backup execution failed")
