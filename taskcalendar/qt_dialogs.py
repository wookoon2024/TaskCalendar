from __future__ import annotations

from datetime import date
import ctypes
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QTime, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QTextCursor
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
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.models import (
    ALERT_OPTIONS,
    ICON_OPTIONS,
    RECURRENCE_OPTIONS,
    WEEKDAY_LABELS,
    AlertType,
    CalendarEntry,
    EntryType,
    RecurrenceType,
    THEME_OPTIONS,
)
from taskcalendar.desktop_services import _parse_hotkey, normalize_shortcut
from taskcalendar.paths import asset_path

ICON_PREVIEW_EMOJI = {
    "anniversary": "🎂",
    "important": "⭐",
    "coffee": "☕",
    "meal": "🍚",
    "meeting": "👥",
}


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


class EntryDialog(QDialog):
    def __init__(self, parent, entry_type: EntryType, selected_day: date | None, entry: CalendarEntry | None = None) -> None:
        super().__init__(parent)
        self.entry_type = entry_type
        self.result: CalendarEntry | None = None
        self.attachments = list(entry.attachments if entry else [])

        entry_start = entry.start_date if entry else None
        entry_day = entry.day if entry else None
        base_day = entry_start or entry_day or selected_day or date.today()

        self.setModal(True)
        self.setObjectName("entryDialog")
        self.setWindowTitle("메모 등록" if entry_type == EntryType.MEMO else "일정 등록")
        self.setWindowIcon(_dialog_icon())
        self.dialog_width = 620 if entry_type != EntryType.MEMO else 600
        self.resize(self.dialog_width, 560 if entry_type != EntryType.MEMO else 430)
        self._apply_styles()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

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
            repeat_form.setColumnStretch(3, 1)

            repeat_form.addWidget(self._section_title("반복 설정"), 0, 0, 1, 4)
            repeat_form.addWidget(self._muted("주기"), 1, 0)
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
            interval_layout.addWidget(self._step_field(self.recurrence_interval, 20))
            interval_layout.addWidget(self._muted("일마다"))
            self.interval_wrap.setMinimumHeight(30)
            repeat_form.addWidget(self.interval_wrap, 1, 2)

            detail_slot = QWidget()
            detail_layout = QHBoxLayout(detail_slot)
            detail_layout.setContentsMargins(0, 0, 0, 0)
            detail_layout.setSpacing(8)
            detail_layout.addStretch(1)

            self.recurrence_summary = QLabel("")
            self.recurrence_summary.setObjectName("hint")
            self.recurrence_summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            detail_layout.addWidget(self.recurrence_summary)

            self.weekday_wrap = QWidget()
            weekday_layout = QHBoxLayout(self.weekday_wrap)
            weekday_layout.setContentsMargins(0, 0, 0, 0)
            weekday_layout.setSpacing(6)
            self.weekly_interval = QSpinBox()
            self.weekly_interval.setRange(1, 52)
            self.weekly_interval.setValue(self.recurrence_interval.value())
            self.weekly_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.weekly_interval.setMinimumWidth(44)
            self.weekly_interval.setMaximumWidth(44)
            self.weekly_interval.setMinimumHeight(30)
            self.weekly_interval.valueChanged.connect(self._sync_recurrence_interval_from_weekly)
            weekday_layout.addWidget(self._step_field(self.weekly_interval, 20))
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
            month_day_layout.addWidget(self.recurrence_month_day_field)
            month_day_layout.addWidget(self._muted("일"))
            self.recurrence_month_end_check = QCheckBox("말일")
            self.recurrence_month_end_check.setChecked(entry.recurrence_month_end if entry else False)
            self.recurrence_month_end_check.toggled.connect(self._toggle_month_end)
            self.recurrence_month_end_check.toggled.connect(self._refresh_repeat_details)
            month_day_layout.addWidget(self.recurrence_month_end_check)
            self.month_day_wrap.setMinimumHeight(30)
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
            repeat_form.addWidget(self.month_week_wrap, 1, 2)
            root.addWidget(self.repeat_panel)

        if self.entry_type != EntryType.MEMO:
            details_card, details_layout = self._create_grid_card()
            details_layout.addWidget(self._muted("아이콘"), 0, 0)
            self.icon_combo = QComboBox()
            for label, value in ICON_OPTIONS:
                preview = ICON_PREVIEW_EMOJI.get(str(value), "")
                display = f"{preview} {label}".strip() if preview else label
                self.icon_combo.addItem(display, value)
            self.icon_combo.setCurrentIndex(max(0, self.icon_combo.findData(entry.icon_type if entry else "")))
            self.icon_combo.setMinimumWidth(132)
            self.icon_combo.setMaximumWidth(180)
            self.icon_combo.view().setMinimumWidth(180)
            details_layout.addWidget(self.icon_combo, 0, 1)

            details_layout.addWidget(self._muted("일시"), 1, 0)
            self.start_date = OverwriteDateEdit(_to_qdate(base_day))
            self.start_date.setDisplayFormat("yyyy-MM-dd")
            self.start_date.setCalendarPopup(True)
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
            details_layout.addWidget(self._step_field(self.start_time, 20), 1, 2)
            details_layout.addWidget(self._muted("~"), 1, 3)
            self.end_date = OverwriteDateEdit(_to_qdate(entry.end_date if entry else base_day))
            self.end_date.setDisplayFormat("yyyy-MM-dd")
            self.end_date.setCalendarPopup(True)
            self.end_date.setMinimumWidth(112)
            self.end_date.setMaximumWidth(112)
            details_layout.addWidget(self.end_date, 1, 4)
            self.end_time = OverwriteTimeEdit(_to_qtime(entry.end_time if entry else "", "18:00"))
            self.end_time.setDisplayFormat("HH:mm")
            self.end_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.end_time.setMinimumWidth(58)
            self.end_time.setMaximumWidth(58)
            self.end_time.setMinimumHeight(30)
            details_layout.addWidget(self._step_field(self.end_time, 20), 1, 5)
            self.all_day = QCheckBox("종일")
            self.all_day.setChecked(entry.all_day if entry else True)
            self.all_day.toggled.connect(self._toggle_all_day)
            details_layout.addWidget(self.all_day, 1, 6)
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
        self.description_input = QPlainTextEdit()
        self.description_input.setPlainText(entry.description if entry else "")
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

    def _save(self) -> None:
        if self.entry_type == EntryType.MEMO:
            title = self.title_input.text().strip()
            if not title:
                QMessageBox.warning(self, "입력 오류", "메모 제목을 입력해 주세요.")
                return
            self.result = CalendarEntry(
                entry_type=self.entry_type,
                title=title,
                description=self.description_input.toPlainText().strip(),
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

        content = self.description_input.toPlainText().strip()
        default_title = content.splitlines()[0].strip()[:40] if content else "일정"
        self.result = CalendarEntry(
            entry_type=self.entry_type,
            title=default_title or "일정",
            description=content,
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
            alert_type=AlertType.POPUP if self.alert_popup.isChecked() else AlertType.NONE,
            alert_offset=str(self.alert_offset_combo.currentData()),
        )
        self.accept()


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
        self.entry_type = entry_type
        self.entry = entry
        self._on_download_attachment = on_download_attachment
        self._on_edit_entry = on_edit_entry

        self.setModal(True)
        self.setObjectName("entryDialog")
        self.setWindowTitle("메모 보기" if entry_type == EntryType.MEMO else "일정 보기")
        self.setWindowIcon(_dialog_icon())
        self.dialog_width = 620 if entry_type != EntryType.MEMO else 600
        self.resize(self.dialog_width, 560 if entry_type != EntryType.MEMO else 430)
        self._apply_styles()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

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

        self.content_view = QPlainTextEdit()
        self.content_view.setReadOnly(True)
        self.content_view.setPlainText(entry.description or "")
        self.content_view.setMinimumHeight(240 if entry_type != EntryType.MEMO else 160)
        self.content_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
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


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent,
        current_theme: str,
        current_shortcut: str,
        auto_start_enabled: bool,
        sticker_animation_enabled: bool,
    ) -> None:
        super().__init__(parent)
        self.result: dict[str, object] | None = None
        self._current_shortcut = normalize_shortcut(current_shortcut)
        shortcut_modifiers, shortcut_key = self._shortcut_parts(current_shortcut)
        self.setModal(True)
        self.setWindowTitle("환경설정")
        self.setWindowIcon(_dialog_icon())
        self.resize(470, 460)
        self.setFixedWidth(470)
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
                border-radius: 12px;
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
                border-radius: 8px;
                padding: 7px 16px;
                min-width: 88px;
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
            """
            % (asset_path("checkmark.svg").as_posix(),)
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel("환경설정")
        title.setObjectName("title")
        root.addWidget(title)
        subtitle = QLabel("스킨, 단축키, 실행 옵션을 여기에서 관리합니다.")
        subtitle.setObjectName("subtitle")
        root.addWidget(subtitle)

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
        root.addWidget(appearance)

        shortcut_card = QFrame()
        shortcut_card.setObjectName("card")
        shortcut_layout = QVBoxLayout(shortcut_card)
        shortcut_layout.setContentsMargins(14, 12, 14, 12)
        shortcut_layout.setSpacing(8)
        shortcut_title = QLabel("단축키 설정")
        shortcut_title.setObjectName("sectionTitle")
        shortcut_layout.addWidget(shortcut_title)
        shortcut_row = QHBoxLayout()
        shortcut_row.setContentsMargins(0, 0, 0, 0)
        shortcut_row.setSpacing(8)
        shortcut_label = QLabel("토글")
        shortcut_label.setObjectName("muted")
        shortcut_row.addWidget(shortcut_label)

        self.shortcut_ctrl_check = QCheckBox("Ctrl")
        self.shortcut_ctrl_check.setChecked("Ctrl" in shortcut_modifiers)
        shortcut_row.addWidget(self.shortcut_ctrl_check)

        self.shortcut_shift_check = QCheckBox("Shift")
        self.shortcut_shift_check.setChecked("Shift" in shortcut_modifiers)
        shortcut_row.addWidget(self.shortcut_shift_check)

        self.shortcut_alt_check = QCheckBox("Alt")
        self.shortcut_alt_check.setChecked("Alt" in shortcut_modifiers)
        shortcut_row.addWidget(self.shortcut_alt_check)

        plus_label = QLabel("+")
        plus_label.setObjectName("muted")
        shortcut_row.addWidget(plus_label)

        self.shortcut_key_combo = QComboBox()
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            self.shortcut_key_combo.addItem(letter, letter)
        for num in range(1, 13):
            token = f"F{num}"
            self.shortcut_key_combo.addItem(token, token)
        self.shortcut_key_combo.setCurrentIndex(max(0, self.shortcut_key_combo.findData(shortcut_key)))
        self.shortcut_key_combo.setMinimumWidth(72)
        self.shortcut_key_combo.setMaximumWidth(88)
        shortcut_row.addWidget(self.shortcut_key_combo)
        shortcut_row.addStretch(1)
        shortcut_layout.addLayout(shortcut_row)
        shortcut_hint = QLabel("조합키+키 또는 F1~F12 단독으로 캘린더 표시/숨김 단축키를 정합니다.\n기본값: F3 (불가 시 Ctrl+Alt+S)")
        shortcut_hint.setWordWrap(True)
        shortcut_hint.setObjectName("subtitle")
        shortcut_layout.addWidget(shortcut_hint)
        self.shortcut_status_label = QLabel("")
        self.shortcut_status_label.setWordWrap(True)
        self.shortcut_status_label.setObjectName("subtitle")
        shortcut_layout.addWidget(self.shortcut_status_label)
        root.addWidget(shortcut_card)

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
        auto_start_hint = QLabel("체크하면 Windows 로그인 후 캘린더가 자동으로 실행됩니다.")
        auto_start_hint.setObjectName("subtitle")
        auto_start_hint.setWordWrap(True)
        behavior_layout.addWidget(auto_start_hint)
        self.sticker_animation_check = QCheckBox("스티커 움직임 사용")
        self.sticker_animation_check.setChecked(sticker_animation_enabled)
        behavior_layout.addWidget(self.sticker_animation_check)
        sticker_animation_hint = QLabel("체크하면 스티커 애니메이션(움직임/깜빡임)이 표시됩니다.")
        sticker_animation_hint.setObjectName("subtitle")
        sticker_animation_hint.setWordWrap(True)
        behavior_layout.addWidget(sticker_animation_hint)
        root.addWidget(behavior_card)

        data_card = QFrame()
        data_card.setObjectName("card")
        data_layout = QVBoxLayout(data_card)
        data_layout.setContentsMargins(14, 12, 14, 12)
        data_layout.setSpacing(8)
        data_title = QLabel("데이터 공유")
        data_title.setObjectName("sectionTitle")
        data_layout.addWidget(data_title)
        data_hint = QLabel("전체 일정/업무/메모를 엑셀로 내보내거나 불러옵니다.")
        data_hint.setObjectName("subtitle")
        data_hint.setWordWrap(True)
        data_layout.addWidget(data_hint)
        data_buttons = QHBoxLayout()
        data_buttons.setContentsMargins(0, 0, 0, 0)
        data_buttons.setSpacing(8)
        export_button = QPushButton("전체 엑셀 저장")
        export_button.setObjectName("topbarButton")
        export_button.clicked.connect(self._request_export_all_excel)
        data_buttons.addWidget(export_button)
        import_button = QPushButton("엑셀 불러오기")
        import_button.setObjectName("topbarButton")
        import_button.clicked.connect(self._request_import_all_excel)
        data_buttons.addWidget(import_button)
        data_buttons.addStretch(1)
        data_layout.addLayout(data_buttons)
        root.addWidget(data_card)

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
        root.addWidget(info_card)

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

        self.shortcut_ctrl_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_shift_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_alt_check.toggled.connect(self._refresh_shortcut_status)
        self.shortcut_key_combo.currentIndexChanged.connect(self._refresh_shortcut_status)
        self._refresh_shortcut_status()

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
        modifiers: list[str] = []
        if self.shortcut_ctrl_check.isChecked():
            modifiers.append("Ctrl")
        if self.shortcut_shift_check.isChecked():
            modifiers.append("Shift")
        if self.shortcut_alt_check.isChecked():
            modifiers.append("Alt")
        key_token = str(self.shortcut_key_combo.currentData())
        if not modifiers and not (key_token.startswith("F") and key_token[1:].isdigit()):
            QMessageBox.warning(self, "입력 오류", "단독 키는 F1~F12만 설정할 수 있습니다.")
            return
        shortcut = "+".join(modifiers + [key_token]) if modifiers else key_token
        available, message = self._check_shortcut_availability(shortcut)
        if not available:
            self.shortcut_status_label.setStyleSheet("color: #d15d48;")
            self.shortcut_status_label.setText(message)
            QMessageBox.warning(self, "단축키 오류", "해당 단축키를 다른 프로그램에서 사용 중이오니, 다른 단축키로 변경해 주세요.")
            return
        self.result = {
            "action": "apply",
            "theme": str(self.theme_combo.currentData()),
            "shortcut": shortcut,
            "auto_start": self.auto_start_check.isChecked(),
            "sticker_animation_enabled": self.sticker_animation_check.isChecked(),
        }
        self.accept()

    def _request_export_all_excel(self) -> None:
        self.result = {"action": "export_excel_all"}
        self.accept()

    def _request_import_all_excel(self) -> None:
        self.result = {"action": "import_excel_all"}
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
        available, message = self._check_shortcut_availability(shortcut)
        self.shortcut_status_label.setStyleSheet("color: #1f7a67;" if available else "color: #d15d48;")
        self.shortcut_status_label.setText(message)

    def _check_shortcut_availability(self, shortcut: str) -> tuple[bool, str]:
        normalized = normalize_shortcut(shortcut)
        if normalized == self._current_shortcut:
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
        test_id = 0xB7FE
        ok = bool(user32.RegisterHotKey(None, test_id, modifiers, vk))
        if ok:
            user32.UnregisterHotKey(None, test_id)
            return True, "사용 가능한 단축키입니다."
        return False, "다른 프로그램에서 사용 중인 단축키입니다."
