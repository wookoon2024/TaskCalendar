from __future__ import annotations

import calendar
import ctypes
from ctypes import wintypes
from datetime import date, datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css, make_ui_font, make_ui_font_like, scale_px, ui_font_family
from taskcalendar.lunar import get_korean_holiday_name, get_lunar_date, get_solar_term
from taskcalendar.models import CalendarEntry, EntryType
from taskcalendar.qt_dialogs import get_sticker_pixmap

if TYPE_CHECKING:
    from taskcalendar.qt_main_window import MainWindow

logger = logging.getLogger(__name__)

WIDGET_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "widget"


def hex_to_rgba(hex_code: str, alpha: float) -> str:
    color = QColor(hex_code)
    if not color.isValid():
        return f"rgba(255, 255, 255, {alpha})"
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def get_entry_title_with_icon(entry: CalendarEntry, parent_window: MainWindow | None = None) -> str:
    """메인 캘린더와 동일하게 이모지 아이콘이 포함된 제목 텍스트 반환"""
    if parent_window and hasattr(parent_window, "_entry_title_text"):
        try:
            return parent_window._entry_title_text(entry)
        except Exception:
            pass
    icon_type = str(getattr(entry, "icon_type", "") or "")
    if icon_type and not (icon_type.startswith(("custom:", "built_in:")) or icon_type.endswith(".png")):
        legacy = {
            "anniversary": "🎂",
            "important": "⭐",
            "coffee": "☕",
            "meal": "🍚",
            "meeting": "👥",
            "floating": "",
        }
        emoji = legacy.get(icon_type, icon_type)
        if emoji:
            return f"{emoji} {entry.title}".strip()
    return entry.title


class DesktopWidgetEntryItem(QFrame):
    """바탕화면 위젯 내 개별 일정 한 줄 표시 (메인 캘린더 칩 스타일 동일 적용)"""

    clicked = Signal(object)          # 단일 클릭: 해당 날짜 셀 선택
    double_clicked = Signal(object)   # 더블 클릭: 일정 편집창 열기

    def __init__(self, entry: CalendarEntry, target_day: date, parent_window: MainWindow, palette: dict[str, str], parent=None):
        super().__init__(parent)
        self.entry = entry
        self.target_day = target_day
        self.parent_window = parent_window
        self.palette = palette
        self._press_pos: QPoint | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(max(19, scale_px(19)))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

        is_completed = False
        try:
            is_completed = parent_window._is_entry_completed_on_day(entry, target_day)
        except Exception:
            is_completed = (entry.status == "완료")

        chip_bg = str(getattr(entry, "bg_color", "") or "").strip()
        actual_entry_fg = palette.get("text", "#1F2328")
        actual_time_fg = palette.get("info", "#2563EB")
        if chip_bg:
            try:
                c = chip_bg.lstrip("#")
                if len(c) == 6:
                    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
                    lum = 0.299 * r + 0.587 * g + 0.114 * b
                    if lum > 140:
                        actual_entry_fg = "#111827"
                        actual_time_fg = "#1e3a8a"
                    else:
                        actual_entry_fg = "#f9fafb"
                        actual_time_fg = "#93c5fd"
            except Exception:
                pass
        if is_completed:
            actual_entry_fg = palette.get("muted", "#9CA3AF")
            actual_time_fg = palette.get("muted", "#9CA3AF")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 0, 3, 0)
        layout.setSpacing(2)

        # 1. 스티커 아이콘 (PNG 이미지 파일인 경우 픽스맵 라벨로 표시)
        icon_type = str(getattr(entry, "icon_type", "") or "")
        if icon_type and (icon_type.startswith(("custom:", "built_in:")) or icon_type.endswith(".png")):
            try:
                pix = get_sticker_pixmap(icon_type)
                if pix and not pix.isNull():
                    icon_lbl = QLabel(self)
                    icon_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                    icon_lbl.setPixmap(pix.scaled(14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    icon_lbl.setStyleSheet("background: transparent; border: none; margin-right: 1px;")
                    layout.addWidget(icon_lbl)
            except Exception:
                pass

        # 2. 시간 (있을 경우)
        strike = " text-decoration: line-through;" if is_completed else ""
        if entry.start_time:
            time_label = QLabel(f"[{entry.start_time}] ", self)
            time_label.setFont(make_ui_font_like(9))
            time_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            time_label.setStyleSheet(f"color: {actual_time_fg}; background: transparent; border: none; padding: 0px; margin: 0px;{strike}")
            layout.addWidget(time_label)

        # 3. 제목 라벨 (이모지 아이콘 포함, 메인 캘린더와 동일)
        title_text = get_entry_title_with_icon(entry, parent_window)
        full_text = f"[{entry.start_time}] {title_text}" if entry.start_time else title_text

        self.title_label = QLabel(title_text, self)
        self.title_label.setFont(make_ui_font_like(9.5))
        self.title_label.setMinimumWidth(0)
        self.title_label.setToolTip(full_text)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setStyleSheet(f"color: {actual_entry_fg}; font-weight: normal; background: transparent; border: none; padding: 0px; margin: 0px;{strike}")
        layout.addWidget(self.title_label, 1)

        # 4. 배경색 스타일 적용 (설정된 배경색이 있으면 메인 캘린더처럼 QFrame 칩 형태로 예쁘게 렌더링)
        if chip_bg:
            hover_bg = hex_to_rgba(chip_bg, 0.85)
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {chip_bg};
                    border: none;
                    border-radius: 4px;
                }}
                QFrame:hover {{
                    background-color: {hover_bg};
                }}
            """)
        else:
            hover_bg = hex_to_rgba(palette.get("text", "#1F2328"), 0.08)
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: transparent;
                    border: none;
                    border-radius: 3px;
                }}
                QFrame:hover {{
                    background-color: {hover_bg};
                }}
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and hasattr(self, "_press_pos") and self._press_pos is not None:
            self._press_pos = None
            # 단순 클릭은 해당 날짜 셀 선택만 발생 (편집창 열지 않음)
            self.clicked.emit(self.target_day)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 더블 클릭 시에만 일정 편집 창 열기!
            self.double_clicked.emit(self.entry)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class DesktopWidgetDayCell(QFrame):
    """바탕화면 달력 위젯의 개별 일자 셀 (완전 균등 크기 보장)"""

    day_clicked = Signal(object)
    day_double_clicked = Signal(object)

    def __init__(self, parent_widget: DesktopCalendarWidget, parent=None):
        super().__init__(parent)
        self.parent_widget = parent_widget
        self.day_value: date | None = None
        self.in_month = True
        self.is_today = False
        self.is_selected = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        # 내용물 길이에 영향받지 않고 그리드 크기에 맞춰 강제 균등 분할되도록 설정
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(0, 0)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(1)

        # 셀 상단 헤더 (날짜 번호, 오늘 뱃지, 기념일/음력 라벨)
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(3)

        self.number_label = QLabel()
        self.number_label.setFont(make_ui_font_like(11, weight=QFont.Weight.Bold.value))
        self.number_label.setMinimumWidth(0)
        top_layout.addWidget(self.number_label)

        self.badge_label = QLabel("오늘")
        self.badge_label.setFont(make_ui_font_like(8, weight=QFont.Weight.Bold.value))
        self.badge_label.hide()
        top_layout.addWidget(self.badge_label)

        top_layout.addStretch(1)

        self.sub_label = QLabel()
        self.sub_label.setFont(make_ui_font_like(8))
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sub_label.setMinimumWidth(0)
        top_layout.addWidget(self.sub_label)

        self.main_layout.addLayout(top_layout)

        # 일정 항목들을 담는 컨테이너
        self.items_container = QWidget()
        self.items_container.setMinimumWidth(0)
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(0, 1, 0, 0)
        self.items_layout.setSpacing(2)
        self.main_layout.addWidget(self.items_container, 1)

    def clear_items(self):
        while self.items_layout.count() > 0:
            item = self.items_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def update_cell(
        self,
        target_day: date,
        in_month: bool,
        is_today: bool,
        is_selected: bool,
        holiday_name: str,
        solar_term: str,
        lunar_text: str,
        entries: list[CalendarEntry],
        palette: dict[str, str],
    ):
        self.day_value = target_day
        self.in_month = in_month
        self.is_today = is_today
        self.is_selected = is_selected

        self.clear_items()
        self.number_label.setText(str(target_day.day))
        self.number_label.setFont(make_ui_font_like(11, weight=QFont.Weight.Bold.value if in_month else QFont.Weight.Normal.value))

        # 날짜 번호 색상
        danger_color = palette.get("danger", "#DC2626")
        info_color = palette.get("info", "#2563EB")
        text_color = palette.get("text", "#1F2328")
        muted_color = palette.get("muted", "#9CA3AF")

        if holiday_name or target_day.weekday() == 6:  # 일요일 또는 공휴일
            num_color = danger_color if in_month else hex_to_rgba(danger_color, 0.45)
        elif target_day.weekday() == 5:  # 토요일
            num_color = info_color if in_month else hex_to_rgba(info_color, 0.45)
        else:
            num_color = text_color if in_month else muted_color

        weight = "bold" if in_month else "normal"
        self.number_label.setStyleSheet(f"color: {num_color}; font-weight: {weight}; background: transparent; border: none;")

        # 오늘 뱃지
        if is_today:
            accent = palette.get("accent", "#10B981")
            self.badge_label.setFont(make_ui_font_like(8, weight=QFont.Weight.Bold.value))
            self.badge_label.setStyleSheet(f"""
                QLabel {{
                    background-color: {accent};
                    color: #FFFFFF;
                    font-weight: bold;
                    padding: 0px 3px;
                    border-radius: 3px;
                }}
            """)
            self.badge_label.show()
        else:
            self.badge_label.hide()

        # 보조 텍스트 (상단 우측: 24절기 > 음력 순, 공휴일은 아래 독립 행으로 배치하여 잘림 방지)
        sub_text = solar_term or lunar_text
        if sub_text:
            if solar_term:
                sub_style = f"color: {palette.get('accent', '#10B981')}; font-weight: bold; background: transparent; border: none;"
            else:
                sub_style = f"color: {muted_color}; background: transparent; border: none;"
            self.sub_label.setText(sub_text)
            self.sub_label.setToolTip(sub_text)
            self.sub_label.setFont(make_ui_font_like(8))
            self.sub_label.setStyleSheet(sub_style)
            self.sub_label.show()
        else:
            self.sub_label.hide()

        # 셀 배경 및 테두리 스타일 (클릭 시 칸 크기가 줄어들지 않도록 1px 두께 엄격 고정)
        bg_alpha = 0.65 if in_month else 0.25
        if is_today:
            bg_alpha = 0.85
            border_color = palette.get("accent", "#10B981")
        elif is_selected:
            bg_alpha = 0.80
            border_color = palette.get("info", "#2563EB")
        else:
            border_color = hex_to_rgba(palette.get("line", "#E2E8F0"), 0.5)

        panel_hex = palette.get("panel", "#FFFFFF")
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {hex_to_rgba(panel_hex, bg_alpha)};
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
            QFrame:hover {{
                background-color: {hex_to_rgba(panel_hex, min(1.0, bg_alpha + 0.15))};
            }}
        """)

        # 공휴일 라벨 (메인 캘린더처럼 items_layout 맨 첫 번째 줄에 독립 행으로 배치하여 '개천절 대체공휴일' 등 긴 이름도 전체 폭 활용)
        if holiday_name:
            holiday_label = QLabel(holiday_name)
            holiday_label.setFont(make_ui_font_like(9, weight=QFont.Weight.Bold.value))
            holiday_label.setStyleSheet(f"color: {danger_color}; font-weight: bold; background: transparent; border: none; padding: 0px 2px;")
            holiday_label.setToolTip(holiday_name)
            self.items_layout.addWidget(holiday_label)

        # 컴팩트 일정 한 줄 항목 배치 (클릭 시 선택, 더블 클릭 시 편집)
        max_items = 3 if holiday_name else 4
        schedules = [e for e in entries if e.entry_type != EntryType.TASK]
        for entry in schedules[:max_items]:
            item = DesktopWidgetEntryItem(entry, target_day, self.parent_widget.main_window, palette, self)
            item.clicked.connect(self.day_clicked.emit)
            item.double_clicked.connect(self.parent_widget._on_entry_clicked)
            self.items_layout.addWidget(item)

        if len(schedules) > max_items:
            more_count = len(schedules) - max_items
            more_label = QLabel(f"+{more_count}건 더보기")
            more_label.setFont(make_ui_font_like(8.5, weight=QFont.Weight.Bold.value))
            more_label.setStyleSheet(f"color: {muted_color}; font-weight: bold; background: transparent; border: none; padding-left: 2px;")
            self.items_layout.addWidget(more_label)

        # 항목들을 위쪽에 정렬하고 남는 공간은 stretch가 채우도록 하여 목록 줄간격 벌어짐 방지!
        self.items_layout.addStretch(1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.day_clicked.emit(self.day_value)
            event.accept()
            return
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.day_double_clicked.emit(self.day_value)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _show_context_menu(self, pos: QPoint):
        if not self.day_value:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E0;
                border-radius: 6px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #F1F5F9;
                color: #0F172A;
            }
        """)
        act_add_sch = menu.addAction(f"📅 {self.day_value.strftime('%m-%d')} 일정 추가")
        act_add_sch.triggered.connect(lambda: self.parent_widget.main_window._open_add_for_day(self.day_value))

        act_add_memo = menu.addAction(f"📝 {self.day_value.strftime('%m-%d')} 메모 추가")
        act_add_memo.triggered.connect(lambda: self.parent_widget.main_window._edit_entry(EntryType.MEMO, None))

        menu.addSeparator()
        act_open_main = menu.addAction("🗔 캘린더 메인 창 열기")
        act_open_main.triggered.connect(self.parent_widget._open_main_calendar)

        menu.exec(pos)


class DesktopCalendarWidget(QWidget):
    """DesktopCal 스타일의 바탕화면 월간 달력 위젯"""

    def __init__(self, main_window: MainWindow):
        super().__init__(None)
        self.main_window = main_window
        self.repository = main_window.repository
        self.today = date.today()
        self.current_year = self.today.year
        self.current_month = self.today.month
        self.selected_day = self.today

        # 윈도우 기본 설정 (프레임리스 + 툴 윈도우 + 반투명 배경)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumSize(480, 320)

        # 설정값 로드
        self.is_locked = self.repository.get_setting("desktop_widget_locked", "0") == "1"
        self.is_pinned = True  # 바탕화면 벽지 계층 기본 고정
        try:
            self.opacity_pct = int(self.repository.get_setting("desktop_widget_opacity", "85"))
        except Exception:
            self.opacity_pct = 85

        # AI 생성 고해상도 위젯 아이콘 로드
        self._load_widget_icons()

        # 드래그 이동 관련 상태
        self._drag_pos: QPoint | None = None
        self._day_cells: list[DesktopWidgetDayCell] = []

        self._init_ui()
        self._restore_geometry()
        self.apply_theme()
        self.refresh_calendar()
        self._apply_desktop_zorder()

    def _load_widget_icons(self):
        closed_path = WIDGET_ASSETS_DIR / "lock_closed.png"
        open_path = WIDGET_ASSETS_DIR / "lock_open.png"
        opacity_path = WIDGET_ASSETS_DIR / "opacity.png"

        self._icon_lock_closed = QIcon(str(closed_path)) if closed_path.exists() else QIcon()
        self._icon_lock_open = QIcon(str(open_path)) if open_path.exists() else QIcon()
        self._pixmap_opacity = QPixmap(str(opacity_path)) if opacity_path.exists() else QPixmap()

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(6, 6, 6, 6)
        outer_layout.setSpacing(0)

        # 메인 컨테이너 프레임
        self.container = QFrame(self)
        self.container.setObjectName("widgetContainer")
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(6)

        # 1. 헤더 툴바 (네비게이션, 투명도 드래그 슬라이더, 제어 버튼)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        # 이전달 버튼
        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.clicked.connect(self.prev_month)
        header_layout.addWidget(self.btn_prev)

        # 년월 표시 라벨
        self.lbl_year_month = QLabel()
        self.lbl_year_month.setFont(make_ui_font(15, weight=QFont.Weight.Bold.value))
        header_layout.addWidget(self.lbl_year_month)

        # 다음달 버튼
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.clicked.connect(self.next_month)
        header_layout.addWidget(self.btn_next)

        # 오늘 버튼 (크고 뚜렷하게)
        self.btn_today = QPushButton("오늘")
        self.btn_today.setFixedSize(50, 28)
        self.btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_today.setFont(make_ui_font_like(10.5, weight=QFont.Weight.Bold.value))
        self.btn_today.clicked.connect(self.go_today)
        header_layout.addWidget(self.btn_today)

        # 상태 안내 라벨 (잠금 해제 시 드래그 이동 안내 - 높이 28px 고정으로 헤더 높이 변화 차단)
        self.lbl_status = QLabel()
        self.lbl_status.setFixedHeight(28)
        self.lbl_status.setFont(make_ui_font_like(10, weight=QFont.Weight.Bold.value))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.lbl_status, 1)

        # 투명도 조절 영역 (세련된 알약형 캡슐 박스)
        self.opacity_box = QWidget()
        self.opacity_box.setObjectName("opacityBox")
        op_layout = QHBoxLayout(self.opacity_box)
        op_layout.setContentsMargins(7, 2, 7, 2)
        op_layout.setSpacing(5)

        self.lbl_op_icon = QLabel()
        if not self._pixmap_opacity.isNull():
            self.lbl_op_icon.setPixmap(
                self._pixmap_opacity.scaled(17, 17, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.lbl_op_icon.setText("🔆")
        self.lbl_op_icon.setStyleSheet("background: transparent; border: none;")
        self.lbl_op_icon.setToolTip("위젯 투명도 조절")
        op_layout.addWidget(self.lbl_op_icon)

        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(20, 100)
        self.slider_opacity.setValue(self.opacity_pct)
        self.slider_opacity.setFixedWidth(80)
        self.slider_opacity.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider_opacity.setToolTip("좌우로 드래그하여 투명도 실시간 조절 (20% ~ 100%)")
        self.slider_opacity.valueChanged.connect(self._on_opacity_slider_changed)
        self.slider_opacity.sliderReleased.connect(self._save_opacity_setting)
        op_layout.addWidget(self.slider_opacity)

        self.lbl_opacity_val = QLabel(f"{self.opacity_pct}%")
        self.lbl_opacity_val.setFont(make_ui_font_like(8.5, weight=QFont.Weight.Bold.value))
        self.lbl_opacity_val.setFixedWidth(32)
        self.lbl_opacity_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        op_layout.addWidget(self.lbl_opacity_val)
        header_layout.addWidget(self.opacity_box)

        # 잠금 토글 버튼 (AI 생성 고화질 자물쇠 아이콘 적용)
        self.btn_lock = QPushButton()
        self.btn_lock.setFixedSize(28, 28)
        self.btn_lock.setIconSize(QSize(18, 18))
        self.btn_lock.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lock.setToolTip("위치 및 크기 고정 / 해제")
        self.btn_lock.clicked.connect(self.toggle_locked)
        header_layout.addWidget(self.btn_lock)

        # 메인 캘린더 창 열기 버튼
        self.btn_open_main = QPushButton("🗔")
        self.btn_open_main.setFixedSize(28, 28)
        self.btn_open_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_main.setToolTip("TaskCalendar 메인 창 열기")
        self.btn_open_main.clicked.connect(self._open_main_calendar)
        header_layout.addWidget(self.btn_open_main)

        # 위젯 닫기 버튼
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip("바탕화면 달력 위젯 숨기기 (트레이에서 다시 켜기 가능)")
        self.btn_close.clicked.connect(self.close_widget)
        header_layout.addWidget(self.btn_close)

        container_layout.addLayout(header_layout)

        # 2. 달력 통합 그리드 (0행: 요일 헤더, 1~6행: 42일 셀)
        # 요일과 날짜 셀을 동일한 QGridLayout에 배치하여 7개 열이 100% 동일한 폭으로 정렬되도록 보장
        self.grid_widget = QWidget()
        self.calendar_grid = QGridLayout(self.grid_widget)
        self.calendar_grid.setContentsMargins(0, 0, 0, 0)
        self.calendar_grid.setHorizontalSpacing(4)
        self.calendar_grid.setVerticalSpacing(4)

        # 요일 헤더 (0행)
        weekday_names = ["일", "월", "화", "수", "목", "금", "토"]
        self.weekday_labels = []
        for col, name in enumerate(weekday_names):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(make_ui_font_like(10, weight=QFont.Weight.Bold.value))
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            lbl.setFixedHeight(22)
            lbl.setMinimumWidth(0)
            if col == 0:
                lbl.setStyleSheet("color: #DC2626; font-weight: bold; background: transparent;")
            elif col == 6:
                lbl.setStyleSheet("color: #2563EB; font-weight: bold; background: transparent;")
            else:
                lbl.setStyleSheet("color: #4B5563; font-weight: bold; background: transparent;")
            self.weekday_labels.append(lbl)
            self.calendar_grid.addWidget(lbl, 0, col)
            self.calendar_grid.setColumnStretch(col, 1)

        self.calendar_grid.setRowMinimumHeight(0, 22)
        self.calendar_grid.setRowStretch(0, 0)

        # 42일 날짜 셀 (1행~6행)
        for row in range(6):
            self.calendar_grid.setRowStretch(row + 1, 1)
            for col in range(7):
                cell = DesktopWidgetDayCell(self)
                cell.day_clicked.connect(self._on_day_clicked)
                cell.day_double_clicked.connect(self._on_day_double_clicked)
                self._day_cells.append(cell)
                self.calendar_grid.addWidget(cell, row + 1, col)

        container_layout.addWidget(self.grid_widget, 1)

        # 3. 하단 상태 및 리사이즈 그립 (잠금/해제 전환 시 높이 흔들림 없도록 16px 고정)
        bottom_widget = QWidget()
        bottom_widget.setFixedHeight(16)
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addStretch(1)

        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(16, 16)
        bottom_layout.addWidget(self.size_grip)
        container_layout.addWidget(bottom_widget)

        outer_layout.addWidget(self.container)

        self._update_lock_ui()

    def _enforce_equal_cells(self):
        """그리드 위젯의 7열 x 6행 셀이 100% 동일한 폭과 높이로 균등 분할되도록 설정하되,
        최소 크기를 키우지 않아 창 축소(줄이기)가 자유자재로 되도록 보장"""
        if not hasattr(self, "grid_widget"):
            return
        header_h = 22
        for col in range(7):
            self.calendar_grid.setColumnMinimumWidth(col, 10)
            self.calendar_grid.setColumnStretch(col, 1)
        self.calendar_grid.setRowMinimumHeight(0, header_h)
        self.calendar_grid.setRowStretch(0, 0)
        for row in range(6):
            self.calendar_grid.setRowMinimumHeight(row + 1, 10)
            self.calendar_grid.setRowStretch(row + 1, 1)

    def apply_theme(self):
        palette = self.main_window.palette
        panel_hex = palette.get("panel", "#FFFFFF")
        text_color = palette.get("text", "#1F2328")
        line_color = palette.get("line", "#CBD5E0")
        accent = palette.get("accent", "#10B981")

        alpha = max(0.2, min(1.0, self.opacity_pct / 100.0))
        bg_rgba = hex_to_rgba(panel_hex, alpha)
        border_rgba = hex_to_rgba(line_color, 0.8)

        # 컨테이너 프레임 스타일 (잠금/해제 상태 모두 1px solid로 두께를 완전히 일치시켜 크기 변화 원천 차단)
        border_col = hex_to_rgba(accent, 0.7) if not self.is_locked else border_rgba
        self.container.setStyleSheet(f"""
            QFrame#widgetContainer {{
                background-color: {bg_rgba};
                border: 1px solid {border_col};
                border-radius: 12px;
                font-family: {font_family_css()};
            }}
        """)

        # 요일 헤더 및 제목 글꼴 갱신 (프로그램 UI 글꼴/크기 연동)
        for lbl in getattr(self, "weekday_labels", []):
            lbl.setFont(make_ui_font_like(10, weight=QFont.Weight.Bold.value))

        self.lbl_year_month.setFont(make_ui_font_like(13, weight=QFont.Weight.Bold.value))
        self.lbl_opacity_val.setFont(make_ui_font_like(8.5, weight=QFont.Weight.Bold.value))

        # 헤더 버튼 스타일
        btn_bg = hex_to_rgba(palette.get("panel_alt", "#F1F5F9"), 0.8)
        btn_hover = hex_to_rgba(palette.get("accent_soft", "#E6F0EC"), 0.95)
        btn_style = f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_color};
                border: 1px solid {hex_to_rgba(line_color, 0.5)};
                border-radius: 6px;
                padding: 2px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
                border-color: {accent};
            }}
        """
        for btn in (self.btn_prev, self.btn_next, self.btn_lock, self.btn_open_main, self.btn_close):
            btn.setStyleSheet(btn_style)

        # 오늘 버튼 전용 스타일 (시인성 좋게 강조)
        today_style = f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_color};
                border: 1.5px solid {hex_to_rgba(accent, 0.7)};
                border-radius: 6px;
                padding: 2px 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {accent};
                color: #FFFFFF;
                border-color: {accent};
            }}
        """
        self.btn_today.setStyleSheet(today_style)
        self.btn_today.setFont(make_ui_font_like(10.5, weight=QFont.Weight.Bold.value))

        # 투명도 조절 박스 전용 세련된 캡슐 스타일
        op_bg = hex_to_rgba(palette.get("panel_alt", "#F1F5F9"), 0.75)
        self.opacity_box.setStyleSheet(f"""
            QWidget#opacityBox {{
                background-color: {op_bg};
                border: 1px solid {hex_to_rgba(line_color, 0.45)};
                border-radius: 6px;
            }}
        """)

        # 슬라이더 스타일 (모던하고 깔끔한 디자인)
        self.slider_opacity.setStyleSheet(f"""
            QSlider {{
                background: transparent;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {hex_to_rgba(line_color, 0.7)};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {accent};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
                background: #FFFFFF;
                border: 2px solid {accent};
            }}
            QSlider::handle:horizontal:hover {{
                background: #F0FDF4;
                border-width: 2.5px;
            }}
        """)

        self.lbl_year_month.setStyleSheet(f"color: {text_color}; background: transparent; border: none;")
        self.lbl_opacity_val.setStyleSheet(f"color: {text_color}; background: transparent; border: none; font-weight: bold;")

    def refresh_calendar(self):
        self.lbl_year_month.setText(f"{self.current_year}년 {self.current_month}월")

        cal = calendar.Calendar(firstweekday=6)  # 일요일 시작
        weeks = cal.monthdatescalendar(self.current_year, self.current_month)
        while len(weeks) < 6:
            last_day = weeks[-1][-1]
            weeks.append([last_day.fromordinal(last_day.toordinal() + offset + 1) for offset in range(7)])
        flat_days = [item for week in weeks[:6] for item in week]

        # list_entries_for_month는 이미 calendar_days(year, month)로 42일 전체 그리드 일정을 전개하므로
        # 현재 연/월에 대해 1회만 호출하여 중복 전개 방지
        all_entries = self.repository.list_entries_for_month(self.current_year, self.current_month)

        hide_completed = self.repository.get_setting("hide_completed_on_calendar", "1") == "1"
        grouped: dict[date, list[CalendarEntry]] = {}
        seen_keys: set[tuple[int | None, date]] = set()

        for entry in all_entries:
            if not entry.day:
                continue
            key = (entry.entry_id, entry.day)
            if entry.entry_id is not None and key in seen_keys:
                continue
            seen_keys.add(key)

            if entry.entry_type != EntryType.TASK and hide_completed:
                try:
                    if self.main_window._is_entry_completed_on_day(entry, entry.day):
                        continue
                except Exception:
                    pass
            grouped.setdefault(entry.day, []).append(entry)

        palette = self.main_window.palette
        show_lunar = self.repository.get_setting("show_lunar_calendar", "1") == "1"
        show_solar = self.repository.get_setting("show_solar_terms", "1") == "1"

        for idx, current_day in enumerate(flat_days):
            cell = self._day_cells[idx]
            in_month = (current_day.month == self.current_month)
            is_today = (current_day == date.today())
            is_selected = (current_day == self.selected_day)

            # 공휴일
            holiday_name = ""
            try:
                holiday_name = self.main_window._holiday_name_for_day(current_day)
            except Exception:
                holiday_name = get_korean_holiday_name(current_day) or ""

            # 24절기
            solar_term = get_solar_term(current_day) if show_solar else ""

            # 음력
            lunar_text = ""
            if show_lunar:
                try:
                    raw_lunar = get_lunar_date(current_day)
                    if raw_lunar and (raw_lunar.day in (1, 15) or current_day.day == 1):
                        leap_pfx = "윤" if raw_lunar.is_leap else ""
                        lunar_text = f"음 {leap_pfx}{raw_lunar.month}.{raw_lunar.day}"
                except Exception:
                    pass

            day_entries = grouped.get(current_day, [])
            cell.update_cell(
                target_day=current_day,
                in_month=in_month,
                is_today=is_today,
                is_selected=is_selected,
                holiday_name=holiday_name,
                solar_term=solar_term,
                lunar_text=lunar_text,
                entries=day_entries,
                palette=palette,
            )

        self._enforce_equal_cells()

    def prev_month(self):
        if self.current_month == 1:
            self.current_year -= 1
            self.current_month = 12
        else:
            self.current_month -= 1
        self.refresh_calendar()

    def next_month(self):
        if self.current_month == 12:
            self.current_year += 1
            self.current_month = 1
        else:
            self.current_month += 1
        self.refresh_calendar()

    def go_today(self):
        self.current_year = self.today.year
        self.current_month = self.today.month
        self.selected_day = self.today
        self.refresh_calendar()

    def toggle_locked(self):
        self.is_locked = not self.is_locked
        self.repository.set_setting("desktop_widget_locked", "1" if self.is_locked else "0")
        self.repository.save()
        self._update_lock_ui()
        self.apply_theme()

    def _update_lock_ui(self):
        if self.is_locked:
            if not self._icon_lock_closed.isNull():
                self.btn_lock.setIcon(self._icon_lock_closed)
                self.btn_lock.setText("")
            else:
                self.btn_lock.setText("🔒")
            self.btn_lock.setToolTip("위치 및 크기 고정됨 (클릭하여 잠금 해제)")
            self.lbl_status.setText("")
            self.size_grip.hide()
        else:
            if not self._icon_lock_open.isNull():
                self.btn_lock.setIcon(self._icon_lock_open)
                self.btn_lock.setText("")
            else:
                self.btn_lock.setText("🔓")
            self.btn_lock.setToolTip("위치 및 크기 잠금 해제됨 (클릭하여 고정)")
            self.lbl_status.setText("💡 잠금 해제됨 (상단 드래그: 이동 / 우측 하단: 크기 조절)")
            self.lbl_status.setFont(make_ui_font_like(10, weight=QFont.Weight.Bold.value))
            self.lbl_status.setStyleSheet("color: #1D4ED8; font-weight: bold; background: transparent; padding: 0 4px;")
            self.size_grip.show()

    def _on_opacity_slider_changed(self, val: int):
        self.opacity_pct = val
        self.lbl_opacity_val.setText(f"{val}%")
        self.apply_theme()
        self.refresh_calendar()

    def _save_opacity_setting(self):
        self.repository.set_setting("desktop_widget_opacity", str(self.opacity_pct))
        self.repository.save()

    def _open_main_calendar(self):
        self.main_window._restore_window_state()
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def close_widget(self):
        self.hide()
        self.repository.set_setting("desktop_widget_enabled", "0")
        self.repository.save()
        if hasattr(self.main_window, "_update_desktop_widget_actions"):
            self.main_window._update_desktop_widget_actions()

    def _on_day_clicked(self, target_day: date):
        if self.selected_day == target_day:
            return
        self.selected_day = target_day
        self.refresh_calendar()

    def _on_day_double_clicked(self, target_day: date):
        self.selected_day = target_day
        self.main_window._open_add_for_day(target_day)

    def _on_entry_clicked(self, entry: CalendarEntry):
        if entry.day:
            self.selected_day = entry.day
        self.main_window._edit_entry(entry.entry_type, entry)

    # ==================== 창 이동 및 크기 조절 / Z-Order ====================

    def mousePressEvent(self, event):
        if not self.is_locked and event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.is_locked and self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None:
            self._drag_pos = None
            self._save_geometry()
        self._apply_desktop_zorder()
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._enforce_equal_cells()
        self._save_geometry()

    def _save_geometry(self):
        geom = self.geometry()
        val = f"{geom.x()},{geom.y()},{geom.width()},{geom.height()}"
        self.repository.set_setting("desktop_widget_geometry", val)

    def _restore_geometry(self):
        raw = self.repository.get_setting("desktop_widget_geometry", "")
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        w, h = 760, 560
        x = avail.right() - w - 40
        y = avail.top() + 40

        if raw:
            try:
                parts = [int(p.strip()) for p in raw.split(",")]
                if len(parts) == 4 and parts[2] >= 400 and parts[3] >= 300:
                    x, y, w, h = parts
            except Exception:
                pass

        # 화면 밖으로 나가지 않도록 보정
        x = max(avail.left(), min(x, avail.right() - 100))
        y = max(avail.top(), min(y, avail.bottom() - 100))
        self.setGeometry(x, y, w, h)

    def _apply_desktop_zorder(self):
        """Win32 API를 사용하여 위젯을 바탕화면 배경 레이어(HWND_BOTTOM)로 배치 및 비활성화 유지"""
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            HWND_BOTTOM = 1
            SWP_NOSIZE = 1
            SWP_NOMOVE = 2
            SWP_NOACTIVATE = 0x10

            # 1. WS_EX_NOACTIVATE 스타일 적용 (클릭해도 창이 Foreground 최상위로 승격되지 않고 원래 Z-Order 유지)
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            current_ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
            if not (current_ex & WS_EX_NOACTIVATE):
                user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, current_ex | WS_EX_NOACTIVATE)

            # 2. Z-Order를 모든 일반 프로그램 창 아래(HWND_BOTTOM)로 배치
            user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_BOTTOM), 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        except Exception:
            pass

    def nativeEvent(self, event_type, message):
        """윈도우 메시지를 가로채 마우스 클릭 시에도 창이 다른 작업창 위로 올라오지 않도록 제어"""
        try:
            if event_type == b"windows_generic_MSG":
                addr = int(message)
                msg = wintypes.MSG.from_address(addr)
                if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                    if getattr(self, "is_pinned", True):
                        # MA_NOACTIVATE = 3 (마우스 클릭 이벤트는 위젯이 정상 수신하되, 창을 활성화하여 상단으로 띄우지 않음)
                        return True, 3
        except Exception:
            pass
        return super().nativeEvent(event_type, message)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            # Win+D 등으로 최소화 시 바탕화면 위젯이므로 즉시 복원
            self.showNormal()
            self._apply_desktop_zorder()
