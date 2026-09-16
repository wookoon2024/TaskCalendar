from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, timedelta

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


KOREAN_ELECTIONS: dict[date, str] = {
    date(2020, 4, 15): "제21대 국회의원선거",
    date(2022, 3, 9): "제20대 대통령선거",
    date(2022, 6, 1): "제8회 전국동시지방선거",
    date(2024, 4, 10): "제22대 국회의원선거",
    date(2026, 6, 3): "제9회 전국동시지방선거",
    date(2027, 3, 3): "제21대 대통령선거",
    date(2028, 4, 12): "제23대 국회의원선거",
    date(2030, 6, 5): "제10회 전국동시지방선거",
    date(2032, 3, 3): "제22대 대통령선거",
    date(2032, 4, 14): "제24대 국회의원선거",
    date(2034, 6, 7): "제11회 전국동시지방선거",
    date(2036, 4, 9): "제25대 국회의원선거",
    date(2037, 3, 4): "제23대 대통령선거",
    date(2038, 6, 2): "제12회 전국동시지방선거",
    date(2040, 4, 11): "제26대 국회의원선거",
}

HISTORIC_TEMPORARY_HOLIDAYS: dict[date, str] = {
    date(2020, 8, 17): "임시공휴일",
    date(2023, 10, 2): "임시공휴일",
    date(2024, 10, 1): "임시공휴일 (국군의 날)",
}

FIXED_KR_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "신정",
    (3, 1): "삼일절",
    (5, 5): "어린이날",
    (6, 6): "현충일",
    (8, 15): "광복절",
    (10, 3): "개천절",
    (10, 9): "한글날",
    (12, 25): "성탄절",
}


@functools.lru_cache(maxsize=32)
def get_korean_holidays_for_year(year: int) -> dict[date, str]:
    """「관공서의 공휴일에 관한 규정」에 따른 해당 연도 대한민국 공휴일 자동 산출"""
    holidays: dict[date, str] = {}

    # 1. 양력 고정 공휴일
    fixed_for_year = {date(year, m, d): name for (m, d), name in FIXED_KR_HOLIDAYS.items()}

    # 2. 음력 공휴일 (설날, 추석, 부처님오신날)
    base_lunar: dict[date, str] = {}
    if _HAS_KOREAN_LUNAR:
        try:
            seollal_day = get_solar_date(year, 1, 1)
            chuseok_day = get_solar_date(year, 8, 15)
            buddha_day = get_solar_date(year, 4, 8)

            if seollal_day:
                seollal_eve = seollal_day - timedelta(days=1)
                seollal_next = seollal_day + timedelta(days=1)
                base_lunar[seollal_eve] = "설날 연휴"
                base_lunar[seollal_day] = "설날"
                base_lunar[seollal_next] = "설날 연휴"

            if chuseok_day:
                chuseok_eve = chuseok_day - timedelta(days=1)
                chuseok_next = chuseok_day + timedelta(days=1)
                base_lunar[chuseok_eve] = "추석 연휴"
                base_lunar[chuseok_day] = "추석"
                base_lunar[chuseok_next] = "추석 연휴"

            if buddha_day:
                base_lunar[buddha_day] = "부처님오신날"
        except Exception:
            pass

    # 3. 선거일 및 임시공휴일
    for dt, nm in KOREAN_ELECTIONS.items():
        if dt.year == year:
            base_lunar[dt] = nm
    for dt, nm in HISTORIC_TEMPORARY_HOLIDAYS.items():
        if dt.year == year:
            base_lunar[dt] = nm

    holidays.update(fixed_for_year)
    holidays.update(base_lunar)

    # 4. 대체공휴일 산출
    all_dates = set(holidays.keys())
    substitutes: dict[date, str] = {}

    # 설날 대체공휴일 (연휴 3일 중 일요일 또는 타 공휴일 중복 시)
    if _HAS_KOREAN_LUNAR and seollal_day:
        seollal_triplet = [seollal_eve, seollal_day, seollal_next]
        if any(d.weekday() == 6 for d in seollal_triplet):
            cand = seollal_next + timedelta(days=1)
            while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
                cand += timedelta(days=1)
            substitutes[cand] = "설날 대체공휴일"
            all_dates.add(cand)

    # 추석 대체공휴일 (연휴 3일 중 일요일 또는 개천절/한글날 중복 시)
    if _HAS_KOREAN_LUNAR and chuseok_day:
        chuseok_triplet = [chuseok_eve, chuseok_day, chuseok_next]
        has_sun = any(d.weekday() == 6 for d in chuseok_triplet)
        has_overlap = any(d in fixed_for_year and fixed_for_year[d] in ("개천절", "한글날") for d in chuseok_triplet)
        if has_sun or has_overlap:
            cand = chuseok_next + timedelta(days=1)
            while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
                cand += timedelta(days=1)
            substitutes[cand] = "추석 대체공휴일"
            all_dates.add(cand)

    # 어린이날 대체공휴일 (토/일 또는 부처님오신날 중복 시)
    c_day = date(year, 5, 5)
    buddha_day = get_solar_date(year, 4, 8) if _HAS_KOREAN_LUNAR else None
    if c_day.weekday() in (5, 6) or (buddha_day and c_day == buddha_day):
        cand = c_day + timedelta(days=1)
        while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
            cand += timedelta(days=1)
        substitutes[cand] = "어린이날 대체공휴일"
        all_dates.add(cand)

    # 부처님오신날 대체공휴일 (2023년부터 토/일 중복 시)
    if year >= 2023 and buddha_day and (buddha_day.weekday() in (5, 6) or buddha_day == c_day):
        cand = buddha_day + timedelta(days=1)
        while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
            cand += timedelta(days=1)
        substitutes[cand] = "부처님오신날 대체공휴일"
        all_dates.add(cand)

    # 성탄절 대체공휴일 (2023년부터 토/일 중복 시)
    xmas = date(year, 12, 25)
    if year >= 2023 and xmas.weekday() in (5, 6):
        cand = xmas + timedelta(days=1)
        while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
            cand += timedelta(days=1)
        substitutes[cand] = "성탄절 대체공휴일"
        all_dates.add(cand)

    # 삼일절, 광복절, 개천절, 한글날 대체공휴일 (2021년부터 토/일 중복 시)
    for h_date, h_name in [
        (date(year, 3, 1), "삼일절"),
        (date(year, 8, 15), "광복절"),
        (date(year, 10, 3), "개천절"),
        (date(year, 10, 9), "한글날"),
    ]:
        if year >= 2021 and h_date.weekday() in (5, 6):
            cand = h_date + timedelta(days=1)
            while cand.weekday() in (5, 6) or cand in all_dates or cand in substitutes:
                cand += timedelta(days=1)
            substitutes[cand] = f"{h_name} 대체공휴일"
            all_dates.add(cand)

    holidays.update(substitutes)
    return holidays


@functools.lru_cache(maxsize=512)
def get_korean_holiday_name(dt: date) -> str | None:
    """특정 날짜가 법정 공휴일이면 공휴일 명칭 반환, 아니면 None"""
    year_hols = get_korean_holidays_for_year(dt.year)
    return year_hols.get(dt)

