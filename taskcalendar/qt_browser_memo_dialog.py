"""
브라우저 페이지 메모 관리 & 단독 공유 (내보내기/가져오기) 다이얼로그.

work/browser/page_memos.json에 보관된 브라우저 확장 프로그램 플로팅 메모를 관리하고,
외부 파일(.json)로 내보내어 타인과 공유하거나 다른 PC로 이전/가져오기(병합)하는 기능을 제공합니다.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from taskcalendar import work_files
from taskcalendar.fonts import font_family_css, make_ui_font_like, scale_px
from taskcalendar.qt_styles import _shade, resolve_palette

logger = logging.getLogger(__name__)


class BrowserMemoDetailDialog(QDialog):
    """개별 브라우저 메모 상세 확인 및 수정 다이얼로그"""

    def __init__(self, parent=None, memo_data: dict | None = None, palette: dict | None = None):
        super().__init__(parent)
        self.memo = dict(memo_data or {})
        self.palette = palette or {}
        self.setWindowTitle("브라우저 메모 상세 / 편집")
        self.resize(scale_px(540), scale_px(420))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._init_ui()

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        line = self.palette.get("line", "#CBD5E1")
        text = self.palette.get("text", "#0F172A")
        accent = self.palette.get("accent", "#0284C7")
        ff_css = font_family_css()

        self.setStyleSheet(f"""
            QDialog {{ background-color: {bg}; color: {text}; font-family: {ff_css}; }}
            QLabel {{ color: {text}; font-family: {ff_css}; font-size: {scale_px(12)}px; }}
            QLineEdit, QTextEdit {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                padding: 6px;
                font-family: {ff_css};
                font-size: {scale_px(12)}px;
            }}
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                padding: 6px 14px;
                font-size: {scale_px(12)}px;
                font-weight: 600;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # URL
        layout.addWidget(QLabel("<b>원문 웹페이지 URL:</b>"))
        self.url_input = QLineEdit(self.memo.get("url", ""))
        layout.addWidget(self.url_input)

        # 제목
        layout.addWidget(QLabel("<b>메모 제목 (선택):</b>"))
        self.title_input = QLineEdit(self.memo.get("title", ""))
        layout.addWidget(self.title_input)

        # 본문
        layout.addWidget(QLabel("<b>메모 본문:</b>"))
        self.content_edit = QTextEdit()
        self.content_edit.setPlainText(self.memo.get("content", ""))
        layout.addWidget(self.content_edit, 1)

        # 하단 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = QPushButton("저장")
        btn_save.setStyleSheet(f"background-color: {accent}; color: #FFFFFF; border: none;")
        btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(btn_save)

        layout.addLayout(btn_layout)

    def _on_save(self) -> None:
        self.memo["url"] = self.url_input.text().strip()
        self.memo["title"] = self.title_input.text().strip()
        self.memo["content"] = self.content_edit.toPlainText().strip()
        self.memo["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.accept()

    def get_memo(self) -> dict:
        return self.memo


class BrowserMemoManagerDialog(QDialog):
    """
    브라우저 페이지 메모 관리 & 단독 공유 (내보내기 / 가져오기) 다이얼로그
    """

    def __init__(self, parent=None, palette: dict | None = None):
        super().__init__(parent)
        self.palette = resolve_palette(palette or parent)
        self.memos: list[dict] = []
        self._filtered_memos: list[dict] = []

        self.setWindowTitle("브라우저 페이지 메모 관리")
        self.resize(scale_px(860), scale_px(580))
        self.setMinimumSize(scale_px(680), scale_px(460))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._init_ui()
        self.reload_memos()

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#f4f7fb")
        panel = self.palette.get("panel", "#ffffff")
        panel_alt = self.palette.get("panel_alt", "#f8fafc")
        line = self.palette.get("line", "#dbe3ec")
        text = self.palette.get("text", "#1f2328")
        muted = self.palette.get("muted", "#667085")
        accent = self.palette.get("accent", "#1f7a67")
        accent_soft = self.palette.get("accent_soft", "#e7f4f0")
        accent_hover = _shade(accent, -0.12)
        ff_css = font_family_css()

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: {ff_css};
                font-size: {scale_px(13)}px;
            }}
            QLabel {{
                color: {text};
                font-family: {ff_css};
            }}
            QLineEdit {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                color: {text};
                padding: 4px 10px;
                font-family: {ff_css};
                font-size: {scale_px(13)}px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: {accent};
            }}
            QTableWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                gridline-color: {line};
                color: {text};
                font-family: {ff_css};
                font-size: {scale_px(12)}px;
                selection-background-color: {accent_soft};
                selection-color: {text};
            }}
            QHeaderView::section {{
                background-color: {panel_alt};
                color: {muted};
                border: none;
                border-bottom: 1px solid {line};
                border-right: 1px solid {line};
                padding: 6px;
                font-weight: bold;
                font-size: {scale_px(11)}px;
            }}
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                color: {text};
                font-family: {ff_css};
                font-size: {scale_px(13)}px;
                padding: 5px 14px;
                min-height: {scale_px(30)}px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
                border-color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {accent_soft};
            }}
            QPushButton:focus {{
                outline: none;
            }}
            QPushButton#primary {{
                background-color: {accent};
                color: #FFFFFF;
                border: 1px solid {accent};
                font-weight: 700;
            }}
            QPushButton#primary:hover {{
                background-color: {accent_hover};
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 상단 안내 영역 (테두리 없는 수평 레이아웃)
        h_layout = QHBoxLayout()
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(12)

        lbl_desc = QLabel(
            "<b>브라우저 페이지 메모 관리</b><br>"
            "<span style='color: #64748B; font-size: 11px;'>"
            "웹 서핑 중 저장한 페이지 메모를 보관·관리하고, 전용 공유 파일(.json)로 내보내어 동료와 공유하거나 다른 PC로 이전할 수 있습니다."
            "</span>"
        )
        h_layout.addWidget(lbl_desc, 1)

        btn_open_folder = QPushButton("저장 폴더 열기")
        btn_open_folder.setFixedHeight(scale_px(32))
        btn_open_folder.setToolTip("work/browser 폴더를 탐색기에서 엽니다.")
        btn_open_folder.clicked.connect(self._on_open_browser_dir)
        h_layout.addWidget(btn_open_folder)

        main_layout.addLayout(h_layout)

        # 검색 및 요약 바
        bar_layout = QHBoxLayout()
        bar_layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setFixedHeight(scale_px(32))
        self.search_input.setPlaceholderText("URL, 제목 또는 메모 내용 검색...")
        self.search_input.textChanged.connect(self._apply_filter)
        bar_layout.addWidget(self.search_input, 1)

        self.count_label = QLabel("총 0건의 메모")
        self.count_label.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: {scale_px(12)}px;")
        bar_layout.addWidget(self.count_label)

        btn_refresh = QPushButton("새로고침")
        btn_refresh.setFixedHeight(scale_px(32))
        btn_refresh.setFixedWidth(scale_px(75))
        btn_refresh.clicked.connect(self.reload_memos)
        bar_layout.addWidget(btn_refresh)

        main_layout.addLayout(bar_layout)

        # 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["사이트(URL)", "메모 제목", "메모 내용 요약", "색상", "작성일시"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(scale_px(28))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, scale_px(180))
        self.table.setColumnWidth(1, scale_px(140))
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        main_layout.addWidget(self.table, 1)

        # 하단 액션 버튼들 (적용/취소 버튼 스타일과 일치)
        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        btn_import = QPushButton("메모 가져오기")
        btn_import.setFixedHeight(scale_px(32))
        btn_import.setToolTip("공유 파일(.json)에서 메모를 가져옵니다.")
        btn_import.clicked.connect(self._on_import_memos)
        action_layout.addWidget(btn_import)

        btn_export = QPushButton("메모 내보내기")
        btn_export.setFixedHeight(scale_px(32))
        btn_export.setToolTip("메모를 공유 파일(.json)로 내보냅니다.")
        btn_export.clicked.connect(self._on_export_memos)
        action_layout.addWidget(btn_export)

        btn_delete = QPushButton("선택 삭제")
        btn_delete.setFixedHeight(scale_px(32))
        btn_delete.setToolTip("선택한 메모를 삭제합니다.")
        btn_delete.clicked.connect(self._on_delete_selected)
        action_layout.addWidget(btn_delete)

        action_layout.addStretch(1)

        btn_close = QPushButton("닫기")
        btn_close.setFixedHeight(scale_px(32))
        btn_close.setMinimumWidth(scale_px(80))
        btn_close.clicked.connect(self.accept)
        action_layout.addWidget(btn_close)

        main_layout.addLayout(action_layout)

    def reload_memos(self) -> None:
        """work/browser/page_memos.json에서 메모를 다시 읽어온다."""
        self.memos = work_files.load_browser_memos()
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self.search_input.text().strip().lower()
        if not query:
            self._filtered_memos = list(self.memos)
        else:
            self._filtered_memos = [
                m for m in self.memos
                if query in str(m.get("url", "")).lower()
                or query in str(m.get("title", "")).lower()
                or query in str(m.get("content", "")).lower()
            ]

        self.count_label.setText(f"총 {len(self._filtered_memos)}건 (전체 {len(self.memos)}건)")
        self._populate_table()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._filtered_memos))
        for row, m in enumerate(self._filtered_memos):
            url = str(m.get("url") or "")
            title = str(m.get("title") or "")
            content = str(m.get("content") or "").replace("\n", " ")
            color = str(m.get("color") or "yellow")
            created_at = str(m.get("created_at") or m.get("date") or "")

            item_url = QTableWidgetItem(url)
            item_url.setToolTip(url)
            item_title = QTableWidgetItem(title or "(제목 없음)")
            item_title.setToolTip(title)
            item_content = QTableWidgetItem(content)
            item_content.setToolTip(str(m.get("content") or ""))

            item_color = QTableWidgetItem(f"● {color}")
            item_created = QTableWidgetItem(created_at)

            self.table.setItem(row, 0, item_url)
            self.table.setItem(row, 1, item_title)
            self.table.setItem(row, 2, item_content)
            self.table.setItem(row, 3, item_color)
            self.table.setItem(row, 4, item_created)

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        if row < 0 or row >= len(self._filtered_memos):
            return
        m = self._filtered_memos[row]
        dlg = BrowserMemoDetailDialog(self, memo_data=m, palette=self.palette)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_memo()
            # 원본 목록에서 교체
            for i, orig in enumerate(self.memos):
                if orig.get("id") == m.get("id"):
                    self.memos[i] = updated
                    break
            work_files.save_browser_memos(self.memos)
            self._apply_filter()

    def _on_open_browser_dir(self) -> None:
        try:
            p = work_files.browser_root()
            os.startfile(str(p))
        except Exception as e:
            QMessageBox.warning(self, "폴더 열기 오류", f"폴더를 열 수 없습니다:\n{e}")

    def _on_export_memos(self) -> None:
        """브라우저 메모 단독 파일(.json) 내보내기"""
        if not self.memos:
            QMessageBox.information(self, "알림", "내보낼 브라우저 메모가 없습니다.")
            return

        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        default_name = f"바로업무_브라우저메모_{now_str}.json"
        target_path, _ = QFileDialog.getSaveFileName(
            self,
            "브라우저 메모 내보내기 (공유용)",
            str(Path.home() / "Documents" / default_name),
            "JSON 파일 (*.json)",
        )
        if not target_path:
            return

        # 필터링 중이면 필터된 것만 내보낼지 여부 확인
        export_targets = self.memos
        if len(self._filtered_memos) < len(self.memos):
            res = QMessageBox.question(
                self,
                "내보내기 범위 선택",
                f"현재 검색 필터가 적용되어 있습니다.\n\n"
                f"• '예': 현재 검색/선택된 {len(self._filtered_memos)}건만 내보내기\n"
                f"• '아니오': 전체 {len(self.memos)}건 모두 내보내기",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Cancel:
                return
            if res == QMessageBox.StandardButton.Yes:
                export_targets = self._filtered_memos

        ok = work_files.export_browser_memos_to_file(target_path, export_targets)
        if ok:
            QMessageBox.information(
                self,
                "내보내기 완료",
                f"총 {len(export_targets)}건의 브라우저 메모가 성공적으로 내보내졌습니다.\n\n저장 위치:\n{target_path}\n\n"
                "• 크롬 브라우저로 다시 옮기는 방법:\n"
                "  Chrome 우측 상단 확장프로그램 아이콘 클릭 → [메모 관리자] → [가져오기]에서 이 .json 파일을 선택하세요.\n"
                "  (해당 웹페이지 방문 시 원래 위치에 메모가 그대로 복원됩니다)",
            )
        else:
            QMessageBox.critical(self, "내보내기 실패", "파일을 저장하는 중 오류가 발생했습니다.")

    def _on_import_memos(self) -> None:
        """외부 .json 파일에서 메모 가져오기 (병합)"""
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "브라우저 메모 가져오기",
            str(Path.home() / "Documents"),
            "JSON 파일 (*.json);;모든 파일 (*.*)",
        )
        if not source_path:
            return

        # 병합 방식 선택
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("가져오기 방식 선택")
        msg_box.setText("메모를 현재 저장소에 어떻게 반영하시겠습니까?")
        btn_merge = msg_box.addButton("스마트 병합 (기존 유지+새 항목 추가)", QMessageBox.ButtonRole.AcceptRole)
        btn_overwrite = msg_box.addButton("덮어쓰기 (중복 시 새 내용으로 갱신)", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = msg_box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        msg_box.exec()

        if msg_box.clickedButton() == btn_cancel:
            return

        overwrite = (msg_box.clickedButton() == btn_overwrite)
        added, updated = work_files.import_browser_memos_from_file(source_path, overwrite=overwrite)
        self.reload_memos()

        QMessageBox.information(
            self,
            "가져오기 완료",
            f"메모 가져오기가 완료되었습니다.\n\n"
            f"• 새로 추가된 메모: {added}건\n"
            f"• 갱신된 메모: {updated}건\n"
            f"• 현재 보관 총 메모: {len(self.memos)}건\n\n"
            "※ 웹 브라우저(Chrome)에도 반영하려면 확장 프로그램의 메모 관리자에서 [가져오기]를 실행하세요.",
        )

    def _on_delete_selected(self) -> None:
        """선택한 메모 삭제"""
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        if not selected_rows:
            QMessageBox.information(self, "알림", "삭제할 메모를 테이블에서 선택해주세요.")
            return

        count = len(selected_rows)
        if QMessageBox.question(
            self,
            "삭제 확인",
            f"선택한 {count}건의 브라우저 메모를 삭제하시겠습니까?\n이 작업은 work/browser/page_memos.json에 즉시 반영됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        targets_to_remove = [self._filtered_memos[r] for r in selected_rows]
        remove_ids = set(m.get("id") for m in targets_to_remove if m.get("id"))

        self.memos = [m for m in self.memos if m.get("id") not in remove_ids]
        work_files.save_browser_memos(self.memos)
        self.reload_memos()

    def _show_extension_guide(self) -> None:
        """확장 프로그램 연동 도움말"""
        QMessageBox.information(
            self,
            "💡 크롬 확장프로그램 연동 안내",
            "1. 크롬 확장프로그램 '바로업무 연동 도우미'에서 웹페이지에 메모를 작성합니다.\n\n"
            "2. 메모 관리자 창(chrome_extension) 우측 상단의 '💼 바로업무(work/browser)로 백업' 버튼을 클릭하면 "
            "이곳 바로업무(work/browser/page_memos.json)로 자동 백업됩니다.\n\n"
            "3. 업무 내보내기/가져오기 시에도 브라우저 메모 옵션을 체크하면 업무 데이터와 함께 완벽하게 공유 및 이전됩니다.",
        )
