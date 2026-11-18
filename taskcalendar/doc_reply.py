"""접수 문서의 회신기한: 설정값, 빠른 기한 버튼, 휴일 판정, 알림 대상 계산.

문서(TASK 항목)의 reply_due 가 기준이며, 캘린더 회신 일정 생성·동기화는 storage 쪽
(set_reply_schedule 등)이 맡는다. 여기에는 화면과 알림이 함께 쓰는 순수 로직만 둔다.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, time as datetime_time, timedelta

from taskcalendar.models import CalendarEntry

DONE_STATUS = "완료"

DEFAULTS: dict[str, str] = {
    "doc_reply_auto_calendar": "1",          # 회신기한을 넣으면 '캘린더에 등록' 을 기본 체크
    "doc_reply_title_format": "[문서] {제목} 회신",
    "doc_reply_alert_offsets": "1,0",        # 며칠 전에 알릴지 (0 = 당일)
    "doc_reply_alert_time": "09:00",
    "doc_reply_quick_buttons": "0,1,3,7,14,1m",
    "doc_reply_move_sync": "1",              # 캘린더에서 회신 일정을 옮기면 문서 기한도 변경
}
ALERT_OFFSET_CHOICES = (3, 1, 0)
_SHOWN_KEY = "doc_reply_alert_shown"


def load_settings(repository) -> dict[str, str]:
    values = {key: repository.get_setting(key, default) or default for key, default in DEFAULTS.items()}
    # 예전 기본값('+N일' 누적 방식 시절)이 저장돼 있으면 새 기본값(당일 포함)으로 바꿔 보여 준다.
    if values["doc_reply_quick_buttons"].replace(" ", "") == "1,3,7,14,1m":
        values["doc_reply_quick_buttons"] = DEFAULTS["doc_reply_quick_buttons"]
    return values


def save_settings(repository, values: dict[str, str]) -> None:
    for key in DEFAULTS:
        if key in values:
            repository.set_setting(key, str(values[key]))


# ── 빠른 기한 버튼 ────────────────────────────────────────────────────

def parse_quick_buttons(text: str) -> list[tuple[str, str]]:
    """'0,1,7,1m' → [('당일','0'), ('1일 뒤','1'), ('7일 뒤','7'), ('1달 뒤','1m')]. 잘못된 항목은 건너뛴다.
    버튼을 누르면 등록일자 기준으로 그 날짜가 회신기한이 된다 (누를 때마다 더해지지 않음)."""
    buttons: list[tuple[str, str]] = []
    for raw in (text or "").replace(" ", "").split(","):
        token = raw.lower()
        if token.endswith("m") and token[:-1].isdigit() and int(token[:-1]) > 0:
            buttons.append((f"{int(token[:-1])}달 뒤", token))
        elif token.isdigit():
            buttons.append(("당일" if int(token) == 0 else f"{int(token)}일 뒤", token))
    return buttons or parse_quick_buttons(DEFAULTS["doc_reply_quick_buttons"])


def add_period(base: date, token: str) -> date:
    token = token.lower()
    if token.endswith("m"):
        months = int(token[:-1])
        month_index = base.month - 1 + months
        year, month = base.year + month_index // 12, month_index % 12 + 1
        return date(year, month, min(base.day, calendar.monthrange(year, month)[1]))
    return base + timedelta(days=int(token))


# ── 휴일 ──────────────────────────────────────────────────────────────

def holiday_name(day: date) -> str:
    """토·일·공휴일이면 그 이름, 근무일이면 빈 문자열."""
    if day.weekday() == 5:
        return "토요일"
    if day.weekday() == 6:
        return "일요일"
    try:
        from taskcalendar.lunar import get_korean_holiday_name

        return get_korean_holiday_name(day) or ""
    except Exception:
        return ""


def previous_workday(day: date) -> date:
    current = day
    for _ in range(14):
        if not holiday_name(current):
            return current
        current -= timedelta(days=1)
    return day


def dday_text(due: date, today: date | None = None) -> str:
    diff = (due - (today or date.today())).days
    if diff == 0:
        return "D-DAY"
    return f"D-{diff}" if diff > 0 else f"D+{-diff} (기한 지남)"


# ── 알림 ──────────────────────────────────────────────────────────────

def _alert_offsets(settings: dict[str, str]) -> list[int]:
    offsets = {int(t) for t in settings["doc_reply_alert_offsets"].split(",") if t.strip().isdigit()}
    return sorted(offsets, reverse=True)


def _alert_time(settings: dict[str, str]) -> datetime_time:
    try:
        hour, minute = (int(part) for part in settings["doc_reply_alert_time"].split(":", 1))
        return datetime_time(hour, minute)
    except ValueError:
        return datetime_time(9, 0)


def _load_shown(repository) -> set[str]:
    try:
        return set(json.loads(repository.get_setting(_SHOWN_KEY, "[]") or "[]"))
    except ValueError:
        return set()


def _due_keys(doc: CalendarEntry, settings: dict[str, str], now: datetime) -> list[tuple[str, str]]:
    """지금 시각 기준으로 이미 알림 시점이 지난 (key, 종류) 목록. 종류는 'D-1' / 'D-DAY'."""
    due = doc.reply_due
    at = _alert_time(settings)
    result: list[tuple[str, str]] = []
    for offset in _alert_offsets(settings):
        if datetime.combine(due - timedelta(days=offset), at) <= now:
            result.append((f"{doc.entry_id}:{due.isoformat()}:{offset}", "D-DAY" if offset == 0 else f"D-{offset}"))
    # 기한이 지난 뒤에는 따로 알리지 않는다 (목록의 D-day 와 캘린더로 충분)
    return result


def mark_passed_as_shown(repository, doc: CalendarEntry, now: datetime | None = None) -> None:
    """문서를 방금 저장했을 때, 이미 지나간 알림 시점은 '알린 것'으로 처리한다.
    (내일이 기한인 문서를 오후에 등록하자마자 '1일 전' 알림이 뜨는 것을 막는다.)"""
    if not doc.entry_id or not doc.reply_due:
        return
    shown = _load_shown(repository)
    shown.update(key for key, _ in _due_keys(doc, load_settings(repository), now or datetime.now()))
    repository.set_setting(_SHOWN_KEY, json.dumps(sorted(shown)))


def collect_pending_alerts(repository, now: datetime | None = None) -> list[tuple[CalendarEntry, str]]:
    """아직 알리지 않은 회신기한 알림을 모아 '알림 표시됨'으로 기록하고 돌려준다.

    PC 가 꺼져 있어 놓친 알림도 다음 실행 때 여기서 걸린다. 한 문서에 여러 시점이 밀려 있으면
    가장 최근 것 하나만 돌려준다. 완료된 문서, 캘린더 회신 일정이 있는 문서(그 일정의 알림을 따름),
    기한이 이미 지난 경우는 알리지 않는다.
    """
    now = now or datetime.now()
    settings = load_settings(repository)
    shown = _load_shown(repository)
    docs = repository.list_docs_with_reply_due()
    pending: list[tuple[CalendarEntry, str]] = []
    changed = False
    for doc in docs:
        if doc.status == DONE_STATUS or doc.reply_due < now.date():
            continue
        # 캘린더에 회신 일정을 만들어 둔 문서는 그 일정의 알림 설정을 따른다 (여기서 따로 알리지 않음)
        try:
            if doc.entry_id and repository.get_reply_schedule(doc.entry_id):
                continue
        except Exception:
            pass
        new_keys = [(key, kind) for key, kind in _due_keys(doc, settings, now) if key not in shown]
        if not new_keys:
            continue
        shown.update(key for key, _ in new_keys)
        changed = True
        pending.append((doc, new_keys[-1][1]))
    # 지워졌거나 기한이 바뀐 문서의 기록은 정리한다.
    valid_prefixes = {f"{doc.entry_id}:{doc.reply_due.isoformat()}:" for doc in docs}
    pruned = {key for key in shown if key.rsplit(":", 1)[0] + ":" in valid_prefixes}
    if changed or pruned != shown:
        repository.set_setting(_SHOWN_KEY, json.dumps(sorted(pruned)))
        repository.save()
    return pending


def alert_message(doc: CalendarEntry, kind: str) -> tuple[str, str]:
    due_text = doc.reply_due.strftime("%Y.%m.%d")
    if doc.reply_time:
        due_text = f"{due_text} {doc.reply_time}"
    if kind == "over":
        return f"회신기한 지남: {doc.title}", f"기한 {due_text} · 아직 완료되지 않았습니다"
    label = "오늘까지" if kind == "D-DAY" else f"{kind}"
    return f"회신기한 {label}: {doc.title}", f"기한 {due_text}"
