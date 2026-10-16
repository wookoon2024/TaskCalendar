"""법령 근거 확인, 개정 알림 및 조문 대조·업데이트 다이얼로그 & 배너 위젯.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.ai_assistant import AIChatWorker, AIConfigManager
from taskcalendar.fonts import make_ui_font, make_ui_font_like, scale_px
from taskcalendar.work_law_tracker import LawReference, compare_snippets

if TYPE_CHECKING:
    from taskcalendar.qt_main_window import MainWindow

logger = logging.getLogger(__name__)


class LawChipButton(QFrame):
    """참조 법령 한 건을 나타내는 칩 버튼 (클릭 시 국가법령정보센터 원문 열기)"""

    clicked = Signal(object)  # LawReference 전달

    def __init__(self, ref: LawReference, parent=None):
        super().__init__(parent)
        self.ref = ref
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(max(20, scale_px(20)))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        # 개정 여부에 따른 스타일
        if ref.is_revised:
            bg_color = "#FEF3C7"
            border_color = "#F59E0B"
            text_color = "#92400E"
            status_tag = f"[개정 {ref.latest_enforce_date}]"
        else:
            bg_color = "#F1F5F9"
            border_color = "#CBD5E1"
            text_color = "#1E293B"
            ef = ref.latest_enforce_date or ref.enforce_date
            status_tag = f"({ef} 시행)" if ef else ""

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 10px;
            }}
            QFrame:hover {{
                background-color: #E2E8F0;
                border-color: #0284C7;
            }}
            QToolTip {{
                background-color: #FFFFFF;
                color: #1E293B;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)

        title_str = f"{ref.display_title} {status_tag}".strip()
        self.lbl_title = QLabel(title_str, self)
        self.lbl_title.setFont(make_ui_font_like(8.5, weight=QFont.Weight.Medium.value))
        self.lbl_title.setStyleSheet(f"color: {text_color}; background: transparent; border: none;")
        layout.addWidget(self.lbl_title)

        tip_parts = [
            f"법령명: {ref.official_name or ref.law_name}",
            f"조문: {ref.article_label or '전체'}",
            f"시행일자: {ref.latest_enforce_date or ref.enforce_date or '정보없음'}",
            "클릭 시 법령 대조 및 개정 검토 창을 엽니다.",
        ]
        if ref.is_revised:
            tip_parts.insert(3, "[최근 개정되었습니다]")
        self.setToolTip("\n".join(tip_parts))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 클릭 시 대조 다이얼로그만 열림 (브라우저 웹사이트 자동 열림 방지)
            self.clicked.emit(self.ref)
            event.accept()
            return
        super().mousePressEvent(event)


class LawReferenceBanner(QFrame):
    """업무 편집기 상단에 위치하는 법령 근거 알림 배너 위젯 (슬림형)"""

    compare_requested = Signal(object)  # LawReference
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lawReferenceBanner")
        self.refs: list[LawReference] = []

        self.setFixedHeight(max(26, scale_px(26)))
        self.setStyleSheet("""
            QFrame#lawReferenceBanner {
                background-color: #F8FAFC;
                border-bottom: 1px solid #E2E8F0;
                padding: 0px 6px;
            }
            QToolTip {
                background-color: #FFFFFF;
                color: #1E293B;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 1, 6, 1)
        layout.setSpacing(6)

        self.lbl_header = QLabel("참조 법령:", self)
        self.lbl_header.setFont(make_ui_font_like(8.5, weight=QFont.Weight.Bold.value))
        self.lbl_header.setStyleSheet("color: #475569; background: transparent; border: none;")
        layout.addWidget(self.lbl_header)

        # 칩들을 스크롤 가능하게 담는 영역
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFixedHeight(max(22, scale_px(22)))
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")

        self.chips_container = QWidget()
        self.chips_container.setStyleSheet("background: transparent;")
        self.chips_layout = QHBoxLayout(self.chips_container)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(4)
        self.chips_layout.addStretch(1)

        self.scroll_area.setWidget(self.chips_container)
        layout.addWidget(self.scroll_area, 1)

        # 닫기(접기) 버튼
        btn_close = QToolButton(self)
        btn_close.setText("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("QToolButton { background: transparent; border: none; color: #94A3B8; font-size: 10px; } QToolButton:hover { color: #475569; }")
        btn_close.setToolTip("법령 배너 숨기기")
        btn_close.clicked.connect(self.hide)
        layout.addWidget(btn_close)

        self.hide()  # 초기에는 숨김

    def update_references(self, refs: list[LawReference]):
        """인식된 법령 목록 갱신"""
        self.refs = refs

        # 기존 칩 정리
        while self.chips_layout.count() > 1:
            item = self.chips_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not refs:
            self.hide()
            return

        has_revised = any(r.is_revised for r in refs)
        if has_revised:
            self.lbl_header.setText("개정 법령 감지:")
            self.lbl_header.setStyleSheet("color: #DC2626; font-weight: bold; background: transparent; border: none;")
        else:
            self.lbl_header.setText("참조 법령:")
            self.lbl_header.setStyleSheet("color: #475569; font-weight: bold; background: transparent; border: none;")

        for r in refs:
            chip = LawChipButton(r, self.chips_container)
            chip.clicked.connect(self.compare_requested.emit)
            self.chips_layout.insertWidget(self.chips_layout.count() - 1, chip)

        self.show()


class LawSuggestionCard(QFrame):
    """문맥 단위 개별 법령 수정 제안 카드 위젯 (체크박스 선택 및 문구 직접 편집 지원)"""

    toggled = Signal()

    def __init__(self, item_id: int, original_text: str, reason: str, replacement_text: str, parent=None):
        super().__init__(parent)
        self.item_id = item_id
        self.original_text = original_text.strip()
        self.setObjectName("suggestionCard")
        self.setStyleSheet("""
            QFrame#suggestionCard {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
            }
            QFrame#suggestionCard:hover {
                border-color: #93C5FD;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # 1. 상단: 체크박스 선택 행
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.chk_apply = QCheckBox(f"항목 #{item_id} 수정안 문서 반영", self)
        self.chk_apply.setChecked(True)
        self.chk_apply.setFont(make_ui_font_like(9.5, weight=QFont.Weight.Bold.value))
        self.chk_apply.setStyleSheet("QCheckBox { color: #0F172A; font-weight: bold; } QCheckBox::indicator { width: 16px; height: 16px; }")
        self.chk_apply.toggled.connect(lambda: self.toggled.emit())
        top_row.addWidget(self.chk_apply)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        # 2. 검토 사유 강조 박스 (아이콘 없이 깔끔한 강조 박스)
        reason_frame = QFrame(self)
        reason_frame.setStyleSheet("""
            QFrame {
                background-color: #FEF2F2;
                border: 1px solid #FECACA;
                border-left: 4px solid #DC2626;
                border-radius: 4px;
            }
        """)
        rf_layout = QHBoxLayout(reason_frame)
        rf_layout.setContentsMargins(10, 6, 10, 6)
        rf_layout.setSpacing(6)

        self.lbl_reason = QLabel(f"<b>검토 사유:</b> {reason}", reason_frame)
        self.lbl_reason.setFont(make_ui_font_like(9.5))
        self.lbl_reason.setWordWrap(True)
        self.lbl_reason.setStyleSheet("color: #991B1B; border: none; background: transparent;")
        rf_layout.addWidget(self.lbl_reason, 1)

        layout.addWidget(reason_frame)

        # 3. 중단: [변경 전] ➔ [변경 후] 나란히 비교
        compare_box = QHBoxLayout()
        compare_box.setSpacing(8)

        # 좌측: 변경 전 (읽기 전용)
        orig_box = QVBoxLayout()
        orig_box.setSpacing(3)
        lbl_orig_title = QLabel("변경 전:", self)
        lbl_orig_title.setFont(make_ui_font_like(9, weight=QFont.Weight.Bold.value))
        lbl_orig_title.setStyleSheet("color: #64748B; border: none; background: transparent;")
        orig_box.addWidget(lbl_orig_title)

        self.txt_orig = QTextEdit(self)
        self.txt_orig.setFont(make_ui_font_like(9))
        self.txt_orig.setReadOnly(True)
        self.txt_orig.setPlainText(self.original_text)
        self.txt_orig.setFixedHeight(48)
        self.txt_orig.setStyleSheet("""
            QTextEdit {
                background-color: #F8FAFC;
                color: #64748B;
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                padding: 4px 6px;
            }
        """)
        orig_box.addWidget(self.txt_orig)
        compare_box.addLayout(orig_box, 1)

        # 중앙: 화살표 (인풋 필드 높이에 맞춘 수직 중앙 배치)
        arrow_box = QVBoxLayout()
        arrow_box.setSpacing(3)
        lbl_arrow_spacer = QLabel("", self)
        lbl_arrow_spacer.setFont(make_ui_font_like(9))
        arrow_box.addWidget(lbl_arrow_spacer)

        lbl_arrow = QLabel("➔", self)
        lbl_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_arrow.setFont(make_ui_font_like(13, weight=QFont.Weight.Bold.value))
        lbl_arrow.setStyleSheet("color: #3B82F6; border: none; background: transparent;")
        lbl_arrow.setFixedHeight(48)
        arrow_box.addWidget(lbl_arrow)
        compare_box.addLayout(arrow_box)

        # 우측: 변경 후 (실제 편집 가능한 텍스트박스 외형)
        repl_box = QVBoxLayout()
        repl_box.setSpacing(3)
        lbl_repl_title = QLabel("변경 후 (직접 수정 가능):", self)
        lbl_repl_title.setFont(make_ui_font_like(9, weight=QFont.Weight.Bold.value))
        lbl_repl_title.setStyleSheet("color: #2563EB; border: none; background: transparent;")
        repl_box.addWidget(lbl_repl_title)

        self.txt_repl = QTextEdit(self)
        self.txt_repl.setFont(make_ui_font_like(9.5))
        self.txt_repl.setPlainText(replacement_text.strip())
        self.txt_repl.setFixedHeight(48)
        self.txt_repl.setPlaceholderText("필요 시 직접 수정하여 반영할 수 있습니다.")
        self.txt_repl.setStyleSheet("""
            QTextEdit {
                background-color: #FFFFFF;
                color: #0F172A;
                border: 1.5px solid #3B82F6;
                border-radius: 4px;
                padding: 4px 6px;
                selection-background-color: #BFDBFE;
            }
            QTextEdit:focus {
                border: 2px solid #1D4ED8;
                background-color: #FFFFFF;
            }
        """)
        repl_box.addWidget(self.txt_repl)
        compare_box.addLayout(repl_box, 1)

        layout.addLayout(compare_box)

    def is_selected(self) -> bool:
        return self.chk_apply.isChecked()

    def set_selected(self, checked: bool) -> None:
        self.chk_apply.setChecked(checked)

    def get_replacement_pair(self) -> tuple[str, str]:
        return (self.original_text, self.txt_repl.toPlainText().strip())


class LawComparisonDialog(QDialog):
    """업무 문서 내용과 최신 법령 조문을 대조하고, 문맥 단위로 개별 검토 및 선택 반영하는 다이얼로그"""

    update_applied = Signal(object, str)  # (pairs_or_text, replacement_text)

    def __init__(
        self,
        ref: LawReference,
        doc_context: str = "",
        all_refs: list[LawReference] | None = None,
        on_apply_update: Callable[[Any, str], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.ref = ref
        self.doc_context = doc_context
        self.all_refs = all_refs or [ref]
        self.on_apply_update = on_apply_update
        self._ai_worker: AIChatWorker | None = None
        self._suggestion_cards: list[LawSuggestionCard] = []

        self.setWindowTitle(f"법령 근거 확인 및 최신 조문 대조 - {ref.display_title}")
        self.setMinimumSize(940, 700)
        self.resize(1000, 740)

        self._init_ui()
        self._load_data()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # 1. 상단 안내 헤더 (단정하고 깔끔한 플랫 디자인)
        self.header_frame = QFrame(self)
        self.header_frame.setStyleSheet("""
            QFrame {
                background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
            }
            QLabel {
                background: transparent;
                border: none;
                padding: 0px;
            }
        """)
        h_layout = QHBoxLayout(self.header_frame)
        h_layout.setContentsMargins(12, 10, 12, 10)
        h_layout.setSpacing(12)

        info_box = QVBoxLayout()
        info_box.setSpacing(4)

        self.lbl_law_name = QLabel(f"{self.ref.display_title}", self.header_frame)
        self.lbl_law_name.setFont(make_ui_font_like(12.5, weight=QFont.Weight.Bold.value))
        self.lbl_law_name.setStyleSheet("color: #0F172A; background: transparent; border: none;")
        info_box.addWidget(self.lbl_law_name)

        self.lbl_meta_info = QLabel("시행일자 및 소관부처 정보 확인 중...", self.header_frame)
        self.lbl_meta_info.setFont(make_ui_font_like(9.5))
        self.lbl_meta_info.setStyleSheet("color: #64748B; background: transparent; border: none;")
        info_box.addWidget(self.lbl_meta_info)

        h_layout.addLayout(info_box, 1)

        # 국가법령정보센터 원문 보기 버튼
        btn_open_web = QPushButton("law.go.kr 원문 보기", self.header_frame)
        btn_open_web.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_web.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 500;
                font-size: 11.5px;
                color: #334155;
            }
            QPushButton:hover {
                background-color: #F1F5F9;
                border-color: #94A3B8;
            }
        """)
        btn_open_web.clicked.connect(self._open_law_web)
        h_layout.addWidget(btn_open_web)

        main_layout.addWidget(self.header_frame)

        # 2. 중앙 2단 비교 뷰어 (상단: 내 업무 문서 전체 / 우측: 최신 법령 조문 원문 전체)
        top_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # 좌측 상단: 내 업무 문서 내용 전체
        left_box = QWidget(top_splitter)
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(6)

        lbl_doc_title = QLabel("내 업무 문서 내용 (인용 문맥)", left_box)
        lbl_doc_title.setFont(make_ui_font_like(9.5, weight=QFont.Weight.Bold.value))
        lbl_doc_title.setStyleSheet("color: #1E293B;")
        left_layout.addWidget(lbl_doc_title)

        self.txt_doc_content = QTextEdit(left_box)
        self.txt_doc_content.setFont(make_ui_font_like(9.5))
        self.txt_doc_content.setReadOnly(True)
        self.txt_doc_content.setStyleSheet("""
            QTextEdit {
                background-color: #FAFAFA;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 8px;
                color: #1E293B;
                line-height: 1.5;
            }
        """)
        left_layout.addWidget(self.txt_doc_content)
        top_splitter.addWidget(left_box)

        # 우측 상단: 최신 법령 조문 원문 전체
        right_box = QWidget(top_splitter)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        lbl_law_title = QLabel("국가법령정보센터 최신 조문 원문", right_box)
        lbl_law_title.setFont(make_ui_font_like(9.5, weight=QFont.Weight.Bold.value))
        lbl_law_title.setStyleSheet("color: #1E293B;")
        right_layout.addWidget(lbl_law_title)

        self.txt_law_content = QTextEdit(right_box)
        self.txt_law_content.setFont(make_ui_font_like(9.5))
        self.txt_law_content.setReadOnly(True)
        self.txt_law_content.setStyleSheet("""
            QTextEdit {
                background-color: #F8FAFC;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 8px;
                color: #1E293B;
                line-height: 1.5;
            }
        """)
        right_layout.addWidget(self.txt_law_content)
        top_splitter.addWidget(right_box)

        top_splitter.setSizes([470, 470])
        main_layout.addWidget(top_splitter, 1)

        # 3. 중앙 하단: 문맥 단위 법령 대조 분석 및 개별 선택 수정 영역
        bottom_panel = QFrame(self)
        bottom_panel.setStyleSheet("""
            QFrame#bottomPanel {
                background-color: #F8FAFC;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
            }
        """)
        bottom_panel.setObjectName("bottomPanel")
        bp_layout = QVBoxLayout(bottom_panel)
        bp_layout.setContentsMargins(10, 8, 10, 8)
        bp_layout.setSpacing(6)

        # 제어 헤더 바
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(10)

        self.lbl_ai_status = QLabel("<b>AI 대조 분석:</b> 버튼을 누르면 문맥 단위로 어떤 항목이 왜 잘못되었는지 분석하여 개별 수정안 카드로 표시합니다.", bottom_panel)
        self.lbl_ai_status.setFont(make_ui_font_like(9.5))
        self.lbl_ai_status.setWordWrap(True)
        self.lbl_ai_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lbl_ai_status.setStyleSheet("color: #1E293B; background: transparent; border: none;")
        ctrl_bar.addWidget(self.lbl_ai_status, 1)

        self.btn_select_all = QPushButton("전체 선택", bottom_panel)
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setFixedHeight(max(26, scale_px(26)))
        self.btn_select_all.setStyleSheet("QPushButton { background-color: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 3px; padding: 0 8px; font-size: 11px; }")
        self.btn_select_all.clicked.connect(lambda: self._set_all_suggestions_checked(True))
        self.btn_select_all.hide()
        ctrl_bar.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("전체 해제", bottom_panel)
        self.btn_deselect_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deselect_all.setFixedHeight(max(26, scale_px(26)))
        self.btn_deselect_all.setStyleSheet("QPushButton { background-color: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 3px; padding: 0 8px; font-size: 11px; }")
        self.btn_deselect_all.clicked.connect(lambda: self._set_all_suggestions_checked(False))
        self.btn_deselect_all.hide()
        ctrl_bar.addWidget(self.btn_deselect_all)

        self.btn_run_ai = QPushButton("AI 법령 대조 분석 시작", bottom_panel)
        self.btn_run_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run_ai.setFixedHeight(max(28, scale_px(28)))
        self.btn_run_ai.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 0 14px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton:disabled {
                background-color: #94A3B8;
                color: #F1F5F9;
            }
        """)
        self.btn_run_ai.clicked.connect(self._start_ai_comparison)
        ctrl_bar.addWidget(self.btn_run_ai)
        bp_layout.addLayout(ctrl_bar)

        self.ai_progress = QProgressBar(bottom_panel)
        self.ai_progress.setRange(0, 0)
        self.ai_progress.setFixedHeight(3)
        self.ai_progress.setTextVisible(False)
        self.ai_progress.setStyleSheet("QProgressBar { border: none; background-color: #CBD5E1; } QProgressBar::chunk { background-color: #2563EB; }")
        self.ai_progress.hide()
        bp_layout.addWidget(self.ai_progress)

        # 개별 수정 제안 카드들을 담는 스크롤 영역
        self.scroll_suggestions = QScrollArea(bottom_panel)
        self.scroll_suggestions.setWidgetResizable(True)
        self.scroll_suggestions.setStyleSheet("QScrollArea { border: 1px solid #E2E8F0; background-color: #FFFFFF; border-radius: 4px; }")

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(8, 8, 8, 8)
        self.cards_layout.setSpacing(8)

        # 초기 안내 레이블
        self.lbl_placeholder = QLabel("상단의 [AI 법령 대조 분석 시작] 버튼을 누르면 문맥별 점검 및 선택형 수정안 카드가 표시됩니다.", self.cards_container)
        self.lbl_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_placeholder.setStyleSheet("color: #94A3B8; font-size: 12px; padding: 24px;")
        self.cards_layout.addWidget(self.lbl_placeholder)
        self.cards_layout.addStretch(1)

        self.scroll_suggestions.setWidget(self.cards_container)
        bp_layout.addWidget(self.scroll_suggestions, 1)

        main_layout.addWidget(bottom_panel, 1)

        # 4. 하단 액션 버튼 바
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(8)

        # 선택한 항목만 문서에 반영하는 버튼
        self.btn_apply_update = QPushButton("선택한 수정안 문서 반영", self)
        self.btn_apply_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply_update.setFixedHeight(max(34, scale_px(34)))
        self.btn_apply_update.setFont(make_ui_font_like(10, weight=QFont.Weight.Bold.value))
        self.btn_apply_update.setEnabled(False)
        self.btn_apply_update.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                padding: 0 16px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
            QPushButton:disabled {
                background-color: #CBD5E1;
                color: #94A3B8;
            }
        """)
        self.btn_apply_update.clicked.connect(self._on_apply_selected_clicked)
        bottom_layout.addWidget(self.btn_apply_update)

        # 조문 원문 블록 직접 삽입 보조 버튼
        self.btn_manual_append = QPushButton("조문 원문 그대로 문서에 추가", self)
        self.btn_manual_append.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_manual_append.setFixedHeight(max(34, scale_px(34)))
        self.btn_manual_append.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 5px;
                padding: 0 12px;
                color: #475569;
            }
            QPushButton:hover {
                background-color: #F8FAFC;
                border-color: #94A3B8;
            }
        """)
        self.btn_manual_append.clicked.connect(self._on_manual_append_clicked)
        bottom_layout.addWidget(self.btn_manual_append)

        bottom_layout.addStretch(1)

        btn_close = QPushButton("닫기", self)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setFixedHeight(max(34, scale_px(34)))
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 5px;
                padding: 0 16px;
                color: #475569;
            }
            QPushButton:hover {
                background-color: #F8FAFC;
            }
        """)
        btn_close.clicked.connect(self.close)
        bottom_layout.addWidget(btn_close)

        main_layout.addLayout(bottom_layout)

    def _load_data(self):
        """데이터 로드 및 우측 법령 조문 원문 확실하게 채우기"""
        ref = self.ref

        # 헤더 스타일 설정 (개정 여부에 따라 은은한 플랫 색상 적용)
        if ref.is_revised:
            self.header_frame.setStyleSheet("""
                QFrame {
                    background-color: #FEF2F2;
                    border: 1px solid #FECACA;
                    border-radius: 6px;
                }
                QLabel {
                    background: transparent;
                    border: none;
                    padding: 0px;
                }
            """)
            meta = [
                f"소관: {ref.office or '미지정'}",
                f"기존 인용 시행일: {ref.enforce_date or '기록없음'}",
                f"최신 개정 시행일: <b style='color:#DC2626;'>{ref.latest_enforce_date}</b> ({ref.latest_revision or '개정'})",
            ]
            self.lbl_meta_info.setText("  ·  ".join(meta))
        else:
            self.header_frame.setStyleSheet("""
                QFrame {
                    background-color: #F8FAFC;
                    border: 1px solid #E2E8F0;
                    border-radius: 6px;
                }
                QLabel {
                    background: transparent;
                    border: none;
                    padding: 0px;
                }
            """)
            meta = [
                f"소관: {ref.office or '미지정'}",
                f"현행 시행일: <b>{ref.latest_enforce_date or ref.enforce_date or '정보없음'}</b>",
                f"구분: {ref.law_kind or '법률'}",
            ]
            self.lbl_meta_info.setText("  ·  ".join(meta))

        # 1. 좌측 내 문서 내용 표시
        doc_display = self.doc_context.strip() if self.doc_context else f"문서 내 언급: 「{ref.raw_matched}」"
        self.txt_doc_content.setPlainText(doc_display)

        # 2. 우측 법령 조문 내용 로드 (ref에 조문 원문이 비어있으면 즉시 조회하여 보강)
        law_display = ""
        if ref.article_title or ref.article_text:
            title_pfx = f"[{ref.article_title}]\n" if ref.article_title else ""
            law_display = f"{title_pfx}{ref.article_text}".strip()
        else:
            try:
                from taskcalendar.law_reference import get_law_article, search_laws
                mst = ref.mst
                if not mst:
                    s_res = search_laws(ref.law_name, limit=1, timeout=3, include_history=False)
                    if s_res:
                        mst = s_res[0].mst
                        ref.mst = mst
                        ref.official_name = s_res[0].name
                        ref.latest_enforce_date = s_res[0].enforce_date
                        ref.office = s_res[0].office

                if mst and ref.article_no:
                    art_dict = get_law_article(mst, ref.article_no, ref.sub_article_no)
                    if art_dict:
                        ref.article_title = art_dict.get("title", "")
                        ref.article_text = art_dict.get("full_text", "")
                        title_pfx = f"[{ref.article_title}]\n" if ref.article_title else ""
                        law_display = f"{title_pfx}{ref.article_text}".strip()
            except Exception as ex:
                logger.debug(f"Failed to fetch law article on demand: {ex}")

        if not law_display:
            law_display = f"법령명: {ref.official_name or ref.law_name}\n(상세 조문 원문이 본문에 인용되지 않았거나 조문 번호가 지정되지 않았습니다.)"

        self.txt_law_content.setPlainText(law_display)

    def _start_ai_comparison(self):
        """AI API를 호출하여 문맥 단위 개별 수정안 카드 도출"""
        doc_text = self.txt_doc_content.toPlainText().strip()
        law_text = self.txt_law_content.toPlainText().strip()

        if not doc_text or not law_text:
            self.lbl_ai_status.setText("대조할 문서 내용 또는 법령 조문이 비어있습니다.")
            return

        cfg = AIConfigManager.load()
        api_key = str(cfg.get("api_key", "")).strip()
        if not api_key:
            self.lbl_ai_status.setText("AI API 키가 설정되어 있지 않습니다. [스마트 부가기능]에서 API 키를 등록해 주세요.")
            return

        self.btn_run_ai.setEnabled(False)
        self.btn_apply_update.setEnabled(False)
        self.ai_progress.show()
        self.lbl_ai_status.setText("AI가 문서 내용을 문맥 단위로 정밀 대조 분석 중입니다...")

        # 현재 대상 법령으로 검토 범위를 엄격히 한정하는 문맥 단위 팩트체크 프롬프트
        curr_law_name = self.ref.official_name or self.ref.law_name
        system_prompt = (
            "당신은 공문서 및 업무 규정의 법률 적합성을 검토하고 문맥 단위로 교정(Proofreading)하는 최고 전문 AI입니다.\n"
            f"★ 현재 검토 대상 법령: 「{self.ref.display_title}」 (공식 명칭: {curr_law_name})\n\n"
            "★ 엄격한 검토 범위 한정 규칙 (반드시 철저히 준수할 것):\n"
            f"1. [오직 대상 법령만 검토]: 당신은 오직 현재 선택된 대상 법령인 「{self.ref.display_title}」에 대해서만 검토해야 합니다!\n"
            f"2. [타 법령 절대 배제]: 문서 내에 다른 법령(예: 개인정보 보호법, 근로기준법, 지방자치법 등)에 관한 내용이 있더라도, 그것은 현재 검토 대상 법령인 「{self.ref.display_title}」과 무관하므로 일체 무시하고 절대로 지적하거나 수정안에 포함하지 마십시오!\n"
            f"3. [관련 내용 없을 시]: 만약 문서 내에 「{self.ref.display_title}」과 관련된 구체적인 업무 서술(차량 등록, 정비, 검사 등)이 없거나 단순 명칭만 적혀 있다면, 'has_issue': false, 'suggestions': [] 로 응답하고, '문서 내에 「{self.ref.display_title}」과 관련된 구체적인 업무 서술이나 상충되는 내용이 없습니다.'라고만 보고하십시오.\n"
            f"4. [문맥 단위 수정]: 오직 「{self.ref.display_title}」의 최신 조문과 직접 상충되거나 법리적으로 잘못 서술된 특정 문장만을 target_text로 추출하십시오.\n"
            "5. [원문 일치 필수]: target_text는 반드시 사용자가 작성한 본문 텍스트 내에 실제로 존재하는 문장/어구여야 합니다 (치환 대상이 됨).\n\n"
            "반드시 아래 JSON 형식으로만 응답하십시오(마크다운 코드블록 없이 순수 JSON):\n"
            "{\n"
            '  "has_issue": true/false,\n'
            f'  "check_summary": "「{self.ref.display_title}」에 대한 대조 검토 요약 (타 법령 언급 절대 금지, 1~2문장)",\n'
            '  "suggestions": [\n'
            '    {\n'
            '      "target_text": "문서 내에서 현재 대상 법령과 상충되는 원래 문장 (원문과 완전히 일치해야 함)",\n'
            f'      "reason": "「{self.ref.display_title}」 기준에 비추어 왜 잘못되었는지 구체적 법적 사유",\n'
            '      "replacement_text": "원문의 어투와 문맥을 살려 해당 문장만 올바르게 고친 수정안"\n'
            '    }\n'
            '  ]\n'
            "}"
        )

        user_prompt = (
            f"=== [검토 대상 법령]: {self.ref.display_title} ===\n"
            f"국가법령정보센터 최신 조문 원문:\n{law_text}\n\n"
            f"=== 내 업무 문서 전체 내용 ===\n{doc_text}\n\n"
            f"※ 주의: 문서 내에 다른 법령(예: 개인정보보호법 등) 내용이 있더라도 일체 무시하십시오. 오직 「{self.ref.display_title}」과 직접 관련된 내용만 찾아 상충되는 문장을 검토해 주세요."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.stop()
            self._ai_worker.wait(200)

        self._ai_worker = AIChatWorker(messages, cfg, self)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_finished(self, response_text: str):
        """AI 분석 완료 처리 (문맥별 카드 위젯 동적 생성)"""
        self.ai_progress.hide()
        self.btn_run_ai.setEnabled(True)
        self.btn_run_ai.setText("AI 대조 재분석")

        clean_resp = response_text.strip()
        if clean_resp.startswith("```"):
            clean_resp = re.sub(r"^```[a-zA-Z]*\n?", "", clean_resp)
            clean_resp = re.sub(r"\n?```$", "", clean_resp).strip()

        parsed = None
        try:
            parsed = json.loads(clean_resp)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", clean_resp)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    pass

        # 기존 카드 정리
        self._clear_suggestion_cards()

        if parsed and isinstance(parsed, dict):
            has_issue = bool(parsed.get("has_issue", False))
            summary = parsed.get("check_summary", "")
            suggestions = parsed.get("suggestions", [])

            if has_issue and suggestions:
                self.lbl_ai_status.setText(f"<b>검토 완료:</b> {summary} <span style='color: #2563EB; font-weight: bold;'>- 반영할 항목을 선택하세요</span>")
                self.btn_select_all.show()
                self.btn_deselect_all.show()

                for idx, s in enumerate(suggestions, 1):
                    target = s.get("target_text", "")
                    reason = s.get("reason", "")
                    replacement = s.get("replacement_text", "")
                    if target and replacement:
                        card = LawSuggestionCard(idx, target, reason, replacement, self.cards_container)
                        card.toggled.connect(self._update_apply_button_state)
                        self.cards_layout.addWidget(card)
                        self._suggestion_cards.append(card)

                self.cards_layout.addStretch(1)
                self._update_apply_button_state()
            else:
                clean_summary = summary or "작성하신 내용 중 해당 법령 조문과 상충되거나 잘못된 부분이 없습니다."
                self.lbl_ai_status.setText("<b>검토 완료:</b> 법령과 상충되는 내용이 없습니다.")
                no_issue_lbl = QLabel(
                    f"• {clean_summary}\n\n"
                    "• 업무 문서에 작성된 내용이 해당 법령 기준에 부합하며, 위배되거나 잘못된 서술이 발견되지 않았습니다.\n"
                    "• 현재 작성된 문안을 그대로 유지하셔도 좋습니다.",
                    self.cards_container,
                )
                no_issue_lbl.setWordWrap(True)
                no_issue_lbl.setStyleSheet("color: #334155; font-size: 12px; padding: 14px 10px; background: transparent; border: none;")
                self.cards_layout.addWidget(no_issue_lbl)
                self.cards_layout.addStretch(1)
                self.btn_apply_update.setEnabled(False)
                self.btn_apply_update.setText("선택한 수정안 문서 반영")
        else:
            fallback_lbl = QLabel("AI 응답을 처리하는 중 형식을 파악하지 못했습니다.\n\n" + clean_resp, self.cards_container)
            fallback_lbl.setStyleSheet("color: #DC2626; padding: 12px;")
            self.cards_layout.addWidget(fallback_lbl)
            self.cards_layout.addStretch(1)

    def _clear_suggestion_cards(self):
        """카드 목록 초기화"""
        self._suggestion_cards.clear()
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.btn_select_all.hide()
        self.btn_deselect_all.hide()

    def _set_all_suggestions_checked(self, checked: bool):
        for card in self._suggestion_cards:
            card.set_selected(checked)
        self._update_apply_button_state()

    def _update_apply_button_state(self):
        """체크된 카드 개수에 따라 하단 반영 버튼 텍스트 및 활성화 상태 갱신"""
        selected_count = sum(1 for card in self._suggestion_cards if card.is_selected())
        if selected_count > 0:
            self.btn_apply_update.setEnabled(True)
            self.btn_apply_update.setText(f"선택한 {selected_count}개 항목만 문서에 반영")
        else:
            self.btn_apply_update.setEnabled(False)
            self.btn_apply_update.setText("반영할 항목을 선택해 주세요")

    def _on_ai_error(self, err_msg: str):
        """AI 분석 오류 처리"""
        self.ai_progress.hide()
        self.btn_run_ai.setEnabled(True)
        self.lbl_ai_status.setText(f"AI 분석 중 오류가 발생했습니다.")
        self._clear_suggestion_cards()
        err_lbl = QLabel(f"AI 통신 오류: {err_msg}\n\n우측 법령 원문을 참고하여 수동으로 수정하거나 원문 삽입을 진행하세요.", self.cards_container)
        err_lbl.setStyleSheet("color: #DC2626; padding: 12px;")
        self.cards_layout.addWidget(err_lbl)
        self.cards_layout.addStretch(1)

    def _open_law_web(self):
        """국가법령정보센터 웹페이지 열기 (버튼 클릭 시에만)"""
        if self.ref.url:
            QDesktopServices.openUrl(self.ref.url)

    def _on_apply_selected_clicked(self):
        """체크된 개별 문맥 항목들만 문서에 스마트 치환 반영"""
        selected_pairs = [card.get_replacement_pair() for card in self._suggestion_cards if card.is_selected()]
        if not selected_pairs:
            QMessageBox.warning(self, "안내", "선택된 수정 항목이 없습니다.")
            return

        confirm_msg = (
            f"선택하신 {len(selected_pairs)}개 문맥 항목을 업무 문서에 반영하시겠습니까?\n\n"
            f"• 적용 법령: {self.ref.display_title}\n"
            f"• 선택 항목 외의 나머지 문장은 원래 작성하신 내용 그대로 유지됩니다."
        )
        if QMessageBox.question(self, "선택 항목 반영 승인", confirm_msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            if callable(self.on_apply_update):
                self.on_apply_update(selected_pairs, "")
            self.update_applied.emit(selected_pairs, "")
            QMessageBox.information(self, "반영 완료", f"선택하신 {len(selected_pairs)}개 항목이 최신 법령에 맞게 문서에 성공적으로 치환 반영되었습니다.")
            self.accept()

    def _on_manual_append_clicked(self):
        """최신 법령 조문 원문 블록을 본문에 직접 추가"""
        law_text = self.txt_law_content.toPlainText().strip()
        if not law_text:
            QMessageBox.warning(self, "안내", "반영할 법령 조문 내용이 없습니다.")
            return

        confirm_msg = (
            f"국가법령정보센터 최신 조문 원문 블록을 문서에 삽입하시겠습니까?\n\n"
            f"적용 법령: {self.ref.display_title}"
        )
        if QMessageBox.question(self, "조문 원문 삽입", confirm_msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            update_snippet = (
                f"\n\n[법령 근거: {self.ref.display_title} (시행 {self.ref.latest_enforce_date or self.ref.enforce_date})]\n"
                f"{law_text}\n"
            )
            target_str = self.ref.raw_matched
            if callable(self.on_apply_update):
                self.on_apply_update(target_str, update_snippet)
            self.update_applied.emit(target_str, update_snippet)
            QMessageBox.information(self, "완료", "최신 법령 조문 원문이 업무 문서에 반영되었습니다.")
            self.accept()

    def closeEvent(self, event):
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.stop()
            self._ai_worker.wait(200)
        super().closeEvent(event)
