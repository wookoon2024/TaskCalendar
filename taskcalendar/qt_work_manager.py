from __future__ import annotations

import logging
import os
import subprocess
from datetime import date
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QSplitter,
    QStyle,
    QStyleFactory,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css, scale_px, ui_font_family
from taskcalendar.paths import asset_path
from taskcalendar.qt_styles import _shade, resolve_palette
from taskcalendar.qt_rhwp_editor import RhwpEditorWidget
from taskcalendar.rich_text_edit import RichTextEdit
from taskcalendar.storage import EncryptedRepository

logger = logging.getLogger(__name__)


def get_file_extension_icon(filename: str) -> str:
    """확장자에 따른 직관적인 아이콘 이모지 반환"""
    ext = Path(filename).suffix.lower()
    if ext in (".hwp", ".hwpx"):
        return "📄"
    elif ext in (".xlsx", ".xls", ".csv"):
        return "📊"
    elif ext in (".pdf",):
        return "📕"
    elif ext in (".docx", ".doc", ".txt"):
        return "📝"
    elif ext in (".pptx", ".ppt"):
        return "📑"
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"):
        return "🖼️"
    elif ext in (".zip", ".rar", ".7z", ".tar", ".gz"):
        return "📦"
    elif ext in (".mp4", ".avi", ".mkv", ".mov"):
        return "🎬"
    elif ext in (".mp3", ".wav", ".m4a"):
        return "🎵"
    elif ext in (".py", ".js", ".html", ".css", ".json", ".xml"):
        return "💻"
    return "📎"


class CompactCategoryItemDelegate(QStyledItemDelegate):
    """트리 항목의 텍스트 영역만 둥근 알약형으로 하이라이트하고 왼쪽 인덴트/가지 영역은 제외"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        is_sel = bool(view and view.selectionModel() and view.selectionModel().isSelected(index))
        is_hover = bool(opt.state & QStyle.State_MouseOver)
        is_parent = not index.parent().isValid()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if is_sel and not is_parent:
            bg_rect = opt.rect.adjusted(0, 1, -2, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#E0F2FE'))
            painter.drawRoundedRect(bg_rect, 4, 4)
            painter.setPen(QColor('#0284C7'))
        elif is_hover and not is_parent:
            bg_rect = opt.rect.adjusted(0, 1, -2, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#F1F5F9'))
            painter.drawRoundedRect(bg_rect, 4, 4)
            painter.setPen(QColor('#0F172A'))
        else:
            painter.setPen(QColor('#1F2328') if is_parent else QColor('#334155'))

        font = opt.font
        if is_parent or is_sel:
            font.setBold(True)
        painter.setFont(font)

        text_rect = opt.rect.adjusted(4, 0, -4, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, opt.text)
        painter.restore()


class CompactCategoryTree(QTreeWidget):
    """왼쪽 인덴트/가지 영역에 선택 박스가 칠해지지 않고 텍스트 아이템만 깔끔하게 선택되는 트리 위젯"""

    def drawRow(self, painter, option, index):
        if self.model().hasChildren(index):
            item = self.itemFromIndex(index)
            vr = self.visualItemRect(item)
            b_rect = QRect(0, option.rect.y(), vr.left(), option.rect.height())
            self.drawBranches(painter, b_rect, index)
        self.itemDelegate().paint(painter, option, index)


class _CleanTreeProxyStyle(QProxyStyle):
    """트리 뷰의 OS 기본 포커스 사각 테두리 및 인디케이터 잔상을 깔끔하게 제거하는 프록시 스타일"""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PrimitiveElement.PE_FrameFocusRect, QStyle.PrimitiveElement.PE_PanelItemViewRow):
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            if option.state & QStyle.State_Children:
                super().drawPrimitive(element, option, painter, widget)
            return
        super().drawPrimitive(element, option, painter, widget)


class WorkSheetData:
    """단위 업무 시트 메모리 데이터 구조"""

    def __init__(
        self,
        sheet_id: str,
        title: str,
        category: str = "일반 업무",
        cycle: str = "수시",
        assignee: str = "",
        deadline: str = "",
        content_html: str = "",
        content_text: str = "",
        hwpx_blob: bytes | None = None,
        db_id: int | None = None,
        attachments: list[dict] | None = None,
    ):
        self.db_id = db_id
        self.sheet_id = sheet_id
        self.title = title
        self.category = category
        self.cycle = cycle
        self.assignee = assignee
        self.deadline = deadline
        self.content_html = content_html
        self.content_text = content_text
        self.hwpx_blob = hwpx_blob
        self.attachments = attachments or []  # [{"id": 1, "name": "...", "path": "...", "size": "..."}]


class WorkManagerDialog(QDialog):
    """
    3단 분할 레이아웃 업무 관리 및 인수인계 편람 창
    - 상단: 통합 검색 바 & 좌우 패널 토글 버튼
    - 중간: [좌: 업무 분류 트리] | [중: rhwp 리치 에디터] | [우: 첨부파일 관리 패널]
    - 하단: 엑셀/한글 스타일 멀티 시트(Tab) 바
    """

    def __init__(self, parent: QWidget | None = None, repository: EncryptedRepository = None, main_window=None) -> None:
        super().__init__(None)  # 독립 탑레벨 윈도우로 동작하여 캘린더 부모창 깜빡임/숨김 차단
        self.repository = repository
        self.main_window = main_window

        # 테마 팔레트 추출
        p = getattr(main_window, "palette", None) or getattr(parent, "palette", None)
        if not isinstance(p, dict) or not p.get("bg"):
            from taskcalendar.themes import THEMES
            p = THEMES.get("default", {})
        self.palette = p

        self.setWindowTitle("업무 관리 및 인수인계 편람")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1180, 740)
        self.setMinimumSize(880, 540)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # 시트 및 카테고리 데이터
        self._categories: list[str] = []
        self._all_sheets: list[WorkSheetData] = []  # DB에 존재하는 전체 업무 목록 (좌측 트리에 표시)
        self._open_sheets: list[WorkSheetData] = []  # 현재 하단 탭에 열려있는 업무 목록
        self._active_sheet_index: int = -1
        self._is_loading_sheet: bool = False

        self._load_data_from_db()
        self._init_ui()
        if self._all_sheets:
            # 시작 시 전체를 열지 않고 첫 번째 문서만 1개 탭으로 기본 오픈
            self.open_sheet(self._all_sheets[0])

    @property
    def _sheets(self) -> list[WorkSheetData]:
        """외부 및 하위 호환용: 현재 열려있는 시트 목록 반환"""
        return self._open_sheets

    def _load_data_from_db(self) -> None:
        """SQLite DB에서 업무 분류 및 시트 데이터 로드"""
        if not self.repository:
            self._init_sample_data()
            return

        cat_rows = self.repository.list_work_categories()
        self._categories = [c["name"] for c in cat_rows]
        if not self._categories:
            self._categories = ["1. 부서 총괄 및 서무", "2. 예산 및 회계 관리", "3. 고유 사업 및 정책"]

        item_rows = self.repository.list_work_items()
        self._all_sheets = []
        for r in item_rows:
            raw_atts = r.get("attachments", [])
            norm_atts = []
            for a in raw_atts:
                norm_atts.append({
                    "id": a.get("id"),
                    "name": a.get("file_name", ""),
                    "path": a.get("file_path", ""),
                    "size": a.get("file_size", ""),
                })
            sheet = WorkSheetData(
                db_id=r["id"],
                sheet_id=f"sheet_{r['id']}",
                title=r["title"],
                category=r.get("category_name") or (self._categories[0] if self._categories else "기본 분류"),
                cycle=r.get("cycle", "수시"),
                assignee=r.get("assignee", ""),
                deadline=r.get("deadline", ""),
                content_html=r.get("content_html", ""),
                content_text=r.get("content_text", ""),
                hwpx_blob=r.get("hwpx_blob"),
                attachments=norm_atts,
            )
            self._all_sheets.append(sheet)

        if not self._all_sheets:
            self._init_sample_data()

    def _init_sample_data(self) -> None:
        """기본 샘플 업무 시트 구성"""
        sample1_html = """
        <h2 style="color: #1D4ED8; margin-bottom: 8px;">1. 업무 개요 및 목적</h2>
        <p>본 업무는 부서 소관 <b>공용차량 운용, 일상 배차, 정기 안전점검 및 유류비 정산</b>을 체계적으로 관리하여 행정 공백을 방지하는 것을 목적으로 합니다.</p>
        <br>
        <div style="background-color: #FEF2F2; border-left: 4px solid #EF4444; padding: 8px 12px; margin: 8px 0; border-radius: 4px;">
            <b style="color: #DC2626;">🚨 [필수확인]</b> 매월 25일까지 회계과에 전월 주행일지 및 유류대 정산 결재공문을 발송해야 다음 달 유류카드 한도가 연장됩니다.
        </div>
        <br>
        <h2 style="color: #1D4ED8; margin-bottom: 8px;">2. 업무 추진 절차 (Step-by-Step)</h2>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse; width: 100%; border-color: #CBD5E1;">
            <tr style="background-color: #F1F5F9; color: #1E293B;">
                <th style="width: 15%; text-align: center;">단계</th>
                <th style="width: 25%; text-align: center;">추진 내용</th>
                <th style="width: 35%; text-align: center;">세부 확인사항</th>
                <th style="width: 25%; text-align: center;">관련 서식 / 근거</th>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold;">1단계</td>
                <td>배차 신청 및 승인</td>
                <td>출장신청서 승인 여부 확인, 운행 목적지 기재</td>
                <td>배차신청서(온나라)</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold;">2단계</td>
                <td>운행 및 일지 작성</td>
                <td>운행 전/후 주행거리계(km) 확인 및 서명</td>
                <td>차량운행일지.xlsx</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: bold;">3단계</td>
                <td>정기점검 및 오일교환</td>
                <td>분기별 1회 지정정비소 입고 점검</td>
                <td>정기점검지침.hwpx</td>
            </tr>
        </table>
        <br>
        <div style="background-color: #FFFBEB; border-left: 4px solid #F59E0B; padding: 8px 12px; margin: 8px 0; border-radius: 4px;">
            <b style="color: #D97706;">⚠️ [감사주의]</b> 공휴일 또는 주말에 차량을 운행할 경우, 반드시 사전 승인 결재문서 번호를 주행일지에 기재해야 감사 시 지적되지 않습니다.
        </div>
        <br>
        <div style="background-color: #EFF6FF; border-left: 4px solid #3B82F6; padding: 8px 12px; margin: 8px 0; border-radius: 4px;">
            <b style="color: #2563EB;">💡 [실무팁]</b> 하이패스 단말기 통행료 청구서는 매월 초 도로공사 포털에서 일괄 다운로드하여 첨부하면 정산이 10분 만에 끝납니다.
        </div>
        """

        sample2_html = """
        <h2 style="color: #1D4ED8; margin-bottom: 8px;">1. 예산 집행 개요</h2>
        <p>부서 일상경비 및 세출예산 집행 절차와 품의·원인행위·지출결의 가이드라인입니다.</p>
        <br>
        <div style="background-color: #F0FDF4; border-left: 4px solid #10B981; padding: 8px 12px; margin: 8px 0; border-radius: 4px;">
            <b style="color: #059669;">📌 [관련규정]</b> 지방재정법 제00조, 행정안전부 세출예산 집행기준
        </div>
        """

        self._all_sheets = [
            WorkSheetData(
                sheet_id="sheet_1",
                title="공용차량 운용 및 관리 매뉴얼",
                category="1. 부서 총괄 및 서무",
                cycle="매월",
                assignee="홍길동",
                deadline="매월 25일",
                content_html=sample1_html,
                attachments=[
                    {"name": "차량운행일지_표준서식.xlsx", "size": "18.4 KB", "path": ""},
                    {"name": "공용차량_관리규정(훈령).hwpx", "size": "45.1 KB", "path": ""},
                    {"name": "2026년도_정기점검_예시.pdf", "size": "112.0 KB", "path": ""},
                ],
            ),
            WorkSheetData(
                sheet_id="sheet_2",
                title="부서 일상경비 및 세출예산 집행",
                category="2. 예산 및 회계 관리",
                cycle="매주",
                assignee="김철수",
                deadline="매주 금요일",
                content_html=sample2_html,
                attachments=[
                    {"name": "지출결의서_체크리스트.hwp", "size": "32.0 KB", "path": ""},
                ],
            ),
            WorkSheetData(
                sheet_id="sheet_3",
                title="정보화시스템 정기 유지보수 점검",
                category="3. 고유 사업 및 정책",
                cycle="분기",
                assignee="이영희",
                deadline="분기말",
                content_html="<h2>유지보수 용역 점검표 작성 절차</h2><p>용역업체 월간 보고서 검수 및 기성금 청구 안내</p>",
                attachments=[],
            ),
        ]

    def _init_ui(self) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.palette.get("bg", "#F1F5F9")};
                color: {text};
            }}
            QSplitter::handle {{
                background-color: {line};
                width: 1px;
            }}
            QSplitter::handle:hover {{
                background-color: {accent};
                width: 3px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 8)
        main_layout.setSpacing(8)

        # =========================================================================
        # 1. 맨 위: 통합 검색 및 상단 도구 바
        # =========================================================================
        top_bar = QFrame()
        top_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
                padding: 4px 8px;
            }}
        """)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(4, 2, 4, 2)
        top_layout.setSpacing(8)

        # 검색 입력창
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 전체 업무 및 본문 내용, 첨부파일 통합 검색 (키워드 또는 질문 입력)...")
        self.search_input.setFixedHeight(32)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 13px;
                color: {text};
            }}
            QLineEdit:focus {{
                background-color: {panel};
                border-color: {accent};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        top_layout.addWidget(self.search_input, 1)

        # 새 업무 추가 버튼
        btn_new_work = QPushButton("➕ 새 업무")
        btn_new_work.setFixedHeight(30)
        btn_new_work.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {_shade(accent, -0.1)};
            }}
        """)
        btn_new_work.clicked.connect(self._on_add_new_sheet)
        top_layout.addWidget(btn_new_work)

        # 저장 버튼 (Ctrl+S)
        btn_save_work = QPushButton("💾 저장")
        btn_save_work.setFixedHeight(30)
        btn_save_work.setToolTip("현재 업무 문서 및 변경사항 저장 (Ctrl+S)")
        btn_save_work.setShortcut(QKeySequence("Ctrl+S"))
        btn_save_work.setStyleSheet(f"""
            QPushButton {{
                background-color: #10B981;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 14px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #059669;
            }}
        """)
        btn_save_work.clicked.connect(self._on_save_button_clicked)
        top_layout.addWidget(btn_save_work)

        main_layout.addWidget(top_bar)

        # =========================================================================
        # 2. 중간: 좌 / 중 / 우 3분할 스플리터
        # =========================================================================
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)

        # -------------------------------------------------------------------------
        # [중간 - 좌측 컨테이너]: 업무 분류 트리 (열림) + 슬림 바 (닫힘)
        # -------------------------------------------------------------------------
        self._left_expanded = True
        self._last_left_width = 220

        self.left_container = QWidget()
        self.left_container.setMinimumWidth(130)
        left_container_layout = QHBoxLayout(self.left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(0)

        # 1) 좌측 패널 (펼침 상태)
        self.left_panel = QFrame()
        self.left_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        left_h_layout = QHBoxLayout(self.left_panel)
        left_h_layout.setContentsMargins(4, 6, 2, 6)
        left_h_layout.setSpacing(2)

        # 좌측 패널 본체
        left_main_widget = QWidget()
        left_layout = QVBoxLayout(left_main_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        left_header = QHBoxLayout()
        left_header.setContentsMargins(2, 0, 2, 0)
        left_title = QLabel("📁 업무 분류")
        left_title.setStyleSheet(f"font-weight: bold; font-size: 12px; color: {text}; border: none;")
        left_header.addWidget(left_title)
        left_header.addStretch(1)

        btn_add_cat = QPushButton("+ 분류")
        btn_add_cat.setFixedHeight(20)
        btn_add_cat.setStyleSheet(self._sub_btn_style())
        btn_add_cat.clicked.connect(self._on_add_category)
        left_header.addWidget(btn_add_cat)
        left_layout.addLayout(left_header)

        self.category_tree = CompactCategoryTree()
        self.category_tree.setStyle(QStyleFactory.create("Fusion"))
        self.category_tree.setItemDelegate(CompactCategoryItemDelegate(self.category_tree))
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setIndentation(18)
        self.category_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_tree.setRootIsDecorated(True)
        self.category_tree.setAnimated(False)
        self.category_tree.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                padding: 2px 0px;
                outline: none;
            }}
            QTreeWidget::item {{
                height: 22px;
                padding: 0px 4px;
                margin: 1px 2px;
                border: none;
                background: transparent;
            }}
            QTreeWidget::branch {{
                background: transparent;
            }}
        """)
        self.category_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.category_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        left_layout.addWidget(self.category_tree)
        left_h_layout.addWidget(left_main_widget, 1)

        # 좌측 패널 우측 경계면 - 위아래 중간에 위치한 [◀] 버튼 거터
        left_gutter = QWidget()
        left_gutter_layout = QVBoxLayout(left_gutter)
        left_gutter_layout.setContentsMargins(0, 0, 0, 0)
        left_gutter_layout.setSpacing(0)
        left_gutter_layout.addStretch(1)

        self.btn_collapse_left = QPushButton("◀")
        self.btn_collapse_left.setToolTip("업무 분류 패널 접기 (◀)")
        self.btn_collapse_left.setFixedSize(16, 44)
        self.btn_collapse_left.setStyleSheet(self._gutter_arrow_style())
        self.btn_collapse_left.clicked.connect(self._collapse_left_panel)
        left_gutter_layout.addWidget(self.btn_collapse_left)

        left_gutter_layout.addStretch(1)
        left_h_layout.addWidget(left_gutter, 0)

        left_container_layout.addWidget(self.left_panel)

        # 2) 좌측 슬림 바 (접힘 상태) - 위아래 중간에 위치한 [▶] 버튼
        self.left_collapsed_bar = QFrame()
        self.left_collapsed_bar.setFixedWidth(24)
        self.left_collapsed_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.left_collapsed_bar.setToolTip("클릭하여 업무 분류 패널 펼치기 (▶)")
        self.left_collapsed_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
            }}
            QFrame:hover {{
                border-color: {accent};
                background-color: {accent_soft};
            }}
        """)
        left_col_layout = QVBoxLayout(self.left_collapsed_bar)
        left_col_layout.setContentsMargins(3, 8, 3, 8)
        left_col_layout.setSpacing(0)
        left_col_layout.addStretch(1)

        btn_expand_left = QPushButton("▶")
        btn_expand_left.setToolTip("업무 분류 패널 펼치기 (▶)")
        btn_expand_left.setFixedSize(16, 44)
        btn_expand_left.setStyleSheet(self._gutter_arrow_style())
        btn_expand_left.clicked.connect(self._expand_left_panel)
        left_col_layout.addWidget(btn_expand_left, alignment=Qt.AlignmentFlag.AlignCenter)

        left_col_layout.addStretch(1)

        self.left_collapsed_bar.mousePressEvent = lambda e: self._expand_left_panel()
        self.left_collapsed_bar.hide()
        left_container_layout.addWidget(self.left_collapsed_bar)

        self.splitter.addWidget(self.left_container)

        # -------------------------------------------------------------------------
        # [중간 - 중앙 패널]: rhwp / 리치 에디터
        # -------------------------------------------------------------------------
        self.center_panel = QFrame()
        self.center_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # 데이터 바인딩용 헤드리스 위젯 (사용자 요청: 상단 제목 및 메타정보 바 제거, 하단 탭 및 카테고리로 관리)
        self.work_title_input = QLineEdit()
        self.work_title_input.textChanged.connect(self._on_title_text_changed)

        self.meta_cat_combo = QComboBox()
        self.meta_cat_combo.addItems(self._categories)
        self.meta_cat_combo.currentTextChanged.connect(self._on_meta_cat_changed)

        self.meta_cycle_combo = QComboBox()
        self.meta_cycle_combo.addItems(["매일", "매주", "매월", "분기", "반기", "연간", "수시"])
        self.meta_cycle_combo.currentTextChanged.connect(self._on_meta_cycle_changed)

        self.meta_assignee_input = QLineEdit()
        self.meta_assignee_input.textChanged.connect(self._on_meta_assignee_changed)

        self.meta_deadline_input = QLineEdit()
        self.meta_deadline_input.textChanged.connect(self._on_meta_deadline_changed)

        # 본문 웹 에디터 (rhwp 오픈소스 엔진 탑재) - 패널 전체에 깔끔하게 배치
        self.editor = RhwpEditorWidget(self, palette=self.palette)
        self.editor.contentChanged.connect(self._on_editor_text_changed)
        center_layout.addWidget(self.editor, 1)

        self.splitter.addWidget(self.center_panel)

        # -------------------------------------------------------------------------
        # [중간 - 우측 컨테이너]: 첨부파일 관리 (열림) + 슬림 바 (닫힘)
        # -------------------------------------------------------------------------
        self._right_expanded = True
        self._last_right_width = 220

        self.right_container = QWidget()
        self.right_container.setMinimumWidth(130)
        right_container_layout = QHBoxLayout(self.right_container)
        right_container_layout.setContentsMargins(0, 0, 0, 0)
        right_container_layout.setSpacing(0)

        # 1) 우측 슬림 바 (접힘 상태) - 위아래 중간에 위치한 [◀] 버튼
        self.right_collapsed_bar = QFrame()
        self.right_collapsed_bar.setFixedWidth(24)
        self.right_collapsed_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.right_collapsed_bar.setToolTip("클릭하여 첨부파일 패널 펼치기 (◀)")
        self.right_collapsed_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
            }}
            QFrame:hover {{
                border-color: {accent};
                background-color: {accent_soft};
            }}
        """)
        right_col_layout = QVBoxLayout(self.right_collapsed_bar)
        right_col_layout.setContentsMargins(3, 8, 3, 8)
        right_col_layout.setSpacing(0)
        right_col_layout.addStretch(1)

        btn_expand_right = QPushButton("◀")
        btn_expand_right.setToolTip("첨부파일 패널 펼치기 (◀)")
        btn_expand_right.setFixedSize(16, 44)
        btn_expand_right.setStyleSheet(self._gutter_arrow_style())
        btn_expand_right.clicked.connect(self._expand_right_panel)
        right_col_layout.addWidget(btn_expand_right, alignment=Qt.AlignmentFlag.AlignCenter)

        right_col_layout.addStretch(1)

        self.right_collapsed_bar.mousePressEvent = lambda e: self._expand_right_panel()
        self.right_collapsed_bar.hide()
        right_container_layout.addWidget(self.right_collapsed_bar)

        # 2) 우측 패널 (펼침 상태)
        self.right_panel = QFrame()
        self.right_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        right_h_layout = QHBoxLayout(self.right_panel)
        right_h_layout.setContentsMargins(4, 8, 8, 8)
        right_h_layout.setSpacing(4)

        # 우측 패널 좌측 경계면 - 위아래 중간에 위치한 [▶] 버튼 거터
        right_gutter = QWidget()
        right_gutter_layout = QVBoxLayout(right_gutter)
        right_gutter_layout.setContentsMargins(0, 0, 0, 0)
        right_gutter_layout.setSpacing(0)
        right_gutter_layout.addStretch(1)

        self.btn_collapse_right = QPushButton("▶")
        self.btn_collapse_right.setToolTip("첨부파일 패널 접기 (▶)")
        self.btn_collapse_right.setFixedSize(16, 44)
        self.btn_collapse_right.setStyleSheet(self._gutter_arrow_style())
        self.btn_collapse_right.clicked.connect(self._collapse_right_panel)
        right_gutter_layout.addWidget(self.btn_collapse_right)

        right_gutter_layout.addStretch(1)
        right_h_layout.addWidget(right_gutter, 0)

        # 우측 패널 본체
        right_main_widget = QWidget()
        right_layout = QVBoxLayout(right_main_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        right_header = QHBoxLayout()
        self.right_title = QLabel("📎 첨부파일 (0)")
        self.right_title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {text}; border: none;")
        right_header.addWidget(self.right_title)
        right_header.addStretch(1)

        btn_add_file = QPushButton("+ 파일 추가")
        btn_add_file.setFixedHeight(22)
        btn_add_file.setStyleSheet(self._sub_btn_style())
        btn_add_file.clicked.connect(self._on_add_attachment)
        right_header.addWidget(btn_add_file)
        right_layout.addLayout(right_header)

        # 파일 드래그앤드롭 안내 및 리스트
        self.file_list = QListWidget()
        self.file_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.file_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                padding: 2px;
                outline: none;
            }}
            QListWidget::item {{
                height: 22px;
                padding: 0px 6px;
                margin: 1px 2px;
                border: none;
                border-radius: 3px;
            }}
            QListWidget::item:hover:!selected {{
                background-color: #F1F5F9;
                color: #0F172A;
            }}
            QListWidget::item:selected {{
                background-color: #E0F2FE;
                color: #0284C7;
                font-weight: 600;
                border: none;
                outline: none;
            }}
        """)
        self.file_list.itemDoubleClicked.connect(self._on_file_double_clicked)
        right_layout.addWidget(self.file_list)

        # 하단 액션 버튼 (열기, 삭제)
        file_btn_row = QHBoxLayout()
        file_btn_row.setSpacing(6)

        btn_open_file = QPushButton("열기")
        btn_open_file.setFixedHeight(26)
        btn_open_file.setStyleSheet(self._sub_btn_style())
        btn_open_file.clicked.connect(self._open_selected_attachment)
        file_btn_row.addWidget(btn_open_file)

        btn_delete_file = QPushButton("삭제")
        btn_delete_file.setFixedHeight(26)
        btn_delete_file.setStyleSheet(self._sub_btn_style())
        btn_delete_file.clicked.connect(self._delete_selected_attachment)
        file_btn_row.addWidget(btn_delete_file)

        right_layout.addLayout(file_btn_row)
        right_h_layout.addWidget(right_main_widget, 1)

        right_container_layout.addWidget(self.right_panel)

        self.splitter.addWidget(self.right_container)

        # 스플리터 초기 비율 설정 (좌 220px : 중 잔여 : 우 220px 동일 대칭)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([220, 760, 220])
        self.splitter.splitterMoved.connect(lambda pos, idx: self.editor.fit_page())
        main_layout.addWidget(self.splitter, 1)

        # =========================================================================
        # 3. 맨 아래: 엑셀/한글 스타일 시트(Tab) 바
        # =========================================================================
        bottom_bar = QFrame()
        bottom_bar.setFixedHeight(36)
        bottom_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 0px 4px;
            }}
        """)
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(4, 0, 4, 0)
        bottom_layout.setSpacing(6)

        self.sheet_tab_bar = QTabBar()
        self.sheet_tab_bar.setDrawBase(False)
        self.sheet_tab_bar.setTabsClosable(True)
        self.sheet_tab_bar.setMovable(True)
        self.sheet_tab_bar.setExpanding(False)
        self.sheet_tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.sheet_tab_bar.setStyleSheet(f"""
            QTabBar {{
                background: transparent;
                border: none;
            }}
            QTabBar::tab {{
                background: #E2E8F0;
                color: #64748B;
                border: 1px solid {line};
                border-bottom: none;
                border-radius: 0px;
                padding: 4px 14px 4px 10px;
                margin-top: 4px;
                margin-right: 2px;
                font-size: 12px;
                min-width: 90px;
                max-width: 180px;
                height: 22px;
            }}
            QTabBar::tab:selected {{
                background: {panel};
                color: {accent};
                font-weight: bold;
                border: 1px solid {line};
                border-top: 3px solid {accent};
                border-bottom: 1px solid {panel};
                margin-top: 0px;
                height: 26px;
            }}
            QTabBar::tab:hover:!selected {{
                background: #FFFFFF;
                color: {text};
                border-top: 2px solid #94A3B8;
            }}
            QTabBar::close-button {{
                image: url('{str(asset_path("memo_close.svg")).replace("\\", "/")}');
                subcontrol-position: right;
                margin-left: 6px;
                margin-right: 2px;
                padding: 2px;
                border-radius: 0px;
            }}
            QTabBar::close-button:hover {{
                background-color: #FEE2E2;
            }}
        """)
        self.sheet_tab_bar.currentChanged.connect(self._on_sheet_tab_changed)
        self.sheet_tab_bar.tabCloseRequested.connect(self._on_sheet_tab_close)
        self.sheet_tab_bar.tabBarDoubleClicked.connect(self._on_sheet_tab_double_clicked)
        self.sheet_tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sheet_tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        bottom_layout.addWidget(self.sheet_tab_bar, 0)

        # 엑셀 스타일 시트 추가 [+] 버튼
        btn_add_sheet = QPushButton("＋")
        btn_add_sheet.setFixedSize(24, 24)
        btn_add_sheet.setToolTip("새 업무 시트 추가")
        btn_add_sheet.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 2px;
                font-weight: bold;
                font-size: 14px;
                color: {text};
                margin-top: 2px;
            }}
            QPushButton:hover {{
                background-color: {accent_soft};
                border: 1px solid {accent};
                color: {accent};
            }}
        """)
        btn_add_sheet.clicked.connect(self._on_add_new_sheet)
        bottom_layout.addWidget(btn_add_sheet, 0)
        bottom_layout.addStretch(1)

        main_layout.addWidget(bottom_bar)

        self._refresh_category_combos()
        self._refresh_category_tree()
        self._refresh_sheet_tabs()

    # =========================================================================
    # UI 이벤트 핸들러 & 뷰 전환
    # =========================================================================

    def _arrow_btn_style(self) -> str:
        line = self.palette.get("line", "#CBD5E0")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")
        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                padding: 0;
            }}
            QPushButton:hover {{
                border-color: {accent};
                color: {accent};
                background-color: {accent_soft};
            }}
        """

    def _gutter_arrow_style(self) -> str:
        line = self.palette.get("line", "#CBD5E0")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text_muted", "#64748B")
        accent = self.palette.get("accent", "#2563EB")
        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
                padding: 0;
            }}
            QPushButton:hover {{
                border-color: {accent};
                color: #FFFFFF;
                background-color: {accent};
            }}
        """

    def _collapse_left_panel(self) -> None:
        """좌측 업무 분류 패널 접기 (◀)"""
        if not self._left_expanded:
            return
        sizes = self.splitter.sizes()
        if sizes and sizes[0] > 60:
            self._last_left_width = sizes[0]
        self._left_expanded = False
        self.left_panel.hide()
        self.left_collapsed_bar.show()
        self.left_container.setFixedWidth(24)
        current_sizes = self.splitter.sizes()
        diff = max(0, current_sizes[0] - 24)
        self.splitter.setSizes([24, current_sizes[1] + diff, current_sizes[2]])
        self.editor.fit_page()

    def _expand_left_panel(self) -> None:
        """좌측 업무 분류 패널 펼치기 (▶)"""
        if self._left_expanded:
            return
        self._left_expanded = True
        self.left_container.setMinimumWidth(130)
        self.left_container.setMaximumWidth(16777215)
        self.left_collapsed_bar.hide()
        self.left_panel.show()
        target_w = max(self._last_left_width, 220)
        current_sizes = self.splitter.sizes()
        diff = target_w - current_sizes[0]
        new_center = max(200, current_sizes[1] - diff)
        self.splitter.setSizes([target_w, new_center, current_sizes[2]])
        self.editor.fit_page()

    def _collapse_right_panel(self) -> None:
        """우측 첨부파일 패널 접기 (▶)"""
        if not self._right_expanded:
            return
        sizes = self.splitter.sizes()
        if len(sizes) >= 3 and sizes[2] > 60:
            self._last_right_width = sizes[2]
        self._right_expanded = False
        self.right_panel.hide()
        self.right_collapsed_bar.show()
        self.right_container.setFixedWidth(24)
        current_sizes = self.splitter.sizes()
        diff = max(0, current_sizes[2] - 24)
        self.splitter.setSizes([current_sizes[0], current_sizes[1] + diff, 24])
        self.editor.fit_page()

    def _expand_right_panel(self) -> None:
        """우측 첨부파일 패널 펼치기 (◀)"""
        if self._right_expanded:
            return
        self._right_expanded = True
        self.right_container.setMinimumWidth(130)
        self.right_container.setMaximumWidth(16777215)
        self.right_collapsed_bar.hide()
        self.right_panel.show()
        target_w = max(self._last_right_width, 220)
        current_sizes = self.splitter.sizes()
        diff = target_w - current_sizes[2]
        new_center = max(200, current_sizes[1] - diff)
        self.splitter.setSizes([current_sizes[0], new_center, target_w])
        self.editor.fit_page()

    def _toggle_left_panel(self) -> None:
        if self._left_expanded:
            self._collapse_left_panel()
        else:
            self._expand_left_panel()

    def _toggle_right_panel(self) -> None:
        if self._right_expanded:
            self._collapse_right_panel()
        else:
            self._expand_right_panel()

    def _refresh_sheet_tabs(self) -> None:
        """하단 엑셀 스타일 시트 탭 바 갱신 (열려있는 문서 탭만 표시)"""
        self.sheet_tab_bar.blockSignals(True)
        while self.sheet_tab_bar.count() > 0:
            self.sheet_tab_bar.removeTab(0)

        for sheet in self._open_sheets:
            short_title = sheet.title if len(sheet.title) <= 15 else (sheet.title[:14] + "…")
            self.sheet_tab_bar.addTab(f"📄 {short_title}")

        if 0 <= self._active_sheet_index < self.sheet_tab_bar.count():
            self.sheet_tab_bar.setCurrentIndex(self._active_sheet_index)

        self.sheet_tab_bar.blockSignals(False)

    def _on_sheet_tab_changed(self, index: int) -> None:
        """하단 시트 탭 클릭 시 해당 업무 로드"""
        if index < 0 or index >= len(self._open_sheets):
            return
        self._save_current_sheet_data()
        self._active_sheet_index = index
        self._load_sheet_to_editor(index)

    def open_sheet(self, sheet: WorkSheetData) -> None:
        """문서를 하단 탭에 열고 중앙 에디터에 로드 (이미 열려있으면 해당 탭으로 전환)"""
        if sheet not in self._open_sheets:
            self._open_sheets.append(sheet)
            short_title = sheet.title if len(sheet.title) <= 15 else (sheet.title[:14] + "…")
            self.sheet_tab_bar.blockSignals(True)
            self.sheet_tab_bar.addTab(f"📄 {short_title}")
            self.sheet_tab_bar.blockSignals(False)

        tab_idx = self._open_sheets.index(sheet)
        self.sheet_tab_bar.blockSignals(True)
        self.sheet_tab_bar.setCurrentIndex(tab_idx)
        self.sheet_tab_bar.blockSignals(False)
        self._active_sheet_index = tab_idx
        self._load_sheet_to_editor(tab_idx)

    def _on_sheet_tab_close(self, index: int) -> None:
        """시트 탭 닫기 (문서 삭제가 아니며, 하단 탭에서만 닫히고 좌측 목록에는 그대로 유지)"""
        if index < 0 or index >= len(self._open_sheets):
            return

        self._save_current_sheet_data()
        self._open_sheets.pop(index)
        self.sheet_tab_bar.blockSignals(True)
        self.sheet_tab_bar.removeTab(index)
        self.sheet_tab_bar.blockSignals(False)

        if self._open_sheets:
            new_idx = min(index, len(self._open_sheets) - 1)
            self.sheet_tab_bar.blockSignals(True)
            self.sheet_tab_bar.setCurrentIndex(new_idx)
            self.sheet_tab_bar.blockSignals(False)
            self._active_sheet_index = new_idx
            self._load_sheet_to_editor(new_idx)
        else:
            self._active_sheet_index = -1
            self._clear_editor_view()

    def _clear_editor_view(self) -> None:
        """열려있는 탭이 없을 때 에디터 및 우측 패널을 빈 상태로 초기화"""
        self.work_title_input.setText("")
        self.meta_assignee_input.setText("")
        self.meta_deadline_input.setText("")
        self.editor.load_document("열린 문서 없음", "좌측 업무 분류에서 열람할 문서를 선택하거나 ➕ 새 업무 버튼을 클릭하세요.", None)
        self._refresh_attachments_list([])
        self.category_tree.blockSignals(True)
        self.category_tree.clearSelection()
        self.category_tree.blockSignals(False)

    def _on_sheet_tab_double_clicked(self, index: int) -> None:
        """하단 시트 탭 더블 클릭 시 시트 이름 변경 (엑셀 스타일)"""
        if index < 0 or index >= len(self._open_sheets):
            return
        sheet = self._open_sheets[index]
        new_title, ok = QInputDialog.getText(
            self, "업무 시트 이름 변경", "새 시트 이름:", text=sheet.title
        )
        if ok and new_title.strip():
            sheet.title = new_title.strip()
            self.work_title_input.setText(sheet.title)
            if self.repository and sheet.db_id:
                self.repository.upsert_work_item(
                    work_id=sheet.db_id,
                    title=sheet.title,
                    category_name=sheet.category,
                    cycle=sheet.cycle,
                    assignee=sheet.assignee,
                    deadline=sheet.deadline,
                    content_text=sheet.content_text,
                    content_html=sheet.content_html,
                    hwpx_blob=sheet.hwpx_blob,
                )
            short_title = sheet.title if len(sheet.title) <= 15 else (sheet.title[:14] + "…")
            self.sheet_tab_bar.setTabText(index, f"📄 {short_title}")
            self._refresh_category_tree()

    def _on_tab_context_menu(self, pos: QPoint) -> None:
        """하단 시트 탭 우클릭 컨텍스트 메뉴"""
        tab_idx = self.sheet_tab_bar.tabAt(pos)
        if tab_idx < 0 or tab_idx >= len(self._open_sheets):
            return
        target = self._open_sheets[tab_idx]
        menu = QMenu(self)
        act_close = menu.addAction("✕ 탭 닫기")
        act_rename = menu.addAction("✏️ 이름 바꾸기")
        act_duplicate = menu.addAction("📋 시트 복제")
        menu.addSeparator()
        act_delete = menu.addAction("🗑️ 업무 삭제 (DB 영구 삭제)")

        action = menu.exec(self.sheet_tab_bar.mapToGlobal(pos))
        if action == act_close:
            self._on_sheet_tab_close(tab_idx)
        elif action == act_rename:
            self._on_sheet_tab_double_clicked(tab_idx)
        elif action == act_duplicate:
            self._duplicate_sheet(target)
        elif action == act_delete:
            self._delete_sheet_permanently(target)

    def _duplicate_sheet(self, src: WorkSheetData) -> None:
        """시트 복제 및 새 탭 오픈"""
        new_title = f"{src.title} (사본)"
        db_id = None
        if self.repository:
            db_id = self.repository.upsert_work_item(
                work_id=None,
                title=new_title,
                category_name=src.category,
                cycle=src.cycle,
                assignee=src.assignee,
                deadline=src.deadline,
                content_text=src.content_text,
                content_html=src.content_html,
                hwpx_blob=src.hwpx_blob,
                sort_order=len(self._all_sheets) + 1,
            )
        new_sheet = WorkSheetData(
            db_id=db_id,
            sheet_id=f"sheet_{db_id or len(self._all_sheets)+1}",
            title=new_title,
            category=src.category,
            cycle=src.cycle,
            assignee=src.assignee,
            deadline=src.deadline,
            content_html=src.content_html,
            content_text=src.content_text,
            hwpx_blob=src.hwpx_blob,
            attachments=list(src.attachments),
        )
        self._all_sheets.append(new_sheet)
        self._refresh_category_tree()
        self.open_sheet(new_sheet)

    def _delete_sheet_permanently(self, target: WorkSheetData) -> None:
        """업무 문서 DB 영구 삭제 및 탭/트리 반영"""
        res = QMessageBox.question(
            self,
            "업무 삭제 확인",
            f"'{target.title}' 업무 문서를 영구히 삭제하시겠습니까?\n작성된 매뉴얼 및 연결 정보가 DB에서 제거됩니다.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if res == QMessageBox.Yes:
            if self.repository and target.db_id:
                self.repository.delete_work_item(target.db_id)
            if target in self._all_sheets:
                self._all_sheets.remove(target)
            if target in self._open_sheets:
                idx = self._open_sheets.index(target)
                self._open_sheets.pop(idx)
                self.sheet_tab_bar.blockSignals(True)
                self.sheet_tab_bar.removeTab(idx)
                self.sheet_tab_bar.blockSignals(False)
                if self._open_sheets:
                    new_idx = min(idx, len(self._open_sheets) - 1)
                    self.sheet_tab_bar.blockSignals(True)
                    self.sheet_tab_bar.setCurrentIndex(new_idx)
                    self.sheet_tab_bar.blockSignals(False)
                    self._active_sheet_index = new_idx
                    self._load_sheet_to_editor(new_idx)
                else:
                    self._active_sheet_index = -1
                    self._clear_editor_view()
            self._refresh_category_tree()

    def _load_sheet_to_editor(self, index: int) -> None:
        """선택된 시트 데이터를 중앙 에디터와 우측 패널에 주입"""
        if index < 0 or index >= len(self._open_sheets):
            return

        self._is_loading_sheet = True
        sheet = self._open_sheets[index]

        self.work_title_input.setText(sheet.title)

        c_idx = self.meta_cat_combo.findText(sheet.category)
        if c_idx >= 0:
            self.meta_cat_combo.setCurrentIndex(c_idx)

        cy_idx = self.meta_cycle_combo.findText(sheet.cycle)
        if cy_idx >= 0:
            self.meta_cycle_combo.setCurrentIndex(cy_idx)

        self.meta_assignee_input.setText(sheet.assignee)
        self.meta_deadline_input.setText(sheet.deadline)

        # rhwp 에디터로 본문 로드 (HWPX 바이너리가 있으면 무손실 원본 로드, 없으면 텍스트로 로드)
        text_content = sheet.content_text or sheet.title
        self.editor.load_document(
            title=sheet.title,
            text=text_content,
            hwpx_bytes=sheet.hwpx_blob,
        )

        # 우측 첨부파일 갱신
        self._refresh_attachments_list(sheet.attachments)

        # 좌측 카테고리 트리 선택 동기화
        self._sync_tree_selection(index)

        self._is_loading_sheet = False

    def _sync_tree_selection(self, index: int) -> None:
        """좌측 카테고리 트리의 선택 항목을 현재 열린 시트와 동기화"""
        self.category_tree.blockSignals(True)
        if index < 0 or index >= len(self._open_sheets):
            self.category_tree.clearSelection()
            self.category_tree.blockSignals(False)
            return

        current_sheet = self._open_sheets[index]
        for i in range(self.category_tree.topLevelItemCount()):
            top = self.category_tree.topLevelItem(i)
            for j in range(top.childCount()):
                child = top.child(j)
                item_sheet = child.data(0, Qt.UserRole)
                if item_sheet is current_sheet or (
                    current_sheet.db_id and getattr(item_sheet, "db_id", None) == current_sheet.db_id
                ):
                    self.category_tree.setCurrentItem(child)
                    self.category_tree.blockSignals(False)
                    return
        self.category_tree.blockSignals(False)

    def _save_current_sheet_data(self) -> None:
        """현재 편집 중인 시트 내용 메모리 저장 및 메타 DB 저장"""
        if self._is_loading_sheet:
            return
        if 0 <= self._active_sheet_index < len(self._open_sheets):
            current = self._open_sheets[self._active_sheet_index]
            current.title = self.work_title_input.text().strip() or "새 업무"
            current.category = self.meta_cat_combo.currentText()
            current.cycle = self.meta_cycle_combo.currentText()
            current.assignee = self.meta_assignee_input.text().strip()
            current.deadline = self.meta_deadline_input.text().strip()

    def _on_save_button_clicked(self) -> None:
        """저장 버튼(또는 Ctrl+S) 클릭 시 rhwp 에디터 내용 추출 및 DB 영구 저장"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "안내", "저장할 열린 문서가 없습니다.")
            return

        def _after_export(text: str, hwpx_bytes: bytes | None):
            if 0 <= self._active_sheet_index < len(self._open_sheets):
                current = self._open_sheets[self._active_sheet_index]
                current.title = self.work_title_input.text().strip() or "새 업무"
                current.category = self.meta_cat_combo.currentText()
                current.cycle = self.meta_cycle_combo.currentText()
                current.assignee = self.meta_assignee_input.text().strip()
                current.deadline = self.meta_deadline_input.text().strip()
                if text:
                    current.content_text = text
                if hwpx_bytes:
                    current.hwpx_blob = hwpx_bytes

                if self.repository:
                    current.db_id = self.repository.upsert_work_item(
                        work_id=current.db_id,
                        title=current.title,
                        category_name=current.category,
                        cycle=current.cycle,
                        assignee=current.assignee,
                        deadline=current.deadline,
                        content_text=current.content_text,
                        content_html=current.content_html,
                        hwpx_blob=current.hwpx_blob,
                        sort_order=self._all_sheets.index(current) if current in self._all_sheets else 0,
                    )
                short_title = current.title if len(current.title) <= 15 else (current.title[:14] + "…")
                self.sheet_tab_bar.setTabText(self._active_sheet_index, f"📄 {short_title}")
                self._refresh_category_tree()
                QMessageBox.information(self, "저장 완료", f"'{current.title}' 업무 매뉴얼이 DB에 성공적으로 저장되었습니다.")

        self.editor.export_document_data(_after_export)

    def _on_add_new_sheet(self) -> None:
        """새 업무 시트 생성 및 탭 열기"""
        self._save_current_sheet_data()
        new_title = f"신규 단위업무 {len(self._all_sheets) + 1}"
        default_cat = self._categories[0] if self._categories else "기본 업무"
        default_text = f"【 {new_title} 】\n\n1. 업무 개요\n업무 내용을 입력하세요..."
        db_id = None
        if self.repository:
            db_id = self.repository.upsert_work_item(
                work_id=None,
                title=new_title,
                category_name=default_cat,
                cycle="수시",
                content_text=default_text,
                sort_order=len(self._all_sheets) + 1,
            )
        new_sheet = WorkSheetData(
            db_id=db_id,
            sheet_id=f"sheet_{db_id or (len(self._all_sheets) + 1)}",
            title=new_title,
            category=default_cat,
            cycle="수시",
            content_text=default_text,
        )
        self._all_sheets.append(new_sheet)
        self._refresh_category_tree()
        self.open_sheet(new_sheet)

    # =========================================================================
    # 좌측 카테고리 트리 & 우측 첨부파일 관리
    # =========================================================================

    def _refresh_category_combos(self) -> None:
        self.meta_cat_combo.blockSignals(True)
        self.meta_cat_combo.clear()
        self.meta_cat_combo.addItems(self._categories)
        self.meta_cat_combo.blockSignals(False)

    def _refresh_category_tree(self) -> None:
        """좌측 업무 분류 트리 재구성 (전체 문서 목록 표시)"""
        self.category_tree.clear()
        cat_items_map = {}

        for cat in self._categories:
            item = QTreeWidgetItem([f"📁 {cat}"])
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setForeground(0, QColor("#1E293B"))
            self.category_tree.addTopLevelItem(item)
            cat_items_map[cat] = item

        for sheet in self._all_sheets:
            parent_item = cat_items_map.get(sheet.category)
            if not parent_item:
                if not self.category_tree.topLevelItemCount():
                    parent_item = QTreeWidgetItem(["📁 기본 분류"])
                    self.category_tree.addTopLevelItem(parent_item)
                else:
                    parent_item = self.category_tree.topLevelItem(0)

            child = QTreeWidgetItem([f"📄 {sheet.title}"])
            child.setData(0, Qt.UserRole, sheet)
            child.setToolTip(0, sheet.title)
            parent_item.addChild(child)
            parent_item.setExpanded(True)

        self._sync_tree_selection(self._active_sheet_index)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """좌측 트리 문서 클릭 시 하단 탭으로 열기 (이미 열려있으면 해당 탭으로 전환)"""
        sheet = item.data(0, Qt.UserRole)
        if isinstance(sheet, WorkSheetData):
            self.open_sheet(sheet)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        """좌측 트리 항목 우클릭 컨텍스트 메뉴"""
        item = self.category_tree.itemAt(pos)
        if not item:
            return
        sheet = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        if isinstance(sheet, WorkSheetData):
            act_open = menu.addAction("📄 열기")
            act_rename = menu.addAction("✏️ 이름 바꾸기")
            menu.addSeparator()
            act_delete = menu.addAction("🗑️ 업무 삭제 (DB 영구 삭제)")
            action = menu.exec(self.category_tree.mapToGlobal(pos))
            if action == act_open:
                self.open_sheet(sheet)
            elif action == act_rename:
                new_title, ok = QInputDialog.getText(self, "업무 이름 변경", "새 이름:", text=sheet.title)
                if ok and new_title.strip():
                    sheet.title = new_title.strip()
                    if self.repository and sheet.db_id:
                        self.repository.upsert_work_item(
                            work_id=sheet.db_id,
                            title=sheet.title,
                            category_name=sheet.category,
                            cycle=sheet.cycle,
                            assignee=sheet.assignee,
                            deadline=sheet.deadline,
                            content_text=sheet.content_text,
                            content_html=sheet.content_html,
                            hwpx_blob=sheet.hwpx_blob,
                        )
                    if sheet in self._open_sheets:
                        o_idx = self._open_sheets.index(sheet)
                        short_title = sheet.title if len(sheet.title) <= 15 else (sheet.title[:14] + "…")
                        self.sheet_tab_bar.setTabText(o_idx, f"📄 {short_title}")
                        if o_idx == self._active_sheet_index:
                            self.work_title_input.setText(sheet.title)
                    self._refresh_category_tree()
            elif action == act_delete:
                self._delete_sheet_permanently(sheet)

    def _on_add_category(self) -> None:
        cat_name, ok = QInputDialog.getText(self, "새 업무 분류 추가", "분류 명칭을 입력하세요 (예: 4. 대민 행정 서비스):")
        if ok and cat_name.strip():
            c = cat_name.strip()
            if c not in self._categories:
                self._categories.append(c)
                if self.repository:
                    self.repository.add_work_category(c, len(self._categories))
                self._refresh_category_combos()
                self._refresh_category_tree()

    def _refresh_attachments_list(self, files: list[dict]) -> None:
        self.file_list.clear()
        self.right_title.setText(f"📎 첨부파일 ({len(files)})")
        for f in files:
            name = f.get("name", "첨부파일")
            size = f.get("size", "")
            icon = get_file_extension_icon(name)
            item_text = f"{icon} {name}  ({size})" if size else f"{icon} {name}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, f)
            self.file_list.addItem(item)

    def _on_add_attachment(self) -> None:
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "업무 관련 서식 및 첨부파일 선택",
            "",
            "모든 지원 파일 (*.hwp *.hwpx *.xlsx *.xls *.pdf *.docx *.zip);;한글 문서 (*.hwp *.hwpx);;엑셀 서식 (*.xlsx *.xls);;PDF (*.pdf);;모든 파일 (*.*)",
        )
        if path:
            p = Path(path)
            size_kb = round(p.stat().st_size / 1024, 1)
            size_str = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb/1024, 1)} MB"

            curr = self._open_sheets[self._active_sheet_index]
            att_id = None
            if self.repository and curr.db_id:
                att_id = self.repository.add_work_attachment(
                    work_id=curr.db_id,
                    file_name=p.name,
                    file_path=str(p),
                    file_size=size_str,
                    file_type=p.suffix.lower(),
                )

            new_att = {"id": att_id, "name": p.name, "path": str(p), "size": size_str}
            curr.attachments.append(new_att)
            self._refresh_attachments_list(curr.attachments)

    def _open_selected_attachment(self) -> None:
        item = self.file_list.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        path = data.get("path")
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.warning(self, "파일 열기 실패", "해당 첨부파일의 로컬 경로를 찾을 수 없습니다.")

    def _delete_selected_attachment(self) -> None:
        row = self.file_list.currentRow()
        if row < 0 or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        curr = self._open_sheets[self._active_sheet_index]
        if 0 <= row < len(curr.attachments):
            att = curr.attachments[row]
            if self.repository and att.get("id"):
                self.repository.delete_work_attachment(att["id"])
            curr.attachments.pop(row)
            self._refresh_attachments_list(curr.attachments)

    def _on_file_double_clicked(self, item: QListWidgetItem) -> None:
        self._open_selected_attachment()

    # =========================================================================
    # 에디터 서식 및 공공 템플릿
    # =========================================================================

    def _on_title_text_changed(self, text: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        curr = self._open_sheets[self._active_sheet_index]
        curr.title = text.strip() or "새 업무"
        short_title = curr.title if len(curr.title) <= 15 else (curr.title[:14] + "…")
        self.sheet_tab_bar.setTabText(self._active_sheet_index, f"📄 {short_title}")
        self._refresh_category_tree()

    def _on_meta_cat_changed(self, cat: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].category = cat
        self._refresh_category_tree()

    def _on_meta_cycle_changed(self, cycle: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].cycle = cycle

    def _on_meta_assignee_changed(self, val: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].assignee = val

    def _on_meta_deadline_changed(self, val: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].deadline = val

    def _on_editor_text_changed(self) -> None:
        pass

    def _on_search_text_changed(self, query: str) -> None:
        """통합 검색 및 RAG FTS5 매칭 필터링"""
        query = query.strip().lower()
        if not query:
            for i in range(self.sheet_tab_bar.count()):
                self.sheet_tab_bar.setTabVisible(i, True)
            self._refresh_category_tree()
            return

        rag_matched_ids = set()
        if self.repository:
            results = self.repository.search_work_rag(query)
            for r in results:
                rag_matched_ids.add(r["id"])

        for idx, sheet in enumerate(self._open_sheets):
            matched = (
                (sheet.db_id and sheet.db_id in rag_matched_ids)
                or query in sheet.title.lower()
                or query in sheet.category.lower()
                or query in sheet.assignee.lower()
                or query in sheet.content_text.lower()
                or query in sheet.content_html.lower()
                or any(query in att.get("name", "").lower() for att in sheet.attachments)
            )
            self.sheet_tab_bar.setTabVisible(idx, matched)

        # 좌측 카테고리 트리 필터링 적용
        for i in range(self.category_tree.topLevelItemCount()):
            top = self.category_tree.topLevelItem(i)
            has_visible_child = False
            for j in range(top.childCount()):
                child = top.child(j)
                sheet = child.data(0, Qt.UserRole)
                if isinstance(sheet, WorkSheetData):
                    matched = (
                        (sheet.db_id and sheet.db_id in rag_matched_ids)
                        or query in sheet.title.lower()
                        or query in sheet.category.lower()
                        or query in sheet.assignee.lower()
                        or query in sheet.content_text.lower()
                        or query in sheet.content_html.lower()
                        or any(query in att.get("name", "").lower() for att in sheet.attachments)
                    )
                    child.setHidden(not matched)
                    if matched:
                        has_visible_child = True
            top.setHidden(not has_visible_child)
            if has_visible_child:
                top.setExpanded(True)

    def _format_bold(self) -> None:
        fmt = QTextCharFormat()
        weight = QFont.Bold if self.btn_bold.isChecked() else QFont.Normal
        fmt.setFontWeight(weight)
        self.editor.textCursor().mergeCharFormat(fmt)

    def _format_italic(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.btn_italic.isChecked())
        self.editor.textCursor().mergeCharFormat(fmt)

    def _format_underline(self) -> None:
        fmt = QTextCharFormat()
        fmt.setFontUnderline(self.btn_underline.isChecked())
        self.editor.textCursor().mergeCharFormat(fmt)

    def _format_text_color(self) -> None:
        c = QColorDialog.getColor(Qt.black, self, "글자 색상 선택")
        if c.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(c)
            self.editor.textCursor().mergeCharFormat(fmt)

    def _format_bg_color(self) -> None:
        c = QColorDialog.getColor(QColor("#FEF08A"), self, "형광펜 배경색 선택")
        if c.isValid():
            fmt = QTextCharFormat()
            fmt.setBackground(c)
            self.editor.textCursor().mergeCharFormat(fmt)

    def _insert_table_dialog(self) -> None:
        rows, ok1 = QInputDialog.getInt(self, "표 삽입", "행(Row) 개수:", 3, 1, 20, 1)
        if not ok1:
            return
        cols, ok2 = QInputDialog.getInt(self, "표 삽입", "열(Column) 개수:", 3, 1, 10, 1)
        if not ok2:
            return

        cursor = self.editor.textCursor()
        fmt = QTextTableFormat()
        fmt.setBorder(1)
        fmt.setCellPadding(6)
        fmt.setCellSpacing(0)
        fmt.setBorderBrush(QColor("#CBD5E1"))
        fmt.setWidth(QTextTableFormat().width())
        cursor.insertTable(rows, cols, fmt)

    def _insert_image_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "이미지 삽입",
            "",
            "이미지 파일 (*.png *.jpg *.jpeg *.bmp *.gif);;모든 파일 (*.*)",
        )
        if path:
            cursor = self.editor.textCursor()
            cursor.insertHtml(f'<img src="{path}" style="max-width: 100%; border: 1px solid #E2E8F0; border-radius: 4px;" />')

    def _insert_badge(self, label: str, bg_hex: str, border_hex: str) -> None:
        html = f"""
        <div style="background-color: {bg_hex}; border-left: 4px solid {border_hex}; padding: 8px 12px; margin: 8px 0; border-radius: 4px;">
            <b style="color: {border_hex};">[{label}]</b> 세부 내용을 여기에 기재하세요...
        </div><br>
        """
        current = self.editor.get_cached_html()
        self.editor.set_html(current + html)

    def _load_template(self, tpl_type: str) -> None:
        if tpl_type == "manual":
            html = """
            <h2 style="color: #1D4ED8;">1. 업무 개요 및 법적 근거</h2>
            <p>• 업무 명칭: <br>• 관련 법령 및 규정: </p>
            <h2 style="color: #1D4ED8;">2. 단계별 추진 절차</h2>
            <p>1단계: <br>2단계: <br>3단계: </p>
            <h2 style="color: #1D4ED8;">3. 주요 서식 및 협조 부서</h2>
            <p>• 제출 서식: <br>• 협조 부서: </p>
            """
        elif tpl_type == "handover":
            html = """
            <h2 style="color: #1D4ED8;">1. 소관 업무 현황 및 목표</h2>
            <p>담당 업무 요약 및 연간 주요 추진 과제 기술</p>
            <h2 style="color: #1D4ED8;">2. 현재 진행 중인 현안 과제</h2>
            <p>• 과제명: <br>• 추진 상황: <br>• 향후 조치 계획: </p>
            <h2 style="color: #1D4ED8;">3. 미결 과제 및 유의사항</h2>
            <p>후임자가 즉시 처리해야 할 긴급 과제 및 유관부서 주의사항</p>
            """
        elif tpl_type == "audit":
            html = """
            <h2 style="color: #1D4ED8;">1. 감사 대비 필수 점검표</h2>
            <p>☑ 결재공문 번호 일치 여부 확인<br>☑ 증빙 영수증 및 카드전표 첨부 확인<br>☑ 계약 상대방 청렴서약서 징구 확인</p>
            <h2 style="color: #1D4ED8;">2. 과거 감사 지적사례 및 예방요령</h2>
            <p>지적 사례 요약 및 사전 점검 포인트</p>
            """
        else:
            return

        self.editor.set_html(html)

    # =========================================================================
    # UI 스타일 헬퍼
    # =========================================================================

    def _button_style(self, active: bool = False) -> str:
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")

        if active:
            return f"""
                QPushButton {{
                    background-color: {accent_soft};
                    color: {accent};
                    border: 1px solid {accent};
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 12px;
                    font-weight: bold;
                }}
            """
        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {accent};
            }}
        """

    def _sub_btn_style(self) -> str:
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")

        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                border-color: {accent};
                color: {accent};
            }}
        """

    def _create_format_btn(self, label: str, tooltip: str, checkable: bool, callback) -> QToolButton:
        btn = QToolButton()
        btn.setText(label)
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        btn.setFixedSize(24, 24)
        btn.setStyleSheet(f"""
            QToolButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                font-weight: bold;
            }}
            QToolButton:hover {{
                background-color: #E2E8F0;
            }}
            QToolButton:checked {{
                background-color: #DBEAFE;
                color: #1D4ED8;
                border-color: #93C5FD;
            }}
        """)
        btn.clicked.connect(callback)
        return btn

    def _combo_style(self) -> str:
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")

        return f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
                background-color: {panel};
                color: {text};
            }}
            QComboBox:hover, QComboBox:focus {{
                border-color: {accent};
            }}
        """

    def _small_input_style(self) -> str:
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")

        return f"""
            QLineEdit {{
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
                background-color: {panel};
                color: {text};
            }}
            QLineEdit:focus {{
                border-color: {accent};
            }}
        """

    def _v_sep(self) -> QFrame:
        line = self.palette.get("line", "#CBD5E0")
        f = QFrame()
        f.setFrameShape(QFrame.VLine)
        f.setFrameShadow(QFrame.Sunken)
        f.setStyleSheet(f"color: {line};")
        return f

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.palette = palette
