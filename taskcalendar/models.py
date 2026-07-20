from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
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
    bg_color: str = ""
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
    ("5분전", "5m"),
    ("10분전", "10m"),
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
COLOR_OPTIONS = [
    ("기본", ""),
    ("노랑", "#FFF3BF"),
    ("민트", "#D9FBE5"),
    ("하늘", "#DCEBFF"),
    ("분홍", "#FFE0EC"),
    ("보라", "#EFE4FF"),
    ("주황", "#FFE9D2"),
    ("회색", "#E9EDF3"),
]
WEEKDAY_LABELS = ["일", "월", "화", "수", "목", "금", "토"]


@dataclass(slots=True)
class Alarm:
    alarm_id: int | None = None
    title: str = ""
    start_date: date | None = None
    end_date: date | None = None
    alarm_time: str = ""  # HH:MM
    repeat_days: list[int] = field(default_factory=list)  # [0, 1, 2, 3, 4, 5, 6] (0=Sun, 1=Mon, ..., 6=Sat)
    alert_offset: str = "at_start"  # 'at_start', '5m', '10m', '30m', '1h'
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    hourly_repeat: bool = False
    hourly_interval: int = 1
    hourly_end_time: str = ""


def calculate_next_alarm_trigger(alarm: Alarm, now: datetime) -> datetime | None:
    offset_map = {
        "at_start": timedelta(),
        "5m": timedelta(minutes=5),
        "10m": timedelta(minutes=10),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
    }
    offset_delta = offset_map.get(alarm.alert_offset, timedelta())
    
    def parse_time(time_str: str) -> time | None:
        try:
            h, m = map(int, time_str.split(":"))
            return time(h, m)
        except Exception:
            return None

    def get_occurrence_times() -> list[time]:
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
        return None

    if not alarm.start_date and not alarm.repeat_days:
        # One-time alarm: valid for 24 hours from creation/reference time
        created_at = alarm.created_at or now
        today_date = created_at.date()
        tomorrow_date = today_date + timedelta(days=1)
        
        for d in [today_date, tomorrow_date]:
            for t in occurrence_times:
                alarm_dt = datetime.combine(d, t)
                trigger_dt = alarm_dt - offset_delta
                if now < trigger_dt <= created_at + timedelta(days=1):
                    return trigger_dt
        return None
        
    start_date = alarm.start_date or now.date()
    end_date = alarm.end_date
    
    check_date = start_date
    limit_date = now.date() + timedelta(days=366)
    if end_date and limit_date > end_date:
        limit_date = end_date
        
    while check_date <= limit_date:
        py_weekday = check_date.weekday()
        alarm_weekday = (py_weekday + 1) % 7
        
        if not alarm.repeat_days or alarm_weekday in alarm.repeat_days:
            for t in occurrence_times:
                alarm_dt = datetime.combine(check_date, t)
                trigger_dt = alarm_dt - offset_delta
                if trigger_dt > now:
                    if end_date and check_date > end_date:
                        continue
                    return trigger_dt
        check_date += timedelta(days=1)
        
    return None

