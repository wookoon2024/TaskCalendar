from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css, make_ui_font_like, scale_px

logger = logging.getLogger(__name__)


class _NoBarListSelectionDelegate(QStyledItemDelegate):
    """선택 시 좌측 기본 마크를 제거하고 행 전체에 연한 배경만 칠하는 델리게이트"""

    def __init__(self, parent: QWidget | None = None, sel_bg: str = "#EFF6FF", sel_fg: str = "#1E293B") -> None:
        super().__init__(parent)
        self._sel_bg = QColor(sel_bg)
        self._sel_fg = QColor(sel_fg)

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        is_selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if is_selected:
            painter.save()
            painter.fillRect(opt.rect, self._sel_bg)
            painter.restore()
            opt.state &= ~QStyle.StateFlag.State_Selected
            opt.state &= ~QStyle.StateFlag.State_HasFocus
            opt.backgroundBrush = Qt.BrushStyle.NoBrush
            opt.palette.setColor(QPalette.ColorRole.Text, self._sel_fg)
            opt.palette.setColor(QPalette.ColorRole.HighlightedText, self._sel_fg)
        else:
            opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, opt, index)


class _LawSearchWorker(QThread):
    finished_laws = Signal(list)

    def __init__(self, keyword: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keyword = keyword.strip()

    def run(self) -> None:
        if not self._keyword:
            self.finished_laws.emit([])
            return
        try:
            from taskcalendar.law_reference import search_laws

            results = search_laws(self._keyword, limit=10)
            items = []
            for r in results:
                items.append({
                    "name": r.name,
                    "type": r.law_kind or "법령",
                    "ministry": r.office or "-",
                    "enforce_date": r.enforce_date or "",
                    "law_no": r.mst or "",
                    "url": getattr(r, "url", ""),
                })
            self.finished_laws.emit(items)
        except Exception as e:
            logger.warning("Law search error: %s", e)
            self.finished_laws.emit([])


class _LawSelectionDialog(QDialog):
    """검색된 관련 법령 목록에서 보고서에 표로 삽입할 법령을 직접 선택하는 다이얼로그"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        keyword: str = "",
        law_items: list[dict[str, Any]] | None = None,
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("관련 법령 선택")
        self.resize(scale_px(580), scale_px(420))
        self.setModal(True)

        pal = palette or {}
        bg = pal.get("bg", "#F8FAFC")
        panel = pal.get("panel", "#FFFFFF")
        text = pal.get("text", "#1E293B")
        muted = pal.get("muted", "#64748B")
        line = pal.get("line", "#CBD5E1")
        accent = pal.get("accent", "#2563EB")
        ff_css = font_family_css()

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: {ff_css};
            }}
            QLabel {{
                color: {text};
                font-family: {ff_css};
            }}
            QListWidget {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                outline: none;
                font-family: {ff_css};
            }}
            QListWidget::item {{
                padding: 8px 10px;
                border-bottom: 1px solid #F1F5F9;
            }}
            QListWidget::item:hover {{
                background-color: #F8FAFC;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        # 헤더 안내
        hdr_lbl = QLabel(f"• 국가법령정보센터 검색 결과 ('{keyword}'): 총 {len(law_items or [])}건")
        hdr_lbl.setFont(make_ui_font_like(13, bold=True))
        layout.addWidget(hdr_lbl)

        sub_lbl = QLabel("보고서 본문의 [관련 법령 및 근거] 표에 포함할 법령을 체크해 주세요.")
        sub_lbl.setFont(make_ui_font_like(11))
        sub_lbl.setStyleSheet(f"color: {muted};")
        layout.addWidget(sub_lbl)

        # 전체 선택 / 해제 버튼
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(8)
        self.btn_select_all = QPushButton("전체 선택")
        self.btn_select_all.setFixedHeight(scale_px(26))
        self.btn_select_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select_all.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 0 8px; font-size: {scale_px(11)}px;")
        self.btn_select_all.clicked.connect(self._select_all)
        sel_bar.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("전체 해제")
        self.btn_deselect_all.setFixedHeight(scale_px(26))
        self.btn_deselect_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_deselect_all.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 0 8px; font-size: {scale_px(11)}px;")
        self.btn_deselect_all.clicked.connect(self._deselect_all)
        sel_bar.addWidget(self.btn_deselect_all)
        sel_bar.addStretch(1)
        layout.addLayout(sel_bar)

        # 체크 가능한 법령 리스트
        self.list_widget = QListWidget()
        for idx, item in enumerate(law_items or []):
            name = str(item.get("name", "")).strip()
            ltype = str(item.get("type", "법령")).strip()
            ministry = str(item.get("ministry", "-")).strip()
            enf_date = str(item.get("enforce_date", "")).strip()
            meta_str = f"[{ltype}] 소관: {ministry}" + (f" | 시행: {enf_date}" if enf_date else "")
            display_text = f"{name}\n   └ {meta_str}"

            list_item = QListWidgetItem(display_text)
            list_item.setFlags(list_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # 기본적으로 상위 3개까지 자동 체크
            list_item.setCheckState(Qt.CheckState.Checked if idx < 3 else Qt.CheckState.Unchecked)
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self.list_widget.addItem(list_item)
        layout.addWidget(self.list_widget, 1)

        # 하단 액션 버튼
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)
        btn_bar.addStretch(1)

        btn_ok = QPushButton("선택 완료")
        btn_ok.setFixedHeight(scale_px(32))
        btn_ok.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.setStyleSheet(f"background-color: {accent}; color: #FFFFFF; border: none; border-radius: 6px; padding: 0 16px; font-weight: bold;")
        btn_ok.clicked.connect(self.accept)
        btn_bar.addWidget(btn_ok)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(scale_px(32))
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 0 14px;")
        btn_cancel.clicked.connect(self.reject)
        btn_bar.addWidget(btn_cancel)

        layout.addLayout(btn_bar)

    def _select_all(self) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    def get_selected_items(self) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                data = it.data(Qt.ItemDataRole.UserRole)
                if data:
                    selected.append(data)
        return selected


class _AIProgressDialog(QDialog):
    """AI 보고서 생성 중 화면 중앙에 표시되는 모던 진행 상태 다이얼로그"""

    def __init__(
        self,
        parent: QWidget | None = None,
        title: str = "AI 보고서 작성",
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        self.setFixedSize(scale_px(420), scale_px(175))
        self.setModal(True)
        self._cancelled = False

        pal = palette or {}
        bg = pal.get("panel", "#FFFFFF")
        text = pal.get("text", "#1E293B")
        muted = pal.get("muted", "#64748B")
        line = pal.get("line", "#CBD5E1")
        accent = pal.get("accent", "#2563EB")
        ff_css = font_family_css()

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: {ff_css};
                border: 1px solid {line};
                border-radius: 8px;
            }}
            QLabel {{
                color: {text};
                font-family: {ff_css};
                background: transparent;
            }}
            QProgressBar {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: #F1F5F9;
                height: 8px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 4px;
            }}
            QPushButton {{
                background-color: {bg};
                border: 1px solid {line};
                border-radius: 6px;
                color: {text};
                padding: 4px 16px;
                font-family: {ff_css};
                font-size: {scale_px(12)}px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: #F1F5F9;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        self.lbl_main = QLabel("연결된 AI가 메모를 분석하여 공문서를 구성하고 있습니다")
        self.lbl_main.setFont(make_ui_font_like(13, bold=True))
        self.lbl_main.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_main)

        self.lbl_sub = QLabel("표준 보고서 서식(HWPX) 구성 중 (약 5~10초 소요)...")
        self.lbl_sub.setFont(make_ui_font_like(11))
        self.lbl_sub.setStyleSheet(f"color: {muted};")
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_sub)

        self.pbar = QProgressBar()
        self.pbar.setRange(0, 0)
        self.pbar.setFixedHeight(8)
        layout.addWidget(self.pbar)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.reject()

    def is_cancelled(self) -> bool:
        return self._cancelled


def get_supported_ministries() -> list[str]:
    """korean-report-hwpx가 지원하는 중앙행정기관 목록 반환"""
    try:
        from taskcalendar.korean_report_hwpx.server import list_ministries

        info = list_ministries()
        if isinstance(info, list):
            items = [str(it.get("기관", "")).strip() for it in info if isinstance(it, dict) and it.get("기관")]
        elif isinstance(info, dict):
            items = list(info.get("지원기관", []) or [])
        else:
            items = []
        return ["공통"] + [m for m in items if m and m != "공통(미지정)"]
    except Exception as e:
        logger.warning("Failed to load ministry list: %s", e)
        return ["공통", "행정안전부", "기획예산처", "교육부", "고용노동부", "과학기술정보통신부"]


def extract_keywords_for_law(title: str, memo_text: str) -> str:
    """제목이나 메모 본문에서 법령 검색용 핵심 단어 추출"""
    src = f"{title} {memo_text}".strip()
    if not src:
        return ""
    m = re.search(r"([가-힣]{2,10}(?:법|령|규칙|조례|규정))", src)
    if m:
        return m.group(1)
    words = re.findall(r"[가-힣]{2,8}", title or memo_text)
    stop = {"보고서", "계획서", "안내", "검토", "결과", "업무", "메모", "관련", "추진", "계획", "현황", "보고", "방안"}
    filtered = [w for w in words if w not in stop]
    return filtered[0] if filtered else (words[0] if words else "")


def memo_to_structure_local(
    title: str,
    memo_text: str,
    *,
    doc_type: str = "report",
    dept: str = "",
    date_str: str = "",
    law_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    AI 없이도 메모 원문(줄바꿈, 글머리 기호, 마크다운 표 등)을 분석하여
    korean-report-hwpx 표준 JSON 구조(title, date, dept, summary, body, appendix)로 변환합니다.
    """
    clean_title = (title or "").strip() or "업무 보고서"
    clean_date = (date_str or "").strip() or date.today().strftime("%Y. %m. %d.")
    clean_dept = (dept or "").strip() or "담당부서"

    lines = (memo_text or "").splitlines()
    body: list[dict[str, Any]] = []
    summary_lines: list[str] = []

    table_buffer: list[list[str]] = []

    def _flush_table() -> None:
        nonlocal table_buffer
        if not table_buffer:
            return
        if len(table_buffer) >= 2:
            headers = table_buffer[0]
            rows = []
            for r in table_buffer[1:]:
                if all(re.match(r"^[-:]+$", c.strip()) for c in r if c.strip()):
                    continue
                padded = (r + [""] * len(headers))[: len(headers)]
                rows.append(padded)
            if headers and rows:
                body.append({"type": "table", "headers": headers, "rows": rows})
        else:
            for r in table_buffer:
                body.append({"type": "l1", "text": " / ".join(r)})
        table_buffer = []

    has_heading = False
    for raw_line in lines:
        s = raw_line.strip()
        if not s:
            _flush_table()
            continue

        # 마크다운/파이프 표 감지: | 컬럼1 | 컬럼2 |
        if "|" in s and (s.startswith("|") or s.count("|") >= 2):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if any(cells):
                table_buffer.append(cells)
                continue

        _flush_table()

        # 소제목 감지: # 제목, [제목], 1. 제목, Ⅰ. 제목 등
        m_sec = re.match(r"^(?:#+\s*|\[([^\]]+)\]$|(?:[0-9]+|[ⅠⅡⅢⅣⅤI]+)\.\s+)(.+)?$", s)
        if m_sec and len(s) <= 50:
            sec_text = (m_sec.group(1) or m_sec.group(2) or s).strip()
            sec_text = re.sub(r"^(?:#+\s*|(?:[0-9]+|[ⅠⅡⅢⅣⅤI]+)\.\s*)", "", sec_text).strip("[] ")
            if sec_text:
                body.append({"type": "h", "text": sec_text})
                has_heading = True
                continue

        # 개조식 계층 감지
        if s.startswith(("□", "■", "◆", "◈")):
            txt = re.sub(r"^[□■◆◈]\s*", "", s).strip()
            if txt:
                body.append({"type": "l1", "text": txt})
                if len(summary_lines) < 3:
                    summary_lines.append(txt)
        elif s.startswith(("ㅇ", "○", "◯", "•", "·", "*")):
            txt = re.sub(r"^[ㅇ○◯•·*]\s*", "", s).strip()
            if txt:
                body.append({"type": "l2", "text": txt})
        elif s.startswith(("-", "―", "–", "▸", "▹")):
            txt = re.sub(r"^[-―–▸▹]\s*", "", s).strip()
            if txt:
                body.append({"type": "l3", "text": txt})
        elif s.startswith(("※", "☞", "참고:")):
            txt = re.sub(r"^(?:※|☞|참고:?)\s*", "", s).strip()
            if txt:
                body.append({"type": "ref", "label": "참고", "text": txt})
        else:
            # 들여쓰기 수준에 따라 l1 / l2 / l3 자동 배정
            leading_spaces = len(raw_line) - len(raw_line.lstrip(" \t"))
            if leading_spaces >= 4:
                body.append({"type": "l3", "text": s})
            elif leading_spaces >= 2:
                body.append({"type": "l2", "text": s})
            else:
                body.append({"type": "l1", "text": s})
                if len(summary_lines) < 3:
                    summary_lines.append(s)

    _flush_table()

    if not body:
        body.append({"type": "h", "text": "추진 내용"})
        body.append({"type": "l1", "text": clean_title})
    elif not has_heading and body[0].get("type") != "h":
        body.insert(0, {"type": "h", "text": "주요 내용"})

    # 관련 법령 및 근거 자동 추가
    if law_items:
        body.append({"type": "h", "text": "관련 법령 및 근거"})
        law_rows: list[list[str]] = []
        for it in law_items[:5]:
            lname = str(it.get("name", "") or "").strip()
            ltype = str(it.get("type", "") or "법령").strip()
            ministry = str(it.get("ministry", "") or "-").strip()
            enf_date = str(it.get("enforce_date", "") or "").strip()
            law_no = str(it.get("law_no", "") or "").strip()
            detail = f"{ltype}" + (f" 제{law_no}호" if law_no else "")
            meta = ministry + (f" / 시행 {enf_date}" if enf_date else "")
            if lname:
                law_rows.append([lname, detail, meta])
        if law_rows:
            body.append(
                {
                    "type": "table",
                    "headers": ["법령·행정규칙명", "구분 및 공포번호", "소관부처 및 시행일"],
                    "rows": law_rows,
                }
            )

    structure: dict[str, Any] = {
        "title": clean_title,
        "date": clean_date,
        "dept": clean_dept,
        "body": body,
    }
    if doc_type == "press_release":
        structure["summary"] = summary_lines[:3] if summary_lines else [clean_title]
    return structure


def build_ai_memo_prompt(
    title: str,
    memo_text: str,
    *,
    doc_type: str = "report",
    style: str = "standard",
    report_kind: str = "standard",
    ministry: str = "",
    dept: str = "",
    date_str: str = "",
    law_items: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """연결된 AI가 메모를 korean-report-hwpx JSON 구조로 작성하도록 프롬프트 생성"""
    clean_title = (title or "").strip() or "업무 보고서"
    clean_date = (date_str or "").strip() or date.today().strftime("%Y. %m. %d.")
    clean_dept = (dept or "").strip() or "담당부서"

    law_context = ""
    if law_items:
        lines = []
        for it in law_items[:5]:
            lines.append(
                f"- {it.get('name', '')} ({it.get('type', '법령')}, 소관: {it.get('ministry', '-')}, 시행일: {it.get('enforce_date', '-')})"
            )
        law_context = "\n\n[참고할 관련 법령 목록]\n" + "\n".join(lines)

    doc_desc = "보도자료" if doc_type == "press_release" else "공공기관 개조식 보고서"
    system_prompt = (
        "당신은 대한민국 행정기관 공문서 및 보고서 작성 전문가입니다.\n"
        "사용자의 간단한 업무 메모를 바탕으로 korean-report-hwpx 엔진에 입력할 JSON 구조(structure)만 출력하세요.\n"
        "반드시 순수 JSON 객체만 출력하고 설명이나 마크다운 외곽 텍스트를 붙이지 마세요.\n\n"
        "[JSON 스키마 규칙]\n"
        "{\n"
        '  "title": "문서 제목",\n'
        '  "subtitle": "부제목 (선택)",\n'
        '  "date": "YYYY. M. D.",\n'
        '  "dept": "담당부서명",\n'
        '  "summary": ["보도자료일 때 핵심 요약 1~3줄 (보고서면 생략 가능)"],\n'
        '  "body": [\n'
        '    {"type": "h", "text": "대항목 제목 (번호 없이 텍스트만, 엔진이 1. 2. 자동 부여)"},\n'
        '    {"type": "l1", "text": "□ 기호 없이 핵심 요지 (명사형/개조식 종결)"},\n'
        '    {"type": "l2", "text": "ㅇ 기호 없이 세부 내용 설명"},\n'
        '    {"type": "l3", "text": "- 기호 없이 보충 사례나 수치"},\n'
        '    {"type": "table", "headers": ["열1", "열2", "열3"], "rows": [["값1", "값2", "값3"]]},\n'
        '    {"type": "ref", "label": "참고", "text": "참고 설명 텍스트"}\n'
        "  ]\n"
        "}\n\n"
        "[작성 원칙]\n"
        "1. l1, l2, l3의 text 앞에는 □, ㅇ, -, · 같은 기호를 절대 넣지 마세요 (HWPX 엔진이 자동 삽입함).\n"
        "2. h의 text 앞에도 '1.', '2.' 같은 번호를 넣지 마세요 (HWPX 엔진이 자동 번호 부여).\n"
        "3. 개조식 종결어미(~함, ~음, ~추진, ~필요 등)를 사용하여 간결하고 명확하게 작성하세요.\n"
        "4. 메모에 일정, 예산, 비교 항목이나 관련 법령이 있으면 적극 표(table)로 구조화하세요."
    )

    user_prompt = (
        f"[작성할 문서 종류]: {doc_desc} (style={style}, report_kind={report_kind})\n"
        f"[소속 기관]: {ministry or '공통'}\n"
        f"[담당 부서]: {clean_dept}\n"
        f"[작성 일자]: {clean_date}\n"
        f"[문서 제목 초안]: {clean_title}\n\n"
        f"[사용자 메모 원문]:\n{memo_text.strip() or clean_title}"
        f"{law_context}\n\n"
        "위 메모를 바탕으로 격식 있는 공문서 HWPX 생성용 JSON을 반환하세요."
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_ai_structure_json(
    raw_response: str,
    fallback_title: str,
    fallback_memo: str,
    *,
    doc_type: str = "report",
    dept: str = "",
    date_str: str = "",
    law_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """AI 응답에서 JSON을 추출·검증하고 실패 시 로컬 변환기로 폴백"""
    text = (raw_response or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("body"), list) and data["body"]:
            if not data.get("title"):
                data["title"] = fallback_title or "업무 보고서"
            if not data.get("date"):
                data["date"] = date_str or date.today().strftime("%Y. %m. %d.")
            if not data.get("dept"):
                data["dept"] = dept or "담당부서"
            return data
    except Exception as e:
        logger.warning("AI JSON parse failed, falling back to local parser: %s", e)

    return memo_to_structure_local(
        fallback_title,
        fallback_memo,
        doc_type=doc_type,
        dept=dept,
        date_str=date_str,
        law_items=law_items,
    )


def structure_to_plain_text(structure: dict[str, Any]) -> str:
    """생성된 HWPX 구조체를 업무 본문 검색·미리보기용 일반 텍스트로 변환"""
    lines: list[str] = []
    title = str(structure.get("title", "") or "").strip()
    subtitle = str(structure.get("subtitle", "") or "").strip()
    dept = str(structure.get("dept", "") or "").strip()
    dt = str(structure.get("date", "") or "").strip()

    if title:
        lines.append(title)
    if subtitle:
        lines.append(subtitle)
    if dept or dt:
        lines.append(f"{dept} ({dt})".strip())
    lines.append("")

    sec_no = 0
    for item in structure.get("body", []) or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type", "")
        txt = str(item.get("text", "") or "").strip()
        if itype == "h":
            sec_no += 1
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{sec_no}. {txt}")
        elif itype == "l1":
            lines.append(f"□ {txt}")
        elif itype == "l2":
            lines.append(f"  ㅇ {txt}")
        elif itype == "l3":
            lines.append(f"    - {txt}")
        elif itype == "ref":
            lbl = str(item.get("label", "참고") or "참고").strip()
            lines.append(f"  ※ {lbl}: {txt}")
        elif itype == "table":
            headers = item.get("headers", []) or []
            rows = item.get("rows", []) or []
            if headers:
                lines.append("  | " + " | ".join(str(h) for h in headers) + " |")
            for r in rows:
                lines.append("  | " + " | ".join(str(c) for c in r) + " |")

    return "\n".join(lines).strip()


def generate_hwpx_from_structure(
    structure: dict[str, Any],
    *,
    doc_type: str = "report",
    ministry: str = "",
    style: str = "standard",
    report_kind: str = "standard",
) -> tuple[bytes, str, list[str], dict[str, Any]]:
    """
    korean_report_hwpx 엔진의 build_document를 호출하여 HWPX 바이너리를 생성하고 반환합니다.
    반환: (hwpx_bytes, plain_text, warnings, final_structure)
    """
    from taskcalendar.korean_report_hwpx.server import build_document

    ministry_arg = "" if ministry in ("", "공통", "공통(미지정)") else ministry
    with tempfile.TemporaryDirectory(prefix="kr_report_") as tmpdir:
        out_file = Path(tmpdir) / "generated_report.hwpx"
        res = build_document(
            structure=structure,
            doc_type=doc_type,
            ministry=ministry_arg,
            out_path=str(out_file),
            style=style,
            report_kind=report_kind,
        )
        if "error" in res:
            raise ValueError(str(res["error"]))
        hwpx_path = Path(res["hwpx"])
        hwpx_bytes = hwpx_path.read_bytes()
        final_struct = res.get("structure") or structure
        warnings = list(res.get("warnings") or [])
        plain_text = structure_to_plain_text(final_struct)
        return hwpx_bytes, plain_text, warnings, final_struct


class MemoToReportDialog(QDialog):
    """
    메모로 공문서(보고서·보도자료) HWPX 만들기 다이얼로그
    - UpstageAI/korean-report-hwpx (MIT License) 엔진 내장
    - 저장된 메모 불러오기 또는 직접 메모 작성 지원
    - 국가법령정보센터 법령·근거 표 자동 삽입 지원
    - AI 자동 구조화 및 로컬 즉시 변환 모두 지원
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        repository: Any = None,
        palette: dict[str, str] | None = None,
        categories: list[Any] | None = None,
        default_category: str = "",
        initial_title: str = "",
        initial_memo_text: str = "",
        on_document_created: Callable[[str, str, bytes, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("메모로 보고서 만들기")
        self.resize(scale_px(900), scale_px(640))

        self._repository = repository
        self._palette = palette or {}
        self._categories = list(categories or [])
        self._default_category = default_category
        self._on_document_created = on_document_created

        self._law_items: list[dict[str, Any]] = []
        self._law_worker: _LawSearchWorker | None = None
        self._ai_worker: Any = None

        self.generated_hwpx_bytes: bytes | None = None
        self.generated_plain_text: str = ""
        self.generated_title: str = ""
        self.selected_category: str = default_category

        self._init_ui(initial_title, initial_memo_text)
        self._load_saved_memos()

    def _init_ui(self, initial_title: str, initial_memo_text: str) -> None:
        p = self._palette
        bg = p.get("bg", "#F8FAFC")
        panel = p.get("panel", "#FFFFFF")
        panel_alt = p.get("panel_alt", "#F1F5F9")
        text = p.get("text", "#1E293B")
        muted = p.get("muted", "#64748B")
        line = p.get("line", "#CBD5E1")
        accent = p.get("accent", "#2563EB")
        accent_soft = p.get("accent_soft", "#EFF6FF")

        ff_css = font_family_css()
        f_base = make_ui_font_like(12)
        f_bold = make_ui_font_like(12, bold=True)
        self.setFont(f_base)

        fs_12 = scale_px(12)
        fs_11 = scale_px(11)
        ctrl_h = scale_px(30)
        btn_h = scale_px(32)

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: {ff_css};
                font-size: {fs_12}px;
            }}
            QLabel {{
                color: {text};
                font-family: {ff_css};
                font-size: {fs_12}px;
                background: transparent;
            }}
            QLineEdit, QPlainTextEdit, QComboBox {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 8px;
                font-family: {ff_css};
                font-size: {fs_12}px;
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
                border: 1px solid {accent};
            }}
            QListWidget {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                outline: none;
                font-family: {ff_css};
                font-size: {fs_12}px;
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-bottom: 1px solid {panel_alt};
            }}
            QCheckBox {{
                color: {text};
                font-family: {ff_css};
                font-size: {fs_12}px;
                spacing: 6px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 상단 개조식 안내 바
        top_bar = QFrame()
        top_bar.setStyleSheet(
            f"QFrame {{ background-color: {panel}; border: 1px solid {line}; border-radius: 8px; }}"
        )
        top_lay = QHBoxLayout(top_bar)
        top_lay.setContentsMargins(12, 8, 12, 8)
        top_lay.setSpacing(8)

        self.status_lbl = QLabel("• 메모 선택 또는 직접 입력 후 문서 생성을 실행하세요")
        self.status_lbl.setFont(f_bold)
        self.status_lbl.setStyleSheet(f"color: {text}; border: none;")
        top_lay.addWidget(self.status_lbl)
        top_lay.addStretch(1)
        root.addWidget(top_bar)

        # 중앙 좌우 스플리터 (좌: 저장된 메모 목록 / 우: 서식 옵션 및 메모 편집)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # [좌측] 저장된 메모 선택 패널
        left_panel = QFrame()
        left_panel.setMinimumWidth(scale_px(220))
        left_panel.setMaximumWidth(scale_px(300))
        left_panel.setStyleSheet(
            f"QFrame {{ background-color: {panel}; border: 1px solid {line}; border-radius: 8px; }}"
        )
        left_lay = QVBoxLayout(left_panel)
        left_lay.setContentsMargins(10, 10, 10, 10)
        left_lay.setSpacing(6)

        memo_hdr = QLabel("• 저장된 메모 목록")
        memo_hdr.setFont(f_bold)
        memo_hdr.setStyleSheet(f"color: {text}; border: none;")
        left_lay.addWidget(memo_hdr)

        self.memo_search_edit = QLineEdit()
        self.memo_search_edit.setFixedHeight(ctrl_h)
        self.memo_search_edit.setPlaceholderText("메모 검색")
        self.memo_search_edit.textChanged.connect(self._filter_memo_list)
        left_lay.addWidget(self.memo_search_edit)

        self.memo_list = QListWidget()
        self.memo_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.memo_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.memo_list.setItemDelegate(
            _NoBarListSelectionDelegate(self.memo_list, sel_bg=accent_soft, sel_fg=text)
        )
        self.memo_list.itemClicked.connect(self._on_memo_item_clicked)
        left_lay.addWidget(self.memo_list, 1)

        splitter.addWidget(left_panel)

        # [우측] 문서 설정 및 메모 내용 편집 패널
        right_panel = QFrame()
        right_panel.setStyleSheet(
            f"QFrame {{ background-color: {panel}; border: 1px solid {line}; border-radius: 8px; }}"
        )
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(12, 10, 12, 10)
        right_lay.setSpacing(8)

        # 상단 서식 및 메타 정보 정리 카드
        opt_card = QFrame()
        opt_card.setStyleSheet(
            f"QFrame {{ background-color: {panel_alt}; border: 1px solid {line}; border-radius: 8px; }}"
        )
        opt_card_lay = QVBoxLayout(opt_card)
        opt_card_lay.setContentsMargins(12, 10, 12, 10)
        opt_card_lay.setSpacing(8)

        # 1행: 서식 종류 / 기관 서식
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        lbl_doc_type = QLabel("문서 종류")
        lbl_doc_type.setFont(f_bold)
        lbl_doc_type.setStyleSheet("border: none;")
        row1.addWidget(lbl_doc_type)

        self.combo_doc_type = QComboBox()
        self.combo_doc_type.setFixedHeight(ctrl_h)
        self.combo_doc_type.addItem("보고서 표준", ("report", "standard", "standard"))
        self.combo_doc_type.addItem("내부 결재 보고서", ("report", "standard", "internal"))
        self.combo_doc_type.addItem("간소화 보고서", ("report", "simplified", "standard"))
        self.combo_doc_type.addItem("보도자료", ("press_release", "standard", "standard"))
        row1.addWidget(self.combo_doc_type, 1)

        lbl_ministry = QLabel("기관 서식")
        lbl_ministry.setFont(f_bold)
        lbl_ministry.setStyleSheet("border: none;")
        row1.addWidget(lbl_ministry)

        self.combo_ministry = QComboBox()
        self.combo_ministry.setFixedHeight(ctrl_h)
        for m in get_supported_ministries():
            self.combo_ministry.addItem(m, m)
        row1.addWidget(self.combo_ministry, 1)

        opt_card_lay.addLayout(row1)

        # 2행: 문서 제목 (전체 폭)
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        lbl_title = QLabel("문서 제목")
        lbl_title.setFont(f_bold)
        lbl_title.setStyleSheet("border: none;")
        row2.addWidget(lbl_title)

        self.edit_title = QLineEdit(initial_title)
        self.edit_title.setFixedHeight(ctrl_h)
        self.edit_title.setPlaceholderText("보고서 제목 입력 (미입력 시 첫 줄 자동 활용)")
        row2.addWidget(self.edit_title, 1)

        opt_card_lay.addLayout(row2)
        right_lay.addWidget(opt_card)

        # 메모 원문 입력/편집
        lbl_memo = QLabel("• 메모 내용   |   줄바꿈, □ · ㅇ · - 개조식, | 표1 | 표2 | 자동 인식")
        lbl_memo.setFont(f_bold)
        lbl_memo.setStyleSheet(f"color: {text}; border: none;")
        right_lay.addWidget(lbl_memo)

        self.edit_memo = QPlainTextEdit()
        self.edit_memo.setPlainText(initial_memo_text)
        self.edit_memo.setPlaceholderText(
            "추진 배경\n"
            "□ 핵심 추진 배경 및 목적 입력\n"
            "ㅇ 세부 현황 및 검토 사항 입력\n"
            "- 보충 설명 및 주요 수치 입력\n\n"
            "향후 계획\n"
            "| 구분 | 주요 내용 | 일정 |\n"
            "| 1단계 | 기본계획 수립 | 10월 |\n"
            "| 2단계 | 시행 및 점검 | 11월 |"
        )
        right_lay.addWidget(self.edit_memo, 1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # 하단 액션 버튼 바
        bottom_lay = QHBoxLayout()
        bottom_lay.setSpacing(8)
        bottom_lay.addStretch(1)

        self.btn_build_local = QPushButton("메모 원문으로 생성")
        self.btn_build_local.setFixedHeight(btn_h)
        self.btn_build_local.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_build_local.setStyleSheet(self._sub_btn_style(accent_border=True))
        self.btn_build_local.clicked.connect(self._on_generate_local)
        bottom_lay.addWidget(self.btn_build_local)

        self.btn_build_ai = QPushButton("AI 보고서 구성 생성")
        self.btn_build_ai.setFixedHeight(btn_h)
        self.btn_build_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_build_ai.setStyleSheet(self._primary_btn_style())
        self.btn_build_ai.clicked.connect(self._on_generate_ai)
        bottom_lay.addWidget(self.btn_build_ai)

        self.btn_close = QPushButton("닫기")
        self.btn_close.setFixedHeight(btn_h)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet(self._sub_btn_style())
        self.btn_close.clicked.connect(self.reject)
        bottom_lay.addWidget(self.btn_close)

        root.addLayout(bottom_lay)

    def _sub_btn_style(self, accent_border: bool = False) -> str:
        p = self._palette
        panel = p.get("panel", "#FFFFFF")
        panel_alt = p.get("panel_alt", "#F1F5F9")
        text = p.get("text", "#1E293B")
        line = p.get("accent", "#2563EB") if accent_border else p.get("line", "#CBD5E1")
        fg = p.get("accent", "#2563EB") if accent_border else text
        ff_css = font_family_css()
        fs = scale_px(12)
        return f"""
            QPushButton {{
                background-color: {panel};
                color: {fg};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0px 14px;
                font-family: {ff_css};
                font-size: {fs}px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
            QPushButton:disabled {{
                background-color: #F8FAFC;
                color: #94A3B8;
                border-color: #CBD5E1;
            }}
        """

    def _primary_btn_style(self) -> str:
        p = self._palette
        accent = p.get("accent", "#2563EB")
        ff_css = font_family_css()
        fs = scale_px(12)
        return f"""
            QPushButton {{
                background-color: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0px 16px;
                font-family: {ff_css};
                font-size: {fs}px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                opacity: 0.92;
            }}
            QPushButton:disabled {{
                background-color: #94A3B8;
                color: #F8FAFC;
            }}
        """

    def _load_saved_memos(self) -> None:
        self._all_memos: list[Any] = []
        if self._repository and hasattr(self._repository, "list_memos"):
            try:
                self._all_memos = list(self._repository.list_memos() or [])
            except Exception as e:
                logger.warning("Failed to load memos: %s", e)
        self._render_memo_list("")

    def _render_memo_list(self, query: str) -> None:
        self.memo_list.clear()
        q = query.strip().lower()
        for m in self._all_memos:
            t = str(getattr(m, "title", "") or "").strip() or "제목 없음"
            desc = str(getattr(m, "description", "") or "").strip()
            if q and (q not in t.lower() and q not in desc.lower()):
                continue
            item = QListWidgetItem(t)
            item.setData(Qt.ItemDataRole.UserRole, {"title": t, "description": desc})
            if desc:
                preview = desc.splitlines()[0][:60]
                item.setToolTip(preview)
            self.memo_list.addItem(item)

    def _filter_memo_list(self, text: str) -> None:
        self._render_memo_list(text)

    def _on_memo_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        t = str(data.get("title", "") or "").strip()
        desc = str(data.get("description", "") or "").strip()
        if t:
            self.edit_title.setText(t)
        self.edit_memo.setPlainText(desc or t)

    def _ensure_laws_if_checked(self) -> list[dict[str, Any]]:
        return []

    def _collect_options(self) -> tuple[str, str, str, str, str, str, str, str]:
        title = self.edit_title.text().strip() or "업무 보고서"
        memo_text = self.edit_memo.toPlainText().strip()
        doc_tuple = self.combo_doc_type.currentData() or ("report", "standard", "standard")
        doc_type, style, report_kind = doc_tuple
        ministry = str(self.combo_ministry.currentData() or "공통")
        dept = "담당부서"
        date_str = date.today().strftime("%Y. %m. %d.")
        category = self.selected_category or self._default_category or "1. 일반 업무"
        return title, memo_text, doc_type, style, report_kind, ministry, dept, date_str

    def _finalize_generation(self, structure: dict[str, Any], save_only: bool = False) -> None:
        title, _, doc_type, style, report_kind, ministry, _, _ = self._collect_options()
        try:
            hwpx_bytes, plain_text, warnings, final_struct = generate_hwpx_from_structure(
                structure,
                doc_type=doc_type,
                ministry=ministry,
                style=style,
                report_kind=report_kind,
            )
        except Exception as e:
            logger.exception("Failed to build HWPX document: %s", e)
            QMessageBox.warning(self, "문서 생성 오류", f"HWPX 문서 생성 중 오류가 발생했습니다.\n{e}")
            return

        self.generated_hwpx_bytes = hwpx_bytes
        self.generated_plain_text = plain_text
        self.generated_title = str(final_struct.get("title") or title).strip()
        self.selected_category = self.selected_category or self._default_category or "1. 일반 업무"

        if save_only:
            default_name = re.sub(r'[\\/:*?"<>|]+', "_", self.generated_title) + ".hwpx"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "HWPX 문서 저장",
                default_name,
                "한글 문서 (*.hwpx)",
            )
            if file_path:
                Path(file_path).write_bytes(hwpx_bytes)
                self.status_lbl.setText(f"• 파일 저장 완료: {Path(file_path).name}")
            return

        if callable(self._on_document_created):
            try:
                self._on_document_created(
                    self.generated_title,
                    self.selected_category,
                    hwpx_bytes,
                    plain_text,
                )
            except Exception as e:
                logger.exception("Error in on_document_created callback: %s", e)
        self.accept()

    def _on_save_hwpx_file(self) -> None:
        title, memo_text, doc_type, _, _, _, dept, date_str = self._collect_options()
        if not title and not memo_text:
            self.status_lbl.setText("• 제목이나 메모 내용을 입력하세요")
            return
        laws = self._ensure_laws_if_checked()
        structure = memo_to_structure_local(
            title,
            memo_text,
            doc_type=doc_type,
            dept=dept,
            date_str=date_str,
            law_items=laws,
        )
        self._finalize_generation(structure, save_only=True)

    def _on_generate_local(self) -> None:
        title, memo_text, doc_type, _, _, _, dept, date_str = self._collect_options()
        if not title and not memo_text:
            self.status_lbl.setText("• 제목이나 메모 내용을 입력하세요")
            return
        self.btn_build_local.setEnabled(False)
        self.btn_build_ai.setEnabled(False)
        self.btn_build_local.setText("보고서 생성 중...")
        self.status_lbl.setText("• 메모 원문 기반으로 HWPX 보고서를 생성하고 있습니다...")
        QApplication.processEvents()
        try:
            laws = self._ensure_laws_if_checked()
            structure = memo_to_structure_local(
                title,
                memo_text,
                doc_type=doc_type,
                dept=dept,
                date_str=date_str,
                law_items=laws,
            )
            self._finalize_generation(structure, save_only=False)
        finally:
            self.btn_build_local.setEnabled(True)
            self.btn_build_ai.setEnabled(True)
            self.btn_build_local.setText("메모 원문으로 생성")

    def _on_generate_ai(self) -> None:
        title, memo_text, doc_type, style, report_kind, ministry, dept, date_str = self._collect_options()
        if not title and not memo_text:
            self.status_lbl.setText("• 제목이나 메모 내용을 입력하세요")
            return

        try:
            from taskcalendar.ai_assistant import AIChatWorker, AIConfigManager
            from taskcalendar.work_smart_tools import get_ai_connection_status

            status = get_ai_connection_status()
            cfg = {**AIConfigManager.load(force_reload=True), "stream": False}
        except Exception:
            status = {"connected": False}
            cfg = {}

        if not status.get("connected"):
            reply = QMessageBox.question(
                self,
                "AI 미연결 안내",
                "현재 연결된 AI가 없습니다.\n메모 원문 변환 방식으로 즉시 HWPX 문서를 생성할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_generate_local()
            return

        laws = self._ensure_laws_if_checked()
        messages = build_ai_memo_prompt(
            title,
            memo_text,
            doc_type=doc_type,
            style=style,
            report_kind=report_kind,
            ministry=ministry,
            dept=dept,
            date_str=date_str,
            law_items=laws,
        )

        self.btn_build_ai.setEnabled(False)
        self.btn_build_local.setEnabled(False)
        self.btn_build_ai.setText("AI 보고서 생성 중...")
        self.status_lbl.setText("• 연결된 AI가 보고서(HWPX)를 구성하고 있습니다 (약 5~10초 소요)...")
        QApplication.processEvents()

        # 화면 중앙 진행 상태 팝업 다이얼로그 표시
        self._progress_dlg = _AIProgressDialog(self, title="AI 보고서 작성", palette=self._palette)

        def _on_cancel_ai():
            if self._ai_worker and self._ai_worker.isRunning():
                try:
                    self._ai_worker.stop()
                except Exception:
                    pass
            self.btn_build_ai.setEnabled(True)
            self.btn_build_local.setEnabled(True)
            self.btn_build_ai.setText("AI 보고서 구성 생성")
            self.status_lbl.setText("• AI 보고서 작성이 취소되었습니다.")

        self._progress_dlg.btn_cancel.clicked.connect(_on_cancel_ai)
        self._progress_dlg.show()
        QApplication.processEvents()

        self._ai_worker = AIChatWorker(cfg, messages, self)
        self._ai_worker.finished_ok.connect(
            lambda resp: self._on_ai_finished(resp, title, memo_text, doc_type, dept, date_str, laws)
        )
        self._ai_worker.error_occurred.connect(
            lambda err: self._on_ai_error(err, title, memo_text, doc_type, dept, date_str, laws)
        )
        self._ai_worker.start()

    def _on_ai_finished(
        self,
        response_text: str,
        title: str,
        memo_text: str,
        doc_type: str,
        dept: str,
        date_str: str,
        laws: list[dict[str, Any]],
    ) -> None:
        if hasattr(self, "_progress_dlg") and self._progress_dlg:
            if self._progress_dlg.is_cancelled():
                return
            self._progress_dlg.close()

        self.btn_build_ai.setEnabled(True)
        self.btn_build_local.setEnabled(True)
        self.btn_build_ai.setText("AI 보고서 구성 생성")
        self.status_lbl.setText("• AI 보고서 구조화 완료! HWPX 문서를 생성합니다...")
        QApplication.processEvents()
        structure = parse_ai_structure_json(
            response_text,
            title,
            memo_text,
            doc_type=doc_type,
            dept=dept,
            date_str=date_str,
            law_items=laws,
        )
        self._finalize_generation(structure, save_only=False)

    def _on_ai_error(
        self,
        err_msg: str,
        title: str,
        memo_text: str,
        doc_type: str,
        dept: str,
        date_str: str,
        laws: list[dict[str, Any]],
    ) -> None:
        if hasattr(self, "_progress_dlg") and self._progress_dlg:
            if self._progress_dlg.is_cancelled():
                return
            self._progress_dlg.close()

        self.btn_build_ai.setEnabled(True)
        self.btn_build_local.setEnabled(True)
        self.btn_build_ai.setText("AI 보고서 구성 생성")
        logger.warning("AI report structuring failed (%s), using local parser.", err_msg)
        self.status_lbl.setText("• AI 응답 지연/오류로 로컬 엔진을 통해 보고서를 생성합니다...")
        QApplication.processEvents()
        structure = memo_to_structure_local(
            title,
            memo_text,
            doc_type=doc_type,
            dept=dept,
            date_str=date_str,
            law_items=laws,
        )
        self._finalize_generation(structure, save_only=False)
