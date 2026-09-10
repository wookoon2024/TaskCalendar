from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
import sys

from taskcalendar.paths import data_path


@dataclass(slots=True)
class ComplaintCalcResult:
    start_dt: datetime
    due_dt: datetime
    unit: str  # "hours" or "days"
    amount: int
    is_public_standard: bool
    excluded_days: list[tuple[date, str]] = field(default_factory=list)
    total_calendar_days: int = 0
    working_days: int = 0
    description: str = ""


class ComplaintCalculator:
    def __init__(self, holidays_fixed: dict[str, str] | None = None, holidays_yearly: dict[str, str] | None = None) -> None:
        if holidays_fixed is None or holidays_yearly is None:
            self.holidays_fixed, self.holidays_yearly = self._load_default_holidays()
        else:
            self.holidays_fixed = holidays_fixed
            self.holidays_yearly = holidays_yearly

    @classmethod
    def _load_default_holidays(cls) -> tuple[dict[str, str], dict[str, str]]:
        path = data_path("holidays_kr.json")
        if not path.exists():
            return {}, {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            fixed: dict[str, str] = {}
            yearly: dict[str, str] = {}
            if isinstance(raw, dict):
                for k, v in raw.get("fixed", {}).items():
                    fixed[str(k).strip()] = str(v).strip()
                for k, v in raw.get("yearly", {}).items():
                    yearly[str(k).strip()] = str(v).strip()
            return fixed, yearly
        except Exception:
            return {}, {}

    def is_holiday_or_weekend(self, target_date: date) -> tuple[bool, str]:
        """Returns (is_non_working, reason_string)"""
        iso = target_date.isoformat()
        if iso in self.holidays_yearly:
            return True, self.holidays_yearly[iso]
        mmdd = target_date.strftime("%m-%d")
        if mmdd in self.holidays_fixed:
            return True, self.holidays_fixed[mmdd]
        if target_date.weekday() == 5:
            return True, "토요일"
        if target_date.weekday() == 6:
            return True, "일요일"
        return False, ""

    def is_working_day(self, target_date: date) -> bool:
        is_non_working, _ = self.is_holiday_or_weekend(target_date)
        return not is_non_working

    def next_working_day(self, current: date) -> date:
        cand = current + timedelta(days=1)
        while not self.is_working_day(cand):
            cand += timedelta(days=1)
        return cand

    def calculate(
        self,
        received_dt: datetime,
        amount: int,
        unit: str = "days",  # "hours" or "days"
        is_public_standard: bool = True,
    ) -> ComplaintCalcResult:
        """
        Calculates civil complaint deadline.
        amount: number of hours or days
        unit: 'hours' or 'days'
        is_public_standard: True for legal administrative standard, False for simple 24h calculation
        """
        if not is_public_standard:
            # Simple continuous calculation
            if unit == "hours":
                due_dt = received_dt + timedelta(hours=amount)
            else:
                due_dt = received_dt + timedelta(days=amount)
            total_days = max(1, (due_dt.date() - received_dt.date()).days)
            return ComplaintCalcResult(
                start_dt=received_dt,
                due_dt=due_dt,
                unit=unit,
                amount=amount,
                is_public_standard=False,
                excluded_days=[],
                total_calendar_days=total_days,
                working_days=total_days,
                description=f"단순 {amount}{'시간' if unit == 'hours' else '일'} 연속 경과 계산",
            )

        # Public Standard calculation
        WORK_START_HOUR = 9
        WORK_END_HOUR = 18

        excluded_days: list[tuple[date, str]] = []

        if unit == "hours":
            # Hour-based calculation (근무시간 09:00 ~ 18:00 기준)
            curr_d = received_dt.date()
            curr_t = received_dt.time()

            # Check if received day is non-working
            is_non_work, reason = self.is_holiday_or_weekend(curr_d)
            if is_non_work:
                excluded_days.append((curr_d, reason))
                curr_d = self.next_working_day(curr_d)
                curr_dt = datetime.combine(curr_d, time(WORK_START_HOUR, 0))
            elif curr_t >= time(WORK_END_HOUR, 0):
                # After 18:00 -> start next working day 09:00
                curr_d = self.next_working_day(curr_d)
                curr_dt = datetime.combine(curr_d, time(WORK_START_HOUR, 0))
            elif curr_t < time(WORK_START_HOUR, 0):
                # Before 09:00 -> start today 09:00
                curr_dt = datetime.combine(curr_d, time(WORK_START_HOUR, 0))
            else:
                curr_dt = received_dt

            hours_left = float(amount)
            while hours_left > 0:
                work_end_today = datetime.combine(curr_dt.date(), time(WORK_END_HOUR, 0))
                available_today = (work_end_today - curr_dt).total_seconds() / 3600.0

                if hours_left <= available_today:
                    curr_dt = curr_dt + timedelta(hours=hours_left)
                    hours_left = 0
                else:
                    hours_left -= available_today
                    # Advance to next working day 09:00
                    next_d = curr_dt.date() + timedelta(days=1)
                    while not self.is_working_day(next_d):
                        _, skip_reason = self.is_holiday_or_weekend(next_d)
                        if (next_d, skip_reason) not in excluded_days:
                            excluded_days.append((next_d, skip_reason))
                        next_d += timedelta(days=1)
                    curr_dt = datetime.combine(next_d, time(WORK_START_HOUR, 0))

            due_dt = curr_dt
            total_days = (due_dt.date() - received_dt.date()).days

            return ComplaintCalcResult(
                start_dt=received_dt,
                due_dt=due_dt,
                unit="hours",
                amount=amount,
                is_public_standard=True,
                excluded_days=excluded_days,
                total_calendar_days=total_days,
                working_days=max(1, total_days - len(excluded_days)),
                description=f"근무시간(09:00~18:00) 기준 {amount}시간 계산",
            )

        else:
            # Day-based calculation (일 단위 민원)
            # 마감 시간은 만료일의 근무시간 종료시점(18:00)
            curr_d = received_dt.date()
            if not self.is_working_day(curr_d) or received_dt.time() >= time(WORK_END_HOUR, 0):
                is_non_work, reason = self.is_holiday_or_weekend(curr_d)
                if is_non_work:
                    excluded_days.append((curr_d, reason))
                curr_d = self.next_working_day(curr_d)

            days_added = 0
            check_d = curr_d
            while days_added < amount:
                check_d += timedelta(days=1)
                is_non_work, reason = self.is_holiday_or_weekend(check_d)
                if is_non_work:
                    excluded_days.append((check_d, reason))
                else:
                    days_added += 1

            due_dt = datetime.combine(check_d, time(WORK_END_HOUR, 0))
            total_days = (due_dt.date() - received_dt.date()).days

            return ComplaintCalcResult(
                start_dt=received_dt,
                due_dt=due_dt,
                unit="days",
                amount=amount,
                is_public_standard=True,
                excluded_days=excluded_days,
                total_calendar_days=total_days,
                working_days=amount,
                description=f"영업일 기준 {amount}일 (마감: 18:00)",
            )
