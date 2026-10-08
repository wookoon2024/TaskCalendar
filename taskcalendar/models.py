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
    LUNAR_YEARLY = "lunar_yearly"


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
    department: str = ""
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
    memo_group: str = ""
    entry_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source_entry_id: int | None = None
    linked_work_id: int | None = None
    linked_work_type: str = ""


@dataclass(slots=True)
class DaySummary:
    schedules: int = 0
    tasks: int = 0


STATUS_OPTIONS = ["", "예정", "진행중", "완료", "보류"]
THEME_OPTIONS = ["light", "warm", "dark", "pink", "mint", "lavender"]
RECURRENCE_OPTIONS = [
    ("반복 안함", RecurrenceType.NONE),
    ("매일", RecurrenceType.DAILY),
    ("매주", RecurrenceType.WEEKLY),
    ("매월", RecurrenceType.MONTHLY),
    ("매월 n번째 요일", RecurrenceType.MONTHLY_NTH),
    ("매년 (양력)", RecurrenceType.YEARLY),
    ("매년 (음력)", RecurrenceType.LUNAR_YEARLY),
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
    ("🎂 생일/기념일", "🎂"),
    ("🕯️ 제사/추모", "🕯️"),
    ("⭐ 중요/별", "⭐"),
    ("💼 업무/일정", "💼"),
    ("👥 회의/미팅", "👥"),
    ("📊 보고/발표", "📊"),
    ("⏰ 마감/기한", "⏰"),
    ("📢 공지/안내", "📢"),
    ("📝 문서/계약", "📝"),
    ("☕ 커피/티타임", "☕"),
    ("🍚 식사/점심", "🍚"),
    ("🍺 회식/약속", "🍺"),
    ("💊 병원/약복용", "💊"),
    ("🏥 검진/진료", "🏥"),
    ("🏋️ 운동/건강", "🏋️"),
    ("✈️ 휴가/여행", "✈️"),
    ("🚗 출장/이동", "🚗"),
    ("💰 급여/정산", "💰"),
    ("🛒 쇼핑/구매", "🛒"),
    ("🎉 축하/파티", "🎉"),
    ("💍 결혼기념일", "💍"),
    ("❤️ 하트/가족", "❤️"),
    ("🍀 행운/소원", "🍀"),
    ("💡 아이디어", "💡"),
    ("🔥 긴급/필독", "🔥"),
]

STICKER_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "업무/일정": [
        ("💼", "업무"), ("👥", "회의"), ("📊", "보고"), ("📝", "문서"),
        ("💻", "전산"), ("⏰", "마감"), ("📢", "공지"), ("📞", "통화"),
        ("🚗", "출장"), ("✈️", "비행"), ("🎯", "목표"), ("🔥", "긴급"),
    ],
    "기념일/가족": [
        ("🎂", "생일"), ("🕯️", "제사"), ("🎉", "축하"), ("💍", "결혼"),
        ("🎁", "선물"), ("👨‍👩‍👧", "가족"), ("💐", "꽃"), ("🎈", "파티"),
        ("👶", "돌/출산"), ("🏆", "수상"), ("🎓", "졸업"), ("🙏", "추모"),
    ],
    "일상/생활": [
        ("☕", "커피"), ("🍚", "식사"), ("🍺", "회식"), ("💊", "약"),
        ("🏥", "병원"), ("🏋️", "운동"), ("🛒", "쇼핑"), ("💇", "미용"),
        ("🎬", "영화"), ("📚", "공부"), ("🧹", "청소"), ("🚗", "드라이브"),
    ],
    "강조/스티커": [
        ("⭐", "중요"), ("❤️", "하트"), ("🍀", "클로버"), ("💰", "급여"),
        ("📌", "고정"), ("💡", "아이디어"), ("🚩", "깃발"), ("✨", "반짝"),
        ("❗", "주의"), ("❓", "확인"), ("👍", "최고"), ("🌈", "무지개"),
    ],
}
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
    interval_minutes: int = 5
    exclude_holidays: bool = False


_HOLIDAY_JSON_CACHE: tuple[float, dict[str, str], dict[str, str]] | None = None


def _get_holiday_json_data() -> tuple[dict[str, str], dict[str, str]]:
    global _HOLIDAY_JSON_CACHE
    try:
        from taskcalendar.paths import data_path
        path = data_path("holidays_kr.json")
        if not path.exists():
            return {}, {}
        mtime = path.stat().st_mtime
        if _HOLIDAY_JSON_CACHE is not None and _HOLIDAY_JSON_CACHE[0] == mtime:
            return _HOLIDAY_JSON_CACHE[1], _HOLIDAY_JSON_CACHE[2]
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
        fixed: dict[str, str] = {}
        yearly: dict[str, str] = {}
        if isinstance(raw, dict):
            for k, v in raw.get("fixed", {}).items():
                fixed[str(k).strip()] = str(v).strip()
            for k, v in raw.get("yearly", {}).items():
                yearly[str(k).strip()] = str(v).strip()
        _HOLIDAY_JSON_CACHE = (mtime, fixed, yearly)
        return fixed, yearly
    except Exception:
        return {}, {}


def is_korean_holiday(target_date: date) -> tuple[bool, str]:
    """공휴일 여부 및 공휴일 명칭 반환 (holidays_kr.json 및 lunar 모듈 종합)"""
    fixed, yearly = _get_holiday_json_data()
    iso = target_date.isoformat()
    if iso in yearly:
        return True, yearly[iso]
    mmdd = target_date.strftime("%m-%d")
    if mmdd in fixed:
        return True, fixed[mmdd]
    try:
        from taskcalendar.lunar import get_korean_holiday_name
        name = get_korean_holiday_name(target_date)
        if name:
            return True, name
    except Exception:
        pass
    return False, ""


def calculate_next_alarm_trigger(alarm: Alarm, now: datetime) -> datetime | None:
    triggers = calculate_upcoming_alarm_triggers(alarm, now, count=1)
    return triggers[0] if triggers else None


def calculate_upcoming_alarm_triggers(alarm: Alarm, now: datetime, count: int = 30) -> list[datetime]:
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
        interval_min = alarm.interval_minutes if alarm.interval_minutes > 0 else max(1, alarm.hourly_interval) * 60
        interval_min = max(1, interval_min)
        while curr_dt <= end_dt:
            occurrences.append(curr_dt.time())
            curr_dt += timedelta(minutes=interval_min)
        return occurrences

    occurrence_times = get_occurrence_times()
    if not occurrence_times:
        return []

    results: list[datetime] = []

    if not alarm.start_date and not alarm.repeat_days:
        # One-time alarm: valid for 24 hours from creation/reference time
        created_at = alarm.created_at or now
        today_date = created_at.date()
        tomorrow_date = today_date + timedelta(days=1)
        
        for d in [today_date, tomorrow_date]:
            if alarm.exclude_holidays and is_korean_holiday(d)[0]:
                continue
            for t in occurrence_times:
                alarm_dt = datetime.combine(d, t)
                trigger_dt = alarm_dt - offset_delta
                if now < trigger_dt <= created_at + timedelta(days=1):
                    results.append(trigger_dt)
                    if len(results) >= count:
                        return results
        return results
        
    start_date = alarm.start_date or now.date()
    end_date = alarm.end_date
    
    check_date = max(start_date, now.date())
    limit_date = now.date() + timedelta(days=366)
    if end_date and limit_date > end_date:
        limit_date = end_date
        
    while check_date <= limit_date:
        if alarm.exclude_holidays and is_korean_holiday(check_date)[0]:
            check_date += timedelta(days=1)
            continue

        py_weekday = check_date.weekday()
        alarm_weekday = (py_weekday + 1) % 7
        
        if not alarm.repeat_days or alarm_weekday in alarm.repeat_days:
            for t in occurrence_times:
                alarm_dt = datetime.combine(check_date, t)
                trigger_dt = alarm_dt - offset_delta
                if trigger_dt > now:
                    if end_date and check_date > end_date:
                        continue
                    results.append(trigger_dt)
                    if len(results) >= count:
                        return results
        check_date += timedelta(days=1)
        
    return results

