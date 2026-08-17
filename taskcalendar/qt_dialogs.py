from __future__ import annotations

from datetime import date, datetime
import ctypes
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

from PySide6.QtCore import QDate, QTime, Qt, QTimer, QRect, QPoint, QSize, QUrl, QEvent
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextCursor, QPainter, QPen, QColor, QDesktopServices, QCursor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from taskcalendar.rich_text_edit import RichTextEdit
from taskcalendar.models import (
    ALERT_OPTIONS,
    COLOR_OPTIONS,
    ICON_OPTIONS,
    RECURRENCE_OPTIONS,
    WEEKDAY_LABELS,
    AlertType,
    CalendarEntry,
    EntryType,
    RecurrenceType,
    THEME_OPTIONS,
    Alarm,
    calculate_next_alarm_trigger,
)
from taskcalendar.desktop_services import _parse_hotkey, normalize_shortcut
from taskcalendar.paths import asset_path

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
    
    # 1. Screen edge snapping
    if abs(x - screen_geo.left()) <= threshold:
        x = screen_geo.left()
    elif abs((x + w) - screen_geo.right()) <= threshold:
        x = screen_geo.right() - w
        
    if abs(y - screen_geo.top()) <= threshold:
        y = screen_geo.top()
    elif abs((y + h) - screen_geo.bottom()) <= threshold:
        y = screen_geo.bottom() - h
        
    # 2. Other memo windows snapping
    for other in other_geos:
        # Horizontal docking & alignment
        if abs((x + w) - other.left()) <= threshold:
            x = other.left() - w
        elif abs(x - other.right()) <= threshold:
            x = other.right()
        elif abs(x - other.left()) <= threshold:
            x = other.left()
        elif abs((x + w) - other.right()) <= threshold:
            x = other.right() - w
            
        # Vertical docking & alignment
        if abs((y + h) - other.top()) <= threshold:
            y = other.top() - h
        elif abs(y - other.bottom()) <= threshold:
            y = other.bottom()
        elif abs(y - other.top()) <= threshold:
            y = other.top()
        elif abs((y + h) - other.bottom()) <= threshold:
            y = other.bottom() - h
            
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
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isReadOnly() and event.buttons() == Qt.MouseButton.LeftButton:
            dlg = self.window()
            if dlg and hasattr(dlg, "_perform_window_drag"):
                dlg._perform_window_drag(event.globalPosition().toPoint())
                event.accept()
                return
        super().mouseMoveEvent(event)


class EntryDialog(QDialog):
    def __init__(self, parent, entry_type: EntryType, selected_day: date | None, entry: CalendarEntry | None = None, restore_mode: bool = False) -> None:
        self._owner_window = parent
        logger.info(f"[EntryDialog.__init__] entry_type={entry_type}, id={entry.entry_id if entry else None}, title='{entry.title if entry else ''}', restore={restore_mode}")
        if entry_type == EntryType.MEMO:
            super().__init__(None)
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
            self.setModal(False)
        else:
            super().__init__(parent)
            self.setModal(True)
        self.palette = parent.palette if (parent and hasattr(parent, "palette")) else {"text": "#333333", "line": "#e2e8f0", "muted": "#718096"}
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
            self._current_memo_theme = entry.bg_color if (entry and entry.bg_color in MEMO_THEMES) else "yellow"
            self._is_floating = (entry and entry.icon_type == "floating") if entry else False
            self.setMinimumWidth(180)
            self.setMinimumHeight(34)
            
            # Load remembered geometry, collapse state, and opacity
            has_saved_geo = False
            if entry and entry.entry_id:
                parent = getattr(self, "_owner_window", None)
                if parent and hasattr(parent, "repository"):
                    geo_str = parent.repository.get_setting(f"memo_geo_{entry.entry_id}", "")
                    if geo_str:
                        try:
                            pts = [int(p) for p in geo_str.split(",")]
                            if len(pts) == 4:
                                self.setGeometry(pts[0], pts[1], max(180, pts[2]), max(150, pts[3]))
                                self._expanded_height = max(150, pts[3])
                                has_saved_geo = True
                        except Exception:
                            pass
                    collapsed_saved = parent.repository.get_setting(f"memo_collapsed_{entry.entry_id}", "0") == "1"
                    self._is_collapsed = (collapsed_saved if restore_mode else False)
                    self._expanded_height = max(150, getattr(self, "_expanded_height", 360))
                    opacity_saved = parent.repository.get_setting(f"memo_opacity_{entry.entry_id}", "100")
                    try:
                        self.setWindowOpacity(int(opacity_saved) / 100.0)
                    except Exception:
                        pass
            
            if not has_saved_geo:
                screen = self.screen() or QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    cx = avail.x() + (avail.width() - 380) // 2
                    cy = avail.y() + (avail.height() - 360) // 2
                    self.setGeometry(cx, cy, 380, 360)
                else:
                    self.resize(380, 360)
            else:
                screen = self.screen() or QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    geo = self.geometry()
                    nx = max(avail.left(), min(geo.x(), avail.right() - 100))
                    ny = max(avail.top(), min(geo.y(), avail.bottom() - 34))
                    self.move(nx, ny)

            if getattr(self, "_is_collapsed", False):
                self.setFixedHeight(34)
                
            self.setMouseTracking(True)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            # Header bar with integrated title field
            self.header = QWidget()
            self.header.setFixedHeight(34)
            self.header.setMouseTracking(True)
            self.header.installEventFilter(self)
            h_layout = QHBoxLayout(self.header)
            h_layout.setContentsMargins(8, 0, 6, 0)
            h_layout.setSpacing(4)

            self._pin_icon = QLabel()
            self._pin_icon.setPixmap(QIcon(str(asset_path("memo_pin.svg"))).pixmap(18, 18))
            self._pin_icon.setFixedSize(18, 18)
            self._pin_icon.setStyleSheet("background: transparent; border: none;")
            self._pin_icon.setToolTip("항상 위에 표시 중")
            self._pin_icon.setVisible(self._is_floating)
            h_layout.addWidget(self._pin_icon)

            self.title_input = EditableTitleLineEdit(entry.title if entry else "", self.header)
            self.title_input.setPlaceholderText("메모 제목")
            self._update_title_input_width()
            self.title_input.textChanged.connect(self._update_title_input_width)
            h_layout.addWidget(self.title_input)

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
            content_layout = QVBoxLayout(self.content_wrap)
            content_layout.setContentsMargins(6, 4, 6, 6)
            content_layout.setSpacing(4)

            self.description_input = RichTextEdit()
            self.description_input.setPlaceholderText("메모 내용을 입력하세요...")
            self.description_input.setTabChangesFocus(True)
            self.description_input.document().setDocumentMargin(2)
            desc_val = entry.description if entry else ""
            if desc_val.strip().startswith("<"):
                self.description_input.setHtml(desc_val)
            else:
                self.description_input.setPlainText(desc_val)
            content_layout.addWidget(self.description_input, 1)

            bottom_row = QHBoxLayout()
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

            content_layout.addLayout(bottom_row)
            root.addWidget(self.content_wrap, 1)

            # Apply initial collapse state if remembered
            if getattr(self, "_is_collapsed", False):
                self.content_wrap.hide()
                self.setFixedHeight(34)

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
            details_layout.setColumnMinimumWidth(0, 40)
            icon_label = self._muted("아이콘")
            icon_label.setFixedWidth(FORM_LABEL_WIDTH)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            details_layout.addWidget(icon_label, 0, 0)
            self.icon_combo = QComboBox()
            for label, value in ICON_OPTIONS:
                preview = ICON_PREVIEW_EMOJI.get(str(value), "")
                display = f"{preview} {label}".strip() if preview else label
                self.icon_combo.addItem(display, value)
            self.icon_combo.setCurrentIndex(max(0, self.icon_combo.findData(entry.icon_type if entry else "")))
            self.icon_combo.setMinimumWidth(112)
            self.icon_combo.setMaximumWidth(112)
            self.icon_combo.setMinimumHeight(30)
            self.icon_combo.setMaximumHeight(30)
            self.icon_combo.view().setMinimumWidth(112)
            details_layout.addWidget(self.icon_combo, 0, 1)

            bg_row = QWidget()
            bg_row_layout = QHBoxLayout(bg_row)
            bg_row_layout.setContentsMargins(0, 0, 0, 0)
            bg_row_layout.setSpacing(8)
            bg_color_label = self._muted("배경색")
            bg_color_label.setFixedWidth(FORM_LABEL_WIDTH)
            bg_color_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            bg_row_layout.addWidget(bg_color_label)
            self.bg_color_combo = QComboBox()
            for label, value in COLOR_OPTIONS:
                self.bg_color_combo.addItem(label, value)
            self.bg_color_combo.setCurrentIndex(max(0, self.bg_color_combo.findData(entry.bg_color if entry else "")))
            self.bg_color_combo.setMinimumWidth(112)
            self.bg_color_combo.setMaximumWidth(112)
            self.bg_color_combo.setMinimumHeight(30)
            self.bg_color_combo.setMaximumHeight(30)
            bg_row_layout.addWidget(self.bg_color_combo)
            details_layout.addWidget(bg_row, 0, 3, 1, 3, Qt.AlignmentFlag.AlignLeft)

            when_label = self._muted("일시")
            when_label.setFixedWidth(FORM_LABEL_WIDTH)
            when_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            details_layout.addWidget(when_label, 1, 0)
            self.start_date = OverwriteDateEdit(_to_qdate(base_day))
            self.start_date.setDisplayFormat("yyyy-MM-dd")
            self.start_date.setCalendarPopup(True)
            self.start_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.start_date.setMinimumWidth(112)
            self.start_date.setMaximumWidth(112)
            self.start_date.dateChanged.connect(self._refresh_repeat_details)
            details_layout.addWidget(self.start_date, 1, 1)
            self.start_time = OverwriteTimeEdit(_to_qtime(entry.start_time if entry else "", "09:00"))
            self.start_time.setDisplayFormat("HH:mm")
            self.start_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.start_time.setMinimumWidth(58)
            self.start_time.setMaximumWidth(58)
            self.start_time.setMinimumHeight(30)
            start_time_field = self._step_field(self.start_time, 20)

            end_row = QWidget()
            end_row_layout = QHBoxLayout(end_row)
            end_row_layout.setContentsMargins(0, 0, 0, 0)
            end_row_layout.setSpacing(6)
            end_row_layout.addWidget(start_time_field)
            end_row_layout.addWidget(self._muted("~"))
            self.end_date = OverwriteDateEdit(_to_qdate(entry.end_date if entry else base_day))
            self.end_date.setDisplayFormat("yyyy-MM-dd")
            self.end_date.setCalendarPopup(True)
            self.end_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.end_date.setMinimumWidth(112)
            self.end_date.setMaximumWidth(112)
            end_row_layout.addWidget(self.end_date)
            self.end_time = OverwriteTimeEdit(_to_qtime(entry.end_time if entry else "", "18:00"))
            self.end_time.setDisplayFormat("HH:mm")
            self.end_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.end_time.setMinimumWidth(58)
            self.end_time.setMaximumWidth(58)
            self.end_time.setMinimumHeight(30)
            end_row_layout.addWidget(self._step_field(self.end_time, 20))
            self.all_day = QCheckBox("종일")
            self.all_day.setChecked(entry.all_day if entry else True)
            self.all_day.toggled.connect(self._toggle_all_day)
            end_row_layout.addWidget(self.all_day)
            end_row_layout.addStretch(1)
            details_layout.addWidget(end_row, 1, 2, 1, 5)
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

        title_row = QHBoxLayout()
        title_row.addWidget(self._section_title("내용"))
        title_row.addStretch(1)

        self.add_image_button = QPushButton("이미지 추가")
        self.add_image_button.setObjectName("topbarButton")
        self.add_image_button.setCursor(Qt.PointingHandCursor)
        self.add_image_button.clicked.connect(self._add_image_from_file)
        title_row.addWidget(self.add_image_button)
        content_layout.addLayout(title_row)

        self.description_input = RichTextEdit()
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
        layout.setVerticalSpacing(7)
        return frame, layout

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("separator")
        return line

    def _apply_styles(self) -> None:
        check_icon = asset_path("checkmark.svg").as_posix()
        self.setStyleSheet(
            """
            QDialog#entryDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel {
                color: #1f2328;
            }
            QLabel#muted {
                color: #667085;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#sectionTitle {
                color: #223044;
                font-size: 13px;
                font-weight: 700;
                padding-bottom: 2px;
            }
            QLabel#hint {
                color: #667085;
                font-size: 12px;
                font-weight: 600;
                padding-right: 2px;
            }
            QFrame#card, QFrame#softCard {
                border: 1px solid #dbe3ec;
                border-radius: 12px;
            }
            QFrame#card {
                background: #ffffff;
            }
            QFrame#softCard {
                background: #f8fafc;
            }
            QFrame#separator {
                background: #e5ebf2;
                max-height: 1px;
                border: none;
            }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                color: #1f2328;
            }
            QLineEdit, QComboBox, QDateEdit {
                min-height: 20px;
                padding: 4px 8px;
            }
            QTimeEdit, QSpinBox {
                min-height: 20px;
                padding: 4px 8px;
            }
            QPlainTextEdit {
                padding: 8px;
                selection-background-color: #cfe7df;
            }
            QCheckBox, QRadioButton {
                color: #1f2328;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #788496;
                background: #ffffff;
                border-radius: 0px;
            }
            QCheckBox::indicator:checked {
                background: #d7ece6;
                border: 1px solid #1f7a67;
            }
            """
            + f"""
            QCheckBox::indicator:checked {{
                image: url("{check_icon}");
            }}
            """
            + """
            QPushButton, QDialogButtonBox QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 7px 16px;
                min-width: 88px;
            }
            QPushButton:focus, QDialogButtonBox QPushButton:focus, QToolButton:focus {
                outline: none;
            }
            QToolButton#stepButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 0px;
                font-size: 9px;
                color: #344054;
            }
            QToolButton#stepButton:hover {
                background: #f7fafc;
            }
            QPushButton:hover, QDialogButtonBox QPushButton:hover {
                background: #f7fafc;
            }
            QPushButton#primary, QDialogButtonBox QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
            }
            QPushButton#primary:hover, QDialogButtonBox QPushButton#primary:hover {
                background: #236f60;
            }
            QComboBox:disabled, QDateEdit:disabled, QTimeEdit:disabled, QSpinBox:disabled {
                background: #e2e8f0;
                color: #8a94a6;
                border: 1px solid #d1d8e2;
            }
            """
        )

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

    def _sync_recurrence_interval_from_weekly(self, value: int) -> None:
        if self._syncing_interval:
            return
        self._syncing_interval = True
        self.recurrence_interval.setValue(max(1, value))
        self._syncing_interval = False
        self._refresh_repeat_details()

    def _refresh_repeat_details(self) -> None:
        recurrence = self.recurrence_combo.currentData()
        self.interval_wrap.setVisible(recurrence == RecurrenceType.DAILY.value)
        self.month_day_wrap.setVisible(recurrence == RecurrenceType.MONTHLY.value)
        self.month_week_wrap.setVisible(recurrence == RecurrenceType.MONTHLY_NTH.value)
        self.weekday_wrap.setVisible(recurrence == RecurrenceType.WEEKLY.value)
        self.recurrence_summary.setVisible(recurrence != RecurrenceType.WEEKLY.value)
        if recurrence == RecurrenceType.YEARLY.value:
            self.recurrence_summary.setText(f"매년 {self.start_date.date().toString('MM월 dd일')}")
        elif recurrence == RecurrenceType.MONTHLY.value:
            if self.recurrence_month_end_check.isChecked():
                self.recurrence_summary.setText("매월 말일")
            else:
                self.recurrence_summary.setText(f"매월 {self.recurrence_month_day.value()}일")
        elif recurrence == RecurrenceType.MONTHLY_NTH.value:
            week_label = str(self.recurrence_month_week_combo.currentText())
            weekday_label = str(self.recurrence_month_weekday_combo.currentText())
            self.recurrence_summary.setText(f"매월 {week_label} {weekday_label}요일")
        elif recurrence == RecurrenceType.WEEKLY.value:
            selected = [label for label, checkbox in zip(WEEKDAY_LABELS, self.weekday_checks) if checkbox.isChecked()]
            self.recurrence_summary.setText(" ".join(selected) if selected else "요일 선택")
        else:
            interval = max(1, self.recurrence_interval.value())
            self.recurrence_summary.setText("매일 반복" if interval == 1 else f"{interval}일 간격")

    def _add_attachment(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(self, "첨부파일 선택")
        if filenames:
            self.attachments.extend(filenames)
            self.attachments_label.setText(self._attachments_text())
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
            html_content = self.description_input.toHtml()
            description_to_save = html_content if "<img" in html_content else self.description_input.toPlainText().strip()
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

        plain_content = self.description_input.toPlainText().strip()
        default_title = plain_content.splitlines()[0].strip()[:40] if plain_content else "일정"
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
            recurrence_month_day=self.recurrence_month_day.value(),
            recurrence_month_week=int(self.recurrence_month_week_combo.currentData()),
            recurrence_month_end=self.recurrence_month_end_check.isChecked(),
            icon_type=str(self.icon_combo.currentData()),
            bg_color=str(self.bg_color_combo.currentData()),
            alert_type=AlertType.POPUP if self.alert_popup.isChecked() else AlertType.NONE,
            alert_offset=str(self.alert_offset_combo.currentData()),
        )
        self.accept()

    def _trigger_auto_save(self) -> None:
        if self.entry_type == EntryType.MEMO:
            self._save_timer.start(1000)

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
        self.description_input.setStyleSheet(f"border: none; background: transparent; font-size: 13px; padding: 0px; color: {theme['text']};")
        if hasattr(self, "_close_btn"):
            self._close_btn.setStyleSheet(f"background: transparent; border: none; font-weight: bold; color: {theme['text']}; font-size: 13px;")
        if hasattr(self, "_collapse_btn"):
            self._collapse_btn.setStyleSheet(f"background: transparent; border: none; font-weight: bold; color: {theme['text']}; font-size: 14px;")
        if hasattr(self, "_pin_btn"):
            self._pin_btn.setStyleSheet(f"background: transparent; border: none; font-weight: bold; color: {theme['text']}; font-size: 13px;")
            
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

    def eventFilter(self, watched, event) -> bool:
        if self.entry_type == EntryType.MEMO and watched is getattr(self, "header", None):
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                border = 8
                if pos.x() >= self.header.width() - border:
                    self.header.setCursor(Qt.CursorShape.SizeHorCursor)
                else:
                    self.header.setCursor(Qt.CursorShape.ArrowCursor)
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                border = 8
                if pos.x() >= self.header.width() - border:
                    self._resize_dir = "r"
                    self._initial_geometry = self.geometry()
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                    return True
        return super().eventFilter(watched, event)

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

    def _create_new_memo(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "_edit_entry"):
            parent._edit_entry(EntryType.MEMO, None)

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
            parent = getattr(self, "_owner_window", None) or self.parent()
            if self.entry and self.entry.entry_id and parent and hasattr(parent, "repository"):
                parent.repository.delete_entry(self.entry.entry_id)
                if hasattr(parent, "_load_memo_order_ids") and hasattr(parent, "_save_memo_order_ids"):
                    ids = [m_id for m_id in parent._load_memo_order_ids() if m_id != int(self.entry.entry_id)]
                    parent._save_memo_order_ids(ids, persist=False)
                parent.repository.save()
                parent.refresh()
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

    def _toggle_floating(self, checked: bool) -> None:
        self._is_floating = checked
        self._set_topmost_native(checked)
        if hasattr(self, "_pin_icon"):
            self._pin_icon.setVisible(checked)
        self._auto_save_to_db()

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
            self._auto_save_to_db()

    def _toggle_collapse(self) -> None:
        self._is_collapsed = not getattr(self, "_is_collapsed", False)
        if self._is_collapsed:
            self._expanded_height = self.height()
            if hasattr(self, "content_wrap"):
                self.content_wrap.hide()
            self.setFixedHeight(34)
        else:
            if hasattr(self, "content_wrap"):
                self.content_wrap.show()
            self.setMinimumHeight(200)
            self.setMaximumHeight(16777215)
            self.resize(self.width(), getattr(self, "_expanded_height", 420))
            
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and hasattr(parent, "repository") and self.entry and self.entry.entry_id:
            parent.repository.set_setting(f"memo_collapsed_{self.entry.entry_id}", "1" if self._is_collapsed else "0")
            h_val = getattr(self, "_expanded_height", self.height())
            parent.repository.set_setting(f"memo_geo_{self.entry.entry_id}", f"{self.x()},{self.y()},{self.width()},{h_val}")

    def _start_window_drag(self, global_pos: QPoint) -> None:
        self._drag_position = global_pos - self.frameGeometry().topLeft()

    def _perform_window_drag(self, global_pos: QPoint) -> None:
        if hasattr(self, "_drag_position"):
            target_pos = global_pos - self._drag_position
            curr_geo = QRect(target_pos, self.size())
            
            parent = getattr(self, "_owner_window", None) or self.parent()
            other_geos: list[QRect] = []
            if parent and hasattr(parent, "_active_memo_dialogs"):
                for dlg in parent._active_memo_dialogs.values():
                    if dlg is not self and dlg.isVisible():
                        other_geos.append(dlg.geometry())
                        
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
        description_to_save = html_content if "<img" in html_content else plain_content
        
        from taskcalendar.models import CalendarEntry
        self.result = CalendarEntry(
            entry_type=self.entry_type,
            title=title or "제목 없음",
            description=description_to_save,
            attachments=list(self.attachments),
            bg_color=getattr(self, "_current_memo_theme", "yellow"),
            icon_type="floating" if getattr(self, "_is_floating", False) else "",
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
                curr_geo = self.geometry()
                h_val = getattr(self, "_expanded_height", curr_geo.height()) if getattr(self, "_is_collapsed", False) else curr_geo.height()
                parent.repository.set_setting(f"memo_geo_{saved.entry_id}", f"{curr_geo.x()},{curr_geo.y()},{curr_geo.width()},{h_val}")
                parent.repository.set_setting(f"memo_collapsed_{saved.entry_id}", "1" if getattr(self, "_is_collapsed", False) else "0")
                
            if persist_disk:
                parent.repository.save()
            if refresh_parent:
                parent.refresh()

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
        if self.entry_type == EntryType.MEMO:
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and getattr(parent, "_is_app_quitting", False):
                self.close()
                return
            try:
                self._auto_save_to_db(persist_disk=True, refresh_parent=True)
            except Exception:
                pass
            if parent and hasattr(parent, "_active_memo_dialogs"):
                to_remove = [k for k, v in list(parent._active_memo_dialogs.items()) if v is self]
                for k in to_remove:
                    parent._active_memo_dialogs.pop(k, None)
                if hasattr(parent, "_sync_open_memo_ids"):
                    parent._sync_open_memo_ids(persist=True)
            self.close()

    def closeEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            parent = getattr(self, "_owner_window", None) or self.parent()
            if parent and getattr(parent, "_is_app_quitting", False):
                super().closeEvent(event)
                return
            try:
                self._auto_save_to_db(persist_disk=True, refresh_parent=True)
            except Exception:
                pass
            if parent and hasattr(parent, "_active_memo_dialogs"):
                to_remove = [k for k, v in list(parent._active_memo_dialogs.items()) if v is self]
                for k in to_remove:
                    parent._active_memo_dialogs.pop(k, None)
                if hasattr(parent, "_sync_open_memo_ids"):
                    parent._sync_open_memo_ids(persist=True)
        super().closeEvent(event)

    def reject(self) -> None:
        parent = getattr(self, "_owner_window", None) or self.parent()
        if parent and getattr(parent, "_is_app_quitting", False):
            super().reject()
            return
        if self.entry_type == EntryType.MEMO:
            self._close_memo()
        super().reject()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO and event.button() == Qt.MouseButton.LeftButton:
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
                pos = event.position().toPoint()
                rect = self.rect()
                border = 8
                self._resize_dir = None
                if pos.x() >= rect.width() - border and pos.y() >= rect.height() - border and not getattr(self, "_is_collapsed", False):
                    self._resize_dir = "br"
                elif pos.x() <= border and pos.y() >= rect.height() - border and not getattr(self, "_is_collapsed", False):
                    self._resize_dir = "bl"
                elif pos.x() >= rect.width() - border:
                    self._resize_dir = "r"
                elif pos.y() >= rect.height() - border and not getattr(self, "_is_collapsed", False):
                    self._resize_dir = "b"
                
                if self._resize_dir:
                    self._initial_geometry = self.geometry()
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                    event.accept()
                    return
                    
                if pos.y() <= 34:
                    self._start_window_drag(event.globalPosition().toPoint())
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            pos = event.position().toPoint()
            rect = self.rect()
            border = 8
            
            if not event.buttons():
                if getattr(self, "_is_collapsed", False):
                    if pos.x() >= rect.width() - border:
                        self.setCursor(Qt.CursorShape.SizeHorCursor)
                    else:
                        self.setCursor(Qt.CursorShape.ArrowCursor)
                else:
                    if pos.x() >= rect.width() - border and pos.y() >= rect.height() - border:
                        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    elif pos.x() <= border and pos.y() >= rect.height() - border:
                        self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    elif pos.x() >= rect.width() - border:
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
                        new_w = max(180, geom.width() + delta.x())
                        geom.setWidth(new_w)
                        if getattr(self, "_is_collapsed", False):
                            geom.setHeight(34)
                    elif self._resize_dir == "b":
                        if not getattr(self, "_is_collapsed", False):
                            geom.setHeight(max(150, geom.height() + delta.y()))
                    elif self._resize_dir == "br":
                        new_w = max(180, geom.width() + delta.x())
                        geom.setWidth(new_w)
                        if getattr(self, "_is_collapsed", False):
                            geom.setHeight(34)
                        else:
                            geom.setHeight(max(150, geom.height() + delta.y()))
                    elif self._resize_dir == "bl":
                        new_w = max(180, geom.width() - delta.x())
                        new_x = geom.right() - new_w
                        geom.setX(new_x)
                        geom.setWidth(new_w)
                        if getattr(self, "_is_collapsed", False):
                            geom.setHeight(34)
                        else:
                            geom.setHeight(max(150, geom.height() + delta.y()))
                            
                    parent = getattr(self, "_owner_window", None) or self.parent()
                    other_geos: list[QRect] = []
                    if parent and hasattr(parent, "_active_memo_dialogs"):
                        for dlg in parent._active_memo_dialogs.values():
                            if dlg is not self and dlg.isVisible():
                                other_geos.append(dlg.geometry())
                                
                    screen = self.screen()
                    screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
                    snapped_geom = snap_resize_rect(geom, self._resize_dir, other_geos, screen_geo, threshold=16)
                    
                    if snapped_geom.width() < 180:
                        snapped_geom.setWidth(180)
                    if getattr(self, "_is_collapsed", False):
                        snapped_geom.setHeight(34)
                    elif snapped_geom.height() < 150:
                        snapped_geom.setHeight(150)
                        
                    self.setGeometry(snapped_geom)
                    if not getattr(self, "_is_collapsed", False):
                        self._expanded_height = snapped_geom.height()
                    event.accept()
                    return
                elif hasattr(self, "_drag_position"):
                    self._perform_window_drag(event.globalPosition().toPoint())
                    event.accept()
                    return
                    
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.entry_type == EntryType.MEMO:
            self._resize_dir = None
            if hasattr(self, "_drag_position"):
                delattr(self, "_drag_position")
            if self.entry and self.entry.entry_id:
                parent = getattr(self, "_owner_window", None) or self.parent()
                if parent and hasattr(parent, "repository"):
                    curr_geo = self.geometry()
                    h_val = getattr(self, "_expanded_height", curr_geo.height()) if getattr(self, "_is_collapsed", False) else curr_geo.height()
                    parent.repository.set_setting(f"memo_geo_{self.entry.entry_id}", f"{curr_geo.x()},{curr_geo.y()},{curr_geo.width()},{h_val}")
                    parent.repository.set_setting(f"memo_collapsed_{self.entry.entry_id}", "1" if getattr(self, "_is_collapsed", False) else "0")
                    parent.repository.save()
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
        self.palette = parent.palette if (parent and hasattr(parent, "palette")) else {"text": "#333333", "line": "#e2e8f0", "muted": "#718096"}
        self.entry_type = entry_type
        self.entry = entry
        self._on_download_attachment = on_download_attachment
        self._on_edit_entry = on_edit_entry

        self.setModal(True)
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

            close_btn = QPushButton("✕")
            close_btn.setFixedSize(20, 20)
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setStyleSheet("background: transparent; border: none; font-weight: bold; color: #5a5120; font-size: 13px;")
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
        if self._on_edit_entry is None:
            QMessageBox.warning(self, "수정", "수정 기능을 사용할 수 없습니다.")
            return
        self.accept()
        self._on_edit_entry(self.entry)

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
        check_icon = asset_path("checkmark.svg").as_posix()
        self.setStyleSheet(
            """
            QDialog#entryDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel {
                color: #1f2328;
            }
            QLabel#muted {
                color: #667085;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#sectionTitle {
                color: #223044;
                font-size: 13px;
                font-weight: 700;
                padding-bottom: 2px;
            }
            QFrame#card, QFrame#softCard {
                border: 1px solid #dbe3ec;
                border-radius: 12px;
            }
            QFrame#card {
                background: #ffffff;
            }
            QFrame#softCard {
                background: #f8fafc;
            }
            QLineEdit, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                color: #1f2328;
            }
            QLineEdit {
                min-height: 20px;
                padding: 4px 8px;
            }
            QPlainTextEdit {
                padding: 8px;
                selection-background-color: #cfe7df;
            }
            """
            + f"""
            QCheckBox::indicator:checked {{
                image: url("{check_icon}");
            }}
            """
            + """
            QPushButton, QDialogButtonBox QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 7px 16px;
                min-width: 88px;
            }
            QPushButton:focus, QDialogButtonBox QPushButton:focus, QToolButton:focus {
                outline: none;
            }
            QPushButton:hover, QDialogButtonBox QPushButton:hover {
                background: #f7fafc;
            }
            QPushButton#secondary, QDialogButtonBox QPushButton#secondary {
                background: #ffffff;
                color: #1f2328;
                border: 1px solid #cfd8e3;
                font-weight: 600;
            }
            QPushButton#secondary:hover, QDialogButtonBox QPushButton#secondary:hover {
                background: #f1f5f9;
            }
            QPushButton#primary, QDialogButtonBox QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
            }
            QPushButton#primary:hover, QDialogButtonBox QPushButton#primary:hover {
                background: #236f60;
            }
            QPushButton#attachLink {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                text-align: left;
                color: #1f2328;
                padding: 4px 10px;
            }
            QPushButton#attachLink:hover {
                background: #eef4fb;
            }
            """
        )

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
                elif pos.x() >= rect.width() - border:
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif pos.y() >= rect.height() - border:
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            
            if event.buttons() == Qt.MouseButton.LeftButton:
                if hasattr(self, "_resize_dir") and self._resize_dir:
                    delta = event.globalPosition().toPoint() - self._initial_mouse_pos
                    geom = QRect(self._initial_geometry)
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
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self.result: dict[str, object] | None = None
        self._current_shortcut = normalize_shortcut(current_shortcut)
        self._current_memo_shortcut = normalize_shortcut(current_memo_shortcut)
        shortcut_modifiers, shortcut_key = self._shortcut_parts(current_shortcut)
        memo_modifiers, memo_key = self._shortcut_parts(current_memo_shortcut)
        
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("환경설정")
        self.setWindowIcon(_dialog_icon())
        self.resize(700, 520)
        self.setFixedWidth(700)
        self.setStyleSheet(
            """
            QDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel#title {
                color: #223044;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: #667085;
                font-size: 12px;
            }
            QLabel#sectionTitle {
                color: #223044;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#muted {
                color: #667085;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#value {
                color: #1f2328;
                font-size: 13px;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 10px;
            }
            QListWidget#navSidebar {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 10px;
                outline: none;
                padding: 6px;
            }
            QListWidget#navSidebar::item {
                height: 38px;
                padding-left: 12px;
                font-size: 13px;
                font-weight: 600;
                color: #334155;
                border-radius: 6px;
                margin-bottom: 3px;
            }
            QListWidget#navSidebar::item:hover {
                background-color: #f1f5f9;
                color: #0f172a;
            }
            QListWidget#navSidebar::item:selected {
                background-color: #e2e8f0;
                color: #0f172a;
                font-weight: 700;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                color: #1f2328;
                min-height: 24px;
                padding: 3px 8px;
            }
            QCheckBox {
                color: #1f2328;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #788496;
                background: #ffffff;
                border-radius: 0px;
            }
            QCheckBox::indicator:checked {
                background: #d7ece6;
                border: 1px solid #1f7a67;
                image: url("%s");
            }
            QPushButton, QDialogButtonBox QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 6px 14px;
                min-width: 80px;
            }
            QPushButton:hover, QDialogButtonBox QPushButton:hover {
                background: #f8fafc;
                border-color: #94a3b8;
            }
            QPushButton:focus, QDialogButtonBox QPushButton:focus, QToolButton:focus {
                outline: none;
            }
            QPushButton#primary, QDialogButtonBox QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
            }
            QPushButton#primary:hover, QDialogButtonBox QPushButton#primary:hover {
                background: #186354;
            }
            """
            % (asset_path("checkmark.svg").as_posix(),)
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("환경설정")
        title.setObjectName("title")
        title_box.addWidget(title)
        subtitle = QLabel("기본, 스킨, 단축키, 데이터 설정을 여기에서 관리합니다.")
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
            ("🎨 스킨", 1),
            ("⌨️ 단축키", 2),
            ("💾 데이터", 3),
        ]
        for label, idx in items:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, idx)
            self.nav_list.addItem(item)
            
        body_layout.addWidget(self.nav_list)

        self.pages = QStackedWidget()

        page_general = QWidget()
        pg_gen_layout = QVBoxLayout(page_general)
        pg_gen_layout.setContentsMargins(0, 0, 0, 0)
        pg_gen_layout.setSpacing(10)

        behavior_card = QFrame()
        behavior_card.setObjectName("card")
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(14, 12, 14, 12)
        behavior_layout.setSpacing(8)
        behavior_title = QLabel("실행 옵션")
        behavior_title.setObjectName("sectionTitle")
        behavior_layout.addWidget(behavior_title)
        self.auto_start_check = QCheckBox("윈도우 시작 시 자동 시작")
        self.auto_start_check.setChecked(auto_start_enabled)
        behavior_layout.addWidget(self.auto_start_check)
        self.sticker_animation_check = QCheckBox("스티커 움직임 사용")
        self.sticker_animation_check.setChecked(sticker_animation_enabled)
        behavior_layout.addWidget(self.sticker_animation_check)
        self.hide_completed_on_calendar_check = QCheckBox("달력에서 완료 일정 숨기기")
        self.hide_completed_on_calendar_check.setChecked(hide_completed_on_calendar)
        behavior_layout.addWidget(self.hide_completed_on_calendar_check)
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
            self.theme_combo.addItem(theme_name, theme_name)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(current_theme)))
        theme_label = QLabel("테마")
        theme_label.setObjectName("muted")
        appearance_layout.addRow(theme_label, self.theme_combo)
        pg_skin_layout.addWidget(appearance)
        pg_skin_layout.addStretch(1)

        self.pages.addWidget(page_skin)

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
        memo_row.addStretch(1)
        sc_memo_layout.addLayout(memo_row)

        self.memo_shortcut_status_label = QLabel("")
        self.memo_shortcut_status_label.setObjectName("subtitle")
        sc_memo_layout.addWidget(self.memo_shortcut_status_label)
        pg_sc_layout.addWidget(sc_memo_card)
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
        backup_note.setStyleSheet("color: #d15d48; font-weight: 600;")
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
        
        if initial_tab == "shortcuts":
            self.nav_list.setCurrentRow(2)
        elif initial_tab == "skin":
            self.nav_list.setCurrentRow(1)
        elif initial_tab == "data":
            self.nav_list.setCurrentRow(3)
        else:
            self.nav_list.setCurrentRow(0)

        self.shortcut_ctrl_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_shift_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_alt_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_key_combo.currentIndexChanged.connect(self._refresh_shortcut_status)

        self.memo_shortcut_ctrl_check.toggled.connect(self._refresh_memo_shortcut_status)
        self.memo_shortcut_shift_check.toggled.connect(self._refresh_memo_shortcut_status)
        self.memo_shortcut_alt_check.toggled.connect(self._refresh_memo_shortcut_status)
        self.memo_shortcut_key_combo.currentIndexChanged.connect(self._refresh_memo_shortcut_status)

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
            self.nav_list.setCurrentRow(2)
            return
        cal_shortcut = "+".join(cal_modifiers + [cal_key_token]) if cal_modifiers else cal_key_token
        cal_available, cal_message = self._check_shortcut_availability(cal_shortcut, is_memo=False)
        if not cal_available:
            self.shortcut_status_label.setStyleSheet("color: #d15d48;")
            self.shortcut_status_label.setText(cal_message)
            self.nav_list.setCurrentRow(2)
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
            self.nav_list.setCurrentRow(2)
            return
        memo_shortcut = "+".join(memo_modifiers + [memo_key_token]) if memo_modifiers else memo_key_token
        
        if normalize_shortcut(cal_shortcut) == normalize_shortcut(memo_shortcut):
            QMessageBox.warning(self, "단축키 중복", "캘린더 단축키와 메모 단축키는 서로 달라야 합니다.")
            self.nav_list.setCurrentRow(2)
            return

        memo_available, memo_message = self._check_shortcut_availability(memo_shortcut, is_memo=True)
        if not memo_available:
            self.memo_shortcut_status_label.setStyleSheet("color: #d15d48;")
            self.memo_shortcut_status_label.setText(memo_message)
            self.nav_list.setCurrentRow(2)
            QMessageBox.warning(self, "단축키 오류", f"메모 단축키 오류: {memo_message}")
            return

        self.result = {
            "action": "apply",
            "theme": str(self.theme_combo.currentData()),
            "shortcut": cal_shortcut,
            "memo_shortcut": memo_shortcut,
            "auto_start": self.auto_start_check.isChecked(),
            "sticker_animation_enabled": self.sticker_animation_check.isChecked(),
            "hide_completed_on_calendar": self.hide_completed_on_calendar_check.isChecked(),
            "auto_backup_enabled": self.auto_backup_check.isChecked(),
            "auto_backup_interval_days": int(self.auto_backup_interval_combo.currentData() or 0),
            "auto_backup_keep_count": int(self.auto_backup_keep_combo.currentData() or 0),
        }
        self.accept()

    def _refresh_shortcut_status(self) -> None:
        modifiers: list[str] = []
        if self.shortcut_ctrl_check.isChecked():
            modifiers.append("Ctrl")
        if self.shortcut_shift_check.isChecked():
            modifiers.append("Shift")
        if self.shortcut_alt_check.isChecked():
            modifiers.append("Alt")
        key_token = str(self.shortcut_key_combo.currentData())
        if not modifiers and not (key_token.startswith("F") and key_token[1:].isdigit()):
            self.shortcut_status_label.setStyleSheet("color: #d15d48;")
            self.shortcut_status_label.setText("단독 키는 F1~F12만 가능합니다.")
            return
        shortcut = "+".join(modifiers + [key_token]) if modifiers else key_token
        available, message = self._check_shortcut_availability(shortcut, is_memo=False)
        self.shortcut_status_label.setStyleSheet("color: #1f7a67;" if available else "color: #d15d48;")
        self.shortcut_status_label.setText(message)

    def _refresh_memo_shortcut_status(self) -> None:
        modifiers: list[str] = []
        if self.memo_shortcut_ctrl_check.isChecked():
            modifiers.append("Ctrl")
        if self.memo_shortcut_shift_check.isChecked():
            modifiers.append("Shift")
        if self.memo_shortcut_alt_check.isChecked():
            modifiers.append("Alt")
        key_token = str(self.memo_shortcut_key_combo.currentData())
        if not modifiers and not (key_token.startswith("F") and key_token[1:].isdigit()):
            self.memo_shortcut_status_label.setStyleSheet("color: #d15d48;")
            self.memo_shortcut_status_label.setText("단독 키는 F1~F12만 가능합니다.")
            return
        shortcut = "+".join(modifiers + [key_token]) if modifiers else key_token
        available, message = self._check_shortcut_availability(shortcut, is_memo=True)
        self.memo_shortcut_status_label.setStyleSheet("color: #1f7a67;" if available else "color: #d15d48;")
        self.memo_shortcut_status_label.setText(message)

    def _check_shortcut_availability(self, shortcut: str, is_memo: bool = False) -> tuple[bool, str]:
        normalized = normalize_shortcut(shortcut)
        current = self._current_memo_shortcut if is_memo else self._current_shortcut
        if normalized == current:
            return True, "현재 사용 중인 단축키입니다. (사용 가능)"
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
        self.alarm = alarm
        self.setModal(True)
        self.setWindowTitle("알람 등록" if alarm is None else "알람 수정")
        self.setWindowIcon(_dialog_icon())
        self.resize(500, 440)
        self.setFixedWidth(500)
        
        self.setStyleSheet(
            """
            QDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QRadioButton {
                color: #1f2328;
                spacing: 6px;
            }
            QLabel#title {
                color: #223044;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                color: #223044;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#muted {
                color: #667085;
                font-size: 12px;
                font-weight: 600;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 12px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 6px 10px;
                color: #1f2328;
            }
            QTimeEdit, QDateEdit {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 4px 8px;
                color: #1f2328;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                color: #1f2328;
                min-height: 22px;
                padding: 4px 8px;
            }
            QCheckBox {
                color: #1f2328;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #788496;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #d7ece6;
                border: 1px solid #1f7a67;
                image: url("%s");
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 7px 16px;
                min-width: 88px;
            }
            QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
            }
            QToolButton#weekdayBtn {
                border: 1px solid #cfd8e3;
                border-radius: 14px;
                background: #ffffff;
                color: #1f2328;
                font-weight: 600;
                min-width: 28px;
                min-height: 28px;
                max-width: 28px;
                max-height: 28px;
            }
            QToolButton#weekdayBtn:checked {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
            }
            """
            % (asset_path("checkmark.svg").as_posix(),)
        )
        
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
        self.period_checkbox.setStyleSheet("color: #667085; font-size: 12px; font-weight: 600;")
        self.period_checkbox.setFixedWidth(70)
        self.period_checkbox.toggled.connect(self._on_period_toggled)
        period_layout.addWidget(self.period_checkbox)
        
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setFixedWidth(115)
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setFixedWidth(115)
        
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
        self.setModal(True)
        self.setWindowTitle("알람 설정")
        self.setWindowIcon(_dialog_icon())
        self.resize(600, 500)
        self.setFixedWidth(600)
        
        self.palette = parent.palette if hasattr(parent, "palette") else {
            "bg": "#f4f7fb", "text": "#1f2328", "muted": "#667085", "accent": "#1f7a67"
        }
        
        self.setStyleSheet(
            """
            QDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel#title {
                color: #223044;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: #667085;
                font-size: 12px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 12px;
            }
            QFrame#alarmItem {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 10px;
            }
            QLabel#alarmTime {
                color: #223044;
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#alarmTitle {
                color: #1f2328;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#alarmInfo {
                color: #667085;
                font-size: 11px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 8px;
                padding: 5px 12px;
                min-width: 60px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #f4f7fb;
            }
            QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
                padding: 7px 16px;
                min-width: 88px;
                font-size: 13px;
            }
            QPushButton#danger {
                background: #ffffff;
                color: #d15d48;
                border: 1px solid #cfd8e3;
            }
            QPushButton#danger:hover {
                background: #ffebe9;
                border: 1px solid #d15d48;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #788496;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #d7ece6;
                border: 1px solid #1f7a67;
                image: url("%s");
            }
            """
            % (asset_path("checkmark.svg").as_posix(),)
        )
        
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
        self.mode = mode  # "export" or "import"
        self.selected_format = "zip"  # default
        self.setModal(True)
        self.setWindowTitle("데이터 내보내기" if mode == "export" else "데이터 가져오기")
        self.resize(480, 260)
        self.setFixedWidth(480)

        # Style sheet
        self.setStyleSheet(
            """
            QDialog {
                background: #f4f7fb;
                color: #1f2328;
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel#title {
                color: #223044;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#description {
                color: #667085;
                font-size: 12px;
            }
            QFrame#card {
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 10px;
                padding: 10px;
            }
            QFrame#card:hover {
                border-color: #1f7a67;
            }
            QRadioButton {
                font-weight: 600;
                color: #223044;
                font-size: 14px;
            }
            QLabel#info_label {
                color: #667085;
                font-size: 11px;
                margin-left: 20px;
            }
            QLabel#warning_label {
                color: #e15741;
                font-size: 11px;
                font-weight: 600;
                margin-left: 20px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #cfd8e3;
                border-radius: 6px;
                padding: 6px 14px;
                min-width: 70px;
            }
            QPushButton#primary {
                background: #1f7a67;
                color: #ffffff;
                border: 1px solid #1f7a67;
                font-weight: 700;
            }
            """
        )

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


