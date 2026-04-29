from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class EntryType(StrEnum):
    SCHEDULE = "schedule"
    TASK = "task"
    MEMO = "memo"


class RecurrenceType(StrEnum):
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    MONTHLY_NTH = "monthly_nth"
    YEARLY = "yearly"


class AlertType(StrEnum):
    NONE = "none"
    POPUP = "popup"


@dataclass(slots=True)
class CalendarEntry:
    entry_type: EntryType
    title: str
    description: str = ""
    day: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    start_time: str = ""
    end_time: str = ""
    all_day: bool = False
    assignee: str = ""
    status: str = ""
    attachments: list[str] = field(default_factory=list)
    recurrence_enabled: bool = False
    recurrence_type: RecurrenceType = RecurrenceType.NONE
    recurrence_interval: int = 1
    recurrence_weekdays: list[int] = field(default_factory=list)
    recurrence_month_day: int = 1
    recurrence_month_week: int = 1
    recurrence_month_end: bool = False
    completed_dates: list[str] = field(default_factory=list)
    icon_type: str = ""
    alert_type: AlertType = AlertType.NONE
    alert_offset: str = "at_start"
    entry_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_entry_id: int | None = None


@dataclass(slots=True)
class DaySummary:
    schedules: int = 0
    tasks: int = 0


STATUS_OPTIONS = ["", "예정", "진행중", "완료", "보류"]
THEME_OPTIONS = ["light", "warm", "dark"]
RECURRENCE_OPTIONS = [
    ("반복 안함", RecurrenceType.NONE),
    ("매일", RecurrenceType.DAILY),
    ("매주", RecurrenceType.WEEKLY),
    ("매월", RecurrenceType.MONTHLY),
    ("매월 n번째 요일", RecurrenceType.MONTHLY_NTH),
    ("매년", RecurrenceType.YEARLY),
]
ALERT_OPTIONS = [
    ("시작시간", "at_start"),
    ("30분전", "30m"),
    ("1시간전", "1h"),
    ("1일전", "1d"),
]
ICON_OPTIONS = [
    ("없음", ""),
    ("케이크", "anniversary"),
    ("별", "important"),
    ("커피", "coffee"),
    ("식사", "meal"),
    ("회의", "meeting"),
]
WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"]


