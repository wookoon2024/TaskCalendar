from __future__ import annotations

from datetime import date, datetime, timedelta
import ctypes
import logging
import shutil
from pathlib import Path
from typing import Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

from PySide6.QtCore import QDate, QTime, Qt, QTimer, QRect, QPoint, QSize, QUrl, QEvent, Signal, QMimeData
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextCursor, QPainter, QPen, QColor, QDesktopServices, QCursor, QPixmap, QFont, QTextCharFormat, QDrag
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
    QStyle,
    QStyleOption,
    QStylePainter,
)

from taskcalendar.rich_text_edit import RichTextEdit
from taskcalendar.models import (
    ALERT_OPTIONS,
    COLOR_OPTIONS,
    ICON_OPTIONS,
    RECURRENCE_OPTIONS,
    STICKER_CATEGORIES,
    WEEKDAY_LABELS,
    AlertType,
    CalendarEntry,
    EntryType,
    RecurrenceType,
    THEME_OPTIONS,
    Alarm,
    calculate_next_alarm_trigger,
)
from taskcalendar import APP_VERSION
from taskcalendar.themes import THEME_LABELS
from taskcalendar.qt_styles import dialog_stylesheet, resolve_palette
from taskcalendar.desktop_services import _parse_hotkey, default_shortcut, default_memo_shortcut, normalize_shortcut
from taskcalendar.paths import asset_path, custom_stickers_path
from taskcalendar.lunar import get_lunar_date

MEMO_THEMES = {
    "yellow": {
        "name": "노랑",
        "bg": "#fff7c2",
        "header": "#f5e99f",
        "border": "#d5c880",
        "text": "#2c2c2c",
    },
    "green": {
        "name": "연두",
        "bg": "#daf5cb",
        "header": "#c4e9b0",
        "border": "#a8d98e",
        "text": "#1f3812",
    },
    "pink": {
        "name": "핑크",
        "bg": "#fedce6",
        "header": "#f8c5d3",
        "border": "#e8a5b8",
        "text": "#401524",
    },
    "purple": {
        "name": "보라",
        "bg": "#e8dcfe",
        "header": "#d6c3f8",
        "border": "#bca0ea",
        "text": "#281545",
    },
    "blue": {
        "name": "하늘",
        "bg": "#d4effe",
        "header": "#bfe2f8",
        "border": "#9dcfea",
        "text": "#102d42",
    },
    "white": {
        "name": "화이트",
        "bg": "#ffffff",
        "header": "#f0f2f5",
        "border": "#d0d5dd",
        "text": "#222222",
    },
    "dark": {
        "name": "다크",
        "bg": "#2f3136",
        "header": "#202225",
        "border": "#1e1f22",
        "text": "#f2f3f5",
    },
}

ICON_PREVIEW_EMOJI = {
    "anniversary": "🎂",
    "important": "⭐",
    "coffee": "☕",
    "meal": "🍚",
    "meeting": "👥",
}
REPEAT_DETAIL_WIDTH = 210
REPEAT_SPIN_FIELD_WIDTH = 68
REPEAT_DETAIL_HEIGHT = 30
FORM_LABEL_WIDTH = 40


def _dialog_icon() -> QIcon:
    icon_path = asset_path("dialog_icon.svg")
    return QIcon(str(icon_path))


def get_sticker_pixmap(icon_type: str) -> QPixmap | None:
    if not icon_type:
        return None
    file_path: Path | None = None
    if icon_type.startswith("custom:"):
        fname = icon_type[7:]
        file_path = custom_stickers_path(fname)
    elif icon_type.startswith("built_in:"):
        fname = icon_type[9:]
        file_path = asset_path("stickers", fname)
    elif icon_type.endswith(".png"):
        file_path = custom_stickers_path(icon_type)
        if not file_path.exists():
            file_path = asset_path("stickers", icon_type)
    if file_path and file_path.exists():
        return QPixmap(str(file_path))
    return None


def _to_qdate(value: date | None) -> QDate:
    target = value or date.today()
    return QDate(target.year, target.month, target.day)


def _to_qtime(value: str, fallback: str) -> QTime:
    parsed = QTime.fromString(value or fallback, "HH:mm")
    return parsed if parsed.isValid() else QTime.fromString(fallback, "HH:mm")


class OverwriteTimeEdit(QTimeEdit):
    def __init__(self, value: QTime, parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self._typed_section: QDateEdit.Section | None = None
        self._typed_digits = ""

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        QTimer.singleShot(0, self._select_current_section)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        QTimer.singleShot(0, self._select_current_section)

    def stepBy(self, steps: int) -> None:
        self._reset_typed_digits()
        super().stepBy(steps)
        QTimer.singleShot(0, self._select_current_section)

    def keyPressEvent(self, event) -> None:
        text = event.text()
        if text.isdigit() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
            if self._handle_digit(text):
                return
        self._reset_typed_digits()
        super().keyPressEvent(event)
        QTimer.singleShot(0, self._select_current_section)

    def _handle_digit(self, digit: str) -> bool:
        section = self.currentSection()
        if section not in (QDateEdit.Section.HourSection, QDateEdit.Section.MinuteSection):
            return False

        max_value = 23 if section == QDateEdit.Section.HourSection else 59
        max_tens = 2 if section == QDateEdit.Section.HourSection else 5

        if self._typed_section != section:
            self._typed_digits = ""
        self._typed_section = section

        if not self._typed_digits:
            typed_value = int(digit)
            self._set_section_value(section, typed_value)
            if typed_value > max_tens:
                self._advance_to_next_section(section)
            else:
                self._typed_digits = digit
                self._select_current_section()
            return True

        candidate = int(f"{self._typed_digits}{digit}")
        self._typed_digits = ""
        if candidate <= max_value:
            self._set_section_value(section, candidate)
            self._advance_to_next_section(section)
            return True

        self._set_section_value(section, int(digit))
        if int(digit) > max_tens:
            self._advance_to_next_section(section)
        else:
            self._typed_digits = digit
            self._select_current_section()
        return True

    def _set_section_value(self, section: QDateEdit.Section, value: int) -> None:
        current = self.time()
        if section == QDateEdit.Section.HourSection:
            current.setHMS(value, current.minute(), 0)
        else:
            current.setHMS(current.hour(), value, 0)
        self.setTime(current)

    def _advance_to_next_section(self, section: QDateEdit.Section) -> None:
        self._reset_typed_digits()
        next_section = QDateEdit.Section.MinuteSection if section == QDateEdit.Section.HourSection else section
        self.setCurrentSection(next_section)
        self._select_section(next_section)

    def _select_current_section(self) -> None:
        self._select_section(self.currentSection())

    def _select_section(self, section: QDateEdit.Section) -> None:
        line_edit = self.lineEdit()
        if line_edit is None:
            return
        if section == QDateEdit.Section.HourSection:
            line_edit.setSelection(0, 2)
        elif section == QDateEdit.Section.MinuteSection:
            line_edit.setSelection(3, 2)

    def _reset_typed_digits(self) -> None:
        self._typed_section = None
        self._typed_digits = ""


class OverwriteDateEdit(QDateEdit):
    def __init__(self, value: QDate, parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self._typed_section: QDateEdit.Section | None = None
        self._typed_digits = ""
        self._raw_text = value.toString("yyyy-MM-dd")

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._sync_raw_text()
        QTimer.singleShot(0, self._select_current_section)

    def focusOutEvent(self, event) -> None:
        self._commit_raw_text()
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._sync_raw_text()
        QTimer.singleShot(0, self._select_current_section)

    def wheelEvent(self, event) -> None:
        # Prevent accidental year/month/day jumps from touchpad or mouse wheel.
        event.ignore()

    def stepBy(self, steps: int) -> None:
        self._reset_typed_digits()
        super().stepBy(steps)
        self._sync_raw_text()
        QTimer.singleShot(0, self._select_current_section)

    def keyPressEvent(self, event) -> None:
        text = event.text()
        if text.isdigit() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)):
            if self._handle_digit(text):
                return
        self._reset_typed_digits()
        super().keyPressEvent(event)
        QTimer.singleShot(0, self._select_current_section)

    def _handle_digit(self, digit: str) -> bool:
        section = self.currentSection()
        if section == QDateEdit.Section.YearSection:
            return self._handle_year_digit(digit)
        if section in (QDateEdit.Section.MonthSection, QDateEdit.Section.DaySection):
            return self._handle_two_digit_section(section, digit)
        return False

    def _handle_year_digit(self, digit: str) -> bool:
        if self._typed_section != QDateEdit.Section.YearSection:
            self._typed_digits = ""
        self._typed_section = QDateEdit.Section.YearSection
        self._sync_raw_text()
        index = len(self._typed_digits)
        if index >= 4:
            self._typed_digits = ""
            index = 0
        self._replace_range(0 + index, 1, digit)
        self._typed_digits += digit
        if len(self._typed_digits) < 4:
            self._select_year_slot(len(self._typed_digits))
            return True
        self._commit_raw_text()
        self._advance_to_next_section(QDateEdit.Section.YearSection)
        return True

    def _handle_two_digit_section(self, section: QDateEdit.Section, digit: str) -> bool:
        max_value = 12 if section == QDateEdit.Section.MonthSection else 31
        max_tens = 1 if section == QDateEdit.Section.MonthSection else 3
        start = 5 if section == QDateEdit.Section.MonthSection else 8

        if self._typed_section != section:
            self._typed_digits = ""
        self._typed_section = section
        self._sync_raw_text()

        if not self._typed_digits:
            typed_value = int(digit)
            if typed_value > max_tens:
                self._replace_range(start, 2, f"{typed_value:02d}")
                self._commit_raw_text()
                self._advance_to_next_section(section)
            else:
                self._replace_range(start, 2, f"{typed_value:02d}")
                self._typed_digits = digit
                if typed_value == 0:
                    self._select_partial_slot(section, 1)
                else:
                    self._select_partial_slot(section, 1)
            return True

        candidate = int(f"{self._typed_digits}{digit}")
        self._typed_digits = ""
        if 1 <= candidate <= max_value:
            self._replace_range(start, 2, f"{candidate:02d}")
            self._commit_raw_text()
            self._advance_to_next_section(section)
            return True

        if int(digit) > max_tens:
            self._replace_range(start, 2, f"{int(digit):02d}")
            self._commit_raw_text()
            self._advance_to_next_section(section)
        else:
            self._replace_range(start, 2, f"{int(digit):02d}")
            self._typed_digits = digit
            self._select_partial_slot(section, 1)
        return True

    def _advance_to_next_section(self, section: QDateEdit.Section) -> None:
        self._commit_raw_text()
        self._reset_typed_digits()
        next_section = section
        if section == QDateEdit.Section.YearSection:
            next_section = QDateEdit.Section.MonthSection
        elif section == QDateEdit.Section.MonthSection:
            next_section = QDateEdit.Section.DaySection
        self.setCurrentSection(next_section)
        self._select_section(next_section)

    def _select_current_section(self) -> None:
        self._select_section(self.currentSection())

    def _select_section(self, section: QDateEdit.Section) -> None:
        line_edit = self.lineEdit()
        if line_edit is None:
            return
        if section == QDateEdit.Section.YearSection:
            line_edit.setSelection(0, 4)
        elif section == QDateEdit.Section.MonthSection:
            line_edit.setSelection(5, 2)
        elif section == QDateEdit.Section.DaySection:
            line_edit.setSelection(8, 2)

    def _reset_typed_digits(self) -> None:
        self._typed_section = None
        self._typed_digits = ""

    def _select_year_slot(self, index: int) -> None:
        line_edit = self.lineEdit()
        if line_edit is None:
            return
        line_edit.setSelection(index, 1)

    def _select_partial_slot(self, section: QDateEdit.Section, index: int) -> None:
        line_edit = self.lineEdit()
        if line_edit is None:
            return
        start = 5 if section == QDateEdit.Section.MonthSection else 8
        line_edit.setSelection(start + index, 1)

    def _replace_range(self, start: int, length: int, text: str) -> None:
        base = self._raw_text
        self._raw_text = f"{base[:start]}{text}{base[start + length:]}"
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setText(self._raw_text)

    def _sync_raw_text(self) -> None:
        self._raw_text = self.text() or self.date().toString("yyyy-MM-dd")

    def _commit_raw_text(self) -> None:
        self._sync_raw_text()
        try:
            year = max(100, min(9999, int(self._raw_text[0:4])))
            month = max(1, min(12, int(self._raw_text[5:7])))
            max_day = QDate(year, month, 1).daysInMonth()
            day = max(1, min(max_day, int(self._raw_text[8:10])))
        except ValueError:
            self._raw_text = self.date().toString("yyyy-MM-dd")
            return
        committed = QDate(year, month, day)
        self.setDate(committed)
        self._raw_text = committed.toString("yyyy-MM-dd")


def snap_window_rect(current_geo: QRect, other_geos: list[QRect], screen_geo: QRect, threshold: int = 16) -> QPoint:
    x = current_geo.x()
    y = current_geo.y()
    w = current_geo.width()
    h = current_geo.height()
    
    screen_left = screen_geo.x()
    screen_top = screen_geo.y()
    screen_right = screen_geo.x() + screen_geo.width()
    screen_bottom = screen_geo.y() + screen_geo.height()

    # 1. Screen edge snapping
    if abs(x - screen_left) <= threshold:
        x = screen_left
    elif abs((x + w) - screen_right) <= threshold:
        x = screen_right - w
        
    if abs(y - screen_top) <= threshold:
        y = screen_top
    elif abs((y + h) - screen_bottom) <= threshold:
        y = screen_bottom - h
        
    # 2. Other windows (memos & groups) snapping
    for other in other_geos:
        o_x = other.x()
        o_y = other.y()
        o_w = other.width()
        o_h = other.height()
        o_right = o_x + o_w
        o_bottom = o_y + o_h

        # Horizontal docking & alignment
        if abs((x + w) - o_x) <= threshold:
            x = o_x - w
        elif abs(x - o_right) <= threshold:
            x = o_right
        elif abs(x - o_x) <= threshold:
            x = o_x
        elif abs((x + w) - o_right) <= threshold:
            x = o_right - w
            
        # Vertical docking & alignment
        if abs((y + h) - o_y) <= threshold:
            y = o_y - h
        elif abs(y - o_bottom) <= threshold:
            y = o_bottom
        elif abs(y - o_y) <= threshold:
            y = o_y
        elif abs((y + h) - o_bottom) <= threshold:
            y = o_bottom - h
            
    return QPoint(x, y)


def snap_resize_rect(current_geo: QRect, resize_dir: str, other_geos: list[QRect], screen_geo: QRect, threshold: int = 16) -> QRect:
    x = current_geo.x()
    y = current_geo.y()
    w = current_geo.width()
    h = current_geo.height()
    
    screen_right = screen_geo.x() + screen_geo.width()
    screen_bottom = screen_geo.y() + screen_geo.height()
    screen_left = screen_geo.x()
    screen_top = screen_geo.y()
    
    # 1. Right edge snapping ('r', 'br')
    if resize_dir in ("r", "br"):
        cur_right = x + w
        if abs(cur_right - screen_right) <= threshold:
            w = max(180, screen_right - x)
        else:
            for other in other_geos:
                o_left = other.x()
                o_right = other.x() + other.width()
                if abs(cur_right - o_left) <= threshold:
                    w = max(180, o_left - x)
                    break
                elif abs(cur_right - o_right) <= threshold:
                    w = max(180, o_right - x)
                    break

    # 2. Left edge snapping ('bl', 'l')
    elif resize_dir in ("bl", "l"):
        cur_left = x
        orig_right = x + w
        if abs(cur_left - screen_left) <= threshold:
            x = screen_left
            w = max(180, orig_right - x)
        else:
            for other in other_geos:
                o_left = other.x()
                o_right = other.x() + other.width()
                if abs(cur_left - o_right) <= threshold:
                    x = o_right
                    w = max(180, orig_right - x)
                    break
                elif abs(cur_left - o_left) <= threshold:
                    x = o_left
                    w = max(180, orig_right - x)
                    break

    # 3. Bottom edge snapping ('b', 'br', 'bl')
    if resize_dir in ("b", "br", "bl"):
        cur_bottom = y + h
        if abs(cur_bottom - screen_bottom) <= threshold:
            h = max(150, screen_bottom - y)
        else:
            for other in other_geos:
                o_top = other.y()
                o_bottom = other.y() + other.height()
                if abs(cur_bottom - o_top) <= threshold:
                    h = max(150, o_top - y)
                    break
                elif abs(cur_bottom - o_bottom) <= threshold:
                    h = max(150, o_bottom - y)
                    break

    return QRect(x, y, w, h)


class EditableTitleLineEdit(QLineEdit):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("border: none; background: transparent; font-size: 13px; font-weight: bold; padding: 2px;")
        
    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setReadOnly(False)
            self.setCursor(Qt.CursorShape.IBeamCursor)
            self.selectAll()
            self.setFocus()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def focusInEvent(self, event) -> None:
        if self.isReadOnly():
            self.deselect()
        super().focusInEvent(event)
        
    def focusOutEvent(self, event) -> None:
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.deselect()
        super().focusOutEvent(event)
        dlg = self.window()
        if dlg and hasattr(dlg, "_auto_save_to_db"):
            dlg._auto_save_to_db()
        
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setReadOnly(True)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.deselect()
            self.clearFocus()
            event.accept()
            dlg = self.window()
            if dlg and hasattr(dlg, "_auto_save_to_db"):
                dlg._auto_save_to_db()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.isReadOnly() and event.button() == Qt.MouseButton.LeftButton:
            dlg = self.window()
            if dlg and hasattr(dlg, "_start_window_drag"):
                dlg._start_window_drag(event.globalPosition().toPoint())
                event.accept()
                return
        if event.button() == Qt.MouseButton.RightButton:
            dlg = self.window()
            if dlg and hasattr(dlg, "_show_memo_context_menu"):
                dlg._show_memo_context_menu(event.globalPosition().toPoint())
                event.accept()
                return
            elif dlg and hasattr(dlg, "_show_context_menu"):
                dlg._show_context_menu(event.globalPosition().toPoint())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isReadOnly() and event.buttons() == Qt.MouseButton.LeftButton:
            dlg = self.window()
            if dlg and hasattr(dlg, "_perform_window_drag"):
                dlg._perform_window_drag(event.globalPosition().toPoint())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        dlg = self.window()
        if dlg and hasattr(dlg, "_end_window_drag"):
            dlg._end_window_drag()
        super().mouseReleaseEvent(event)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None, is_elastic: bool = False) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._is_elastic = is_elastic
        self.setWordWrap(False)
        self.set_full_text(text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0 if self._is_elastic else 20, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._is_elastic:
            return QSize(0, super().sizeHint().height())
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full_text), super().sizeHint().height())

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = max(0, self.contentsRect().width())
        if width <= 8:
            elided = ""
        else:
            elided = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        if elided != self.text():
            QLabel.setText(self, elided)


class IconPickerPopup(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.setWindowTitle("스티커 선택")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(480, 380)
        self.setStyleSheet(dialog_stylesheet(self.palette))
        self.selected_icon: str | None = None
        self.selected_label: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        header_title = QLabel("🎨 스티커 & 아이콘")
        header_title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {self.palette['text']};")
        header_layout.addWidget(header_title)
        header_layout.addStretch(1)

        none_btn = QPushButton("스티커 제거")
        none_btn.setStyleSheet(f"font-size: 11px; padding: 3px 8px; background: {self.palette['panel_alt']}; color: {self.palette['danger']}; border: 1px solid {self.palette['line']}; border-radius: 4px; font-weight: bold;")
        none_btn.setCursor(Qt.PointingHandCursor)
        none_btn.clicked.connect(self._select_none)
        header_layout.addWidget(none_btn)

        close_btn = QPushButton()
        close_btn.setIcon(QIcon(str(asset_path("memo_close.svg"))))
        close_btn.setIconSize(QSize(11, 11))
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("닫기 (Esc)")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                font-size: 11px;
                font-weight: bold;
                color: {self.palette['muted']};
                background: transparent;
                border: 1px solid {self.palette['line']};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {self.palette['panel_alt']};
                color: {self.palette['text']};
                border-color: {self.palette['muted']};
            }}
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)

        layout.addLayout(header_layout)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(False)

        # 1. 내 스티커 Tab
        self.tabs.addTab(self._build_custom_stickers_tab(), "내 스티커")

        # 2. 기본 스티커 Tab
        self.tabs.addTab(self._build_builtin_stickers_tab(), "기본")

        # 3~6. Emoji Category Tabs (업무, 기념일, 일상, 강조)
        short_names = {
            "업무/일정": "업무",
            "기념일/가족": "기념일",
            "일상/생활": "일상",
            "강조/스티커": "강조",
        }
        for cat_name, items in STICKER_CATEGORIES.items():
            disp_name = short_names.get(cat_name, cat_name)
            self.tabs.addTab(self._build_emoji_tab(items), disp_name)

        layout.addWidget(self.tabs)

    def _build_custom_stickers_tab(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(6)

        top_row = QHBoxLayout()
        add_btn = QPushButton("➕ PNG 스티커 등록...")
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"font-size: 11px; font-weight: bold; padding: 4px 10px; background: {self.palette['accent']}; color: {self.palette['button_text']}; border: none; border-radius: 4px;")
        add_btn.clicked.connect(self._add_custom_png)
        top_row.addWidget(add_btn)

        tip_lbl = QLabel("※ 등록된 스티커 우클릭 시 삭제")
        tip_lbl.setStyleSheet(f"font-size: 10px; color: {self.palette['muted']};")
        top_row.addWidget(tip_lbl)
        top_row.addStretch(1)
        vbox.addLayout(top_row)

        self.custom_scroll = QScrollArea()
        self.custom_scroll.setWidgetResizable(True)
        self.custom_scroll.setStyleSheet("border: none; background: transparent;")
        self.custom_grid_widget = QWidget()
        self.custom_grid = QGridLayout(self.custom_grid_widget)
        self.custom_grid.setContentsMargins(4, 4, 4, 4)
        self.custom_grid.setSpacing(6)
        self.custom_scroll.setWidget(self.custom_grid_widget)
        vbox.addWidget(self.custom_scroll)

        self._refresh_custom_stickers_grid()
        return container

    def _add_custom_png(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "PNG 스티커 선택",
            "",
            "PNG 이미지 (*.png);;모든 이미지 (*.png *.jpg *.jpeg *.webp)"
        )
        if not file_path:
            return
        src = Path(file_path)
        dest_dir = custom_stickers_path()
        dest_dir.mkdir(parents=True, exist_ok=True)
        clean_stem = "".join(c for c in src.stem if c.isalnum() or c in ("_", "-"))[:20] or "sticker"
        dest_file = dest_dir / f"{clean_stem}_{uuid4().hex[:6]}{src.suffix.lower()}"
        try:
            shutil.copy2(src, dest_file)
        except Exception as e:
            QMessageBox.warning(self, "오류", f"스티커 복사 실패: {e}")
            return
        self._refresh_custom_stickers_grid()

    def _refresh_custom_stickers_grid(self) -> None:
        while self.custom_grid.count():
            item = self.custom_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        dest_dir = custom_stickers_path()
        png_files = sorted(dest_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not png_files:
            empty_lbl = QLabel("등록된 스티커가 없습니다.\n상단의 '➕ PNG 스티커 등록...' 버튼으로 추가해 보세요.")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet(f"color: {self.palette['muted']}; font-size: 11px; padding: 25px;")
            self.custom_grid.addWidget(empty_lbl, 0, 0, 1, 6)
            return

        row, col = 0, 0
        for p in png_files:
            btn = QToolButton()
            btn.setProperty("class", "sticker-btn")
            btn.setIcon(QIcon(str(p)))
            btn.setIconSize(QSize(32, 32))
            btn.setToolTip(f"{p.name}\n(좌클릭: 선택 / 우클릭: 삭제)")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, fname=p.name: self._choose(f"custom:{fname}", fname))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pt, file_p=p: self._show_custom_context_menu(pt, file_p))
            self.custom_grid.addWidget(btn, row, col)
            col += 1
            if col >= 6:
                col = 0
                row += 1

    def _show_custom_context_menu(self, point: QPoint, file_p: Path) -> None:
        menu = QMenu(self)
        del_act = menu.addAction("🗑️ 스티커 삭제")
        action = menu.exec(QCursor.pos())
        if action == del_act:
            if QMessageBox.question(self, "스티커 삭제", f"'{file_p.name}' 스티커를 삭제하시겠습니까?") == QMessageBox.Yes:
                try:
                    file_p.unlink(missing_ok=True)
                except Exception:
                    pass
                self._refresh_custom_stickers_grid()

    def _build_builtin_stickers_tab(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 6, 6, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(6)

        sticker_dir = asset_path("stickers")
        builtin_names = [
            ("star.png", "별"),
            ("heart_pink.png", "하트"),
            ("clover.png", "클로버"),
            ("coffee_time.png", "커피"),
            ("food.png", "식사"),
            ("rice_bowl.png", "밥"),
            ("meeting.png", "회의"),
            ("leave_day.png", "휴가"),
            ("medicine_pill.png", "약"),
            ("money.png", "급여"),
            ("car.png", "출장/차"),
            ("cat.png", "고양이"),
            ("bunny.png", "토끼"),
            ("ribbon.png", "리본"),
        ]
        row, col = 0, 0
        for fname, label in builtin_names:
            p = sticker_dir / fname
            if p.exists():
                btn = QToolButton()
                btn.setProperty("class", "sticker-btn")
                btn.setIcon(QIcon(str(p)))
                btn.setIconSize(QSize(32, 32))
                btn.setToolTip(label)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _chk=False, f=fname, l=label: self._choose(f"built_in:{f}", l))
                grid.addWidget(btn, row, col)
                col += 1
                if col >= 6:
                    col = 0
                    row += 1
        scroll.setWidget(grid_widget)
        vbox.addWidget(scroll)
        return container

    def _build_emoji_tab(self, items: list[tuple[str, str]]) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 6, 6, 6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(6)
        row, col = 0, 0
        for emoji, label in items:
            btn = QToolButton()
            btn.setProperty("class", "sticker-btn")
            btn.setText(emoji)
            btn.setToolTip(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, e=emoji, l=label: self._choose(e, l))
            grid.addWidget(btn, row, col)
            col += 1
            if col >= 6:
                col = 0
                row += 1
        scroll.setWidget(grid_widget)
        vbox.addWidget(scroll)
        return container

    def _choose(self, icon_code: str, label: str) -> None:
        self.selected_icon = icon_code
        self.selected_label = label
        self.accept()

    def _select_none(self) -> None:
        self.selected_icon = ""
        self.selected_label = ""
        self.accept()


class EntryDialog(QDialog):
    def __init__(self, parent, entry_type: EntryType, selected_day: date | None, entry: CalendarEntry | None = None, restore_mode: bool = False) -> None:
        self._owner_window = parent
        logger.info(f"[EntryDialog.__init__] entry_type={entry_type}, id={entry.entry_id if entry else None}, title='{entry.title if entry else ''}', restore={restore_mode}")
        if entry_type == EntryType.MEMO:
            super().__init__(None)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
            self.setModal(False)
            self.setAttribute(Qt.WA_StyledBackground, True)
        else:
            super().__init__(parent)
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self.palette = resolve_palette(parent)
        self.entry_type = entry_type
        self.entry = entry
        self.result: CalendarEntry | None = None
        self.attachments = list(entry.attachments if entry else [])

        entry_start = entry.start_date if entry else None
        entry_day = entry.day if entry else None
        base_day = entry_start or entry_day or selected_day or date.today()

        self.setObjectName("entryDialog")
        self.setWindowTitle("메모 등록" if entry_type == EntryType.MEMO else "일정 등록")
        self.setWindowIcon(_dialog_icon())
        self.dialog_width = 700 if entry_type != EntryType.MEMO else 380
        
        if entry_type != EntryType.MEMO:
            self.resize(self.dialog_width, 560)
            self._apply_styles()
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        if self.entry_type == EntryType.MEMO:
            parent = getattr(self, "_owner_window", None) or self.parent()
            repo = getattr(parent, "repository", None) if parent else None

            # 1. Theme Color: if existing entry has bg_color in MEMO_THEMES, use it. Otherwise use default setting.
            if entry and entry.bg_color in MEMO_THEMES:
                self._current_memo_theme = entry.bg_color
            else:
                def_color_setting = repo.get_setting("memo_default_color", "yellow") if repo else "yellow"
                if def_color_setting == "random":
                    import random
                    self._current_memo_theme = random.choice(["yellow", "green", "pink", "purple", "blue"])
                elif def_color_setting in MEMO_THEMES:
                    self._current_memo_theme = def_color_setting
                else:
                    self._current_memo_theme = "yellow"

            # 2. Pin / Floating: if existing entry, check icon_type. If new memo, use default setting.
            if entry and entry.entry_id:
                self._is_floating = (entry.icon_type == "floating")
            else:
                self._is_floating = (repo.get_setting("memo_default_floating", "0") == "1") if repo else False

            self.setMinimumWidth(180)
            self.setMinimumHeight(36)
            self._expanded_width = 380
            self._expanded_height = 360
            self._collapsed_width = None
            self._expand_anchor_right = bool(repo) and repo.get_setting("memo_expand_anchor", "left") == "right"
            self._anchored_to_right = False
            
            # Load remembered geometry, collapse state, and opacity
            has_saved_geo = False
            if entry and entry.entry_id:
                if repo:
                    geo_str = repo.get_setting(f"memo_geo_{entry.entry_id}", "")
                    if geo_str:
                        try:
                            pts = [int(p) for p in geo_str.split(",")]
                            if len(pts) == 4:
                                self._expanded_width = max(180, pts[2])
                                self._expanded_height = max(150, pts[3])
                                self.setGeometry(pts[0], pts[1], self._expanded_width, self._expanded_height)
                                has_saved_geo = True
                        except Exception:
                            pass
                    collapsed_saved = repo.get_setting(f"memo_collapsed_{entry.entry_id}", "0") == "1"
                    self._is_collapsed = collapsed_saved
                    col_w_saved = repo.get_setting(f"memo_collapsed_w_{entry.entry_id}", "")
                    if col_w_saved:
                        try:
                            self._collapsed_width = max(180, int(col_w_saved))
                        except Exception:
                            pass
                    opacity_saved = repo.get_setting(f"memo_opacity_{entry.entry_id}", "")
                    if opacity_saved:
                        try:
                            self.setWindowOpacity(int(opacity_saved) / 100.0)
                        except Exception:
                            pass
            
            if not has_saved_geo:
                # Default opacity setting
                def_op = int(repo.get_setting("memo_default_opacity", "100") if repo else 100)
                try:
                    self.setWindowOpacity(def_op / 100.0)
                except Exception:
                    pass

                # Default size setting
                def_size_str = repo.get_setting("memo_default_size", "380,360") if repo else "380,360"
                try:
                    sw_str, sh_str = def_size_str.split(",")
                    init_w = max(180, int(sw_str))
                    init_h = max(150, int(sh_str))
                except Exception:
                    init_w, init_h = 380, 360

                self._expanded_width = init_w
                self._expanded_height = init_h

                screen = self.screen() or QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    cx = avail.x() + (avail.width() - init_w) // 2
                    cy = avail.y() + (avail.height() - init_h) // 2
                    self.setGeometry(cx, cy, init_w, init_h)
                else:
                    self.resize(init_w, init_h)
            else:
                screen = self.screen() or QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    geo = self.geometry()
                    nx = max(avail.left(), min(geo.x(), avail.right() - 100))
                    ny = max(avail.top(), min(geo.y(), avail.bottom() - 36))
                    self.move(nx, ny)

            self.setMouseTracking(True)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            # Header bar with integrated title field
            self.header = QWidget()
            self.header.setFixedHeight(36)
            self.header.setMouseTracking(True)
            self.header.installEventFilter(self)
            h_layout = QHBoxLayout(self.header)
            h_layout.setContentsMargins(8, 0, 6, 0)
            h_layout.setSpacing(4)
            h_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            self.title_input = EditableTitleLineEdit(entry.title if entry else "", self.header)
            self.title_input.setPlaceholderText("메모 제목")
            self._update_title_input_width()
            self.title_input.textChanged.connect(self._update_title_input_width)
            h_layout.addWidget(self.title_input)

            if entry and entry.created_at:
                self.header.setToolTip(f"등록일: {entry.created_at.strftime('%Y.%m.%d %H:%M')}")

            h_layout.addStretch(1)

            # Opacity slider widget in header (to the left of - X buttons)
            from PySide6.QtWidgets import QSlider
            self._opacity_bar = QWidget(self.header)
            self._opacity_bar.setFixedHeight(26)
            self._opacity_bar.setStyleSheet("background: transparent;")
            op_layout = QHBoxLayout(self._opacity_bar)
            op_layout.setContentsMargins(0, 0, 2, 0)
            op_layout.setSpacing(4)
            
            self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
            self._opacity_slider.setRange(20, 100)
            self._opacity_slider.setFixedWidth(65)
            self._opacity_slider.setFixedHeight(14)
            self._opacity_slider.setToolTip("투명도 조절")
            cur_op_int = int(round(self.windowOpacity() * 100))
            self._opacity_slider.setValue(cur_op_int)
            self._opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
            op_layout.addWidget(self._opacity_slider)
            
            self._opacity_val_label = QLabel(f"{cur_op_int}%")
            self._opacity_val_label.setStyleSheet("font-size: 10px; font-weight: bold; background: transparent; color: #444444; min-width: 28px;")
            op_layout.addWidget(self._opacity_val_label)
            
            self._opacity_bar.hide()
            h_layout.addWidget(self._opacity_bar)

            self._pin_btn = QPushButton()
            self._pin_btn.setIconSize(QSize(13, 13))
            self._pin_btn.setFixedSize(22, 22)
            self._pin_btn.setCursor(Qt.PointingHandCursor)
            self._pin_btn.clicked.connect(self._toggle_pin_memo)
            self._update_pin_btn()
            h_layout.addWidget(self._pin_btn)

            self._collapse_btn = QPushButton()
            self._collapse_btn.setIcon(QIcon(str(asset_path("memo_minimize.svg"))))
            self._collapse_btn.setIconSize(QSize(12, 12))
            self._collapse_btn.setFixedSize(22, 22)
            self._collapse_btn.setCursor(Qt.PointingHandCursor)
            self._collapse_btn.setToolTip("메모 접기 / 펼치기")
            self._collapse_btn.clicked.connect(self._toggle_collapse)
            h_layout.addWidget(self._collapse_btn)

            self._close_btn = QPushButton()
            self._close_btn.setIcon(QIcon(str(asset_path("memo_close.svg"))))
            self._close_btn.setIconSize(QSize(12, 12))
            self._close_btn.setFixedSize(22, 22)
            self._close_btn.setCursor(Qt.PointingHandCursor)
            self._close_btn.setToolTip("닫기 (자동 저장)")
            self._close_btn.clicked.connect(self._close_memo)
            h_layout.addWidget(self._close_btn)

            root.addWidget(self.header)

            # Content container
            self.content_wrap = QWidget()
            self.content_wrap.setMouseTracking(True)
            self.content_wrap.installEventFilter(self)
            content_layout = QVBoxLayout(self.content_wrap)
            content_layout.setContentsMargins(8, 4, 8, 6)
            content_layout.setSpacing(4)

            self._setup_memo_editor_toolbar(content_layout)

            self.description_input = RichTextEdit()
            self.description_input.setPlaceholderText("메모 내용을 입력하세요...")
            self.description_input.setTabChangesFocus(True)
            self.description_input.document().setDocumentMargin(2)
            desc_val = entry.description if entry else ""
            if desc_val.strip().startswith("<") or "<html" in desc_val.lower() or "<p" in desc_val.lower():
                self.description_input.setHtml(desc_val)
            else:
                self.description_input.setPlainText(desc_val)
            if "☐" in desc_val or "☑" in desc_val:
                self.description_input.is_todo_mode = True
            self.description_input.cursorPositionChanged.connect(self._sync_editor_toolbar_state)
            content_layout.addWidget(self.description_input, 1)

            self.attachment_bar = QWidget()
            bottom_row = QHBoxLayout(self.attachment_bar)
            bottom_row.setContentsMargins(0, 0, 0, 0)
            bottom_row.setSpacing(4)

            self.add_image_button = QPushButton()
            self.add_image_button.setIcon(QIcon(str(asset_path("memo_image.svg"))))
            self.add_image_button.setIconSize(QSize(16, 16))
            self.add_image_button.setFixedSize(24, 22)
            self.add_image_button.setToolTip("이미지 추가")
            self.add_image_button.setCursor(Qt.PointingHandCursor)
            self.add_image_button.setStyleSheet("padding: 1px; border: 1px solid rgba(0,0,0,0.12); border-radius: 3px; background: rgba(255,255,255,0.7);")
            self.add_image_button.clicked.connect(self._add_image_from_file)
            bottom_row.addWidget(self.add_image_button)

            self.attach_button = QPushButton()
            self.attach_button.setIcon(QIcon(str(asset_path("memo_attach.svg"))))
            self.attach_button.setIconSize(QSize(15, 15))
            self.attach_button.setFixedSize(24, 22)
            self.attach_button.setToolTip("파일 첨부")
            self.attach_button.setCursor(Qt.PointingHandCursor)
            self.attach_button.setStyleSheet("padding: 1px; border: 1px solid rgba(0,0,0,0.12); border-radius: 3px; background: rgba(255,255,255,0.7);")
            self.attach_button.clicked.connect(self._on_attach_button_clicked)
            bottom_row.addWidget(self.attach_button)

            self.attachments_label = QPushButton(self._attachments_text())
            self.attachments_label.setCursor(Qt.PointingHandCursor)
            self.attachments_label.setStyleSheet("padding: 2px 4px; font-size: 11px; border: none; background: transparent; text-align: left;")
            self.attachments_label.clicked.connect(self._show_attachments_menu)
            bottom_row.addWidget(self.attachments_label, 1)

            content_layout.addWidget(self.attachment_bar)

            # Apply initial attachment bar visibility based on per-memo setting, or fallback to global setting & attachments
            per_memo_attach = repo.get_setting(f"memo_show_attach_{self.entry.entry_id}", "") if (repo and self.entry and self.entry.entry_id) else ""
            if per_memo_attach != "":
                if per_memo_attach == "0":
                    self.attachment_bar.hide()
                else:
                    self.attachment_bar.show()
            else:
                show_attach_setting = (repo.get_setting("memo_show_attachment_bar", "1") != "0") if repo else True
                if not show_attach_setting and not self.attachments:
                    self.attachment_bar.hide()

            # Apply initial default font size
            def_font_size = int(repo.get_setting("memo_default_font_size", "11") if repo else 11)
            self._current_memo_font_size = def_font_size
            desc_font = self.description_input.font()
            desc_font.setPointSize(def_font_size)
            self.description_input.setFont(desc_font)
            if hasattr(self, "size_combo"):
                s_idx = self.size_combo.findData(def_font_size)
                if s_idx >= 0:
                    self.size_combo.blockSignals(True)
                    self.size_combo.setCurrentIndex(s_idx)
                    self.size_combo.blockSignals(False)

            root.addWidget(self.content_wrap, 1)

            # Apply initial collapse state if remembered
            if getattr(self, "_is_collapsed", False):
                self.content_wrap.hide()
                target_w = self._collapsed_width if self._collapsed_width is not None else self._expanded_width
                self._resize_with_anchor(target_w, 36)
                self.setFixedHeight(36)
                if hasattr(self, "_collapse_btn") and self._collapse_btn is not None:
                    self._collapse_btn.setIcon(QIcon(str(asset_path("memo_maximize.svg"))))
                    self._collapse_btn.setToolTip("메모 펼치기")

            # Apply initial theme colors & topmost
            self._apply_memo_theme(self._current_memo_theme)
            if self._is_floating:
                self._set_topmost_native(True)

            # Auto-save triggers
            self.title_input.textChanged.connect(self._trigger_auto_save)
            self.description_input.textChanged.connect(self._trigger_auto_save)
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.timeout.connect(self._auto_save_to_db)

            # If new memo, auto-focus title
            if entry is None or entry.entry_id is None:
                QTimer.singleShot(50, self._focus_title_for_new_memo)

            self._resize_dir = None
            return

        if self.entry_type != EntryType.MEMO:
            repeat_card, repeat_layout = self._create_card(soft=True)
            repeat_row = QHBoxLayout()
            repeat_row.setContentsMargins(0, 0, 0, 0)
            repeat_row.setSpacing(8)
            repeat_label = QLabel("반복")
            repeat_label.setObjectName("muted")
            repeat_row.addWidget(repeat_label)
            self.repeat_check = QCheckBox("반복")
            self.repeat_check.setChecked(entry.recurrence_enabled if entry else False)
            self.repeat_check.toggled.connect(self._toggle_repeat)
            repeat_row.addWidget(self.repeat_check)
            repeat_row.addStretch(1)
            repeat_layout.addLayout(repeat_row)
            root.addWidget(repeat_card)

            self.repeat_panel, repeat_form = self._create_grid_card()
            repeat_form.setColumnMinimumWidth(0, 40)
            repeat_form.setColumnStretch(3, 1)

            repeat_form.addWidget(self._section_title("반복 설정"), 0, 0, 1, 4)
            repeat_cycle_label = self._muted("주기")
            repeat_cycle_label.setFixedWidth(FORM_LABEL_WIDTH)
            repeat_cycle_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            repeat_form.addWidget(repeat_cycle_label, 1, 0)
            self.recurrence_combo = QComboBox()
            for label, value in RECURRENCE_OPTIONS[1:]:
                self.recurrence_combo.addItem(label, value.value)
            recurrence_value = entry.recurrence_type.value if entry and entry.recurrence_enabled else RecurrenceType.DAILY.value
            self.recurrence_combo.setCurrentIndex(max(0, self.recurrence_combo.findData(recurrence_value)))
            self.recurrence_combo.currentIndexChanged.connect(self._refresh_repeat_details)
            self.recurrence_combo.setMinimumWidth(124)
            self.recurrence_combo.setMaximumWidth(156)
            self.recurrence_combo.setMinimumHeight(30)
            self.recurrence_combo.setMaximumHeight(30)
            repeat_form.addWidget(self.recurrence_combo, 1, 1)

            self.interval_wrap = QWidget()
            interval_layout = QHBoxLayout(self.interval_wrap)
            interval_layout.setContentsMargins(0, 0, 0, 0)
            interval_layout.setSpacing(6)
            interval_layout.addWidget(self._muted("간격"))
            self.recurrence_interval = QSpinBox()
            self.recurrence_interval.setRange(1, 365)
            self.recurrence_interval.setValue(entry.recurrence_interval if entry else 1)
            self.recurrence_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.recurrence_interval.setMinimumWidth(44)
            self.recurrence_interval.setMaximumWidth(44)
            self.recurrence_interval.setMinimumHeight(30)
            self._syncing_interval = False
            self.recurrence_interval.valueChanged.connect(self._sync_recurrence_interval_from_daily)
            self.recurrence_interval_field = self._step_field(self.recurrence_interval, 20)
            self.recurrence_interval_field.setFixedWidth(REPEAT_SPIN_FIELD_WIDTH)
            interval_layout.addWidget(self.recurrence_interval_field)
            interval_layout.addWidget(self._muted("일마다"))
            self.interval_wrap.setMinimumHeight(30)
            self.interval_wrap.setMaximumHeight(REPEAT_DETAIL_HEIGHT)
            self.interval_wrap.setMinimumWidth(REPEAT_DETAIL_WIDTH)
            self.interval_wrap.setMaximumWidth(REPEAT_DETAIL_WIDTH)
            repeat_form.addWidget(self.interval_wrap, 1, 2)

            detail_slot = QWidget()
            detail_layout = QHBoxLayout(detail_slot)
            detail_layout.setContentsMargins(0, 0, 0, 0)
            detail_layout.setSpacing(8)
            detail_layout.addStretch(1)
            detail_slot.setMinimumHeight(REPEAT_DETAIL_HEIGHT)
            detail_slot.setMaximumHeight(REPEAT_DETAIL_HEIGHT)

            self.recurrence_summary = QLabel("")
            self.recurrence_summary.setObjectName("hint")
            self.recurrence_summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.recurrence_summary.setMinimumHeight(REPEAT_DETAIL_HEIGHT)
            self.recurrence_summary.setMaximumHeight(REPEAT_DETAIL_HEIGHT)
            detail_layout.addWidget(self.recurrence_summary)

            self.weekday_wrap = QWidget()
            weekday_layout = QHBoxLayout(self.weekday_wrap)
            weekday_layout.setContentsMargins(0, 0, 0, 0)
            weekday_layout.setSpacing(6)
            self.weekday_wrap.setMinimumHeight(REPEAT_DETAIL_HEIGHT)
            self.weekday_wrap.setMaximumHeight(REPEAT_DETAIL_HEIGHT)
            self.weekly_interval = QSpinBox()
            self.weekly_interval.setRange(1, 52)
            self.weekly_interval.setValue(self.recurrence_interval.value())
            self.weekly_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.weekly_interval.setMinimumWidth(44)
            self.weekly_interval.setMaximumWidth(44)
            self.weekly_interval.setMinimumHeight(30)
            self.weekly_interval.valueChanged.connect(self._sync_recurrence_interval_from_weekly)
            self.weekly_interval_field = self._step_field(self.weekly_interval, 20)
            self.weekly_interval_field.setFixedWidth(REPEAT_SPIN_FIELD_WIDTH)
            weekday_layout.addWidget(self.weekly_interval_field)
            weekday_layout.addWidget(self._muted("주마다"))
            selected_weekdays = set(entry.recurrence_weekdays if entry else [])
            self.weekday_checks: list[QCheckBox] = []
            for idx, label in enumerate(WEEKDAY_LABELS):
                checkbox = QCheckBox(label)
                checkbox.setChecked(idx in selected_weekdays)
                checkbox.toggled.connect(self._refresh_repeat_details)
                self.weekday_checks.append(checkbox)
                weekday_layout.addWidget(checkbox)
            detail_layout.addWidget(self.weekday_wrap)
            repeat_form.addWidget(detail_slot, 1, 3)

            self.month_day_wrap = QWidget()
            month_day_layout = QHBoxLayout(self.month_day_wrap)
            month_day_layout.setContentsMargins(0, 0, 0, 0)
            month_day_layout.setSpacing(6)
            month_day_layout.addWidget(self._muted("매월"))
            self.recurrence_month_day = QSpinBox()
            self.recurrence_month_day.setRange(1, 31)
            self.recurrence_month_day.setValue(entry.recurrence_month_day if entry else base_day.day)
            self.recurrence_month_day.valueChanged.connect(self._refresh_repeat_details)
            self.recurrence_month_day.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.recurrence_month_day.setMinimumWidth(44)
            self.recurrence_month_day.setMaximumWidth(44)
            self.recurrence_month_day.setMinimumHeight(30)
            self.recurrence_month_day_field = self._step_field(self.recurrence_month_day, 20)
            self.recurrence_month_day_field.setFixedWidth(REPEAT_SPIN_FIELD_WIDTH)
            month_day_layout.addWidget(self.recurrence_month_day_field)
            month_day_layout.addWidget(self._muted("일"))
            self.recurrence_month_end_check = QCheckBox("말일")
            self.recurrence_month_end_check.setChecked(entry.recurrence_month_end if entry else False)
            self.recurrence_month_end_check.toggled.connect(self._toggle_month_end)
            self.recurrence_month_end_check.toggled.connect(self._refresh_repeat_details)
            month_day_layout.addWidget(self.recurrence_month_end_check)
            self.month_day_wrap.setMinimumHeight(30)
            self.month_day_wrap.setMaximumHeight(REPEAT_DETAIL_HEIGHT)
            self.month_day_wrap.setMinimumWidth(REPEAT_DETAIL_WIDTH)
            self.month_day_wrap.setMaximumWidth(REPEAT_DETAIL_WIDTH)
            repeat_form.addWidget(self.month_day_wrap, 1, 2)

            self.month_week_wrap = QWidget()
            month_week_layout = QHBoxLayout(self.month_week_wrap)
            month_week_layout.setContentsMargins(0, 0, 0, 0)
            month_week_layout.setSpacing(6)
            month_week_layout.addWidget(self._muted("매월"))
            self.recurrence_month_week_combo = QComboBox()
            self.recurrence_month_week_combo.addItem("첫째", 1)
            self.recurrence_month_week_combo.addItem("둘째", 2)
            self.recurrence_month_week_combo.addItem("셋째", 3)
            self.recurrence_month_week_combo.addItem("넷째", 4)
            self.recurrence_month_week_combo.addItem("마지막", -1)
            selected_week = entry.recurrence_month_week if entry else 1
            self.recurrence_month_week_combo.setCurrentIndex(max(0, self.recurrence_month_week_combo.findData(selected_week)))
            self.recurrence_month_week_combo.currentIndexChanged.connect(self._refresh_repeat_details)
            self.recurrence_month_week_combo.setMinimumWidth(68)
            self.recurrence_month_week_combo.setMaximumWidth(84)
            self.recurrence_month_week_combo.setMinimumHeight(30)
            self.recurrence_month_week_combo.setMaximumHeight(30)
            month_week_layout.addWidget(self.recurrence_month_week_combo)

            self.recurrence_month_weekday_combo = QComboBox()
            for idx, label in enumerate(WEEKDAY_LABELS):
                self.recurrence_month_weekday_combo.addItem(label, idx)
            default_weekday = (base_day.weekday() + 1) % 7
            if entry and entry.recurrence_weekdays:
                default_weekday = int(entry.recurrence_weekdays[0])
            self.recurrence_month_weekday_combo.setCurrentIndex(max(0, self.recurrence_month_weekday_combo.findData(default_weekday)))
            self.recurrence_month_weekday_combo.currentIndexChanged.connect(self._refresh_repeat_details)
            self.recurrence_month_weekday_combo.setMinimumWidth(56)
            self.recurrence_month_weekday_combo.setMaximumWidth(72)
            self.recurrence_month_weekday_combo.setMinimumHeight(30)
            self.recurrence_month_weekday_combo.setMaximumHeight(30)
            month_week_layout.addWidget(self.recurrence_month_weekday_combo)
            self.month_week_wrap.setMinimumHeight(30)
            self.month_week_wrap.setMaximumHeight(REPEAT_DETAIL_HEIGHT)
            self.month_week_wrap.setMinimumWidth(REPEAT_DETAIL_WIDTH)
            self.month_week_wrap.setMaximumWidth(REPEAT_DETAIL_WIDTH)
            repeat_form.addWidget(self.month_week_wrap, 1, 2)
            root.addWidget(self.repeat_panel)

        if self.entry_type != EntryType.MEMO:
            details_card, details_layout = self._create_grid_card()
            details_layout.setColumnMinimumWidth(0, FORM_LABEL_WIDTH)
            details_layout.setColumnStretch(0, 0)
            details_layout.setColumnStretch(1, 1)

            # Row 0: 스티커 & 배경색
            icon_label = self._muted("스티커")
            icon_label.setFixedWidth(FORM_LABEL_WIDTH)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            details_layout.addWidget(icon_label, 0, 0)

            row0 = QWidget()
            row0.setFixedHeight(30)
            row0_layout = QHBoxLayout(row0)
            row0_layout.setContentsMargins(0, 0, 0, 0)
            row0_layout.setSpacing(6)

            self._selected_icon: str = entry.icon_type if entry else ""

            sticker_wrap = QWidget()
            sticker_wrap.setFixedHeight(30)
            sticker_wrap_layout = QHBoxLayout(sticker_wrap)
            sticker_wrap_layout.setContentsMargins(0, 0, 0, 0)
            sticker_wrap_layout.setSpacing(6)

            self.sticker_btn = QPushButton()
            self.sticker_btn.setCursor(Qt.PointingHandCursor)
            self.sticker_btn.setFixedHeight(30)
            self.sticker_btn.clicked.connect(self._open_icon_picker)
            sticker_wrap_layout.addWidget(self.sticker_btn)

            self.sticker_clear_btn = QPushButton("✕")
            self.sticker_clear_btn.setToolTip("스티커 제거")
            self.sticker_clear_btn.setCursor(Qt.PointingHandCursor)
            self.sticker_clear_btn.setFixedSize(24, 30)
            self.sticker_clear_btn.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {self.palette['danger']}; background: {self.palette['panel_alt']}; border: 1px solid {self.palette['line']}; border-radius: 4px;")
            self.sticker_clear_btn.clicked.connect(self._clear_sticker)
            sticker_wrap_layout.addWidget(self.sticker_clear_btn)
            sticker_wrap_layout.addStretch(1)

            self._refresh_sticker_button()
            row0_layout.addWidget(sticker_wrap)

            sticker_wrap.setFixedWidth(145)

            row0_layout.addSpacing(16)

            bg_color_label = self._muted("배경색")
            bg_color_label.setFixedWidth(FORM_LABEL_WIDTH)
            bg_color_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            row0_layout.addWidget(bg_color_label)

            self.bg_color_combo = QComboBox()
            for label, value in COLOR_OPTIONS:
                self.bg_color_combo.addItem(label, value)
            self.bg_color_combo.setCurrentIndex(max(0, self.bg_color_combo.findData(entry.bg_color if entry else "")))
            self.bg_color_combo.setFixedWidth(112)
            self.bg_color_combo.setFixedHeight(30)
            row0_layout.addWidget(self.bg_color_combo)

            row0_layout.addStretch(1)
            details_layout.addWidget(row0, 0, 1)

            # Row 1: 일시
            when_label = self._muted("일시")
            when_label.setFixedWidth(FORM_LABEL_WIDTH)
            when_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            details_layout.addWidget(when_label, 1, 0)

            when_row = QWidget()
            when_row.setFixedHeight(30)
            when_row_layout = QHBoxLayout(when_row)
            when_row_layout.setContentsMargins(0, 0, 0, 0)
            when_row_layout.setSpacing(6)

            self.start_date = OverwriteDateEdit(_to_qdate(base_day))
            self.start_date.setDisplayFormat("yyyy-MM-dd")
            self.start_date.setCalendarPopup(True)
            self.start_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.start_date.setMinimumWidth(125)
            self.start_date.setMaximumWidth(125)
            self.start_date.setFixedHeight(30)
            self.start_date.dateChanged.connect(self._refresh_repeat_details)
            when_row_layout.addWidget(self.start_date)

            self.start_time = OverwriteTimeEdit(_to_qtime(entry.start_time if entry else "", "09:00"))
            self.start_time.setDisplayFormat("HH:mm")
            self.start_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.start_time.setMinimumWidth(65)
            self.start_time.setMaximumWidth(65)
            self.start_time.setFixedHeight(30)
            when_row_layout.addWidget(self._step_field(self.start_time, 20))

            when_row_layout.addWidget(self._muted("~"))

            self.end_date = OverwriteDateEdit(_to_qdate(entry.end_date if entry else base_day))
            self.end_date.setDisplayFormat("yyyy-MM-dd")
            self.end_date.setCalendarPopup(True)
            self.end_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.end_date.setMinimumWidth(125)
            self.end_date.setMaximumWidth(125)
            self.end_date.setFixedHeight(30)
            when_row_layout.addWidget(self.end_date)

            self.end_time = OverwriteTimeEdit(_to_qtime(entry.end_time if entry else "", "18:00"))
            self.end_time.setDisplayFormat("HH:mm")
            self.end_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.end_time.setMinimumWidth(65)
            self.end_time.setMaximumWidth(65)
            self.end_time.setFixedHeight(30)
            when_row_layout.addWidget(self._step_field(self.end_time, 20))

            self.all_day = QCheckBox("종일")
            self.all_day.setChecked(entry.all_day if entry else True)
            self.all_day.toggled.connect(self._toggle_all_day)
            when_row_layout.addWidget(self.all_day)
            when_row_layout.addStretch(1)

            details_layout.addWidget(when_row, 1, 1)

            period_label = self._muted("기간")
            period_label.setFixedWidth(FORM_LABEL_WIDTH)
            period_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            details_layout.addWidget(period_label, 2, 0)

            period_row = QWidget()
            period_row.setFixedHeight(30)
            period_row_layout = QHBoxLayout(period_row)
            period_row_layout.setContentsMargins(0, 0, 0, 0)
            period_row_layout.setSpacing(4)
            period_row_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            btn_defs = [
                ("1일", "1d", 34),
                ("1주일", "1w", 44),
                ("1달", "1m", 34),
                ("3개월", "3m", 44),
                ("6개월", "6m", 44),
                ("1년", "1y", 34),
                ("10년", "10y", 44),
                ("영구", "forever", 40),
            ]
            self.period_buttons: list[QPushButton] = []
            for btn_text, duration_key, btn_w in btn_defs:
                p_btn = QPushButton(btn_text)
                p_btn.setFixedWidth(btn_w)
                p_btn.setFixedHeight(26)
                p_btn.setCursor(Qt.PointingHandCursor)
                p_btn.setToolTip(f"종료일을 시작일 기준 {btn_text} 뒤로 자동 설정")
                if duration_key == "forever":
                    p_btn.setStyleSheet(f"""
                        QPushButton {{
                            min-width: 40px;
                            max-width: 40px;
                            font-size: 11px;
                            font-weight: bold;
                            padding: 2px 2px;
                            background: {self.palette['accent_soft']};
                            color: {self.palette['accent']};
                            border: 1px solid {self.palette['accent']};
                            border-radius: 4px;
                        }}
                        QPushButton:hover {{
                            background: {self.palette['panel_alt']};
                            border-color: {self.palette['accent']};
                            color: {self.palette['accent']};
                        }}
                    """)
                else:
                    p_btn.setStyleSheet(f"""
                        QPushButton {{
                            min-width: {btn_w}px;
                            max-width: {btn_w}px;
                            font-size: 11px;
                            font-weight: 500;
                            padding: 2px 2px;
                            background: {self.palette['panel_alt']};
                            color: {self.palette['text']};
                            border: 1px solid {self.palette['line']};
                            border-radius: 4px;
                        }}
                        QPushButton:hover {{
                            background: {self.palette['accent_soft']};
                            border-color: {self.palette['accent']};
                            color: {self.palette['accent']};
                        }}
                    """)
                p_btn.clicked.connect(lambda _chk=False, dk=duration_key: self._set_period_duration(dk))
                self.period_buttons.append(p_btn)
                period_row_layout.addWidget(p_btn)

            period_row_layout.addStretch(1)

            self.start_lunar_badge = QLabel("")
            self.start_lunar_badge.setFixedHeight(24)
            self.start_lunar_badge.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {self.palette['info']}; background: {self.palette['accent_soft']}; border: 1px solid {self.palette['line']}; border-radius: 4px; padding: 2px 6px;")
            period_row_layout.addWidget(self.start_lunar_badge)

            details_layout.addWidget(period_row, 2, 1)
            root.addWidget(details_card)

        if self.entry_type == EntryType.MEMO:
            memo_card, memo_layout = self._create_grid_card()
            memo_layout.addWidget(self._section_title("메모 정보"), 0, 0, 1, 2)
            memo_layout.addWidget(self._muted("제목"), 1, 0)
            self.title_input = QLineEdit(entry.title if entry else "")
            memo_layout.addWidget(self.title_input, 1, 1)
            root.addWidget(memo_card)

        self.content_card, content_layout = self._create_card()
        self.content_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.description_input = RichTextEdit()
        self.description_input.setPlaceholderText("일정 내용을 입력하세요...")
        desc_val = entry.description if entry else ""
        if desc_val.strip().startswith("<"):
            self.description_input.setHtml(desc_val)
        else:
            self.description_input.setPlainText(desc_val)
        self.description_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        content_layout.addWidget(self.description_input, 1)
        root.addWidget(self.content_card)

        footer_card, footer_layout = self._create_card(soft=True)
        attach_row = QHBoxLayout()
        attach_row.setContentsMargins(0, 0, 0, 0)
        attach_row.setSpacing(8)
        attach_row.addWidget(self._muted("첨부파일"))
        attach_button = QPushButton("파일 선택")
        attach_button.clicked.connect(self._add_attachment)
        attach_row.addWidget(attach_button)
        self.attachments_label = self._muted(self._attachments_text())
        attach_row.addWidget(self.attachments_label)
        attach_row.addStretch(1)
        footer_layout.addLayout(attach_row)

        if self.entry_type != EntryType.MEMO:
            footer_layout.addWidget(self._separator())
            alert_layout = QHBoxLayout()
            alert_layout.setContentsMargins(0, 0, 0, 0)
            alert_layout.setSpacing(10)
            alert_layout.addWidget(self._muted("알림"))
            self.alert_none = QRadioButton("알리지 않음")
            self.alert_popup = QRadioButton("팝업 알림")
            if entry and entry.alert_type == AlertType.POPUP:
                self.alert_popup.setChecked(True)
            else:
                self.alert_none.setChecked(True)
            self.alert_none.toggled.connect(self._toggle_alerts)
            self.alert_popup.toggled.connect(self._toggle_alerts)
            alert_layout.addWidget(self.alert_none)
            alert_layout.addWidget(self.alert_popup)
            alert_layout.addWidget(self._muted("시점"))
            self.alert_offset_combo = QComboBox()
            for label, value in ALERT_OPTIONS:
                self.alert_offset_combo.addItem(label, value)
            self.alert_offset_combo.setCurrentIndex(max(0, self.alert_offset_combo.findData(entry.alert_offset if entry else "at_start")))
            alert_layout.addWidget(self.alert_offset_combo)
            alert_layout.addStretch(1)
            footer_layout.addLayout(alert_layout)
        root.addWidget(footer_card)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.Save)
        if save_button is not None:
            save_button.setObjectName("primary")
            save_button.setText("저장")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setText("취소")
        root.addWidget(buttons)

        if self.entry_type != EntryType.MEMO:
            self._toggle_repeat()
            self._refresh_repeat_details()
            self._toggle_month_end()
            self._toggle_all_day()
            self._toggle_alerts()
            self._sync_description_height()
        self.setFixedWidth(self.dialog_width)
        self._sync_dialog_height()

    def _step_field(self, field: QAbstractSpinBox, button_width: int) -> QWidget:
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(field)

        buttons = QWidget()
        button_layout = QVBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(2)

        up_button = QToolButton()
        up_button.setObjectName("stepButton")
        up_button.setText("▲")
        up_button.setFixedSize(button_width, 14)
        up_button.clicked.connect(field.stepUp)

        down_button = QToolButton()
        down_button.setObjectName("stepButton")
        down_button.setText("▼")
        down_button.setFixedSize(button_width, 14)
        down_button.clicked.connect(field.stepDown)

        button_layout.addWidget(up_button)
        button_layout.addWidget(down_button)
        layout.addWidget(buttons)
        return wrap

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _create_card(self, soft: bool = False) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("softCard" if soft else "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        return frame, layout

    def _create_grid_card(self, soft: bool = False) -> tuple[QFrame, QGridLayout]:
        frame = QFrame()
        frame.setObjectName("softCard" if soft else "card")
        layout = QGridLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)
        return frame, layout

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("separator")
        return line

    def _apply_styles(self) -> None:
        self.setStyleSheet(dialog_stylesheet(self.palette))

    def _attachments_text(self) -> str:
        return f"{len(self.attachments)}개 파일 선택" if self.attachments else "첨부파일 없음"

    def _toggle_repeat(self) -> None:
        self.repeat_panel.setVisible(self.repeat_check.isChecked())
        self._sync_description_height()

    def _sync_description_height(self) -> None:
        if self.entry_type == EntryType.MEMO:
            self.content_card.setMinimumHeight(142)
            self.content_card.setMaximumHeight(142)
            self.description_input.setMinimumHeight(128)
            self.description_input.setMaximumHeight(128)
            return
        if self.repeat_check.isChecked():
            card_height = 118
            text_height = 82
        else:
            card_height = 176
            text_height = 140
        self.content_card.setMinimumHeight(card_height)
        self.content_card.setMaximumHeight(card_height)
        self.description_input.setMinimumHeight(text_height)
        self.description_input.setMaximumHeight(text_height)
        self.layout().activate()
        self._sync_dialog_height()

    def _sync_dialog_height(self) -> None:
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.layout().activate()
        target_height = self.layout().sizeHint().height() + 10
        self.setFixedHeight(target_height)

    def _toggle_all_day(self) -> None:
        self.start_time.setEnabled(not self.all_day.isChecked())
        self.end_time.setEnabled(not self.all_day.isChecked())

    def _toggle_month_end(self) -> None:
        checked = self.recurrence_month_end_check.isChecked()
        self.recurrence_month_day.setEnabled(not checked)
        self.recurrence_month_day_field.setEnabled(not checked)

    def _toggle_alerts(self) -> None:
        self.alert_offset_combo.setEnabled(self.alert_popup.isChecked())

    def _sync_recurrence_interval_from_daily(self, value: int) -> None:
        if self._syncing_interval:
            return
        self._syncing_interval = True
        self.weekly_interval.setValue(max(1, value))
        self._syncing_interval = False
        self._refresh_repeat_details()

    def _open_icon_picker(self) -> None:
        popup = IconPickerPopup(self)
        btn_pos = self.sticker_btn.mapToGlobal(QPoint(0, self.sticker_btn.height() + 2))
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            px = max(avail.left() + 10, min(btn_pos.x(), avail.right() - popup.width() - 10))
            py = max(avail.top() + 10, min(btn_pos.y(), avail.bottom() - popup.height() - 10))
            popup.move(px, py)
        else:
            popup.move(btn_pos)
        if popup.exec() and popup.selected_icon is not None:
            self._selected_icon = popup.selected_icon
            self._refresh_sticker_button()

    def _clear_sticker(self) -> None:
        self._selected_icon = ""
        self._refresh_sticker_button()

    def _refresh_sticker_button(self) -> None:
        icon_val = getattr(self, "_selected_icon", "")
        if not icon_val:
            self.sticker_btn.setIcon(QIcon())
            self.sticker_btn.setText("🎨 스티커 선택...")
            self.sticker_btn.setStyleSheet("text-align: center; padding: 4px 8px; font-size: 11px;")
            self.sticker_btn.setFixedWidth(115)
            self.sticker_btn.setToolTip("스티커 선택")
            self.sticker_clear_btn.hide()
            return
        self.sticker_clear_btn.show()
        pix = get_sticker_pixmap(icon_val)
        if pix and not pix.isNull():
            self.sticker_btn.setIcon(QIcon(pix))
            self.sticker_btn.setIconSize(QSize(20, 20))
            self.sticker_btn.setText("")
            self.sticker_btn.setStyleSheet("text-align: center; padding: 2px 4px;")
            self.sticker_btn.setFixedWidth(42)
            self.sticker_btn.setToolTip(f"스티커 변경 ({icon_val})")
        else:
            self.sticker_btn.setIcon(QIcon())
            self.sticker_btn.setText(f"{icon_val}")
            self.sticker_btn.setStyleSheet("text-align: center; padding: 2px 4px; font-size: 15px;")
            self.sticker_btn.setFixedWidth(42)
            self.sticker_btn.setToolTip(f"스티커 변경 ({icon_val})")

    def _sync_recurrence_interval_from_weekly(self, value: int) -> None:
        if self._syncing_interval:
            return
        self._syncing_interval = True
        self.recurrence_interval.setValue(max(1, value))
        self._syncing_interval = False
        self._refresh_repeat_details()

    def _set_period_duration(self, duration_key: str) -> None:
        import calendar
        start_q = self.start_date.date()
        start = date(start_q.year(), start_q.month(), start_q.day())

        if duration_key == "1d":
            end = start
        elif duration_key == "1w":
            end = start + timedelta(days=7)
        elif duration_key == "1m":
            year = start.year + (start.month + 1 - 1) // 12
            month = (start.month + 1 - 1) % 12 + 1
            max_d = calendar.monthrange(year, month)[1]
            end = date(year, month, min(start.day, max_d))
        elif duration_key == "3m":
            year = start.year + (start.month + 3 - 1) // 12
            month = (start.month + 3 - 1) % 12 + 1
            max_d = calendar.monthrange(year, month)[1]
            end = date(year, month, min(start.day, max_d))
        elif duration_key == "6m":
            year = start.year + (start.month + 6 - 1) // 12
            month = (start.month + 6 - 1) % 12 + 1
            max_d = calendar.monthrange(year, month)[1]
            end = date(year, month, min(start.day, max_d))
        elif duration_key == "1y":
            year = start.year + 1
            max_d = calendar.monthrange(year, start.month)[1]
            end = date(year, start.month, min(start.day, max_d))
        elif duration_key == "10y":
            year = start.year + 10
            max_d = calendar.monthrange(year, start.month)[1]
            end = date(year, start.month, min(start.day, max_d))
        elif duration_key == "forever":
            end = date(2099, 12, 31)
        else:
            return

        self.end_date.setDate(QDate(end.year, end.month, end.day))

    def _refresh_repeat_details(self) -> None:
        q_d = self.start_date.date()
        cur_d = date(q_d.year(), q_d.month(), q_d.day())
        lunar_info = get_lunar_date(cur_d)
        if hasattr(self, "start_lunar_badge"):
            if lunar_info:
                leap_str = " (윤달)" if lunar_info.is_leap else ""
                self.start_lunar_badge.setText(f"음력 {lunar_info.month}월 {lunar_info.day}일{leap_str}")
                self.start_lunar_badge.show()
            else:
                self.start_lunar_badge.hide()

        recurrence = self.recurrence_combo.currentData()
        self.interval_wrap.setVisible(recurrence == RecurrenceType.DAILY.value)
        self.month_day_wrap.setVisible(recurrence == RecurrenceType.MONTHLY.value)
        self.month_week_wrap.setVisible(recurrence == RecurrenceType.MONTHLY_NTH.value)
        self.weekday_wrap.setVisible(recurrence == RecurrenceType.WEEKLY.value)
        self.recurrence_summary.setVisible(recurrence != RecurrenceType.WEEKLY.value and recurrence != RecurrenceType.LUNAR_YEARLY.value)
        if recurrence == RecurrenceType.YEARLY.value:
            self.recurrence_summary.setText(f"매년 {self.start_date.date().toString('MM월 dd일')}")
            self.recurrence_summary.setStyleSheet("")
        elif recurrence == RecurrenceType.LUNAR_YEARLY.value:
            self.recurrence_summary.setText("")
            self.recurrence_summary.setStyleSheet("")
        elif recurrence == RecurrenceType.MONTHLY.value:
            self.recurrence_summary.setStyleSheet("")
            if self.recurrence_month_end_check.isChecked():
                self.recurrence_summary.setText("매월 말일")
            else:
                self.recurrence_summary.setText(f"매월 {self.recurrence_month_day.value()}일")
        elif recurrence == RecurrenceType.MONTHLY_NTH.value:
            self.recurrence_summary.setStyleSheet("")
            week_label = str(self.recurrence_month_week_combo.currentText())
            weekday_label = str(self.recurrence_month_weekday_combo.currentText())
            self.recurrence_summary.setText(f"매월 {week_label} {weekday_label}요일")
        elif recurrence == RecurrenceType.WEEKLY.value:
            self.recurrence_summary.setStyleSheet("")
            selected = [label for label, checkbox in zip(WEEKDAY_LABELS, self.weekday_checks) if checkbox.isChecked()]
            self.recurrence_summary.setText(" ".join(selected) if selected else "요일 선택")
        else:
            self.recurrence_summary.setStyleSheet("")
            interval = max(1, self.recurrence_interval.value())
            self.recurrence_summary.setText("매일 반복" if interval == 1 else f"{interval}일 간격")

    def _add_attachment(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "첨부파일 선택")
        if filenames:
            self.attachments.extend(filenames)
            self.attachments_label.setText(self._attachments_text())
            if hasattr(self, "attachment_bar") and not self.attachment_bar.isVisible():
                self.attachment_bar.show()
                parent = getattr(self, "_owner_window", None) or self.parent()
                if parent and hasattr(parent, "repository") and self.entry and self.entry.entry_id:
                    parent.repository.set_setting(f"memo_show_attach_{self.entry.entry_id}", "1")
            if self.entry_type == EntryType.MEMO:
                self._auto_save_to_db()

    def _add_image_from_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "이미지 선택",
            "",
            "Image Files (*.png *.jpg *.jpeg *.gif *.bmp);;All Files (*)"
        )
        if filename:
            self.description_input.insert_image_file(filename)
            if self.entry_type == EntryType.MEMO:
                self._auto_save_to_db()

    def _save(self) -> None:
        if self.entry_type == EntryType.MEMO:
            title = self.title_input.text().strip()
            if not title:
                QMessageBox.warning(self, "입력 오류", "메모 제목을 입력해 주세요.")
                return
            plain_content = self.description_input.toPlainText().strip()
            html_content = self.description_input.toHtml()
            description_to_save = html_content if plain_content else ""
            self.result = CalendarEntry(
                entry_type=self.entry_type,
                title=title,
                description=description_to_save,
                attachments=list(self.attachments),
            )
            self.accept()
            return

        start_date = self.start_date.date().toPython()
        end_date = self.end_date.date().toPython()
        if end_date < start_date:
            reply = QMessageBox.question(
                self,
                "종료일 확인",
                "종료일은 시작일보다 빠를 수 없습니다.\n종료일을 시작일로 맞춰서 저장할까요?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return
            self.end_date.setDate(self.start_date.date())
            end_date = start_date

        recurrence_enabled = self.repeat_check.isChecked()
        recurrence_type = RecurrenceType(self.recurrence_combo.currentData()) if recurrence_enabled else RecurrenceType.NONE
        weekdays = [idx for idx, checkbox in enumerate(self.weekday_checks) if checkbox.isChecked()]
        if recurrence_enabled and recurrence_type == RecurrenceType.WEEKLY and not weekdays:
            QMessageBox.warning(self, "입력 오류", "매주 반복은 최소 1개 요일을 선택해야 합니다.")
            return
        if recurrence_enabled and recurrence_type == RecurrenceType.MONTHLY_NTH:
            weekdays = [int(self.recurrence_month_weekday_combo.currentData())]

        if recurrence_enabled and recurrence_type == RecurrenceType.LUNAR_YEARLY:
            lunar_info = get_lunar_date(start_date)
            rec_month = lunar_info.month if lunar_info else start_date.month
            rec_day = lunar_info.day if lunar_info else start_date.day
            rec_leap = lunar_info.is_leap if lunar_info else False
        else:
            rec_month = self.recurrence_month_day.value()
            rec_day = int(self.recurrence_month_week_combo.currentData())
            rec_leap = self.recurrence_month_end_check.isChecked()

        plain_content = self.description_input.toPlainText().strip()
        if self.entry and self.entry.title and self.entry.title not in ("일정", "크롬에서 등록"):
            default_title = self.entry.title[:40]
        else:
            first_line = plain_content.splitlines()[0].strip() if plain_content else "일정"
            import re
            m = re.match(r"^(?:\d+[\.\)]\s*)?제목:\s*(.+)$", first_line)
            if m:
                default_title = m.group(1).strip()[:40]
            else:
                default_title = first_line[:40]
        html_content = self.description_input.toHtml()
        description_to_save = html_content if "<img" in html_content else plain_content
        self.result = CalendarEntry(
            entry_type=self.entry_type,
            title=default_title or "일정",
            description=description_to_save,
            day=start_date,
            start_date=start_date,
            end_date=end_date,
            start_time="" if self.all_day.isChecked() else self.start_time.time().toString("HH:mm"),
            end_time="" if self.all_day.isChecked() else self.end_time.time().toString("HH:mm"),
            all_day=self.all_day.isChecked(),
            attachments=list(self.attachments),
            recurrence_enabled=recurrence_enabled,
            recurrence_type=recurrence_type,
            recurrence_interval=self.recurrence_interval.value(),
            recurrence_weekdays=weekdays,
            recurrence_month_day=rec_month,
            recurrence_month_week=rec_day,
            recurrence_month_end=rec_leap,
            icon_type=str(getattr(self, "_selected_icon", "")),
            bg_color=str(self.bg_color_combo.currentData()),
            alert_type=AlertType.POPUP if self.alert_popup.isChecked() else AlertType.NONE,
            alert_offset=str(self.alert_offset_combo.currentData()),
        )
        self.accept()

    def _trigger_auto_save(self) -> None:
        if self.entry_type == EntryType.MEMO:
            if self.entry is not None:
                self.entry.title = self.title_input.text().strip() or "제목 없음"
                self.entry.description = self.description_input.toHtml()
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and hasattr(parent, "_update_group_dialog_memos"):
                parent._update_group_dialog_memos(self.entry)
            self._save_timer.start(400)

    def _update_title_input_width(self) -> None:
        if hasattr(self, "title_input"):
            text = self.title_input.text() or self.title_input.placeholderText()
            fm = self.title_input.fontMetrics()
            w = fm.horizontalAdvance(text) + 24
            max_w = max(100, self.width() - 100)
            self.title_input.setFixedWidth(min(max_w, max(70, w)))

    def _apply_memo_theme(self, theme_key: str) -> None:
        theme = MEMO_THEMES.get(theme_key, MEMO_THEMES["yellow"])
        self._current_memo_theme = theme_key
        
        self.setStyleSheet(f"QDialog#entryDialog {{ background-color: {theme['bg']}; border: 1px solid {theme['border']}; }}")
        self.header.setStyleSheet(f"background-color: {theme['header']};")
        self.title_input.setStyleSheet(f"font-size: 13px; font-weight: bold; border: none; background: transparent; padding: 2px; color: {theme['text']};")
        font_size_pt = getattr(self, "_current_memo_font_size", 11)
        self.description_input.setStyleSheet(f"border: none; background: transparent; font-size: {font_size_pt}pt; padding: 0px; color: {theme['text']};")
        icon_btn_style = (
            "QPushButton {"
            "  background-color: rgba(255, 255, 255, 0.7);"
            "  border: 1px solid rgba(0, 0, 0, 0.15);"
            "  border-radius: 4px;"
            "  padding: 0px;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            f"  color: {theme['text']};"
            "  text-align: center;"
            "  width: 22px;"
            "  height: 22px;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(255, 255, 255, 0.95);"
            "  border: 1px solid rgba(0, 0, 0, 0.35);"
            "}"
        )
        for btn_attr in ("_pin_btn", "_collapse_btn", "_close_btn"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                btn.setStyleSheet(icon_btn_style)
            
        btn_style = (
            "QPushButton {"
            f"  background-color: rgba(255, 255, 255, 0.75);"
            f"  border: 1px solid rgba(0, 0, 0, 0.22);"
            "  border-radius: 0px;"
            "  padding: 2px 8px;"
            "  font-size: 11px;"
            f"  color: {theme['text']};"
            "  height: 22px;"
            "}"
            "QPushButton:hover {"
            f"  background-color: rgba(255, 255, 255, 0.95);"
            f"  border: 1px solid rgba(0, 0, 0, 0.4);"
            "}"
            "QPushButton:pressed {"
            f"  background-color: rgba(0, 0, 0, 0.06);"
            "}"
        )
        if hasattr(self, "add_image_button"):
            self.add_image_button.setStyleSheet(btn_style)
        if hasattr(self, "attach_button"):
            self.attach_button.setStyleSheet(btn_style)
        if hasattr(self, "attachments_label"):
            label_style = (
                "QPushButton {"
                "  background: transparent;"
                "  border: none;"
                "  font-size: 11px;"
                f"  color: {theme['text']};"
                "  text-align: left;"
                "  padding-left: 2px;"
                "}"
                "QPushButton:hover {"
                "  text-decoration: underline;"
                "}"
            )
            self.attachments_label.setStyleSheet(label_style)
        self.update()

    def _get_memo_resize_direction(self, global_pos: QPoint) -> str | None:
        local_pos = self.mapFromGlobal(global_pos)
        w = self.width()
        h = self.height()
        border = 8

        if not (0 <= local_pos.x() <= w and 0 <= local_pos.y() <= h):
            return None

        is_collapsed = getattr(self, "_is_collapsed", False)
        if is_collapsed:
            if local_pos.x() >= w - border:
                return "r"
            elif local_pos.x() <= border:
                return "l"
            return None

        # Corners
        if local_pos.x() >= w - border and local_pos.y() >= h - border:
            return "br"
        if local_pos.x() <= border and local_pos.y() >= h - border:
            return "bl"

        # Edges
        if local_pos.x() >= w - border:
            return "r"
        if local_pos.x() <= border:
            return "l"
        if local_pos.y() >= h - border:
            return "b"

        return None

    def _perform_memo_resize(self, global_pos: QPoint) -> None:
        if not getattr(self, "_resize_dir", None):
            return
        delta = global_pos - self._initial_mouse_pos
        geom = QRect(self._initial_geometry)
        is_collapsed = getattr(self, "_is_collapsed", False)

        if self._resize_dir == "r":
            new_w = max(180, geom.width() + delta.x())
            geom.setWidth(new_w)
            if is_collapsed:
                geom.setHeight(36)
        elif self._resize_dir == "l":
            new_w = max(180, geom.width() - delta.x())
            new_x = (geom.x() + geom.width()) - new_w
            geom.setX(new_x)
            geom.setWidth(new_w)
            if is_collapsed:
                geom.setHeight(36)
        elif self._resize_dir == "b" and not is_collapsed:
            geom.setHeight(max(150, geom.height() + delta.y()))
        elif self._resize_dir == "br" and not is_collapsed:
            new_w = max(180, geom.width() + delta.x())
            geom.setWidth(new_w)
            geom.setHeight(max(150, geom.height() + delta.y()))
        elif self._resize_dir == "bl" and not is_collapsed:
            new_w = max(180, geom.width() - delta.x())
            new_x = (geom.x() + geom.width()) - new_w
            geom.setX(new_x)
            geom.setWidth(new_w)
            geom.setHeight(max(150, geom.height() + delta.y()))

        other_geos = self._other_window_geometries()
        screen = self.screen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        snapped_geom = snap_resize_rect(geom, self._resize_dir, other_geos, screen_geo, threshold=16)

        if snapped_geom.width() < 180:
            snapped_geom.setWidth(180)
        if is_collapsed:
            snapped_geom.setHeight(36)
        elif snapped_geom.height() < 150:
            snapped_geom.setHeight(150)

        self.setGeometry(snapped_geom)
        if not is_collapsed:
            self._expanded_width = snapped_geom.width()
            self._expanded_height = snapped_geom.height()
        else:
            self._collapsed_width = snapped_geom.width()

    def eventFilter(self, watched, event) -> bool:
        if self.entry_type == EntryType.MEMO and watched in (getattr(self, "header", None), getattr(self, "content_wrap", None)):
            if event.type() == QEvent.Type.MouseMove:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    if getattr(self, "_resize_dir", None):
                        self._perform_memo_resize(event.globalPosition().toPoint())
                        return True
                    elif hasattr(self, "_drag_position"):
                        self._perform_window_drag(event.globalPosition().toPoint())
                        return True
                else:
                    r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
                    if r_dir in ("r", "l"):
                        watched.setCursor(Qt.CursorShape.SizeHorCursor)
                    elif r_dir == "b":
                        watched.setCursor(Qt.CursorShape.SizeVerCursor)
                    elif r_dir == "br":
                        watched.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    elif r_dir == "bl":
                        watched.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    else:
                        watched.unsetCursor()
            elif event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
                if self._handle_collapsed_edge_dblclick(r_dir):
                    return True
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
                if r_dir:
                    self._resize_dir = r_dir
                    self._initial_geometry = self.geometry()
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if getattr(self, "_resize_dir", None):
                    self._resize_dir = None
                    self._debounced_save_memo_geometry(250)
                    return True
        return super().eventFilter(watched, event)

    def _handle_collapsed_edge_dblclick(self, r_dir: str | None) -> bool:
        if not getattr(self, "_is_collapsed", False) or r_dir not in ("r", "l"):
            return False
        target_w = max(180, getattr(self, "_expanded_width", 380) or 380)
        curr_w = self.width()
        if abs(curr_w - target_w) <= 10 and getattr(self, "_prev_compact_width", None):
            new_w = self._prev_compact_width
        else:
            self._prev_compact_width = curr_w
            new_w = target_w

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        screen_right = avail.x() + avail.width()
        old_right = self.x() + self.width()

        should_shift_left = (r_dir == "l")
        if not should_shift_left and getattr(self, "_expand_anchor_right", False):
            if (self.x() + new_w > screen_right) or getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                should_shift_left = True
                self._anchored_to_right = True

        if should_shift_left:
            delta_w = new_w - curr_w
            new_x = self.x() - delta_w
            if new_x + new_w > screen_right:
                new_x = screen_right - new_w
            if new_x < avail.left():
                new_x = avail.left()
            self.move(new_x, self.y())
        self._collapsed_width = new_w
        self.resize(new_w, self.height())
        self._debounced_save_memo_geometry(250)
        return True

    def _show_memo_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 0px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 24px 6px 20px;
                font-size: 12px;
                color: #222222;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)
        
        new_memo_action = menu.addAction("새 메모 추가")
        new_memo_action.triggered.connect(self._create_new_memo)
        new_group_action = menu.addAction("새 그룹 추가...")
        new_group_action.triggered.connect(lambda: self._create_new_group(assign_current=False))
        menu.addSeparator()

        curr_group_id = getattr(self.entry, "memo_group", "") if self.entry else ""
        if curr_group_id:
            parent = getattr(self, "_owner_window", None) or self.parent()
            grp = parent.repository.get_memo_group(curr_group_id) if parent and hasattr(parent, "repository") else None
            grp_name = grp.get("title", "그룹") if grp else "소속 그룹"
            open_grp_act = menu.addAction(f"📂 소속 그룹 열기 ({grp_name})")
            open_grp_act.triggered.connect(lambda: parent._open_memo_group(curr_group_id) if parent and hasattr(parent, "_open_memo_group") else None)
            open_grp_memos_act = menu.addAction(f"그룹 메모 모두 열기 ({grp_name})")
            open_grp_memos_act.triggered.connect(lambda: self._open_group_memos(curr_group_id))
            close_grp_memos_act = menu.addAction(f"그룹 메모 모두 닫기 ({grp_name})")
            close_grp_memos_act.triggered.connect(lambda: self._close_group_memos(curr_group_id))
            menu.addSeparator()

        open_all_act = menu.addAction("모든 메모 열기")
        open_all_act.triggered.connect(self._open_all_memos)
        close_all_act = menu.addAction("모든 메모 닫기")
        close_all_act.triggered.connect(self._close_all_memos)
        menu.addSeparator()

        group_menu = menu.addMenu("📁 그룹 지정 / 이동")
        group_menu.setStyleSheet(menu.styleSheet())
        self._populate_group_menu(group_menu)
        menu.addSeparator()

        color_menu = menu.addMenu("메모 색상 변경")
        color_menu.setStyleSheet(menu.styleSheet())
        for k, th in MEMO_THEMES.items():
            action = color_menu.addAction(th['name'])
            action.triggered.connect(lambda _=False, key=k: self._on_theme_selected(key))
            
        op_action = menu.addAction("투명도 조절")
        op_action.setCheckable(True)
        op_action.setChecked(hasattr(self, "_opacity_bar") and self._opacity_bar.isVisible())
        op_action.triggered.connect(lambda: self._toggle_opacity_bar())

        is_editor_vis = hasattr(self, "memo_editor_toolbar") and self.memo_editor_toolbar.isVisible()
        editor_action = menu.addAction("에디터 보기/닫기")
        editor_action.setCheckable(True)
        editor_action.setChecked(is_editor_vis)
        editor_action.triggered.connect(lambda: self._toggle_editor_toolbar())

        is_todo = hasattr(self, "description_input") and getattr(self.description_input, "is_todo_mode", False)
        todo_action = menu.addAction("To-Do 스타일로 변경" if not is_todo else "일반 텍스트로 변경")
        todo_action.setCheckable(True)
        todo_action.setChecked(is_todo)
        todo_action.triggered.connect(self._toggle_todo_mode)

        is_attach_vis = hasattr(self, "attachment_bar") and self.attachment_bar.isVisible()
        attach_action = menu.addAction("하단 파일첨부 열고/닫기")
        attach_action.setCheckable(True)
        attach_action.setChecked(is_attach_vis)
        attach_action.triggered.connect(lambda: self._toggle_attachment_bar())

        menu.addSeparator()
        
        is_floating = getattr(self, "_is_floating", False)
        float_action = menu.addAction("항상 위에 고정")
        float_action.setCheckable(True)
        float_action.setChecked(is_floating)
        float_action.triggered.connect(self._toggle_floating)
        
        shortcut_action = menu.addAction("메모 단축키 설정...")
        shortcut_action.triggered.connect(self._open_hotkey_settings)
        
        menu.addSeparator()
        del_action = menu.addAction("메모 삭제")
        del_action.triggered.connect(self._delete_memo)
        
        menu.exec(global_pos)

    def _populate_group_menu(self, group_menu: QMenu) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        curr_group_id = getattr(self.entry, "memo_group", "") if self.entry else ""

        none_act = group_menu.addAction("그룹 없음 (지정 해제)")
        none_act.setCheckable(True)
        none_act.setChecked(not curr_group_id)
        none_act.triggered.connect(lambda: self._set_memo_group(""))
        group_menu.addSeparator()

        groups = parent.repository.list_memo_groups() if parent and hasattr(parent, "repository") else []
        if groups:
            for grp in groups:
                gid = grp.get("id", "")
                gtitle = grp.get("title", "그룹")
                act = group_menu.addAction(f"📁 {gtitle}")
                act.setCheckable(True)
                act.setChecked(curr_group_id == gid)
                act.triggered.connect(lambda _=False, target_id=gid: self._set_memo_group(target_id))
            group_menu.addSeparator()

        new_grp_act = group_menu.addAction("➕ 새 그룹 생성 및 이동...")
        new_grp_act.triggered.connect(lambda: self._create_new_group(assign_current=True))

    def _set_memo_group(self, group_id: str) -> None:
        if not self.entry:
            return
        self.entry.memo_group = group_id
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository"):
            self.entry = parent.repository.upsert_entry(self.entry)
            parent.repository.save()
            if hasattr(parent, "refresh"):
                parent.refresh()
            if hasattr(parent, "_refresh_all_group_dialogs"):
                parent._refresh_all_group_dialogs()

    def _create_new_group(self, assign_current: bool = False) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if not parent:
            return
        title, ok = QInputDialog.getText(self, "새 메모 그룹", "새 그룹 이름을 입력하세요:", text="새 그룹")
        if not ok or not title.strip():
            return
        assign_id = self.entry.entry_id if assign_current and self.entry and self.entry.entry_id else None
        if hasattr(parent, "_create_new_memo_group"):
            parent._create_new_memo_group(title=title.strip(), assign_memo_id=assign_id)
        if hasattr(parent, "refresh"):
            parent.refresh()
        if hasattr(parent, "_refresh_all_group_dialogs"):
            parent._refresh_all_group_dialogs()

    def _create_new_memo(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_edit_entry"):
            parent._edit_entry(EntryType.MEMO, None)

    def _open_all_memos(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_open_all_memos"):
            parent._open_all_memos()

    def _close_all_memos(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_close_all_memos"):
            parent._close_all_memos()

    def _open_group_memos(self, group_id: str) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if not parent or not hasattr(parent, "repository"):
            return
        memos = [m for m in parent.repository.list_memos() if getattr(m, "memo_group", "") == group_id]
        if not memos:
            return
        setattr(parent, "_batch_updating_memos", True)
        try:
            for m in memos:
                parent._edit_entry(EntryType.MEMO, m)
        finally:
            setattr(parent, "_batch_updating_memos", False)
        if hasattr(parent, "_sync_open_memo_ids"):
            parent._sync_open_memo_ids(persist=True)
        if hasattr(parent, "_refresh_all_group_dialogs"):
            parent._refresh_all_group_dialogs(status_only=True)
        if hasattr(parent, "refresh"):
            parent.refresh()

    def _close_group_memos(self, group_id: str) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if not parent or not hasattr(parent, "_active_memo_dialogs"):
            return
        memos = [m for m in parent.repository.list_memos() if getattr(m, "memo_group", "") == group_id]
        if not memos:
            return
        setattr(parent, "_batch_updating_memos", True)
        try:
            for m in memos:
                key = int(m.entry_id) if m.entry_id is not None else None
                if key in parent._active_memo_dialogs:
                    dlg = parent._active_memo_dialogs[key]
                    if dlg and dlg.isVisible():
                        dlg.close()
        finally:
            setattr(parent, "_batch_updating_memos", False)
        if hasattr(parent, "_sync_open_memo_ids"):
            parent._sync_open_memo_ids(persist=True)
        if hasattr(parent, "_refresh_all_group_dialogs"):
            parent._refresh_all_group_dialogs(status_only=True)
        if hasattr(parent, "refresh"):
            parent.refresh()

    def _open_hotkey_settings(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_open_settings"):
            parent._open_settings(initial_tab="shortcuts")

    def _delete_memo(self) -> None:
        title = self.title_input.text().strip() or "제목 없음"
        reply = QMessageBox.question(
            self,
            "메모 삭제",
            f"'{title}' 메모를 삭제할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._is_deleted = True
            if hasattr(self, "_geo_save_timer") and self._geo_save_timer.isActive():
                self._geo_save_timer.stop()
            parent = getattr(self, "_owner_window", None) or self.parent()
            if self.entry and self.entry.entry_id and parent and hasattr(parent, "repository"):
                target_id = self.entry.entry_id
                parent.repository.delete_entry(target_id)
                if hasattr(parent, "_load_memo_order_ids") and hasattr(parent, "_save_memo_order_ids"):
                    ids = [m_id for m_id in parent._load_memo_order_ids() if m_id != int(target_id)]
                    parent._save_memo_order_ids(ids, persist=False)
                if hasattr(parent, "_active_memo_dialogs"):
                    for k in [target_id, int(target_id)]:
                        parent._active_memo_dialogs.pop(k, None)
                if hasattr(parent, "_sync_open_memo_ids"):
                    parent._sync_open_memo_ids(persist=False)
                parent.repository.save()
                if hasattr(parent, "refresh"):
                    try:
                        parent.refresh()
                    except Exception:
                        pass
                if hasattr(parent, "_refresh_all_group_dialogs"):
                    try:
                        parent._refresh_all_group_dialogs()
                    except Exception:
                        pass
            self._save_timer.stop()
            self.close()

    def _on_theme_selected(self, key: str) -> None:
        self._apply_memo_theme(key)
        self._auto_save_to_db()

    def _set_topmost_native(self, topmost: bool) -> None:
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_int
            HWND_TOPMOST = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                HWND_TOPMOST if topmost else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        except Exception:
            pass

    def _update_pin_btn(self) -> None:
        btn = getattr(self, "_pin_btn", None)
        if btn is None:
            return
        if getattr(self, "_is_floating", False):
            btn.setIcon(QIcon(str(asset_path("pin_active.svg"))))
            btn.setToolTip("항상 위에 고정 해제")
        else:
            btn.setIcon(QIcon(str(asset_path("pin_inactive.svg"))))
            btn.setToolTip("항상 위에 고정")

    def _toggle_pin_memo(self) -> None:
        self._toggle_floating(not getattr(self, "_is_floating", False))

    def _toggle_floating(self, checked: bool) -> None:
        self._is_floating = checked
        self._set_topmost_native(checked)
        self._update_pin_btn()
        self._auto_save_to_db()

    def _setup_memo_editor_toolbar(self, parent_layout: QVBoxLayout) -> None:
        self.memo_editor_toolbar = QWidget()
        self.memo_editor_toolbar.setObjectName("memoEditorToolbar")
        self.memo_editor_toolbar.setStyleSheet("""
            QWidget#memoEditorToolbar {
                background: rgba(255, 255, 255, 0.95);
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 5px;
                padding: 1px 2px;
            }
            QPushButton.editorBtn {
                min-width: 22px;
                max-width: 24px;
                height: 22px;
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 3px;
                background: #ffffff;
                color: #222222;
                font-size: 11px;
                font-weight: 700;
                padding: 0px;
            }
            QPushButton.editorBtn:hover {
                background: #f1f5f9;
            }
            QPushButton.editorBtn:checked {
                background: #2563eb;
                color: #ffffff;
                border-color: #1d4ed8;
            }
            QComboBox#editorCombo, QFontComboBox#editorFontCombo {
                height: 22px;
                font-size: 11px;
                background: #ffffff;
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 3px;
                padding: 0 2px;
            }
            QPushButton#editorCloseBtn {
                min-width: 18px;
                max-width: 18px;
                height: 18px;
                border: none;
                background: transparent;
                color: #64748b;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton#editorCloseBtn:hover {
                color: #ef4444;
                background: rgba(239, 68, 68, 0.1);
                border-radius: 2px;
            }
        """)

        tb = QHBoxLayout(self.memo_editor_toolbar)
        tb.setContentsMargins(3, 2, 3, 2)
        tb.setSpacing(3)

        self.btn_bold = QPushButton("B")
        self.btn_bold.setProperty("class", "editorBtn")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setToolTip("굵게 (Ctrl+B)")
        self.btn_bold.clicked.connect(self._format_bold)
        tb.addWidget(self.btn_bold)

        self.btn_italic = QPushButton("I")
        self.btn_italic.setProperty("class", "editorBtn")
        self.btn_italic.setCheckable(True)
        font_i = QFont("Segoe UI", 10)
        font_i.setItalic(True)
        self.btn_italic.setFont(font_i)
        self.btn_italic.setToolTip("기울임 (Ctrl+I)")
        self.btn_italic.clicked.connect(self._format_italic)
        tb.addWidget(self.btn_italic)

        self.btn_underline = QPushButton("U")
        self.btn_underline.setProperty("class", "editorBtn")
        self.btn_underline.setCheckable(True)
        font_u = QFont("Segoe UI", 10)
        font_u.setUnderline(True)
        self.btn_underline.setFont(font_u)
        self.btn_underline.setToolTip("밑줄 (Ctrl+U)")
        self.btn_underline.clicked.connect(self._format_underline)
        tb.addWidget(self.btn_underline)

        self.btn_strike = QPushButton("S")
        self.btn_strike.setProperty("class", "editorBtn")
        self.btn_strike.setCheckable(True)
        font_s = QFont("Segoe UI", 10)
        font_s.setStrikeOut(True)
        self.btn_strike.setFont(font_s)
        self.btn_strike.setToolTip("취소선")
        self.btn_strike.clicked.connect(self._format_strike)
        tb.addWidget(self.btn_strike)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        tb.addWidget(sep)

        self.font_combo = QFontComboBox()
        self.font_combo.setObjectName("editorFontCombo")
        self.font_combo.setToolTip("글꼴")
        self.font_combo.setMaximumWidth(95)
        self.font_combo.currentFontChanged.connect(self._format_font)
        tb.addWidget(self.font_combo)

        self.size_combo = QComboBox()
        self.size_combo.setObjectName("editorCombo")
        self.size_combo.setToolTip("글자 크기")
        self.size_combo.setMaximumWidth(48)
        for s in [8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 24]:
            self.size_combo.addItem(str(s), s)
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(10)))
        self.size_combo.currentIndexChanged.connect(self._format_size)
        tb.addWidget(self.size_combo)

        self.btn_color = QPushButton("🎨")
        self.btn_color.setProperty("class", "editorBtn")
        self.btn_color.setToolTip("글자 색상")
        self.btn_color.clicked.connect(self._format_color)
        tb.addWidget(self.btn_color)

        tb.addStretch(1)

        close_btn = QPushButton()
        close_btn.setIcon(QIcon(str(asset_path("memo_close.svg"))))
        close_btn.setIconSize(QSize(10, 10))
        close_btn.setObjectName("editorCloseBtn")
        close_btn.setToolTip("에디터 도구 닫기")
        close_btn.clicked.connect(lambda: self._toggle_editor_toolbar(False))
        tb.addWidget(close_btn)

        parent_layout.addWidget(self.memo_editor_toolbar)

        # 기본 상태는 닫힘 (숨김)
        self.memo_editor_toolbar.setVisible(False)

    def _format_bold(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Bold if self.btn_bold.isChecked() else QFont.Weight.Normal)
        self._apply_char_format(fmt)

    def _format_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.btn_italic.isChecked())
        self._apply_char_format(fmt)

    def _format_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self.btn_underline.isChecked())
        self._apply_char_format(fmt)

    def _format_strike(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(self.btn_strike.isChecked())
        self._apply_char_format(fmt)

    def _format_font(self, font: QFont) -> None:
        fmt = QTextCharFormat()
        fmt.setFontFamilies([font.family()])
        self._apply_char_format(fmt)

    def _format_size(self) -> None:
        val = self.size_combo.currentData()
        if val is not None:
            fmt = QTextCharFormat()
            fmt.setFontPointSize(float(val))
            self._apply_char_format(fmt)

    def _format_color(self) -> None:
        color = QColorDialog.getColor(Qt.GlobalColor.black, self, "글자 색상 선택")
        if color.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            self._apply_char_format(fmt)

    def _apply_char_format(self, fmt: QTextCharFormat) -> None:
        cursor = self.description_input.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        cursor.mergeCharFormat(fmt)
        self.description_input.mergeCurrentCharFormat(fmt)
        self.description_input.setFocus()

    def _sync_editor_toolbar_state(self) -> None:
        if not hasattr(self, "memo_editor_toolbar") or not self.memo_editor_toolbar.isVisible():
            return
        try:
            fmt = self.description_input.currentCharFormat()

            self.btn_bold.blockSignals(True)
            self.btn_bold.setChecked(fmt.fontWeight() == QFont.Weight.Bold or fmt.fontWeight() >= 700)
            self.btn_bold.blockSignals(False)

            self.btn_italic.blockSignals(True)
            self.btn_italic.setChecked(fmt.fontItalic())
            self.btn_italic.blockSignals(False)

            self.btn_underline.blockSignals(True)
            self.btn_underline.setChecked(fmt.fontUnderline())
            self.btn_underline.blockSignals(False)

            self.btn_strike.blockSignals(True)
            self.btn_strike.setChecked(fmt.fontStrikeOut())
            self.btn_strike.blockSignals(False)

            fams = fmt.fontFamilies()
            if fams:
                fam = fams[0] if isinstance(fams, (list, tuple)) else str(fams)
                self.font_combo.blockSignals(True)
                self.font_combo.setCurrentFont(QFont(fam))
                self.font_combo.blockSignals(False)

            pt = int(round(fmt.fontPointSize()))
            if pt > 0:
                idx = self.size_combo.findData(pt)
                if idx >= 0:
                    self.size_combo.blockSignals(True)
                    self.size_combo.setCurrentIndex(idx)
                    self.size_combo.blockSignals(False)
        except Exception:
            pass

    def _toggle_editor_toolbar(self, show: bool | None = None) -> None:
        if not hasattr(self, "memo_editor_toolbar"):
            return
        try:
            if show is None:
                show = not self.memo_editor_toolbar.isVisible()

            was_visible = self.memo_editor_toolbar.isVisible()
            self.memo_editor_toolbar.setVisible(show)
            if show:
                if not was_visible and self.height() < 240:
                    self.resize(self.width(), self.height() + 32)
                self._sync_editor_toolbar_state()
            self.description_input.setFocus()
        except Exception as exc:
            logger.exception("Error in _toggle_editor_toolbar: %s", exc)

    def _toggle_todo_mode(self) -> None:
        if hasattr(self, "description_input") and hasattr(self.description_input, "toggle_todo_style"):
            self.description_input.toggle_todo_style()
            self._trigger_auto_save()

    def _toggle_attachment_bar(self, show: bool | None = None) -> None:
        if not hasattr(self, "attachment_bar"):
            return
        try:
            if show is None:
                show = not self.attachment_bar.isVisible()
            self.attachment_bar.setVisible(show)
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and hasattr(parent, "repository") and self.entry and self.entry.entry_id:
                parent.repository.set_setting(f"memo_show_attach_{self.entry.entry_id}", "1" if show else "0")
                if hasattr(self, "_debounced_save_memo_geometry"):
                    self._debounced_save_memo_geometry(250)
                else:
                    parent.repository.save()
        except Exception as exc:
            logger.exception("Error in _toggle_attachment_bar: %s", exc)

    def _on_attach_button_clicked(self) -> None:
        if self.attachments:
            self._show_attachments_menu()
        else:
            self._add_attachment()

    def _resolve_attachment_file(self, file_path: str) -> Path | None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository"):
            try:
                p = parent.repository.resolve_attachment_path(file_path)
                if p.exists() and p.is_file():
                    return p
            except Exception:
                pass
        direct_p = Path(file_path)
        if direct_p.exists() and direct_p.is_file():
            return direct_p
        # Fallback check across standard paths
        for base in [
            runtime_root() / "db" / "attachments",
            runtime_root() / "attachments",
            runtime_root().parent / "db" / "attachments",
        ]:
            try:
                candidate = base / file_path
                if candidate.exists() and candidate.is_file():
                    return candidate
            except Exception:
                pass
        return None

    def _get_attachment_name(self, file_path: str) -> str:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_attachment_display_name"):
            return parent._attachment_display_name(file_path)
        p = Path(file_path)
        parts = p.name.split("_", 2)
        if len(parts) == 3 and len(parts[0]) == 6 and len(parts[1]) == 8:
            return parts[2]
        return p.name

    def _show_attachments_menu(self) -> None:
        if not self.attachments:
            self._add_attachment()
            return
            
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 0px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 20px 6px 16px;
                font-size: 12px;
                color: #222222;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)
        
        for idx, file_path in enumerate(self.attachments):
            display_name = self._get_attachment_name(file_path)
            file_menu = menu.addMenu(display_name)
            file_menu.setStyleSheet(menu.styleSheet())
            
            open_action = file_menu.addAction("파일 열기 (실행)")
            open_action.triggered.connect(lambda _=False, path=file_path: self._open_file(path))
            
            folder_action = file_menu.addAction("파일 폴더 열기")
            folder_action.triggered.connect(lambda _=False, path=file_path: self._open_folder(path))
            
            save_action = file_menu.addAction("다른 이름으로 저장...")
            save_action.triggered.connect(lambda _=False, path=file_path: self._save_file_as(path))
            
            file_menu.addSeparator()
            del_action = file_menu.addAction("첨부 삭제")
            del_action.triggered.connect(lambda _=False, i=idx: self._remove_attachment(i))
            
        btn = getattr(self, "attachments_label", None) or getattr(self, "attach_button", None)
        if btn:
            menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        else:
            menu.exec(QCursor.pos())

    def _open_file(self, file_path: str) -> None:
        target = self._resolve_attachment_file(file_path)
        if not target or not target.exists():
            QMessageBox.warning(self, "오류", "첨부파일 원본을 찾을 수 없습니다.")
            return

        dangerous_exts = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".scr", ".com", ".hta", ".cpl", ".msi", ".wsf", ".reg"}
        if target.suffix.lower() in dangerous_exts:
            ret = QMessageBox.warning(
                self,
                "보안 확인",
                f"선택한 첨부파일({target.name})은 실행 가능한 스크립트/프로그램 파일입니다.\n"
                "신뢰할 수 없는 파일인 경우 악성 코드가 실행될 수 있습니다.\n\n"
                "정말 이 파일을 실행하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        try:
            import os
            os.startfile(str(target.resolve()))
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve())))

    def _open_folder(self, file_path: str) -> None:
        target = self._resolve_attachment_file(file_path)
        if not target or not target.exists():
            QMessageBox.warning(self, "오류", "첨부파일 원본을 찾을 수 없습니다.")
            return
        try:
            import subprocess
            subprocess.run(["explorer", f"/select,{str(target.resolve())}"])
        except Exception:
            try:
                import os
                os.startfile(str(target.parent.resolve()))
            except Exception:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent.resolve())))

    def _save_file_as(self, file_path: str) -> None:
        target = self._resolve_attachment_file(file_path)
        if not target or not target.exists():
            QMessageBox.warning(self, "오류", "첨부파일 원본을 찾을 수 없습니다.")
            return
        display_name = self._get_attachment_name(file_path)
        dest, _ = QFileDialog.getSaveFileName(self, "파일 저장", display_name)
        if dest:
            try:
                import shutil
                shutil.copy2(target, dest)
                QMessageBox.information(self, "완료", "첨부파일을 저장했습니다.")
            except Exception as e:
                QMessageBox.warning(self, "오류", f"파일 저장 실패: {e}")

    def _remove_attachment(self, index: int) -> None:
        if 0 <= index < len(self.attachments):
            self.attachments.pop(index)
            self.attachments_label.setText(self._attachments_text())
            parent = getattr(self, "_owner_window", None) or self.parent()
            repo = getattr(parent, "repository", None) if parent else None
            per_memo_attach = repo.get_setting(f"memo_show_attach_{self.entry.entry_id}", "") if (repo and self.entry and self.entry.entry_id) else ""
            if per_memo_attach == "0" and hasattr(self, "attachment_bar"):
                self.attachment_bar.hide()
            elif per_memo_attach == "":
                show_attach_setting = (repo.get_setting("memo_show_attachment_bar", "1") != "0") if repo else True
                if not show_attach_setting and not self.attachments and hasattr(self, "attachment_bar"):
                    self.attachment_bar.hide()
            self._auto_save_to_db()

    def _resize_with_anchor(self, width: int, height: int) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        screen_right = avail.x() + avail.width()

        old_right = self.x() + self.width()
        target_x = self.x()

        if getattr(self, "_expand_anchor_right", False):
            is_expanding = width > self.width()
            is_collapsing = width < self.width()

            if is_expanding:
                if (target_x + width > screen_right) or getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                    target_x = old_right - width
                    self._anchored_to_right = True
                else:
                    self._anchored_to_right = False
            elif is_collapsing:
                if getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                    target_x = old_right - width
                    self._anchored_to_right = True
                else:
                    self._anchored_to_right = False

            if getattr(self, "_anchored_to_right", False):
                if target_x + width > screen_right:
                    target_x = screen_right - width
                if target_x < avail.left():
                    target_x = avail.left()

        self.setGeometry(target_x, self.y(), width, height)

    def _toggle_collapse(self) -> None:
        self._is_collapsed = not getattr(self, "_is_collapsed", False)
        if self._is_collapsed:
            curr_w = self.width()
            col_w = getattr(self, "_collapsed_width", None)
            if col_w is None or curr_w != col_w:
                self._expanded_width = curr_w
            self._expanded_height = max(150, getattr(self, "_expanded_height", self.height()))
            if hasattr(self, "content_wrap"):
                self.content_wrap.hide()
            self.setMinimumHeight(36)
            self.setMaximumHeight(36)
            target_w = getattr(self, "_collapsed_width", None) or self._expanded_width
            self._resize_with_anchor(target_w, 36)
            if hasattr(self, "_collapse_btn") and self._collapse_btn is not None:
                self._collapse_btn.setIcon(QIcon(str(asset_path("memo_maximize.svg"))))
                self._collapse_btn.setToolTip("메모 펼치기")
        else:
            curr_w = self.width()
            exp_w = getattr(self, "_expanded_width", None)
            if exp_w is None or curr_w != exp_w:
                self._collapsed_width = curr_w
            if hasattr(self, "content_wrap"):
                self.content_wrap.show()
            self.setMinimumHeight(150)
            self.setMaximumHeight(16777215)
            target_w = getattr(self, "_expanded_width", 380)
            target_h = getattr(self, "_expanded_height", 360)
            self._resize_with_anchor(target_w, target_h)
            if hasattr(self, "_collapse_btn") and self._collapse_btn is not None:
                self._collapse_btn.setIcon(QIcon(str(asset_path("memo_minimize.svg"))))
                self._collapse_btn.setToolTip("메모 접기")
            
        self._debounced_save_memo_geometry(250)

    def _debounced_save_memo_geometry(self, delay_ms: int = 250) -> None:
        self._save_memo_geometry(persist=False)
        if not hasattr(self, "_geo_save_timer"):
            self._geo_save_timer = QTimer(self)
            self._geo_save_timer.setSingleShot(True)
            self._geo_save_timer.timeout.connect(self._flush_memo_geometry)
        self._geo_save_timer.start(delay_ms)

    def _flush_memo_geometry(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository"):
            parent.repository.save()

    def _save_memo_geometry(self, persist: bool = True) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository") and self.entry and self.entry.entry_id:
            curr_geo = self.geometry()
            is_col = getattr(self, "_is_collapsed", False)
            w_val = getattr(self, "_expanded_width", curr_geo.width())
            h_val = getattr(self, "_expanded_height", curr_geo.height())
            save_x = curr_geo.x()
            if is_col and getattr(self, "_expand_anchor_right", False):
                screen = self.screen() or QApplication.primaryScreen()
                avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
                screen_right = avail.x() + avail.width()
                if getattr(self, "_anchored_to_right", False) or (curr_geo.x() + w_val > screen_right) or (curr_geo.x() + curr_geo.width() >= screen_right - 16):
                    save_x = curr_geo.x() + curr_geo.width() - w_val
            parent.repository.set_setting(f"memo_geo_{self.entry.entry_id}", f"{save_x},{curr_geo.y()},{w_val},{h_val}")
            parent.repository.set_setting(f"memo_collapsed_{self.entry.entry_id}", "1" if is_col else "0")
            if getattr(self, "_collapsed_width", None) is not None:
                parent.repository.set_setting(f"memo_collapsed_w_{self.entry.entry_id}", str(self._collapsed_width))
            if hasattr(self, "attachment_bar"):
                parent.repository.set_setting(f"memo_show_attach_{self.entry.entry_id}", "1" if self.attachment_bar.isVisible() else "0")
            if persist:
                parent.repository.save()

    def _end_window_drag(self) -> None:
        if hasattr(self, "_drag_position"):
            delattr(self, "_drag_position")
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            screen_right = avail.x() + avail.width()
            exp_w = getattr(self, "_expanded_width", 380)
            self._anchored_to_right = (self.x() + exp_w > screen_right) or (self.x() + self.width() >= screen_right - 16)
        self._debounced_save_memo_geometry(250)

    def _start_window_drag(self, global_pos: QPoint) -> None:
        self._drag_position = global_pos - self.frameGeometry().topLeft()

    def _other_window_geometries(self) -> list[QRect]:
        geos: list[QRect] = []
        parent = getattr(self, "_owner_window", None) or self.parent()
        if not parent:
            return geos
        for attr in ("_active_group_dialogs", "_active_memo_dialogs"):
            dialogs = getattr(parent, attr, None)
            if not dialogs:
                continue
            for dlg in list(dialogs.values()):
                if dlg is not None and dlg is not self and dlg.isVisible():
                    geos.append(dlg.geometry())
        return geos

    def _perform_window_drag(self, global_pos: QPoint) -> None:
        if hasattr(self, "_drag_position"):
            target_pos = global_pos - self._drag_position
            curr_geo = QRect(target_pos, self.size())
            other_geos = self._other_window_geometries()
            screen = self.screen()
            screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
            snapped_pos = snap_window_rect(curr_geo, other_geos, screen_geo, threshold=16)
            self.move(snapped_pos)

    def _auto_save_to_db(self, persist_disk: bool = True, refresh_parent: bool = False) -> None:
        if self.entry_type != EntryType.MEMO:
            return
        
        self._save_timer.stop()
        
        title = self.title_input.text().strip()
        plain_content = self.description_input.toPlainText().strip()
        
        if not title and not plain_content:
            return
            
        html_content = self.description_input.toHtml()
        description_to_save = html_content if plain_content else ""
        
        from taskcalendar.models import CalendarEntry
        curr_memo_group = getattr(self.entry, "memo_group", "") if self.entry else ""
        curr_created_at = self.entry.created_at if self.entry else None
        self.result = CalendarEntry(
            entry_type=self.entry_type,
            title=title or "제목 없음",
            description=description_to_save,
            attachments=list(self.attachments),
            bg_color=getattr(self, "_current_memo_theme", "yellow"),
            icon_type="floating" if getattr(self, "_is_floating", False) else "",
            memo_group=curr_memo_group,
            created_at=curr_created_at,
        )
        
        if self.entry and self.entry.entry_id:
            self.result.entry_id = self.entry.entry_id
            
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository"):
            is_new = (self.entry is None or self.entry.entry_id is None)
            saved = parent.repository.upsert_entry(self.result)
            self.entry = saved
            self.attachments = list(saved.attachments)
            if hasattr(self, "attachments_label"):
                self.attachments_label.setText(self._attachments_text())
            
            if is_new and saved.entry_id is not None and hasattr(parent, "_ordered_memos") and hasattr(parent, "_save_memo_order_ids"):
                ids = [int(e.entry_id) for e in parent._ordered_memos(parent.repository.list_memos()) if e.entry_id is not None]
                if int(saved.entry_id) not in ids:
                    ids.insert(0, int(saved.entry_id))
                parent._save_memo_order_ids(ids, persist=False)
                
            if is_new and saved.entry_id is not None and hasattr(parent, "_active_memo_dialogs"):
                if hasattr(self, "_active_key") and self._active_key in parent._active_memo_dialogs:
                    parent._active_memo_dialogs.pop(self._active_key, None)
                parent._active_memo_dialogs[int(saved.entry_id)] = self
                self._active_key = int(saved.entry_id)
                if hasattr(parent, "_sync_open_memo_ids"):
                    parent._sync_open_memo_ids(persist=False)
                
            # Persist geometry and collapse state
            if saved.entry_id is not None:
                self._save_memo_geometry(persist=False)
                
            if persist_disk:
                parent.repository.save()
            if refresh_parent:
                parent.refresh()
            if is_new and curr_memo_group:
                if hasattr(parent, "_refresh_all_group_dialogs"):
                    parent._refresh_all_group_dialogs()
            elif hasattr(parent, "_update_group_dialog_memos"):
                parent._update_group_dialog_memos(self.entry)

    def _focus_title_for_new_memo(self) -> None:
        if hasattr(self, "title_input") and self.title_input is not None:
            self.title_input.setReadOnly(False)
            self.title_input.setCursor(Qt.CursorShape.IBeamCursor)
            self.title_input.activateWindow()
            self.title_input.setFocus(Qt.FocusReason.OtherFocusReason)
            self.title_input.selectAll()

    def _toggle_opacity_bar(self, show: bool | None = None) -> None:
        if hasattr(self, "_opacity_bar"):
            if show is None:
                show = self._opacity_bar.isHidden()
            self._opacity_bar.setVisible(show)

    def _on_opacity_slider_changed(self, val: int) -> None:
        if hasattr(self, "_opacity_val_label"):
            self._opacity_val_label.setText(f"{val}%")
        self._set_memo_opacity(val)

    def _set_memo_opacity(self, percent: int) -> None:
        opacity = max(0.2, min(1.0, percent / 100.0))
        self.setWindowOpacity(opacity)
        if hasattr(self, "_opacity_slider") and self._opacity_slider.value() != percent:
            self._opacity_slider.blockSignals(True)
            self._opacity_slider.setValue(percent)
            self._opacity_slider.blockSignals(False)
        if hasattr(self, "_opacity_val_label"):
            self._opacity_val_label.setText(f"{percent}%")
        if self.entry and self.entry.entry_id:
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and hasattr(parent, "repository"):
                parent.repository.set_setting(f"memo_opacity_{self.entry.entry_id}", str(percent))

    def _close_memo(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self.hide()
        if self.entry_type == EntryType.MEMO:
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and getattr(parent, "_is_app_quitting", False):
                self.close()
                return
            try:
                self._auto_save_to_db(persist_disk=False, refresh_parent=False)
            except Exception:
                pass
            if parent and hasattr(parent, "_active_memo_dialogs"):
                to_remove = [k for k, v in list(parent._active_memo_dialogs.items()) if v is self]
                for k in to_remove:
                    parent._active_memo_dialogs.pop(k, None)
                if not getattr(parent, "_batch_updating_memos", False):
                    if hasattr(parent, "_sync_open_memo_ids"):
                        parent._sync_open_memo_ids(persist=True)
            if not getattr(parent, "_batch_updating_memos", False):
                if parent and hasattr(parent, "refresh"):
                    try:
                        parent.refresh()
                    except Exception:
                        pass
                if parent and hasattr(parent, "_refresh_all_group_dialogs"):
                    try:
                        parent._refresh_all_group_dialogs(status_only=True)
                    except Exception:
                        pass
        self.close()

    def closeEvent(self, event) -> None:
        if getattr(self, "_is_deleted", False):
            super().closeEvent(event)
            return
        if hasattr(self, "_geo_save_timer") and self._geo_save_timer.isActive():
            self._geo_save_timer.stop()
        if self.entry_type == EntryType.MEMO:
            parent = getattr(self, "_owner_window", None) or self.parent()
            is_quitting = bool(parent and getattr(parent, "_is_app_quitting", False))
            is_batch = bool(parent and getattr(parent, "_batch_updating_memos", False))
            self._save_memo_geometry(persist=not (is_quitting or is_batch))
            if is_quitting:
                super().closeEvent(event)
                return
            if not getattr(self, "_closing", False):
                self._close_memo()
        super().closeEvent(event)

    def reject(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and getattr(parent, "_is_app_quitting", False):
            super().reject()
            return
        if self.entry_type == EntryType.MEMO:
            self._close_memo()
            return
        super().reject()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO and event.button() == Qt.MouseButton.LeftButton:
            r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
            if self._handle_collapsed_edge_dblclick(r_dir):
                event.accept()
                return
            pos = event.position().toPoint()
            if pos.y() <= 34:
                self._toggle_collapse()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            if event.button() == Qt.MouseButton.RightButton:
                self._show_memo_context_menu(event.globalPosition().toPoint())
                event.accept()
                return
            if event.button() == Qt.MouseButton.LeftButton:
                r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
                if r_dir:
                    self._resize_dir = r_dir
                    self._initial_geometry = self.geometry()
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                    event.accept()
                    return

                pos = event.position().toPoint()
                if pos.y() <= 34:
                    self._start_window_drag(event.globalPosition().toPoint())
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            if event.buttons() & Qt.MouseButton.LeftButton:
                if getattr(self, "_resize_dir", None):
                    self._perform_memo_resize(event.globalPosition().toPoint())
                    event.accept()
                    return
                elif hasattr(self, "_drag_position"):
                    self._perform_window_drag(event.globalPosition().toPoint())
                    event.accept()
                    return
            else:
                r_dir = self._get_memo_resize_direction(event.globalPosition().toPoint())
                if r_dir in ("r", "l"):
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif r_dir == "b":
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                elif r_dir == "br":
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif r_dir == "bl":
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            was_resizing = getattr(self, "_resize_dir", None) is not None
            self._resize_dir = None
            self._end_window_drag()
            if was_resizing:
                self._debounced_save_memo_geometry(250)
        super().mouseReleaseEvent(event)

    def add_dropped_attachments(self, filepaths: list[str]) -> None:
        added = False
        for path_str in filepaths:
            if path_str not in self.attachments:
                self.attachments.append(path_str)
                added = True
        if added:
            if hasattr(self, "attachments_label"):
                self.attachments_label.setText(self._attachments_text())
            if hasattr(self, "attachment_bar") and not self.attachment_bar.isVisible():
                self.attachment_bar.show()
                parent = getattr(self, "_owner_window", None) or self.parent()
                if parent and hasattr(parent, "repository") and self.entry and self.entry.entry_id:
                    parent.repository.set_setting(f"memo_show_attach_{self.entry.entry_id}", "1")
            if self.entry_type == EntryType.MEMO:
                self._auto_save_to_db()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
            image_files = []
            other_files = []
            for url in event.mimeData().urls():
                local_path = url.toLocalFile()
                if local_path and os.path.exists(local_path):
                    ext = os.path.splitext(local_path)[1].lower()
                    if ext in image_exts:
                        image_files.append(local_path)
                    else:
                        other_files.append(local_path)
            
            if self.entry_type == EntryType.MEMO and hasattr(self, "description_input") and image_files:
                for img_p in image_files:
                    self.description_input.insert_image_file(img_p)
                    
            if other_files:
                self.add_dropped_attachments(other_files)
                
            event.acceptProposedAction()
            if self.entry_type == EntryType.MEMO:
                self._auto_save_to_db()
            return
        super().dropEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.entry_type == EntryType.MEMO and not getattr(self, "_is_collapsed", False):
            painter = QPainter(self)
            theme = MEMO_THEMES.get(getattr(self, "_current_memo_theme", "yellow"), MEMO_THEMES["yellow"])
            grip_color = QColor(theme["text"])
            grip_color.setAlpha(90)
            painter.setPen(QPen(grip_color, 1))
            w = self.width()
            h = self.height()
            painter.drawLine(w - 3, h - 11, w - 11, h - 3)
            painter.drawLine(w - 3, h - 7, w - 7, h - 3)
            painter.drawLine(w - 3, h - 3, w - 3, h - 3)
            painter.end()


class MiniMemoCardWidget(QFrame):
    clicked = Signal(object)
    doubleClicked = Signal(object)
    reordered = Signal(int, int, bool)
    requestToggleOpen = Signal(object)
    requestRemoveFromGroup = Signal(object)
    requestDeleteMemo = Signal(object)

    _MIME_TYPE = "application/x-taskcalendar-memo-id"

    def __init__(self, entry: CalendarEntry, is_open_on_desktop: bool = False, view_mode: str = "card", parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.is_open_on_desktop = is_open_on_desktop
        self.view_mode = view_mode
        if self.view_mode == "list":
            self.setFixedHeight(34)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedSize(105, 140)  # 3:4 aspect ratio
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)
        self._press_pos: QPoint | None = None
        self._drag_started = False
        self._drop_indicator: str | None = None
        self._init_ui()

    @staticmethod
    def _plain_snippet(description: str) -> str:
        desc_text = description or ""
        if not desc_text:
            return ""
        if "<" in desc_text and ">" in desc_text:
            import re
            clean_html = re.sub(r"(?is)<head.*?</head>", "", desc_text)
            clean_html = re.sub(r"(?is)<style.*?</style>", "", clean_html)
            clean_html = re.sub(r"(?is)<script.*?</script>", "", clean_html)
            from PySide6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(clean_html)
            plain = doc.toPlainText().strip()
            if not plain:
                plain = re.sub(r"<[^>]+>", " ", clean_html).strip()
            return plain
        return desc_text.strip()

    def update_content(self, entry: CalendarEntry) -> None:
        self.entry = entry
        title_text = (entry.title or "제목 없음").strip()
        if hasattr(self, "title_lbl"):
            if hasattr(self.title_lbl, "set_full_text"):
                self.title_lbl.set_full_text(title_text)
            else:
                self.title_lbl.setText(title_text)
        snippet = self._plain_snippet(entry.description)
        if self.view_mode == "list":
            clean_snippet = " · ".join([line.strip() for line in snippet.splitlines() if line.strip()])
            if hasattr(self, "desc_lbl"):
                if hasattr(self.desc_lbl, "set_full_text"):
                    self.desc_lbl.set_full_text(clean_snippet)
                else:
                    self.desc_lbl.setText(clean_snippet)
        else:
            if len(snippet) > 80:
                snippet = snippet[:80] + "..."
            if hasattr(self, "desc_lbl"):
                self.desc_lbl.setText(snippet)

    def _init_ui(self) -> None:
        theme = MEMO_THEMES.get(self.entry.bg_color, MEMO_THEMES["yellow"])
        bg_col = theme.get("bg", "#fff7c2")
        border_col = theme.get("border", "#d5c880")
        text_col = theme.get("text", "#2c2c2c")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 6px;
            }}
            QFrame:hover {{
                border: 1.5px solid rgba(0, 0, 0, 0.45);
            }}
        """)

        title_text = (self.entry.title or "제목 없음").strip()

        # Content snippet (strip html if html)
        snippet = self._plain_snippet(self.entry.description)

        dot_color = "#16a34a" if self.is_open_on_desktop else "#94a3b8"
        dot_tip = "바탕화면에 열려 있음" if self.is_open_on_desktop else "바탕화면에서 닫힘"
        date_str = self.entry.created_at.strftime("%m.%d") if self.entry.created_at else ""

        if self.view_mode == "list":
            self.setMinimumWidth(0)
            layout = QHBoxLayout(self)
            layout.setContentsMargins(10, 4, 10, 4)
            layout.setSpacing(8)
            layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            self.status_dot = QLabel("●" if self.is_open_on_desktop else "○")
            self.status_dot.setStyleSheet(f"font-size: 10px; color: {dot_color}; font-weight: bold; background: transparent; border: none;")
            self.status_dot.setToolTip(dot_tip)
            self.status_dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.status_dot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(self.status_dot)

            self.title_lbl = ElidedLabel(title_text, is_elastic=False)
            self.title_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {text_col}; background: transparent; border: none;")
            self.title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.title_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            self.title_lbl.setMinimumWidth(20)
            layout.addWidget(self.title_lbl)

            clean_snippet = " · ".join([line.strip() for line in snippet.splitlines() if line.strip()])
            self.desc_lbl = ElidedLabel(clean_snippet, is_elastic=True)
            self.desc_lbl.setStyleSheet(f"font-size: 10px; color: {text_col}; opacity: 0.75; background: transparent; border: none;")
            self.desc_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.desc_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.desc_lbl.setMinimumWidth(0)
            layout.addWidget(self.desc_lbl, 1)

            date_lbl = QLabel(date_str)
            date_lbl.setStyleSheet(f"font-size: 9px; color: {text_col}; opacity: 0.65; background: transparent; border: none;")
            date_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            date_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.addWidget(date_lbl)
        else:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 5)
            layout.setSpacing(3)

            # Header with Title
            self.title_lbl = QLabel(title_text)
            self.title_lbl.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {text_col}; background: transparent; border: none;")
            self.title_lbl.setWordWrap(True)
            self.title_lbl.setFixedHeight(28)
            self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            self.title_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(self.title_lbl)

            # Separator line
            self._sep = QFrame()
            self._sep.setFixedHeight(1)
            self._sep.setStyleSheet(f"background-color: {border_col}; border: none;")
            self._sep.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(self._sep)

            if len(snippet) > 80:
                snippet = snippet[:80] + "..."

            self.desc_lbl = QLabel(snippet)
            self.desc_lbl.setStyleSheet(f"font-size: 10px; color: {text_col}; background: transparent; border: none;")
            self.desc_lbl.setWordWrap(True)
            self.desc_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.desc_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(self.desc_lbl, 1)

            # Bottom status row
            footer = QHBoxLayout()
            footer.setContentsMargins(0, 0, 0, 0)
            footer.setSpacing(2)

            self.status_dot = QLabel("●" if self.is_open_on_desktop else "○")
            self.status_dot.setStyleSheet(f"font-size: 9px; color: {dot_color}; font-weight: bold; background: transparent; border: none;")
            self.status_dot.setToolTip(dot_tip)
            footer.addWidget(self.status_dot)

            date_lbl = QLabel(date_str)
            date_lbl.setStyleSheet(f"font-size: 9px; color: {text_col}; opacity: 0.7; background: transparent; border: none;")
            footer.addStretch(1)
            footer.addWidget(date_lbl)

            layout.addLayout(footer)

    def set_open_on_desktop(self, is_open: bool) -> None:
        if self.is_open_on_desktop == is_open:
            return
        self.is_open_on_desktop = is_open
        dot_color = "#16a34a" if is_open else "#94a3b8"
        dot_tip = "바탕화면에 열려 있음" if is_open else "바탕화면에서 닫힘"
        if hasattr(self, "status_dot"):
            self.status_dot.setText("●" if is_open else "○")
            font_size = "10px" if self.view_mode == "list" else "9px"
            self.status_dot.setStyleSheet(f"font-size: {font_size}; color: {dot_color}; font-weight: bold; background: transparent; border: none;")
            self.status_dot.setToolTip(dot_tip)

    def set_preview_visible(self, visible: bool) -> None:
        if not hasattr(self, "desc_lbl"):
            return
        self.desc_lbl.setVisible(visible)
        sep = getattr(self, "_sep", None)
        if sep is not None:
            sep.setVisible(visible)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_started = False
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            dist = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._drag_started = True
                self._start_drag()
                self._press_pos = None
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._press_pos is not None and not self._drag_started:
                self.clicked.emit(self.entry)
            self._press_pos = None
            self._drag_started = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.entry)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _start_drag(self) -> None:
        if self.entry.entry_id is None:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._MIME_TYPE, str(self.entry.entry_id).encode("utf-8"))
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def _is_valid_drag(self, event) -> bool:
        data = event.mimeData().data(self._MIME_TYPE)
        if not data:
            return False
        try:
            source_id = int(bytes(data).decode("utf-8"))
            return self.entry.entry_id is not None and source_id != int(self.entry.entry_id)
        except Exception:
            return False

    def dragEnterEvent(self, event) -> None:
        if self._is_valid_drag(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._is_valid_drag(event):
            event.acceptProposedAction()
            if self.view_mode == "list":
                before = event.position().y() < (self.height() / 2.0)
                indicator = "top" if before else "bottom"
            else:
                before = event.position().x() < (self.width() / 2.0)
                indicator = "left" if before else "right"
            if self._drop_indicator != indicator:
                self._drop_indicator = indicator
                self.update()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        if self._drop_indicator is not None:
            self._drop_indicator = None
            self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        if self._drop_indicator is not None:
            self._drop_indicator = None
            self.update()
        if not self._is_valid_drag(event):
            event.ignore()
            return
        try:
            source_id = int(bytes(event.mimeData().data(self._MIME_TYPE)).decode("utf-8"))
            target_id = int(self.entry.entry_id)
            if self.view_mode == "list":
                before = event.position().y() < (self.height() / 2.0)
            else:
                before = event.position().x() < (self.width() / 2.0)
            self.reordered.emit(source_id, target_id, before)
            event.acceptProposedAction()
        except Exception:
            event.ignore()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_indicator:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, False)
            pen = QPen(QColor("#0284c7"), 3)
            painter.setPen(pen)
            if self._drop_indicator == "left":
                painter.drawLine(1, 4, 1, self.height() - 4)
            elif self._drop_indicator == "right":
                painter.drawLine(self.width() - 2, 4, self.width() - 2, self.height() - 4)
            elif self._drop_indicator == "top":
                painter.drawLine(4, 1, self.width() - 4, 1)
            elif self._drop_indicator == "bottom":
                painter.drawLine(4, self.height() - 2, self.width() - 4, self.height() - 2)
            painter.end()

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 0px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 20px 6px 16px;
                font-size: 12px;
                color: #222222;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)
        open_act = menu.addAction("바탕화면에서 닫기" if self.is_open_on_desktop else "바탕화면에 열기")
        open_act.triggered.connect(lambda: self.requestToggleOpen.emit(self.entry))

        remove_act = menu.addAction("이 그룹에서 제외 (그룹 해제)")
        remove_act.triggered.connect(lambda: self.requestRemoveFromGroup.emit(self.entry))

        menu.addSeparator()
        del_act = menu.addAction("메모 삭제")
        del_act.triggered.connect(lambda: self.requestDeleteMemo.emit(self.entry))

        menu.exec(global_pos)


class FloatingGroupDialog(QDialog):
    def __init__(self, parent, group_dict: dict) -> None:
        super().__init__(None)
        self._owner_window = parent
        self.group_dict = dict(group_dict)
        self.group_id = str(group_dict.get("id", ""))
        self.group_title = str(group_dict.get("title", "새 그룹"))
        self.group_color = str(group_dict.get("color", "yellow"))
        self.view_mode = str(group_dict.get("view_mode", "list"))
        self._is_floating = bool(group_dict.get("is_floating", False))
        self._is_collapsed = bool(group_dict.get("is_collapsed", False))
        self._initial_mouse_pos = QPoint()
        self._initial_geometry = QRect()
        self._resize_dir: str | None = None
        self._expanded_height = 420
        self._expanded_width = 360
        self._collapsed_width = self.group_dict.get("collapsed_width", None)
        _owner_repo = getattr(parent, "repository", None)
        self._expand_anchor_right = bool(_owner_repo) and _owner_repo.get_setting("memo_expand_anchor", "left") == "right"
        self._anchored_to_right = False
        self._drag_pos: QPoint | None = None
        self._cards: list[MiniMemoCardWidget] = []
        self._current_cols: int = 0
        self._preview_hidden: bool | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setModal(False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(250, 180)

        # Load saved geo or default
        geo_str = self.group_dict.get("geo", "")
        has_geo = False
        if geo_str:
            try:
                pts = [int(p) for p in geo_str.split(",")]
                if len(pts) == 4:
                    self._expanded_width = max(250, pts[2])
                    self._expanded_height = max(180, pts[3])
                    self.setGeometry(pts[0], pts[1], self._expanded_width, self._expanded_height)
                    has_geo = True
            except Exception:
                pass
        if has_geo:
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                geo = self.geometry()
                nx = max(avail.left(), min(geo.x(), avail.right() - 100))
                ny = max(avail.top(), min(geo.y(), avail.bottom() - 36))
                self.move(nx, ny)
        else:
            screen = self.screen() or QApplication.primaryScreen()
            if screen:
                avail = screen.availableGeometry()
                cx = avail.x() + (avail.width() - 360) // 2 + 50
                cy = avail.y() + (avail.height() - 420) // 2 + 50
                self.setGeometry(cx, cy, 360, 420)
            else:
                self.resize(360, 420)

        self._build_ui()
        self._apply_theme(self.group_color)
        if self._is_collapsed:
            self.content_wrap.hide()
            target_w = self._collapsed_width if self._collapsed_width is not None else self._expanded_width
            self._resize_with_anchor(target_w, 36)
            self.setFixedHeight(36)
            self._update_collapse_btn()
            self._apply_theme(self.group_color)
        else:
            self.setMinimumSize(250, 180)
            self.setMaximumHeight(16777215)
        if self._is_floating:
            self._set_topmost_native(True)
        self._update_header_mode(force=True)
        self.refresh_memos()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Header Bar
        self.header = QWidget()
        self.header.setFixedHeight(36)
        self.header.setMouseTracking(True)
        self.header.installEventFilter(self)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(8, 0, 6, 0)
        h_layout.setSpacing(4)
        h_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.icon_lbl = QLabel()
        self.icon_lbl.setPixmap(QIcon(str(asset_path("folder.svg"))).pixmap(18, 18))
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        self.icon_lbl.setFixedSize(18, 22)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.installEventFilter(self)
        h_layout.addWidget(self.icon_lbl)

        self.title_input = EditableTitleLineEdit(self.group_title, self.header)
        self.title_input.setPlaceholderText("그룹 이름")
        self.title_input.setFixedHeight(24)
        self.title_input.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.title_input.textChanged.connect(self._on_title_changed)
        self.title_input.textChanged.connect(self._update_title_input_width)
        h_layout.addWidget(self.title_input)

        self.count_badge = QLabel("0개")
        self.count_badge.setStyleSheet("font-size: 11px; font-weight: bold; background: rgba(0,0,0,0.08); padding: 2px 6px; border-radius: 4px; color: #333333;")
        self.count_badge.setFixedHeight(20)
        self.count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_badge.installEventFilter(self)
        h_layout.addWidget(self.count_badge)

        h_layout.addStretch(1)
        self._update_title_input_width()

        self.add_memo_btn = QPushButton("+ 메모")
        self.add_memo_btn.setToolTip("이 그룹에 새 메모 추가")
        self.add_memo_btn.setCursor(Qt.PointingHandCursor)
        self.add_memo_btn.setFixedHeight(22)
        self.add_memo_btn.clicked.connect(self._on_add_memo_clicked)
        h_layout.addWidget(self.add_memo_btn)

        self.open_all_btn = QPushButton("모두 열기")
        self.open_all_btn.setToolTip("그룹 내 모든 메모 바탕화면에 열기")
        self.open_all_btn.setCursor(Qt.PointingHandCursor)
        self.open_all_btn.setFixedHeight(22)
        self.open_all_btn.clicked.connect(self._open_all_memos)
        h_layout.addWidget(self.open_all_btn)

        self.close_all_btn = QPushButton("모두 닫기")
        self.close_all_btn.setToolTip("그룹 내 모든 메모 바탕화면에서 닫기")
        self.close_all_btn.setCursor(Qt.PointingHandCursor)
        self.close_all_btn.setFixedHeight(22)
        self.close_all_btn.clicked.connect(self._close_all_memos)
        h_layout.addWidget(self.close_all_btn)

        self.view_mode_btn = QPushButton()
        self.view_mode_btn.setFixedSize(22, 22)
        self.view_mode_btn.setIconSize(QSize(12, 12))
        self.view_mode_btn.setCursor(Qt.PointingHandCursor)
        self.view_mode_btn.clicked.connect(self._toggle_view_mode)
        self._update_view_mode_btn()
        h_layout.addWidget(self.view_mode_btn)

        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(22, 22)
        self.pin_btn.setIconSize(QSize(13, 13))
        self.pin_btn.setCursor(Qt.PointingHandCursor)
        self.pin_btn.clicked.connect(self._toggle_pin)
        self._update_pin_btn()
        h_layout.addWidget(self.pin_btn)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setFixedSize(22, 22)
        self.collapse_btn.setIconSize(QSize(12, 12))
        self.collapse_btn.setToolTip("접기 / 펼치기")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self._toggle_collapse)
        self._update_collapse_btn()
        h_layout.addWidget(self.collapse_btn)

        self.close_btn = QPushButton()
        self.close_btn.setIcon(QIcon(str(asset_path("memo_close.svg"))))
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setToolTip("그룹 창 닫기")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        h_layout.addWidget(self.close_btn)

        root.addWidget(self.header)

        # 2. Content Area
        self.content_wrap = QWidget()
        self.content_wrap.setMouseTracking(True)
        self.content_wrap.installEventFilter(self)
        c_layout = QVBoxLayout(self.content_wrap)
        c_layout.setContentsMargins(8, 8, 8, 8)
        c_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("border: none; background: transparent;")
        self.scroll.setAcceptDrops(True)
        self.scroll.setMouseTracking(True)
        self.scroll.installEventFilter(self)
        if self.scroll.viewport():
            self.scroll.viewport().setMouseTracking(True)
            self.scroll.viewport().installEventFilter(self)

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_container.setAcceptDrops(True)
        self.cards_container.setMouseTracking(True)
        self.cards_container.installEventFilter(self)
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setContentsMargins(4, 4, 4, 4)
        self.cards_grid.setSpacing(8)
        self.cards_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.cards_container)
        c_layout.addWidget(self.scroll)

        root.addWidget(self.content_wrap, 1)

    def _apply_theme(self, color_key: str) -> None:
        self.group_color = color_key
        theme = MEMO_THEMES.get(color_key, MEMO_THEMES["yellow"])
        bg_col = theme.get("bg", "#fff7c2")
        hdr_col = theme.get("header", "#f5e99f")
        border_col = theme.get("border", "#d5c880")
        text_col = theme.get("text", "#2c2c2c")

        self.setStyleSheet(f"""
            FloatingGroupDialog {{
                background-color: {bg_col};
                border: 2px solid {border_col};
                border-radius: 8px;
            }}
        """)
        is_collapsed = getattr(self, "_is_collapsed", False)
        header_border_bottom = "none" if is_collapsed else f"1px solid {border_col}"
        header_bottom_radius = "6px" if is_collapsed else "0px"
        self.header.setStyleSheet(f"""
            QWidget {{
                background-color: {hdr_col};
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border-bottom-left-radius: {header_bottom_radius};
                border-bottom-right-radius: {header_bottom_radius};
                border-bottom: {header_border_bottom};
            }}
        """)
        self._text_btn_style = f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 11px;
                font-weight: 600;
                color: {text_col};
                height: 22px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.95);
                border: 1px solid rgba(0, 0, 0, 0.35);
            }}
        """
        self._icon_btn_style = f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.7);
                border: 1px solid rgba(0, 0, 0, 0.15);
                border-radius: 4px;
                padding: 0px;
                font-size: 11px;
                font-weight: 600;
                color: {text_col};
                text-align: center;
                width: 22px;
                height: 22px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.95);
                border: 1px solid rgba(0, 0, 0, 0.35);
            }}
        """
        self.view_mode_btn.setStyleSheet(self._icon_btn_style)
        self.pin_btn.setStyleSheet(self._icon_btn_style)
        self.collapse_btn.setStyleSheet(self._icon_btn_style)
        self.close_btn.setStyleSheet(self._icon_btn_style)
        if hasattr(self, "title_input"):
            self.title_input.setStyleSheet(f"font-size: 13px; font-weight: bold; border: none; background: transparent; padding: 2px; color: {text_col};")
        self._update_header_mode(force=True)

    def _update_header_mode(self, force: bool = False) -> None:
        is_narrow = self.width() < 480
        if not force and getattr(self, "_header_is_narrow", None) == is_narrow:
            self._update_title_input_width()
            return
        self._header_is_narrow = is_narrow

        txt_style = getattr(self, "_text_btn_style", "")
        if is_narrow:
            self.count_badge.hide()
            self.add_memo_btn.hide()
            self.open_all_btn.hide()
            self.close_all_btn.hide()
        else:
            self.count_badge.show()
            self.add_memo_btn.show()
            self.open_all_btn.show()
            self.close_all_btn.show()

            self.add_memo_btn.setText("+ 메모")
            self.add_memo_btn.setFixedHeight(22)
            if txt_style:
                self.add_memo_btn.setStyleSheet(txt_style)

            self.open_all_btn.setText("모두 열기")
            self.open_all_btn.setFixedHeight(22)
            if txt_style:
                self.open_all_btn.setStyleSheet(txt_style)

            self.close_all_btn.setText("모두 닫기")
            self.close_all_btn.setFixedHeight(22)
            if txt_style:
                self.close_all_btn.setStyleSheet(txt_style)
        self._update_title_input_width()

    def update_open_statuses(self) -> None:
        parent = self._owner_window
        if not parent:
            return
        active_ids = set()
        if hasattr(parent, "_active_memo_dialogs"):
            for k, dlg in parent._active_memo_dialogs.items():
                if dlg and getattr(dlg, "entry", None) and dlg.entry.entry_id:
                    if dlg.isVisible():
                        active_ids.add(dlg.entry.entry_id)
        for card in getattr(self, "_cards", []):
            if hasattr(card, "entry") and card.entry and card.entry.entry_id:
                card.set_open_on_desktop(card.entry.entry_id in active_ids)

    def update_memo_content(self, entry: CalendarEntry) -> None:
        if entry is None or getattr(entry, "entry_id", None) is None:
            return
        for card in getattr(self, "_cards", []):
            card_entry = getattr(card, "entry", None)
            if card_entry is not None and card_entry.entry_id == entry.entry_id:
                card.update_content(entry)
                return

    def _update_preview_visibility(self) -> None:
        hide_preview = self.width() < 300
        if self._preview_hidden == hide_preview:
            return
        self._preview_hidden = hide_preview
        for card in getattr(self, "_cards", []):
            if hasattr(card, "set_preview_visible"):
                card.set_preview_visible(not hide_preview)

    def _realign_cards_on_resize(self) -> None:
        if self.view_mode == "list":
            return
        cards = getattr(self, "_cards", [])
        if not cards:
            return
        cols = max(1, (self.width() - 28) // 115)
        if cols == getattr(self, "_current_cols", None):
            return
        self._current_cols = cols

        self.cards_container.setUpdatesEnabled(False)
        try:
            for r in range(self.cards_grid.rowCount()):
                self.cards_grid.setRowStretch(r, 0)
            for c in range(self.cards_grid.columnCount()):
                self.cards_grid.setColumnStretch(c, 0)

            while self.cards_grid.count():
                self.cards_grid.takeAt(0)

            for i, card in enumerate(cards):
                row = i // cols
                col = i % cols
                self.cards_grid.addWidget(card, row, col, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            total_rows = (len(cards) + cols - 1) // cols
            self.cards_grid.setRowStretch(total_rows, 1)
        finally:
            self.cards_container.setUpdatesEnabled(True)

    def refresh_memos(self) -> None:
        if hasattr(self, "cards_container"):
            self.cards_container.setUpdatesEnabled(False)
        try:
            # Clear existing grid items and detach immediately
            while self.cards_grid.count():
                item = self.cards_grid.takeAt(0)
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
            self._cards = []

            for r in range(self.cards_grid.rowCount()):
                self.cards_grid.setRowStretch(r, 0)
            for c in range(self.cards_grid.columnCount()):
                self.cards_grid.setColumnStretch(c, 0)

            parent = self._owner_window
            if not parent or not hasattr(parent, "repository"):
                return

            db_memos = parent._ordered_memos(parent.repository.list_memos()) if hasattr(parent, "_ordered_memos") else parent.repository.list_memos()
            active_entries = {}
            active_ids = set()
            if hasattr(parent, "_active_memo_dialogs"):
                for k, dlg in parent._active_memo_dialogs.items():
                    if dlg and getattr(dlg, "entry", None) and dlg.entry.entry_id:
                        active_entries[dlg.entry.entry_id] = dlg.entry
                        if dlg.isVisible():
                            active_ids.add(dlg.entry.entry_id)

            memos = []
            for m in db_memos:
                live_entry = active_entries.get(m.entry_id, m)
                if getattr(live_entry, "memo_group", "") == self.group_id:
                    memos.append(live_entry)

            self._current_memos = memos
            self.count_badge.setText(f"{len(memos)}개")

            if not memos:
                empty_lbl = QLabel("그룹에 속한 메모가 없습니다.\n\n상단의 [+ 메모]를 누르거나\n기존 메모 우클릭으로 이 그룹을 지정하세요.")
                empty_lbl.setAlignment(Qt.AlignCenter)
                empty_lbl.setStyleSheet("font-size: 12px; color: rgba(0,0,0,0.45); padding: 40px 10px; line-height: 1.5; background: transparent;")
                self.cards_grid.addWidget(empty_lbl, 0, 0)
                return

            if self.view_mode == "list":
                self.cards_grid.setAlignment(Qt.AlignTop)
                self.cards_grid.setSpacing(6)
                self.cards_grid.setColumnStretch(0, 1)
                if hasattr(self, "cards_container") and hasattr(self, "scroll") and self.scroll.viewport():
                    vp_w = self.scroll.viewport().width()
                    if vp_w > 0:
                        self.cards_container.setMaximumWidth(vp_w)
                for i, memo in enumerate(memos):
                    is_open = memo.entry_id in active_ids
                    card = MiniMemoCardWidget(memo, is_open_on_desktop=is_open, view_mode="list", parent=self.cards_container)
                    card.clicked.connect(self._on_card_clicked)
                    card.doubleClicked.connect(self._on_card_double_clicked)
                    card.reordered.connect(self._on_memo_reordered)
                    card.requestToggleOpen.connect(self._on_card_toggle_open)
                    card.requestRemoveFromGroup.connect(self._on_card_remove_group)
                    card.requestDeleteMemo.connect(self._on_card_delete_memo)
                    self.cards_grid.addWidget(card, i, 0)
                    self._cards.append(card)
                self.cards_grid.setRowStretch(len(memos), 1)
            else:
                self.cards_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                self.cards_grid.setSpacing(8)
                cols = max(1, (self.width() - 28) // 115)
                self._current_cols = cols
                for i, memo in enumerate(memos):
                    is_open = memo.entry_id in active_ids
                    card = MiniMemoCardWidget(memo, is_open_on_desktop=is_open, view_mode="card", parent=self.cards_container)
                    card.clicked.connect(self._on_card_clicked)
                    card.doubleClicked.connect(self._on_card_double_clicked)
                    card.reordered.connect(self._on_memo_reordered)
                    card.requestToggleOpen.connect(self._on_card_toggle_open)
                    card.requestRemoveFromGroup.connect(self._on_card_remove_group)
                    card.requestDeleteMemo.connect(self._on_card_delete_memo)
                    row = i // cols
                    col = i % cols
                    self.cards_grid.addWidget(card, row, col, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                    self._cards.append(card)
                total_rows = (len(memos) + cols - 1) // cols
                self.cards_grid.setRowStretch(total_rows, 1)

            self._preview_hidden = None
            self._update_preview_visibility()
        finally:
            if hasattr(self, "cards_container"):
                self.cards_container.setUpdatesEnabled(True)

    def _on_memo_reordered(self, source_id: int, target_id: int, before: bool) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "repository"):
            return
        source_id = int(source_id)
        target_id = int(target_id)
        source_entry = parent.repository.get_entry(source_id)
        if source_entry and getattr(source_entry, "memo_group", "") != self.group_id:
            source_entry.memo_group = self.group_id
            parent.repository.upsert_entry(source_entry)
            parent.repository.save()
        if hasattr(parent, "_on_memo_card_reordered"):
            parent._on_memo_card_reordered(source_id, target_id, before)
        self.refresh_memos()

    def _on_card_clicked(self, entry: CalendarEntry) -> None:
        self._on_card_toggle_open(entry)

    def _on_card_double_clicked(self, entry: CalendarEntry) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "_active_memo_dialogs"):
            return
        key = int(entry.entry_id) if entry.entry_id is not None else None
        dlg = None
        if key is not None and key in parent._active_memo_dialogs:
            dlg = parent._active_memo_dialogs.get(key)
        elif entry.entry_id is not None and str(entry.entry_id) in parent._active_memo_dialogs:
            dlg = parent._active_memo_dialogs.get(str(entry.entry_id))

        if dlg and dlg.isVisible():
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        else:
            self._open_memo(entry)
        self.update_open_statuses()

    def _open_memo(self, entry: CalendarEntry) -> None:
        if self._owner_window and hasattr(self._owner_window, "_edit_entry"):
            self._owner_window._edit_entry(EntryType.MEMO, entry)
            self.update_open_statuses()

    def _on_card_toggle_open(self, entry: CalendarEntry) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "_active_memo_dialogs"):
            return
        key = int(entry.entry_id) if entry.entry_id is not None else None
        dlg = None
        if key is not None and key in parent._active_memo_dialogs:
            dlg = parent._active_memo_dialogs.get(key)
        elif entry.entry_id is not None and str(entry.entry_id) in parent._active_memo_dialogs:
            dlg = parent._active_memo_dialogs.get(str(entry.entry_id))

        if dlg and dlg.isVisible():
            dlg.close()
        else:
            self._open_memo(entry)
        self.update_open_statuses()

    def _on_card_remove_group(self, entry: CalendarEntry) -> None:
        parent = self._owner_window
        if parent and hasattr(parent, "repository"):
            entry.memo_group = ""
            parent.repository.upsert_entry(entry)
            parent.repository.save()
            self.refresh_memos()

    def _on_card_delete_memo(self, entry: CalendarEntry) -> None:
        parent = self._owner_window
        if parent and hasattr(parent, "repository") and entry.entry_id:
            if QMessageBox.question(self, "메모 삭제", f"'{entry.title or '메모'}'를 삭제할까요?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                target_id = entry.entry_id
                parent.repository.delete_entry(target_id)
                if hasattr(parent, "_load_memo_order_ids") and hasattr(parent, "_save_memo_order_ids"):
                    ids = [m_id for m_id in parent._load_memo_order_ids() if m_id != int(target_id)]
                    parent._save_memo_order_ids(ids, persist=False)
                if hasattr(parent, "_active_memo_dialogs"):
                    for k in [target_id, int(target_id)]:
                        if k in parent._active_memo_dialogs:
                            dlg = parent._active_memo_dialogs.pop(k)
                            try:
                                setattr(dlg, "_is_deleted", True)
                                dlg.close()
                            except Exception:
                                pass
                if hasattr(parent, "_sync_open_memo_ids"):
                    parent._sync_open_memo_ids(persist=False)
                parent.repository.save()
                self.refresh_memos()
                if hasattr(parent, "refresh"):
                    try:
                        parent.refresh()
                    except Exception:
                        pass
                if hasattr(parent, "_refresh_all_group_dialogs"):
                    try:
                        parent._refresh_all_group_dialogs()
                    except Exception:
                        pass

    def _on_add_memo_clicked(self) -> None:
        parent = self._owner_window
        if parent and hasattr(parent, "_edit_entry"):
            from taskcalendar.models import CalendarEntry, EntryType
            new_memo = CalendarEntry(EntryType.MEMO, title="새 메모", memo_group=self.group_id)
            saved = parent.repository.upsert_entry(new_memo)
            parent.repository.save()
            parent._edit_entry(EntryType.MEMO, saved)
            self.refresh_memos()

    def _open_all_memos(self) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "repository"):
            return
        memos = [m for m in parent.repository.list_memos() if getattr(m, "memo_group", "") == self.group_id]
        if not memos:
            return
        setattr(parent, "_batch_updating_memos", True)
        self.setUpdatesEnabled(False)
        try:
            for m in memos:
                parent._edit_entry(EntryType.MEMO, m)
        finally:
            setattr(parent, "_batch_updating_memos", False)
            self.setUpdatesEnabled(True)
        if hasattr(parent, "_sync_open_memo_ids"):
            parent._sync_open_memo_ids(persist=True)
        if hasattr(parent, "_refresh_all_group_dialogs"):
            parent._refresh_all_group_dialogs(status_only=True)
        else:
            self.update_open_statuses()

    def _close_all_memos(self) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "_active_memo_dialogs"):
            return
        memos = [m for m in parent.repository.list_memos() if getattr(m, "memo_group", "") == self.group_id]
        if not memos:
            return
        setattr(parent, "_batch_updating_memos", True)
        self.setUpdatesEnabled(False)
        try:
            for m in memos:
                if m.entry_id in parent._active_memo_dialogs:
                    dlg = parent._active_memo_dialogs[m.entry_id]
                    if dlg and dlg.isVisible():
                        dlg.close()
        finally:
            setattr(parent, "_batch_updating_memos", False)
            self.setUpdatesEnabled(True)
        if hasattr(parent, "_sync_open_memo_ids"):
            parent._sync_open_memo_ids(persist=True)
        if hasattr(parent, "refresh"):
            try:
                parent.refresh()
            except Exception:
                pass
        if hasattr(parent, "_refresh_all_group_dialogs"):
            parent._refresh_all_group_dialogs(status_only=True)
        else:
            self.update_open_statuses()

    def _update_title_input_width(self) -> None:
        if hasattr(self, "title_input") and self.title_input is not None:
            text = self.title_input.text() or self.title_input.placeholderText() or "그룹 이름"
            fm = self.title_input.fontMetrics()
            w = fm.horizontalAdvance(text) + 20
            occupied = 160 if self.width() < 480 else 380
            max_w = max(60, self.width() - occupied)
            self.title_input.setFixedWidth(min(max_w, max(60, w)))

    def _auto_save_to_db(self) -> None:
        self._save_group_state()

    def _on_title_changed(self, new_title: str) -> None:
        self.group_title = new_title.strip() or "새 그룹"
        self.group_dict["title"] = self.group_title
        self._update_title_input_width()
        self._debounced_save(400)

    def _update_view_mode_btn(self) -> None:
        if self.view_mode == "card":
            self.view_mode_btn.setIcon(QIcon(str(asset_path("view_list.svg"))))
            self.view_mode_btn.setToolTip("한줄 목록으로 보기")
        else:
            self.view_mode_btn.setIcon(QIcon(str(asset_path("view_grid.svg"))))
            self.view_mode_btn.setToolTip("카드형으로 보기")

    def _update_pin_btn(self) -> None:
        if self._is_floating:
            self.pin_btn.setIcon(QIcon(str(asset_path("pin_active.svg"))))
            self.pin_btn.setToolTip("항상 위에 고정 해제")
        else:
            self.pin_btn.setIcon(QIcon(str(asset_path("pin_inactive.svg"))))
            self.pin_btn.setToolTip("항상 위에 고정")

    def _update_collapse_btn(self) -> None:
        if getattr(self, "_is_collapsed", False):
            self.collapse_btn.setIcon(QIcon(str(asset_path("memo_maximize.svg"))))
        else:
            self.collapse_btn.setIcon(QIcon(str(asset_path("memo_minimize.svg"))))

    def _toggle_pin(self) -> None:
        self._is_floating = not self._is_floating
        self.group_dict["is_floating"] = self._is_floating
        self._set_topmost_native(self._is_floating)
        self._update_pin_btn()
        self._save_group_state()

    def _toggle_collapse(self) -> None:
        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            curr_w = self.width()
            col_w = getattr(self, "_collapsed_width", None)
            if col_w is None or curr_w != col_w:
                self._expanded_width = curr_w
            self._expanded_height = max(180, getattr(self, "_expanded_height", self.height()))
            self.content_wrap.hide()
            self.setMinimumHeight(36)
            self.setMaximumHeight(36)
            target_w = getattr(self, "_collapsed_width", None) or self._expanded_width
            self._resize_with_anchor(target_w, 36)
        else:
            curr_w = self.width()
            exp_w = getattr(self, "_expanded_width", None)
            if exp_w is None or curr_w != exp_w:
                self._collapsed_width = curr_w
            self.content_wrap.show()
            self.setMinimumHeight(180)
            self.setMaximumHeight(16777215)
            target_w = getattr(self, "_expanded_width", 360)
            target_h = getattr(self, "_expanded_height", 420)
            self._resize_with_anchor(target_w, target_h)
        self._update_collapse_btn()
        self._apply_theme(self.group_color)
        self._debounced_save(250)

    def _resize_with_anchor(self, width: int, height: int) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        screen_right = avail.x() + avail.width()

        old_right = self.x() + self.width()
        target_x = self.x()

        if getattr(self, "_expand_anchor_right", False):
            is_expanding = width > self.width()
            is_collapsing = width < self.width()

            if is_expanding:
                if (target_x + width > screen_right) or getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                    target_x = old_right - width
                    self._anchored_to_right = True
                else:
                    self._anchored_to_right = False
            elif is_collapsing:
                if getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                    target_x = old_right - width
                    self._anchored_to_right = True
                else:
                    self._anchored_to_right = False

            if getattr(self, "_anchored_to_right", False):
                if target_x + width > screen_right:
                    target_x = screen_right - width
                if target_x < avail.left():
                    target_x = avail.left()

        self.setGeometry(target_x, self.y(), width, height)

    def paintEvent(self, event) -> None:
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QStylePainter(self)
        painter.drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt)
        super().paintEvent(event)

        if not getattr(self, "_is_collapsed", False):
            p = QPainter(self)
            theme = MEMO_THEMES.get(getattr(self, "group_color", "yellow"), MEMO_THEMES["yellow"])
            grip_color = QColor(theme.get("border", "#d5c880"))
            p.setPen(QPen(grip_color, 1.5))
            w = self.width()
            h = self.height()
            p.drawLine(w - 4, h - 14, w - 14, h - 4)
            p.drawLine(w - 4, h - 10, w - 10, h - 4)
            p.drawLine(w - 4, h - 6, w - 6, h - 4)

    def _toggle_view_mode(self) -> None:
        self.view_mode = "list" if self.view_mode == "card" else "card"
        self.group_dict["view_mode"] = self.view_mode
        self._update_view_mode_btn()
        if hasattr(self, "cards_container") and hasattr(self, "scroll") and self.scroll.viewport():
            if self.view_mode == "list":
                vp_w = self.scroll.viewport().width()
                if vp_w > 0:
                    self.cards_container.setMaximumWidth(vp_w)
            else:
                self.cards_container.setMaximumWidth(16777215)
        self._save_group_state()
        self.refresh_memos()

    def _debounced_save(self, delay_ms: int = 250) -> None:
        self._save_group_state(persist=False)
        if not hasattr(self, "_group_save_timer"):
            self._group_save_timer = QTimer(self)
            self._group_save_timer.setSingleShot(True)
            self._group_save_timer.timeout.connect(self._flush_save_state)
        self._group_save_timer.start(delay_ms)

    def _flush_save_state(self) -> None:
        parent = self._owner_window
        if parent and hasattr(parent, "repository"):
            parent.repository.save()

    def _save_group_state(self, persist: bool = True) -> None:
        parent = self._owner_window
        if parent and hasattr(parent, "repository"):
            curr_geo = self.geometry()
            is_col = getattr(self, "_is_collapsed", False)
            w_val = getattr(self, "_expanded_width", curr_geo.width())
            h_val = getattr(self, "_expanded_height", curr_geo.height())
            save_x = curr_geo.x()
            if is_col and getattr(self, "_expand_anchor_right", False):
                screen = self.screen() or QApplication.primaryScreen()
                avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
                screen_right = avail.x() + avail.width()
                if getattr(self, "_anchored_to_right", False) or (curr_geo.x() + w_val > screen_right) or (curr_geo.x() + curr_geo.width() >= screen_right - 16):
                    save_x = curr_geo.x() + curr_geo.width() - w_val
            self.group_dict["geo"] = f"{save_x},{curr_geo.y()},{w_val},{h_val}"
            self.group_dict["color"] = self.group_color
            self.group_dict["is_floating"] = self._is_floating
            self.group_dict["view_mode"] = self.view_mode
            self.group_dict["is_collapsed"] = self._is_collapsed
            if getattr(self, "_collapsed_width", None) is not None:
                self.group_dict["collapsed_width"] = self._collapsed_width
            parent.repository.upsert_memo_group(self.group_dict, persist=persist)

    def _set_topmost_native(self, topmost: bool) -> None:
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            HWND_TOPMOST = ctypes.c_void_p(-1)
            HWND_NOTOPMOST = ctypes.c_void_p(-2)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                HWND_TOPMOST if topmost else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_header_mode()
        self._update_title_input_width()
        if hasattr(self, "cards_container") and hasattr(self, "scroll") and self.scroll.viewport():
            if self.view_mode == "list":
                vp_w = self.scroll.viewport().width()
                if vp_w > 0:
                    self.cards_container.setMaximumWidth(vp_w)
            else:
                self.cards_container.setMaximumWidth(16777215)
        self._update_preview_visibility()
        self._realign_cards_on_resize()

    def _other_window_geometries(self) -> list[QRect]:
        geos: list[QRect] = []
        parent = getattr(self, "_owner_window", None) or self.parent()
        if not parent:
            return geos
        for attr in ("_active_group_dialogs", "_active_memo_dialogs"):
            dialogs = getattr(parent, attr, None)
            if not dialogs:
                continue
            for dlg in list(dialogs.values()):
                if dlg is not None and dlg is not self and dlg.isVisible():
                    geos.append(dlg.geometry())
        return geos

    def _screen_geometry(self) -> QRect:
        screen = self.screen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def _start_window_drag(self, global_pos: QPoint) -> None:
        self._drag_pos = global_pos - self.frameGeometry().topLeft()

    def _perform_window_drag(self, global_pos: QPoint) -> None:
        if hasattr(self, "_drag_pos") and self._drag_pos is not None:
            target_pos = global_pos - self._drag_pos
            curr_geo = QRect(target_pos, self.size())
            snapped_pos = snap_window_rect(curr_geo, self._other_window_geometries(), self._screen_geometry(), threshold=16)
            self.move(snapped_pos)

    def _end_window_drag(self) -> None:
        if hasattr(self, "_drag_pos") and self._drag_pos is not None:
            self._drag_pos = None
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            screen_right = avail.x() + avail.width()
            exp_w = getattr(self, "_expanded_width", 360)
            self._anchored_to_right = (self.x() + exp_w > screen_right) or (self.x() + self.width() >= screen_right - 16)
        self._debounced_save(250)

    def _get_resize_direction(self, global_pos: QPoint) -> str | None:
        local_pos = self.mapFromGlobal(global_pos)
        w = self.width()
        h = self.height()
        border = 10

        if not (0 <= local_pos.x() <= w and 0 <= local_pos.y() <= h):
            return None

        if getattr(self, "_is_collapsed", False):
            if local_pos.x() >= w - border:
                return "r"
            elif local_pos.x() <= border:
                return "l"
            return None

        # Corners
        if local_pos.x() >= w - border and local_pos.y() >= h - border:
            return "br"
        if local_pos.x() <= border and local_pos.y() >= h - border:
            return "bl"

        # Edges
        if local_pos.x() >= w - border:
            return "r"
        if local_pos.x() <= border:
            return "l"
        if local_pos.y() >= h - border:
            return "b"

        return None

    def _perform_resize(self, global_pos: QPoint) -> None:
        if not self._resize_dir:
            return
        delta = global_pos - self._initial_mouse_pos
        geom = QRect(self._initial_geometry)

        if self._resize_dir == "r":
            geom.setWidth(max(250, geom.width() + delta.x()))
        elif self._resize_dir == "l":
            new_w = max(250, geom.width() - delta.x())
            new_x = geom.right() - new_w
            geom.setX(new_x)
            geom.setWidth(new_w)
        elif self._resize_dir == "b" and not self._is_collapsed:
            geom.setHeight(max(180, geom.height() + delta.y()))
        elif self._resize_dir == "br" and not self._is_collapsed:
            geom.setWidth(max(250, geom.width() + delta.x()))
            geom.setHeight(max(180, geom.height() + delta.y()))
        elif self._resize_dir == "bl" and not self._is_collapsed:
            new_w = max(250, geom.width() - delta.x())
            new_x = geom.right() - new_w
            geom.setX(new_x)
            geom.setWidth(new_w)
            geom.setHeight(max(180, geom.height() + delta.y()))

        geom = snap_resize_rect(
            geom,
            self._resize_dir,
            self._other_window_geometries(),
            self._screen_geometry(),
            threshold=16,
        )
        if geom.width() < 250:
            geom.setWidth(250)
        if not self._is_collapsed and geom.height() < 180:
            geom.setHeight(180)

        self.setGeometry(geom)
        if not self._is_collapsed:
            self._expanded_width = geom.width()
            self._expanded_height = geom.height()
        else:
            self._collapsed_width = geom.width()

    def eventFilter(self, watched, event) -> bool:
        # 1. Resize & Hover cursor handling across all watched widgets
        if event.type() == QEvent.Type.MouseMove:
            if event.buttons() & Qt.MouseButton.LeftButton:
                if self._resize_dir:
                    self._perform_resize(event.globalPosition().toPoint())
                    return True
                elif hasattr(self, "_drag_pos") and self._drag_pos is not None:
                    self._perform_window_drag(event.globalPosition().toPoint())
                    return True
            else:
                r_dir = self._get_resize_direction(event.globalPosition().toPoint())
                if r_dir in ("r", "l"):
                    watched.setCursor(Qt.CursorShape.SizeHorCursor)
                elif r_dir == "b":
                    watched.setCursor(Qt.CursorShape.SizeVerCursor)
                elif r_dir == "br":
                    watched.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif r_dir == "bl":
                    watched.setCursor(Qt.CursorShape.SizeBDiagCursor)
                else:
                    watched.unsetCursor()

        elif event.type() == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                r_dir = self._get_resize_direction(event.globalPosition().toPoint())
                if self._handle_collapsed_edge_dblclick(r_dir):
                    return True
        elif event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                r_dir = self._get_resize_direction(event.globalPosition().toPoint())
                if r_dir:
                    self._resize_dir = r_dir
                    self._initial_geometry = self.geometry()
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                    return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._resize_dir:
                    self._resize_dir = None
                    self._debounced_save(250)
                    return True

        # 2. Header drag, context menu, and double-click collapse/expand
        header_targets = (getattr(self, "header", None), getattr(self, "icon_lbl", None), getattr(self, "count_badge", None))
        if watched in header_targets:
            if event.type() == QEvent.Type.MouseButtonDblClick:
                if event.button() == Qt.MouseButton.LeftButton:
                    r_dir = self._get_resize_direction(event.globalPosition().toPoint())
                    if self._handle_collapsed_edge_dblclick(r_dir):
                        return True
                    self._toggle_collapse()
                    return True
            elif event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    r_dir = self._get_resize_direction(event.globalPosition().toPoint())
                    if not r_dir:
                        self._start_window_drag(event.globalPosition().toPoint())
                        return True
                elif event.button() == Qt.MouseButton.RightButton:
                    self._show_context_menu(event.globalPosition().toPoint())
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._end_window_drag()
                    return True

        # 3. Drag-and-drop reordering
        drop_targets = (getattr(self, "cards_container", None), getattr(self, "scroll", None))
        if watched in drop_targets:
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasFormat(MiniMemoCardWidget._MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                data = event.mimeData().data(MiniMemoCardWidget._MIME_TYPE)
                if data:
                    try:
                        source_id = int(bytes(data).decode("utf-8"))
                        memos = getattr(self, "_current_memos", [])
                        if memos:
                            last_id = int(memos[-1].entry_id)
                            if source_id != last_id:
                                self._on_memo_reordered(source_id, last_id, before=False)
                            else:
                                self.refresh_memos()
                        else:
                            parent = self._owner_window
                            if parent and hasattr(parent, "repository"):
                                source_entry = parent.repository.get_entry(source_id)
                                if source_entry:
                                    source_entry.memo_group = self.group_id
                                    parent.repository.upsert_entry(source_entry)
                                    parent.repository.save()
                                    self.refresh_memos()
                        event.acceptProposedAction()
                        return True
                    except Exception:
                        pass

        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            r_dir = self._get_resize_direction(event.globalPosition().toPoint())
            if r_dir:
                self._resize_dir = r_dir
                self._initial_geometry = self.geometry()
                self._initial_mouse_pos = event.globalPosition().toPoint()
                event.accept()
                return

            if event.position().toPoint().y() <= 36:
                self._start_window_drag(event.globalPosition().toPoint())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._resize_dir:
                self._perform_resize(event.globalPosition().toPoint())
                event.accept()
                return
            elif hasattr(self, "_drag_pos") and self._drag_pos is not None:
                self._perform_window_drag(event.globalPosition().toPoint())
                event.accept()
                return
        else:
            r_dir = self._get_resize_direction(event.globalPosition().toPoint())
            if r_dir in ("r", "l"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif r_dir == "b":
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif r_dir == "br":
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif r_dir == "bl":
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was_resizing = self._resize_dir is not None
        self._resize_dir = None
        self._end_window_drag()
        if was_resizing:
            self._debounced_save(250)
        super().mouseReleaseEvent(event)

    def _handle_collapsed_edge_dblclick(self, r_dir: str | None) -> bool:
        if not getattr(self, "_is_collapsed", False) or r_dir not in ("r", "l"):
            return False
        target_w = max(200, getattr(self, "_expanded_width", 360) or 360)
        curr_w = self.width()
        if abs(curr_w - target_w) <= 10 and getattr(self, "_prev_compact_width", None):
            new_w = self._prev_compact_width
        else:
            self._prev_compact_width = curr_w
            new_w = target_w

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        screen_right = avail.x() + avail.width()
        old_right = self.x() + self.width()

        should_shift_left = (r_dir == "l")
        if not should_shift_left and getattr(self, "_expand_anchor_right", False):
            if (self.x() + new_w > screen_right) or getattr(self, "_anchored_to_right", False) or (old_right >= screen_right - 16):
                should_shift_left = True
                self._anchored_to_right = True

        if should_shift_left:
            delta_w = new_w - curr_w
            new_x = self.x() - delta_w
            if new_x + new_w > screen_right:
                new_x = screen_right - new_w
            if new_x < avail.left():
                new_x = avail.left()
            self.move(new_x, self.y())
        self._collapsed_width = new_w
        self.resize(new_w, self.height())
        self._debounced_save(250)
        return True

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            r_dir = self._get_resize_direction(event.globalPosition().toPoint())
            if self._handle_collapsed_edge_dblclick(r_dir):
                event.accept()
                return
            pos = event.position().toPoint()
            hdr_h = self.header.height() if hasattr(self, "header") and self.header else 36
            if pos.y() <= hdr_h:
                self._toggle_collapse()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def closeEvent(self, event) -> None:
        if hasattr(self, "_group_save_timer") and self._group_save_timer.isActive():
            self._group_save_timer.stop()
        self._save_group_state(persist=True)
        parent = self._owner_window
        if parent and getattr(parent, "_is_app_quitting", False):
            super().closeEvent(event)
            return
        if parent and hasattr(parent, "_active_group_dialogs") and self.group_id in parent._active_group_dialogs:
            parent._active_group_dialogs.pop(self.group_id, None)
            if hasattr(parent, "_sync_open_group_ids"):
                parent._sync_open_group_ids(persist=True)
        super().closeEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 0px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 20px 6px 16px;
                font-size: 12px;
                color: #222222;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)
        add_act = menu.addAction("➕ 새 메모 추가")
        add_act.triggered.connect(self._on_add_memo_clicked)
        menu.addSeparator()

        open_act = menu.addAction("모든 메모 열기")
        open_act.triggered.connect(self._open_all_memos)
        close_act = menu.addAction("모든 메모 닫기")
        close_act.triggered.connect(self._close_all_memos)
        menu.addSeparator()

        mode_text = "📋 한줄 목록으로 보기" if self.view_mode == "card" else "🗂️ 카드형으로 보기"
        mode_act = menu.addAction(mode_text)
        mode_act.triggered.connect(self._toggle_view_mode)
        menu.addSeparator()

        color_menu = menu.addMenu("🎨 그룹 색상 변경")
        color_menu.setStyleSheet(menu.styleSheet())
        for k, th in MEMO_THEMES.items():
            act = color_menu.addAction(th['name'])
            act.triggered.connect(lambda _=False, key=k: (self._apply_theme(key), self._save_group_state()))

        pin_act = menu.addAction("📌 항상 위에 고정")
        pin_act.setCheckable(True)
        pin_act.setChecked(self._is_floating)
        pin_act.triggered.connect(self._toggle_pin)

        menu.addSeparator()
        del_act = menu.addAction("🗑️ 그룹 삭제")
        del_act.triggered.connect(self._on_delete_group)

        menu.exec(global_pos)

    def _on_delete_group(self) -> None:
        parent = self._owner_window
        if not parent or not hasattr(parent, "repository"):
            return
        reply = QMessageBox.question(
            self,
            "그룹 삭제",
            f"'{self.group_title}' 그룹을 삭제할까요?\n\n(그룹에 속한 메모들은 삭제되지 않고 그룹 지정만 해제됩니다.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            parent.repository.delete_memo_group(self.group_id, delete_memos=False)
            self.close()
            if hasattr(parent, "refresh"):
                parent.refresh()
            if hasattr(parent, "_refresh_all_group_dialogs"):
                parent._refresh_all_group_dialogs()


class EntryViewDialog(QDialog):
    def __init__(
        self,
        parent,
        entry_type: EntryType,
        entry: CalendarEntry,
        on_download_attachment: Callable[[str], None] | None = None,
        on_edit_entry: Callable[[CalendarEntry], None] | None = None,
    ) -> None:
        super().__init__(parent)
        logger.info(f"[EntryViewDialog.__init__] entry_type={entry_type}, id={entry.entry_id if entry else None}, title='{entry.title if entry else ''}'")
        self.palette = resolve_palette(parent)
        self.entry_type = entry_type
        self.entry = entry
        self._on_download_attachment = on_download_attachment
        self._on_edit_entry = on_edit_entry
        if entry_type != EntryType.MEMO:
            self.setWindowModality(Qt.WindowModality.WindowModal)
        else:
            self.setModal(False)
        self.setObjectName("entryDialog")
        self.setWindowTitle("메모 보기" if entry_type == EntryType.MEMO else "일정 보기")
        self.setWindowIcon(_dialog_icon())
        self.dialog_width = 620 if entry_type != EntryType.MEMO else 500
        self.resize(self.dialog_width, 560 if entry_type != EntryType.MEMO else 420)
        self._apply_styles()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        if self.entry_type == EntryType.MEMO:
            # Style the dialog to look like a classic yellow post-it note
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
            self.setStyleSheet("QDialog#entryDialog { background-color: #fff7c2; border: 1px solid #d5c880; }")
            self.setMouseTracking(True)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            # Header bar
            from PySide6.QtWidgets import QWidget
            self.header = QWidget()
            self.header.setFixedHeight(32)
            self.header.setStyleSheet("background-color: #f5e99f;")
            h_layout = QHBoxLayout(self.header)
            h_layout.setContentsMargins(12, 0, 12, 0)

            title_label = QLabel("메모 보기")
            title_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #5a5120;")
            h_layout.addWidget(title_label)
            h_layout.addStretch(1)

            close_btn = QPushButton()
            close_btn.setIcon(QIcon(str(asset_path("memo_close.svg"))))
            close_btn.setIconSize(QSize(11, 11))
            close_btn.setFixedSize(20, 20)
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setStyleSheet("background: transparent; border: none;")
            close_btn.clicked.connect(self.reject)
            h_layout.addWidget(close_btn)

            root.addWidget(self.header)

            # Content container
            content_wrap = QWidget()
            content_wrap.setMouseTracking(True)
            content_layout = QVBoxLayout(content_wrap)
            content_layout.setContentsMargins(16, 12, 16, 12)
            content_layout.setSpacing(10)

            title_layout = QHBoxLayout()
            title_val = QLineEdit((entry.title or "제목 없음").strip())
            title_val.setReadOnly(True)
            title_val.setStyleSheet("font-size: 13px; font-weight: bold; border: none; border-bottom: 1px solid rgba(0, 0, 0, 0.08); background: transparent; padding: 4px 0px; color: #2c2c2c;")
            title_layout.addWidget(title_val, 1)
            content_layout.addLayout(title_layout)

            from PySide6.QtWidgets import QTextEdit
            self.content_view = QTextEdit()
            self.content_view.setReadOnly(True)
            desc_val = entry.description or ""
            if desc_val.strip().startswith("<"):
                self.content_view.setHtml(desc_val)
            else:
                self.content_view.setPlainText(desc_val)
            self.content_view.setStyleSheet("border: none; background: transparent; font-size: 13px; padding: 0px; color: #2c2c2c;")
            self.content_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            content_layout.addWidget(self.content_view, 1)

            bottom_row = QHBoxLayout()
            bottom_row.setContentsMargins(0, 0, 0, 0)
            bottom_row.setSpacing(8)

            attach_layout = QHBoxLayout()
            attach_layout.setSpacing(4)
            if entry.attachments:
                attach_lbl = QLabel(f"첨부 ({len(entry.attachments)}):")
                attach_lbl.setObjectName("muted")
                attach_layout.addWidget(attach_lbl)
                for attachment in entry.attachments:
                    button = QPushButton(Path(str(attachment)).name)
                    button.setCursor(Qt.PointingHandCursor)
                    button.setObjectName("attachLink")
                    button.setToolTip(str(attachment))
                    button.clicked.connect(lambda _checked=False, a=attachment: self._download_attachment(a))
                    attach_layout.addWidget(button)
            bottom_row.addLayout(attach_layout, 1)

            btn_layout = QHBoxLayout()
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.setSpacing(6)
            edit_btn = QPushButton("수정")
            edit_btn.setObjectName("secondary")
            edit_btn.clicked.connect(self._edit_entry)
            close_btn = QPushButton("닫기")
            close_btn.setObjectName("primary")
            close_btn.clicked.connect(self.accept)
            btn_layout.addWidget(edit_btn)
            btn_layout.addWidget(close_btn)
            bottom_row.addLayout(btn_layout)

            content_layout.addLayout(bottom_row)
            root.addWidget(content_wrap, 1)

            self._resize_dir = None

            # Setup shortcuts for finding
            self._find_shortcut = QShortcut(QKeySequence.Find, self)
            self._find_shortcut.activated.connect(self._prompt_find)
            self._find_next_shortcut = QShortcut(QKeySequence.FindNext, self)
            self._find_next_shortcut.activated.connect(self._find_next)
            return

        info_card, info_layout = self._create_card()
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(8)
        if self.entry_type == EntryType.MEMO:
            info_row.addWidget(self._muted("제목"))
            title = QLineEdit((entry.title or "메모").strip())
            title.setReadOnly(True)
            info_row.addWidget(title, 1)
        else:
            info_row.addWidget(self._muted("일시"))
            when_value = QLineEdit(self._entry_when_text(entry))
            when_value.setReadOnly(True)
            info_row.addWidget(when_value, 1)
        info_layout.addLayout(info_row)
        root.addWidget(info_card)

        content_card, content_layout = self._create_card()
        content_layout.addWidget(self._section_title("내용"))

        from PySide6.QtWidgets import QTextEdit
        self.content_view = QTextEdit()
        self.content_view.setReadOnly(True)
        desc_val = entry.description or ""
        if desc_val.strip().startswith("<"):
            self.content_view.setHtml(desc_val)
        else:
            self.content_view.setPlainText(desc_val)
        self.content_view.setMinimumHeight(240 if entry_type != EntryType.MEMO else 160)
        self.content_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        content_layout.addWidget(self.content_view, 1)
        root.addWidget(content_card, 1)
        self._find_term = ""

        if entry.attachments:
            attach_card, attach_layout = self._create_card(soft=True)
            attach_layout.addWidget(self._section_title(f"첨부파일 {len(entry.attachments)}건"))
            for attachment in entry.attachments:
                button = QPushButton(Path(str(attachment)).name)
                button.setCursor(Qt.PointingHandCursor)
                button.setObjectName("attachLink")
                button.setToolTip(str(attachment))
                button.setMinimumHeight(28)
                button.setMaximumHeight(32)
                button.clicked.connect(lambda _checked=False, a=attachment: self._download_attachment(a))
                attach_layout.addWidget(button)
            root.addWidget(attach_card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        edit_btn = buttons.addButton("수정", QDialogButtonBox.ButtonRole.ActionRole)
        edit_btn.setObjectName("secondary")
        edit_btn.clicked.connect(self._edit_entry)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setObjectName("primary")
            close_btn.setText("닫기")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.activated.connect(self._prompt_find)
        self._find_next_shortcut = QShortcut(QKeySequence.FindNext, self)
        self._find_next_shortcut.activated.connect(self._find_next)

    @staticmethod
    def _entry_when_text(entry: CalendarEntry) -> str:
        start_day = entry.start_date or entry.day
        end_day = entry.end_date or entry.day
        if start_day is None:
            return "-"
        base = start_day.isoformat()
        if end_day and end_day != start_day:
            base = f"{start_day.isoformat()} ~ {end_day.isoformat()}"
        if entry.start_time:
            base = f"{base} {entry.start_time}"
        elif entry.all_day:
            base = f"{base} (종일)"
        return base

    def _prompt_find(self) -> None:
        text, ok = QInputDialog.getText(self, "찾기", "찾을 내용을 입력하세요:", text=self._find_term)
        if not ok:
            return
        self._find_term = text.strip()
        if not self._find_term:
            return
        cursor = self.content_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.content_view.setTextCursor(cursor)
        self._find_next()

    def _find_next(self) -> None:
        needle = self._find_term.strip()
        if not needle:
            self._prompt_find()
            return
        if self.content_view.find(needle):
            return
        cursor = self.content_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.content_view.setTextCursor(cursor)
        if not self.content_view.find(needle):
            QMessageBox.information(self, "찾기", f"'{needle}' 검색 결과가 없습니다.")

    def _download_attachment(self, stored_path: str) -> None:
        if self._on_download_attachment is None:
            QMessageBox.warning(self, "첨부파일", "다운로드 기능을 사용할 수 없습니다.")
            return
        self._on_download_attachment(stored_path)

    def _edit_entry(self) -> None:
        callback = getattr(self, "_on_edit_entry", None)
        if callback is None:
            QMessageBox.warning(self, "수정", "수정 기능을 사용할 수 없습니다.")
            return
        entry_to_edit = self.entry
        self.accept()
        QTimer.singleShot(50, lambda: callback(entry_to_edit))

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _create_card(self, soft: bool = False) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("softCard" if soft else "card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        return frame, layout

    def _create_grid_card(self, soft: bool = False) -> tuple[QFrame, QGridLayout]:
        frame = QFrame()
        frame.setObjectName("softCard" if soft else "card")
        layout = QGridLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(7)
        return frame, layout

    def _apply_styles(self) -> None:
        self.setStyleSheet(dialog_stylesheet(self.palette))

    def mousePressEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            rect = self.rect()
            border = 8
            self._resize_dir = None
            if pos.x() >= rect.width() - border and pos.y() >= rect.height() - border:
                self._resize_dir = "br"
            elif pos.x() <= border and pos.y() >= rect.height() - border:
                self._resize_dir = "bl"
            elif pos.x() >= rect.width() - border:
                self._resize_dir = "r"
            elif pos.x() <= border:
                self._resize_dir = "l"
            elif pos.y() >= rect.height() - border:
                self._resize_dir = "b"
            
            if self._resize_dir:
                self._initial_geometry = self.geometry()
                self._initial_mouse_pos = event.globalPosition().toPoint()
                event.accept()
                return
                
            if pos.y() <= 32:
                self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            pos = event.position().toPoint()
            rect = self.rect()
            border = 8
            
            if not event.buttons():
                if pos.x() >= rect.width() - border and pos.y() >= rect.height() - border:
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif pos.x() <= border and pos.y() >= rect.height() - border:
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif pos.x() >= rect.width() - border or pos.x() <= border:
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif pos.y() >= rect.height() - border:
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            
            if event.buttons() == Qt.MouseButton.LeftButton:
                if hasattr(self, "_resize_dir") and self._resize_dir:
                    delta = event.globalPosition().toPoint() - self._initial_mouse_pos
                    geom = QRect(self._initial_geometry)
                    if self._resize_dir == "r":
                        geom.setWidth(max(180, geom.width() + delta.x()))
                    elif self._resize_dir == "l":
                        new_w = max(180, geom.width() - delta.x())
                        new_x = (geom.x() + geom.width()) - new_w
                        geom.setX(new_x)
                        geom.setWidth(new_w)
                    elif self._resize_dir == "b":
                        geom.setHeight(max(150, geom.height() + delta.y()))
                    elif self._resize_dir == "br":
                        geom.setWidth(max(180, geom.width() + delta.x()))
                        geom.setHeight(max(150, geom.height() + delta.y()))
                    elif self._resize_dir == "bl":
                        new_w = max(180, geom.width() - delta.x())
                        new_x = (geom.x() + geom.width()) - new_w
                        geom.setX(new_x)
                        geom.setWidth(new_w)
                        geom.setHeight(max(150, geom.height() + delta.y()))
                    self.setGeometry(geom)
                    event.accept()
                    return
                elif hasattr(self, "_drag_position"):
                    self.move(event.globalPosition().toPoint() - self._drag_position)
                    event.accept()
                    return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            self._resize_dir = None
            if hasattr(self, "_drag_position"):
                delattr(self, "_drag_position")
        super().mouseReleaseEvent(event)
class SettingsDialog(QDialog):
    def __init__(
        self,
        parent,
        current_theme: str,
        current_shortcut: str,
        auto_start_enabled: bool,
        sticker_animation_enabled: bool,
        hide_completed_on_calendar: bool,
        auto_backup_enabled: bool,
        auto_backup_interval_days: int,
        auto_backup_keep_count: int,
        db_path: Path,
        current_memo_shortcut: str = "F4",
        initial_tab: str = "general",
        show_lunar_calendar: bool = True,
        show_solar_terms: bool = True,
        lunar_display_frequency: str = "all",
        memo_default_color: str = "yellow",
        memo_show_attachment_bar: bool = True,
        memo_default_floating: bool = False,
        memo_default_opacity: int = 100,
        memo_default_size: str = "380,360",
        memo_default_font_size: int = 11,
        memo_title_only: bool = True,
        memo_expand_anchor: str = "left",
        show_window_controls: bool = True,
        show_task_count_on_calendar: bool = True,
        calendar_sidebar_title_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self._db_path = db_path
        self.result: dict[str, object] | None = None
        self._current_shortcut = normalize_shortcut(current_shortcut)
        self._current_memo_shortcut = normalize_shortcut(current_memo_shortcut)
        shortcut_modifiers, shortcut_key = self._shortcut_parts(current_shortcut)
        memo_modifiers, memo_key = self._shortcut_parts(current_memo_shortcut)
        
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("환경설정")
        self.setWindowIcon(_dialog_icon())
        self.resize(700, 520)
        self.setFixedWidth(700)
        self.setStyleSheet(dialog_stylesheet(self.palette))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_row = QHBoxLayout()
        title = QLabel("환경설정")
        title.setObjectName("title")
        title_row.addWidget(title)
        ver_badge = QLabel(APP_VERSION)
        ver_badge.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {self.palette['muted']}; padding: 2px 6px; background: {self.palette['panel_alt']}; border-radius: 4px;")
        title_row.addWidget(ver_badge)
        title_row.addStretch(1)
        title_box.addLayout(title_row)
        subtitle = QLabel("기본, 캘린더, 스킨, 메모, 단축키, 데이터 설정을 여기에서 관리합니다.")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(subtitle)
        root.addLayout(title_box)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navSidebar")
        self.nav_list.setFixedWidth(140)
        
        items = [
            ("⚙️ 기본", 0),
            ("📅 캘린더", 1),
            ("🎨 스킨", 2),
            ("📝 메모", 3),
            ("⌨️ 단축키", 4),
            ("💾 데이터", 5),
        ]
        for label, idx in items:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, idx)
            self.nav_list.addItem(item)
            
        body_layout.addWidget(self.nav_list)

        self.pages = QStackedWidget()

        # ----------------------------------------------------
        # Page 0: 기본 (General)
        # ----------------------------------------------------
        page_general = QWidget()
        pg_gen_layout = QVBoxLayout(page_general)
        pg_gen_layout.setContentsMargins(0, 0, 0, 0)
        pg_gen_layout.setSpacing(10)

        behavior_card = QFrame()
        behavior_card.setObjectName("card")
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(14, 12, 14, 12)
        behavior_layout.setSpacing(8)
        behavior_title = QLabel("실행 및 화면 옵션")
        behavior_title.setObjectName("sectionTitle")
        behavior_layout.addWidget(behavior_title)

        self.auto_start_check = QCheckBox("윈도우 시작 시 자동 시작")
        self.auto_start_check.setChecked(auto_start_enabled)
        behavior_layout.addWidget(self.auto_start_check)

        self.window_controls_check = QCheckBox("상단바에 창 투명도 조절 · 항상 위 고정 표시")
        self.window_controls_check.setChecked(show_window_controls)
        self.window_controls_check.setToolTip("체크 해제 시 상단바의 투명도 슬라이더와 항상 위 고정 버튼을 숨깁니다.")
        behavior_layout.addWidget(self.window_controls_check)

        intro_btn = QPushButton("💡 기능 안내 팝업 다시 보기")
        intro_btn.setStyleSheet(f"padding: 5px 10px; font-size: 12px; margin-top: 4px; background: {self.palette['panel']}; border: 1px solid {self.palette['line']}; border-radius: 4px;")
        intro_btn.setCursor(Qt.PointingHandCursor)
        intro_btn.clicked.connect(self._show_intro_guide)
        behavior_layout.addWidget(intro_btn)

        pg_gen_layout.addWidget(behavior_card)

        info_card = QFrame()
        info_card.setObjectName("card")
        info_layout = QFormLayout(info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(8)
        info_title = QLabel("개발자 정보")
        info_title.setObjectName("sectionTitle")
        info_layout.addRow(info_title)
        email_label = QLabel("이메일")
        email_label.setObjectName("muted")
        email_value = QLabel("westock@korea.kr")
        email_value.setObjectName("value")
        info_layout.addRow(email_label, email_value)
        pg_gen_layout.addWidget(info_card)
        pg_gen_layout.addStretch(1)

        self.pages.addWidget(page_general)

        # ----------------------------------------------------
        # Page 1: 캘린더 (Calendar)
        # ----------------------------------------------------
        page_calendar = QWidget()
        pg_cal_layout = QVBoxLayout(page_calendar)
        pg_cal_layout.setContentsMargins(0, 0, 0, 0)
        pg_cal_layout.setSpacing(10)

        # 1. 캘린더 화면 표시 카드
        cal_view_card = QFrame()
        cal_view_card.setObjectName("card")
        cal_view_layout = QVBoxLayout(cal_view_card)
        cal_view_layout.setContentsMargins(14, 12, 14, 12)
        cal_view_layout.setSpacing(8)

        cal_view_title = QLabel("캘린더 화면 표시")
        cal_view_title.setObjectName("sectionTitle")
        cal_view_layout.addWidget(cal_view_title)

        self.hide_completed_on_calendar_check = QCheckBox("달력에서 완료 일정 숨기기")
        self.hide_completed_on_calendar_check.setChecked(hide_completed_on_calendar)
        cal_view_layout.addWidget(self.hide_completed_on_calendar_check)

        self.show_task_count_on_calendar_check = QCheckBox("캘린더에 업무 건수 표시")
        self.show_task_count_on_calendar_check.setChecked(show_task_count_on_calendar)
        self.show_task_count_on_calendar_check.setToolTip("체크 시 캘린더 날짜 칸에 해당 날짜의 업무를 '업무 N건' 형태로 요약 표시합니다.")
        cal_view_layout.addWidget(self.show_task_count_on_calendar_check)

        self.sticker_animation_check = QCheckBox("스티커 움직임 사용")
        self.sticker_animation_check.setChecked(sticker_animation_enabled)
        cal_view_layout.addWidget(self.sticker_animation_check)

        lunar_row = QHBoxLayout()
        lunar_row.setContentsMargins(0, 0, 0, 0)
        lunar_row.setSpacing(10)
        self.show_lunar_check = QCheckBox("캘린더에 음력 날짜 표시")
        self.show_lunar_check.setChecked(show_lunar_calendar)
        lunar_row.addWidget(self.show_lunar_check)

        lunar_freq_label = QLabel("표시 주기:")
        lunar_freq_label.setObjectName("muted")
        lunar_row.addWidget(lunar_freq_label)

        self.lunar_freq_combo = QComboBox()
        self.lunar_freq_combo.addItem("매일 (모든 날)", "all")
        self.lunar_freq_combo.addItem("1주일 간격 (매주 일요일)", "weekly")
        self.lunar_freq_combo.addItem("양력 1일만 (매달 1일)", "monthly_1st")
        self.lunar_freq_combo.addItem("음력 1일만 (초하루)", "lunar_1st")
        self.lunar_freq_combo.addItem("음력 1일·15일만 (초하루/보름)", "lunar_1st_15th")
        self.lunar_freq_combo.addItem("10일 간격 (1일, 11일, 21일)", "ten_days")

        freq_idx = self.lunar_freq_combo.findData(lunar_display_frequency)
        if freq_idx >= 0:
            self.lunar_freq_combo.setCurrentIndex(freq_idx)
        else:
            self.lunar_freq_combo.setCurrentIndex(0)
        self.lunar_freq_combo.setEnabled(show_lunar_calendar)
        lunar_freq_label.setEnabled(show_lunar_calendar)

        def _on_lunar_toggled(checked: bool) -> None:
            self.lunar_freq_combo.setEnabled(checked)
            lunar_freq_label.setEnabled(checked)

        self.show_lunar_check.toggled.connect(_on_lunar_toggled)
        self.lunar_freq_combo.setFixedWidth(190)
        lunar_row.addWidget(self.lunar_freq_combo)
        lunar_row.addStretch(1)
        cal_view_layout.addLayout(lunar_row)

        self.show_solar_terms_check = QCheckBox("캘린더에 24절기 표시")
        self.show_solar_terms_check.setChecked(show_solar_terms)
        cal_view_layout.addWidget(self.show_solar_terms_check)

        pg_cal_layout.addWidget(cal_view_card)

        # 2. 캘린더 사이드바 표시 카드
        cal_sidebar_card = QFrame()
        cal_sidebar_card.setObjectName("card")
        cal_sidebar_layout = QVBoxLayout(cal_sidebar_card)
        cal_sidebar_layout.setContentsMargins(14, 12, 14, 12)
        cal_sidebar_layout.setSpacing(8)

        cal_sidebar_title = QLabel("캘린더 사이드바 표시")
        cal_sidebar_title.setObjectName("sectionTitle")
        cal_sidebar_layout.addWidget(cal_sidebar_title)

        self.calendar_sidebar_title_only_check = QCheckBox("사이드바 일정 목록: 제목만 1줄로 표시")
        self.calendar_sidebar_title_only_check.setChecked(calendar_sidebar_title_only)
        self.calendar_sidebar_title_only_check.setToolTip(
            "우측 사이드바의 일정 카드에서 본문 내용을 숨기고 제목만 1줄로 콤팩트하게 표시합니다.\n"
            "체크 해제 시 일정 본문 내용이 함께 표시됩니다."
        )
        cal_sidebar_layout.addWidget(self.calendar_sidebar_title_only_check)

        pg_cal_layout.addWidget(cal_sidebar_card)
        pg_cal_layout.addStretch(1)

        self.pages.addWidget(page_calendar)


        page_skin = QWidget()
        pg_skin_layout = QVBoxLayout(page_skin)
        pg_skin_layout.setContentsMargins(0, 0, 0, 0)
        pg_skin_layout.setSpacing(10)

        appearance = QFrame()
        appearance.setObjectName("card")
        appearance_layout = QFormLayout(appearance)
        appearance_layout.setContentsMargins(14, 12, 14, 12)
        appearance_layout.setSpacing(10)
        appearance_title = QLabel("스킨 설정")
        appearance_title.setObjectName("sectionTitle")
        appearance_layout.addRow(appearance_title)
        self.theme_combo = QComboBox()
        for theme_name in THEME_OPTIONS:
            self.theme_combo.addItem(THEME_LABELS.get(theme_name, theme_name), theme_name)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(current_theme)))
        theme_label = QLabel("테마")
        theme_label.setObjectName("muted")
        appearance_layout.addRow(theme_label, self.theme_combo)
        pg_skin_layout.addWidget(appearance)
        pg_skin_layout.addStretch(1)

        self.pages.addWidget(page_skin)

        page_memo = QWidget()
        pg_memo_layout = QVBoxLayout(page_memo)
        pg_memo_layout.setContentsMargins(0, 0, 0, 0)
        pg_memo_layout.setSpacing(10)

        # 1. 새 메모 기본 속성 카드
        memo_card1 = QFrame()
        memo_card1.setObjectName("card")
        mc1_layout = QVBoxLayout(memo_card1)
        mc1_layout.setContentsMargins(14, 12, 14, 12)
        mc1_layout.setSpacing(8)

        mc1_title = QLabel("새 메모 기본 속성")
        mc1_title.setObjectName("sectionTitle")
        mc1_layout.addWidget(mc1_title)

        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(10)
        color_label = QLabel("기본 색상:")
        color_label.setObjectName("muted")
        color_row.addWidget(color_label)

        self.memo_default_color_combo = QComboBox()
        self.memo_default_color_combo.addItem("🎲 랜덤 (무작위 생성)", "random")
        self.memo_default_color_combo.addItem("💛 노랑 (기본)", "yellow")
        self.memo_default_color_combo.addItem("💚 연두", "green")
        self.memo_default_color_combo.addItem("💖 핑크", "pink")
        self.memo_default_color_combo.addItem("💜 보라", "purple")
        self.memo_default_color_combo.addItem("💙 하늘", "blue")
        self.memo_default_color_combo.addItem("🤍 화이트", "white")
        self.memo_default_color_combo.addItem("🖤 다크", "dark")

        c_idx = self.memo_default_color_combo.findData(memo_default_color)
        self.memo_default_color_combo.setCurrentIndex(c_idx if c_idx >= 0 else 1)
        self.memo_default_color_combo.setFixedWidth(190)
        color_row.addWidget(self.memo_default_color_combo)
        color_row.addStretch(1)
        mc1_layout.addLayout(color_row)

        self.memo_show_attachment_bar_check = QCheckBox("하단 파일 첨부 및 이미지 바 기본 표시")
        self.memo_show_attachment_bar_check.setChecked(memo_show_attachment_bar)
        self.memo_show_attachment_bar_check.setToolTip("해제 시 첨부파일이 없는 새 메모에서 하단 바를 숨겨 심플하게 표시합니다 (우클릭 메뉴로 언제든 표시 가능).")
        mc1_layout.addWidget(self.memo_show_attachment_bar_check)

        self.memo_default_floating_check = QCheckBox("새 메모 생성 시 항상 위에 고정 (Topmost)")
        self.memo_default_floating_check.setChecked(memo_default_floating)
        self.memo_default_floating_check.setToolTip("새 메모를 만들 때 항상 다른 프로그램 창 위에 떠 있도록 핀을 기본으로 고정합니다.")
        mc1_layout.addWidget(self.memo_default_floating_check)

        opt_row = QHBoxLayout()
        opt_row.setContentsMargins(0, 0, 0, 0)
        opt_row.setSpacing(10)

        op_label = QLabel("기본 투명도:")
        op_label.setObjectName("muted")
        opt_row.addWidget(op_label)

        self.memo_default_opacity_combo = QComboBox()
        self.memo_default_opacity_combo.addItem("100% (완전 불투명)", 100)
        self.memo_default_opacity_combo.addItem("90%", 90)
        self.memo_default_opacity_combo.addItem("80%", 80)
        self.memo_default_opacity_combo.addItem("70%", 70)
        self.memo_default_opacity_combo.addItem("60%", 60)
        self.memo_default_opacity_combo.addItem("50% (반투명)", 50)
        op_idx = self.memo_default_opacity_combo.findData(memo_default_opacity)
        self.memo_default_opacity_combo.setCurrentIndex(op_idx if op_idx >= 0 else 0)
        self.memo_default_opacity_combo.setFixedWidth(150)
        opt_row.addWidget(self.memo_default_opacity_combo)

        opt_row.addSpacing(16)

        sz_label = QLabel("기본 크기:")
        sz_label.setObjectName("muted")
        opt_row.addWidget(sz_label)

        self.memo_default_size_combo = QComboBox()
        self.memo_default_size_combo.addItem("보통 (380 × 360)", "380,360")
        self.memo_default_size_combo.addItem("작게 (300 × 280)", "300,280")
        self.memo_default_size_combo.addItem("크게 (480 × 440)", "480,440")
        self.memo_default_size_combo.addItem("와이드 (560 × 360)", "560,360")
        sz_idx = self.memo_default_size_combo.findData(memo_default_size)
        self.memo_default_size_combo.setCurrentIndex(sz_idx if sz_idx >= 0 else 0)
        self.memo_default_size_combo.setFixedWidth(160)
        opt_row.addWidget(self.memo_default_size_combo)
        opt_row.addStretch(1)
        mc1_layout.addLayout(opt_row)

        pg_memo_layout.addWidget(memo_card1)

        # 2. 메모 본문 및 사이드바 표시 카드
        memo_card2 = QFrame()
        memo_card2.setObjectName("card")
        mc2_layout = QVBoxLayout(memo_card2)
        mc2_layout.setContentsMargins(14, 12, 14, 12)
        mc2_layout.setSpacing(8)

        mc2_title = QLabel("메모 본문 및 사이드바 표시")
        mc2_title.setObjectName("sectionTitle")
        mc2_layout.addWidget(mc2_title)

        font_row = QHBoxLayout()
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(10)
        font_label = QLabel("본문 글꼴 크기:")
        font_label.setObjectName("muted")
        font_row.addWidget(font_label)

        self.memo_default_font_size_combo = QComboBox()
        self.memo_default_font_size_combo.addItem("작게 (9pt)", 9)
        self.memo_default_font_size_combo.addItem("보통 (11pt)", 11)
        self.memo_default_font_size_combo.addItem("크게 (13pt)", 13)
        self.memo_default_font_size_combo.addItem("아주 크게 (15pt)", 15)
        f_idx = self.memo_default_font_size_combo.findData(memo_default_font_size)
        self.memo_default_font_size_combo.setCurrentIndex(f_idx if f_idx >= 0 else 1)
        self.memo_default_font_size_combo.setFixedWidth(150)
        font_row.addWidget(self.memo_default_font_size_combo)
        font_row.addStretch(1)
        mc2_layout.addLayout(font_row)

        self.memo_title_only_check = QCheckBox("사이드바 메모 목록: 제목만 1줄로 표시")
        self.memo_title_only_check.setChecked(memo_title_only)
        self.memo_title_only_check.setToolTip("우측 사이드바의 메모 카드에서 본문 내용을 숨기고 제목만 1줄로 콤팩트하게 표시합니다.\n체크 해제 시 메모 본문 내용이 함께 표시됩니다.")
        mc2_layout.addWidget(self.memo_title_only_check)

        self.memo_expand_anchor_check = QCheckBox("메모/그룹 펼칠 때 우측 기준으로 확장 (화면 밖으로 안 넘침)")
        self.memo_expand_anchor_check.setChecked(memo_expand_anchor == "right")
        self.memo_expand_anchor_check.setToolTip(
            "화면 오른쪽 끝에 붙여둔 메모나 그룹을 접었다 펼칠 때, 왼쪽 대신 오른쪽 끝을 기준으로\n"
            "넓어지게 하여 창이 화면 밖으로 밀려나지 않게 합니다."
        )
        mc2_layout.addWidget(self.memo_expand_anchor_check)

        pg_memo_layout.addWidget(memo_card2)
        pg_memo_layout.addStretch(1)

        self.pages.addWidget(page_memo)

        page_shortcuts = QWidget()
        pg_sc_layout = QVBoxLayout(page_shortcuts)
        pg_sc_layout.setContentsMargins(0, 0, 0, 0)
        pg_sc_layout.setSpacing(10)

        sc_cal_card = QFrame()
        sc_cal_card.setObjectName("card")
        sc_cal_layout = QVBoxLayout(sc_cal_card)
        sc_cal_layout.setContentsMargins(14, 12, 14, 12)
        sc_cal_layout.setSpacing(8)
        self.cal_shortcut_title = QLabel(f"캘린더 단축키 설정 (현재: {self._current_shortcut})")
        self.cal_shortcut_title.setObjectName("sectionTitle")
        sc_cal_layout.addWidget(self.cal_shortcut_title)

        cal_row = QHBoxLayout()
        cal_row.setContentsMargins(0, 0, 0, 0)
        cal_row.setSpacing(8)
        cal_label = QLabel("토글")
        cal_label.setObjectName("muted")
        cal_row.addWidget(cal_label)

        self.shortcut_ctrl_check = QCheckBox("Ctrl")
        self.shortcut_ctrl_check.setChecked("Ctrl" in shortcut_modifiers)
        cal_row.addWidget(self.shortcut_ctrl_check)

        self.shortcut_shift_check = QCheckBox("Shift")
        self.shortcut_shift_check.setChecked("Shift" in shortcut_modifiers)
        cal_row.addWidget(self.shortcut_shift_check)

        self.shortcut_alt_check = QCheckBox("Alt")
        self.shortcut_alt_check.setChecked("Alt" in shortcut_modifiers)
        cal_row.addWidget(self.shortcut_alt_check)

        plus_label1 = QLabel("+")
        plus_label1.setObjectName("muted")
        cal_row.addWidget(plus_label1)

        self.shortcut_key_combo = QComboBox()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            self.shortcut_key_combo.addItem(letter, letter)
        for num in range(1, 13):
            token = f"F{num}"
            self.shortcut_key_combo.addItem(token, token)
        self.shortcut_key_combo.setCurrentIndex(max(0, self.shortcut_key_combo.findData(shortcut_key)))
        self.shortcut_key_combo.setMinimumWidth(72)
        self.shortcut_key_combo.setMaximumWidth(88)
        cal_row.addWidget(self.shortcut_key_combo)
        self.cal_default_btn = QPushButton("기본값(F3)")
        self.cal_default_btn.setToolTip("캘린더 토글 단축키를 기본값인 F3으로 설정합니다.")
        self.cal_default_btn.clicked.connect(self._set_cal_default)
        cal_row.addWidget(self.cal_default_btn)
        cal_row.addStretch(1)
        sc_cal_layout.addLayout(cal_row)

        self.shortcut_status_label = QLabel("")
        self.shortcut_status_label.setObjectName("subtitle")
        sc_cal_layout.addWidget(self.shortcut_status_label)
        pg_sc_layout.addWidget(sc_cal_card)

        sc_memo_card = QFrame()
        sc_memo_card.setObjectName("card")
        sc_memo_layout = QVBoxLayout(sc_memo_card)
        sc_memo_layout.setContentsMargins(14, 12, 14, 12)
        sc_memo_layout.setSpacing(8)
        self.memo_shortcut_title = QLabel(f"메모 단축키 설정 (현재: {self._current_memo_shortcut})")
        self.memo_shortcut_title.setObjectName("sectionTitle")
        sc_memo_layout.addWidget(self.memo_shortcut_title)

        memo_row = QHBoxLayout()
        memo_row.setContentsMargins(0, 0, 0, 0)
        memo_row.setSpacing(8)
        memo_lbl = QLabel("토글")
        memo_lbl.setObjectName("muted")
        memo_row.addWidget(memo_lbl)

        self.memo_shortcut_ctrl_check = QCheckBox("Ctrl")
        self.memo_shortcut_ctrl_check.setChecked("Ctrl" in memo_modifiers)
        memo_row.addWidget(self.memo_shortcut_ctrl_check)

        self.memo_shortcut_shift_check = QCheckBox("Shift")
        self.memo_shortcut_shift_check.setChecked("Shift" in memo_modifiers)
        memo_row.addWidget(self.memo_shortcut_shift_check)

        self.memo_shortcut_alt_check = QCheckBox("Alt")
        self.memo_shortcut_alt_check.setChecked("Alt" in memo_modifiers)
        memo_row.addWidget(self.memo_shortcut_alt_check)

        plus_label2 = QLabel("+")
        plus_label2.setObjectName("muted")
        memo_row.addWidget(plus_label2)

        self.memo_shortcut_key_combo = QComboBox()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            self.memo_shortcut_key_combo.addItem(letter, letter)
        for num in range(1, 13):
            token = f"F{num}"
            self.memo_shortcut_key_combo.addItem(token, token)
        self.memo_shortcut_key_combo.setCurrentIndex(max(0, self.memo_shortcut_key_combo.findData(memo_key)))
        self.memo_shortcut_key_combo.setMinimumWidth(72)
        self.memo_shortcut_key_combo.setMaximumWidth(88)
        memo_row.addWidget(self.memo_shortcut_key_combo)
        self.memo_default_btn = QPushButton("기본값(F4)")
        self.memo_default_btn.setToolTip("메모 토글 단축키를 기본값인 F4로 설정합니다.")
        self.memo_default_btn.clicked.connect(self._set_memo_default)
        memo_row.addWidget(self.memo_default_btn)
        memo_row.addStretch(1)
        sc_memo_layout.addLayout(memo_row)

        self.memo_shortcut_status_label = QLabel("")
        self.memo_shortcut_status_label.setObjectName("subtitle")
        sc_memo_layout.addWidget(self.memo_shortcut_status_label)
        pg_sc_layout.addWidget(sc_memo_card)

        reset_all_row = QHBoxLayout()
        reset_all_btn = QPushButton("단축키 기본값 초기화 (캘린더: F3, 메모: F4)")
        reset_all_btn.setToolTip("캘린더(F3) 및 메모(F4) 단축키를 기본값으로 일괄 재설정합니다.")
        reset_all_btn.clicked.connect(self._reset_all_shortcuts_default)
        reset_all_row.addWidget(reset_all_btn)
        reset_all_row.addStretch(1)
        pg_sc_layout.addLayout(reset_all_row)

        pg_sc_layout.addStretch(1)

        self.pages.addWidget(page_shortcuts)

        page_data = QWidget()
        pg_dt_layout = QVBoxLayout(page_data)
        pg_dt_layout.setContentsMargins(0, 0, 0, 0)
        pg_dt_layout.setSpacing(10)

        backup_card = QFrame()
        backup_card.setObjectName("card")
        backup_layout = QVBoxLayout(backup_card)
        backup_layout.setContentsMargins(14, 12, 14, 12)
        backup_layout.setSpacing(8)
        backup_title = QLabel("자동 백업 설정")
        backup_title.setObjectName("sectionTitle")
        backup_layout.addWidget(backup_title)

        backup_row1 = QHBoxLayout()
        self.auto_backup_check = QCheckBox("앱 시작 시 자동 백업 활성화")
        self.auto_backup_check.setChecked(auto_backup_enabled)
        backup_row1.addWidget(self.auto_backup_check)

        backup_note = QLabel("(※ 첨부파일은 백업에 포함되지 않습니다.)")
        backup_note.setObjectName("subtitle")
        backup_note.setStyleSheet(f"color: {self.palette['danger']}; font-weight: 600;")
        backup_row1.addWidget(backup_note)
        backup_row1.addStretch(1)
        backup_layout.addLayout(backup_row1)

        backup_row2 = QHBoxLayout()
        interval_label = QLabel("백업 주기:")
        interval_label.setObjectName("muted")
        backup_row2.addWidget(interval_label)

        self.auto_backup_interval_combo = QComboBox()
        self.auto_backup_interval_combo.addItem("매일 (1일 마다)", 1)
        self.auto_backup_interval_combo.addItem("3일 마다", 3)
        self.auto_backup_interval_combo.addItem("7일 마다 (매주)", 7)
        self.auto_backup_interval_combo.addItem("30일 마다 (매월)", 30)

        check_interval = auto_backup_interval_days if auto_backup_interval_days > 0 else 1
        idx = self.auto_backup_interval_combo.findData(check_interval)
        if idx >= 0:
            self.auto_backup_interval_combo.setCurrentIndex(idx)
        else:
            self.auto_backup_interval_combo.setCurrentIndex(0)
        backup_row2.addWidget(self.auto_backup_interval_combo)

        backup_row2.addSpacing(16)

        keep_label = QLabel("보관 개수:")
        keep_label.setObjectName("muted")
        backup_row2.addWidget(keep_label)

        self.auto_backup_keep_combo = QComboBox()
        self.auto_backup_keep_combo.addItem("3개", 3)
        self.auto_backup_keep_combo.addItem("5개", 5)
        self.auto_backup_keep_combo.addItem("10개", 10)
        self.auto_backup_keep_combo.addItem("20개", 20)
        self.auto_backup_keep_combo.addItem("무제한", 0)

        idx = self.auto_backup_keep_combo.findData(auto_backup_keep_count)
        if idx >= 0:
            self.auto_backup_keep_combo.setCurrentIndex(idx)
        else:
            self.auto_backup_keep_combo.setCurrentIndex(1)
        backup_row2.addWidget(self.auto_backup_keep_combo)
        backup_row2.addStretch(1)
        backup_layout.addLayout(backup_row2)

        backup_row3 = QHBoxLayout()
        restore_backup_button = QPushButton("백업 파일 복원")
        restore_backup_button.clicked.connect(self._request_restore_backup)
        backup_row3.addWidget(restore_backup_button)

        open_backup_button = QPushButton("백업 폴더 열기")
        open_backup_button.clicked.connect(self._open_backup_folder)
        backup_row3.addWidget(open_backup_button)
        backup_row3.addStretch(1)
        backup_layout.addLayout(backup_row3)
        pg_dt_layout.addWidget(backup_card)

        self.auto_backup_check.toggled.connect(self._on_auto_backup_toggled)
        self._on_auto_backup_toggled(auto_backup_enabled)

        data_card = QFrame()
        data_card.setObjectName("card")
        data_layout = QVBoxLayout(data_card)
        data_layout.setContentsMargins(14, 12, 14, 12)
        data_layout.setSpacing(6)
        data_title = QLabel("데이터 관리")
        data_title.setObjectName("sectionTitle")
        data_layout.addWidget(data_title)
        data_hint = QLabel("전체 데이터를 내보내거나 가져옵니다. 공휴일 설정 파일을 열어 직접 편집할 수도 있습니다.")
        data_hint.setObjectName("subtitle")
        data_hint.setWordWrap(True)
        data_layout.addWidget(data_hint)
        data_buttons = QGridLayout()
        data_buttons.setContentsMargins(0, 4, 0, 0)
        data_buttons.setHorizontalSpacing(8)
        data_buttons.setVerticalSpacing(8)
        data_buttons.setColumnStretch(0, 1)
        data_buttons.setColumnStretch(1, 1)
        export_button = QPushButton("데이터 내보내기")
        export_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        export_button.clicked.connect(self._request_export_data)
        data_buttons.addWidget(export_button, 0, 0)
        import_button = QPushButton("데이터 가져오기")
        import_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        import_button.clicked.connect(self._request_import_data)
        data_buttons.addWidget(import_button, 0, 1)
        holiday_button = QPushButton("공휴일 파일 열기")
        holiday_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        holiday_button.clicked.connect(self._open_holiday_file)
        data_buttons.addWidget(holiday_button, 1, 0)
        reload_holiday_button = QPushButton("공휴일 반영")
        reload_holiday_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        reload_holiday_button.clicked.connect(self._request_reload_holidays)
        data_buttons.addWidget(reload_holiday_button, 1, 1)
        data_layout.addLayout(data_buttons)

        pg_dt_layout.addWidget(data_card)
        pg_dt_layout.addStretch(1)

        self.pages.addWidget(page_data)

        body_layout.addWidget(self.pages, 1)
        root.addLayout(body_layout, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.Save)
        if save_button is not None:
            save_button.setObjectName("primary")
            save_button.setText("적용")
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setText("취소")
        root.addWidget(buttons)

        self.nav_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        
        if initial_tab == "calendar":
            self.nav_list.setCurrentRow(1)
        elif initial_tab == "skin":
            self.nav_list.setCurrentRow(2)
        elif initial_tab == "memo":
            self.nav_list.setCurrentRow(3)
        elif initial_tab == "shortcuts":
            self.nav_list.setCurrentRow(4)
        elif initial_tab == "data":
            self.nav_list.setCurrentRow(5)
        else:
            self.nav_list.setCurrentRow(0)

        self.shortcut_ctrl_check.toggled.connect(self._on_cal_shortcut_changed)
        self.shortcut_shift_check.toggled.connect(self._on_cal_shortcut_changed)
        self.shortcut_alt_check.toggled.connect(self._on_cal_shortcut_changed)
        self.shortcut_key_combo.currentIndexChanged.connect(self._on_cal_shortcut_changed)

        self.memo_shortcut_ctrl_check.toggled.connect(self._on_memo_shortcut_changed)
        self.memo_shortcut_shift_check.toggled.connect(self._on_memo_shortcut_changed)
        self.memo_shortcut_alt_check.toggled.connect(self._on_memo_shortcut_changed)
        self.memo_shortcut_key_combo.currentIndexChanged.connect(self._on_memo_shortcut_changed)

        self._refresh_shortcut_status()
        self._refresh_memo_shortcut_status()

    @staticmethod
    def _shortcut_parts(shortcut: str) -> tuple[set[str], str]:
        modifiers: set[str] = set()
        token = "S"
        parts = [part.strip() for part in (shortcut or "").split(",")[0].strip().split("+") if part.strip()]
        for part in parts:
            lowered = part.lower()
            if lowered in {"ctrl", "control"}:
                modifiers.add("Ctrl")
            elif lowered == "shift":
                modifiers.add("Shift")
            elif lowered == "alt":
                modifiers.add("Alt")
            elif len(part) == 1 and part.isalpha():
                token = part.upper()
            elif lowered.startswith("f") and lowered[1:].isdigit():
                fn = int(lowered[1:])
                if 1 <= fn <= 12:
                    token = f"F{fn}"
        if not modifiers:
            if not token.startswith("F"):
                modifiers = {"Ctrl", "Alt"}
        return modifiers, token

    def _save(self) -> None:
        cal_modifiers: list[str] = []
        if self.shortcut_ctrl_check.isChecked():
            cal_modifiers.append("Ctrl")
        if self.shortcut_shift_check.isChecked():
            cal_modifiers.append("Shift")
        if self.shortcut_alt_check.isChecked():
            cal_modifiers.append("Alt")
        cal_key_token = str(self.shortcut_key_combo.currentData())
        if not cal_modifiers and not (cal_key_token.startswith("F") and cal_key_token[1:].isdigit()):
            QMessageBox.warning(self, "입력 오류", "캘린더 단독 키는 F1~F12만 설정할 수 있습니다.")
            self.nav_list.setCurrentRow(4)
            return
        cal_shortcut = "+".join(cal_modifiers + [cal_key_token]) if cal_modifiers else cal_key_token
        cal_available, cal_message = self._check_shortcut_availability(cal_shortcut, is_memo=False)
        if not cal_available:
            self.shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.shortcut_status_label.setText(cal_message)
            self.nav_list.setCurrentRow(4)
            QMessageBox.warning(self, "단축키 오류", f"캘린더 단축키 오류: {cal_message}")
            return

        memo_modifiers: list[str] = []
        if self.memo_shortcut_ctrl_check.isChecked():
            memo_modifiers.append("Ctrl")
        if self.memo_shortcut_shift_check.isChecked():
            memo_modifiers.append("Shift")
        if self.memo_shortcut_alt_check.isChecked():
            memo_modifiers.append("Alt")
        memo_key_token = str(self.memo_shortcut_key_combo.currentData())
        if not memo_modifiers and not (memo_key_token.startswith("F") and memo_key_token[1:].isdigit()):
            QMessageBox.warning(self, "입력 오류", "메모 단독 키는 F1~F12만 설정할 수 있습니다.")
            self.nav_list.setCurrentRow(4)
            return
        memo_shortcut = "+".join(memo_modifiers + [memo_key_token]) if memo_modifiers else memo_key_token
        
        if normalize_shortcut(cal_shortcut) == normalize_shortcut(memo_shortcut):
            QMessageBox.warning(self, "단축키 중복", "캘린더 단축키와 메모 단축키는 서로 달라야 합니다.")
            self.nav_list.setCurrentRow(4)
            return

        memo_available, memo_message = self._check_shortcut_availability(memo_shortcut, is_memo=True)
        if not memo_available:
            self.memo_shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.memo_shortcut_status_label.setText(memo_message)
            self.nav_list.setCurrentRow(4)
            QMessageBox.warning(self, "단축키 오류", f"메모 단축키 오류: {memo_message}")
            return

        self.result = {
            "action": "apply",
            "theme": str(self.theme_combo.currentData()),
            "shortcut": cal_shortcut,
            "memo_shortcut": memo_shortcut,
            "auto_start": self.auto_start_check.isChecked(),
            "calendar_sidebar_title_only": self.calendar_sidebar_title_only_check.isChecked(),
            "sticker_animation_enabled": self.sticker_animation_check.isChecked(),
            "hide_completed_on_calendar": self.hide_completed_on_calendar_check.isChecked(),
            "show_task_count_on_calendar": self.show_task_count_on_calendar_check.isChecked(),
            "auto_backup_enabled": self.auto_backup_check.isChecked(),
            "auto_backup_interval_days": int(self.auto_backup_interval_combo.currentData() or 0),
            "auto_backup_keep_count": int(self.auto_backup_keep_combo.currentData() or 0),
            "show_lunar_calendar": self.show_lunar_check.isChecked(),
            "lunar_display_frequency": str(self.lunar_freq_combo.currentData() or "all"),
            "show_solar_terms": self.show_solar_terms_check.isChecked(),
            "memo_default_color": str(self.memo_default_color_combo.currentData() or "yellow"),
            "memo_show_attachment_bar": self.memo_show_attachment_bar_check.isChecked(),
            "memo_default_floating": self.memo_default_floating_check.isChecked(),
            "memo_default_opacity": int(self.memo_default_opacity_combo.currentData() or 100),
            "memo_default_size": str(self.memo_default_size_combo.currentData() or "380,360"),
            "memo_default_font_size": int(self.memo_default_font_size_combo.currentData() or 11),
            "memo_title_only": self.memo_title_only_check.isChecked(),
            "memo_expand_anchor": "right" if self.memo_expand_anchor_check.isChecked() else "left",
            "show_window_controls": self.window_controls_check.isChecked(),
        }
        self.accept()


    def _show_intro_guide(self) -> None:
        dlg = WelcomeFeatureIntroDialog(self, is_dismissed=False)
        dlg.exec()

    def _get_current_cal_shortcut_from_ui(self) -> str:
        modifiers: list[str] = []
        if self.shortcut_ctrl_check.isChecked():
            modifiers.append("Ctrl")
        if self.shortcut_shift_check.isChecked():
            modifiers.append("Shift")
        if self.shortcut_alt_check.isChecked():
            modifiers.append("Alt")
        key_token = str(self.shortcut_key_combo.currentData())
        return "+".join(modifiers + [key_token]) if modifiers else key_token

    def _get_current_memo_shortcut_from_ui(self) -> str:
        modifiers: list[str] = []
        if self.memo_shortcut_ctrl_check.isChecked():
            modifiers.append("Ctrl")
        if self.memo_shortcut_shift_check.isChecked():
            modifiers.append("Shift")
        if self.memo_shortcut_alt_check.isChecked():
            modifiers.append("Alt")
        key_token = str(self.memo_shortcut_key_combo.currentData())
        return "+".join(modifiers + [key_token]) if modifiers else key_token

    def _on_cal_shortcut_changed(self) -> None:
        self._refresh_shortcut_status()
        self._refresh_memo_shortcut_status()

    def _on_memo_shortcut_changed(self) -> None:
        self._refresh_memo_shortcut_status()
        self._refresh_shortcut_status()

    def _set_cal_default(self) -> None:
        self.shortcut_ctrl_check.setChecked(False)
        self.shortcut_shift_check.setChecked(False)
        self.shortcut_alt_check.setChecked(False)
        idx = self.shortcut_key_combo.findData("F3")
        if idx >= 0:
            self.shortcut_key_combo.setCurrentIndex(idx)
        self._on_cal_shortcut_changed()

    def _set_memo_default(self) -> None:
        self.memo_shortcut_ctrl_check.setChecked(False)
        self.memo_shortcut_shift_check.setChecked(False)
        self.memo_shortcut_alt_check.setChecked(False)
        idx = self.memo_shortcut_key_combo.findData("F4")
        if idx >= 0:
            self.memo_shortcut_key_combo.setCurrentIndex(idx)
        self._on_memo_shortcut_changed()

    def _reset_all_shortcuts_default(self) -> None:
        self.shortcut_ctrl_check.setChecked(False)
        self.shortcut_shift_check.setChecked(False)
        self.shortcut_alt_check.setChecked(False)
        idx_cal = self.shortcut_key_combo.findData("F3")
        if idx_cal >= 0:
            self.shortcut_key_combo.setCurrentIndex(idx_cal)

        self.memo_shortcut_ctrl_check.setChecked(False)
        self.memo_shortcut_shift_check.setChecked(False)
        self.memo_shortcut_alt_check.setChecked(False)
        idx_memo = self.memo_shortcut_key_combo.findData("F4")
        if idx_memo >= 0:
            self.memo_shortcut_key_combo.setCurrentIndex(idx_memo)

        self._refresh_shortcut_status()
        self._refresh_memo_shortcut_status()

    def _refresh_shortcut_status(self) -> None:
        shortcut = self._get_current_cal_shortcut_from_ui()
        modifiers = [m for m in ["Ctrl", "Shift", "Alt"] if getattr(self, f"shortcut_{m.lower()}_check").isChecked()]
        key_token = str(self.shortcut_key_combo.currentData())
        if not modifiers and not (key_token.startswith("F") and key_token[1:].isdigit()):
            self.shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.shortcut_status_label.setText("단독 키는 F1~F12만 가능합니다.")
            return

        memo_shortcut = self._get_current_memo_shortcut_from_ui()
        if normalize_shortcut(shortcut) == normalize_shortcut(memo_shortcut):
            self.shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.shortcut_status_label.setText("메모 단축키와 중복됩니다.")
            return

        available, message = self._check_shortcut_availability(shortcut, is_memo=False)
        self.shortcut_status_label.setStyleSheet(f"color: {self.palette['accent']};" if available else f"color: {self.palette['danger']};")
        self.shortcut_status_label.setText(message)

    def _refresh_memo_shortcut_status(self) -> None:
        shortcut = self._get_current_memo_shortcut_from_ui()
        modifiers = [m for m in ["Ctrl", "Shift", "Alt"] if getattr(self, f"memo_shortcut_{m.lower()}_check").isChecked()]
        key_token = str(self.memo_shortcut_key_combo.currentData())
        if not modifiers and not (key_token.startswith("F") and key_token[1:].isdigit()):
            self.memo_shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.memo_shortcut_status_label.setText("단독 키는 F1~F12만 가능합니다.")
            return

        cal_shortcut = self._get_current_cal_shortcut_from_ui()
        if normalize_shortcut(shortcut) == normalize_shortcut(cal_shortcut):
            self.memo_shortcut_status_label.setStyleSheet(f"color: {self.palette['danger']};")
            self.memo_shortcut_status_label.setText("캘린더 단축키와 중복됩니다.")
            return

        available, message = self._check_shortcut_availability(shortcut, is_memo=True)
        self.memo_shortcut_status_label.setStyleSheet(f"color: {self.palette['accent']};" if available else f"color: {self.palette['danger']};")
        self.memo_shortcut_status_label.setText(message)

    def _check_shortcut_availability(self, shortcut: str, is_memo: bool = False) -> tuple[bool, str]:
        normalized = normalize_shortcut(shortcut)
        current = self._current_memo_shortcut if is_memo else self._current_shortcut
        if normalized == current:
            return True, "현재 사용 중인 단축키입니다. (사용 가능)"
        other_current = self._current_shortcut if is_memo else self._current_memo_shortcut
        if normalized == other_current:
            other_ui = self._get_current_cal_shortcut_from_ui() if is_memo else self._get_current_memo_shortcut_from_ui()
            if normalize_shortcut(other_ui) != other_current:
                return True, "사용 가능한 단축키입니다."
        binding = _parse_hotkey(normalized)
        if binding is None:
            return False, "유효하지 않은 단축키 형식입니다."
        modifiers, vk = binding
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        test_id = 0xB7FD if is_memo else 0xB7FE
        ok = bool(user32.RegisterHotKey(None, test_id, modifiers, vk))
        if ok:
            user32.UnregisterHotKey(None, test_id)
            return True, "사용 가능한 단축키입니다."
        return False, "다른 프로그램에서 사용 중인 단축키입니다."

    def _request_export_data(self) -> None:
        self.result = {"action": "export_data"}
        self.accept()

    def _request_import_data(self) -> None:
        self.result = {"action": "import_data"}
        self.accept()

    def _request_reload_holidays(self) -> None:
        self.result = {"action": "reload_holidays"}
        self.accept()

    def _request_restore_backup(self) -> None:
        self.result = {"action": "restore_auto_backup"}
        self.accept()


    def _open_backup_folder(self) -> None:
        import os
        backup_dir = self._db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(backup_dir.resolve()))
        except Exception:
            pass

    def _on_auto_backup_toggled(self, checked: bool) -> None:
        self.auto_backup_interval_combo.setEnabled(checked)
        self.auto_backup_keep_combo.setEnabled(checked)

    def _open_holiday_file(self) -> None:
        import json
        import subprocess
        from taskcalendar.paths import data_path

        holiday_path = data_path("holidays_kr.json")
        if not holiday_path.exists():
            holiday_path.parent.mkdir(parents=True, exist_ok=True)
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
            holiday_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            subprocess.Popen(["notepad.exe", str(holiday_path)])
            QMessageBox.information(
                self,
                "안내",
                "메모장에서 공휴일 설정 파일을 열었습니다.\n"
                "수정 후 저장한 다음 '공휴일 반영' 또는 '적용'을 누르면 캘린더에 반영됩니다."
            )
        except Exception as e:
            QMessageBox.warning(self, "오류", f"파일을 여는 중 오류가 발생했습니다.\n{e}")


class AlarmEditDialog(QDialog):
    def __init__(self, parent, alarm: Alarm | None = None) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.alarm = alarm
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("알람 등록" if alarm is None else "알람 수정")
        self.setWindowIcon(_dialog_icon())
        self.resize(500, 440)
        self.setFixedWidth(500)
        
        self.setStyleSheet(dialog_stylesheet(self.palette))
        
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        
        # Card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(10)
        
        # 1. Alarm Title
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_lbl_muted = QLabel("알람 제목")
        title_lbl_muted.setObjectName("muted")
        title_lbl_muted.setFixedWidth(70)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("알람 제목 입력")
        if alarm:
            self.title_input.setText(alarm.title)
        title_layout.addWidget(title_lbl_muted)
        title_layout.addWidget(self.title_input)
        card_layout.addLayout(title_layout)
        
        # 1.5 Alarm Type (Radio buttons)
        type_layout = QHBoxLayout()
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_lbl_muted = QLabel("알람 유형")
        type_lbl_muted.setObjectName("muted")
        type_lbl_muted.setFixedWidth(70)
        type_layout.addWidget(type_lbl_muted)
        
        self.type_regular_radio = QRadioButton("일반 알람")
        self.type_regular_radio.toggled.connect(self._on_type_changed)
        self.type_interval_radio = QRadioButton("시간 간격 반복")
        self.type_interval_radio.toggled.connect(self._on_type_changed)
        
        type_layout.addWidget(self.type_regular_radio)
        type_layout.addWidget(self.type_interval_radio)
        type_layout.addStretch(1)
        card_layout.addLayout(type_layout)
        
        # 2. Time
        time_layout = QHBoxLayout()
        time_layout.setContentsMargins(0, 0, 0, 0)
        self.time_lbl_muted = QLabel("알람 시간")
        self.time_lbl_muted.setObjectName("muted")
        self.time_lbl_muted.setFixedWidth(70)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        if alarm and alarm.alarm_time:
            h, m = map(int, alarm.alarm_time.split(":"))
            self.time_edit.setTime(QTime(h, m))
        else:
            self.time_edit.setTime(QTime.currentTime())
        time_layout.addWidget(self.time_lbl_muted)
        time_layout.addWidget(self.time_edit)
        time_layout.addStretch(1)
        card_layout.addLayout(time_layout)

        # 2.5 End Time Row
        self.end_time_row = QWidget()
        end_time_layout = QHBoxLayout(self.end_time_row)
        end_time_layout.setContentsMargins(0, 0, 0, 0)
        end_time_lbl_muted = QLabel("종료 시간")
        end_time_lbl_muted.setObjectName("muted")
        end_time_lbl_muted.setFixedWidth(70)
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setDisplayFormat("HH:mm")
        if alarm and alarm.hourly_end_time:
            eh, em = map(int, alarm.hourly_end_time.split(":"))
            self.end_time_edit.setTime(QTime(eh, em))
        else:
            self.end_time_edit.setTime(QTime.currentTime().addSecs(3600))
        end_time_layout.addWidget(end_time_lbl_muted)
        end_time_layout.addWidget(self.end_time_edit)
        end_time_layout.addStretch(1)
        card_layout.addWidget(self.end_time_row)

        # 2.6 Interval Row
        self.interval_row = QWidget()
        interval_layout = QHBoxLayout(self.interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_lbl_muted = QLabel("반복 간격")
        interval_lbl_muted.setObjectName("muted")
        interval_lbl_muted.setFixedWidth(70)
        self.interval_combo = QComboBox()
        self.interval_combo.addItem("1시간 간격", 1)
        self.interval_combo.addItem("2시간 간격", 2)
        self.interval_combo.addItem("3시간 간격", 3)
        self.interval_combo.addItem("4시간 간격", 4)
        self.interval_combo.addItem("6시간 간격", 6)
        self.interval_combo.addItem("8시간 간격", 8)
        self.interval_combo.addItem("12시간 간격", 12)
        if alarm and alarm.hourly_interval:
            idx = self.interval_combo.findData(alarm.hourly_interval)
            if idx >= 0:
                self.interval_combo.setCurrentIndex(idx)
        interval_layout.addWidget(interval_lbl_muted)
        interval_layout.addWidget(self.interval_combo)
        interval_layout.addStretch(1)
        card_layout.addWidget(self.interval_row)
        
        # 3. Repeat Weekdays
        weekday_layout = QHBoxLayout()
        weekday_layout.setContentsMargins(0, 0, 0, 0)
        weekday_lbl_muted = QLabel("요일 반복")
        weekday_lbl_muted.setObjectName("muted")
        weekday_lbl_muted.setFixedWidth(70)
        weekday_layout.addWidget(weekday_lbl_muted)
        
        weekday_btn_layout = QHBoxLayout()
        weekday_btn_layout.setContentsMargins(0, 0, 0, 0)
        weekday_btn_layout.setSpacing(6)
        self.weekday_buttons: list[QToolButton] = []
        weekday_labels = ["일", "월", "화", "수", "목", "금", "토"]
        for i, label in enumerate(weekday_labels):
            btn = QToolButton()
            btn.setObjectName("weekdayBtn")
            btn.setText(label)
            btn.setCheckable(True)
            if alarm and i in alarm.repeat_days:
                btn.setChecked(True)
            weekday_btn_layout.addWidget(btn)
            self.weekday_buttons.append(btn)
        weekday_layout.addLayout(weekday_btn_layout)
        weekday_layout.addStretch(1)
        card_layout.addLayout(weekday_layout)
        
        # 4. Period
        period_layout = QHBoxLayout()
        period_layout.setContentsMargins(0, 0, 0, 0)
        period_layout.setSpacing(8)
        
        self.period_checkbox = QCheckBox("기간")
        self.period_checkbox.setStyleSheet(f"color: {self.palette['muted']}; font-size: 12px; font-weight: 600;")
        self.period_checkbox.setFixedWidth(70)
        self.period_checkbox.toggled.connect(self._on_period_toggled)
        period_layout.addWidget(self.period_checkbox)
        
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setFixedWidth(125)
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setFixedWidth(125)
        
        # Set default dates
        if alarm and alarm.start_date:
            self.start_date_edit.setDate(QDate(alarm.start_date.year, alarm.start_date.month, alarm.start_date.day))
            self.end_date_edit.setDate(QDate(alarm.end_date.year, alarm.end_date.month, alarm.end_date.day))
            self.period_checkbox.setChecked(True)
        else:
            today = date.today()
            self.start_date_edit.setDate(QDate(today.year, today.month, today.day))
            self.end_date_edit.setDate(QDate(today.year, today.month, today.day))
            self.period_checkbox.setChecked(False)
            
        self.start_date_edit.dateChanged.connect(self._on_date_changed)
        self.end_date_edit.dateChanged.connect(self._on_date_changed)
            
        period_layout.addWidget(self.start_date_edit)
        tilde = QLabel("~")
        tilde.setObjectName("value")
        period_layout.addWidget(tilde)
        period_layout.addWidget(self.end_date_edit)
        period_layout.addStretch(1)
        card_layout.addLayout(period_layout)
        
        # 5. Alert Offset
        offset_layout = QHBoxLayout()
        offset_layout.setContentsMargins(0, 0, 0, 0)
        offset_lbl_muted = QLabel("알림 시점")
        offset_lbl_muted.setObjectName("muted")
        offset_lbl_muted.setFixedWidth(70)
        self.offset_combo = QComboBox()
        self.offset_combo.addItem("정시", "at_start")
        self.offset_combo.addItem("5분 전", "5m")
        self.offset_combo.addItem("10분 전", "10m")
        self.offset_combo.addItem("30분 전", "30m")
        self.offset_combo.addItem("1시간 전", "1h")
        
        if alarm:
            idx = self.offset_combo.findData(alarm.alert_offset)
            if idx >= 0:
                self.offset_combo.setCurrentIndex(idx)
                
        offset_layout.addWidget(offset_lbl_muted)
        offset_layout.addWidget(self.offset_combo)
        offset_layout.addStretch(1)
        card_layout.addLayout(offset_layout)
        
        card_layout.addStretch(1)
        
        root.addWidget(card)
        
        # Buttons
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addStretch(1)
        
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.clicked.connect(self.reject)
        actions_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("저장")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._on_save)
        actions_layout.addWidget(self.save_btn)
        
        root.addLayout(actions_layout)
        
        self.saved_alarm: Alarm | None = None

        if alarm and alarm.hourly_repeat:
            self.type_interval_radio.setChecked(True)
        else:
            self.type_regular_radio.setChecked(True)
        self._on_type_changed()

    def _on_type_changed(self) -> None:
        is_interval = self.type_interval_radio.isChecked()
        self.time_lbl_muted.setText("시작 시간" if is_interval else "알람 시간")
        self.end_time_row.setVisible(is_interval)
        self.interval_row.setVisible(is_interval)

    def _on_period_toggled(self, checked: bool) -> None:
        if not checked:
            self.start_date_edit.blockSignals(True)
            self.end_date_edit.blockSignals(True)
            today = date.today()
            self.start_date_edit.setDate(QDate(today.year, today.month, today.day))
            self.end_date_edit.setDate(QDate(today.year, today.month, today.day))
            self.start_date_edit.blockSignals(False)
            self.end_date_edit.blockSignals(False)

    def _on_date_changed(self) -> None:
        self.period_checkbox.setChecked(True)

    def _on_save(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            title = "알람"
            
        alarm_time = self.time_edit.time().toString("HH:mm")
        
        hourly_repeat = self.type_interval_radio.isChecked()
        hourly_interval = self.interval_combo.currentData() if hourly_repeat else 1
        hourly_end_time = self.end_time_edit.time().toString("HH:mm") if hourly_repeat else ""
        
        if hourly_repeat:
            qstart_t = self.time_edit.time()
            qend_t = self.end_time_edit.time()
            if qstart_t >= qend_t:
                QMessageBox.warning(self, "오류", "종료 시간이 시작 시간보다 늦어야 합니다.")
                return

        repeat_days = []
        for i, btn in enumerate(self.weekday_buttons):
            if btn.isChecked():
                repeat_days.append(i)
                
        if self.period_checkbox.isChecked():
            qstart = self.start_date_edit.date()
            qend = self.end_date_edit.date()
            start_date = date(qstart.year(), qstart.month(), qstart.day())
            end_date = date(qend.year(), qend.month(), qend.day())
            if start_date > end_date:
                QMessageBox.warning(self, "오류", "시작일이 종료일보다 늦을 수 없습니다.")
                return
        else:
            start_date = None
            end_date = None
            
        alert_offset = self.offset_combo.currentData()
        
        temp_alarm = Alarm(
            alarm_id=self.alarm.alarm_id if self.alarm else None,
            title=title,
            start_date=start_date,
            end_date=end_date,
            alarm_time=alarm_time,
            repeat_days=repeat_days,
            alert_offset=alert_offset,
            enabled=self.alarm.enabled if self.alarm else True,
            created_at=self.alarm.created_at if self.alarm else datetime.now(),
            hourly_repeat=hourly_repeat,
            hourly_interval=hourly_interval,
            hourly_end_time=hourly_end_time,
        )
        
        next_trigger = calculate_next_alarm_trigger(temp_alarm, datetime.now())
        if next_trigger is None:
            QMessageBox.warning(self, "오류", "유효한 알람 실행 시간을 계산할 수 없습니다. 설정을 확인해 주세요.")
            return
            
        self.saved_alarm = temp_alarm
        self.accept()


class AlarmManagerDialog(QDialog):
    def __init__(self, parent, repository) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("알람 설정")
        self.setWindowIcon(_dialog_icon())
        self.resize(600, 500)
        self.setFixedWidth(600)
        
        self.palette = resolve_palette(parent)
        
        self.setStyleSheet(dialog_stylesheet(self.palette))
        
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        
        # Header
        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel("알람 설정")
        title.setObjectName("title")
        header_text.addWidget(title)
        header.addLayout(header_text)
        
        self.add_btn = QPushButton("알람 추가")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._on_add_alarm)
        header.addWidget(self.add_btn)
        root.addLayout(header)
        
        # Scroll Area for Alarms List
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.addStretch(1)
        
        self.scroll.setWidget(self.scroll_content)
        root.addWidget(self.scroll)
        
        # Bottom Buttons
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        self.close_btn = QPushButton("닫기")
        self.close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(self.close_btn)
        root.addLayout(bottom_layout)
        
        self._load_alarms()

    def _load_alarms(self) -> None:
        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
                
        alarms = self.repository.list_alarms()
        if not alarms:
            no_alarms = QFrame()
            no_alarms.setObjectName("card")
            no_layout = QVBoxLayout(no_alarms)
            no_layout.setContentsMargins(24, 24, 24, 24)
            no_lbl = QLabel("등록된 알람이 없습니다. '알람 추가' 버튼을 눌러 새로운 알람을 등록해 보세요.")
            no_lbl.setObjectName("subtitle")
            no_lbl.setAlignment(Qt.AlignCenter)
            no_layout.addWidget(no_lbl)
            self.scroll_layout.insertWidget(0, no_alarms)
        else:
            for alarm in alarms:
                item_widget = self._create_alarm_item(alarm)
                self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, item_widget)

    def _create_alarm_item(self, alarm: Alarm) -> QWidget:
        card = QFrame()
        card.setObjectName("alarmItem")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        
        enabled_check = QCheckBox()
        enabled_check.setChecked(alarm.enabled)
        enabled_check.toggled.connect(lambda checked: self._on_toggle_alarm(alarm, checked))
        layout.addWidget(enabled_check)
        
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        time_layout = QHBoxLayout()
        time_layout.setSpacing(8)
        
        time_lbl = QLabel(alarm.alarm_time)
        time_lbl.setObjectName("alarmTime")
        time_layout.addWidget(time_lbl)
        
        title_lbl = QLabel(alarm.title)
        title_lbl.setObjectName("alarmTitle")
        time_layout.addWidget(title_lbl)
        time_layout.addStretch(1)
        info_layout.addLayout(time_layout)
        
        repeat_str = self._format_repeat(alarm)
        info_lbl = QLabel(repeat_str)
        info_lbl.setObjectName("alarmInfo")
        info_layout.addWidget(info_lbl)
        
        layout.addLayout(info_layout, 1)
        
        edit_btn = QPushButton("수정")
        edit_btn.clicked.connect(lambda: self._on_edit_alarm(alarm))
        layout.addWidget(edit_btn)
        
        del_btn = QPushButton("삭제")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(lambda: self._on_delete_alarm(alarm))
        layout.addWidget(del_btn)
        
        return card

    def _format_repeat(self, alarm: Alarm) -> str:
        offset_labels = {
            "at_start": "정시",
            "5m": "5분 전",
            "10m": "10분 전",
            "30m": "30분 전",
            "1h": "1시간 전",
        }
        offset_str = offset_labels.get(alarm.alert_offset, "정시")
        
        if alarm.repeat_days:
            if len(alarm.repeat_days) == 7:
                rep = "매일"
            elif sorted(alarm.repeat_days) == [1, 2, 3, 4, 5]:
                rep = "평일"
            elif sorted(alarm.repeat_days) == [0, 6]:
                rep = "주말"
            else:
                weekday_labels = ["일", "월", "화", "수", "목", "금", "토"]
                rep = ", ".join(weekday_labels[d] for d in sorted(alarm.repeat_days))
            rep_str = f"반복: {rep}"
        else:
            rep_str = "1회성"
            
        if alarm.start_date:
            period_str = f"기간: {alarm.start_date.strftime('%Y.%m.%d')} ~ {alarm.end_date.strftime('%Y.%m.%d')}"
        else:
            period_str = ""
            
        parts = [rep_str]
        if period_str:
            parts.append(period_str)
            
        if alarm.hourly_repeat:
            parts.append(f"{alarm.hourly_interval}시간 간격 ({alarm.alarm_time} ~ {alarm.hourly_end_time})")
            
        parts.append(f"알림: {offset_str}")
        return " | ".join(parts)

    def _on_toggle_alarm(self, alarm: Alarm, checked: bool) -> None:
        alarm.enabled = checked
        if checked:
            if not alarm.start_date and not alarm.repeat_days:
                alarm.created_at = datetime.now()
        self.repository.upsert_alarm(alarm)
        next_trigger = calculate_next_alarm_trigger(alarm, datetime.now())
        if checked and next_trigger is None:
            QMessageBox.warning(self, "경고", "이 알람은 유효한 미래 실행 시간이 없으므로 활성화할 수 없습니다.")
            alarm.enabled = False
            self.repository.upsert_alarm(alarm)
            self._load_alarms()

    def _on_add_alarm(self) -> None:
        dialog = AlarmEditDialog(self)
        if dialog.exec() and dialog.saved_alarm:
            self.repository.upsert_alarm(dialog.saved_alarm)
            self._load_alarms()

    def _on_edit_alarm(self, alarm: Alarm) -> None:
        dialog = AlarmEditDialog(self, alarm)
        if dialog.exec() and dialog.saved_alarm:
            dialog.saved_alarm.alarm_id = alarm.alarm_id
            self.repository.upsert_alarm(dialog.saved_alarm)
            self._load_alarms()

    def _on_delete_alarm(self, alarm: Alarm) -> None:
        reply = QMessageBox.question(
            self, "알람 삭제", f"'{alarm.title or '알람'}'을(를) 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if alarm.alarm_id is not None:
                self.repository.delete_alarm(alarm.alarm_id)
                self._load_alarms()


class BackupRestoreFormatDialog(QDialog):
    def __init__(self, parent, mode: str = "export") -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.mode = mode  # "export" or "import"
        self.selected_format = "zip"  # default
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("데이터 내보내기" if mode == "export" else "데이터 가져오기")
        self.resize(480, 260)
        self.setFixedWidth(480)

        # Style sheet
        self.setStyleSheet(dialog_stylesheet(self.palette))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title_text = "내보낼 데이터 형식 선택" if mode == "export" else "가져올 데이터 형식 선택"
        desc_text = "원하는 백업/복원 형식을 선택해 주세요."

        title = QLabel(title_text)
        title.setObjectName("title")
        layout.addWidget(title)

        desc = QLabel(desc_text)
        desc.setObjectName("description")
        layout.addWidget(desc)

        # ZIP Card
        self.zip_card = QFrame()
        self.zip_card.setObjectName("card")
        zip_card_layout = QVBoxLayout(self.zip_card)
        zip_card_layout.setContentsMargins(8, 8, 8, 8)
        zip_card_layout.setSpacing(2)

        self.zip_radio = QRadioButton("ZIP 백업 파일 (.zip) - 권장")
        self.zip_radio.setChecked(True)
        zip_card_layout.addWidget(self.zip_radio)

        zip_desc_text = (
            "일정, 메모, 설정 및 모든 첨부파일을 포함하여 안전하게 백업합니다."
            if mode == "export"
            else "전체 일정, 메모, 설정 및 첨부파일을 백업 파일 상태로 복원합니다.\n(⚠️ 복원 시 현재의 모든 데이터와 첨부파일이 덮어쓰여집니다.)"
        )
        zip_info = QLabel(zip_desc_text)
        if mode == "import":
            zip_info.setObjectName("warning_label")
        else:
            zip_info.setObjectName("info_label")
        zip_info.setWordWrap(True)
        zip_card_layout.addWidget(zip_info)
        layout.addWidget(self.zip_card)

        # Excel Card
        self.excel_card = QFrame()
        self.excel_card.setObjectName("card")
        excel_card_layout = QVBoxLayout(self.excel_card)
        excel_card_layout.setContentsMargins(8, 8, 8, 8)
        excel_card_layout.setSpacing(2)

        self.excel_radio = QRadioButton("Excel 파일 (.xlsx)")
        excel_card_layout.addWidget(self.excel_radio)

        excel_desc_text = (
            "일정과 메모의 텍스트 데이터만 엑셀로 저장합니다.\n(⚠️ 엑셀 형식은 첨부파일을 내보낼 수 없습니다.)"
            if mode == "export"
            else "엑셀 파일로부터 일정 및 메모 데이터를 가져와 현재 데이터에 병합/대체합니다.\n(⚠️ 엑셀 형식은 첨부파일을 가져올 수 없습니다.)"
        )
        excel_info = QLabel(excel_desc_text)
        excel_info.setObjectName("warning_label")
        excel_info.setWordWrap(True)
        excel_card_layout.addWidget(excel_info)
        layout.addWidget(self.excel_card)

        # Button Box
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self.confirm_btn = QPushButton("확인")
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.clicked.connect(self._on_confirm)
        button_layout.addWidget(self.confirm_btn)

        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

    def _on_confirm(self) -> None:
        if self.zip_radio.isChecked():
            self.selected_format = "zip"
        else:
            self.selected_format = "xlsx"
        self.accept()


class CivilComplaintCalculatorDialog(QDialog):
    def __init__(self, parent=None, holidays_fixed: dict[str, str] | None = None, holidays_yearly: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("민원 처리기한 모의계산기")
        self.setFixedWidth(560)
        from taskcalendar.complaint_calculator import ComplaintCalculator, ComplaintCalcResult
        self.calculator = ComplaintCalculator(holidays_fixed, holidays_yearly)
        self.schedule_data: dict | None = None
        self._preset_buttons: list[tuple[QPushButton, str, int]] = []
        self._current_unit = "days"
        self._current_amount = 3
        self._calc_result: ComplaintCalcResult | None = None
        self._updating_custom = False
        self._apply_dialog_styles()
        self._build_ui()
        self._select_preset("days", 3)

    def _apply_dialog_styles(self) -> None:
        self.setStyleSheet(dialog_stylesheet(self.palette))

    def _step_field(self, field: QAbstractSpinBox, button_width: int = 20, button_height: int = 14) -> QWidget:
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(field)

        buttons = QWidget()
        button_layout = QVBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(2)

        up_button = QToolButton()
        up_button.setObjectName("stepButton")
        up_button.setText("▲")
        up_button.setFixedSize(button_width, button_height)
        up_button.clicked.connect(field.stepUp)

        down_button = QToolButton()
        down_button.setObjectName("stepButton")
        down_button.setText("▼")
        down_button.setFixedSize(button_width, button_height)
        down_button.clicked.connect(field.stepDown)

        button_layout.addWidget(up_button)
        button_layout.addWidget(down_button)
        layout.addWidget(buttons)
        return wrap

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        # 1. Header Card
        header_card = QFrame()
        header_card.setObjectName("card")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(3)

        title_lbl = QLabel("민원 처리기한 모의계산기")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.palette['text']};")
        sub_lbl = QLabel("법정 공휴일 및 근무시간(09:00~18:00)을 반영한 마감기한 자동 산정")
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {self.palette['muted']};")
        header_layout.addWidget(title_lbl)
        header_layout.addWidget(sub_lbl)
        root.addWidget(header_card)

        # 2. Received Date/Time Card
        dt_card = QFrame()
        dt_card.setObjectName("card")
        dt_layout = QVBoxLayout(dt_card)
        dt_layout.setContentsMargins(14, 12, 14, 12)
        dt_layout.setSpacing(8)

        dt_title = QLabel("1. 접수 일시 지정")
        dt_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {self.palette['text']};")
        dt_layout.addWidget(dt_title)

        dt_row = QHBoxLayout()
        dt_row.setSpacing(8)

        dt_row.addWidget(QLabel("접수일:"))
        now = datetime.now()
        self.recv_date_edit = OverwriteDateEdit(QDate(now.year, now.month, now.day))
        self.recv_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.recv_date_edit.setCalendarPopup(True)
        self.recv_date_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.recv_date_edit.setFixedWidth(125)
        self.recv_date_edit.setFixedHeight(30)
        self.recv_date_edit.dateChanged.connect(self._recalculate)
        dt_row.addWidget(self.recv_date_edit)

        dt_row.addWidget(QLabel("접수시각:"))
        self.recv_time_edit = OverwriteTimeEdit(QTime(now.hour, now.minute))
        self.recv_time_edit.setDisplayFormat("HH:mm")
        self.recv_time_edit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.recv_time_edit.setFixedWidth(65)
        self.recv_time_edit.setFixedHeight(30)
        self.recv_time_edit.timeChanged.connect(self._recalculate)
        dt_row.addWidget(self._step_field(self.recv_time_edit, 20, 14))

        now_btn = QPushButton("현재시각")
        now_btn.setToolTip("오늘 현재 일시로 재설정")
        now_btn.setFixedHeight(30)
        now_btn.setCursor(Qt.PointingHandCursor)
        now_btn.setStyleSheet(f"padding: 2px 10px; font-size: 11px; font-weight: bold; color: {self.palette['accent']}; background: {self.palette['accent_soft']}; border: 1px solid {self.palette['accent']}; border-radius: 6px;")
        now_btn.clicked.connect(self._set_current_dt)
        dt_row.addWidget(now_btn)

        dt_row.addStretch(1)
        dt_layout.addLayout(dt_row)
        root.addWidget(dt_card)

        # 3. Calculation Method Card
        method_card = QFrame()
        method_card.setObjectName("card")
        method_layout = QVBoxLayout(method_card)
        method_layout.setContentsMargins(14, 12, 14, 12)
        method_layout.setSpacing(6)

        method_title = QLabel("2. 계산 기준 선택")
        method_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {self.palette['text']};")
        method_layout.addWidget(method_title)

        self.radio_public = QRadioButton("공공 표준 (법정 기준: 토·일·공휴일 제외, 09:00~18:00 근무시간 기준)")
        self.radio_public.setChecked(True)
        self.radio_public.toggled.connect(self._recalculate)
        method_layout.addWidget(self.radio_public)

        self.radio_simple = QRadioButton("단순 24시간 연속 계산 (휴일/근무시간 무관)")
        self.radio_simple.toggled.connect(self._recalculate)
        method_layout.addWidget(self.radio_simple)
        root.addWidget(method_card)

        # 4. Duration Card
        dur_card = QFrame()
        dur_card.setObjectName("card")
        dur_layout = QVBoxLayout(dur_card)
        dur_layout.setContentsMargins(14, 12, 14, 12)
        dur_layout.setSpacing(8)

        dur_title = QLabel("3. 처리 기한 선택")
        dur_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {self.palette['text']};")
        dur_layout.addWidget(dur_title)

        presets = [
            ("3시간(즉시)", "hours", 3),
            ("1일", "days", 1),
            ("2일", "days", 2),
            ("3일", "days", 3),
            ("5일", "days", 5),
            ("7일", "days", 7),
            ("10일", "days", 10),
            ("14일", "days", 14),
            ("30일", "days", 30),
        ]
        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        for label, unit, val in presets:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, u=unit, v=val: self._select_preset(u, v))
            preset_row.addWidget(btn)
            self._preset_buttons.append((btn, unit, val))
        dur_layout.addLayout(preset_row)

        direct_row = QHBoxLayout()
        direct_row.setSpacing(6)
        direct_row.addWidget(QLabel("직접 입력:"))
        self.custom_spin = QSpinBox()
        self.custom_spin.setRange(1, 999)
        self.custom_spin.setValue(3)
        self.custom_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.custom_spin.setFixedWidth(55)
        self.custom_spin.setFixedHeight(28)
        self.custom_spin.valueChanged.connect(self._on_custom_changed)
        direct_row.addWidget(self._step_field(self.custom_spin, 20, 13))

        self.custom_unit_combo = QComboBox()
        self.custom_unit_combo.addItem("일 (영업일)", "days")
        self.custom_unit_combo.addItem("시간 (근무시간)", "hours")
        self.custom_unit_combo.setFixedWidth(120)
        self.custom_unit_combo.setFixedHeight(28)
        self.custom_unit_combo.currentIndexChanged.connect(self._on_custom_changed)
        direct_row.addWidget(self.custom_unit_combo)
        direct_row.addStretch(1)
        dur_layout.addLayout(direct_row)

        root.addWidget(dur_card)

        # 5. Result Card
        self.result_card = QFrame()
        self.result_card.setStyleSheet(f"""
            QFrame {{
                background: {self.palette['accent_soft']};
                border: 1.5px solid {self.palette['accent']};
                border-radius: 10px;
            }}
        """)
        res_layout = QVBoxLayout(self.result_card)
        res_layout.setContentsMargins(14, 12, 14, 12)
        res_layout.setSpacing(6)

        res_head = QHBoxLayout()
        res_tag = QLabel("최종 법정 처리 마감일시")
        res_tag.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {self.palette['accent']}; background: transparent; border: none;")
        res_head.addWidget(res_tag)
        res_head.addStretch(1)

        self.res_badge = QLabel("")
        self.res_badge.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {self.palette.get('badge_today_fg', self.palette['text'])}; background: {self.palette.get('badge_today_bg', self.palette['panel_alt'])}; border: 1px solid {self.palette['line']}; border-radius: 4px; padding: 2px 6px;")
        res_head.addWidget(self.res_badge)
        res_layout.addLayout(res_head)

        self.res_due_label = QLabel("")
        self.res_due_label.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {self.palette['text']}; background: transparent; border: none; padding: 4px 0;")
        res_layout.addWidget(self.res_due_label)

        self.res_detail_label = QLabel("")
        self.res_detail_label.setStyleSheet(f"font-size: 12px; color: {self.palette['text']}; background: transparent; border: none;")
        self.res_detail_label.setWordWrap(True)
        res_layout.addWidget(self.res_detail_label)

        self.res_excluded_label = QLabel("")
        self.res_excluded_label.setStyleSheet(f"font-size: 11px; color: {self.palette['danger']}; font-weight: 600; background: transparent; border: none;")
        self.res_excluded_label.setWordWrap(True)
        res_layout.addWidget(self.res_excluded_label)

        root.addWidget(self.result_card)

        # Notice/Disclaimer Card
        notice_card = QFrame()
        notice_card.setStyleSheet(f"background: {self.palette['panel_alt']}; border: 1px solid {self.palette['line']}; border-radius: 6px;")
        notice_layout = QHBoxLayout(notice_card)
        notice_layout.setContentsMargins(10, 7, 10, 7)
        notice_lbl = QLabel("※ 본 계산 결과는 법령 및 공휴일 데이터에 따른 모의계산 참고용이며, 실제 민원 사무 처리 및 최종 마감기한 확인에 대한 책임은 사용자 본인에게 있습니다.")
        notice_lbl.setStyleSheet(f"font-size: 11px; color: {self.palette['muted']}; background: transparent; border: none;")
        notice_lbl.setWordWrap(True)
        notice_layout.addWidget(notice_lbl)
        root.addWidget(notice_card)

        # 6. Bottom Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        close_btn = QPushButton("닫기")
        close_btn.setFixedHeight(34)
        close_btn.setMinimumWidth(80)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        btn_row.addStretch(1)

        self.register_btn = QPushButton("이 기한으로 일정 등록")
        self.register_btn.setFixedHeight(34)
        self.register_btn.setMinimumWidth(160)
        self.register_btn.setObjectName("primary")
        self.register_btn.setCursor(Qt.PointingHandCursor)
        self.register_btn.clicked.connect(self._on_register_schedule)
        btn_row.addWidget(self.register_btn)

        root.addLayout(btn_row)

    def _set_current_dt(self) -> None:
        now = datetime.now()
        self.recv_date_edit.setDate(QDate(now.year, now.month, now.day))
        self.recv_time_edit.setTime(QTime(now.hour, now.minute))
        self._recalculate()

    def _select_preset(self, unit: str, val: int) -> None:
        self._current_unit = unit
        self._current_amount = val

        self._updating_custom = True
        self.custom_spin.setValue(val)
        idx = self.custom_unit_combo.findData(unit)
        if idx >= 0:
            self.custom_unit_combo.setCurrentIndex(idx)
        self._updating_custom = False

        self._update_preset_styles()
        self._recalculate()

    def _on_custom_changed(self) -> None:
        if self._updating_custom:
            return
        self._current_amount = self.custom_spin.value()
        self._current_unit = str(self.custom_unit_combo.currentData())
        self._update_preset_styles()
        self._recalculate()

    def _update_preset_styles(self) -> None:
        for btn, unit, val in self._preset_buttons:
            if unit == self._current_unit and val == self._current_amount:
                btn.setStyleSheet("""
                    QPushButton {
                        font-size: 11px;
                        font-weight: bold;
                        color: #ffffff;
                        background: #1f7a67;
                        border: 1px solid #1f7a67;
                        border-radius: 4px;
                        padding: 2px 4px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        font-size: 11px;
                        font-weight: 500;
                        color: #334155;
                        background: #f8fafc;
                        border: 1px solid #cbd5e1;
                        border-radius: 4px;
                        padding: 2px 4px;
                    }
                    QPushButton:hover {
                        background: #e0f2fe;
                        border-color: #0284c7;
                        color: #0284c7;
                    }
                """)

    def _recalculate(self) -> None:
        qd = self.recv_date_edit.date()
        qt = self.recv_time_edit.time()
        recv_dt = datetime(qd.year(), qd.month(), qd.day(), qt.hour(), qt.minute())

        is_public = self.radio_public.isChecked()
        res = self.calculator.calculate(
            received_dt=recv_dt,
            amount=self._current_amount,
            unit=self._current_unit,
            is_public_standard=is_public,
        )
        self._calc_result = res

        weekdays_kr = ("월", "화", "수", "목", "금", "토", "일")
        wk = weekdays_kr[res.due_dt.weekday()]
        due_str = f"{res.due_dt.year}년 {res.due_dt.month}월 {res.due_dt.day}일 ({wk}) {res.due_dt.strftime('%H:%M')}"
        self.res_due_label.setText(due_str)

        unit_str = "시간" if res.unit == "hours" else "영업일"
        self.res_badge.setText(f"소요: {res.amount}{unit_str} (달력상 {res.total_calendar_days}일 경과)")

        if is_public:
            self.res_detail_label.setText(f"기준: 『민원 처리에 관한 법률』 준용 ({res.description})")
        else:
            self.res_detail_label.setText(f"기준: {res.description}")

        if res.excluded_days:
            exc_str = ", ".join([f"{d.month}/{d.day}({r})" for d, r in res.excluded_days])
            self.res_excluded_label.setText(f"[제외된 일자 ({len(res.excluded_days)}일)] {exc_str}")
        else:
            self.res_excluded_label.setText("[제외된 일자 없음] 정상 영업일 산정")

    def _on_register_schedule(self) -> None:
        if self._calc_result is None:
            return
        qd = self.recv_date_edit.date()
        qt = self.recv_time_edit.time()
        start_date = date(qd.year(), qd.month(), qd.day())
        start_time = f"{qt.hour():02d}:{qt.minute():02d}"

        unit_text = "시간" if self._current_unit == "hours" else "일"
        title = f"[민원] {self._current_amount}{unit_text} 민원 처리 마감"
        due_str = self._calc_result.due_dt.strftime("%Y-%m-%d %H:%M")
        std_str = "공공표준(법정기준)" if self.radio_public.isChecked() else "단순 24시간 연속"

        desc_lines = [
            title,
            f"• 접수일시: {start_date} {start_time}",
            f"• 처리기한: {self._current_amount}{unit_text} ({self.res_badge.text()})",
            f"• 최종마감: {due_str}",
            f"• 적용기준: {std_str}",
        ]
        if self._calc_result.excluded_days:
            exc_str = ", ".join([f"{d.month}/{d.day}({r})" for d, r in self._calc_result.excluded_days])
            desc_lines.append(f"• 제외일자: {exc_str}")
        desc_text = "\n".join(desc_lines)

        self.schedule_data = {
            "title": title,
            "start_date": start_date,
            "start_time": start_time,
            "end_date": self._calc_result.due_dt.date(),
            "end_time": self._calc_result.due_dt.strftime("%H:%M"),
            "description": desc_text,
        }
        self.accept()


class WelcomeFeatureIntroDialog(QDialog):
    def __init__(self, parent=None, is_dismissed: bool = False) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.setWindowTitle("K캘린더 기능 안내 & 팁")
        self.setWindowIcon(_dialog_icon())
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(520, 440)
        self.open_settings_requested = False

        self.setStyleSheet(dialog_stylesheet(self.palette))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        header = QFrame()
        header.setObjectName("headerBox")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(4)
        h_title = QLabel("💡 K캘린더 기능 소개 & 안내")
        h_title.setObjectName("headerTitle")
        h_sub = QLabel("환경설정에서 업무 스타일에 맞춰 다양한 기능을 자유롭게 On/Off 할 수 있습니다.")
        h_sub.setObjectName("headerSubtitle")
        h_layout.addWidget(h_title)
        h_layout.addWidget(h_sub)
        layout.addWidget(header)

        items = [
            ("⚙️ 다양한 기능 맞춤 On/Off (환경설정)", "상단 우측 [환경설정]에서 음력·24절기 표시, 스티커 애니메이션, 완료 일정 숨기기, 자동 백업 등 필요 없는 기능은 끄고 원하는 기능만 켜서 가볍고 깔끔하게 사용할 수 있습니다."),
            ("📝 스마트 플로팅 메모 & 서식 에디터", "바탕화면에 메모를 자유롭게 띄우며, 내용/배경 마우스 우클릭 [에디터 보기/닫기]를 통해 상단 서식 도구(굵게, 폰트, 크기, 색상)로 메모를 손쉽게 편집할 수 있습니다."),
            ("⌨️ 언제 어디서나 전역 단축키 (F3)", "다른 작업 중에도 언제든지 F3 키를 누르면 캘린더가 즉시 열리거나 숨겨집니다. (단축키는 환경설정에서 변경 가능)"),
        ]

        for item_title_text, item_desc_text in items:
            card = QFrame()
            card.setObjectName("itemCard")
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(12, 8, 12, 8)
            c_layout.setSpacing(3)
            lbl_t = QLabel(item_title_text)
            lbl_t.setObjectName("itemTitle")
            lbl_d = QLabel(item_desc_text)
            lbl_d.setObjectName("itemDesc")
            lbl_d.setWordWrap(True)
            c_layout.addWidget(lbl_t)
            c_layout.addWidget(lbl_d)
            layout.addWidget(card)

        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.dismiss_check = QCheckBox("다시 보지 않기")
        self.dismiss_check.setChecked(is_dismissed)
        self.dismiss_check.setStyleSheet(f"color: {self.palette['muted']}; font-size: 12px; font-weight: 500;")
        footer.addWidget(self.dismiss_check)
        footer.addStretch(1)

        settings_btn = QPushButton("⚙️ 환경설정 열기")
        settings_btn.setObjectName("secondaryBtn")
        settings_btn.clicked.connect(self._on_open_settings)
        footer.addWidget(settings_btn)

        confirm_btn = QPushButton("확인")
        confirm_btn.setObjectName("primaryBtn")
        confirm_btn.clicked.connect(self.accept)
        footer.addWidget(confirm_btn)

        layout.addLayout(footer)

    def _on_open_settings(self) -> None:
        self.open_settings_requested = True
        self.accept()

    def is_dismissed_checked(self) -> bool:
        return self.dismiss_check.isChecked()



