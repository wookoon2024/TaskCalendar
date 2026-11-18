from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QEvent, QItemSelectionModel, QObject, QPoint, QPointF, QRectF, QSize, QStringListModel, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QRadioButton,
    QButtonGroup,
)

from taskcalendar.fonts import font_family_css, make_ui_font_like, scale_px, ui_font_family
from taskcalendar import doc_reply
from taskcalendar.models import CalendarEntry, EntryType
from taskcalendar.paths import asset_path
from taskcalendar.storage import EncryptedRepository
from taskcalendar.qt_styles import resolve_palette, _shade
from taskcalendar.themes import THEMES

logger = logging.getLogger(__name__)


def _months_before(d: date, months: int) -> date:
    """d에서 months개월 전 날짜 (해당 월에 같은 일이 없으면 그 달 말일)"""
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, 28)


def get_dialog_palette(parent=None, repository=None, main_window=None) -> dict[str, str]:
    """부모 위젯, 메인 윈도우, 저장소 설정에서 현재 활성 스킨 팔레트를 안전하게 추출"""
    if parent:
        p = getattr(parent, "palette", None)
        if isinstance(p, dict) and p.get("bg"):
            return p
    if main_window:
        p = getattr(main_window, "palette", None)
        if isinstance(p, dict) and p.get("bg"):
            return p
    if repository:
        t = repository.get_setting("theme", "light")
        if t in THEMES:
            return THEMES[t]
    return THEMES["light"]


class SafeDateEdit(QDateEdit):
    """
    마우스 휠 스크롤, 트랙패드 제스처, 더블클릭, 스핀박스 스텝으로 인해 날짜(년도)가
    의도치 않게 급변(예: 2026 -> 2027 -> 2028)하는 현상을 완벽 차단하는 QDateEdit
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setCalendarPopup(True)
        le = self.lineEdit()
        if le:
            le.installEventFilter(self)
        cal = self.calendarWidget()
        if cal:
            cal.installEventFilter(self)
            year_edit = cal.findChild(QSpinBox, "qt_calendar_yearedit")
            if year_edit:
                year_edit.installEventFilter(self)

    def stepBy(self, steps: int) -> None:
        # 스핀박스 증감에 의한 연도/날짜 변동 차단 (2026->2027 방지)
        pass

    def wheelEvent(self, event) -> None:
        # 마우스 휠에 의한 날짜/연도 증감 차단
        event.ignore()

    def mouseDoubleClickEvent(self, event) -> None:
        # 더블클릭 시 텍스트 전체 선택 (연도 증감 차단)
        le = self.lineEdit()
        if le:
            le.selectAll()
        event.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        if event.type() == QEvent.Type.MouseButtonDblClick:
            le = self.lineEdit()
            if le:
                le.selectAll()
            event.accept()
            return True
        return super().eventFilter(watched, event)


HEADER_BUTTON_STYLE = """
    QPushButton {
        background-color: #FFFFFF;
        color: #1F2328;
        border: 1px solid #CBD5E0;
        border-radius: 6px;
        padding: 0 12px;
        font-size: 12px;
    }
    QPushButton:hover {
        background-color: #F6F8FB;
        border-color: #A0AEC0;
    }
"""

PRIMARY_BUTTON_STYLE = """
    QPushButton {
        background-color: #1F7A67;
        color: #FFFFFF;
        border: 1px solid #1F7A67;
        border-radius: 6px;
        padding: 0 12px;
        font-size: 12px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #185F50;
        border-color: #185F50;
    }
    QPushButton:pressed {
        background-color: #144E42;
        border-color: #144E42;
    }
"""

DEFAULT_CATEGORIES = ["일반", "공문", "보고", "기안", "결재", "민원", "예산", "기타"]
DEFAULT_STATUSES = ["등록", "진행중", "완료", "보류", "취소"]


def get_task_categories(repo: EncryptedRepository) -> list[str]:
    cats = list(DEFAULT_CATEGORIES)
    saved = repo.get_setting("custom_task_categories")
    if saved:
        try:
            loaded = json.loads(saved)
            if isinstance(loaded, list) and loaded:
                cats = loaded
        except Exception:
            pass
    # '일반'은 항상 최상단 0번에 고정
    cats = [c for c in cats if c != "일반"]
    return ["일반"] + cats


def save_task_categories(repo: EncryptedRepository, cats: list[str]) -> None:
    # '일반'은 항상 최상단 0번에 고정하여 저장
    clean_cats = [c for c in cats if c != "일반"]
    final_cats = ["일반"] + clean_cats
    repo.set_setting("custom_task_categories", json.dumps(final_cats, ensure_ascii=False))
    repo.save()


def get_task_statuses(repo: EncryptedRepository) -> list[str]:
    statuses = list(DEFAULT_STATUSES)
    saved = repo.get_setting("custom_task_statuses")
    if saved:
        try:
            loaded = json.loads(saved)
            if isinstance(loaded, list) and loaded:
                statuses = loaded
        except Exception:
            pass
    # '등록'은 항상 최상단 0번에 고정
    statuses = [s for s in statuses if s != "등록"]
    return ["등록"] + statuses


def save_task_statuses(repo: EncryptedRepository, statuses: list[str]) -> None:
    # '등록'은 항상 최상단 0번에 고정하여 저장
    clean_statuses = [s for s in statuses if s != "등록"]
    final_statuses = ["등록"] + clean_statuses
    repo.set_setting("custom_task_statuses", json.dumps(final_statuses, ensure_ascii=False))

    # 캘린더 업무 표시 상태 설정에서도 삭제된 상태를 자동으로 제거
    cal_saved = repo.get_setting("calendar_task_statuses")
    if cal_saved:
        try:
            cal_statuses = json.loads(cal_saved)
            if isinstance(cal_statuses, list):
                pruned = [s for s in cal_statuses if s in final_statuses]
                if pruned != cal_statuses:
                    repo.set_setting("calendar_task_statuses", json.dumps(pruned, ensure_ascii=False))
        except Exception:
            pass

    repo.save()


def get_calendar_task_statuses(repo: EncryptedRepository) -> list[str]:
    all_statuses = get_task_statuses(repo)
    saved = repo.get_setting("calendar_task_statuses")
    if saved is not None:
        try:
            loaded = json.loads(saved)
            if isinstance(loaded, list):
                # 현재 존재하는 상태만 유지 (삭제된 상태는 자동 제외)
                return [s for s in loaded if s in all_statuses]
        except Exception:
            pass
    # 명시적으로 설정되지 않은 초기 기본값: '완료', '취소'를 제외한 활성 상태
    default_active = [s for s in all_statuses if s not in ("완료", "취소")]
    return default_active if default_active else all_statuses


STATUS_CONFIG = {
    "등록": {"bg": "#EBF8FF", "text": "#2B6CB0", "border": "#BEE3F8", "icon": "🔵"},
    "진행중": {"bg": "#FFFAF0", "text": "#C05621", "border": "#FEEBC8", "icon": "🟠"},
    "완료": {"bg": "#F0FFF4", "text": "#22543D", "border": "#C6F6D5", "icon": "🟢"},
    "보류": {"bg": "#FAF5FF", "text": "#6B46C1", "border": "#E9D8FD", "icon": "🟣"},
    "취소": {"bg": "#FFF5F5", "text": "#C53030", "border": "#FED7D7", "icon": "⚪"},
}

STATUS_CONFIG_DARK = {
    "등록": {"bg": "#1E3A5F", "text": "#90CDF4", "border": "#2B6CB0", "icon": "🔵"},
    "진행중": {"bg": "#4A2800", "text": "#FBD38D", "border": "#DD6B20", "icon": "🟠"},
    "완료": {"bg": "#1C4532", "text": "#9AE6B4", "border": "#38A169", "icon": "🟢"},
    "보류": {"bg": "#322659", "text": "#D6BCFA", "border": "#805AD5", "icon": "🟣"},
    "취소": {"bg": "#4A1D24", "text": "#FEB2B2", "border": "#E53E3E", "icon": "⚪"},
}

CATEGORY_CONFIG = {
    "일반": {"bg": "#F1F5F9", "text": "#475569", "border": "#CBD5E1"},
    "공문": {"bg": "#EFF6FF", "text": "#1D4ED8", "border": "#BFDBFE"},
    "보고": {"bg": "#F5F3FF", "text": "#6D28D9", "border": "#DDD6FE"},
    "기안": {"bg": "#ECFDF5", "text": "#047857", "border": "#A7F3D0"},
    "결재": {"bg": "#FFFBEB", "text": "#B45309", "border": "#FDE68A"},
    "민원": {"bg": "#FEF2F2", "text": "#B91C1C", "border": "#FECACA"},
    "예산": {"bg": "#F0FDF4", "text": "#15803D", "border": "#BBF7D0"},
    "기타": {"bg": "#F8FAFC", "text": "#64748B", "border": "#E2E8F0"},
}

CATEGORY_CONFIG_DARK = {
    "일반": {"bg": "#2D3748", "text": "#CBD5E0", "border": "#4A5568"},
    "공문": {"bg": "#1E3A8A", "text": "#93C5FD", "border": "#2563EB"},
    "보고": {"bg": "#4C1D95", "text": "#C4B5FD", "border": "#7C3AED"},
    "기안": {"bg": "#064E3B", "text": "#6EE7B7", "border": "#059669"},
    "결재": {"bg": "#78350F", "text": "#FCD34D", "border": "#D97706"},
    "민원": {"bg": "#7F1D1D", "text": "#FCA5A5", "border": "#DC2626"},
    "예산": {"bg": "#14532D", "text": "#86EFAC", "border": "#16A34A"},
    "기타": {"bg": "#1F2937", "text": "#9CA3AF", "border": "#374151"},
}


def split_doc_source(description: str) -> tuple[str, str]:
    """문서 비고에서 끝에 붙은 '출처: URL' 을 떼어 (본문, URL) 로 돌려준다."""
    desc = description or ""
    for marker in ("📎 출처:", "\n출처:", "출처:"):
        if marker in desc:
            body, url = desc.rsplit(marker, 1)
            return body.strip(), url.strip()
    return desc.strip(), ""


def join_doc_source(body: str, url: str) -> str:
    body = (body or "").strip()
    url = (url or "").strip()
    return f"{body}\n\n출처: {url}".strip() if url else body


# 목록 셀에 그릴 꼬리표·회신기한 정보를 담는 데이터 역할
DOC_TAGS_ROLE = Qt.UserRole + 41
DOC_REPLY_ROLE = Qt.UserRole + 42

# 회신기한 단계별 색: (글자, 배경)
REPLY_LEVEL_COLORS = {
    "over": ("#B42318", "#FDE8E6"),
    "today": ("#B54708", "#FDEBD2"),
    "soon": ("#B54708", "#FBF1CF"),
    "normal": ("#2D3748", "#E4F1EC"),
    "done": ("#A0A6AE", "#EEEFF1"),
}
REPLY_LEVEL_COLORS_DARK = {
    "over": ("#FF9C92", "#4A2421"),
    "today": ("#FFB46B", "#47331C"),
    "soon": ("#F2D16B", "#3F3819"),
    "normal": ("#7FD4BF", "#1E3A33"),
    "done": ("#8A8F98", "#2A2E35"),
}
DOC_TAG_COLORS = {
    "출처": ("#2F5C9A", "#E6EEF8"),
    "업무": ("#1F6F62", "#E4F1EC"),
}
DOC_TAG_COLORS_DARK = {
    "출처": ("#9DC1F0", "#22324A"),
    "업무": ("#7FD4BF", "#1E3A33"),
}


def reply_due_info(reply_due, reply_time: str, is_done: bool, today: date | None = None) -> tuple[str, str, str]:
    """회신기한 → (D-day 글자, 단계, 툴팁). 기한이 없으면 ('', '', '').

    -3 = 3일 남음, 0 = 오늘까지, +2 = 2일 지남 (숫자만).
    """
    if not reply_due:
        return "", "", ""
    today = today or date.today()
    days = (reply_due - today).days
    full = reply_due.strftime("%Y-%m-%d") + (f" {reply_time}" if reply_time else "")
    dday = "0" if days == 0 else (f"-{days}" if days > 0 else f"+{-days}")
    if is_done:
        return dday, "done", f"회신기한 {full} (처리 완료)"
    if days < 0:
        return dday, "over", f"회신기한 {full} — {-days}일 지났습니다"
    if days == 0:
        return dday, "today", f"회신기한 {full} — 오늘까지입니다"
    if days <= 3:
        return dday, "soon", f"회신기한 {full} — {days}일 남았습니다"
    return dday, "normal", f"회신기한 {full} — {days}일 남았습니다"


_INK_CACHE: dict[tuple[str, str], tuple[int, int]] = {}


def _text_ink_span(font: QFont, text: str) -> tuple[int, int]:
    """글자를 실제로 그려 보고 잉크의 (위, 아래) 를 기준선 기준으로 잰다. 글꼴 힌팅까지 반영됨."""
    key = (font.key(), text)
    if key in _INK_CACHE:
        return _INK_CACHE[key]
    fm = QFontMetrics(font)
    w = max(4, fm.horizontalAdvance(text) + 8)
    h = fm.height() + 8
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor("#000000"))
    base = 4 + fm.ascent()
    p.drawText(QPointF(4, base), text)
    p.end()
    top, bottom = None, None
    for y in range(h):
        for x in range(w):
            if (img.pixel(x, y) >> 24) & 0xFF > 60:
                if top is None:
                    top = y
                bottom = y
                break
    if top is None:
        span = (-fm.ascent(), 0)
    else:
        span = (top - base, bottom + 1 - base)
    _INK_CACHE[key] = span
    return span


def draw_text_centered(painter, fm: QFontMetrics, rect: QRectF, text: str) -> None:
    """실제 그려지는 글자 모양 기준으로 위아래·좌우 여백이 같게 가운데에 그린다."""
    top, bottom = _text_ink_span(painter.font(), text)
    x = rect.center().x() - fm.horizontalAdvance(text) / 2
    baseline = round(rect.center().y() - (top + bottom) / 2)
    painter.drawText(QPointF(x, baseline), text)


class RowHoverDelegate(QStyledItemDelegate):
    """테이블 마우스 오버 시 1줄 전체 배경색을 균일하게 칠해주는 델리게이트.

    문서 목록에서는 제목 옆 꼬리표(출처·업무)와 회신기한 알약도 여기서 그린다.
    """

    TAG_H = 18
    TAG_GAP = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.hovered_row: int = -1
        self.hover_color: QColor = QColor("#F8FAFC")
        self.dark = False

    def _tag_width(self, fm: QFontMetrics, text: str) -> int:
        return fm.horizontalAdvance(text) + 12

    def paint(self, painter, option, index) -> None:
        if index.row() == self.hovered_row and not (option.state & QStyle.State_Selected):
            painter.fillRect(option.rect, self.hover_color)

        reply = index.data(DOC_REPLY_ROLE)
        if reply:
            self._paint_reply(painter, option, index, reply)
            return

        tags = index.data(DOC_TAGS_ROLE)
        if not tags:
            super().paint(painter, option, index)
            return

        font = QFont(option.font)
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        font.setBold(False)
        fm = QFontMetrics(font)
        widths = [self._tag_width(fm, t) for t in tags]
        tags_w = sum(widths) + self.TAG_GAP * len(tags) + 4

        # 글자는 꼬리표 자리를 비워 두고 그린다 (넘치면 … 으로 줄임)
        text_opt = QStyleOptionViewItem(option)
        text_opt.rect = option.rect.adjusted(0, 0, -min(tags_w, max(0, option.rect.width() - 60)), 0)
        super().paint(painter, text_opt, index)

        colors = DOC_TAG_COLORS_DARK if self.dark else DOC_TAG_COLORS
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(font)
        x = option.rect.right() - tags_w + self.TAG_GAP
        y = option.rect.center().y() - self.TAG_H // 2
        for text, w in zip(tags, widths):
            key = "업무" if text.startswith("업무") else "출처"
            fg, bg = colors[key]
            rect = QRectF(x, y, w, self.TAG_H)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(rect, self.TAG_H / 2, self.TAG_H / 2)
            painter.setPen(QColor(fg))
            draw_text_centered(painter, fm, rect, text)
            x += w + self.TAG_GAP
        painter.restore()

    def _paint_reply(self, painter, option, index, reply) -> None:
        text, level = reply
        bg_opt = QStyleOptionViewItem(option)
        self.initStyleOption(bg_opt, index)
        bg_opt.text = ""
        style = bg_opt.widget.style() if bg_opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, bg_opt, painter, bg_opt.widget)

        # 배경 없이 글자색만 (굵게 하지 않음)
        fg, _bg = (REPLY_LEVEL_COLORS_DARK if self.dark else REPLY_LEVEL_COLORS).get(level, ("#475569", "#F1F5F9"))
        painter.save()
        painter.setFont(option.font)
        painter.setPen(QColor(fg))
        painter.drawText(option.rect, Qt.AlignCenter, text)
        painter.restore()


class PillBadgeWidget(QWidget):
    """알약(Pill) 형태의 깔끔한 뱃지 버튼 위젯 (단일 클릭 드롭다운 메뉴 연결).

    plain_color 를 주면 배경·테두리·색 없이 그 글자색의 평범한 글자로 보인다 (상태 열).
    """

    def __init__(
        self,
        text: str,
        config: dict,
        on_click,
        on_context=None,
        parent: QWidget | None = None,
        plain_color: str | None = None,
        plain_hover: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.on_click = on_click
        self.on_context = on_context
        self.plain_color = plain_color
        self.plain_hover = plain_hover or "#CBD5E1"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setAlignment(Qt.AlignCenter)

        self.btn = QPushButton()
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setFixedHeight(24)
        self.btn.clicked.connect(lambda: self.on_click(self.btn))
        if self.on_context:
            self.btn.setContextMenuPolicy(Qt.CustomContextMenu)
            self.btn.customContextMenuRequested.connect(lambda p: self.on_context(QCursor.pos()))
        layout.addWidget(self.btn)
        self.update_badge(text, config)

    def update_badge(self, text: str, config: dict) -> None:
        """전체 테이블 재빌드 없이 해당 셀의 뱃지 텍스트와 색상만 즉시 갱신"""
        self.btn.setText(f"{text}  ▾")
        if self.plain_color:
            self.btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {self.plain_color};
                    border: 1px solid transparent;
                    border-radius: 6px;
                    padding: 0 8px;
                    font-family: {font_family_css()};
                    font-size: 12px;
                    font-weight: normal;
                }}
                QPushButton:hover {{ border-color: {self.plain_hover}; }}
            """)
            return
        cfg = config.get(text, {"bg": "#F1F5F9", "text": "#475569", "border": "#CBD5E1"})
        bg = cfg.get("bg", "#F1F5F9")
        color = cfg.get("text", "#475569")
        border = cfg.get("border", "#CBD5E1")
        self.btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: 1px solid {border};
                border-radius: 12px;
                padding: 0 10px;
                font-family: {font_family_css()};
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {color};
                background-color: #FFFFFF;
            }}
        """)


class SimpleListManagerDialog(QDialog):
    """항목(분류, 상태 등)을 추가/삭제/관리하는 모달 창"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        items: list[str],
        on_save_callback,
        immutable_item: str = "",
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        from taskcalendar.ui_common import style_button

        self.palette = palette or get_dialog_palette(parent)
        self.setWindowTitle(title)
        self.setFixedSize(360, 440)
        self.immutable_item = immutable_item
        # 기본 고정 항목은 항상 맨 앞에 위치
        if self.immutable_item:
            self.items = [self.immutable_item] + [x for x in items if x != self.immutable_item]
        else:
            self.items = list(items)
        self.on_save_callback = on_save_callback

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#718096")
        line = self.palette.get("line", "#CBD5E0")
        line_soft = self.palette.get("line_soft", line)
        accent = self.palette.get("accent", "#1F7A67")
        accent_soft = self.palette.get("accent_soft", panel_alt)

        # 캘린더·메모 화면과 같은 공통 모양: 패널 바탕, 12px 글씨, 30px 입력칸·버튼, 6~8px 모서리
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {panel};
                color: {text};
                font-family: {font_family_css()};
            }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#listManagerTitle {{ color: {text}; font-size: 15px; font-weight: 700; }}
            QLabel#listManagerNote {{ color: {muted}; font-size: 12px; }}
            QListWidget {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line_soft};
                border-radius: 8px;
                padding: 4px;
                font-size: 12px;
                outline: 0;
                font-family: {font_family_css()};
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-radius: 6px;
                margin: 1px 0;
            }}
            QListWidget::item:hover {{ background-color: {panel_alt}; }}
            QListWidget::item:selected {{
                background-color: {accent_soft};
                color: {accent};
            }}
            QLineEdit {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 12px;
                font-family: {font_family_css()};
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("listManagerTitle")
        layout.addWidget(lbl_title)
        if self.immutable_item:
            lbl_note = QLabel(f"'{self.immutable_item}'은(는) 기본 항목이라 수정·삭제할 수 없습니다.")
            lbl_note.setObjectName("listManagerNote")
            lbl_note.setWordWrap(True)
            layout.addWidget(lbl_note)

        self.list_widget = QListWidget()
        # 윈도우11 기본 스타일은 선택 줄에 밑줄 같은 초점 표시를 덧그리므로 Fusion 으로 그린다
        from taskcalendar.ui_common import _menu_base_style

        if _menu_base_style() is not None:
            self.list_widget.setStyle(_menu_base_style())
        for item in self.items:
            display_text = f"{item}  ·  기본 고정" if item == self.immutable_item else item
            self.list_widget.addItem(display_text)
        if self.immutable_item and self.list_widget.count():
            fixed = self.list_widget.item(0)
            fixed.setForeground(QColor(muted))
        layout.addWidget(self.list_widget, 1)

        # 추가 입력칸 + [추가]
        add_layout = QHBoxLayout()
        add_layout.setSpacing(6)
        self.new_item_input = QLineEdit()
        self.new_item_input.setPlaceholderText("새 항목 이름 (Enter)")
        self.new_item_input.setFixedHeight(30)
        self.new_item_input.returnPressed.connect(self._add_item)
        add_layout.addWidget(self.new_item_input, 1)
        btn_add = QPushButton("추가")
        style_button(btn_add, self.palette, "secondary", "M")
        btn_add.clicked.connect(self._add_item)
        add_layout.addWidget(btn_add)
        layout.addLayout(add_layout)

        # 하단: [선택 항목 삭제] ······ [취소] [저장]
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_del = QPushButton("선택 항목 삭제")
        style_button(btn_del, self.palette, "ghost", "M")
        btn_del.clicked.connect(self._delete_item)
        btn_layout.addWidget(btn_del)
        btn_layout.addStretch(1)
        btn_cancel = QPushButton("취소")
        style_button(btn_cancel, self.palette, "secondary", "M")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_ok = QPushButton("저장")
        style_button(btn_ok, self.palette, "primary", "M")
        btn_ok.clicked.connect(self._save_and_close)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _add_item(self) -> None:
        text = self.new_item_input.text().strip()
        if not text:
            return
        if text in self.items:
            QMessageBox.information(self, "안내", "이미 존재하는 항목입니다.")
            return
        self.items.append(text)
        self.list_widget.addItem(text)
        self.new_item_input.clear()

    def _delete_item(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.items):
            return
        if self.items[row] == self.immutable_item:
            QMessageBox.warning(self, "삭제 불가", f"기본 항목 '{self.immutable_item}'은(는) 삭제할 수 없습니다.")
            return
        del self.items[row]
        self.list_widget.takeItem(row)

    def _save_and_close(self) -> None:
        if not self.items:
            QMessageBox.warning(self, "경고", "최소 하나 이상의 항목이 필요합니다.")
            return
        self.on_save_callback(self.items)
        self.accept()


class TaskEditDialog(QDialog):
    """업무 전용 등록 및 수정 대화상자 (기안자 자동완성, 분류/상태 관리 포함)"""

    def __init__(
        self,
        parent: QWidget | None,
        repository: EncryptedRepository,
        task: CalendarEntry | None = None,
        known_authors: list[str] | None = None,
        palette: dict[str, str] | None = None,
        known_depts: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.palette = palette or get_dialog_palette(parent, repository)
        self.repository = repository
        self.task = task
        self.known_authors = known_authors or []
        self.known_depts = known_depts or []
        self._linked_work_ids: list[int] = []

        is_new = (task is None or task.entry_id is None)
        self.setWindowTitle("새 문서 등록" if is_new else "문서 정보 수정")
        self.resize(640, 630)
        # font-family 를 명시해야 전역 폰트(Pretendard)가 상속되어 글자가 깨지지 않는다.
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.palette['bg']};
                color: {self.palette['text']};
                font-family: {font_family_css()};
            }}
            QLineEdit, QComboBox, QTextEdit, QPushButton, QLabel,
            QTableWidget, QListWidget, QCheckBox {{
                font-family: {font_family_css()};
            }}
        """)

        self._init_ui()
        self._load_data()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        accent_soft = self.palette.get("accent_soft", panel_alt)
        btn_text = self.palette.get("button_text", "#FFFFFF")

        lbl_style = f"font-size: 12px; font-weight: bold; color: {text};"
        arrow_svg = str(asset_path("chevron_down.svg")).replace("\\", "/")

        combo_style = f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 24px 4px 10px;
                font-size: 12px;
                background-color: {panel};
                color: {text};
            }}
            QComboBox:hover, QComboBox:focus {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 22px;
                border-left: 1px solid {line};
            }}
            QComboBox::down-arrow {{
                image: url('{arrow_svg}');
                width: 9px;
                height: 6px;
                margin-right: 2px;
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                selection-background-color: {accent_soft};
                selection-color: {text};
                outline: 0px;
                padding: 4px;
            }}
        """

        sub_btn_style = f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {panel};
                border-color: {accent};
            }}
        """

        input_style = f"""
            QLineEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border-color: {accent};
            }}
        """

        # 1. 업무분류 (0,0~0,1) & 등록일자 (0,2~0,3) 한 줄 배치
        lbl_cat = QLabel("업무분류:")
        lbl_cat.setStyleSheet(lbl_style)
        lbl_cat.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_cat, 0, 0)

        cat_layout = QHBoxLayout()
        cat_layout.setContentsMargins(0, 0, 0, 0)
        cat_layout.setSpacing(4)
        self.category_combo = QComboBox()
        self.category_combo.setFixedHeight(30)
        self.category_combo.setStyleSheet(combo_style)
        self._refresh_categories()
        cat_layout.addWidget(self.category_combo, 1)

        btn_cat_mgr = QPushButton("분류 관리")
        btn_cat_mgr.setFixedHeight(30)
        btn_cat_mgr.setStyleSheet(sub_btn_style)
        btn_cat_mgr.clicked.connect(self._open_category_manager)
        cat_layout.addWidget(btn_cat_mgr)
        grid.addLayout(cat_layout, 0, 1)

        lbl_date = QLabel("등록일자:")
        lbl_date.setStyleSheet(lbl_style)
        lbl_date.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_date, 0, 2)

        date_layout = QHBoxLayout()
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.setSpacing(4)
        self.date_edit = SafeDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setFixedHeight(30)
        self.date_edit.setDate(date.today())
        self.date_edit.setStyleSheet(
            f"QDateEdit {{ background: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 2px 24px 2px 8px; font-size: 13px; }}"
            f"QDateEdit:hover, QDateEdit:focus {{ border-color: {accent}; }}"
            f"QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 22px; border-left: 1px solid {line}; }}"
            f"QDateEdit::down-arrow {{ image: url('{arrow_svg}'); width: 9px; height: 6px; margin-right: 2px; }}"
        )
        date_layout.addWidget(self.date_edit, 1)

        btn_date_today = QPushButton("오늘")
        btn_date_today.setFixedHeight(30)
        btn_date_today.setStyleSheet(sub_btn_style)
        btn_date_today.clicked.connect(lambda: self.date_edit.setDate(date.today()))
        date_layout.addWidget(btn_date_today)
        grid.addLayout(date_layout, 0, 3)

        # 2. 업무 제목 (1,0~1,3 전체 가로 확장)
        lbl_title = QLabel("업무제목:")
        lbl_title.setStyleSheet(lbl_style)
        lbl_title.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_title, 1, 0)

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("온나라 결재문서 제목 또는 업무 명칭")
        self.title_input.setFixedHeight(30)
        self.title_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
                font-weight: bold;
            }}
            QLineEdit:focus {{
                border-color: {accent};
            }}
        """)
        grid.addWidget(self.title_input, 1, 1, 1, 3)

        # 3. 부서 (2,0~2,1) & 기안자 (2,2~2,3) 한 줄 배치
        lbl_dept = QLabel("부서:")
        lbl_dept.setStyleSheet(lbl_style)
        lbl_dept.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_dept, 2, 0)

        self.department_input = QLineEdit()
        self.department_input.setPlaceholderText("부서명 또는 관련근거 (예: 고객과-123)")
        self.department_input.setFixedHeight(30)
        self.department_input.setStyleSheet(input_style)
        self._setup_dept_completer()
        grid.addWidget(self.department_input, 2, 1)

        lbl_author = QLabel("기안자:")
        lbl_author.setStyleSheet(lbl_style)
        lbl_author.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_author, 2, 2)

        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("기안자 성명 (예: 홍길동)")
        self.author_input.setFixedHeight(30)
        self.author_input.setStyleSheet(input_style)
        self._setup_author_completer()
        grid.addWidget(self.author_input, 2, 3)

        # 4. 처리상태 (3,0~3,1)
        lbl_status = QLabel("처리상태:")
        lbl_status.setStyleSheet(lbl_style)
        lbl_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_status, 3, 0)

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)
        self.status_combo = QComboBox()
        self.status_combo.setFixedHeight(30)
        self.status_combo.setStyleSheet(combo_style)
        self._refresh_statuses()
        status_layout.addWidget(self.status_combo, 1)

        btn_status_mgr = QPushButton("상태 관리")
        btn_status_mgr.setFixedHeight(30)
        btn_status_mgr.setStyleSheet(sub_btn_style)
        btn_status_mgr.clicked.connect(self._open_status_manager)
        status_layout.addWidget(btn_status_mgr)
        grid.addLayout(status_layout, 3, 1)

        # 5. 회신기한 (4,0~4,3) — 빠른 기한 버튼 + 캘린더 등록
        lbl_reply = QLabel("회신기한:")
        lbl_reply.setStyleSheet(lbl_style)
        lbl_reply.setAlignment(Qt.AlignRight | Qt.AlignTop)
        grid.addWidget(lbl_reply, 4, 0)

        self._reply_settings = doc_reply.load_settings(self.repository)
        reply_box = QVBoxLayout()
        reply_box.setContentsMargins(0, 0, 0, 0)
        reply_box.setSpacing(4)

        reply_row = QHBoxLayout()
        reply_row.setContentsMargins(0, 0, 0, 0)
        reply_row.setSpacing(4)
        self.reply_check = QCheckBox()
        self.reply_check.setToolTip("회신기한 사용")
        reply_row.addWidget(self.reply_check)

        self.reply_date_edit = SafeDateEdit()
        self.reply_date_edit.setCalendarPopup(True)
        self.reply_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.reply_date_edit.setFixedHeight(30)
        self.reply_date_edit.setMinimumWidth(132)
        self.reply_date_edit.setDate(date.today())
        self.reply_date_edit.setStyleSheet(self.date_edit.styleSheet())
        self.reply_date_edit.setEnabled(False)
        reply_row.addWidget(self.reply_date_edit)

        for label, token in doc_reply.parse_quick_buttons(self._reply_settings["doc_reply_quick_buttons"]):
            btn_quick = QPushButton(label)
            btn_quick.setFixedHeight(30)
            btn_quick.setStyleSheet(sub_btn_style)
            btn_quick.setToolTip("등록일자 기준으로 회신기한을 이 날짜로 정합니다")
            btn_quick.clicked.connect(lambda _=False, t=token: self._apply_reply_quick(t))
            reply_row.addWidget(btn_quick)

        btn_reply_clear = QPushButton("지우기")
        btn_reply_clear.setFixedHeight(30)
        btn_reply_clear.setStyleSheet(sub_btn_style)
        btn_reply_clear.clicked.connect(lambda: self.reply_check.setChecked(False))
        reply_row.addWidget(btn_reply_clear)
        reply_row.addStretch(1)
        reply_box.addLayout(reply_row)

        reply_info_row = QHBoxLayout()
        reply_info_row.setContentsMargins(0, 0, 0, 0)
        reply_info_row.setSpacing(8)
        self.reply_calendar_check = QCheckBox("캘린더에 등록")
        self.reply_calendar_check.setToolTip("회신기한 날짜에 회신 일정을 자동으로 등록하고 이 문서와 연결합니다")
        self.reply_calendar_check.setEnabled(False)
        reply_info_row.addWidget(self.reply_calendar_check)

        self.reply_info_label = QLabel("")
        self.reply_info_label.setStyleSheet(f"color: {self.palette.get('muted', '#64748B')}; font-size: 11px;")
        reply_info_row.addWidget(self.reply_info_label)

        self.btn_reply_prev_workday = QPushButton("직전 근무일로")
        self.btn_reply_prev_workday.setFixedHeight(22)
        self.btn_reply_prev_workday.setStyleSheet(sub_btn_style)
        self.btn_reply_prev_workday.clicked.connect(
            lambda: self.reply_date_edit.setDate(doc_reply.previous_workday(self.reply_date_edit.date().toPython()))
        )
        self.btn_reply_prev_workday.hide()
        reply_info_row.addWidget(self.btn_reply_prev_workday)
        reply_info_row.addStretch(1)
        reply_box.addLayout(reply_info_row)
        grid.addLayout(reply_box, 4, 1, 1, 3)

        self.reply_check.toggled.connect(self._on_reply_toggled)
        self.reply_date_edit.dateChanged.connect(lambda _d: self._update_reply_info())

        # 6. 비고 / 세부내용 (5,0~5,3)
        lbl_desc = QLabel("비고/내용:")
        lbl_desc.setStyleSheet(lbl_style)
        lbl_desc.setAlignment(Qt.AlignRight | Qt.AlignTop)
        grid.addWidget(lbl_desc, 5, 0)

        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("결재 요지, 업무 세부내용, 지시사항 등")
        self.desc_input.setMinimumHeight(140)
        self.desc_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.desc_input.setStyleSheet(f"""
            QTextEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
            QTextEdit:focus {{
                border-color: {accent};
            }}
        """)
        grid.addWidget(self.desc_input, 5, 1, 1, 3)
        grid.setRowStretch(5, 1)

        # 6. 출처 URL (5,0~5,3)
        lbl_url = QLabel("출처 URL:")
        lbl_url.setStyleSheet(lbl_style)
        lbl_url.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_url, 6, 0)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("온나라 문서 링크 또는 웹사이트 URL")
        self.url_input.setFixedHeight(28)
        self.url_input.setStyleSheet(input_style)
        grid.addWidget(self.url_input, 6, 1, 1, 3)

        # 7. 관련 업무 (6,0~6,3) — 업무 관리의 문서와 1:1 로 연결
        lbl_work = QLabel("관련 업무:")
        lbl_work.setStyleSheet(lbl_style)
        lbl_work.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl_work, 7, 0)

        work_row = QHBoxLayout()
        work_row.setSpacing(6)

        self.lbl_linked_works = QLabel("(연결된 업무 없음)")
        self.lbl_linked_works.setStyleSheet(
            f"color: {self.palette.get('muted', '#64748B')}; font-size: 11px;"
        )
        self.lbl_linked_works.setWordWrap(True)
        work_row.addWidget(self.lbl_linked_works, 1)

        self.btn_select_work = QPushButton("업무 선택...")
        self.btn_select_work.setFixedHeight(26)
        self.btn_select_work.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_work.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 0 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        self.btn_select_work.clicked.connect(self._open_work_link_dialog)
        work_row.addWidget(self.btn_select_work)
        grid.addLayout(work_row, 7, 1, 1, 3)

        layout.addLayout(grid, 1)

        # 하단 확인 / 취소 버튼
        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)
        btn_box.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(28)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("저장")
        btn_save.setFixedHeight(28)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.12)};
                border-color: {_shade(accent, -0.12)};
            }}
            QPushButton:pressed {{
                background-color: {_shade(accent, -0.20)};
                border-color: {_shade(accent, -0.20)};
            }}
        """)
        btn_save.clicked.connect(self._on_save)
        btn_box.addWidget(btn_save)

        layout.addLayout(btn_box)

    def _on_reply_toggled(self, checked: bool) -> None:
        self.reply_date_edit.setEnabled(checked)
        self.reply_calendar_check.setEnabled(checked)
        if checked and not getattr(self, "_reply_loading", False):
            # 기한을 새로 넣는 경우에만 설정의 '자동 등록' 기본값을 따른다.
            self.reply_calendar_check.setChecked(self._reply_settings["doc_reply_auto_calendar"] != "0")
        self._update_reply_info()

    def _apply_reply_quick(self, token: str) -> None:
        """빠른 기한 버튼: 등록일자로부터 그 기간 뒤의 날짜를 회신기한으로 정한다 (당일, 7일 뒤, 1달 뒤 …)."""
        base = self.date_edit.date().toPython()
        self.reply_date_edit.setDate(doc_reply.add_period(base, token))
        self.reply_check.setChecked(True)
        self._update_reply_info()

    def _update_reply_info(self) -> None:
        if not self.reply_check.isChecked():
            self.reply_info_label.setText("")
            self.btn_reply_prev_workday.hide()
            return
        due = self.reply_date_edit.date().toPython()
        weekday = "월화수목금토일"[due.weekday()]
        text = f"{due.month}/{due.day}({weekday}) · {doc_reply.dday_text(due)}"
        holiday = doc_reply.holiday_name(due)
        if holiday:
            text += f" · ⚠ {holiday}(휴일)"
        self.reply_info_label.setText(text)
        self.btn_reply_prev_workday.setVisible(bool(holiday))

    def _setup_dept_completer(self) -> None:
        """기존 부서 목록으로 자동완성 세팅"""
        clean_depts = sorted(list({d.strip() for d in self.known_depts if d and d.strip()}))
        completer = QCompleter(clean_depts, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.department_input.setCompleter(completer)

    def _setup_author_completer(self) -> None:
        """기존 기안자 목록으로 자동완성 세팅"""
        clean_authors = sorted(list({a.strip() for a in self.known_authors if a and a.strip()}))
        completer = QCompleter(clean_authors, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)  # 중간 글자 입력해도 매칭
        self.author_input.setCompleter(completer)

    def _refresh_categories(self) -> None:
        curr = self.category_combo.currentText()
        cats = get_task_categories(self.repository)
        self.category_combo.clear()
        self.category_combo.addItems(cats)
        if curr and curr in cats:
            self.category_combo.setCurrentText(curr)

    def _refresh_statuses(self) -> None:
        curr = self.status_combo.currentText()
        statuses = get_task_statuses(self.repository)
        self.status_combo.clear()
        self.status_combo.addItems(statuses)
        if curr and curr in statuses:
            self.status_combo.setCurrentText(curr)

    def _open_work_link_dialog(self) -> None:
        """관련 업무 선택 다이얼로그를 열고 선택 결과를 반영한다."""
        from taskcalendar.work_link_dialog import WorkLinkSelectDialog

        dlg = WorkLinkSelectDialog(
            self,
            self.repository,
            initial_work_ids=list(self._linked_work_ids),
            palette=self.palette,
            title="문서와 연결할 업무 선택",
        )
        if dlg.exec() == QDialog.Accepted:
            self._linked_work_ids = list(dlg.selected_work_ids)
            self._update_linked_works_summary()

    def _update_linked_works_summary(self) -> None:
        """연결된 업무 목록을 라벨에 요약 표시한다."""
        work_ids = list(self._linked_work_ids)
        if not work_ids:
            self.lbl_linked_works.setText("(연결된 업무 없음)")
            return

        titles: list[str] = []
        try:
            for wid in work_ids:
                row = self.repository.connection.execute(
                    "SELECT title FROM work_items WHERE id = ?", (int(wid),)
                ).fetchone()
                if row and row["title"]:
                    titles.append(str(row["title"]))
        except Exception:
            titles = [f"업무 #{wid}" for wid in work_ids]

        if not titles:
            self.lbl_linked_works.setText("(연결된 업무 없음)")
        elif len(titles) <= 2:
            self.lbl_linked_works.setText(f"{len(titles)}개 연결됨: {', '.join(titles)}")
        else:
            self.lbl_linked_works.setText(
                f"{len(titles)}개 연결됨: {titles[0]}, {titles[1]} 외 {len(titles) - 2}개"
            )

    def _open_category_manager(self) -> None:
        cats = get_task_categories(self.repository)
        dlg = SimpleListManagerDialog(
            self,
            "업무 분류 관리",
            cats,
            lambda new_cats: save_task_categories(self.repository, new_cats),
            immutable_item="일반",
            palette=self.palette,
        )
        if dlg.exec() == QDialog.Accepted:
            self._refresh_categories()

    def _open_status_manager(self) -> None:
        statuses = get_task_statuses(self.repository)
        dlg = SimpleListManagerDialog(
            self,
            "처리 상태 관리",
            statuses,
            lambda new_statuses: save_task_statuses(self.repository, new_statuses),
            immutable_item="등록",
            palette=self.palette,
        )
        if dlg.exec() == QDialog.Accepted:
            self._refresh_statuses()
            parent = self.parent()
            if hasattr(parent, "main_window") and getattr(parent, "main_window", None) and hasattr(parent.main_window, "refresh"):
                parent.main_window.refresh()
            elif hasattr(parent, "refresh"):
                parent.refresh()

    def _load_data(self) -> None:
        if not self.task:
            self.category_combo.setCurrentText("일반")
            self.status_combo.setCurrentText("등록")
            self._update_linked_works_summary()
            return

        load_date = self.task.day or self.task.start_date or (self.task.created_at.date() if self.task.created_at else date.today())
        self.date_edit.setDate(load_date)
        self.title_input.setText(self.task.title or "")
        self.department_input.setText(getattr(self.task, "department", "") or "")
        self.author_input.setText(self.task.assignee or "")

        cat = self.task.memo_group or "일반"
        idx = self.category_combo.findText(cat)
        if idx >= 0:
            self.category_combo.setCurrentIndex(idx)
        else:
            self.category_combo.addItem(cat)
            self.category_combo.setCurrentText(cat)

        st = self.task.status or "등록"
        s_idx = self.status_combo.findText(st)
        if s_idx >= 0:
            self.status_combo.setCurrentIndex(s_idx)
        else:
            self.status_combo.addItem(st)
            self.status_combo.setCurrentText(st)

        # 출처 URL과 설명 분리 파싱
        desc, url = split_doc_source(self.task.description or "")

        self.desc_input.setPlainText(desc)
        self.url_input.setText(url)

        # 회신기한: 기존 문서는 실제 회신 일정 유무를, 새 문서는 설정의 기본값을 따른다.
        reply_due = getattr(self.task, "reply_due", None)
        if reply_due:
            self._reply_loading = True
            self.reply_date_edit.setDate(reply_due)
            self.reply_check.setChecked(True)
            self._reply_loading = False
            if self.task.entry_id:
                self.reply_calendar_check.setChecked(
                    self.repository.get_reply_schedule(self.task.entry_id) is not None
                )
            else:
                self.reply_calendar_check.setChecked(self._reply_settings["doc_reply_auto_calendar"] != "0")
            self._update_reply_info()

        # 연결된 업무 목록 로드
        if self.task.entry_id:
            try:
                self._linked_work_ids = list(
                    self.repository.list_linked_works_for_entry(self.task.entry_id)
                )
            except Exception:
                self._linked_work_ids = []
        self._update_linked_works_summary()

    def _on_save(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "입력 확인", "업무 제목을 입력해 주세요.")
            self.title_input.setFocus()
            return

        target_date = self.date_edit.date().toPython()
        category = self.category_combo.currentText().strip() or "일반"
        department = self.department_input.text().strip()
        author = self.author_input.text().strip()
        status = self.status_combo.currentText().strip() or "등록"
        desc = self.desc_input.toPlainText().strip()
        url = self.url_input.text().strip()

        full_desc = desc
        if url:
            full_desc = f"{desc}\n\n출처: {url}".strip()

        reply_due = self.reply_date_edit.date().toPython() if self.reply_check.isChecked() else None
        if reply_due and reply_due < target_date:
            answer = QMessageBox.question(
                self,
                "회신기한 확인",
                f"회신기한({reply_due})이 등록일자({target_date})보다 빠릅니다.\n그대로 저장할까요?",
            )
            if answer != QMessageBox.Yes:
                return

        now = datetime.now()
        if not self.task:
            self.task = CalendarEntry(
                entry_type=EntryType.TASK,
                title=title,
                description=full_desc,
                day=target_date,
                start_date=target_date,
                assignee=author,
                department=department,
                memo_group=category,
                status=status,
                created_at=datetime.combine(target_date, now.time()),
            )
        else:
            self.task.entry_type = EntryType.TASK
            self.task.title = title
            self.task.description = full_desc
            self.task.day = target_date
            self.task.start_date = target_date
            if self.task.created_at:
                self.task.created_at = datetime.combine(target_date, self.task.created_at.time())
            else:
                self.task.created_at = datetime.combine(target_date, now.time())
            self.task.assignee = author
            self.task.department = department
            self.task.memo_group = category
            self.task.status = status
            self.task.updated_at = now

        if not reply_due:
            self.task.reply_time = ""
        self.task.reply_due = reply_due
        self.repository.upsert_entry(self.task)

        # 회신 일정: 체크돼 있으면 만들거나 맞추고, 아니면 지운다.
        try:
            self.repository.set_reply_schedule(
                self.task, enabled=bool(reply_due) and self.reply_calendar_check.isChecked()
            )
            doc_reply.mark_passed_as_shown(self.repository, self.task)
        except Exception:
            logger.warning("Failed to sync reply schedule", exc_info=True)

        # 연결된 업무 반영 (문서 → 업무)
        if self.task.entry_id:
            try:
                self.repository.set_entry_work_links(
                    self.task.entry_id, list(self._linked_work_ids)
                )
            except Exception:
                logger.warning("Failed to save work links", exc_info=True)

        self.repository.save()
        self.accept()


class TaskManagerDialog(QDialog):
    """
    온나라 전자결재 및 웹 업무를 엑셀 표 스타일로 한눈에 조회/관리/검색하고
    엑셀(.xlsx)로 내보내는 업무 관리 대시보드 창
    """

    # 기간 빠른 선택 버튼: (버튼 라벨, 기간 콤보 항목)
    QUICK_PERIODS = [
        ("오늘", "오늘"),
        ("7일", "최근 7일"),
        ("30일", "최근 30일"),
        ("3개월", "최근 3개월"),
        ("6개월", "최근 6개월"),
        ("1년", "최근 1년"),
        ("전체", "기간"),
    ]
    COL_CHECK = 8  # 체크박스 열 (논리 인덱스는 맨 뒤, 화면에서는 맨 왼쪽으로 이동)
    COL_REPLY = 9  # 회신기한 날짜 열 (논리 인덱스는 맨 뒤, 화면에서는 상태 옆, 직접 수정 가능)
    COL_DDAY = 10  # 회신기한 D-day 열 (회신기한 바로 옆)

    def __init__(self, parent: QWidget | None, repository: EncryptedRepository, main_window=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.main_window = main_window
        self.palette = get_dialog_palette(parent, repository, main_window)

        self.setWindowTitle("바로업무 - 문서")
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        self.resize(1100, 700)
        self.setMinimumSize(850, 520)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._all_tasks: list[CalendarEntry] = []
        self._filtered_tasks: list[CalendarEntry] = []
        self._displayed_tasks: list[CalendarEntry] = []
        self._current_tab = "all"
        self._current_page = 1
        self._current_date_anchor = None
        self._checked_ids: set = set()  # 좌측 체크박스로 체크한 문서 ID (일괄 처리 대상)

        self._init_ui()
        self.reload_tasks()

    def _init_ui(self) -> None:
        # 바깥 레이아웃: 맨 위 띠는 창 끝까지 붙이고, 나머지 내용은 안쪽 레이아웃(기존 여백)에 둔다.
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        layout = QVBoxLayout()
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        # 1. 맨 윗줄: 세 창 공통 전환 줄 [캘린더 | 문서 | 업무] ······ [등록] [분류·상태] [엑셀저장] [기능] [설정]
        from taskcalendar.ui_common import WindowSwitcherBar

        mw = self.main_window
        self.window_switcher = WindowSwitcherBar(
            self.palette,
            "doc",
            mw.switch_to_window if mw is not None and hasattr(mw, "switch_to_window") else (lambda _n: None),
            getattr(mw, "show_service_menu_at", None),
            getattr(mw, "_open_settings", None),
        )

        self.btn_add = QPushButton("＋ 문서 등록")
        self.btn_add.clicked.connect(self._on_add_task)
        self.window_switcher.add_action(self.btn_add, "primary")

        # 앱 전체 [설정] 과 헷갈리지 않도록 이름을 '분류·상태' 로 바꾼다 (분류·상태 목록 관리 메뉴)
        self.btn_manage_meta = QPushButton("분류·상태")
        self.btn_manage_meta.setToolTip("문서 분류와 처리상태 목록 관리")
        self.btn_manage_meta.clicked.connect(self._open_meta_manager_menu)
        self.window_switcher.add_action(self.btn_manage_meta)

        self.btn_export = QPushButton("엑셀저장")
        self.btn_export.clicked.connect(self._export_to_excel)
        self.window_switcher.add_action(self.btn_export)

        root_layout.addWidget(self.window_switcher)
        root_layout.addLayout(layout, 1)

        # 2. 검색 및 필터 툴바 카드 (분류, 상태, 기간(시작일~종료일), 제목+내용 검색 순서)
        self.toolbar_card = QFrame()
        self.toolbar_card.setObjectName("toolbar_card")
        tb_outer = QVBoxLayout(self.toolbar_card)
        tb_outer.setContentsMargins(0, 8, 0, 8)  # 좌우 시작점을 아래 목록과 맞춤
        tb_outer.setSpacing(6)
        tb_layout = QHBoxLayout()
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(10)
        tb_outer.addLayout(tb_layout)

        # (1) 분류
        self.category_combo = QComboBox()
        self.category_combo.setFixedHeight(30)
        self.category_combo.setFixedWidth(85)
        if self.category_combo.view():
            self.category_combo.view().setMinimumWidth(100)
        self._refresh_filter_categories()
        self.category_combo.currentIndexChanged.connect(self._apply_filters)
        self.category_combo.activated.connect(self._apply_filters)
        tb_layout.addWidget(self.category_combo)

        # (2) 상태
        self.status_combo = QComboBox()
        self.status_combo.setFixedHeight(30)
        self.status_combo.setFixedWidth(85)
        if self.status_combo.view():
            self.status_combo.view().setMinimumWidth(90)
        self._refresh_filter_statuses()
        self.status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        self.status_combo.activated.connect(self._on_status_combo_changed)
        tb_layout.addWidget(self.status_combo)
        self._tb_layout = tb_layout

        # (3) 기간: [기간 ▾] 버튼 하나. 누르면 빠른 기간 칩 + 직접 지정(시작일 ~ 종료일) 팝업
        self.btn_period = QPushButton("기간: 전체 ▾")
        self.btn_period.setFixedHeight(30)
        self.btn_period.setMinimumWidth(150)
        self.btn_period.setCursor(Qt.PointingHandCursor)
        self.btn_period.setToolTip("조회 기간 (오늘·최근 7일·…·전체, 또는 시작일~종료일 직접 지정)")
        self.btn_period.clicked.connect(self._show_period_popup)
        tb_layout.addWidget(self.btn_period)

        self.period_popup = QFrame(self, Qt.Popup)
        self.period_popup.setObjectName("periodPopup")
        _pp = QVBoxLayout(self.period_popup)
        _pp.setContentsMargins(12, 10, 12, 12)
        _pp.setSpacing(8)
        self._period_popup_layout = _pp
        self.period_combo = QComboBox()
        self.period_combo.setFixedHeight(30)
        self.period_combo.setFixedWidth(100)
        if self.period_combo.view():
            self.period_combo.view().setMinimumWidth(110)
        self.period_combo.addItems([
            "기간", "오늘", "최근 7일", "최근 30일", "이번 주", "이번 달",
            "최근 3개월", "최근 6개월", "최근 1년", "올해", "직접 지정",
        ])
        self.period_combo.currentIndexChanged.connect(self._on_period_combo_changed)
        self.period_combo.activated.connect(self._on_period_combo_changed)
        # 기간 선택은 아래 빠른 기간 칩(오늘·7일·…·전체)이 대신한다. 칩이 이 값을 바꿔 필터에 쓰므로 객체는 남기고 화면에서만 뺀다.
        self.period_combo.hide()

        self.start_date_edit = SafeDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_date_edit.setFixedHeight(30)
        self.start_date_edit.setFixedWidth(125)
        self.start_date_edit.setDate(date.today() - timedelta(days=30))
        self.start_date_edit.setEnabled(True)
        self.start_date_edit.dateChanged.connect(self._on_custom_date_changed)
        self.start_date_edit.editingFinished.connect(self._on_custom_date_changed)
        if self.start_date_edit.calendarWidget():
            self.start_date_edit.calendarWidget().clicked.connect(self._on_custom_date_changed)
        self.lbl_tilde = QLabel("~")

        self.end_date_edit = SafeDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_date_edit.setFixedHeight(30)
        self.end_date_edit.setFixedWidth(125)
        self.end_date_edit.setDate(date.today())
        self.end_date_edit.setEnabled(True)
        self.end_date_edit.dateChanged.connect(self._on_custom_date_changed)
        self.end_date_edit.editingFinished.connect(self._on_custom_date_changed)
        if self.end_date_edit.calendarWidget():
            self.end_date_edit.calendarWidget().clicked.connect(self._on_custom_date_changed)

        # (4) 제목+내용 검색: 오른쪽 끝에 적당한 폭으로 (가운데는 비워 둔다)
        tb_layout.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("제목+내용 검색 (Enter)")
        self.search_input.setFixedHeight(30)
        self.search_input.setClearButtonEnabled(True)
        # 캘린더·메모 검색과 같게: Enter 또는 [검색] 버튼으로 실행, 검색어를 지우면 바로 전체
        self._search_query = ""
        self.search_input.returnPressed.connect(self._run_search)
        self.search_input.textChanged.connect(lambda t: self._run_search() if not t.strip() else None)
        self.search_input.setFixedWidth(240)
        tb_layout.addWidget(self.search_input)
        self.btn_search = QPushButton("검색")
        self.btn_search.setFixedHeight(30)
        self.btn_search.setCursor(Qt.PointingHandCursor)
        self.btn_search.clicked.connect(self._run_search)
        tb_layout.addWidget(self.btn_search)

        # (5) 기간 빠른 선택 버튼 (클릭 한 번으로 조회 기간 변경)
        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(4)
        self.lbl_quick_period = QLabel("빠른 기간")  # 화면에는 두지 않음 (스타일 코드 호환용)
        self.lbl_quick_period.hide()
        self._quick_period_buttons: list[tuple[QPushButton, str]] = []
        for label, period in self.QUICK_PERIODS:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            # 선택되면 굵은 글씨가 되므로, 굵은 글씨 기준으로 폭을 고정해 옆 버튼이 밀리지 않게 한다
            _bold = QFont(btn.font())
            _bold.setPixelSize(12)
            _bold.setWeight(QFont.Weight.DemiBold)
            btn.setFixedWidth(max(44, QFontMetrics(_bold).horizontalAdvance(label) + 30))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=period: (self._set_quick_period(p), self.period_popup.hide()))
            quick_row.addWidget(btn)
            self._quick_period_buttons.append((btn, period))
        quick_row.addStretch(1)
        self._lbl_period_quick = QLabel("빠른 선택")
        self._period_popup_layout.addWidget(self._lbl_period_quick)
        self._period_popup_layout.addLayout(quick_row)
        self._lbl_period_custom = QLabel("직접 지정")
        self._period_popup_layout.addWidget(self._lbl_period_custom)
        date_row = QHBoxLayout()
        date_row.setContentsMargins(0, 0, 0, 0)
        date_row.setSpacing(6)
        date_row.addWidget(self.start_date_edit)
        date_row.addWidget(self.lbl_tilde)
        date_row.addWidget(self.end_date_edit)
        date_row.addStretch(1)
        self._period_popup_layout.addLayout(date_row)

        layout.addWidget(self.toolbar_card)

        # 3. 하단 리스트 영역: 탭 바 + 테이블 묶음 (탭과 표 사이 줄간격 0)
        table_container = QVBoxLayout()
        table_container.setContentsMargins(0, 0, 0, 0)
        table_container.setSpacing(0)

        self.tabs_widget = QWidget()
        self.tabs_widget.setFixedHeight(28)
        tabs_bar = QHBoxLayout(self.tabs_widget)
        tabs_bar.setContentsMargins(0, 0, 0, 0)
        tabs_bar.setSpacing(0)

        self.btn_tab_all = QPushButton("전체")
        self.btn_tab_reg = QPushButton("등록")
        self.btn_tab_prog = QPushButton("진행")
        self.btn_tab_done = QPushButton("완료")

        for btn in (self.btn_tab_all, self.btn_tab_reg, self.btn_tab_prog, self.btn_tab_done):
            btn.setCursor(Qt.PointingHandCursor)

        self.btn_tab_all.clicked.connect(lambda: self._set_active_tab("all"))
        self.btn_tab_reg.clicked.connect(lambda: self._set_active_tab("reg"))
        self.btn_tab_prog.clicked.connect(lambda: self._set_active_tab("prog"))
        self.btn_tab_done.clicked.connect(lambda: self._set_active_tab("done"))

        tabs_bar.addWidget(self.btn_tab_all, 0, Qt.AlignTop)
        tabs_bar.addWidget(self.btn_tab_reg, 0, Qt.AlignTop)
        tabs_bar.addWidget(self.btn_tab_prog, 0, Qt.AlignTop)
        tabs_bar.addWidget(self.btn_tab_done, 0, Qt.AlignTop)
        tabs_bar.addStretch(1)

        # 체크한 문서 일괄 처리 바 (체크한 항목이 있을 때만 표시)
        self.bulk_bar = QWidget()
        self.bulk_bar.setFixedHeight(26)
        bulk_layout = QHBoxLayout(self.bulk_bar)
        bulk_layout.setContentsMargins(0, 0, 8, 0)
        bulk_layout.setSpacing(4)
        self.lbl_bulk_count = QLabel("")
        bulk_layout.addWidget(self.lbl_bulk_count)
        self.btn_bulk_done = QPushButton("완료 처리")
        self.btn_bulk_status = QPushButton("상태 ▾")
        self.btn_bulk_category = QPushButton("분류 ▾")
        self.btn_bulk_link = QPushButton("업무 연결")
        self.btn_bulk_delete = QPushButton("삭제")
        self.btn_bulk_clear = QPushButton("선택 해제")
        self.btn_bulk_done.clicked.connect(lambda: self._bulk_change_status("완료"))
        self.btn_bulk_status.clicked.connect(self._show_bulk_status_menu)
        self.btn_bulk_category.clicked.connect(self._show_bulk_category_menu)
        self.btn_bulk_link.clicked.connect(self._bulk_link_work)
        self.btn_bulk_delete.clicked.connect(self._bulk_delete)
        self.btn_bulk_clear.clicked.connect(self._clear_checked)
        for btn in (
            self.btn_bulk_done, self.btn_bulk_status, self.btn_bulk_category,
            self.btn_bulk_link, self.btn_bulk_delete, self.btn_bulk_clear,
        ):
            btn.setFixedHeight(24)
            btn.setCursor(Qt.PointingHandCursor)
            bulk_layout.addWidget(btn)
        self.bulk_bar.setVisible(False)
        tabs_bar.addWidget(self.bulk_bar, 0, Qt.AlignVCenter)

        # 오른쪽 끝: 페이징 네비게이션 ([◀] [정보] [▶]) + 페이징 단위 선택 셀렉트(QComboBox)
        self.paging_nav_widget = QWidget()
        self.paging_nav_widget.setFixedHeight(26)
        nav_layout = QHBoxLayout(self.paging_nav_widget)
        nav_layout.setContentsMargins(0, 0, 4, 0)
        nav_layout.setSpacing(4)
        nav_layout.setSizeConstraint(QLayout.SetFixedSize)

        self.btn_page_prev = QPushButton("◀")
        self.btn_page_prev.setFixedSize(24, 24)
        self.btn_page_prev.setCursor(Qt.PointingHandCursor)
        self.btn_page_prev.clicked.connect(self._on_page_prev)

        self.lbl_page_info = QLabel("")
        self.lbl_page_info.setAlignment(Qt.AlignCenter)

        self.btn_page_next = QPushButton("▶")
        self.btn_page_next.setFixedSize(24, 24)
        self.btn_page_next.setCursor(Qt.PointingHandCursor)
        self.btn_page_next.clicked.connect(self._on_page_next)

        nav_layout.addWidget(self.btn_page_prev)
        nav_layout.addWidget(self.lbl_page_info)
        nav_layout.addWidget(self.btn_page_next)
        self._tb_layout.insertWidget(self._tb_layout.indexOf(self.btn_period) + 1, self.paging_nav_widget, 0, Qt.AlignVCenter)

        self.paging_combo = QComboBox()
        self.paging_combo.setFixedHeight(26)
        self.paging_combo.setFixedWidth(110)
        self.paging_combo.addItems([
            "전체",
            "10개씩",
            "20개씩",
            "50개씩",
            "일별",
            "주간별",
            "월별",
        ])
        saved_paging = self.repository.get_setting("task_paging_mode") or "전체"
        p_idx = self.paging_combo.findText(saved_paging)
        if p_idx >= 0:
            self.paging_combo.setCurrentIndex(p_idx)
        else:
            self.paging_combo.setCurrentIndex(0)
        self.paging_combo.currentIndexChanged.connect(self._on_paging_combo_changed)
        self._tb_layout.insertWidget(self._tb_layout.indexOf(self.btn_period) + 1, self.paging_combo, 0, Qt.AlignVCenter)

        table_container.addWidget(self.tabs_widget)

        # 4. 하단 리스트 테이블 (QTableWidget) - 8개 컬럼 구성
        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setShowGrid(True)

        self.HEADER_COLUMNS = ["등록일자", "분류", "업무 제목", "부서", "기안자 / 작성자", "상태", "비고 / 세부내용", "편집", "", "회신기한", "D-day"]
        self._sort_column: int = 0
        self._sort_order: Qt.SortOrder = Qt.DescendingOrder
        self._update_header_labels()

        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self._on_header_clicked)

        # 헤더 좌우 드래그로 모든 셀/컬럼 크기 조절 가능하도록 Interactive 모드로 설정
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(7, QHeaderView.Fixed)  # 편집 아이콘 열은 55px 고정
        header.setSectionResizeMode(self.COL_CHECK, QHeaderView.Fixed)  # 체크박스 열은 34px 고정
        header.moveSection(header.visualIndex(self.COL_CHECK), 0)  # 체크박스 열을 화면 맨 왼쪽에 표시
        header.moveSection(header.visualIndex(self.COL_REPLY), header.visualIndex(5) + 1)  # 회신기한은 상태 바로 옆
        header.moveSection(header.visualIndex(self.COL_DDAY), header.visualIndex(self.COL_REPLY) + 1)

        # 머리글 전체선택 체크박스 (행 체크박스와 같은 모양)
        self.header_check = QCheckBox(header.viewport())
        self.header_check.setCursor(Qt.PointingHandCursor)
        self.header_check.setToolTip("현재 보이는 문서 전체 선택 / 해제")
        self.header_check.clicked.connect(lambda _=False: self._toggle_check_all_displayed())
        header.sectionResized.connect(self._position_header_check)
        header.sectionMoved.connect(self._position_header_check)
        header.geometriesChanged.connect(self._position_header_check)

        self._default_col_widths = [110, 90, 240, 110, 110, 90, 220, 55, 34, 110, 72]
        self._restore_column_widths()

        self._save_columns_timer = QTimer(self)
        self._save_columns_timer.setSingleShot(True)
        self._save_columns_timer.setInterval(400)
        self._save_columns_timer.timeout.connect(self._save_column_widths)

        header.sectionResized.connect(self._on_section_resized)

        self.table.verticalHeader().setDefaultSectionSize(38)

        # 우클릭 컨텍스트 메뉴 설정
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # 테이블 내 직접 수정 리스너
        self.table.itemChanged.connect(self._on_table_item_changed)

        # 마우스 오버 시 1줄 전체 배경색 하이라이트를 위한 마우스 추적 및 델리게이트
        self.hover_delegate = RowHoverDelegate(self.table)
        self.table.setItemDelegate(self.hover_delegate)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)

        # 테이블을 table_container에 추가하고 메인 레이아웃에 배치 (탭과의 줄간격 0)
        table_container.addWidget(self.table, 1)
        layout.addLayout(table_container, 1)
        self.tabs_widget.raise_()

        # 스킨/테마 스타일 일괄 적용
        self._apply_dialog_styles()

        # 저장된 필터(분류, 상태, 기간, 시작일, 종료일, 전체/등록/진행/완료 탭) 복원
        self._restore_filter_state()

    def _apply_dialog_styles(self) -> None:
        """현재 self.palette를 바탕으로 창 배경, 버튼, 툴바, 탭, 테이블 스타일 일괄 갱신"""
        # font-family 명시: 상속되지 않으면 캘린더·업무 화면과 달리 글자가 깨진다.
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.palette.get('panel', '#FFFFFF')};
                color: {self.palette['text']};
                font-family: {font_family_css()};
            }}
            QLineEdit, QComboBox, QTextEdit, QPushButton, QLabel,
            QTableWidget, QListWidget, QCheckBox, QToolButton {{
                font-family: {font_family_css()};
            }}
        """)
        self._apply_header_button_styles()
        self._apply_toolbar_styles()
        self._apply_table_styles()
        self._update_tab_styles()
        self._apply_paging_styles()

    def _apply_paging_styles(self) -> None:
        """페이징 셀렉트 및 네비게이션 버튼 스타일 갱신"""
        if not hasattr(self, "paging_combo") or not hasattr(self, "btn_page_prev"):
            return
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        arrow_svg = str(asset_path("chevron_down.svg")).replace("\\", "/")

        self.paging_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 6px;
                padding: 1px 16px 1px 8px;
                font-size: 12px;
                font-weight: 500;
                background-color: {panel};
                color: {text};
            }}
            QComboBox:hover {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 14px;
                border-left: none;
            }}
            QComboBox::down-arrow {{
                image: url("{arrow_svg}");
                width: 8px;
                height: 5px;
                margin-right: 3px;
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
                color: {text};
                padding: 2px;
                outline: 0px;
            }}
        """)

        btn_style = f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
                border-color: {accent};
            }}
            QPushButton:disabled {{
                color: {line};
                background-color: transparent;
                border-color: {line};
            }}
        """
        self.btn_page_prev.setStyleSheet(btn_style)
        self.btn_page_next.setStyleSheet(btn_style)
        self.lbl_page_info.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {text};")

        # 체크한 문서 일괄 처리 바
        btn_text = self.palette.get("button_text", "#FFFFFF")
        bulk_btn_style = f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
                padding: 1px 10px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
                border-color: {accent};
            }}
        """
        for btn in (
            self.btn_bulk_status, self.btn_bulk_category,
            self.btn_bulk_link, self.btn_bulk_delete, self.btn_bulk_clear,
        ):
            btn.setStyleSheet(bulk_btn_style)
        self.btn_bulk_done.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {accent};
                border-radius: 6px;
                background-color: {accent};
                color: {btn_text};
                font-size: 12px;
                font-weight: 600;
                padding: 1px 8px;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.12)};
            }}
        """)
        self.lbl_bulk_count.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {accent}; margin-right: 4px;")

    def _apply_header_button_styles(self) -> None:
        """상단 [등록], [분류·상태], [엑셀저장] 버튼 스킨 스타일 갱신 (공통 전환 줄 규격 S/M/L 사용)"""
        if hasattr(self, "window_switcher"):
            self.window_switcher.apply_palette(self.palette)
            return
        accent = self.palette.get("accent", "#1F7A67")
        btn_text = self.palette.get("button_text", "#FFFFFF")
        accent_hover = _shade(accent, -0.12)
        accent_pressed = _shade(accent, -0.20)

        # 신규 등록 버튼: 검색 버튼과 동일한 테마 강조색(accent)
        self.btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {accent_hover};
                border-color: {accent_hover};
            }}
            QPushButton:pressed {{
                background-color: {accent_pressed};
                border-color: {accent_pressed};
            }}
        """)

        # [설정], [엑셀저장] 버튼: 패널 배경색과 텍스트색
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        panel_alt = self.palette.get("panel_alt", "#F6F8FB")
        muted = self.palette.get("muted", line)

        sub_btn_style = f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
                border-color: {muted};
            }}
        """
        self.btn_manage_meta.setStyleSheet(sub_btn_style)
        self.btn_export.setStyleSheet(sub_btn_style)

    def _apply_toolbar_styles(self) -> None:
        """검색/필터 카드 및 하위 입력 위젯 스킨 스타일 갱신"""
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#718096")
        line = self.palette.get("line", "#CBD5E0")
        line_soft = self.palette.get("line_soft", line)
        accent = self.palette.get("accent", "#1F7A67")
        accent_soft = self.palette.get("accent_soft", panel_alt)

        self.toolbar_card.setStyleSheet(f"""
            QFrame#toolbar_card {{
                background-color: {panel};
                border: none;
                border-radius: 0px;
            }}
            QLabel {{
                border: none;
                background-color: transparent;
                color: {text};
            }}
        """)

        arrow_svg = str(asset_path("chevron_down.svg")).replace("\\", "/")

        combo_style = f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 18px 4px 8px;
                font-size: 12px;
                background-color: {panel};
                color: {text};
            }}
            QComboBox:hover, QComboBox:focus {{
                border-color: {accent};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 16px;
                border-left: none;
            }}
            QComboBox::down-arrow {{
                image: url("{arrow_svg}");
                width: 9px;
                height: 6px;
                margin-right: 4px;
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                selection-background-color: {accent_soft};
                selection-color: {text};
                outline: 0px;
                padding: 4px;
            }}
        """
        self.category_combo.setStyleSheet(combo_style)
        self.status_combo.setStyleSheet(combo_style)
        self.period_combo.setStyleSheet(combo_style)

        date_edit_style = f"""
            QDateEdit {{
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 18px 4px 8px;
                font-size: 12px;
                background-color: {panel};
                color: {text};
            }}
            QDateEdit:hover, QDateEdit:focus {{
                border-color: {accent};
            }}
            QDateEdit::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 16px;
                border-left: none;
            }}
            QDateEdit::down-arrow {{
                image: url("{arrow_svg}");
                width: 9px;
                height: 6px;
                margin-right: 4px;
            }}
        """
        self.start_date_edit.setStyleSheet(date_edit_style)
        self.end_date_edit.setStyleSheet(date_edit_style)

        if hasattr(self, "lbl_tilde"):
            self.lbl_tilde.setStyleSheet(f"border: none; background: transparent; color: {muted}; font-weight: bold;")
        self._update_quick_period_styles()
        if hasattr(self, "btn_search"):
            from taskcalendar.ui_common import style_button

            style_button(self.btn_search, self.palette, "secondary", "M")
        if hasattr(self, "btn_period"):
            from taskcalendar.ui_common import style_button as _sb

            _sb(self.btn_period, self.palette, "secondary", "M")
            # 옆의 분류·상태 셀렉트처럼 글자를 왼쪽에
            self.btn_period.setStyleSheet(self.btn_period.styleSheet() + " QPushButton { text-align: left; padding-left: 8px; }")

        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
                background-color: {panel_alt};
                color: {text};
            }}
            QLineEdit:focus {{
                border-color: {accent};
                background-color: {panel};
            }}
        """)

    def _apply_table_styles(self) -> None:
        """하단 리스트 테이블 스킨 스타일 갱신"""
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        panel_alt_hover = _shade(panel_alt, -0.06)
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#667085")
        line = self.palette.get("line", "#CBD5E0")
        line_soft = self.palette.get("line_soft", line)
        accent_soft = self.palette.get("accent_soft", "#EEF2FF")
        accent = self.palette.get("accent", "#1F7A67")
        check_white_icon = str(asset_path("checkmark_white.svg")).replace("\\", "/")

        self.table.setStyleSheet(f"""
            QCheckBox {{
                background: transparent;
                spacing: 0px;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
            }}
            QCheckBox::indicator:hover {{
                border-color: {accent};
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent};
                border: 1px solid {accent};
                image: url("{check_white_icon}");
            }}
            QTableWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-top: 1px solid {line};
                /* 모서리를 하나씩 지정 (border-radius 에 값 4개를 주면 Qt 는 0×6 타원으로 읽어
                   탭 바로 아래 왼쪽 테두리가 5px 쯤 끊겨 보인다) */
                border-top-left-radius: 0px;
                border-top-right-radius: 6px;
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
                gridline-color: {line_soft};
                font-size: 13px;
                outline: 0px;
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QTableWidget::item {{
                border: none;
                padding: 4px 8px;
                outline: 0px;
            }}
            QTableWidget::item:focus {{
                border: none;
                outline: 0px;
            }}
            QTableWidget::item:selected {{
                background-color: {accent_soft};
                color: {text};
                border: none;
                outline: 0px;
            }}
            QHeaderView::section {{
                background-color: {panel_alt};
                color: {muted};
                font-size: 12px;
                font-weight: bold;
                padding: 8px 8px;
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line_soft};
            }}
        """)

    def _set_hovered_row(self, row: int) -> None:
        """마우스 오버 시 1줄 전체 배경색 하이라이트"""
        if not hasattr(self, "hover_delegate"):
            return
        if row == self.hover_delegate.hovered_row:
            return
        self.hover_delegate.hovered_row = row
        self.table.viewport().update()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """테이블 및 셀 위젯 마우스 오버 시 1줄 전체 호버 배경색 동기화 처리"""
        if event.type() in (QEvent.MouseMove, QEvent.Enter):
            row_val = obj.property("row_idx")
            if row_val is not None:
                self._set_hovered_row(int(row_val))
            elif obj == self.table.viewport():
                if hasattr(event, "position"):
                    y = int(event.position().y())
                elif hasattr(event, "pos"):
                    y = event.pos().y()
                else:
                    y = self.table.viewport().mapFromGlobal(QCursor.pos()).y()
                r = self.table.rowAt(y)
                self._set_hovered_row(r)
        elif event.type() == QEvent.Leave:
            vp_pos = self.table.viewport().mapFromGlobal(QCursor.pos())
            if not self.table.viewport().rect().contains(vp_pos):
                self._set_hovered_row(-1)
        elif obj == self.table.viewport() and event.type() == QEvent.Resize:
            self._adjust_columns_to_fill_width()
        elif event.type() == QEvent.KeyPress and obj in (self.table, self.table.viewport()):
            if event.key() == Qt.Key_Delete and self.table.state() != QTableWidget.EditingState:
                self._on_delete_selected()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Delete and self.table.state() != QTableWidget.EditingState:
            self._on_delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def apply_palette(self, palette: dict[str, str]) -> None:
        """스킨/테마 변경 시 팔레트 갱신 및 모든 UI 요소 실시간 리스타일링"""
        self.palette = palette
        if hasattr(self, "hover_delegate"):
            self.hover_delegate.hover_color = QColor(self.palette.get("panel_alt", "#F8FAFC"))
        self._apply_dialog_styles()
        self._render_table()

    def _menu_stylesheet(self) -> str:
        """컨텍스트 메뉴 및 뱃지 드롭다운 메뉴 스킨 스타일 반환"""
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        line_soft = self.palette.get("line_soft", line)
        accent_soft = self.palette.get("accent_soft", self.palette.get("panel_alt", "#EDF2F7"))

        return f"""
            QMenu {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px;
                font-family: {font_family_css()};
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 6px 20px 6px 12px;
                border-radius: 4px;
                color: {text};
            }}
            QMenu::item:selected {{
                background-color: {accent_soft};
                color: {text};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {line_soft};
                margin: 4px 6px;
            }}
        """

    def _refresh_filter_categories(self) -> None:
        curr = self.category_combo.currentText()
        cats = ["분류"] + get_task_categories(self.repository)
        self.category_combo.clear()
        self.category_combo.addItems(cats)
        if curr and curr in cats:
            self.category_combo.setCurrentText(curr)

    def _refresh_filter_statuses(self) -> None:
        curr = self.status_combo.currentText() if hasattr(self, "status_combo") else ""
        statuses = ["상태"] + get_task_statuses(self.repository)
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        self.status_combo.addItems(statuses)
        if curr and curr in statuses:
            self.status_combo.setCurrentText(curr)
        else:
            self.status_combo.setCurrentIndex(0)
        self.status_combo.blockSignals(False)

    def _update_tab_styles(self) -> None:
        """상태 탭 스타일 갱신: 스킨 팔레트 반영 및 활성 탭-테이블 일체형 연결"""
        tab_buttons = [
            ("all", self.btn_tab_all),
            ("reg", self.btn_tab_reg),
            ("prog", self.btn_tab_prog),
            ("done", self.btn_tab_done),
        ]
        visible_tabs = [(key, btn) for key, btn in tab_buttons if not btn.isHidden()]
        if not visible_tabs:
            return

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#EDF2F7")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#4A5568")
        line = self.palette.get("line", "#CBD5E0")
        panel_alt_hover = _shade(panel_alt, -0.06)

        for idx, (key, btn) in enumerate(visible_tabs):
            is_active = (key == self._current_tab)
            is_first = (idx == 0)
            is_last = (idx == len(visible_tabs) - 1)

            # 첫 번째 탭만 왼쪽 모서리 둥글게, 마지막 탭만 오른쪽 모서리 둥글게
            tl_radius = "6px" if is_first else "0px"
            tr_radius = "6px" if is_last else "0px"
            # 첫 번째 탭만 왼쪽 테두리를 그리고, 이후 탭은 왼쪽 테두리를 생략하여 1px 경계 공유
            border_left = f"1px solid {line}" if is_first else "none"

            if is_active:
                btn.setFixedHeight(28)  # 탭 줄 높이와 같게 (29px 이면 아래 1px 이 잘려 모서리가 깨짐)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {panel};
                        color: {text};
                        font-weight: bold;
                        border-top: 1px solid {line};
                        border-left: {border_left};
                        border-right: 1px solid {line};
                        border-bottom: none;
                        border-top-left-radius: {tl_radius};
                        border-top-right-radius: {tr_radius};
                        border-bottom-left-radius: 0px;
                        border-bottom-right-radius: 0px;
                        padding: 4px 16px;
                        font-size: 12px;
                    }}
                """)
            else:
                btn.setFixedHeight(28)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {panel_alt};
                        color: {muted};
                        font-weight: 500;
                        border-top: 1px solid {line};
                        border-left: {border_left};
                        border-right: 1px solid {line};
                        border-bottom: none;
                        border-top-left-radius: {tl_radius};
                        border-top-right-radius: {tr_radius};
                        border-bottom-left-radius: 0px;
                        border-bottom-right-radius: 0px;
                        padding: 4px 16px;
                        font-size: 12px;
                    }}
                    QPushButton:hover {{
                        background-color: {panel_alt_hover};
                        color: {text};
                    }}
                """)

        if hasattr(self, "tabs_widget"):
            self.tabs_widget.raise_()

    def _set_active_tab(self, tab: str) -> None:
        """상태 탭 전환 (전체/등록/진행/완료) 및 스타일 갱신"""
        self._current_tab = tab
        self._update_tab_styles()
        if hasattr(self, "status_combo") and self.status_combo:
            self.status_combo.blockSignals(True)
            if tab == "all":
                self.status_combo.setCurrentIndex(0)
            elif tab == "reg":
                idx = self.status_combo.findText("등록")
                if idx >= 0:
                    self.status_combo.setCurrentIndex(idx)
            elif tab == "prog":
                idx = self.status_combo.findText("진행중")
                if idx < 0:
                    idx = self.status_combo.findText("진행")
                if idx >= 0:
                    self.status_combo.setCurrentIndex(idx)
            elif tab == "done":
                idx = self.status_combo.findText("완료")
                if idx >= 0:
                    self.status_combo.setCurrentIndex(idx)
            self.status_combo.blockSignals(False)
        self._current_page = 1
        self._apply_filters()

    def _on_status_combo_changed(self, *args) -> None:
        """상태 콤보박스 변경 시 탭과 자동 동기화 후 즉시 필터 적용"""
        if not hasattr(self, "status_combo") or not self.status_combo:
            return
        selected = self.status_combo.currentText()
        if selected == "등록":
            self._current_tab = "reg"
        elif selected in ("진행", "진행중"):
            self._current_tab = "prog"
        elif selected == "완료":
            self._current_tab = "done"
        else:
            self._current_tab = "all"
        self._update_tab_styles()
        self._current_page = 1
        self._apply_filters()

    def _on_period_combo_changed(self, *args) -> None:
        """기간 콤보박스 변경 시 시작일/종료일 자동 설정 및 즉시 필터링"""
        selected = self.period_combo.currentText()
        today = date.today()
        self.start_date_edit.blockSignals(True)
        self.end_date_edit.blockSignals(True)

        if selected == "오늘":
            self.start_date_edit.setDate(today)
            self.end_date_edit.setDate(today)
        elif selected == "최근 7일":
            self.start_date_edit.setDate(today - timedelta(days=6))
            self.end_date_edit.setDate(today)
        elif selected == "최근 30일":
            self.start_date_edit.setDate(today - timedelta(days=29))
            self.end_date_edit.setDate(today)
        elif selected == "이번 주":
            start_w = today - timedelta(days=today.weekday())
            end_w = start_w + timedelta(days=6)
            self.start_date_edit.setDate(start_w)
            self.end_date_edit.setDate(end_w)
        elif selected == "이번 달":
            start_m = date(today.year, today.month, 1)
            if today.month == 12:
                end_m = date(today.year, 12, 31)
            else:
                end_m = date(today.year, today.month + 1, 1) - timedelta(days=1)
            self.start_date_edit.setDate(start_m)
            self.end_date_edit.setDate(end_m)
        elif selected == "최근 3개월":
            self.start_date_edit.setDate(_months_before(today, 3))
            self.end_date_edit.setDate(today)
        elif selected == "최근 6개월":
            self.start_date_edit.setDate(_months_before(today, 6))
            self.end_date_edit.setDate(today)
        elif selected == "최근 1년":
            self.start_date_edit.setDate(_months_before(today, 12))
            self.end_date_edit.setDate(today)
        elif selected == "올해":
            self.start_date_edit.setDate(date(today.year, 1, 1))
            self.end_date_edit.setDate(date(today.year, 12, 31))

        # 전체 기간이더라도 날짜 수정은 항상 가능
        self.start_date_edit.setEnabled(True)
        self.end_date_edit.setEnabled(True)

        self.start_date_edit.blockSignals(False)
        self.end_date_edit.blockSignals(False)
        self._current_page = 1
        self._update_quick_period_styles()
        self._apply_filters()

    _PERIOD_BUTTON_LABELS = {"기간": "전체", "직접 지정": ""}

    def _update_period_button_text(self) -> None:
        if not hasattr(self, "btn_period"):
            return
        current = self.period_combo.currentText()
        label = self._PERIOD_BUTTON_LABELS.get(current, current)
        if not label:  # 직접 지정
            label = f"{self.start_date_edit.date().toString('yyyy-MM-dd')} ~ {self.end_date_edit.date().toString('yyyy-MM-dd')}"
        self.btn_period.setText(f"기간: {label} ▾")

    def _show_period_popup(self) -> None:
        self._update_quick_period_styles()
        p = self.palette
        self.period_popup.setStyleSheet(f"""
            QFrame#periodPopup {{ background-color: #FFFFFF; border: 1px solid {_shade(p.get('line', '#CBD5E0'), -0.15)};
                border-radius: 8px; }}
            QLabel {{ color: {p.get('muted', '#718096')}; font-size: 12px; background: transparent; border: none; }}
        """)
        self.period_popup.adjustSize()
        pos = self.btn_period.mapToGlobal(QPoint(0, self.btn_period.height() + 4))
        self.period_popup.move(pos)
        self.period_popup.show()

    def _set_quick_period(self, period: str) -> None:
        """기간 빠른 선택 버튼 클릭 시 기간 콤보를 맞추고 날짜를 오늘 기준으로 다시 계산"""
        idx = self.period_combo.findText(period)
        if idx < 0:
            return
        self.period_combo.blockSignals(True)
        self.period_combo.setCurrentIndex(idx)
        self.period_combo.blockSignals(False)
        self._on_period_combo_changed()

    def _update_quick_period_styles(self) -> None:
        """현재 기간 콤보 값과 같은 빠른 선택 버튼만 강조 표시"""
        if not hasattr(self, "_quick_period_buttons"):
            return
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#718096")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        btn_text = self.palette.get("button_text", "#FFFFFF")
        current = self.period_combo.currentText()

        self.lbl_quick_period.setStyleSheet(f"border: none; background: transparent; color: {muted}; font-size: 12px;")
        self._update_period_button_text()
        for btn, period in self._quick_period_buttons:
            if period == current:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {accent};
                        color: {btn_text};
                        border: 1px solid {accent};
                        border-radius: 13px;
                        padding: 0px;
                        font-size: 12px;
                        font-weight: 600;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {panel};
                        color: {text};
                        border: 1px solid {line};
                        border-radius: 13px;
                        padding: 0px;
                        font-size: 12px;
                    }}
                    QPushButton:hover {{
                        background-color: {panel_alt};
                        border-color: {accent};
                    }}
                """)

    def _on_custom_date_changed(self, *args) -> None:
        """사용자가 날짜를 직접 변경 시 '직접 지정' 모드로 전환 후 즉시 필터 적용"""
        s_date = self.start_date_edit.date()
        e_date = self.end_date_edit.date()
        sender = self.sender()
        if sender == self.start_date_edit and s_date > e_date:
            self.end_date_edit.blockSignals(True)
            self.end_date_edit.setDate(s_date)
            self.end_date_edit.blockSignals(False)
        elif sender == self.end_date_edit and e_date < s_date:
            self.start_date_edit.blockSignals(True)
            self.start_date_edit.setDate(e_date)
            self.start_date_edit.blockSignals(False)

        if self.period_combo.currentText() != "직접 지정":
            self.period_combo.blockSignals(True)
            idx = self.period_combo.findText("직접 지정")
            if idx >= 0:
                self.period_combo.setCurrentIndex(idx)
            self.period_combo.blockSignals(False)
        self._current_page = 1
        self._update_quick_period_styles()
        self._apply_filters()

    def _open_category_manager(self) -> None:
        cats = get_task_categories(self.repository)
        dlg = SimpleListManagerDialog(
            self,
            "업무 분류 항목 관리",
            cats,
            lambda new_cats: save_task_categories(self.repository, new_cats),
            immutable_item="일반",
            palette=self.palette,
        )
        if dlg.exec() == QDialog.Accepted:
            self._refresh_filter_categories()
            self._apply_filters()

    def _open_status_manager(self) -> None:
        statuses = get_task_statuses(self.repository)
        dlg = SimpleListManagerDialog(
            self,
            "처리 상태 항목 관리",
            statuses,
            lambda new_statuses: save_task_statuses(self.repository, new_statuses),
            immutable_item="등록",
            palette=self.palette,
        )
        if dlg.exec() == QDialog.Accepted:
            self._refresh_filter_statuses()
            self._apply_filters()
            if hasattr(self, "main_window") and getattr(self, "main_window", None) and hasattr(self.main_window, "refresh"):
                self.main_window.refresh()

    def reload_tasks(self) -> None:
        """저장소에서 모든 TASK 타입 엔트리를 불러와 새로고침"""
        all_entries = self.repository.list_all_entries()
        self._all_tasks = [e for e in all_entries if e.entry_type == EntryType.TASK]
        self._all_tasks.sort(key=lambda x: (x.day or date.min, x.created_at or datetime.min), reverse=True)
        self._apply_filters()

    def _run_search(self) -> None:
        self._search_query = self.search_input.text().strip()
        self._apply_filters()

    def _apply_filters(self, *args) -> None:
        """탭(전체/등록/진행/완료), 분류, 상태, 기간, 검색어 필터 즉시 적용"""
        query = getattr(self, "_search_query", "").strip().lower()
        self._update_period_button_text()
        selected_cat = self.category_combo.currentText()
        selected_status = self.status_combo.currentText()
        selected_period = self.period_combo.currentText()

        filtered = []
        for task in self._all_tasks:
            # 1. 상태 필터 (콤보박스 및 탭 동기화 상태)
            st = task.status or "등록"
            if selected_status not in ("상태", "전체", ""):
                if st != selected_status:
                    continue
            elif self._current_tab != "all":
                if self._current_tab == "reg" and st != "등록":
                    continue
                elif self._current_tab == "prog" and st not in ("진행", "진행중"):
                    continue
                elif self._current_tab == "done" and st != "완료":
                    continue

            # 2. 분류 필터
            if selected_cat not in ("분류", "전체", ""):
                task_cat = task.memo_group or "일반"
                if task_cat != selected_cat:
                    continue

            # 3. 기간 필터 (시작일 ~ 종료일)
            if selected_period not in ("기간", "전체", "전체 기간", "전체(기간)", "기간(전체)"):
                t_start = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
                t_end = task.end_date or t_start
                if not t_start:
                    continue
                if not t_end:
                    t_end = t_start
                start_d = self.start_date_edit.date().toPython()
                end_d = self.end_date_edit.date().toPython()
                if not (t_start <= end_d and t_end >= start_d):
                    continue

            # 4. 검색어 필터 (제목, 부서, 기안자, 세부내용, 분류)
            if query:
                title_match = query in (task.title or "").lower()
                dept_match = query in (getattr(task, "department", "") or "").lower()
                author_match = query in (task.assignee or "").lower()
                desc_match = query in (task.description or "").lower()
                cat_match = query in (task.memo_group or "").lower()
                if not (title_match or dept_match or author_match or desc_match or cat_match):
                    continue

            filtered.append(task)

        self._filtered_tasks = filtered
        # 필터에서 빠진 문서는 체크 해제 (보이지 않는 문서가 일괄 처리되지 않도록)
        self._checked_ids &= {t.entry_id for t in filtered}
        self._sort_tasks()
        self._render_table()
        self._save_filter_state(persist_disk=False)

    def _save_filter_state(self, persist_disk: bool = False) -> None:
        """문서관리 필터(분류, 상태, 기간, 시작일, 종료일, 탭) 상태를 저장소에 기록"""
        if getattr(self, "_restoring_filters", False) or not self.repository:
            return
        try:
            state = {
                "category": self.category_combo.currentText() if hasattr(self, "category_combo") else "분류",
                "status": self.status_combo.currentText() if hasattr(self, "status_combo") else "상태",
                "period": self.period_combo.currentText() if hasattr(self, "period_combo") else "기간",
                "start_date": self.start_date_edit.date().toString("yyyy-MM-dd") if hasattr(self, "start_date_edit") else "",
                "end_date": self.end_date_edit.date().toString("yyyy-MM-dd") if hasattr(self, "end_date_edit") else "",
                "tab": getattr(self, "_current_tab", "all") or "all",
            }
            self.repository.set_setting("task_manager_filter_state", json.dumps(state, ensure_ascii=False))
            if persist_disk:
                self.repository.save()
        except Exception:
            pass

    def _restore_filter_state(self) -> None:
        """저장된 문서관리 필터(분류, 상태, 기간, 시작일, 종료일, 탭) 상태 복원"""
        self._restoring_filters = True
        try:
            raw = self.repository.get_setting("task_manager_filter_state") if self.repository else None
            state = json.loads(raw) if raw else None
            if not isinstance(state, dict):
                self._current_tab = "all"
                self._update_tab_styles()
                return

            self.category_combo.blockSignals(True)
            self.status_combo.blockSignals(True)
            self.period_combo.blockSignals(True)
            self.start_date_edit.blockSignals(True)
            self.end_date_edit.blockSignals(True)
            try:
                saved_cat = state.get("category", "")
                if saved_cat:
                    idx = self.category_combo.findText(saved_cat)
                    if idx >= 0:
                        self.category_combo.setCurrentIndex(idx)

                saved_tab = state.get("tab", "all")
                if saved_tab not in ("all", "reg", "prog", "done"):
                    saved_tab = "all"
                self._current_tab = saved_tab

                saved_status = state.get("status", "")
                if saved_status:
                    idx = self.status_combo.findText(saved_status)
                    if idx >= 0:
                        self.status_combo.setCurrentIndex(idx)

                saved_period = state.get("period", "")
                if saved_period:
                    idx = self.period_combo.findText(saved_period)
                    if idx >= 0:
                        self.period_combo.setCurrentIndex(idx)

                saved_start = state.get("start_date", "")
                if saved_start:
                    qd_s = QDate.fromString(saved_start, "yyyy-MM-dd")
                    if qd_s.isValid():
                        self.start_date_edit.setDate(qd_s)

                saved_end = state.get("end_date", "")
                if saved_end:
                    qd_e = QDate.fromString(saved_end, "yyyy-MM-dd")
                    if qd_e.isValid():
                        self.end_date_edit.setDate(qd_e)
            finally:
                self.category_combo.blockSignals(False)
                self.status_combo.blockSignals(False)
                self.period_combo.blockSignals(False)
                self.start_date_edit.blockSignals(False)
                self.end_date_edit.blockSignals(False)

            self._update_tab_styles()
        except Exception:
            self._current_tab = "all"
            self._update_tab_styles()
        finally:
            self._restoring_filters = False
            self._update_quick_period_styles()

    def _update_header_labels(self) -> None:
        """현재 정렬 컬럼 및 방향에 따라 헤더 라벨에 정렬 화살표(▲/▼) 표시"""
        labels = []
        for idx, name in enumerate(self.HEADER_COLUMNS):
            if idx == self.COL_CHECK:
                labels.append("")  # 머리글 칸에는 실제 체크박스(header_check)를 올려 둔다
            elif idx == self._sort_column:
                arrow = " ▲" if self._sort_order == Qt.AscendingOrder else " ▼"
                labels.append(f"{name}{arrow}")
            else:
                labels.append(name)
        self.table.setHorizontalHeaderLabels(labels)

    def _on_header_clicked(self, col: int) -> None:
        """컬럼 헤더 클릭 시 해당 열 기준 오름차순/내림차순 정렬 (편집 컬럼 제외)"""
        if col == self.COL_CHECK:
            self._toggle_check_all_displayed()
            return
        if col == 7:
            return

        if self._sort_column == col:
            self._sort_order = Qt.AscendingOrder if self._sort_order == Qt.DescendingOrder else Qt.DescendingOrder
        else:
            self._sort_column = col
            # 등록일자는 최신순(내림차순) 기본, 기타 항목은 오름차순 기본
            self._sort_order = Qt.DescendingOrder if col == 0 else Qt.AscendingOrder

        self._update_header_labels()
        self._sort_tasks()
        self._render_table()

    def _on_section_resized(self, logicalIndex: int, oldSize: int, newSize: int) -> None:
        """사용자가 헤더를 좌우로 드래그하여 컬럼 크기를 조절할 때 호출 (디바운스 저장)"""
        if getattr(self, "_restoring_columns", False):
            return

        viewport_w = self.table.viewport().width()
        total_w = sum(self.table.columnWidth(i) for i in range(self.table.columnCount()))

        # 사용자가 컬럼을 줄여서 오른쪽에 빈 공간이 생길 경우(total_w < viewport_w),
        # 비고(col 6) 또는 제목(col 2)이 빈 공간을 채워 100% 가로 너비를 항상 유지
        if total_w < viewport_w:
            gap = viewport_w - total_w
            target_col = 6 if logicalIndex != 6 else 2
            self._restoring_columns = True
            try:
                self.table.setColumnWidth(target_col, self.table.columnWidth(target_col) + gap)
            finally:
                self._restoring_columns = False

        if hasattr(self, "_save_columns_timer"):
            self._save_columns_timer.start()

    def _save_column_widths(self) -> None:
        """조정된 컬럼 너비를 저장소에 영구 저장"""
        try:
            widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
            self.repository.set_setting("task_table_column_widths", json.dumps(widths))
            self.repository.save()
        except Exception:
            pass

    def _adjust_columns_to_fill_width(self) -> None:
        """테이블이 가로 너비(100%)를 빈틈없이 꽉 채우도록 여유 공간을 유연 컬럼(제목, 비고)에 배분"""
        if not hasattr(self, "table") or self.table is None:
            return
        viewport_w = self.table.viewport().width()
        if viewport_w <= 150:
            return

        total_w = sum(self.table.columnWidth(i) for i in range(self.table.columnCount()))
        diff = viewport_w - total_w

        if diff != 0:
            col2_w = self.table.columnWidth(2)
            col6_w = self.table.columnWidth(6)

            add_col2 = int(diff * 0.4)
            add_col6 = diff - add_col2

            new_col2 = max(160, col2_w + add_col2)
            new_col6 = max(120, col6_w + add_col6)

            self._restoring_columns = True
            try:
                self.table.setColumnWidth(2, new_col2)
                self.table.setColumnWidth(6, new_col6)

                current_total = sum(self.table.columnWidth(i) for i in range(self.table.columnCount()))
                remaining = viewport_w - current_total
                if remaining != 0:
                    final_col6 = max(100, self.table.columnWidth(6) + remaining)
                    self.table.setColumnWidth(6, final_col6)
            finally:
                self._restoring_columns = False

    def _restore_column_widths(self) -> None:
        """저장소에 저장된 사용자 정의 컬럼 너비 복원 후 100% 가로 너비 자동 맞춤"""
        self._restoring_columns = True
        try:
            raw = self.repository.get_setting("task_table_column_widths")
            widths = None
            if raw:
                widths = json.loads(raw)
            # 체크박스·회신기한·D-day 열이 생기기 전에 저장된 너비는 빠진 칸을 기본값으로 채움
            if isinstance(widths, list) and 8 <= len(widths) < self.table.columnCount():
                widths = widths + self._default_col_widths[len(widths):]
            if widths and isinstance(widths, list) and len(widths) == self.table.columnCount():
                for i, w in enumerate(widths):
                    if i == 7:
                        self.table.setColumnWidth(i, 55)
                    elif i == self.COL_CHECK:
                        self.table.setColumnWidth(i, 34)
                    else:
                        self.table.setColumnWidth(i, max(40, int(w)))
            else:
                for i, w in enumerate(self._default_col_widths):
                    self.table.setColumnWidth(i, w)
        except Exception:
            for i, w in enumerate(self._default_col_widths):
                self.table.setColumnWidth(i, w)
        finally:
            self._restoring_columns = False
            self._adjust_columns_to_fill_width()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._adjust_columns_to_fill_width)
        QTimer.singleShot(0, self._position_header_check)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adjust_columns_to_fill_width()

    def closeEvent(self, event) -> None:
        self._save_filter_state(persist_disk=False)
        self._save_column_widths()
        super().closeEvent(event)

    def _sort_tasks(self) -> None:
        """현재 self._sort_column 및 self._sort_order에 따라 self._filtered_tasks 정렬"""
        if self._sort_column is None or not self._filtered_tasks:
            return

        reverse = (self._sort_order == Qt.DescendingOrder)

        if self._sort_column == 0:  # 등록일자
            def key_fn(t):
                d = t.day or t.start_date or (t.created_at.date() if t.created_at else date.min)
                dt = t.created_at or datetime.min
                return (d, dt)
        elif self._sort_column == 1:  # 분류
            def key_fn(t):
                return (t.memo_group or "일반", t.day or date.min)
        elif self._sort_column == 2:  # 업무 제목
            def key_fn(t):
                return ((t.title or "").strip().lower(), t.day or date.min)
        elif self._sort_column == 3:  # 부서
            def key_fn(t):
                return ((getattr(t, "department", "") or "").strip().lower(), t.day or date.min)
        elif self._sort_column == 4:  # 기안자 / 작성자
            def key_fn(t):
                return ((t.assignee or "").strip().lower(), t.day or date.min)
        elif self._sort_column == 5:  # 상태
            statuses = get_task_statuses(self.repository)
            status_order = {s: i for i, s in enumerate(statuses)}
            def key_fn(t):
                st = t.status or "등록"
                return (status_order.get(st, 999), st, t.day or date.min)
        elif self._sort_column == 6:  # 비고 / 세부내용
            def key_fn(t):
                return ((t.description or "").strip().lower(), t.day or date.min)
        elif self._sort_column in (self.COL_REPLY, self.COL_DDAY):  # 회신기한 (기한 없는 문서는 늘 뒤로)
            def key_fn(t):
                due = getattr(t, "reply_due", None)
                if due:
                    return (0 if not reverse else 1, due)
                return (1 if not reverse else 0, date.max)
        else:
            return

        self._filtered_tasks.sort(key=key_fn, reverse=reverse)

    def _open_meta_manager_menu(self) -> None:
        """분류/상태 설정 드롭다운 메뉴"""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        act_cat = menu.addAction("업무 분류 항목 관리...")
        act_stat = menu.addAction("처리 상태 항목 관리...")
        chosen = menu.exec(self.btn_manage_meta.mapToGlobal(QPoint(0, self.btn_manage_meta.height() + 2)))
        if chosen == act_cat:
            self._open_category_manager()
        elif chosen == act_stat:
            self._open_status_manager()

    def _show_category_menu(self, task: CalendarEntry, btn: QPushButton) -> None:
        """분류 뱃지 클릭 시 드롭다운 메뉴"""
        cats = get_task_categories(self.repository)
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        actions = {}
        for c in cats:
            act = menu.addAction(f"{c}")
            actions[act] = c

        menu.addSeparator()
        act_manage = menu.addAction("분류 항목 관리...")

        chosen = menu.exec(btn.mapToGlobal(QPoint(0, btn.height() + 2)))
        if chosen in actions:
            new_cat = actions[chosen]
            if task.memo_group != new_cat:
                self._on_category_cell_changed(task, new_cat)
                if hasattr(self, "category_combo") and self.category_combo.currentText() not in ("분류", "전체", ""):
                    self._apply_filters()
                else:
                    r = btn.property("row_idx")
                    if r is not None and 0 <= r < self.table.rowCount():
                        w_cat = self.table.cellWidget(r, 1)
                        if isinstance(w_cat, PillBadgeWidget):
                            is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
                            cfg = CATEGORY_CONFIG_DARK if is_dark else CATEGORY_CONFIG
                            w_cat.update_badge(new_cat, cfg)
        elif chosen == act_manage:
            self._open_category_manager()

    def _show_status_menu(self, task: CalendarEntry, btn: QPushButton, row_idx: int) -> None:
        """상태 뱃지 클릭 시 드롭다운 메뉴"""
        statuses = get_task_statuses(self.repository)
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        actions = {}
        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        badge_status_cfg = STATUS_CONFIG_DARK if is_dark else STATUS_CONFIG
        for s in statuses:
            icon = badge_status_cfg.get(s, {}).get("icon", "▫️")
            act = menu.addAction(f"{s}")
            actions[act] = s

        menu.addSeparator()
        act_manage = menu.addAction("상태 항목 관리...")

        chosen = menu.exec(btn.mapToGlobal(QPoint(0, btn.height() + 2)))
        if chosen in actions:
            new_st = actions[chosen]
            if task.status != new_st:
                self._on_status_cell_changed(task, new_st, row_idx)
                # 특정 상태 탭("등록", "진행", "완료") 필터가 걸려있는 경우에만 목록 재필터링
                if self._current_tab != "all" or (hasattr(self, "status_combo") and self.status_combo.currentText() not in ("상태", "전체", "")):
                    self._apply_filters()
        elif chosen == act_manage:
            self._open_status_manager()

    def _on_paging_combo_changed(self) -> None:
        """페이징 단위 콤보박스 변경 시 초기화 및 렌더링"""
        self._current_page = 1
        self._current_date_anchor = None
        mode = self.paging_combo.currentText()
        try:
            self.repository.set_setting("task_paging_mode", mode)
            self.repository.save()
        except Exception:
            pass
        self._render_table()

    def _get_paged_tasks(self) -> list[CalendarEntry]:
        """현재 선택된 페이징 모드(전체/갯수별/일별/주간별/월별)에 따라 표시할 항목 슬라이싱"""
        mode = self.paging_combo.currentText() if hasattr(self, "paging_combo") else "전체"
        total_items = len(self._filtered_tasks)

        if mode == "전체" or not mode:
            if hasattr(self, "paging_nav_widget"):
                self.paging_nav_widget.setVisible(False)
            return self._filtered_tasks

        # 1. 갯수별 페이징 (10개씩, 20개씩, 50개씩)
        if mode in ("10개씩", "20개씩", "50개씩"):
            self.paging_nav_widget.setVisible(True)
            page_size = int(mode.replace("개씩", ""))
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            if self._current_page > total_pages:
                self._current_page = total_pages
            if self._current_page < 1:
                self._current_page = 1

            start_idx = (self._current_page - 1) * page_size
            end_idx = min(start_idx + page_size, total_items)
            paged = self._filtered_tasks[start_idx:end_idx]

            self.lbl_page_info.setText(f"{self._current_page} / {total_pages} (총 {total_items}건)")
            self.btn_page_prev.setEnabled(self._current_page > 1)
            self.btn_page_next.setEnabled(self._current_page < total_pages)
            return paged

        # 날짜 기반 페이징 (일별 / 주간별 / 월별)
        distinct_dates = sorted(list({
            t.day or t.start_date or (t.created_at.date() if t.created_at else None)
            for t in self._filtered_tasks
            if (t.day or t.start_date or (t.created_at.date() if t.created_at else None))
        }))

        if getattr(self, "_current_date_anchor", None) is None:
            today = date.today()
            if today in distinct_dates or not distinct_dates:
                self._current_date_anchor = today
            else:
                self._current_date_anchor = distinct_dates[-1]

        anchor = self._current_date_anchor

        # 2. 일별 페이징
        if mode == "일별":
            self.paging_nav_widget.setVisible(True)
            paged = [
                t for t in self._filtered_tasks
                if (t.day or t.start_date or (t.created_at.date() if t.created_at else None)) == anchor
            ]
            self.lbl_page_info.setText(f"{anchor.strftime('%Y-%m-%d')} ({len(paged)}건)")
            prev_dates = [d for d in distinct_dates if d < anchor]
            next_dates = [d for d in distinct_dates if d > anchor]
            self.btn_page_prev.setEnabled(len(prev_dates) > 0)
            self.btn_page_next.setEnabled(len(next_dates) > 0)
            return paged

        # 3. 주간별 페이징 (월~일)
        if mode == "주간별":
            self.paging_nav_widget.setVisible(True)
            start_week = anchor - timedelta(days=anchor.weekday())
            end_week = start_week + timedelta(days=6)
            paged = [
                t for t in self._filtered_tasks
                if (t.day or t.start_date or (t.created_at.date() if t.created_at else None))
                and start_week <= (t.day or t.start_date or (t.created_at.date() if t.created_at else None)) <= end_week
            ]
            self.lbl_page_info.setText(f"{start_week.strftime('%m.%d')}~{end_week.strftime('%m.%d')} ({len(paged)}건)")
            prev_weeks = [d for d in distinct_dates if d < start_week]
            next_weeks = [d for d in distinct_dates if d > end_week]
            self.btn_page_prev.setEnabled(len(prev_weeks) > 0)
            self.btn_page_next.setEnabled(len(next_weeks) > 0)
            return paged

        # 4. 월별 페이징
        if mode == "월별":
            self.paging_nav_widget.setVisible(True)
            year, month = anchor.year, anchor.month
            paged = [
                t for t in self._filtered_tasks
                if (t.day or t.start_date or (t.created_at.date() if t.created_at else None))
                and (t.day or t.start_date or (t.created_at.date() if t.created_at else None)).year == year
                and (t.day or t.start_date or (t.created_at.date() if t.created_at else None)).month == month
            ]
            self.lbl_page_info.setText(f"{year}년 {month:02d}월 ({len(paged)}건)")
            prev_months = [d for d in distinct_dates if (d.year, d.month) < (year, month)]
            next_months = [d for d in distinct_dates if (d.year, d.month) > (year, month)]
            self.btn_page_prev.setEnabled(len(prev_months) > 0)
            self.btn_page_next.setEnabled(len(next_months) > 0)
            return paged

        self.paging_nav_widget.setVisible(False)
        return self._filtered_tasks

    def _on_page_prev(self) -> None:
        """이전 페이지 / 이전 날짜 이동"""
        mode = self.paging_combo.currentText()
        if mode in ("10개씩", "20개씩", "50개씩"):
            if self._current_page > 1:
                self._current_page -= 1
                self._render_table()
        else:
            distinct_dates = sorted(list({
                t.day or t.start_date or (t.created_at.date() if t.created_at else None)
                for t in self._filtered_tasks
                if (t.day or t.start_date or (t.created_at.date() if t.created_at else None))
            }))
            anchor = getattr(self, "_current_date_anchor", date.today())
            if mode == "일별":
                prev_dates = [d for d in distinct_dates if d < anchor]
                if prev_dates:
                    self._current_date_anchor = prev_dates[-1]
                else:
                    self._current_date_anchor -= timedelta(days=1)
                self._render_table()
            elif mode == "주간별":
                start_week = anchor - timedelta(days=anchor.weekday())
                prev_weeks = [d for d in distinct_dates if d < start_week]
                if prev_weeks:
                    self._current_date_anchor = prev_weeks[-1]
                else:
                    self._current_date_anchor -= timedelta(days=7)
                self._render_table()
            elif mode == "월별":
                year, month = anchor.year, anchor.month
                prev_months = [d for d in distinct_dates if (d.year, d.month) < (year, month)]
                if prev_months:
                    self._current_date_anchor = prev_months[-1]
                else:
                    m = month - 1
                    y = year
                    if m < 1:
                        m = 12
                        y -= 1
                    self._current_date_anchor = date(y, m, 1)
                self._render_table()

    def _on_page_next(self) -> None:
        """다음 페이지 / 다음 날짜 이동"""
        mode = self.paging_combo.currentText()
        if mode in ("10개씩", "20개씩", "50개씩"):
            total_items = len(self._filtered_tasks)
            page_size = int(mode.replace("개씩", ""))
            total_pages = max(1, (total_items + page_size - 1) // page_size)
            if self._current_page < total_pages:
                self._current_page += 1
                self._render_table()
        else:
            distinct_dates = sorted(list({
                t.day or t.start_date or (t.created_at.date() if t.created_at else None)
                for t in self._filtered_tasks
                if (t.day or t.start_date or (t.created_at.date() if t.created_at else None))
            }))
            anchor = getattr(self, "_current_date_anchor", date.today())
            if mode == "일별":
                next_dates = [d for d in distinct_dates if d > anchor]
                if next_dates:
                    self._current_date_anchor = next_dates[0]
                else:
                    self._current_date_anchor += timedelta(days=1)
                self._render_table()
            elif mode == "주간별":
                start_week = anchor - timedelta(days=anchor.weekday())
                end_week = start_week + timedelta(days=6)
                next_weeks = [d for d in distinct_dates if d > end_week]
                if next_weeks:
                    self._current_date_anchor = next_weeks[0]
                else:
                    self._current_date_anchor += timedelta(days=7)
                self._render_table()
            elif mode == "월별":
                year, month = anchor.year, anchor.month
                next_months = [d for d in distinct_dates if (d.year, d.month) > (year, month)]
                if next_months:
                    self._current_date_anchor = next_months[0]
                else:
                    m = month + 1
                    y = year
                    if m > 12:
                        m = 1
                        y += 1
                    self._current_date_anchor = date(y, m, 1)
                self._render_table()

    def _render_table(self) -> None:
        """필터링 및 페이징된 작업 목록을 8개 컬럼으로 렌더링하고 인라인 수정 지원"""
        if hasattr(self, "hover_delegate"):
            self.hover_delegate.hovered_row = -1
        self.table.blockSignals(True)
        self.table.setRowCount(0)

        tasks_to_show = self._get_paged_tasks()
        self._displayed_tasks = tasks_to_show
        self.table.setRowCount(len(tasks_to_show))

        self._update_stats_label()

        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        badge_status_cfg = STATUS_CONFIG_DARK if is_dark else STATUS_CONFIG
        badge_cat_cfg = CATEGORY_CONFIG_DARK if is_dark else CATEGORY_CONFIG
        text_normal = QColor(self.palette.get("text", "#2D3748"))
        text_muted = QColor(self.palette.get("muted", "#A0AEC0"))

        row_font = make_ui_font_like(10)
        if hasattr(self, "hover_delegate"):
            self.hover_delegate.dark = is_dark
        work_titles: dict[int, list[str]] = {}
        try:
            work_titles = self.repository.linked_work_titles_for_entries(
                [t.entry_id for t in tasks_to_show if t.entry_id]
            )
        except Exception:
            work_titles = {}
        today = date.today()
        for row_idx, task in enumerate(tasks_to_show):
            is_done = (task.status == "완료")
            desc_body, source_url = split_doc_source(task.description or "")
            linked_titles = work_titles.get(task.entry_id or -1, [])
            text_color = text_muted if is_done else text_normal

            if is_done:
                f = QFont(row_font)
                f.setStrikeOut(True)
            else:
                f = row_font

            # 0: 등록일자 (직접 편집 가능)
            d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
            d_str = d_val.strftime("%Y-%m-%d") if d_val else "-"
            item_date = QTableWidgetItem(d_str)
            item_date.setTextAlignment(Qt.AlignCenter)
            item_date.setForeground(text_color)
            item_date.setFont(f)
            item_date.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(row_idx, 0, item_date)

            # 1: 분류 (알약 뱃지 버튼 클릭 드롭다운)
            item_cat = QTableWidgetItem()
            item_cat.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row_idx, 1, item_cat)
            cat_str = task.memo_group or "일반"
            cat_widget = PillBadgeWidget(
                cat_str,
                badge_cat_cfg,
                lambda btn, t=task: self._show_category_menu(t, btn),
                on_context=lambda pos, t=task, r=row_idx: self._show_context_menu_for_task(t, pos, r),
            )
            cat_widget.setProperty("row_idx", row_idx)
            cat_widget.btn.setProperty("row_idx", row_idx)
            cat_widget.installEventFilter(self)
            cat_widget.btn.installEventFilter(self)
            self.table.setCellWidget(row_idx, 1, cat_widget)

            # 2: 업무 제목 (직접 편집 가능)
            item_title = QTableWidgetItem(task.title or "")
            item_title.setForeground(text_color)
            item_title.setFont(f)
            item_title.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            tags = []
            tip_lines = [task.title or ""]
            if source_url:
                tags.append("출처")
                tip_lines.append(f"출처: {source_url}  (우클릭 → 출처 웹페이지 열기)")
            if linked_titles:
                tags.append(f"업무 {len(linked_titles)}개")
                tip_lines.append("관련 업무: " + ", ".join(linked_titles))
            if tags:
                item_title.setData(DOC_TAGS_ROLE, tags)
                item_title.setToolTip("\n".join(tip_lines))
            self.table.setItem(row_idx, 2, item_title)

            # 3: 부서 (직접 편집 가능)
            dept_str = getattr(task, "department", "") or ""
            item_dept = QTableWidgetItem(dept_str)
            item_dept.setTextAlignment(Qt.AlignCenter)
            item_dept.setForeground(text_color)
            item_dept.setFont(f)
            item_dept.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(row_idx, 3, item_dept)

            # 4: 기안자 / 작성자 (직접 편집 가능)
            author_str = task.assignee or ""
            item_author = QTableWidgetItem(author_str)
            item_author.setTextAlignment(Qt.AlignCenter)
            item_author.setForeground(text_color)
            item_author.setFont(f)
            item_author.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(row_idx, 4, item_author)

            # 5: 상태 (알약 뱃지 버튼 클릭 드롭다운)
            item_status = QTableWidgetItem()
            item_status.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row_idx, 5, item_status)
            status_str = task.status or "등록"
            status_widget = PillBadgeWidget(
                status_str,
                badge_status_cfg,
                lambda btn, t=task, r=row_idx: self._show_status_menu(t, btn, r),
                on_context=lambda pos, t=task, r=row_idx: self._show_context_menu_for_task(t, pos, r),
                plain_color=self.palette.get("muted", "#94A3B8") if is_done else self.palette.get("text", "#2D3748"),
                plain_hover=self.palette.get("line", "#CBD5E1"),
            )
            status_widget.setProperty("row_idx", row_idx)
            status_widget.btn.setProperty("row_idx", row_idx)
            status_widget.installEventFilter(self)
            status_widget.btn.installEventFilter(self)
            self.table.setCellWidget(row_idx, 5, status_widget)

            # 6: 비고 / 세부내용 (직접 편집 가능)
            desc_preview = desc_body.replace("\n", "  ").strip()
            item_desc = QTableWidgetItem(desc_preview)
            item_desc.setToolTip(desc_body)
            item_desc.setForeground(text_color)
            item_desc.setFont(f)
            item_desc.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(row_idx, 6, item_desc)

            # 7: 편집 (아이콘 클릭 시 수정 창 열기)
            item_edit = QTableWidgetItem()
            item_edit.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row_idx, 7, item_edit)

            btn_edit = QPushButton("✏️")
            btn_edit.setToolTip("업무 상세 수정")
            btn_edit.setCursor(Qt.PointingHandCursor)
            btn_edit.setFixedSize(26, 26)
            panel_alt = self.palette.get("panel_alt", "#F8FAFC")
            line = self.palette.get("line", "#CBD5E0")
            line_soft = self.palette.get("line_soft", "#E2E8F0")
            btn_edit.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    font-size: 13px;
                    padding: 0px;
                }}
                QPushButton:hover {{
                    background-color: {panel_alt};
                    border: 1px solid {line};
                }}
                QPushButton:pressed {{
                    background-color: {line_soft};
                }}
            """)
            btn_edit.clicked.connect(lambda _, t=task: self._edit_task(t))

            edit_container = QWidget()
            edit_container.setAttribute(Qt.WA_TranslucentBackground, True)
            edit_container.setStyleSheet("background: transparent;")
            edit_layout = QHBoxLayout(edit_container)
            edit_layout.setContentsMargins(0, 0, 0, 0)
            edit_layout.setAlignment(Qt.AlignCenter)
            edit_layout.addWidget(btn_edit)

            edit_container.setProperty("row_idx", row_idx)
            btn_edit.setProperty("row_idx", row_idx)
            edit_container.installEventFilter(self)
            btn_edit.installEventFilter(self)

            self.table.setCellWidget(row_idx, 7, edit_container)

            # 9: 회신기한 (등록일자처럼 날짜, 직접 수정 가능 — 비우면 회신기한 없앰)
            reply_due = getattr(task, "reply_due", None)
            dday_text, reply_level, reply_tip = reply_due_info(
                reply_due, getattr(task, "reply_time", "") or "", is_done, today
            )
            item_reply = QTableWidgetItem(reply_due.strftime("%Y-%m-%d") if reply_due else "")
            item_reply.setTextAlignment(Qt.AlignCenter)
            item_reply.setForeground(text_color)
            item_reply.setFont(f)
            item_reply.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            item_reply.setToolTip(reply_tip or "더블클릭해서 회신기한 입력 (예: 2026-11-30)")
            self.table.setItem(row_idx, self.COL_REPLY, item_reply)

            # 10: D-day (남은 날 색 알약: D-3 · D-day · D+1)
            item_dday = QTableWidgetItem(dday_text)
            item_dday.setTextAlignment(Qt.AlignCenter)
            item_dday.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            if dday_text:
                item_dday.setData(DOC_REPLY_ROLE, (dday_text, reply_level))
                item_dday.setToolTip(reply_tip)
            self.table.setItem(row_idx, self.COL_DDAY, item_dday)

            # 8: 체크박스 (화면 맨 왼쪽, 일괄 처리 대상 선택)
            item_check = QTableWidgetItem()
            item_check.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.table.setItem(row_idx, self.COL_CHECK, item_check)

            chk = QCheckBox()
            chk.setCursor(Qt.PointingHandCursor)
            chk.setChecked(task.entry_id in self._checked_ids)
            chk.toggled.connect(lambda checked, t=task: self._on_row_check_toggled(t, checked))

            check_container = QWidget()
            check_container.setAttribute(Qt.WA_TranslucentBackground, True)
            check_container.setStyleSheet("background: transparent;")
            check_layout = QHBoxLayout(check_container)
            check_layout.setContentsMargins(0, 0, 0, 0)
            check_layout.setAlignment(Qt.AlignCenter)
            check_layout.addWidget(chk)

            check_container.setProperty("row_idx", row_idx)
            chk.setProperty("row_idx", row_idx)
            check_container.installEventFilter(self)
            chk.installEventFilter(self)
            self.table.setCellWidget(row_idx, self.COL_CHECK, check_container)

            self.table.setRowHeight(row_idx, 38)

        self.table.blockSignals(False)
        self._refresh_check_ui()

    def _on_row_check_toggled(self, task: CalendarEntry, checked: bool) -> None:
        if not task.entry_id:
            return
        if checked:
            self._checked_ids.add(task.entry_id)
        else:
            self._checked_ids.discard(task.entry_id)
        self._refresh_check_ui()

    def _toggle_check_all_displayed(self) -> None:
        """헤더 체크박스 클릭: 현재 화면의 문서를 모두 체크 / 모두 해제"""
        ids = {t.entry_id for t in self._displayed_tasks if t.entry_id}
        if not ids:
            return
        if ids <= self._checked_ids:
            self._checked_ids -= ids
        else:
            self._checked_ids |= ids
        self._render_table()

    def _clear_checked(self) -> None:
        self._checked_ids.clear()
        self._render_table()

    def _get_checked_tasks(self) -> list[CalendarEntry]:
        return [t for t in self._filtered_tasks if t.entry_id in self._checked_ids]

    def _refresh_check_ui(self) -> None:
        """체크 개수에 맞춰 일괄 처리 바와 헤더 전체선택 표시 갱신"""
        count = len(self._get_checked_tasks())
        self.bulk_bar.setVisible(count > 0)
        self.lbl_bulk_count.setText(f"{count}개 선택")
        displayed = self._displayed_tasks
        all_checked = bool(displayed) and all(t.entry_id in self._checked_ids for t in displayed)
        self.header_check.blockSignals(True)
        self.header_check.setChecked(all_checked)
        self.header_check.blockSignals(False)

    def _position_header_check(self, *args) -> None:
        """머리글 체크박스를 체크박스 열 머리글 칸 가운데에 맞춤"""
        header = self.table.horizontalHeader()
        hint = self.header_check.sizeHint()
        x = header.sectionViewportPosition(self.COL_CHECK) + (header.sectionSize(self.COL_CHECK) - hint.width()) // 2
        y = (header.height() - hint.height()) // 2
        self.header_check.setGeometry(x, y, hint.width(), hint.height())

    def _bulk_change_status(self, new_status: str) -> None:
        tasks = self._get_checked_tasks()
        if not tasks:
            return
        self._checked_ids.clear()
        self._change_tasks_status(tasks, new_status)

    def _bulk_change_category(self, new_cat: str) -> None:
        tasks = self._get_checked_tasks()
        if not tasks:
            return
        self._checked_ids.clear()
        self._change_tasks_category(tasks, new_cat)

    def _show_bulk_status_menu(self) -> None:
        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        badge_status_cfg = STATUS_CONFIG_DARK if is_dark else STATUS_CONFIG
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        actions = {}
        for s in get_task_statuses(self.repository):
            icon = badge_status_cfg.get(s, {}).get("icon", "▫️")
            actions[menu.addAction(f"{s}")] = s
        chosen = menu.exec(self.btn_bulk_status.mapToGlobal(QPoint(0, self.btn_bulk_status.height() + 2)))
        if chosen in actions:
            self._bulk_change_status(actions[chosen])

    def _show_bulk_category_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        actions = {}
        for c in get_task_categories(self.repository):
            actions[menu.addAction(f"{c}")] = c
        chosen = menu.exec(self.btn_bulk_category.mapToGlobal(QPoint(0, self.btn_bulk_category.height() + 2)))
        if chosen in actions:
            self._bulk_change_category(actions[chosen])

    def _bulk_link_work(self) -> None:
        tasks = self._get_checked_tasks()
        if tasks:
            self._link_tasks_to_work(tasks)

    def _bulk_delete(self) -> None:
        # 삭제된 문서는 _apply_filters에서 체크 목록에서도 자동으로 빠진다
        tasks = self._get_checked_tasks()
        if tasks:
            self._delete_tasks(tasks)

    def _update_stats_label(self) -> None:
        """상태 탭 카운트 갱신 (숫자 있는 것만 표기)"""
        total_cnt = len(self._all_tasks)
        reg_cnt = sum(1 for t in self._all_tasks if (t.status or "등록") == "등록")
        prog_cnt = sum(1 for t in self._all_tasks if t.status in ("진행", "진행중"))
        done_cnt = sum(1 for t in self._all_tasks if t.status == "완료")

        self.btn_tab_all.setText(f"전체  {total_cnt}" if total_cnt > 0 else "전체")
        self.btn_tab_reg.setText(f"등록  {reg_cnt}")
        self.btn_tab_prog.setText(f"진행  {prog_cnt}")
        self.btn_tab_done.setText(f"완료  {done_cnt}")

        # 숫자(건수)가 있는 탭만 표기 (전체는 기본 표시)
        self.btn_tab_all.setVisible(True)
        self.btn_tab_reg.setVisible(reg_cnt > 0)
        self.btn_tab_prog.setVisible(prog_cnt > 0)
        self.btn_tab_done.setVisible(done_cnt > 0)

        # 현재 활성화된 탭이 0건이 되어 숨겨진 경우 전체 탭으로 자동 전환
        if self._current_tab == "reg" and reg_cnt == 0:
            self._set_active_tab("all")
        elif self._current_tab == "prog" and prog_cnt == 0:
            self._set_active_tab("all")
        elif self._current_tab == "done" and done_cnt == 0:
            self._set_active_tab("all")
        else:
            self._update_tab_styles()

    def _update_row_appearance(self, row: int, task: CalendarEntry) -> None:
        """완료 상태 변경 시 해당 행 글자 스타일(취소선/색상) 및 상태 뱃지 즉시 갱신"""
        is_done = (task.status == "완료")
        text_color = QColor(self.palette.get("muted", "#A0AEC0")) if is_done else QColor(self.palette.get("text", "#2D3748"))
        row_font = make_ui_font_like(10)
        f = QFont(row_font)
        if is_done:
            f.setStrikeOut(True)

        self.table.blockSignals(True)
        try:
            for col in (0, 2, 3, 4, 6):
                it = self.table.item(row, col)
                if it:
                    it.setFont(f)
                    it.setForeground(text_color)
        finally:
            self.table.blockSignals(False)

        w_status = self.table.cellWidget(row, 5)
        if isinstance(w_status, PillBadgeWidget):
            is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
            badge_status_cfg = STATUS_CONFIG_DARK if is_dark else STATUS_CONFIG
            w_status.update_badge(task.status or "등록", badge_status_cfg)

    def _parse_user_date(self, text: str) -> date | None:
        """사용자가 입력한 문자열을 날짜 객체로 변환 (YYYY-MM-DD, YYYY.MM.DD, YYYYMMDD 등 지원)"""
        clean = text.strip().replace(".", "-").replace("/", "-")
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%y-%m-%d"):
            try:
                return datetime.strptime(clean, fmt).date()
            except ValueError:
                pass
        return None

    def _on_reply_cell_edited(self, row: int, item: QTableWidgetItem) -> None:
        """회신기한 칸 직접 수정: 날짜면 바꾸고, 비우면 회신기한을 없앤다. 회신 일정도 맞춘다."""
        task = self._displayed_tasks[row]
        text = item.text().strip()
        old_due = getattr(task, "reply_due", None)
        if not text or text == "-":
            new_due = None
        else:
            new_due = self._parse_user_date(text)
            if new_due is None:
                self.table.blockSignals(True)
                item.setText(old_due.strftime("%Y-%m-%d") if old_due else "")
                self.table.blockSignals(False)
                QMessageBox.warning(self, "날짜 형식 오류", "회신기한은 'YYYY-MM-DD' 형식으로 입력해주세요.\n(예: 2026-11-30, 비우면 회신기한 없음)")
                return
        if new_due == old_due:
            self.table.blockSignals(True)
            item.setText(old_due.strftime("%Y-%m-%d") if old_due else "")
            self.table.blockSignals(False)
            return

        had_schedule = False
        try:
            had_schedule = bool(task.entry_id and self.repository.get_reply_schedule(task.entry_id))
        except Exception:
            pass
        task.reply_due = new_due
        if not new_due:
            task.reply_time = ""
        task.updated_at = datetime.now()
        self.repository.upsert_entry(task)
        try:
            auto = doc_reply.load_settings(self.repository).get("doc_reply_auto_calendar", "1") != "0"
            self.repository.set_reply_schedule(task, enabled=bool(new_due) and (had_schedule or auto))
        except Exception:
            logger.exception("reply schedule sync failed")
        self.repository.save()
        QTimer.singleShot(0, self._render_table)
        if self.main_window and hasattr(self.main_window, "refresh"):
            QTimer.singleShot(0, self.main_window.refresh)

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        """테이블 셀 내에서 직접 텍스트 수정 시 실시간 저장"""
        row = item.row()
        col = item.column()
        if row < 0 or row >= len(self._displayed_tasks):
            return
        if col == self.COL_REPLY:
            self._on_reply_cell_edited(row, item)
            return
        if col not in (0, 2, 3, 4, 6):
            return

        task = self._displayed_tasks[row]
        new_val = item.text().strip()
        now = datetime.now()

        if col == 0:  # 등록일자
            parsed = self._parse_user_date(new_val)
            if parsed is None:
                self.table.blockSignals(True)
                d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
                item.setText(d_val.strftime("%Y-%m-%d") if d_val else "-")
                self.table.blockSignals(False)
                QMessageBox.warning(self, "날짜 형식 오류", "날짜는 'YYYY-MM-DD' 형식으로 입력해주세요.\n(예: 2026-09-13)")
                return
            if task.day == parsed and task.start_date == parsed:
                return
            task.day = parsed
            task.start_date = parsed
            if task.created_at:
                task.created_at = datetime.combine(parsed, task.created_at.time())
            else:
                task.created_at = datetime.combine(parsed, now.time())
            task.updated_at = now
            self.table.blockSignals(True)
            item.setText(parsed.strftime("%Y-%m-%d"))
            self.table.blockSignals(False)

        elif col == 2:  # 업무 제목
            if not new_val:
                self.table.blockSignals(True)
                item.setText(task.title or "")
                self.table.blockSignals(False)
                QMessageBox.warning(self, "입력 오류", "업무 제목은 비워둘 수 없습니다.")
                return
            if (task.title or "") == new_val:
                return
            task.title = new_val
            task.updated_at = now

        elif col == 3:  # 부서
            if (getattr(task, "department", "") or "") == new_val:
                return
            task.department = new_val
            task.updated_at = now

        elif col == 4:  # 기안자 / 작성자
            if (task.assignee or "") == new_val:
                return
            task.assignee = new_val
            task.updated_at = now

        elif col == 6:  # 비고 / 세부내용 (목록에는 출처를 떼고 보여 주므로 저장 때 다시 붙임)
            old_body, source_url = split_doc_source(task.description or "")
            new_body = item.text().strip()
            if old_body.replace("\n", "  ").strip() == new_body:
                return
            task.description = join_doc_source(new_body, source_url)
            item.setToolTip(new_body)
            task.updated_at = now

        self.repository.upsert_entry(task)
        self.repository.save()
        if self.main_window and hasattr(self.main_window, "refresh"):
            QTimer.singleShot(0, self.main_window.refresh)

    def _on_category_cell_changed(self, task: CalendarEntry, new_cat: str) -> None:
        """셀 내 드롭다운에서 분류 변경 시 즉시 저장"""
        if task.memo_group == new_cat:
            return
        task.memo_group = new_cat
        task.updated_at = datetime.now()
        self.repository.upsert_entry(task)
        self.repository.save()
        if self.main_window and hasattr(self.main_window, "refresh"):
            QTimer.singleShot(0, self.main_window.refresh)

    def _on_status_cell_changed(self, task: CalendarEntry, new_status: str, row_idx: int) -> None:
        """셀 내 드롭다운에서 상태 변경 시 즉시 저장 및 스타일 반영"""
        if task.status == new_status:
            return
        task.status = new_status
        task.updated_at = datetime.now()
        self.repository.upsert_entry(task)
        self.repository.save()
        if self.main_window and hasattr(self.main_window, "refresh"):
            QTimer.singleShot(0, self.main_window.refresh)
        self._update_stats_label()
        self._update_row_appearance(row_idx, task)

    def _get_selected_tasks(self) -> list[CalendarEntry]:
        """현재 테이블에서 선택된 업무 목록 반환"""
        selected_indexes = self.table.selectionModel().selectedRows()
        selected_rows = sorted({idx.row() for idx in selected_indexes})
        return [
            self._displayed_tasks[r]
            for r in selected_rows
            if 0 <= r < len(self._displayed_tasks)
        ]

    def _show_context_menu(self, pos: QPoint) -> None:
        """테이블 우클릭 시 컨텍스트 메뉴 표시"""
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self._displayed_tasks):
            return

        selected_rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        if row not in selected_rows:
            self.table.clearSelection()
            self.table.selectRow(row)

        tasks = self._get_selected_tasks()
        if not tasks:
            tasks = [self._displayed_tasks[row]]

        global_pos = self.table.viewport().mapToGlobal(pos)
        self._show_context_menu_for_tasks(tasks, global_pos)

    def _show_context_menu_for_task(self, task: CalendarEntry, global_pos: QPoint, row_idx: int | None = None) -> None:
        """단일 또는 다중 업무에 대한 컨텍스트 메뉴 팝업"""
        selected_rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        if row_idx is not None and row_idx not in selected_rows:
            self.table.clearSelection()
            self.table.selectRow(row_idx)

        tasks = self._get_selected_tasks()
        if not tasks or task not in tasks:
            tasks = [task]

        self._show_context_menu_for_tasks(tasks, global_pos)

    def _show_context_menu_for_tasks(self, tasks: list[CalendarEntry], global_pos: QPoint) -> None:
        """단일 또는 다중 선택 업무 컨텍스트 메뉴 팝업"""
        if not tasks:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())
        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        badge_status_cfg = STATUS_CONFIG_DARK if is_dark else STATUS_CONFIG

        if len(tasks) == 1:
            task = tasks[0]
            act_edit = menu.addAction("상세 수정 창 열기")
            menu.addSeparator()

            # 관련 업무 연결 (문서 ↔ 업무 양방향)
            act_link_work = menu.addAction("관련 업무 연결...")
            menu.addSeparator()

            # 단일 항목 분류 변경 서브메뉴
            cats = get_task_categories(self.repository)
            menu_cat = menu.addMenu("분류 변경")
            menu_cat.setStyleSheet(self._menu_stylesheet())
            actions_cat = {}
            for c in cats:
                prefix = "✓ " if (task.memo_group or "일반") == c else "   "
                act = menu_cat.addAction(f"{prefix}{c}")
                actions_cat[act] = c

            # 단일 항목 상태 변경 서브메뉴
            statuses = get_task_statuses(self.repository)
            menu_stat = menu.addMenu("상태 변경")
            menu_stat.setStyleSheet(self._menu_stylesheet())
            actions_stat = {}
            for s in statuses:
                icon = badge_status_cfg.get(s, {}).get("icon", "▫️")
                prefix = "✓ " if (task.status or "등록") == s else "   "
                act = menu_stat.addAction(f"{prefix}{s}")
                actions_stat[act] = s

            menu.addSeparator()

            urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', task.description or "")
            act_url = None
            if urls:
                act_url = menu.addAction("출처 웹페이지 열기")
                menu.addSeparator()

            act_del = menu.addAction("업무 삭제")

            action = menu.exec(global_pos)
            if action == act_edit:
                self._edit_task(task)
            elif action == act_link_work:
                self._link_task_to_work(task)
            elif action in actions_cat:
                self._change_tasks_category([task], actions_cat[action])
            elif action in actions_stat:
                self._change_tasks_status([task], actions_stat[action])
            elif act_url and action == act_url:
                url_str = urls[0]
                if not url_str.startswith("http"):
                    url_str = "https://" + url_str
                QDesktopServices.openUrl(QUrl(url_str))
            elif action == act_del:
                self._delete_task(task)
        else:
            count = len(tasks)

            # 다중 항목 분류 일괄 변경 서브메뉴
            cats = get_task_categories(self.repository)
            menu_cat = menu.addMenu(f"선택한 업무 분류 일괄 변경 ({count}개)")
            menu_cat.setStyleSheet(self._menu_stylesheet())
            actions_cat = {}
            for c in cats:
                act = menu_cat.addAction(f"{c}")
                actions_cat[act] = c

            # 다중 항목 상태 일괄 변경 서브메뉴
            statuses = get_task_statuses(self.repository)
            menu_stat = menu.addMenu(f"선택한 업무 상태 일괄 변경 ({count}개)")
            menu_stat.setStyleSheet(self._menu_stylesheet())
            actions_stat = {}
            for s in statuses:
                icon = badge_status_cfg.get(s, {}).get("icon", "▫️")
                act = menu_stat.addAction(f"{s}")
                actions_stat[act] = s

            menu.addSeparator()

            act_link_work = menu.addAction(f"관련 업무 일괄 연결 ({count}개)...")
            menu.addSeparator()
            act_del = menu.addAction(f"선택한 업무 일괄 삭제 ({count}개)")

            action = menu.exec(global_pos)
            if action == act_link_work:
                self._link_tasks_to_work(tasks)
            elif action in actions_cat:
                self._change_tasks_category(tasks, actions_cat[action])
            elif action in actions_stat:
                self._change_tasks_status(tasks, actions_stat[action])
            elif action == act_del:
                self._delete_tasks(tasks)

    def _link_task_to_work(self, task: CalendarEntry) -> None:
        """단일 문서의 관련 업무를 선택해 연결한다."""
        if not task or not task.entry_id:
            return
        self._open_work_link_for_entry(task.entry_id)

    def _link_tasks_to_work(self, tasks: list[CalendarEntry]) -> None:
        """선택한 여러 문서에 동일한 업무를 한 번에 연결한다."""
        ids = [t.entry_id for t in tasks if t.entry_id]
        if not ids:
            return
        self._open_work_link_for_entry(ids[0], extra_entry_ids=ids[1:])

    def _open_work_link_for_entry(
        self,
        entry_id: int,
        extra_entry_ids: list[int] | None = None,
    ) -> None:
        """업무 선택 다이얼로그로 연결하고 저장, 업무 관리 쪽 목록도 갱신한다."""
        from taskcalendar.work_link_dialog import WorkLinkSelectDialog

        try:
            initial = list(self.repository.list_linked_works_for_entry(entry_id))
        except Exception:
            initial = []

        dlg = WorkLinkSelectDialog(
            self,
            self.repository,
            initial_work_ids=initial,
            palette=self.palette,
            title=(
                f"{len(extra_entry_ids) + 1}개 문서에 업무 연결"
                if extra_entry_ids else "문서와 연결할 업무 선택"
            ),
        )
        if dlg.exec() != QDialog.Accepted:
            return

        work_ids = list(dlg.selected_work_ids)
        targets = [entry_id] + list(extra_entry_ids or [])
        for eid in targets:
            try:
                self.repository.set_entry_work_links(eid, work_ids)
            except Exception:
                logger.warning("Failed to link entry to works", exc_info=True)
        self.repository.save()

        # 업무 관리 창이 열려 있으면 연결된 문서 목록을 갱신한다.
        try:
            mw = self.main_window
            wm_dialog = getattr(mw, "_work_manager_dialog", None) if mw else None
            if wm_dialog is not None and wm_dialog.isVisible():
                for sheet in getattr(wm_dialog, "_all_sheets", []):
                    if sheet.db_id in work_ids:
                        wm_dialog._refresh_work_documents_list(sheet)
        except Exception:
            logger.debug("Failed to refresh work manager documents", exc_info=True)

        if getattr(self, "main_window", None) is not None:
            self.main_window.refresh()

    def _change_tasks_category(self, tasks: list[CalendarEntry], new_cat: str) -> None:
        """선택된 업무들의 분류를 일괄 변경 및 저장"""
        if not tasks:
            return
        now = datetime.now()
        selected_ids = {t.entry_id for t in tasks if t.entry_id}
        for task in tasks:
            task.memo_group = new_cat
            task.updated_at = now
            self.repository.upsert_entry(task)
        self.repository.save()
        if self.main_window and hasattr(self.main_window, "refresh"):
            self.main_window.refresh()
        self._refresh_filter_categories()
        self.reload_tasks()
        self._restore_selection_by_ids(selected_ids)

    def _change_tasks_status(self, tasks: list[CalendarEntry], new_status: str) -> None:
        """선택된 업무들의 상태를 일괄 변경 및 저장"""
        if not tasks:
            return
        now = datetime.now()
        selected_ids = {t.entry_id for t in tasks if t.entry_id}
        for task in tasks:
            task.status = new_status
            task.updated_at = now
            self.repository.upsert_entry(task)
        self.repository.save()
        if self.main_window and hasattr(self.main_window, "refresh"):
            self.main_window.refresh()
        self._refresh_filter_statuses()
        self.reload_tasks()
        self._restore_selection_by_ids(selected_ids)

    def _restore_selection_by_ids(self, ids: set[str]) -> None:
        """ID 집합에 해당하는 행들의 선택 상태 복원"""
        if not ids:
            return
        self.table.clearSelection()
        for r, t in enumerate(self._displayed_tasks):
            if t.entry_id in ids:
                self.table.selectionModel().select(
                    self.table.model().index(r, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )

    def select_and_highlight_task(self, entry_id: str | None) -> bool:
        """특정 업무(엔트리 ID)를 테이블에서 찾아 선택하고 화면에 보이도록 스크롤.

        다른 창(업무 관리 -> 관련 문서)에서 넘어올 때 호출된다. 사용자가 갖고 있던
        검색어/상태/기간/분류 필터나 페이징 때문에 대상 업무가 목록에 없으면 아무것도
        안 보이므로, 그 경우에만 필터를 전체로 되돌린 뒤 다시 시도한다.
        """
        if not entry_id:
            return False

        if not self._reveal_task_row(entry_id):
            self._reset_filters_for_reveal()
            self._apply_filters()
            if not self._reveal_task_row(entry_id):
                return False

        return True

    def _reveal_task_row(self, entry_id: str) -> bool:
        """현재 표시 중인 목록에서 entry_id 행을 선택. 있으면 True."""
        for r, t in enumerate(self._displayed_tasks):
            if t.entry_id == entry_id:
                self.table.clearSelection()
                self.table.selectRow(r)
                self.table.setCurrentCell(r, 0)
                self.table.scrollToItem(
                    self.table.item(r, 0), QAbstractItemView.ScrollHint.PositionAtCenter
                )
                return True
        return False

    def _reset_filters_for_reveal(self) -> None:
        """특정 업무를 보여주기 위해 필터를 모두 전체로 되돌린다."""
        self._set_active_tab("all")
        self.period_combo.blockSignals(True)
        idx = self.period_combo.findText("기간")
        if idx >= 0:
            self.period_combo.setCurrentIndex(idx)
        self.period_combo.blockSignals(False)
        # '기간'은 전체 범위지만 날짜 편집기에 이전 기간 값이 남아 있을 수 있어 초기화한다.
        self.start_date_edit.blockSignals(True)
        self.end_date_edit.blockSignals(True)
        self.start_date_edit.setDate(QDate(2000, 1, 1))
        self.end_date_edit.setDate(QDate(2100, 12, 31))
        self.start_date_edit.blockSignals(False)
        self.end_date_edit.blockSignals(False)
        self.category_combo.blockSignals(True)
        c_idx = self.category_combo.findText("분류")
        if c_idx >= 0:
            self.category_combo.setCurrentIndex(c_idx)
        self.category_combo.blockSignals(False)
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if hasattr(self, "paging_combo"):
            self.paging_combo.blockSignals(True)
            p_idx = self.paging_combo.findText("전체")
            if p_idx >= 0:
                self.paging_combo.setCurrentIndex(p_idx)
            self.paging_combo.blockSignals(False)
        self._current_page = 1
        self._current_date_anchor = None

    def _on_add_task(self) -> None:
        """새 업무 등록 (전용 TaskEditDialog 호출)"""
        self._edit_task(None)

    def _edit_task(self, task: CalendarEntry | None) -> None:
        """업무 상세 편집 다이얼로그 열기 (기안자 자동완성 포함)"""
        known_authors = [t.assignee for t in self._all_tasks if t.assignee]
        known_depts = [t.department for t in self._all_tasks if getattr(t, "department", "")]
        dlg = TaskEditDialog(self, self.repository, task, known_authors, palette=self.palette, known_depts=known_depts)
        if dlg.exec() == QDialog.Accepted:
            if self.main_window and hasattr(self.main_window, "refresh"):
                QTimer.singleShot(0, self.main_window.refresh)
            self._refresh_filter_categories()
            self._refresh_filter_statuses()
            self.reload_tasks()

    def _delete_task(self, task: CalendarEntry) -> None:
        """단일 업무 삭제 확인 및 수행"""
        reply = QMessageBox.question(
            self,
            "삭제 확인",
            f"'{task.title}' 업무를 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes and task.entry_id:
            self.repository.delete_entry(task.entry_id)
            self.repository.save()
            if self.main_window and hasattr(self.main_window, "refresh"):
                self.main_window.refresh()
            self.reload_tasks()

    def _delete_tasks(self, tasks: list[CalendarEntry]) -> None:
        """단일 또는 복수 업무 삭제 확인 및 일괄 수행"""
        if not tasks:
            return
        if len(tasks) == 1:
            self._delete_task(tasks[0])
            return

        count = len(tasks)
        reply = QMessageBox.question(
            self,
            "일괄 삭제 확인",
            f"선택한 {count}개의 업무를 모두 삭제하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            for task in tasks:
                if task.entry_id:
                    self.repository.delete_entry(task.entry_id)
            self.repository.save()
            if self.main_window and hasattr(self.main_window, "refresh"):
                self.main_window.refresh()
            self.reload_tasks()

    def _on_delete_selected(self) -> None:
        """선택된 업무 삭제 핸들러 (단일 및 다중 선택 지원)"""
        tasks = self._get_selected_tasks()
        if not tasks:
            selected_row = self.table.currentRow()
            if 0 <= selected_row < len(self._displayed_tasks):
                tasks = [self._displayed_tasks[selected_row]]
        if not tasks:
            QMessageBox.information(self, "안내", "삭제할 업무를 선택해 주세요.")
            return

        self._delete_tasks(tasks)

    def _export_to_excel(self) -> None:
        """업무 목록 내보내기 대화상자 (공문 맞춤형 / 엑셀) 실행"""
        if not self._filtered_tasks:
            QMessageBox.information(self, "안내", "내보낼 업무 항목이 없습니다.")
            return

        dlg = TaskExportDialog(self, self.repository, self._filtered_tasks, palette=self.palette)
        dlg.exec()


def format_korean_sequence(idx: int, style: str) -> str:
    """순번 기호 서식 변환 헬퍼 (행정업무 규정에 따른 한글/숫자/원문자 순번 생성)"""
    korean_chars = [
        "가", "나", "다", "라", "마", "바", "사", "아", "자", "차", "카", "타", "파", "하",
        "거", "너", "더", "러", "머", "버", "서", "어", "저", "처", "커", "터", "퍼", "허",
        "고", "노", "도", "로", "모", "보", "소", "오", "조", "초", "코", "토", "포", "호"
    ]
    circled_nums = [
        "①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩",
        "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳"
    ]
    if style in ("가, 나, 다...", "가"):
        return korean_chars[idx - 1] if 1 <= idx <= len(korean_chars) else f"가{idx - len(korean_chars)}"
    elif style == "가.":
        k = korean_chars[idx - 1] if 1 <= idx <= len(korean_chars) else f"가{idx - len(korean_chars)}"
        return f"{k}."
    elif style == "가)":
        k = korean_chars[idx - 1] if 1 <= idx <= len(korean_chars) else f"가{idx - len(korean_chars)}"
        return f"{k})"
    elif style in ("1, 2, 3...", "1"):
        return str(idx)
    elif style == "1.":
        return f"{idx}."
    elif style == "1)":
        return f"{idx})"
    elif style == "(1)":
        return f"({idx})"
    elif style in ("①, ②, ③...", "①"):
        return circled_nums[idx - 1] if 1 <= idx <= len(circled_nums) else f"({idx})"
    elif style == "○":
        return "○"
    elif style in ("- (대시)", "-"):
        return "-"
    elif style == "없음":
        return ""
    return str(idx)


class TaskExportDialog(QDialog):
    """
    공문 맞춤형 및 엑셀 내보내기 대화상자:
    1. 공문 본문 나열형 (가. 부서 제목... 한 줄 텍스트 클립보드 복사 / 엑셀 저장)
    2. 공문 붙임 표 형식 (표 헤더명/컬럼 구성 커스텀, 한글/엑셀 즉시 붙여넣기용 TSV 복사, 엑셀 표 저장)
    3. 표준 업무관리대장 (전체 8개 열 기본 서식)
    """

    def __init__(
        self,
        parent: QWidget | None,
        repository: EncryptedRepository,
        tasks: list[CalendarEntry],
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.tasks = tasks
        self.palette = palette or get_dialog_palette(parent, repository)

        self.setWindowTitle("문서 목록 내보내기 (공문 맞춤형 / 엑셀)")
        self.resize(840, 640)
        self.setMinimumSize(720, 520)
        # font-family 명시: 상속되지 않으면 캘린더·업무 화면과 달리 글자가 깨진다.
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.palette['bg']};
                color: {self.palette['text']};
                font-family: {font_family_css()};
            }}
            QLineEdit, QComboBox, QTextEdit, QPushButton, QLabel,
            QTableWidget, QListWidget, QCheckBox, QRadioButton {{
                font-family: {font_family_css()};
            }}
        """)

        self._available_columns = [
            ("seq", "순번", "순번", True),
            ("dept", "부서", "관련근거", True),
            ("title", "업무제목", "처리내역 / 자료명", True),
            ("cat", "분류", "처리구분", True),
            ("author", "기안자", "요청자", True),
            ("desc", "비고", "비고", True),
            ("date", "등록일자", "등록일자", False),
            ("status", "상태", "처리상태", False),
        ]

        self._init_ui()
        self._load_saved_config()
        self._update_list_preview()
        self._update_table_preview()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        accent_soft = self.palette.get("accent_soft", "#EEF2FF")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                padding: 12px;
            }}
            QTabBar::tab {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {panel};
                color: {accent};
                border-bottom: 2px solid {accent};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {_shade(panel_alt, -0.04)};
            }}
        """)

        # Tab 1: 공문 본문 나열형
        tab_list = QWidget()
        self._setup_list_tab(tab_list)
        self.tab_widget.addTab(tab_list, "공문 본문 나열형")

        # Tab 2: 공문 붙임 표 형식
        tab_table = QWidget()
        self._setup_table_tab(tab_table)
        self.tab_widget.addTab(tab_table, "공문 붙임 표 형식")

        # Tab 3: 표준 업무관리대장
        tab_standard = QWidget()
        self._setup_standard_tab(tab_standard)
        self.tab_widget.addTab(tab_standard, "표준 업무관리대장")

        layout.addWidget(self.tab_widget, 1)

    # ------------------ Tab 1: 공문 본문 나열형 ------------------
    def _setup_list_tab(self, parent_widget: QWidget) -> None:
        vbox = QVBoxLayout(parent_widget)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(10)

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        # 상단 안내 배너
        lbl_guide = QLabel("<b>공문 본문 작성 시 바로 붙여넣거나(Ctrl+V)</b> 엑셀로 저장할 수 있도록 서식에 맞춰 한 줄씩 나열합니다.")
        lbl_guide.setStyleSheet(f"background: {panel_alt}; border: 1px solid {line}; border-radius: 6px; padding: 8px 10px; font-size: 12px; color: {text};")
        vbox.addWidget(lbl_guide)

        # 설정 그리드
        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet(f"QFrame {{ background: {panel_alt}; border: 1px solid {line}; border-radius: 6px; }}")
        ctrl_layout = QVBoxLayout(ctrl_frame)
        ctrl_layout.setContentsMargins(10, 8, 10, 8)
        ctrl_layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)

        lbl_seq = QLabel("순번 기호:")
        lbl_seq.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text};")
        row1.addWidget(lbl_seq)

        self.combo_list_seq = QComboBox()
        self.combo_list_seq.setFixedHeight(28)
        self.combo_list_seq.addItems(["가, 나, 다...", "1, 2, 3...", "①, ②, ③...", "가)", "1)", "(1)", "○", "- (대시)", "없음"])
        self.combo_list_seq.currentIndexChanged.connect(self._update_list_preview)
        row1.addWidget(self.combo_list_seq)

        row1.addSpacing(14)

        lbl_tpl = QLabel("나열 템플릿:")
        lbl_tpl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text};")
        row1.addWidget(lbl_tpl)

        self.input_list_template = QLineEdit()
        self.input_list_template.setFixedHeight(28)
        self.input_list_template.setText("{순번}. {부서} {제목}")
        self.input_list_template.textChanged.connect(self._update_list_preview)
        row1.addWidget(self.input_list_template, 1)

        ctrl_layout.addLayout(row1)

        # 치환 태그 칩스
        chips_row = QHBoxLayout()
        chips_row.setSpacing(4)
        lbl_chips = QLabel("치환 태그:")
        lbl_chips.setStyleSheet(f"font-size: 11px; color: {text}; font-weight: bold;")
        chips_row.addWidget(lbl_chips)

        tags = ["{순번}", "{부서}", "{제목}", "{기안자}", "{분류}", "{상태}", "{일자}", "{비고}"]
        chip_style = f"""
            QPushButton {{
                background: {panel};
                color: {accent};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 7px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: {accent};
                color: {btn_text};
                border-color: {accent};
            }}
        """
        for t in tags:
            btn_t = QPushButton(t)
            btn_t.setStyleSheet(chip_style)
            btn_t.setCursor(Qt.PointingHandCursor)
            btn_t.clicked.connect(lambda _, tag=t: self._insert_list_tag(tag))
            chips_row.addWidget(btn_t)
        chips_row.addStretch(1)
        ctrl_layout.addLayout(chips_row)

        vbox.addWidget(ctrl_frame)

        # 실시간 미리보기
        self.lbl_list_preview_title = QLabel(f"실시간 본문 미리보기 (총 {len(self.tasks)}건)")
        self.lbl_list_preview_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text}; margin-top: 4px;")
        vbox.addWidget(self.lbl_list_preview_title)

        self.txt_list_preview = QTextEdit()
        self.txt_list_preview.setReadOnly(True)
        self.txt_list_preview.setStyleSheet(f"""
            QTextEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 10px;
                font-family: {font_family_css()}, monospace;
                font-size: 12px;
                line-height: 1.5;
            }}
        """)
        vbox.addWidget(self.txt_list_preview, 1)

        # 하단 액션 버튼
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.lbl_list_status = QLabel("")
        self.lbl_list_status.setStyleSheet("color: #10B981; font-weight: bold; font-size: 12px;")
        btn_bar.addWidget(self.lbl_list_status)
        btn_bar.addStretch(1)

        self.btn_copy_list = QPushButton("본문 텍스트 복사 (클립보드)")
        self.btn_copy_list.setFixedHeight(32)
        self.btn_copy_list.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.12)};
            }}
        """)
        self.btn_copy_list.clicked.connect(self._copy_list_to_clipboard)
        btn_bar.addWidget(self.btn_copy_list)

        self.btn_excel_list = QPushButton("엑셀로 저장 (.xlsx)")
        self.btn_excel_list.setFixedHeight(32)
        self.btn_excel_list.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {panel};
                border-color: {accent};
            }}
        """)
        self.btn_excel_list.clicked.connect(self._export_list_to_excel)
        btn_bar.addWidget(self.btn_excel_list)

        btn_close = QPushButton("닫기")
        btn_close.setFixedHeight(32)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_close.clicked.connect(self.close)
        btn_bar.addWidget(btn_close)

        vbox.addLayout(btn_bar)

    def _insert_list_tag(self, tag: str) -> None:
        self.input_list_template.insert(tag)
        self.input_list_template.setFocus()

    def _generate_list_lines(self) -> list[str]:
        seq_style = self.combo_list_seq.currentText()
        template = self.input_list_template.text()
        lines = []

        for idx, task in enumerate(self.tasks, 1):
            seq_val = format_korean_sequence(idx, seq_style)
            dept_val = (getattr(task, "department", "") or "").strip()
            title_val = (task.title or "").strip()
            author_val = (task.assignee or "").strip()
            cat_val = (task.memo_group or "일반").strip()
            status_val = (task.status or "등록").strip()
            d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
            date_val = d_val.strftime("%Y-%m-%d") if d_val else ""
            desc_val = (task.description or "").replace("\n", " ").strip()

            line = template
            if seq_val:
                line = line.replace("{순번}", seq_val)
            else:
                line = line.replace("{순번}.", "").replace("{순번}", "")

            line = line.replace("{부서}", dept_val)
            line = line.replace("{제목}", title_val)
            line = line.replace("{기안자}", author_val)
            line = line.replace("{작성자}", author_val)
            line = line.replace("{분류}", cat_val)
            line = line.replace("{상태}", status_val)
            line = line.replace("{일자}", date_val)
            line = line.replace("{날짜}", date_val)
            line = line.replace("{비고}", desc_val)
            line = line.replace("{내용}", desc_val)

            # 불필요한 연속 공백 정리
            line = re.sub(r' +', ' ', line).strip()
            lines.append(line)

        return lines

    def _update_list_preview(self) -> None:
        lines = self._generate_list_lines()
        self.txt_list_preview.setPlainText("\n".join(lines))
        self.lbl_list_preview_title.setText(f"실시간 본문 미리보기 (총 {len(lines)}건)")
        self._save_current_config()

    def _copy_list_to_clipboard(self) -> None:
        lines = self._generate_list_lines()
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.lbl_list_status.setText("클립보드에 복사되었습니다. 공문 본문에 바로 붙여넣기(Ctrl+V)하세요.")
        QTimer.singleShot(3500, lambda: self.lbl_list_status.setText(""))

    def _export_list_to_excel(self) -> None:
        lines = self._generate_list_lines()
        if not lines:
            QMessageBox.information(self, "안내", "내보낼 항목이 없습니다.")
            return

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "공문 본문 엑셀 저장",
            f"공문본문_{now_str}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

            wb = Workbook()
            ws = wb.active
            ws.title = "공문본문"

            ws.append(["공문 본문 내역"])
            h_cell = ws.cell(row=1, column=1)
            h_cell.font = Font(name="Malgun Gothic", size=11, bold=True, color="FFFFFF")
            h_cell.fill = PatternFill(start_color="4A5568", end_color="4A5568", fill_type="solid")
            h_cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.row_dimensions[1].height = 26

            thin_border = Border(
                left=Side(style="thin", color="E2E8F0"),
                right=Side(style="thin", color="E2E8F0"),
                top=Side(style="thin", color="E2E8F0"),
                bottom=Side(style="thin", color="E2E8F0"),
            )
            h_cell.border = thin_border

            for r_idx, line in enumerate(lines, start=2):
                c = ws.cell(row=r_idx, column=1, value=line)
                c.font = Font(name="Malgun Gothic", size=10)
                c.border = thin_border
                c.alignment = Alignment(vertical="center")
                ws.row_dimensions[r_idx].height = 22

            ws.column_dimensions["A"].width = 85
            wb.save(file_path)

            reply = QMessageBox.question(
                self,
                "엑셀 저장 완료",
                f"총 {len(lines)}건의 공문 본문 목록이 저장되었습니다.\n\n파일을 지금 열어보시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                os.startfile(file_path)

        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"엑셀 파일 저장 중 오류가 발생했습니다:\n{e}")

    # ------------------ Tab 2: 공문 붙임 표 형식 ------------------
    def _setup_table_tab(self, parent_widget: QWidget) -> None:
        vbox = QVBoxLayout(parent_widget)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(10)

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        lbl_guide = QLabel("<b>공문서 붙임(첨부물)용 표 서식</b>의 열(Column)과 제목을 원하는 형태로 자유롭게 구성합니다.")
        lbl_guide.setStyleSheet(f"background: {panel_alt}; border: 1px solid {line}; border-radius: 6px; padding: 8px 10px; font-size: 12px; color: {text};")
        vbox.addWidget(lbl_guide)

        # 프리셋 및 순번 선택 바
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_preset = QLabel("서식 프리셋:")
        lbl_preset.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text};")
        top_bar.addWidget(lbl_preset)

        self.combo_table_preset = QComboBox()
        self.combo_table_preset.setFixedHeight(28)
        self.combo_table_preset.addItems([
            "공문 붙임 표 기본 (관련근거 | 처리내역/자료명 | 처리구분 | 요청자 | 비고)",
            "업무 처리 대장 (순번 | 등록일자 | 부서 | 업무제목 | 기안자 | 상태)",
            "간편 목록 (순번 | 업무제목 | 비고)",
            "사용자 직접 지정",
        ])
        self.combo_table_preset.currentIndexChanged.connect(self._on_table_preset_changed)
        top_bar.addWidget(self.combo_table_preset, 1)

        top_bar.addSpacing(14)

        lbl_seq = QLabel("순번 기호:")
        lbl_seq.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text};")
        top_bar.addWidget(lbl_seq)

        self.combo_table_seq = QComboBox()
        self.combo_table_seq.setFixedHeight(28)
        self.combo_table_seq.addItems(["가, 나, 다...", "1, 2, 3...", "①, ②, ③...", "가)", "1)", "없음"])
        self.combo_table_seq.currentIndexChanged.connect(self._update_table_preview)
        top_bar.addWidget(self.combo_table_seq)

        vbox.addLayout(top_bar)

        # 컬럼 체크박스 및 헤더 제목 입력 그리드
        col_frame = QFrame()
        col_frame.setStyleSheet(f"QFrame {{ background: {panel_alt}; border: 1px solid {line}; border-radius: 6px; }}")
        col_grid = QGridLayout(col_frame)
        col_grid.setContentsMargins(10, 8, 10, 8)
        col_grid.setHorizontalSpacing(14)
        col_grid.setVerticalSpacing(6)

        self.col_checks: dict[str, QCheckBox] = {}
        self.col_edits: dict[str, QLineEdit] = {}

        for idx, (col_key, original_name, default_title, default_chk) in enumerate(self._available_columns):
            r = idx // 4
            c = (idx % 4) * 2

            chk = QCheckBox(original_name)
            chk.setChecked(default_chk)
            chk.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {text};")
            chk.stateChanged.connect(self._on_table_column_modified)
            col_grid.addWidget(chk, r, c)
            self.col_checks[col_key] = chk

            edit = QLineEdit(default_title)
            edit.setFixedHeight(26)
            edit.setPlaceholderText("표 헤더명")
            edit.setStyleSheet(f"background: {panel}; color: {text}; border: 1px solid {line}; border-radius: 4px; padding: 2px 6px; font-size: 11px;")
            edit.textChanged.connect(self._on_table_column_modified)
            col_grid.addWidget(edit, r, c + 1)
            self.col_edits[col_key] = edit

        vbox.addWidget(col_frame)

        # 실시간 표 미리보기
        self.lbl_table_preview_title = QLabel(f"실시간 표 미리보기 (총 {len(self.tasks)}건)")
        self.lbl_table_preview_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text}; margin-top: 2px;")
        vbox.addWidget(self.lbl_table_preview_title)

        self.tbl_table_preview = QTableWidget()
        self.tbl_table_preview.setAlternatingRowColors(False)
        self.tbl_table_preview.setStyleSheet(f"""
            QTableWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                gridline-color: {line};
                font-size: 12px;
            }}
            QHeaderView::section {{
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                font-weight: bold;
                padding: 6px;
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
            }}
            QTableCornerButton::section {{
                background-color: {panel_alt};
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
            }}
        """)
        vbox.addWidget(self.tbl_table_preview, 1)

        # 하단 액션 버튼
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.lbl_table_status = QLabel("")
        self.lbl_table_status.setStyleSheet("color: #10B981; font-weight: bold; font-size: 12px;")
        btn_bar.addWidget(self.lbl_table_status)
        btn_bar.addStretch(1)

        self.btn_copy_table_tsv = QPushButton("표 텍스트 복사 (한글/엑셀 붙여넣기용)")
        self.btn_copy_table_tsv.setFixedHeight(32)
        self.btn_copy_table_tsv.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.12)};
            }}
        """)
        self.btn_copy_table_tsv.clicked.connect(self._copy_table_tsv)
        btn_bar.addWidget(self.btn_copy_table_tsv)

        self.btn_excel_table = QPushButton("엑셀로 저장 (.xlsx)")
        self.btn_excel_table.setFixedHeight(32)
        self.btn_excel_table.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {panel};
                border-color: {accent};
            }}
        """)
        self.btn_excel_table.clicked.connect(self._export_table_to_excel)
        btn_bar.addWidget(self.btn_excel_table)

        btn_close = QPushButton("닫기")
        btn_close.setFixedHeight(32)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_close.clicked.connect(self.close)
        btn_bar.addWidget(btn_close)

        vbox.addLayout(btn_bar)

    def _on_table_preset_changed(self, idx: int) -> None:
        if idx == 0:  # 공문 붙임 표 기본
            preset = {
                "seq": (True, "순번"),
                "dept": (True, "관련근거"),
                "title": (True, "처리내역 / 자료명"),
                "cat": (True, "처리구분"),
                "author": (True, "요청자"),
                "desc": (True, "비고"),
                "date": (False, "등록일자"),
                "status": (False, "상태"),
            }
        elif idx == 1:  # 업무 처리 대장
            preset = {
                "seq": (True, "순번"),
                "date": (True, "등록일자"),
                "dept": (True, "부서"),
                "title": (True, "업무제목"),
                "author": (True, "기안자"),
                "status": (True, "상태"),
                "cat": (False, "분류"),
                "desc": (False, "비고"),
            }
        elif idx == 2:  # 간편 목록
            preset = {
                "seq": (True, "순번"),
                "title": (True, "업무제목"),
                "desc": (True, "비고"),
                "dept": (False, "부서"),
                "cat": (False, "분류"),
                "author": (False, "기안자"),
                "date": (False, "등록일자"),
                "status": (False, "상태"),
            }
        else:
            return

        self._applying_preset = True
        try:
            for col_key, (chk_val, title_val) in preset.items():
                if col_key in self.col_checks:
                    self.col_checks[col_key].setChecked(chk_val)
                if col_key in self.col_edits:
                    self.col_edits[col_key].setText(title_val)
        finally:
            self._applying_preset = False

        self._update_table_preview()

    def _on_table_column_modified(self) -> None:
        if getattr(self, "_applying_preset", False):
            return
        if self.combo_table_preset.currentIndex() != 3:
            self.combo_table_preset.blockSignals(True)
            self.combo_table_preset.setCurrentIndex(3)  # 사용자 직접 지정
            self.combo_table_preset.blockSignals(False)
        self._update_table_preview()

    def _get_active_table_columns(self) -> list[tuple[str, str]]:
        """현재 체크된 열 키와 사용자 정의 헤더명 목록 반환"""
        active = []
        for col_key, _, _, _ in self._available_columns:
            chk = self.col_checks.get(col_key)
            edit = self.col_edits.get(col_key)
            if chk and chk.isChecked():
                title = edit.text().strip() if edit else col_key
                active.append((col_key, title or col_key))
        return active

    def _get_task_field_value(self, task: CalendarEntry, col_key: str, row_idx: int, seq_style: str) -> str:
        if col_key == "seq":
            return format_korean_sequence(row_idx, seq_style)
        elif col_key == "dept":
            return getattr(task, "department", "") or ""
        elif col_key == "title":
            return task.title or ""
        elif col_key == "cat":
            return task.memo_group or "일반"
        elif col_key == "author":
            return task.assignee or ""
        elif col_key == "desc":
            return (task.description or "").replace("\n", " ").strip()
        elif col_key == "date":
            d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
            return d_val.strftime("%Y-%m-%d") if d_val else ""
        elif col_key == "status":
            return task.status or "등록"
        return ""

    def _update_table_preview(self) -> None:
        active_cols = self._get_active_table_columns()
        seq_style = self.combo_table_seq.currentText()

        self.tbl_table_preview.setColumnCount(len(active_cols))
        self.tbl_table_preview.setHorizontalHeaderLabels([title for _, title in active_cols])
        self.tbl_table_preview.setRowCount(len(self.tasks))

        for r_idx, task in enumerate(self.tasks):
            for c_idx, (col_key, _) in enumerate(active_cols):
                val = self._get_task_field_value(task, col_key, r_idx + 1, seq_style)
                item = QTableWidgetItem(val)
                if col_key in ("seq", "cat", "author", "date", "status"):
                    item.setTextAlignment(Qt.AlignCenter)
                else:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.tbl_table_preview.setItem(r_idx, c_idx, item)

        self.tbl_table_preview.resizeColumnsToContents()
        self.lbl_table_preview_title.setText(f"실시간 표 미리보기 (총 {len(self.tasks)}건, {len(active_cols)}개 열)")
        self._save_current_config()

    def _copy_table_tsv(self) -> None:
        active_cols = self._get_active_table_columns()
        if not active_cols:
            QMessageBox.information(self, "안내", "선택된 열이 없습니다.")
            return

        seq_style = self.combo_table_seq.currentText()
        lines = []

        # 헤더 행
        headers = [title for _, title in active_cols]
        lines.append("\t".join(headers))

        # 데이터 행
        for r_idx, task in enumerate(self.tasks, 1):
            row_vals = [self._get_task_field_value(task, col_key, r_idx, seq_style).replace("\t", " ") for col_key, _ in active_cols]
            lines.append("\t".join(row_vals))

        tsv_text = "\n".join(lines)
        QApplication.clipboard().setText(tsv_text)
        self.lbl_table_status.setText("표가 복사되었습니다. 한글(HWP)이나 엑셀에 바로 붙여넣기(Ctrl+V)하세요.")
        QTimer.singleShot(3500, lambda: self.lbl_table_status.setText(""))

    def _export_table_to_excel(self) -> None:
        active_cols = self._get_active_table_columns()
        if not active_cols:
            QMessageBox.information(self, "안내", "선택된 열이 없습니다.")
            return

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "공문 표 엑셀 저장",
            f"공문붙임표_{now_str}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()
            ws = wb.active
            ws.title = "붙임표"

            headers = [title for _, title in active_cols]
            ws.append(headers)

            h_fill = PatternFill(start_color="4A5568", end_color="4A5568", fill_type="solid")
            h_font = Font(name="Malgun Gothic", size=11, bold=True, color="FFFFFF")
            thin_border = Border(
                left=Side(style="thin", color="CBD5E0"),
                right=Side(style="thin", color="CBD5E0"),
                top=Side(style="thin", color="CBD5E0"),
                bottom=Side(style="thin", color="CBD5E0"),
            )

            for col_idx in range(1, len(headers) + 1):
                c = ws.cell(row=1, column=col_idx)
                c.fill = h_fill
                c.font = h_font
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border = thin_border
            ws.row_dimensions[1].height = 28

            seq_style = self.combo_table_seq.currentText()
            for r_idx, task in enumerate(self.tasks, 1):
                row_vals = [self._get_task_field_value(task, col_key, r_idx, seq_style) for col_key, _ in active_cols]
                ws.append(row_vals)
                row_num = r_idx + 1

                for col_idx, (col_key, _) in enumerate(active_cols, 1):
                    cell = ws.cell(row=row_num, column=col_idx)
                    cell.font = Font(name="Malgun Gothic", size=10)
                    cell.border = thin_border
                    if col_key in ("seq", "cat", "author", "date", "status"):
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    else:
                        cell.alignment = Alignment(vertical="center")

                ws.row_dimensions[row_num].height = 22

            # 열 너비 자동 조정
            for col_idx, (col_key, title) in enumerate(active_cols, 1):
                col_letter = get_column_letter(col_idx)
                if col_key == "seq":
                    ws.column_dimensions[col_letter].width = 8
                elif col_key in ("title", "desc"):
                    ws.column_dimensions[col_letter].width = 35
                elif col_key == "dept":
                    ws.column_dimensions[col_letter].width = 24
                elif col_key in ("cat", "author", "status"):
                    ws.column_dimensions[col_letter].width = 14
                elif col_key == "date":
                    ws.column_dimensions[col_letter].width = 14
                else:
                    ws.column_dimensions[col_letter].width = 18

            wb.save(file_path)

            reply = QMessageBox.question(
                self,
                "엑셀 저장 완료",
                f"총 {len(self.tasks)}건의 붙임 표가 성공적으로 저장되었습니다.\n\n파일을 지금 열어보시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                os.startfile(file_path)

        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"엑셀 파일 저장 중 오류가 발생했습니다:\n{e}")

    # ------------------ Tab 3: 표준 업무관리대장 ------------------
    def _setup_standard_tab(self, parent_widget: QWidget) -> None:
        vbox = QVBoxLayout(parent_widget)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(10)

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#1F7A67")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        lbl_guide = QLabel("<b>전체 8개 열</b>(번호, 상태, 등록일자, 분류, 부서, 업무제목, 기안자, 비고/세부내용)을 모두 포함하는 표준 업무관리대장 서식입니다.")
        lbl_guide.setStyleSheet(f"background: {panel_alt}; border: 1px solid {line}; border-radius: 6px; padding: 8px 10px; font-size: 12px; color: {text};")
        vbox.addWidget(lbl_guide)

        # 미리보기 테이블
        std_headers = ["번호", "상태", "등록일자", "분류", "부서", "업무제목", "기안자", "비고/세부내용"]
        self.tbl_standard_preview = QTableWidget()
        self.tbl_standard_preview.setColumnCount(len(std_headers))
        self.tbl_standard_preview.setHorizontalHeaderLabels(std_headers)
        self.tbl_standard_preview.setRowCount(len(self.tasks))
        self.tbl_standard_preview.setStyleSheet(f"""
            QTableWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                gridline-color: {line};
                font-size: 12px;
            }}
            QHeaderView::section {{
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                font-weight: bold;
                padding: 6px;
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
            }}
            QTableCornerButton::section {{
                background-color: {panel_alt};
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
            }}
        """)

        for r_idx, task in enumerate(self.tasks, 1):
            d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
            d_str = d_val.strftime("%Y-%m-%d") if d_val else ""
            row_vals = [
                str(r_idx),
                task.status or "등록",
                d_str,
                task.memo_group or "일반",
                getattr(task, "department", "") or "",
                task.title or "",
                task.assignee or "",
                (task.description or "").replace("\n", " ").strip(),
            ]
            for c_idx, val in enumerate(row_vals):
                it = QTableWidgetItem(val)
                if c_idx in (0, 1, 2, 3, 6):
                    it.setTextAlignment(Qt.AlignCenter)
                else:
                    it.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                self.tbl_standard_preview.setItem(r_idx - 1, c_idx, it)

        self.tbl_standard_preview.resizeColumnsToContents()
        vbox.addWidget(self.tbl_standard_preview, 1)

        # 하단 액션 버튼
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)
        btn_bar.addStretch(1)

        btn_excel_std = QPushButton("표준 대장 엑셀로 저장 (.xlsx)")
        btn_excel_std.setFixedHeight(32)
        btn_excel_std.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.12)};
            }}
        """)
        btn_excel_std.clicked.connect(self._export_standard_to_excel)
        btn_bar.addWidget(btn_excel_std)

        btn_close = QPushButton("닫기")
        btn_close.setFixedHeight(32)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 14px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_close.clicked.connect(self.close)
        btn_bar.addWidget(btn_close)

        vbox.addLayout(btn_bar)

    def _export_standard_to_excel(self) -> None:
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "표준 업무관리대장 엑셀 저장",
            f"업무관리대장_{now_str}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter

            wb = Workbook()
            ws = wb.active
            ws.title = "업무관리대장"

            headers = ["번호", "상태", "등록일자", "분류", "부서", "업무제목", "기안자/작성자", "세부내용/출처"]
            ws.append(headers)

            h_fill = PatternFill(start_color="4A5568", end_color="4A5568", fill_type="solid")
            h_font = Font(name="Malgun Gothic", size=11, bold=True, color="FFFFFF")
            thin_border = Border(
                left=Side(style="thin", color="CBD5E0"),
                right=Side(style="thin", color="CBD5E0"),
                top=Side(style="thin", color="CBD5E0"),
                bottom=Side(style="thin", color="CBD5E0"),
            )

            for col_idx in range(1, len(headers) + 1):
                c = ws.cell(row=1, column=col_idx)
                c.fill = h_fill
                c.font = h_font
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border = thin_border
            ws.row_dimensions[1].height = 28

            for idx, task in enumerate(self.tasks, 1):
                is_done = (task.status == "완료")
                d_val = task.day or task.start_date or (task.created_at.date() if task.created_at else None)
                d_str = d_val.strftime("%Y-%m-%d") if d_val else ""

                row_values = [
                    idx,
                    task.status or "등록",
                    d_str,
                    task.memo_group or "일반",
                    getattr(task, "department", "") or "",
                    task.title or "",
                    task.assignee or "",
                    task.description or "",
                ]
                ws.append(row_values)
                row_idx = idx + 1

                for col_idx in range(1, len(headers) + 1):
                    c = ws.cell(row=row_idx, column=col_idx)
                    c.font = Font(name="Malgun Gothic", size=10)
                    c.border = thin_border
                    c.alignment = Alignment(vertical="center")

                    if col_idx in (1, 2, 3, 4, 7):
                        c.alignment = Alignment(horizontal="center", vertical="center")
                    elif col_idx == 5:
                        c.alignment = Alignment(horizontal="center", vertical="center")
                        c.font = Font(name="Malgun Gothic", size=10, bold=True, color="4F46E5" if not is_done else "718096")

                    if is_done and col_idx == 6:
                        c.font = Font(name="Malgun Gothic", size=10, strike=True, color="A0AEC0")

                ws.row_dimensions[row_idx].height = 22

            col_widths = {1: 8, 2: 12, 3: 14, 4: 12, 5: 22, 6: 35, 7: 16, 8: 40}
            for col_idx, width in col_widths.items():
                col_letter = get_column_letter(col_idx)
                ws.column_dimensions[col_letter].width = width

            wb.save(file_path)

            reply = QMessageBox.question(
                self,
                "엑셀 저장 완료",
                f"총 {len(self.tasks)}건의 업무 목록이 성공적으로 저장되었습니다.\n\n파일을 지금 열어보시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                os.startfile(file_path)

        except Exception as e:
            QMessageBox.critical(self, "저장 실패", f"엑셀 파일 저장 중 오류가 발생했습니다:\n{e}")

    # ------------------ 설정 저장 및 복원 ------------------
    def _save_current_config(self) -> None:
        try:
            config = {
                "list_seq": self.combo_list_seq.currentText(),
                "list_template": self.input_list_template.text(),
                "table_preset": self.combo_table_preset.currentIndex(),
                "table_seq": self.combo_table_seq.currentText(),
                "table_cols": {k: (self.col_checks[k].isChecked(), self.col_edits[k].text()) for k in self.col_checks},
            }
            self.repository.set_setting("task_export_config", json.dumps(config, ensure_ascii=False))
            self.repository.save()
        except Exception:
            pass

    def _load_saved_config(self) -> None:
        try:
            raw = self.repository.get_setting("task_export_config")
            if not raw:
                return
            config = json.loads(raw)
            if not isinstance(config, dict):
                return

            if "list_seq" in config:
                idx = self.combo_list_seq.findText(config["list_seq"])
                if idx >= 0:
                    self.combo_list_seq.setCurrentIndex(idx)
            if "list_template" in config and config["list_template"]:
                self.input_list_template.setText(config["list_template"])

            if "table_seq" in config:
                idx = self.combo_table_seq.findText(config["table_seq"])
                if idx >= 0:
                    self.combo_table_seq.setCurrentIndex(idx)

            if "table_cols" in config and isinstance(config["table_cols"], dict):
                self._applying_preset = True
                try:
                    for k, val in config["table_cols"].items():
                        if isinstance(val, (list, tuple)) and len(val) >= 2:
                            if k in self.col_checks:
                                self.col_checks[k].setChecked(bool(val[0]))
                            if k in self.col_edits:
                                self.col_edits[k].setText(str(val[1]))
                finally:
                    self._applying_preset = False

            if "table_preset" in config:
                p_idx = int(config["table_preset"])
                if 0 <= p_idx < self.combo_table_preset.count():
                    self.combo_table_preset.blockSignals(True)
                    self.combo_table_preset.setCurrentIndex(p_idx)
                    self.combo_table_preset.blockSignals(False)
        except Exception:
            pass
