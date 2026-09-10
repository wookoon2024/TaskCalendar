from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date

try:
    from korean_lunar_calendar import KoreanLunarCalendar
    _HAS_KOREAN_LUNAR = True
except ImportError:
    _HAS_KOREAN_LUNAR = False


@dataclass(frozen=True, slots=True)
class LunarDate:
    year: int
    month: int
    day: int
    is_leap: bool = False

    def formatted(self, prefix: bool = True) -> str:
        leap_str = "윤" if self.is_leap else ""
        if prefix:
            return f"(음) {leap_str}{self.month}.{self.day}"
        return f"{leap_str}{self.month}.{self.day}"


SOLAR_TERMS_NAMES = (
    "소한", "대한", "입춘", "우수", "경칩", "춘분",
    "청명", "곡우", "입하", "소만", "망종", "하지",
    "소서", "대서", "입추", "처서", "백로", "추분",
    "한로", "상강", "입동", "소설", "대설", "동지",
)

# 21st century Solar terms coefficient C
# Formula: Day = int((Year % 100) * 0.2422 + C) - int((Year % 100 - 1) / 4)
C_21 = (
    5.4055, 20.12, 3.87, 18.73, 5.63, 20.646,
    4.81, 20.1, 5.52, 21.04, 5.678, 21.37,
    7.108, 22.83, 7.5, 23.13, 7.646, 23.042,
    8.318, 23.438, 7.438, 22.36, 7.18, 21.94,
)


@functools.lru_cache(maxsize=128)
def get_solar_terms_for_year(year: int) -> dict[date, str]:
    """Calculate the 24 solar terms for a given year."""
    terms: dict[date, str] = {}
    y = year % 100
    for idx, name in enumerate(SOLAR_TERMS_NAMES):
        month = (idx // 2) + 1
        try:
            day = int(y * 0.2422 + C_21[idx]) - int((y - 1) / 4)
            term_date = date(year, month, day)
            terms[term_date] = name
        except (ValueError, IndexError):
            pass
    return terms


@functools.lru_cache(maxsize=512)
def get_solar_term(dt: date) -> str | None:
    """Return the name of 24 solar term if the date is a solar term day, else None."""
    terms = get_solar_terms_for_year(dt.year)
    return terms.get(dt)


@functools.lru_cache(maxsize=1024)
def get_lunar_date(solar_dt: date) -> LunarDate | None:
    """Convert solar (Gregorian) date to Lunar date."""
    if not _HAS_KOREAN_LUNAR:
        return None
    try:
        cal = KoreanLunarCalendar()
        cal.setSolarDate(solar_dt.year, solar_dt.month, solar_dt.day)
        return LunarDate(
            year=cal.lunarYear,
            month=cal.lunarMonth,
            day=cal.lunarDay,
            is_leap=bool(cal.isIntercalation),
        )
    except Exception:
        return None


@functools.lru_cache(maxsize=1024)
def get_solar_date(lunar_year: int, lunar_month: int, lunar_day: int, is_leap: bool = False) -> date | None:
    """Convert Lunar date to solar (Gregorian) date."""
    if not _HAS_KOREAN_LUNAR:
        return None
    try:
        cal = KoreanLunarCalendar()
        valid = cal.setLunarDate(lunar_year, lunar_month, lunar_day, is_leap)
        if not valid:
            # If specified leap month doesn't exist in that year, fallback to regular month
            if is_leap:
                valid = cal.setLunarDate(lunar_year, lunar_month, lunar_day, False)
        if valid:
            return date(cal.solarYear, cal.solarMonth, cal.solarDay)
    except Exception:
        pass
    return None
