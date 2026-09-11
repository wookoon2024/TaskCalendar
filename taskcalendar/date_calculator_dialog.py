from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.complaint_calculator import ComplaintCalculator
from taskcalendar.lunar import get_lunar_date
from taskcalendar.qt_styles import dialog_stylesheet, resolve_palette


def _get_zodiac_info(year: int) -> tuple[str, str]:
    """Returns (animal, 60_year_name), e.g. ('말띠', '병오년')."""
    stems = ["경", "신", "임", "계", "갑", "을", "병", "정", "무", "기"]
    branches = ["신", "유", "술", "해", "자", "축", "인", "묘", "진", "사", "오", "미"]
    animals = ["원숭이", "닭", "개", "돼지", "쥐", "소", "호랑이", "토끼", "용", "뱀", "말", "양"]

    stem = stems[year % 10]
    branch = branches[year % 12]
    animal = animals[year % 12]
    return f"{animal}띠", f"{stem}{branch}년"


def _calc_ymd_diff(start_dt: date, end_dt: date) -> tuple[int, int, int]:
    """Calculate exact years, months, days between two dates (end_dt >= start_dt)."""
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt
    y = end_dt.year - start_dt.year
    m = end_dt.month - start_dt.month
    d = end_dt.day - start_dt.day
    if d < 0:
        m -= 1
        prev_year = end_dt.year if end_dt.month > 1 else end_dt.year - 1
        prev_month = end_dt.month - 1 if end_dt.month > 1 else 12
        d += calendar.monthrange(prev_year, prev_month)[1]
    if m < 0:
        y -= 1
        m += 12
    return y, m, d


class DateCalculatorDialog(QDialog):
    """K캘린더 다기능 날짜 계산기 다이얼로그.

    - 탭 1: 일수 / D-Day 계산 (두 날짜 간격, D-Day, 주/개월/년 환산)
    - 탭 2: 기념일 계산 (100일~1000일, 주년 프리셋, N일/주/월/년 전·후 계산, 음력 병기)
    - 탭 3: 영업일 (근무일) 계산 (법정공휴일/대체공휴일/주말 제외, 마감일 산정)
    - 탭 4: 만 나이 / 근속기간 계산 (만 나이, 연 나이, 띠/간지, 재직일수)
    """

    register_schedule_requested = Signal(dict)

    WEEKDAYS_KR = ("월", "화", "수", "목", "금", "토", "일")

    def __init__(
        self,
        parent=None,
        holidays_fixed: dict[str, str] | None = None,
        holidays_yearly: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.palette = resolve_palette(parent)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setWindowTitle("K캘린더 날짜 계산기")
        self.resize(620, 600)
        self.setMinimumSize(560, 520)

        self.calculator = ComplaintCalculator(holidays_fixed, holidays_yearly)
        self.holidays_fixed = holidays_fixed or {}
        self.holidays_yearly = holidays_yearly or {}

        self.setStyleSheet(dialog_stylesheet(self.palette))
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header Box
        header_card = QFrame()
        header_card.setObjectName("card")
        header_card.setStyleSheet(
            f"QFrame#card {{ background: {self.palette.get('panel_alt', '#f8fafc')}; "
            f"border: 1px solid {self.palette.get('line', '#dbe3ec')}; border-radius: 10px; padding: 6px 12px; }}"
        )
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(8, 4, 8, 4)

        icon_label = QLabel("📅")
        icon_label.setStyleSheet("font-size: 20px;")
        h_layout.addWidget(icon_label)

        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)
        title_lbl = QLabel("K캘린더 스마트 날짜 계산기")
        title_lbl.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {self.palette.get('text', '#1f2328')};")
        sub_lbl = QLabel("D-Day, 기념일, 영업일(법정공휴일 제외), 나이 및 근속기간을 손쉽게 계산하고 일정으로 등록하세요.")
        sub_lbl.setStyleSheet(f"font-size: 11px; color: {self.palette.get('muted', '#667085')};")
        title_layout.addWidget(title_lbl)
        title_layout.addWidget(sub_lbl)
        h_layout.addLayout(title_layout)
        h_layout.addStretch()

        root.addWidget(header_card)

        # Tab Widget
        self.tabs = QTabWidget()
        self.tabs.setObjectName("dateCalcTabs")

        self.tab1 = self._build_dday_tab()
        self.tab2 = self._build_anniversary_tab()
        self.tab3 = self._build_business_days_tab()
        self.tab4 = self._build_age_tenure_tab()

        self.tabs.addTab(self.tab1, "일수 / D-Day")
        self.tabs.addTab(self.tab2, "기념일 계산")
        self.tabs.addTab(self.tab3, "영업일(근무일)")
        self.tabs.addTab(self.tab4, "만나이 / 근속기간")

        root.addWidget(self.tabs, 1)

        # Bottom Buttons
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()

        close_btn = QPushButton("닫기")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(close_btn)

        root.addLayout(bottom_bar)

    # ----------------------------------------------------------------------
    # Helper widgets
    # ----------------------------------------------------------------------
    def _create_date_edit(self, initial_date: date | None = None) -> QDateEdit:
        de = QDateEdit()
        de.setCalendarPopup(True)
        de.setDisplayFormat("yyyy-MM-dd")
        if initial_date:
            de.setDate(QDate(initial_date.year, initial_date.month, initial_date.day))
        else:
            de.setDate(QDate.currentDate())
        de.setFixedHeight(28)
        return de

    def _today_button(self, date_edit: QDateEdit) -> QPushButton:
        btn = QPushButton("오늘")
        btn.setFixedWidth(46)
        btn.setFixedHeight(28)
        btn.setStyleSheet("min-width: 40px; padding: 2px 6px; font-size: 11px;")
        btn.clicked.connect(lambda: date_edit.setDate(QDate.currentDate()))
        return btn

    def _primary_action_btn(self, text: str, callback: Callable) -> QPushButton:
        accent = self.palette.get("accent", "#1f7a67")
        btn_text = self.palette.get("button_text", "#ffffff")
        btn = QPushButton(text)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {accent}; color: {btn_text}; font-weight: 700; "
            f"border-radius: 6px; padding: 6px 14px; }} QPushButton:hover {{ opacity: 0.9; }}"
        )
        btn.setFixedHeight(32)
        btn.clicked.connect(callback)
        return btn

    # ----------------------------------------------------------------------
    # TAB 1: 일수 / D-Day
    # ----------------------------------------------------------------------
    def _build_dday_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # Input Box
        input_card = QFrame()
        input_card.setObjectName("card")
        ic_layout = QVBoxLayout(input_card)
        ic_layout.setContentsMargins(12, 10, 12, 10)
        ic_layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("기준일 (시작):"), 0, 0)
        self.t1_base_date = self._create_date_edit()
        self.t1_base_date.dateChanged.connect(self._recalc_tab1)
        grid.addWidget(self.t1_base_date, 0, 1)
        grid.addWidget(self._today_button(self.t1_base_date), 0, 2)

        grid.addWidget(QLabel("대상일 (종료):"), 1, 0)
        self.t1_target_date = self._create_date_edit()
        self.t1_target_date.setDate(QDate.currentDate().addDays(100))
        self.t1_target_date.dateChanged.connect(self._recalc_tab1)
        grid.addWidget(self.t1_target_date, 1, 1)
        grid.addWidget(self._today_button(self.t1_target_date), 1, 2)

        ic_layout.addLayout(grid)

        # Options & Quick buttons
        opt_bar = QHBoxLayout()
        self.t1_include_start = QCheckBox("시작일 포함 (당일을 1일째로 계산)")
        self.t1_include_start.setChecked(True)
        self.t1_include_start.toggled.connect(self._recalc_tab1)
        opt_bar.addWidget(self.t1_include_start)
        opt_bar.addStretch()

        # Preset jumps for target date
        for label, days in [("+7일", 7), ("+30일", 30), ("+100일", 100), ("+1년", 365)]:
            p_btn = QPushButton(label)
            p_btn.setFixedWidth(48)
            p_btn.setFixedHeight(24)
            p_btn.setStyleSheet("min-width: 40px; padding: 2px 4px; font-size: 11px;")
            p_btn.clicked.connect(lambda _, d=days: self.t1_target_date.setDate(self.t1_base_date.date().addDays(d)))
            opt_bar.addWidget(p_btn)

        ic_layout.addLayout(opt_bar)
        layout.addWidget(input_card)

        # Result Card
        res_card = QFrame()
        res_card.setObjectName("card")
        res_card.setStyleSheet(
            f"QFrame#card {{ background: {self.palette.get('panel_alt', '#f8fafc')}; "
            f"border: 1px solid {self.palette.get('line', '#dbe3ec')}; border-radius: 10px; padding: 14px; }}"
        )
        res_layout = QVBoxLayout(res_card)
        res_layout.setSpacing(10)

        top_res = QHBoxLayout()
        self.t1_dday_badge = QLabel("D-100")
        accent = self.palette.get("accent", "#1f7a67")
        self.t1_dday_badge.setStyleSheet(
            f"background-color: {accent}; color: white; font-size: 22px; font-weight: 800; "
            f"border-radius: 8px; padding: 6px 16px;"
        )
        top_res.addWidget(self.t1_dday_badge)

        self.t1_range_label = QLabel("")
        self.t1_range_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {self.palette.get('text', '#1f2328')};")
        top_res.addWidget(self.t1_range_label, 1)
        res_layout.addLayout(top_res)

        # Detail rows
        detail_frame = QFrame()
        detail_frame.setObjectName("softCard")
        df_layout = QVBoxLayout(detail_frame)
        df_layout.setContentsMargins(10, 8, 10, 8)
        df_layout.setSpacing(6)

        self.t1_total_days_lbl = QLabel("")
        self.t1_total_days_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        df_layout.addWidget(self.t1_total_days_lbl)

        self.t1_weeks_lbl = QLabel("")
        self.t1_weeks_lbl.setStyleSheet(f"font-size: 12px; color: {self.palette.get('muted', '#667085')};")
        df_layout.addWidget(self.t1_weeks_lbl)

        self.t1_ymd_lbl = QLabel("")
        self.t1_ymd_lbl.setStyleSheet(f"font-size: 12px; color: {self.palette.get('muted', '#667085')};")
        df_layout.addWidget(self.t1_ymd_lbl)

        res_layout.addWidget(detail_frame)

        # Action: Register schedule
        act_bar = QHBoxLayout()
        act_bar.addStretch()
        self.t1_register_btn = self._primary_action_btn("+ 대상일로 일정 등록", self._on_tab1_register)
        act_bar.addWidget(self.t1_register_btn)
        res_layout.addLayout(act_bar)

        layout.addWidget(res_card)
        layout.addStretch()

        self._recalc_tab1()
        return w

    def _recalc_tab1(self) -> None:
        q_b = self.t1_base_date.date()
        q_t = self.t1_target_date.date()
        base_d = date(q_b.year(), q_b.month(), q_b.day())
        target_d = date(q_t.year(), q_t.month(), q_t.day())

        diff = (target_d - base_d).days
        if diff == 0:
            dday_text = "D-Day"
        elif diff > 0:
            dday_text = f"D-{diff}"
        else:
            dday_text = f"D+{abs(diff)}"

        self.t1_dday_badge.setText(dday_text)

        b_wk = self.WEEKDAYS_KR[base_d.weekday()]
        t_wk = self.WEEKDAYS_KR[target_d.weekday()]
        self.t1_range_label.setText(
            f"{base_d.strftime('%Y-%m-%d')}({b_wk})  ➔  {target_d.strftime('%Y-%m-%d')}({t_wk})"
        )

        inc_start = self.t1_include_start.isChecked()
        total_days = abs(diff) + (1 if inc_start else 0)
        inc_msg = "(시작일 1일째 포함)" if inc_start else "(시작일 제외)"

        self.t1_total_days_lbl.setText(f"총 일수: {total_days:,}일 {inc_msg}")

        w = total_days // 7
        rem_d = total_days % 7
        self.t1_weeks_lbl.setText(f"주 단위 환산: {w}주 {rem_d}일 (약 {total_days / 30.4375:.1f}개월)")

        y, m, d = _calc_ymd_diff(base_d, target_d)
        if inc_start and diff != 0:
            d += 1
        ymd_parts = []
        if y > 0:
            ymd_parts.append(f"{y}년")
        if m > 0 or y > 0:
            ymd_parts.append(f"{m}개월")
        ymd_parts.append(f"{d}일")
        self.t1_ymd_lbl.setText(f"연/월/일 환산: {' '.join(ymd_parts)}")

    def _on_tab1_register(self) -> None:
        q_b = self.t1_base_date.date()
        q_t = self.t1_target_date.date()
        base_d = date(q_b.year(), q_b.month(), q_b.day())
        target_d = date(q_t.year(), q_t.month(), q_t.day())
        badge = self.t1_dday_badge.text()
        total_days_txt = self.t1_total_days_lbl.text()

        title = f"[{badge}] 일정 ({target_d.strftime('%m/%d')})"
        desc = (
            f"• D-Day 구분: {badge}\n"
            f"• 대상일자: {target_d.strftime('%Y-%m-%d')}\n"
            f"• 기준일자: {base_d.strftime('%Y-%m-%d')}\n"
            f"• {total_days_txt}\n"
            f"• {self.t1_weeks_lbl.text()}\n"
            f"• {self.t1_ymd_lbl.text()}"
        )
        self.register_schedule_requested.emit({
            "title": title,
            "start_date": target_d,
            "end_date": target_d,
            "all_day": True,
            "description": desc,
        })

    # ----------------------------------------------------------------------
    # TAB 2: 기념일 계산
    # ----------------------------------------------------------------------
    def _build_anniversary_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Base date & option
        top_card = QFrame()
        top_card.setObjectName("card")
        tc_layout = QHBoxLayout(top_card)
        tc_layout.setContentsMargins(12, 8, 12, 8)
        tc_layout.setSpacing(10)

        tc_layout.addWidget(QLabel("기념일 기준일:"))
        self.t2_base_date = self._create_date_edit()
        self.t2_base_date.dateChanged.connect(self._recalc_tab2)
        tc_layout.addWidget(self.t2_base_date)
        tc_layout.addWidget(self._today_button(self.t2_base_date))

        self.t2_inc_start = QCheckBox("시작일을 1일로 계산 (예: 100일 기념)")
        self.t2_inc_start.setChecked(True)
        self.t2_inc_start.toggled.connect(self._recalc_tab2)
        tc_layout.addWidget(self.t2_inc_start)
        tc_layout.addStretch()

        layout.addWidget(top_card)

        # Presets Table
        self.t2_table = QTableWidget()
        self.t2_table.setColumnCount(4)
        self.t2_table.setHorizontalHeaderLabels(["기념일 구분", "해당 날짜", "음력 표기", "일정 등록"])
        self.t2_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.t2_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.t2_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.t2_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.t2_table.verticalHeader().setVisible(False)
        self.t2_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.t2_table.setStyleSheet("QTableWidget { font-size: 12px; }")
        layout.addWidget(self.t2_table, 1)

        # Custom jump calculator
        custom_card = QFrame()
        custom_card.setObjectName("softCard")
        cc_layout = QHBoxLayout(custom_card)
        cc_layout.setContentsMargins(10, 8, 10, 8)
        cc_layout.setSpacing(6)

        cc_layout.addWidget(QLabel("직접 계산: 기준일로부터"))
        self.t2_custom_amount = QSpinBox()
        self.t2_custom_amount.setRange(1, 9999)
        self.t2_custom_amount.setValue(50)
        self.t2_custom_amount.setFixedHeight(28)
        self.t2_custom_amount.valueChanged.connect(self._recalc_tab2_custom)
        cc_layout.addWidget(self.t2_custom_amount)

        self.t2_custom_unit = QComboBox()
        self.t2_custom_unit.addItems(["일", "주", "개월", "년"])
        self.t2_custom_unit.setFixedHeight(28)
        self.t2_custom_unit.currentIndexChanged.connect(self._recalc_tab2_custom)
        cc_layout.addWidget(self.t2_custom_unit)

        self.t2_custom_dir = QComboBox()
        self.t2_custom_dir.addItems(["후", "전"])
        self.t2_custom_dir.setFixedHeight(28)
        self.t2_custom_dir.currentIndexChanged.connect(self._recalc_tab2_custom)
        cc_layout.addWidget(self.t2_custom_dir)

        self.t2_custom_res_lbl = QLabel("")
        self.t2_custom_res_lbl.setStyleSheet("font-weight: 700; color: #1f7a67;")
        cc_layout.addWidget(self.t2_custom_res_lbl, 1)

        self.t2_custom_reg_btn = QPushButton("+ 일정 등록")
        self.t2_custom_reg_btn.setFixedHeight(28)
        self.t2_custom_reg_btn.setStyleSheet("min-width: 65px; font-size: 11px;")
        self.t2_custom_reg_btn.clicked.connect(self._on_tab2_custom_register)
        cc_layout.addWidget(self.t2_custom_reg_btn)

        layout.addWidget(custom_card)

        self._recalc_tab2()
        return w

    def _recalc_tab2(self) -> None:
        q_b = self.t2_base_date.date()
        base_d = date(q_b.year(), q_b.month(), q_b.day())
        inc_start = self.t2_inc_start.isChecked()

        presets: list[tuple[str, date]] = []
        # Days presets
        for d_count in [100, 200, 300, 500, 1000]:
            delta_days = d_count - 1 if inc_start else d_count
            calc_d = base_d + timedelta(days=delta_days)
            presets.append((f"{d_count}일", calc_d))

        # Year presets
        for y_count in [1, 2, 3, 5, 10]:
            try:
                calc_d = date(base_d.year + y_count, base_d.month, base_d.day)
            except ValueError:
                # Leap day fallback (e.g. Feb 29 -> Feb 28)
                calc_d = date(base_d.year + y_count, base_d.month, 28)
            presets.append((f"{y_count}주년", calc_d))

        self.t2_table.setRowCount(len(presets))
        for row, (name, calc_d) in enumerate(presets):
            wk = self.WEEKDAYS_KR[calc_d.weekday()]
            date_str = f"{calc_d.strftime('%Y년 %m월 %d일')} ({wk})"

            # Lunar date
            lunar = get_lunar_date(calc_d)
            lunar_str = lunar.formatted(prefix=True) if lunar else "-"

            it_name = QTableWidgetItem(name)
            it_name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_date = QTableWidgetItem(date_str)
            it_lunar = QTableWidgetItem(lunar_str)
            it_lunar.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.t2_table.setItem(row, 0, it_name)
            self.t2_table.setItem(row, 1, it_date)
            self.t2_table.setItem(row, 2, it_lunar)

            btn = QPushButton("+ 등록")
            btn.setFixedHeight(24)
            btn.setStyleSheet("min-width: 48px; padding: 2px 4px; font-size: 11px;")
            btn.clicked.connect(lambda _, n=name, d=calc_d: self._on_register_anniversary(n, d))
            self.t2_table.setCellWidget(row, 3, btn)

        self._recalc_tab2_custom()

    def _recalc_tab2_custom(self) -> None:
        q_b = self.t2_base_date.date()
        base_d = date(q_b.year(), q_b.month(), q_b.day())
        amount = self.t2_custom_amount.value()
        unit = self.t2_custom_unit.currentText()
        direction = self.t2_custom_dir.currentText()
        sign = 1 if direction == "후" else -1

        if unit == "일":
            res_d = base_d + timedelta(days=sign * amount)
        elif unit == "주":
            res_d = base_d + timedelta(weeks=sign * amount)
        elif unit == "개월":
            # Month arithmetic
            total_m = base_d.year * 12 + (base_d.month - 1) + (sign * amount)
            new_y = total_m // 12
            new_m = (total_m % 12) + 1
            max_day = calendar.monthrange(new_y, new_m)[1]
            res_d = date(new_y, new_m, min(base_d.day, max_day))
        else:  # 년
            try:
                res_d = date(base_d.year + (sign * amount), base_d.month, base_d.day)
            except ValueError:
                res_d = date(base_d.year + (sign * amount), base_d.month, 28)

        self._t2_custom_res_date = res_d
        wk = self.WEEKDAYS_KR[res_d.weekday()]
        lunar = get_lunar_date(res_d)
        lunar_str = f" {lunar.formatted(prefix=True)}" if lunar else ""
        self.t2_custom_res_lbl.setText(f"= {res_d.strftime('%Y년 %m월 %d일')}({wk}){lunar_str}")

    def _on_register_anniversary(self, name: str, d: date) -> None:
        q_b = self.t2_base_date.date()
        base_d = date(q_b.year(), q_b.month(), q_b.day())
        wk = self.WEEKDAYS_KR[d.weekday()]
        title = f"[{name}] 기념일 ({d.strftime('%m/%d')})"
        desc = (
            f"• 기념일명: {name}\n"
            f"• 날짜: {d.strftime('%Y-%m-%d')} ({wk})\n"
            f"• 기준일: {base_d.strftime('%Y-%m-%d')}"
        )
        lunar = get_lunar_date(d)
        if lunar:
            desc += f"\n• 음력: {lunar.formatted(prefix=True)}"

        self.register_schedule_requested.emit({
            "title": title,
            "start_date": d,
            "end_date": d,
            "all_day": True,
            "description": desc,
        })

    def _on_tab2_custom_register(self) -> None:
        if not hasattr(self, "_t2_custom_res_date"):
            return
        res_d = self._t2_custom_res_date
        amount = self.t2_custom_amount.value()
        unit = self.t2_custom_unit.currentText()
        direction = self.t2_custom_dir.currentText()
        name = f"{amount}{unit} {direction}"
        self._on_register_anniversary(name, res_d)

    # ----------------------------------------------------------------------
    # TAB 3: 영업일(근무일) 계산
    # ----------------------------------------------------------------------
    def _build_business_days_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Mode Selector
        mode_box = QHBoxLayout()
        self.t3_radio_range = QRadioButton("기간 내 영업일 수 계산 (시작일 ~ 종료일)")
        self.t3_radio_deadline = QRadioButton("N영업일 후 마감일 계산 (기준일 + N영업일)")
        self.t3_radio_range.setChecked(True)
        self.t3_radio_range.toggled.connect(self._toggle_tab3_mode)
        mode_box.addWidget(self.t3_radio_range)
        mode_box.addWidget(self.t3_radio_deadline)
        mode_box.addStretch()
        layout.addLayout(mode_box)

        # Container for Mode 1: Range
        self.t3_card_range = QFrame()
        self.t3_card_range.setObjectName("card")
        r_layout = QVBoxLayout(self.t3_card_range)
        r_layout.setContentsMargins(12, 10, 12, 10)
        r_layout.setSpacing(8)

        grid = QGridLayout()
        grid.addWidget(QLabel("시작일:"), 0, 0)
        self.t3_r_start = self._create_date_edit()
        self.t3_r_start.dateChanged.connect(self._recalc_tab3_range)
        grid.addWidget(self.t3_r_start, 0, 1)
        grid.addWidget(self._today_button(self.t3_r_start), 0, 2)

        grid.addWidget(QLabel("종료일:"), 1, 0)
        self.t3_r_end = self._create_date_edit()
        self.t3_r_end.setDate(QDate.currentDate().addDays(14))
        self.t3_r_end.dateChanged.connect(self._recalc_tab3_range)
        grid.addWidget(self.t3_r_end, 1, 1)
        grid.addWidget(self._today_button(self.t3_r_end), 1, 2)
        r_layout.addLayout(grid)

        self.t3_r_inc_start = QCheckBox("시작일 포함")
        self.t3_r_inc_start.setChecked(True)
        self.t3_r_inc_start.toggled.connect(self._recalc_tab3_range)
        r_layout.addWidget(self.t3_r_inc_start)

        layout.addWidget(self.t3_card_range)

        # Container for Mode 2: Deadline
        self.t3_card_deadline = QFrame()
        self.t3_card_deadline.setObjectName("card")
        d_layout = QVBoxLayout(self.t3_card_deadline)
        d_layout.setContentsMargins(12, 10, 12, 10)
        d_layout.setSpacing(8)

        d_grid = QGridLayout()
        d_grid.addWidget(QLabel("기준일(접수/시작):"), 0, 0)
        self.t3_d_start = self._create_date_edit()
        self.t3_d_start.dateChanged.connect(self._recalc_tab3_deadline)
        d_grid.addWidget(self.t3_d_start, 0, 1)
        d_grid.addWidget(self._today_button(self.t3_d_start), 0, 2)

        d_grid.addWidget(QLabel("영업일 수:"), 1, 0)
        spin_box = QHBoxLayout()
        self.t3_d_amount = QSpinBox()
        self.t3_d_amount.setRange(1, 365)
        self.t3_d_amount.setValue(5)
        self.t3_d_amount.setFixedHeight(28)
        self.t3_d_amount.valueChanged.connect(self._recalc_tab3_deadline)
        spin_box.addWidget(self.t3_d_amount)
        spin_box.addWidget(QLabel("영업일 후"))
        spin_box.addStretch()
        d_grid.addLayout(spin_box, 1, 1, 1, 2)
        d_layout.addLayout(d_grid)

        # Quick preset buttons for N business days
        preset_bar = QHBoxLayout()
        preset_bar.addWidget(QLabel("자주 쓰는 기한:"))
        for days in [3, 5, 7, 10, 14, 20]:
            btn = QPushButton(f"{days}일")
            btn.setFixedWidth(42)
            btn.setFixedHeight(24)
            btn.setStyleSheet("min-width: 36px; padding: 2px 4px; font-size: 11px;")
            btn.clicked.connect(lambda _, d=days: self.t3_d_amount.setValue(d))
            preset_bar.addWidget(btn)
        preset_bar.addStretch()
        d_layout.addLayout(preset_bar)

        self.t3_card_deadline.setVisible(False)
        layout.addWidget(self.t3_card_deadline)

        # Result Display Area
        res_card = QFrame()
        res_card.setObjectName("softCard")
        res_card.setStyleSheet(
            f"QFrame#softCard {{ background: {self.palette.get('panel_alt', '#f8fafc')}; "
            f"border: 1px solid {self.palette.get('line', '#dbe3ec')}; border-radius: 10px; padding: 12px; }}"
        )
        res_layout = QVBoxLayout(res_card)
        res_layout.setSpacing(8)

        self.t3_main_result_lbl = QLabel("")
        self.t3_main_result_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #1f7a67;")
        res_layout.addWidget(self.t3_main_result_lbl)

        self.t3_stat_lbl = QLabel("")
        self.t3_stat_lbl.setStyleSheet(f"font-size: 12px; color: {self.palette.get('text', '#1f2328')};")
        res_layout.addWidget(self.t3_stat_lbl)

        self.t3_holidays_lbl = QLabel("")
        self.t3_holidays_lbl.setWordWrap(True)
        self.t3_holidays_lbl.setStyleSheet(f"font-size: 11px; color: {self.palette.get('danger', '#d15d48')};")
        res_layout.addWidget(self.t3_holidays_lbl)

        # Register button
        act_bar = QHBoxLayout()
        act_bar.addStretch()
        self.t3_reg_btn = self._primary_action_btn("+ 마감/영업일정 등록", self._on_tab3_register)
        act_bar.addWidget(self.t3_reg_btn)
        res_layout.addLayout(act_bar)

        layout.addWidget(res_card)
        layout.addStretch()

        self._recalc_tab3_range()
        return w

    def _toggle_tab3_mode(self) -> None:
        is_range = self.t3_radio_range.isChecked()
        self.t3_card_range.setVisible(is_range)
        self.t3_card_deadline.setVisible(not is_range)
        if is_range:
            self._recalc_tab3_range()
        else:
            self._recalc_tab3_deadline()

    def _recalc_tab3_range(self) -> None:
        q_s = self.t3_r_start.date()
        q_e = self.t3_r_end.date()
        start_d = date(q_s.year(), q_s.month(), q_s.day())
        end_d = date(q_e.year(), q_e.month(), q_e.day())
        inc_start = self.t3_r_inc_start.isChecked()

        if end_d < start_d:
            start_d, end_d = end_d, start_d

        curr = start_d if inc_start else start_d + timedelta(days=1)
        total_days = 0
        working_days = 0
        weekends = 0
        holidays_list: list[tuple[date, str]] = []

        while curr <= end_d:
            total_days += 1
            is_non_working, reason = self.calculator.is_holiday_or_weekend(curr)
            if is_non_working:
                if reason in ("토요일", "일요일"):
                    weekends += 1
                else:
                    holidays_list.append((curr, reason))
            else:
                working_days += 1
            curr += timedelta(days=1)

        self.t3_main_result_lbl.setText(f"순수 영업일(근무일): {working_days}일")
        self.t3_stat_lbl.setText(
            f"달력상 총 {total_days}일  |  주말(토·일) {weekends}일 제외  |  공휴일 {len(holidays_list)}일 제외"
        )

        if holidays_list:
            h_names = [f"{d.strftime('%m/%d')}({r})" for d, r in holidays_list]
            self.t3_holidays_lbl.setText(f"제외된 법정공휴일: {', '.join(h_names)}")
        else:
            self.t3_holidays_lbl.setText("기간 내 법정공휴일 없음 (정상 근무일)")

        self._t3_last_data = {
            "mode": "range",
            "start_date": start_d,
            "end_date": end_d,
            "working_days": working_days,
            "total_days": total_days,
            "holidays": holidays_list,
        }

    def _recalc_tab3_deadline(self) -> None:
        q_s = self.t3_d_start.date()
        start_d = date(q_s.year(), q_s.month(), q_s.day())
        needed_days = self.t3_d_amount.value()

        curr = start_d
        added = 0
        total_cal_days = 0
        weekends = 0
        holidays_list: list[tuple[date, str]] = []

        while added < needed_days:
            curr += timedelta(days=1)
            total_cal_days += 1
            is_non_working, reason = self.calculator.is_holiday_or_weekend(curr)
            if is_non_working:
                if reason in ("토요일", "일요일"):
                    weekends += 1
                else:
                    holidays_list.append((curr, reason))
            else:
                added += 1

        wk = self.WEEKDAYS_KR[curr.weekday()]
        self.t3_main_result_lbl.setText(f"최종 마감일: {curr.strftime('%Y년 %m월 %d일')} ({wk})")
        self.t3_stat_lbl.setText(
            f"달력상 {total_cal_days}일 소요 ({needed_days}영업일)  |  주말 {weekends}일 제외  |  공휴일 {len(holidays_list)}일 제외"
        )

        if holidays_list:
            h_names = [f"{d.strftime('%m/%d')}({r})" for d, r in holidays_list]
            self.t3_holidays_lbl.setText(f"제외된 법정공휴일: {', '.join(h_names)}")
        else:
            self.t3_holidays_lbl.setText("기간 내 제외된 법정공휴일 없음")

        self._t3_last_data = {
            "mode": "deadline",
            "start_date": start_d,
            "end_date": curr,
            "needed_days": needed_days,
            "total_days": total_cal_days,
            "holidays": holidays_list,
        }

    def _on_tab3_register(self) -> None:
        if not hasattr(self, "_t3_last_data"):
            return
        data = self._t3_last_data
        if data["mode"] == "range":
            s = data["start_date"]
            e = data["end_date"]
            w_days = data["working_days"]
            title = f"[영업일] 업무 일정 ({w_days}영업일)"
            desc = (
                f"• 기간: {s.strftime('%Y-%m-%d')} ~ {e.strftime('%Y-%m-%d')}\n"
                f"• 순수 영업일수: {w_days}일 (총 {data['total_days']}일)\n"
                f"• 제외 휴일: {self.t3_holidays_lbl.text()}"
            )
            self.register_schedule_requested.emit({
                "title": title,
                "start_date": s,
                "end_date": e,
                "all_day": True,
                "description": desc,
            })
        else:
            s = data["start_date"]
            due_d = data["end_date"]
            needed = data["needed_days"]
            wk = self.WEEKDAYS_KR[due_d.weekday()]
            title = f"[마감] {needed}영업일 처리 마감"
            desc = (
                f"• 기준일(접수): {s.strftime('%Y-%m-%d')}\n"
                f"• 최종 마감일: {due_d.strftime('%Y-%m-%d')} ({wk})\n"
                f"• 소요 영업일: {needed}영업일 (달력일수 {data['total_days']}일)\n"
                f"• 제외 휴일: {self.t3_holidays_lbl.text()}"
            )
            self.register_schedule_requested.emit({
                "title": title,
                "start_date": due_d,
                "end_date": due_d,
                "all_day": True,
                "description": desc,
            })

    # ----------------------------------------------------------------------
    # TAB 4: 만 나이 / 근속기간
    # ----------------------------------------------------------------------
    def _build_age_tenure_tab(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        # Section 1: 만 나이 & 띠
        age_group = QGroupBox("만 나이 및 띠(간지) 계산")
        ag_layout = QVBoxLayout(age_group)
        ag_layout.setSpacing(10)

        ag_grid = QGridLayout()
        ag_grid.addWidget(QLabel("생년월일:"), 0, 0)
        self.t4_birth_date = self._create_date_edit(date(1995, 1, 1))
        self.t4_birth_date.dateChanged.connect(self._recalc_tab4_age)
        ag_grid.addWidget(self.t4_birth_date, 0, 1)

        ag_grid.addWidget(QLabel("기준일:"), 1, 0)
        self.t4_age_base = self._create_date_edit()
        self.t4_age_base.dateChanged.connect(self._recalc_tab4_age)
        ag_grid.addWidget(self.t4_age_base, 1, 1)
        ag_grid.addWidget(self._today_button(self.t4_age_base), 1, 2)
        ag_layout.addLayout(ag_grid)

        # Age Results
        age_res_card = QFrame()
        age_res_card.setObjectName("softCard")
        arc_layout = QVBoxLayout(age_res_card)
        arc_layout.setSpacing(6)

        self.t4_age_main_lbl = QLabel("")
        self.t4_age_main_lbl.setStyleSheet("font-size: 16px; font-weight: 800; color: #1f7a67;")
        arc_layout.addWidget(self.t4_age_main_lbl)

        self.t4_zodiac_lbl = QLabel("")
        self.t4_zodiac_lbl.setStyleSheet("font-size: 13px; font-weight: 600;")
        arc_layout.addWidget(self.t4_zodiac_lbl)

        self.t4_next_bday_lbl = QLabel("")
        self.t4_next_bday_lbl.setStyleSheet(f"font-size: 12px; color: {self.palette.get('muted', '#667085')};")
        arc_layout.addWidget(self.t4_next_bday_lbl)

        ag_act = QHBoxLayout()
        ag_act.addStretch()
        self.t4_reg_bday_btn = QPushButton("+ 생일 일정 등록")
        self.t4_reg_bday_btn.setFixedHeight(28)
        self.t4_reg_bday_btn.setStyleSheet("min-width: 80px; font-size: 11px;")
        self.t4_reg_bday_btn.clicked.connect(self._on_tab4_register_bday)
        ag_act.addWidget(self.t4_reg_bday_btn)
        arc_layout.addLayout(ag_act)

        ag_layout.addWidget(age_res_card)
        layout.addWidget(age_group)

        # Section 2: 근속기간 / 재직일수
        tenure_group = QGroupBox("근속기간 / 재직일수 계산")
        tg_layout = QVBoxLayout(tenure_group)
        tg_layout.setSpacing(10)

        tg_grid = QGridLayout()
        tg_grid.addWidget(QLabel("입사일(시작일):"), 0, 0)
        self.t4_tenure_start = self._create_date_edit(date(2020, 1, 1))
        self.t4_tenure_start.dateChanged.connect(self._recalc_tab4_tenure)
        tg_grid.addWidget(self.t4_tenure_start, 0, 1)

        tg_grid.addWidget(QLabel("퇴사/기준일:"), 1, 0)
        self.t4_tenure_end = self._create_date_edit()
        self.t4_tenure_end.dateChanged.connect(self._recalc_tab4_tenure)
        tg_grid.addWidget(self.t4_tenure_end, 1, 1)
        tg_grid.addWidget(self._today_button(self.t4_tenure_end), 1, 2)
        tg_layout.addLayout(tg_grid)

        self.t4_current_employed = QCheckBox("현재 재직 중 (오늘 날짜로 자동 고정)")
        self.t4_current_employed.setChecked(True)
        self.t4_current_employed.toggled.connect(self._on_current_employed_toggled)
        tg_layout.addWidget(self.t4_current_employed)

        # Tenure Results
        tenure_res_card = QFrame()
        tenure_res_card.setObjectName("softCard")
        trc_layout = QVBoxLayout(tenure_res_card)
        trc_layout.setSpacing(6)

        self.t4_tenure_main_lbl = QLabel("")
        self.t4_tenure_main_lbl.setStyleSheet("font-size: 15px; font-weight: 800; color: #1f7a67;")
        trc_layout.addWidget(self.t4_tenure_main_lbl)

        self.t4_tenure_days_lbl = QLabel("")
        self.t4_tenure_days_lbl.setStyleSheet(f"font-size: 12px; color: {self.palette.get('muted', '#667085')};")
        trc_layout.addWidget(self.t4_tenure_days_lbl)

        tg_act = QHBoxLayout()
        tg_act.addStretch()
        self.t4_reg_tenure_btn = QPushButton("+ 근속/입사 기념일 등록")
        self.t4_reg_tenure_btn.setFixedHeight(28)
        self.t4_reg_tenure_btn.setStyleSheet("min-width: 90px; font-size: 11px;")
        self.t4_reg_tenure_btn.clicked.connect(self._on_tab4_register_tenure)
        tg_act.addWidget(self.t4_reg_tenure_btn)
        trc_layout.addLayout(tg_act)

        tg_layout.addWidget(tenure_res_card)
        layout.addWidget(tenure_group)

        scroll.setWidget(container)

        root_w_layout = QVBoxLayout(w)
        root_w_layout.setContentsMargins(0, 0, 0, 0)
        root_w_layout.addWidget(scroll)

        self._recalc_tab4_age()
        self._recalc_tab4_tenure()
        return w

    def _on_current_employed_toggled(self, checked: bool) -> None:
        self.t4_tenure_end.setEnabled(not checked)
        if checked:
            self.t4_tenure_end.setDate(QDate.currentDate())
        self._recalc_tab4_tenure()

    def _recalc_tab4_age(self) -> None:
        q_b = self.t4_birth_date.date()
        q_ref = self.t4_age_base.date()
        birth_d = date(q_b.year(), q_b.month(), q_b.day())
        ref_d = date(q_ref.year(), q_ref.month(), q_ref.day())

        # 만 나이
        has_had_birthday = (ref_d.month, ref_d.day) >= (birth_d.month, birth_d.day)
        international_age = ref_d.year - birth_d.year - (0 if has_had_birthday else 1)
        year_age = ref_d.year - birth_d.year

        status_txt = "생일 지남" if has_had_birthday else "생일 안 지남"
        self.t4_age_main_lbl.setText(f"만 {international_age}세 ({status_txt})  |  연 나이: {year_age}세")

        # 띠 & 간지
        animal, ganji = _get_zodiac_info(birth_d.year)
        lunar = get_lunar_date(birth_d)
        lunar_str = f", 음력 {lunar.formatted(prefix=False)}" if lunar else ""
        self.t4_zodiac_lbl.setText(f"띠/간지: {animal} ({ganji}생{lunar_str})")

        # 다음 생일 계산
        try:
            this_bday = date(ref_d.year, birth_d.month, birth_d.day)
        except ValueError:
            this_bday = date(ref_d.year, birth_d.month, 28)

        if this_bday >= ref_d:
            next_bday = this_bday
        else:
            try:
                next_bday = date(ref_d.year + 1, birth_d.month, birth_d.day)
            except ValueError:
                next_bday = date(ref_d.year + 1, birth_d.month, 28)

        rem_days = (next_bday - ref_d).days
        rem_txt = "오늘이 생일입니다! 🎉" if rem_days == 0 else f"D-{rem_days}일 ({next_bday.strftime('%Y년 %m월 %d일')})"
        self.t4_next_bday_lbl.setText(f"다음 생일까지: {rem_txt}")

        self._t4_next_bday_date = next_bday
        self._t4_birth_date = birth_d
        self._t4_int_age = international_age

    def _on_tab4_register_bday(self) -> None:
        if not hasattr(self, "_t4_next_bday_date"):
            return
        bday = self._t4_next_bday_date
        birth_d = self._t4_birth_date
        title = f"[생일] 생일 ({bday.strftime('%m/%d')})"
        desc = (
            f"• 생일: {bday.strftime('%Y-%m-%d')}\n"
            f"• 출생일: {birth_d.strftime('%Y-%m-%d')}\n"
            f"• 만 나이: 만 {self._t4_int_age + 1}세 도달"
        )
        self.register_schedule_requested.emit({
            "title": title,
            "start_date": bday,
            "end_date": bday,
            "all_day": True,
            "description": desc,
        })

    def _recalc_tab4_tenure(self) -> None:
        q_s = self.t4_tenure_start.date()
        q_e = self.t4_tenure_end.date()
        start_d = date(q_s.year(), q_s.month(), q_s.day())
        end_d = date(q_e.year(), q_e.month(), q_e.day())

        if end_d < start_d:
            start_d, end_d = end_d, start_d

        y, m, d = _calc_ymd_diff(start_d, end_d)
        total_days = (end_d - start_d).days + 1  # include first day

        parts = []
        if y > 0:
            parts.append(f"{y}년")
        if m > 0 or y > 0:
            parts.append(f"{m}개월")
        parts.append(f"{d}일")

        self.t4_tenure_main_lbl.setText(f"총 근속기간: {' '.join(parts)}")
        self.t4_tenure_days_lbl.setText(
            f"총 {total_days:,}일간 재직 ({start_d.strftime('%Y-%m-%d')} ~ {end_d.strftime('%Y-%m-%d')})"
        )

        self._t4_tenure_data = {
            "start_date": start_d,
            "end_date": end_d,
            "years": y,
            "summary": " ".join(parts),
            "total_days": total_days,
        }

    def _on_tab4_register_tenure(self) -> None:
        if not hasattr(self, "_t4_tenure_data"):
            return
        data = self._t4_tenure_data
        s = data["start_date"]
        e = data["end_date"]
        y = data["years"]
        title = f"[근속] 입사 {y}주년 ({s.strftime('%m/%d')})"
        desc = (
            f"• 입사일: {s.strftime('%Y-%m-%d')}\n"
            f"• 기준일: {e.strftime('%Y-%m-%d')}\n"
            f"• 근속기간: {data['summary']} (총 {data['total_days']:,}일)"
        )
        self.register_schedule_requested.emit({
            "title": title,
            "start_date": e,
            "end_date": e,
            "all_day": True,
            "description": desc,
        })
