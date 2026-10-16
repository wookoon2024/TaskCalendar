from __future__ import annotations

import html
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.ai_assistant import AIChatWorker, AIConfigManager, AISettingsDialog
from taskcalendar.fonts import font_family_css, make_ui_font_like, scale_px, ui_font_family
from taskcalendar.qt_styles import resolve_palette

logger = logging.getLogger(__name__)


# =============================================================================
# 1. 개인정보(PII) 검출 및 유지/삭제/변경(마스킹) 도구
# =============================================================================

@dataclass
class PersonalInfoMatch:
    pii_type: str          # 예: "주민등록번호", "휴대전화", "일반전화", "이메일", "계좌/카드번호", "차량번호", "성명(민원인)"
    original: str          # 검출된 원본 문자열
    masked_default: str    # 기본 추천 마스킹 문자열
    context: str           # 앞뒤 문맥 미리보기
    start: int             # 텍스트 내 시작 인덱스
    end: int               # 텍스트 내 종료 인덱스


def _mask_rrn(val: str) -> str:
    # 900101-1234567 -> 900101-1******
    m = re.match(r"^(\d{6})([-\s]?)([1-8])\d{6}$", val)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}******"
    return "******-*******"


def _mask_phone(val: str) -> str:
    # 010-1234-5678 -> 010-****-5678
    parts = re.split(r"([-\s.])", val)
    if len(parts) >= 5:
        parts[2] = "*" * len(parts[2])
        return "".join(parts)
    if len(val) >= 7:
        return val[:3] + "****" + val[-4:]
    return "***-****-****"


def _mask_email(val: str) -> str:
    if "@" not in val:
        return "***@***"
    local, domain = val.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * max(1, len(local) - 1)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


def _mask_card_or_account(val: str) -> str:
    parts = val.split("-")
    if len(parts) == 4 and all(len(p) == 4 for p in parts):
        return f"{parts[0]}-****-****-{parts[3]}"
    if len(parts) >= 3:
        masked = list(parts)
        for i in range(1, len(masked) - 1):
            masked[i] = "*" * len(masked[i])
        return "-".join(masked)
    return val[:4] + "-****-" + val[-4:]


def _mask_car_no(val: str) -> str:
    # 12가 3456 -> 12가 ****
    return re.sub(r"\d{4}$", "****", val)


def _mask_name(val: str) -> str:
    val = val.strip()
    if len(val) <= 1:
        return "*"
    if len(val) == 2:
        return val[0] + "*"
    return val[0] + ("*" * (len(val) - 2)) + val[-1]


def detect_personal_info(text: str) -> list[PersonalInfoMatch]:
    """문서 본문에서 개인정보 의심 항목을 정규식으로 정밀 검출"""
    if not text or not text.strip():
        return []

    matches: list[PersonalInfoMatch] = []
    occupied_spans: list[tuple[int, int]] = []

    def _is_overlapping(s: int, e: int) -> bool:
        for os_, oe_ in occupied_spans:
            if max(s, os_) < min(e, oe_):
                return True
        return False

    def _get_context(s: int, e: int) -> str:
        left = max(0, s - 16)
        right = min(len(text), e + 16)
        snippet = text[left:right].replace("\r", " ").replace("\n", " ")
        prefix = "…" if left > 0 else ""
        suffix = "…" if right < len(text) else ""
        return f"{prefix}{snippet}{suffix}"

    patterns = [
        (
            "주민/외국인등록번호",
            re.compile(r"\b\d{6}[-\s]?[1-8]\d{6}\b"),
            _mask_rrn,
        ),
        (
            "휴대전화번호",
            re.compile(r"\b01[016789][-\s.]\d{3,4}[-\s.]\d{4}\b"),
            _mask_phone,
        ),
        (
            "일반전화번호",
            re.compile(r"\b0(?:2|[3-6][1-5]|70)[-\s.]\d{3,4}[-\s.]\d{4}\b"),
            _mask_phone,
        ),
        (
            "이메일 주소",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            _mask_email,
        ),
        (
            "카드번호",
            re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b"),
            _mask_card_or_account,
        ),
        (
            "계좌번호",
            re.compile(r"\b\d{3,6}-\d{2,6}-\d{4,7}(?:-\d{1,3})?\b"),
            _mask_card_or_account,
        ),
        (
            "차량번호",
            re.compile(r"\b(?:\d{2,3}[가-힣]\s?\d{4}|[가-힣]{2}\s?\d{2}[가-힣]\s?\d{4})\b"),
            _mask_car_no,
        ),
    ]

    for pii_type, regex, mask_fn in patterns:
        for m in regex.finditer(text):
            s, e = m.start(), m.end()
            if _is_overlapping(s, e):
                continue
            orig = m.group(0)
            # 공문서 문서번호(예: 2026-10-12345 등 날짜/시행번호) 오탐 방지
            if pii_type == "계좌번호":
                if re.match(r"^20[12]\d-(?:0?[1-9]|1[0-2])-", orig):
                    continue
            occupied_spans.append((s, e))
            matches.append(
                PersonalInfoMatch(
                    pii_type=pii_type,
                    original=orig,
                    masked_default=mask_fn(orig),
                    context=_get_context(s, e),
                    start=s,
                    end=e,
                )
            )

    # 성명 키워드 기반 검출 (민원인: 홍길동, 신청인: 김철수, 성명: 이영희 등)
    name_regex = re.compile(
        r"(?:민원인|신청인|피신청인|입주자|소유자|대표자|대상자|성\s*명)\s*[:：]\s*([가-힣]{2,4})(?![가-힣])"
    )
    stop_words = {"미정", "없음", "해당", "참조", "별첨", "붙임", "공란", "익명", "담당", "주무관", "팀장", "과장"}
    for m in name_regex.finditer(text):
        s, e = m.start(1), m.end(1)
        name_val = m.group(1)
        if name_val in stop_words or _is_overlapping(s, e):
            continue
        occupied_spans.append((s, e))
        matches.append(
            PersonalInfoMatch(
                pii_type="성명",
                original=name_val,
                masked_default=_mask_name(name_val),
                context=_get_context(s, e),
                start=s,
                end=e,
            )
        )

    matches.sort(key=lambda item: item.start)
    return matches


from PySide6.QtWidgets import (
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
)


def extract_text_from_hwpx_blob(hwpx_blob: bytes | None) -> str:
    """HWPX 바이너리(Contents/section*.xml) 내부의 모든 텍스트 노드를 추출"""
    if not hwpx_blob:
        return ""
    import io
    import xml.etree.ElementTree as ET
    lines: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(hwpx_blob), "r") as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Contents/section") and name.endswith(".xml"):
                    xml_bytes = zf.read(name)
                    root = ET.fromstring(xml_bytes)
                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                        if tag == "t" and elem.text:
                            t_val = elem.text.strip()
                            if t_val:
                                lines.append(t_val)
    except Exception:
        pass
    return "\n".join(lines)


def apply_replacements_to_hwpx_blob(hwpx_blob: bytes | None, replacements: list[tuple[str, str]]) -> bytes | None:
    """
    HWPX 바이너리(ZIP 내 Contents/section*.xml) 안의 텍스트 노드에서
    개인정보 원본 문자열을 변경/수정/삭제된 문자열로 무손실 치환
    """
    if not hwpx_blob or not replacements:
        return hwpx_blob

    import io
    try:
        in_buf = io.BytesIO(hwpx_blob)
        out_buf = io.BytesIO()
        with zipfile.ZipFile(in_buf, "r") as zin, zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("Contents/section") and item.filename.endswith(".xml"):
                    xml_str = data.decode("utf-8", errors="ignore")
                    for orig, repl in replacements:
                        if not orig or orig == repl:
                            continue
                        orig_esc = html.escape(orig, quote=False)
                        repl_esc = html.escape(repl, quote=False)
                        xml_str = xml_str.replace(orig_esc, repl_esc)
                        if orig != orig_esc:
                            xml_str = xml_str.replace(orig, repl_esc)
                    data = xml_str.encode("utf-8")
                zout.writestr(item, data)
        return out_buf.getvalue()
    except Exception as e:
        logger.warning("Failed to apply PII replacements to hwpx_blob: %s", e)
        return hwpx_blob


@dataclass
class SheetPiiEntry:
    sheet: Any
    category_name: str
    work_title: str
    match: PersonalInfoMatch


class PersonalInfoCheckDialog(QDialog):
    """
    등록된 업무 분류 및 업무들을 체크하여 개인정보를 일괄 검사하고,
    검출 결과에 따라 항목별로 [마스킹 / 직접수정 / 유지 / 삭제] 조치하는 모달 다이얼로그
    """
    open_requested = Signal(object, str)  # (sheet, target_text)

    def __init__(
        self,
        parent: QWidget | None,
        doc_text: str = "",
        palette: dict[str, str] | None = None,
        sheets: list[Any] | None = None,
        categories: list[Any] | None = None,
        current_sheet: Any | None = None,
        on_open_entry: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("개인정보 점검")
        self.resize(980, 560)
        self.setMinimumSize(840, 450)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setFont(make_ui_font_like(10))
        self._on_open_entry = on_open_entry
        if callable(on_open_entry):
            self.open_requested.connect(on_open_entry)

        self.doc_text = doc_text or ""
        self.sheets = list(sheets or [])
        self.categories = list(categories or [])
        self.current_sheet = current_sheet

        # 단일 텍스트만 전달된 경우 임시 시트 객체 생성
        if not self.sheets and (self.doc_text or self.current_sheet):
            if self.current_sheet is not None:
                self.sheets = [self.current_sheet]
            else:
                @dataclass
                class _DummySheet:
                    title: str = "현재 문서"
                    category: str = "일반 업무"
                    content_text: str = ""
                    hwpx_blob: bytes | None = None
                self.sheets = [_DummySheet(content_text=self.doc_text)]

        self.entries: list[SheetPiiEntry] = []
        self._replacements: list[tuple[str, str]] = []
        self._replacements_by_sheet: list[tuple[Any, list[tuple[str, str]]]] = []

        pal = palette or resolve_palette(False)
        self.palette = pal
        bg = pal.get("bg", "#F8FAFC")
        panel = pal.get("panel", "#FFFFFF")
        panel_alt = pal.get("panel_alt", "#F1F5F9")
        text = pal.get("text", "#1E293B")
        muted = pal.get("text_muted", "#64748B")
        line = pal.get("line", "#CBD5E1")
        accent = pal.get("accent", "#2563EB")

        self.setStyleSheet(f"""
            QDialog, QWidget {{
                font-family: {font_family_css()};
            }}
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(10)

        # 상단 요약 배너 (개조식 1줄)
        banner = QFrame()
        banner.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        b_layout = QHBoxLayout(banner)
        b_layout.setContentsMargins(14, 8, 14, 8)
        b_layout.setSpacing(10)

        self.lbl_summary_title = QLabel("• 점검 대상 선택 후 검사 실행")
        self.lbl_summary_title.setStyleSheet(
            f"font-family: {font_family_css()}; font-size: 13px; font-weight: bold; color: {text}; border: none;"
        )
        b_layout.addWidget(self.lbl_summary_title, 1)

        main_layout.addWidget(banner)

        btn_style = f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 5px;
                padding: 4px 10px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                border-color: {accent};
                color: {accent};
            }}
        """
        self._btn_style = btn_style

        # 중앙 좌/우 스플리터 (좌: 분류·업무 체크 트리 / 우: 검출 결과 및 조치 테이블)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # ── [좌측 패널] 점검 대상 ─────────────────────────────
        left_frame = QFrame()
        left_frame.setMinimumWidth(250)
        left_frame.setMaximumWidth(340)
        left_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        left_title = QLabel("점검 대상")
        left_title.setStyleSheet(
            f"font-family: {font_family_css()}; font-size: 12px; font-weight: bold; color: {text}; border: none;"
        )
        left_layout.addWidget(left_title)

        chk_btn_row = QHBoxLayout()
        chk_btn_row.setSpacing(4)
        btn_chk_all = QPushButton("전체 선택")
        btn_chk_all.setFixedHeight(scale_px(26))
        btn_chk_all.setStyleSheet(btn_style)
        btn_chk_all.clicked.connect(self._check_all_tree)
        chk_btn_row.addWidget(btn_chk_all)

        btn_unchk_all = QPushButton("전체 해제")
        btn_unchk_all.setFixedHeight(scale_px(26))
        btn_unchk_all.setStyleSheet(btn_style)
        btn_unchk_all.clicked.connect(self._uncheck_all_tree)
        chk_btn_row.addWidget(btn_unchk_all)

        if self.current_sheet is not None:
            btn_chk_cur = QPushButton("현재 업무만")
            btn_chk_cur.setFixedHeight(scale_px(26))
            btn_chk_cur.setStyleSheet(btn_style)
            btn_chk_cur.clicked.connect(self._check_only_current_sheet)
            chk_btn_row.addWidget(btn_chk_cur)

        chk_btn_row.addStretch(1)
        left_layout.addLayout(chk_btn_row)

        accent_soft = pal.get("accent_soft", "#E0F2FE")
        self.tree = QTreeWidget()
        self.tree.setFont(make_ui_font_like(10))
        self.tree.setHeaderHidden(True)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setStyleSheet(f"""
            QTreeWidget {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 6px;
                color: {text};
                font-family: {font_family_css()};
                font-size: 12px;
                padding: 4px;
                outline: 0px;
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QTreeWidget::item {{
                height: {scale_px(24)}px;
                padding: 1px 4px;
                border: none;
                outline: 0px;
            }}
            QTreeWidget::item:selected, QTreeWidget::item:selected:active, QTreeWidget::item:selected:!active {{
                background-color: {accent_soft};
                color: {text};
                border: none;
                outline: 0px;
            }}
        """)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        left_layout.addWidget(self.tree, 1)

        self.btn_scan = QPushButton("개인정보 검사 실행")
        self.btn_scan.setFixedHeight(scale_px(32))
        self.btn_scan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.92;
            }}
        """)
        self.btn_scan.clicked.connect(lambda: self.run_scan_on_checked_sheets(manual=True))
        left_layout.addWidget(self.btn_scan)

        splitter.addWidget(left_frame)

        # ── [우측 패널] 개인정보 검출 결과 및 조치 선택 ─────────────────────
        right_frame = QFrame()
        right_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        batch_row = QHBoxLayout()
        batch_row.setSpacing(6)

        lbl_batch = QLabel("일괄 선택")
        lbl_batch.setStyleSheet(
            f"font-family: {font_family_css()}; font-size: 12px; font-weight: bold; color: {text}; border: none;"
        )
        batch_row.addWidget(lbl_batch)

        btn_all_mask = QPushButton("전체 마스킹")
        btn_all_mask.setFixedHeight(scale_px(26))
        btn_all_mask.setStyleSheet(btn_style)
        btn_all_mask.clicked.connect(lambda: self._set_all_action(0))
        batch_row.addWidget(btn_all_mask)

        btn_all_edit = QPushButton("전체 직접수정")
        btn_all_edit.setFixedHeight(scale_px(26))
        btn_all_edit.setStyleSheet(btn_style)
        btn_all_edit.clicked.connect(lambda: self._set_all_action(1))
        batch_row.addWidget(btn_all_edit)

        btn_all_keep = QPushButton("전체 유지")
        btn_all_keep.setFixedHeight(scale_px(26))
        btn_all_keep.setStyleSheet(btn_style)
        btn_all_keep.clicked.connect(lambda: self._set_all_action(2))
        batch_row.addWidget(btn_all_keep)

        btn_all_del = QPushButton("전체 삭제")
        btn_all_del.setFixedHeight(scale_px(26))
        btn_all_del.setStyleSheet(btn_style)
        btn_all_del.clicked.connect(lambda: self._set_all_action(3))
        batch_row.addWidget(btn_all_del)

        batch_row.addStretch(1)
        right_layout.addLayout(batch_row)

        self.table = QTableWidget(0, 5, self)
        self.table.setFont(make_ui_font_like(10))
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.table.setHorizontalHeaderLabels([
            "분류 / 업무명",
            "개인정보 유형",
            "검출 내용",
            "처리 방식",
            "반영 내용",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self._on_table_cell_clicked)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                gridline-color: {line};
                font-family: {font_family_css()};
                font-size: 12px;
                outline: 0px;
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QTableWidget::item {{
                border: none;
                padding: 0px 8px;
                outline: 0px;
            }}
            QTableWidget::item:focus {{
                border: none;
                outline: 0px;
            }}
            QTableWidget::item:selected, QTableWidget::item:selected:active, QTableWidget::item:selected:!active {{
                background-color: {accent_soft};
                color: {text};
                border: none;
                outline: 0px;
            }}
            QHeaderView::section {{
                background-color: {panel_alt};
                color: {text};
                font-family: {font_family_css()};
                font-weight: bold;
                font-size: 12px;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
            }}
        """)
        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setMinimumSectionSize(scale_px(95))
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, scale_px(110))
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(2, scale_px(145))
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, scale_px(110))
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(4, scale_px(155))

        vp_layout = QVBoxLayout(self.table.viewport())
        vp_layout.setContentsMargins(16, 16, 16, 16)
        self.lbl_empty_table = QLabel("검출된 개인정보가 없습니다.")
        self.lbl_empty_table.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty_table.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_empty_table.setStyleSheet(
            f"font-family: {font_family_css()}; font-size: 13px; color: {muted}; background: transparent; border: none;"
        )
        vp_layout.addWidget(self.lbl_empty_table)

        right_layout.addWidget(self.table, 1)
        splitter.addWidget(right_frame)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 700])

        main_layout.addWidget(splitter, 1)

        self._action_combos: list[QComboBox] = []
        self._replace_inputs: list[QLineEdit] = []

        # 하단 버튼
        footer = QHBoxLayout()
        footer.addStretch(1)

        btn_cancel = QPushButton("닫기")
        btn_cancel.setFixedHeight(scale_px(32))
        btn_cancel.setFixedWidth(scale_px(85))
        btn_cancel.setStyleSheet(btn_style)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        self.btn_apply = QPushButton("조치 일괄 적용")
        self.btn_apply.setFixedHeight(scale_px(32))
        self.btn_apply.setMinimumWidth(scale_px(140))
        self.btn_apply.setEnabled(False)
        self.btn_apply.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: bold;
                padding: 4px 16px;
            }}
            QPushButton:hover {{
                opacity: 0.92;
            }}
            QPushButton:disabled {{
                background-color: {line};
                color: {muted};
            }}
        """)
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        footer.addWidget(self.btn_apply)

        main_layout.addLayout(footer)

        # 트리 채우기 및 초기 자동 검사 실행
        self._populate_tree()
        self.run_scan_on_checked_sheets()

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if not item:
            return
        sheet = item.data(0, Qt.ItemDataRole.UserRole)
        if sheet is not None:
            self.open_requested.emit(sheet, "")

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if row < 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        self.open_requested.emit(entry.sheet, entry.match.original)

    def _populate_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()

        row_font = make_ui_font_like(10)
        bold_font = make_ui_font_like(10)
        bold_font.setBold(True)

        cat_map: dict[str, QTreeWidgetItem] = {}
        for c in self.categories:
            c_name = c.get("name", "") if isinstance(c, dict) else str(c)
            c_name = c_name.strip()
            if not c_name or c_name in cat_map:
                continue
            c_item = QTreeWidgetItem([f"📁 {c_name}"])
            c_item.setFlags(c_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            c_item.setCheckState(0, Qt.CheckState.Checked)
            c_item.setFont(0, bold_font)
            cat_map[c_name] = c_item
            self.tree.addTopLevelItem(c_item)

        for s in self.sheets:
            c_name = (getattr(s, "category", "") or "일반 업무").strip()
            if c_name not in cat_map:
                c_item = QTreeWidgetItem([f"📁 {c_name}"])
                c_item.setFlags(c_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                c_item.setCheckState(0, Qt.CheckState.Checked)
                c_item.setFont(0, bold_font)
                cat_map[c_name] = c_item
                self.tree.addTopLevelItem(c_item)

            parent_item = cat_map[c_name]
            title_str = getattr(s, "title", "") or "제목 없음"
            label = f"📄 {title_str}"
            doc_item = QTreeWidgetItem([label])
            doc_item.setFlags(doc_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            doc_item.setCheckState(0, Qt.CheckState.Checked)
            doc_item.setFont(0, row_font)
            doc_item.setData(0, Qt.ItemDataRole.UserRole, s)
            doc_item.setToolTip(0, f"{c_name} / {title_str}")
            parent_item.addChild(doc_item)

        # 자식이 없는 빈 카테고리는 숨김 처리
        for i in range(self.tree.topLevelItemCount() - 1, -1, -1):
            top = self.tree.topLevelItem(i)
            if top.childCount() == 0:
                self.tree.takeTopLevelItem(i)

        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        state = item.checkState(0)
        self.tree.blockSignals(True)
        if item.childCount() > 0:
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
        else:
            parent = item.parent()
            if parent is not None:
                checked_cnt = sum(
                    1 for i in range(parent.childCount())
                    if parent.child(i).checkState(0) == Qt.CheckState.Checked
                )
                if checked_cnt == 0:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                elif checked_cnt == parent.childCount():
                    parent.setCheckState(0, Qt.CheckState.Checked)
                else:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
        self.tree.blockSignals(False)
        self.run_scan_on_checked_sheets()

    def _check_all_tree(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Checked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self.run_scan_on_checked_sheets()

    def _uncheck_all_tree(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.run_scan_on_checked_sheets()

    def _check_only_current_sheet(self) -> None:
        if self.current_sheet is None:
            return
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            has_cur = False
            for j in range(top.childCount()):
                ch = top.child(j)
                s = ch.data(0, Qt.ItemDataRole.UserRole)
                if s is self.current_sheet:
                    ch.setCheckState(0, Qt.CheckState.Checked)
                    has_cur = True
                else:
                    ch.setCheckState(0, Qt.CheckState.Unchecked)
            top.setCheckState(0, Qt.CheckState.Checked if has_cur else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.run_scan_on_checked_sheets()

    def _get_checked_sheets(self) -> list[Any]:
        checked: list[Any] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.CheckState.Checked:
                    s = ch.data(0, Qt.ItemDataRole.UserRole)
                    if s is not None:
                        checked.append(s)
        return checked

    def _extract_sheet_text(self, sheet: Any) -> str:
        """시트에서 본문 텍스트 추출 (본문 텍스트가 비어있을 때만 HWPX 바이너리에서 추출하여 중복 방지)"""
        if self.current_sheet is not None and sheet is self.current_sheet and self.doc_text.strip():
            return self.doc_text.strip()

        base_txt = (getattr(sheet, "content_text", "") or "").strip()
        if base_txt:
            return base_txt

        hwpx_blob = getattr(sheet, "hwpx_blob", None)
        if hwpx_blob:
            return extract_text_from_hwpx_blob(hwpx_blob).strip()
        return ""

    def run_scan_on_checked_sheets(self, manual: bool = False) -> None:
        """체크된 분류/업무들을 대상으로 개인정보 검출 실행 후 우측 테이블 갱신 (동일 업무 내 동일 개인정보 중복 제거)"""
        checked_sheets = self._get_checked_sheets()
        self.entries = []
        for s in checked_sheets:
            txt = self._extract_sheet_text(s)
            if not txt:
                continue
            c_name = getattr(s, "category", "") or "일반 업무"
            w_title = getattr(s, "title", "") or "제목 없음"
            seen_in_sheet: set[tuple[str, str]] = set()
            for m in detect_personal_info(txt):
                dedup_key = (m.pii_type, m.original)
                if dedup_key in seen_in_sheet:
                    continue
                seen_in_sheet.add(dedup_key)
                self.entries.append(
                    SheetPiiEntry(
                        sheet=s,
                        category_name=c_name,
                        work_title=w_title,
                        match=m,
                    )
                )

        self._rebuild_results_table(len(checked_sheets))

        if manual:
            if not checked_sheets:
                QMessageBox.information(
                    self,
                    "개인정보 점검 안내",
                    "점검할 대상 업무가 선택되지 않았습니다.\n좌측 목록에서 점검할 업무를 체크한 후 다시 실행해 주세요.",
                )
            elif not self.entries:
                QMessageBox.information(
                    self,
                    "개인정보 점검 결과",
                    f"선택한 {len(checked_sheets)}개 업무를 검사한 결과,\n검출된 개인정보가 없습니다.",
                )

    def _rebuild_results_table(self, checked_sheet_count: int) -> None:
        pal = self.palette
        panel = pal.get("panel", "#FFFFFF")
        panel_alt = pal.get("panel_alt", "#F1F5F9")
        text = pal.get("text", "#1E293B")
        muted = pal.get("text_muted", "#64748B")
        line = pal.get("line", "#CBD5E1")
        accent = pal.get("accent", "#2563EB")

        self.table.setRowCount(len(self.entries))
        self._action_combos = []
        self._replace_inputs = []

        count = len(self.entries)
        if checked_sheet_count == 0:
            self.lbl_summary_title.setText("• 점검 대상 미선택")
            if hasattr(self, "lbl_empty_table"):
                self.lbl_empty_table.setText("좌측 목록에서 점검할 업무를 선택해 주세요.")
                self.lbl_empty_table.show()
        elif count > 0:
            self.lbl_summary_title.setText(
                f"• 점검 대상: {checked_sheet_count}개 업무   |   검출 결과: {count}건"
            )
            if hasattr(self, "lbl_empty_table"):
                self.lbl_empty_table.hide()
        else:
            self.lbl_summary_title.setText(
                f"• 점검 대상: {checked_sheet_count}개 업무   |   검출 결과: 없음"
            )
            if hasattr(self, "lbl_empty_table"):
                self.lbl_empty_table.setText("검출된 개인정보가 없습니다.")
                self.lbl_empty_table.show()

        row_font = make_ui_font_like(10)
        row_h = max(34, scale_px(34))

        for row_idx, entry in enumerate(self.entries):
            m = entry.match
            self.table.setRowHeight(row_idx, row_h)

            doc_label = f"{entry.category_name} / {entry.work_title}".replace("\r", " ").replace("\n", " ")
            doc_item = QTableWidgetItem(doc_label)
            doc_item.setFont(row_font)
            doc_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            doc_item.setToolTip(f"{doc_label}\n• 클릭 시 해당 업무를 열고 검출 위치로 이동")
            self.table.setItem(row_idx, 0, doc_item)

            type_item = QTableWidgetItem(m.pii_type)
            type_item.setFont(row_font)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setToolTip("클릭 시 해당 업무를 열고 검출 위치로 이동")
            self.table.setItem(row_idx, 1, type_item)

            orig_item = QTableWidgetItem(m.original)
            orig_item.setFont(row_font)
            orig_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            orig_item.setForeground(QColor("#DC2626"))
            orig_item.setToolTip("클릭 시 해당 업무를 열고 검출 위치로 이동")
            self.table.setItem(row_idx, 2, orig_item)

            combo = QComboBox()
            combo.setFont(row_font)
            combo.addItems([
                "마스킹",
                "직접수정",
                "유지",
                "삭제",
            ])
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {panel};
                    color: {text};
                    border: 1px solid {line};
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-family: {font_family_css()};
                    font-size: 12px;
                }}
            """)

            inp = QLineEdit(m.masked_default)
            inp.setFont(row_font)
            inp.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inp.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {panel};
                    color: {accent};
                    border: 1px solid {line};
                    border-radius: 4px;
                    padding: 2px 6px;
                    font-family: {font_family_css()};
                    font-size: 12px;
                }}
                QLineEdit:disabled {{
                    background-color: {panel_alt};
                    color: {muted};
                }}
            """)

            combo.currentIndexChanged.connect(
                lambda idx, r=row_idx: self._on_row_action_changed(r, idx)
            )
            self._action_combos.append(combo)
            self._replace_inputs.append(inp)

            self.table.setCellWidget(row_idx, 3, combo)
            self.table.setCellWidget(row_idx, 4, inp)

        self.btn_apply.setEnabled(count > 0)

    @property
    def matches(self) -> list[PersonalInfoMatch]:
        return [e.match for e in self.entries]

    def _on_row_action_changed(self, row_idx: int, action_idx: int) -> None:
        if row_idx < 0 or row_idx >= len(self.entries):
            return
        inp = self._replace_inputs[row_idx]
        m = self.entries[row_idx].match
        if action_idx == 0:  # 마스킹
            inp.setEnabled(True)
            inp.setText(m.masked_default)
        elif action_idx == 1:  # 직접수정
            inp.setEnabled(True)
            if not inp.text() or inp.text() == m.masked_default:
                inp.setText(m.original)
            inp.setFocus()
            inp.selectAll()
        elif action_idx == 2:  # 유지
            inp.setEnabled(False)
            inp.setText(m.original)
        else:  # 삭제
            inp.setEnabled(False)
            inp.setText("")
            inp.setPlaceholderText("삭제됨")

    def _set_all_action(self, action_idx: int) -> None:
        for combo in self._action_combos:
            combo.setCurrentIndex(action_idx)

    def _on_apply_clicked(self) -> None:
        flat_replacements: list[tuple[str, str]] = []
        by_sheet_map: dict[int, tuple[Any, list[tuple[str, str]]]] = {}

        for row_idx, entry in enumerate(self.entries):
            m = entry.match
            action_idx = self._action_combos[row_idx].currentIndex()
            if action_idx == 2:  # 유지
                continue
            elif action_idx == 3:  # 삭제
                pair = (m.original, "")
            else:  # 0: 마스킹, 1: 직접수정
                new_val = self._replace_inputs[row_idx].text()
                if new_val == m.original:
                    continue
                pair = (m.original, new_val)

            flat_replacements.append(pair)
            sid = id(entry.sheet)
            if sid not in by_sheet_map:
                by_sheet_map[sid] = (entry.sheet, [])
            if pair not in by_sheet_map[sid][1]:
                by_sheet_map[sid][1].append(pair)

        if not flat_replacements:
            QMessageBox.information(
                self,
                "알림",
                "• 적용할 변경 항목이 없습니다.",
            )
            return

        self._replacements = flat_replacements
        self._replacements_by_sheet = list(by_sheet_map.values())
        self.accept()

    def get_replacements(self) -> list[tuple[str, str]]:
        return self._replacements

    def get_replacements_by_sheet(self) -> list[tuple[Any, list[tuple[str, str]]]]:
        return self._replacements_by_sheet


# =============================================================================
# 2. 외부 AI 연결 여부 및 유료 과금 안내 판별 헬퍼 + 자동분류기 워커
# =============================================================================

def get_ai_connection_status() -> dict[str, Any]:
    """
    현재 AI 연동 설정(AIConfigManager)을 확인하여:
    - connected: AI 사용 가능 여부 (API 키가 있거나 로컬 LLM 엔드포인트인 경우 True)
    - is_external_paid: 외부 상용 유료 AI 여부 (OpenAI, Gemini, Claude, CommandCode 등)
    - provider_label: 표시용 제공자 이름
    - notice_text: 사용자 안내 문구
    """
    cfg = AIConfigManager.load()
    provider = str(cfg.get("provider", "") or "").strip()
    endpoint = str(cfg.get("endpoint", "") or "").strip().lower()
    api_key = str(cfg.get("api_key", "") or "").strip()
    model = str(cfg.get("model", "") or "").strip()

    is_local = any(
        host in endpoint
        for host in ("localhost", "127.0.0.1", "0.0.0.0", "192.168.", "10.", "172.16.", "172.17.", ".local", ".internal")
    )

    if is_local and endpoint:
        return {
            "connected": True,
            "is_external_paid": False,
            "provider_label": f"내부/로컬 AI ({model or 'Local LLM'})",
            "notice_text": "💡 현재 내부망/로컬 AI 서버가 연결되어 있어 외부 API 과금 없이 자동분류를 사용할 수 있습니다.",
            "config": cfg,
        }

    if api_key and endpoint:
        return {
            "connected": True,
            "is_external_paid": True,
            "provider_label": f"외부 AI ({provider or model or 'External API'})",
            "notice_text": (
                f"💰 [외부 유료 AI 과금 안내] 현재 외부 AI({provider or model})가 연결되어 있습니다. "
                "자동분류 실행 시 문서 제목·요약 텍스트가 전송되며 외부 AI API 이용 요금(토큰 비용)이 발생할 수 있습니다."
            ),
            "config": cfg,
        }

    return {
        "connected": False,
        "is_external_paid": True,
        "provider_label": "미연결",
        "notice_text": (
            "⚠️ 외부 AI가 연결되어 있지 않습니다. 자동분류기는 AI 연결 시에만 사용 가능하며, "
            "외부 상용 AI(OpenAI·Gemini·Claude 등) 연결 시 API 호출 금액(토큰 비용)이 발생합니다."
        ),
        "config": cfg,
    }


def build_auto_classify_messages(
    file_name: str,
    title: str,
    content_snippet: str,
    categories: list[str],
) -> list[dict[str, str]]:
    """파일명, 업무명, 본문 앞부분과 현재 카테고리 목록을 바탕으로 최적 업무 분류를 추천하는 프롬프트 구성"""
    cat_list_str = "\n".join(f"- {c}" for c in categories) if categories else "- 일반 업무"
    system_prompt = (
        "당신은 대한민국 공공기관·공무원 업무 문서 자동분류 AI입니다.\n"
        "사용자가 추가하려는 파일의 [업무명(파일명)]과 [본문 요약]을 보고 가장 적합한 업무 분류 1개를 골라 JSON 형식으로만 응답하세요.\n"
        "- 기존 분류 목록에 '일반 행정', '일반 업무' 같은 포괄적 기본 분류만 있거나 문서 성격과 구체적으로 맞는 분류가 없다면, "
        "문서 성격에 맞는 구체적인 새 업무 분류명(예: 예산·회계, 인사·복무, 계약·구매, 법령·규정, 민원·행정, 기획·보고, 보안·개인정보, 시설·물품, 차량·장비관리 등)을 적극 제안하세요.\n"
        "- 기존 분류 목록에 이미 구체적이고 정확히 일치하는 분류가 있을 때만 기존 분류명을 그대로 사용하세요.\n"
        "- 출력은 반드시 아래와 같은 순수 JSON 1줄만 출력하세요 (코드블록이나 부연 설명 금지):\n"
        '{"category": "추천분류명", "reason": "추천 사유 1줄 요약 (35자 이내)"}'
    )
    user_prompt = (
        f"[현재 등록된 업무 분류 목록]\n{cat_list_str}\n\n"
        f"[파일명]: {file_name}\n"
        f"[업무명]: {title}\n"
        f"[본문 내용 일부]:\n{content_snippet[:600] if content_snippet else '(본문 텍스트 없음 - 업무명 기준 분석)'}\n\n"
        "위 업무가 어떤 분류에 들어가면 좋을지 JSON으로 답하세요."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_auto_classify_response(raw_text: str, categories: list[str]) -> tuple[str, str]:
    """AI 응답에서 (추천 카테고리명, 사유) 추출"""
    cleaned = (raw_text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            cat = str(data.get("category", "") or "").strip()
            reason = str(data.get("reason", "") or "").strip()
            if cat:
                # 대소문자/공백 무시 매칭 보정
                for existing in categories:
                    if existing.strip() == cat or existing.replace(" ", "") == cat.replace(" ", ""):
                        return existing, reason
                return cat, reason
        except Exception:
            pass

    # JSON 파싱 실패 시 기존 카테고리 이름이 응답에 포함되어 있는지 확인
    for existing in categories:
        if existing in cleaned:
            return existing, cleaned[:60]

    return (categories[0] if categories else "일반 업무"), cleaned[:60]


# =============================================================================
# 3. 폴더 불러오기 시 폴더·파일 AI 업무분류 자동 정리
# =============================================================================

_GENERIC_FOLDER_NAMES = {
    "새 폴더", "새폴더", "new folder", "기타", "임시", "temp", "tmp", "백업", "backup",
    "문서", "documents", "자료", "다운로드", "downloads", "바탕화면", "desktop", "작업",
    "완료", "보관", "미분류", "파일", "첨부", "work", "취합", "공유",
}

_DOMAIN_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("예산·회계", ["예산", "결산", "회계", "지출", "집행", "세입", "세출", "보조금", "기금", "정산", "수당", "여비", "급량비", "추경"]),
    ("계약·구매", ["계약", "조달", "입찰", "구매", "발주", "용역", "물품구매", "수의계약", "과업지시", "제안요청", "rfp", "검수", "납품"]),
    ("인사·복무", ["인사", "복무", "휴가", "연가", "출장", "근무", "당직", "발령", "채용", "임용", "승진", "평정", "성과상여", "조직", "정원"]),
    ("법령·규정", ["법령", "조례", "규칙", "규정", "지침", "훈령", "예규", "매뉴얼", "개정", "제정", "입법", "법제", "고시", "공고"]),
    ("민원·행정", ["민원", "진정", "정보공개", "청구", "국민신문고", "회신", "답변", "처리기한", "인허가", "신고", "수리", "안내문"]),
    ("보안·개인정보", ["보안", "개인정보", "감사", "점검", "청렴", "비밀", "암호", "출입", "정보보호", "사이버", "취약점", "유출"]),
    ("기획·보고", ["기획", "업무계획", "주요업무", "보고서", "추진계획", "실적", "성과", "평가", "회의", "간담회", "결과보고", "동향", "보도자료"]),
    ("교육·행사", ["교육", "연수", "워크숍", "세미나", "행사", "캠페인", "홍보", "설문", "안내", "초청", "강사", "수료"]),
    ("시설·물품", ["시설", "안전", "점검표", "차량", "청사", "유지보수", "장비", "비품", "재산", "물품", "재물조사", "소방", "방재"]),
]


def extract_quick_file_snippet(file_path: Path, max_chars: int = 220) -> str:
    """폴더 내 파일에서 분류 판단용 앞부분 텍스트 스니펫을 빠르게 추출"""
    try:
        if not file_path.exists() or not file_path.is_file():
            return ""
        if file_path.stat().st_size > 8 * 1024 * 1024:
            return ""
        ext = file_path.suffix.lower()
        if ext in (".txt", ".md", ".csv", ".json"):
            raw = file_path.read_text(encoding="utf-8", errors="ignore")
            return re.sub(r"\s+", " ", raw).strip()[:max_chars]
        if ext == ".hwpx":
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in sorted(zf.namelist()):
                    if name.startswith("Contents/section") and name.endswith(".xml"):
                        xml_bytes = zf.read(name)
                        root = ET.fromstring(xml_bytes)
                        texts = [t.strip() for t in root.itertext() if t and t.strip()]
                        return " ".join(texts)[:max_chars]
        if ext == ".docx":
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(file_path, "r") as zf:
                if "word/document.xml" in zf.namelist():
                    xml_bytes = zf.read("word/document.xml")
                    root = ET.fromstring(xml_bytes)
                    texts = [t.strip() for t in root.itertext() if t and t.strip()]
                    return " ".join(texts)[:max_chars]
    except Exception:
        pass
    return ""


def _clean_subfolder_candidate(folder_name: str) -> str:
    """하위 폴더명이 의미 있는 업무분류명인지 정제하여 반환 (의미 없는 이름이면 빈 문자열)"""
    s = re.sub(r"^[\d\-_.\s()\[\]]+", "", folder_name or "").strip()
    if not s or len(s) < 2:
        return ""
    if s.lower() in _GENERIC_FOLDER_NAMES:
        return ""
    if re.match(r"^(?:20\d\d|\d{1,2}월|\d{1,2}분기|상반기|하반기)$", s):
        return ""
    return s


def classify_folder_files_heuristic(
    root_dir: Path,
    files: list[Path],
    existing_categories: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """
    폴더 경로, 파일명, 문서 스니펫을 분석하여 각 파일의 적합한 업무분류와 근거를 반환합니다.
    반환 형식: {str(file_path): {"category": "분류명", "reason": "분류 근거"}}
    """
    existing = [c.strip() for c in (existing_categories or []) if c and c.strip()]
    existing_clean_map: list[tuple[str, str]] = []
    for c in existing:
        core = re.sub(r"^\d+\.\s*", "", c).strip()
        if core and core not in ("일반 업무", "기본 업무"):
            existing_clean_map.append((c, core))

    result: dict[str, dict[str, str]] = {}

    for fp in files:
        try:
            rel_parent = fp.parent.relative_to(root_dir)
            rel_parts = [p for p in rel_parent.parts if p and p != "."]
        except Exception:
            rel_parts = []

        subfolder_str = " / ".join(rel_parts)
        stem = fp.stem
        snippet = extract_quick_file_snippet(fp, max_chars=200)
        combined = f"{subfolder_str} {stem} {snippet}".lower()

        chosen_cat = ""
        reason = ""

        # 1) 기존 등록된 업무 분류와 키워드 일치 확인
        for orig_cat, core_name in existing_clean_map:
            tokens = [t for t in re.split(r"[·/\s,()]+", core_name) if len(t) >= 2]
            matched_tok = next((t for t in tokens if t.lower() in combined), "")
            if matched_tok:
                chosen_cat = orig_cat
                reason = f"기존 분류 '{core_name}' 키워드 일치"
                break

        # 2) 공공·행정 표준 도메인 키워드 매칭
        if not chosen_cat:
            best_score = 0
            best_domain = ""
            best_kw = ""
            for domain_cat, kws in _DOMAIN_CATEGORY_RULES:
                matched = [kw for kw in kws if kw in combined]
                score = len(matched)
                # 파일명이나 폴더명에 직접 들어가 있으면 가중치 부여
                for kw in matched:
                    if kw in stem.lower() or kw in subfolder_str.lower():
                        score += 2
                if score > best_score:
                    best_score = score
                    best_domain = domain_cat
                    best_kw = matched[0] if matched else ""
            if best_domain and best_score > 0:
                # 기존 분류 중 동일한 도메인명이 있으면 재사용
                for orig_cat, core_name in existing_clean_map:
                    if best_domain in core_name or core_name in best_domain:
                        best_domain = orig_cat
                        break
                chosen_cat = best_domain
                reason = f"'{best_kw}' 관련 업무 문서"

        # 3) 의미 있는 하위 폴더명 활용
        if not chosen_cat and rel_parts:
            for part in reversed(rel_parts):
                cleaned_sub = _clean_subfolder_candidate(part)
                if cleaned_sub:
                    chosen_cat = cleaned_sub
                    reason = f"하위 폴더 '{part}' 기준 분류"
                    break

        # 4) 루트 폴더명 또는 일반 업무 폴백
        if not chosen_cat:
            cleaned_root = _clean_subfolder_candidate(root_dir.name)
            chosen_cat = cleaned_root or (existing[0] if existing else "일반 업무")
            reason = "폴더 기본 분류"

        result[str(fp)] = {"category": chosen_cat, "reason": reason}

    return result


def build_folder_ai_classify_messages(
    root_dir: Path,
    files: list[Path],
    existing_categories: list[str] | None = None,
) -> list[dict[str, str]]:
    """폴더 내 여러 파일의 경로·파일명·스니펫을 한 번에 AI로 분석하여 업무분류를 정리하는 프롬프트 생성"""
    cats = [c.strip() for c in (existing_categories or []) if c and c.strip()]
    cat_list_str = ", ".join(cats) if cats else "없음"

    file_lines: list[str] = []
    for idx, fp in enumerate(files[:60]):
        try:
            rel_parent = str(fp.parent.relative_to(root_dir)).replace("\\", "/")
            if rel_parent == ".":
                rel_parent = "(최상위)"
        except Exception:
            rel_parent = "(최상위)"
        snippet = extract_quick_file_snippet(fp, max_chars=120)
        snip_part = f" | 내용요약: {snippet}" if snippet else ""
        file_lines.append(f"[{idx}] 폴더: {rel_parent} | 파일명: {fp.name}{snip_part}")

    system_prompt = (
        "당신은 대한민국 공공기관·일반행정 업무 폴더 및 문서 자동분류 AI입니다.\n"
        "사용자가 불러온 폴더 내의 하위 폴더 경로, 파일명, 문서 내용 요약을 분석하여 "
        "체계적인 '업무 분류(category)'로 자동 정리하세요.\n\n"
        "[분류 규칙]\n"
        "1. 현재 등록된 업무 분류 중 성격이 일치하는 분류가 있다면 해당 분류명을 우선 사용하세요.\n"
        "2. 기존 분류에 없다면 파일들의 성격을 묶어 간결하고 명확한 업무 분류명(예: 예산·회계, 인사·복무, 계약·구매, 법령·규정, 민원·행정, 기획·보고, 보안·개인정보, 교육·행사, 시설·물품 등 또는 폴더 특성에 맞는 명칭)으로 정리하세요.\n"
        "3. 분류가 지나치게 파편화되지 않도록 유사한 문서들은 동일한 업무 분류명으로 묶어주세요.\n"
        "4. 반드시 아래 순수 JSON 형식으로만 응답하세요 (마크다운 코드블록이나 부연 설명 금지):\n"
        '{"items": [{"id": 0, "category": "배정할 업무분류명", "reason": "분류 근거 요약(20자 이내)"}]}'
    )

    user_prompt = (
        f"[불러온 최상위 폴더명]: {root_dir.name}\n"
        f"[현재 등록된 업무 분류]: {cat_list_str}\n\n"
        f"[분석 대상 파일 목록 ({len(file_lines)}개)]\n"
        + "\n".join(file_lines)
        + "\n\n위 모든 파일의 id별 최적 업무분류(category)와 근거(reason)를 JSON으로 출력하세요."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_folder_ai_classify_response(
    raw_text: str,
    files: list[Path],
    root_dir: Path,
    existing_categories: list[str] | None = None,
) -> dict[str, dict[str, str]]:
    """AI 폴더 분류 JSON 응답을 파싱하고 누락 항목은 휴리스틱 분류기로 보완"""
    fallback = classify_folder_files_heuristic(root_dir, files, existing_categories)
    cleaned = (raw_text or "").strip()
    m_code = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", cleaned, flags=re.DOTALL)
    if m_code:
        cleaned = m_code.group(1).strip()
    else:
        m_obj = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
        if m_obj:
            cleaned = m_obj.group(1).strip()

    try:
        data = json.loads(cleaned)
        items_list: list[Any] = []
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                items_list = data["items"]
            else:
                for k, v in data.items():
                    if str(k).isdigit() and isinstance(v, dict):
                        items_list.append({"id": int(k), **v})
        elif isinstance(data, list):
            items_list = data

        existing = [c.strip() for c in (existing_categories or []) if c and c.strip()]
        for entry in items_list:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("id")
            if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(files):
                continue
            cat = str(entry.get("category", "") or "").strip()
            reason = str(entry.get("reason", "") or "AI 문서·폴더 분석").strip()
            if not cat:
                continue
            for ex in existing:
                if ex == cat or ex.replace(" ", "") == cat.replace(" ", ""):
                    cat = ex
                    break
            fallback[str(files[idx])] = {"category": cat, "reason": reason}
    except Exception as e:
        logger.warning("Failed to parse folder AI classification JSON: %s", e)

    return fallback

