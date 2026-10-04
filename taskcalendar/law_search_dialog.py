"""국가법령정보센터(law.go.kr) 법령 검색 대화상자.

웹 에디터 우클릭 메뉴의 「법령 찾기」 에서 호출된다.
검색 결과를 목록으로 보여주고, 선택하면 기본 브라우저로 공식 페이지를 연다.
"""
from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.law_reference import (
    LawSearchResult,
    build_home_url,
    build_search_page_url,
    search_laws,
)

logger = logging.getLogger(__name__)


class _LawSearchWorker(QThread):
    """법령 검색을 백그라운드에서 수행 (UI 멈춤 방지)"""

    done = Signal(list, str)   # (결과 리스트, 검색어)

    def __init__(self, query: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.query = query

    def run(self) -> None:
        try:
            results = search_laws(self.query, limit=30)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            logger.warning("법령 검색 실패: %s", exc)
            results = []
        self.done.emit(results, self.query)


class LawSearchDialog(QDialog):
    """「법령 찾기」 검색창 + 결과 목록"""

    def __init__(self, parent: QWidget | None = None, palette: dict[str, str] | None = None,
                 initial_query: str = "") -> None:
        super().__init__(parent)
        self.palette = palette or {}
        self._results: list[LawSearchResult] = []
        self._shown: list[LawSearchResult] = []
        self._last_query: str = ""
        self._worker: _LawSearchWorker | None = None
        self._opened_url: str = ""

        self.setWindowTitle("법령 찾기 — 국가법령정보센터")
        self.resize(720, 560)
        self.setMinimumSize(560, 420)

        self._init_ui(initial_query)

    # ------------------------------------------------------------------ UI
    def _init_ui(self, initial_query: str) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        bg = self.palette.get("bg", "#F8FAFC")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")
        button_text = self.palette.get("button_text", "#FFFFFF")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 설명
        lbl_desc = QLabel(
            "국가법령정보센터(law.go.kr)에서 법령을 검색합니다. "
            "결과를 선택하면 공식 페이지가 기본 브라우저에서 열립니다.",
            self,
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"color: {muted}; font-size: 11px;")
        layout.addWidget(lbl_desc)

        # 검색 입력
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.edit_query = QLineEdit(self)
        self.edit_query.setPlaceholderText("법령명 검색 (예: 개인정보 보호법, 정보통신망법)")
        self.edit_query.setText(initial_query)
        self.edit_query.setStyleSheet(
            f"background:{panel}; color:{text}; border:1px solid {line};"
            f"border-radius:4px; padding:7px 10px; font-size:13px;"
        )
        self.edit_query.returnPressed.connect(self._on_search)
        search_row.addWidget(self.edit_query, 1)

        self.btn_search = QPushButton("검색", self)
        self.btn_search.setStyleSheet(
            f"background:{accent}; color:{button_text}; font-weight:600;"
            "padding:7px 18px; border-radius:4px; border:none;"
        )
        self.btn_search.clicked.connect(self._on_search)
        search_row.addWidget(self.btn_search)

        layout.addLayout(search_row)

        # 연혁 법령 표시 옵션 및 상태 안내 행
        from PySide6.QtWidgets import QCheckBox, QFrame  # noqa: PLC0415

        status_row = QHBoxLayout()
        status_row.setSpacing(12)

        self.chk_current_only = QCheckBox("현행 법령만 보기 (권장)", self)
        self.chk_current_only.setChecked(True)
        self.chk_current_only.setToolTip(
            "체크하면 연혁(과거 구법령)을 숨기고 최신 현행 법령만 정돈하여 표시합니다."
        )
        self.chk_current_only.setStyleSheet(f"color: {text}; font-size: 11px; font-weight: 500;")
        self.chk_current_only.stateChanged.connect(self._on_toggle_current_only)
        status_row.addWidget(self.chk_current_only)

        # 상태 라벨: '현행 법령만 보기 (권장)' 오른쪽에 배치
        self.lbl_status = QLabel("", self)
        self.lbl_status.setStyleSheet(f"color: {muted}; font-size: 11px;")
        status_row.addWidget(self.lbl_status, 1)

        layout.addLayout(status_row)

        # 결과 목록 (카드형 리스트)
        self.list_result = QListWidget(self)
        self.list_result.setStyleSheet(f"""
            QListWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
                padding: 6px;
                outline: none;
            }}
            QListWidget::item {{
                background-color: transparent;
                border: none;
                padding: 0px;
                margin-bottom: 4px;
            }}
            QListWidget::item:selected {{
                background-color: transparent;
            }}
        """)
        self.list_result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_result.itemDoubleClicked.connect(lambda _: self._on_open())
        self.list_result.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.list_result, 1)

        # 하단 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_open_site = QPushButton("🌐 법령정보센터 통합검색", self)
        self.btn_open_site.setStyleSheet(
            f"background:{panel}; color:{muted}; border:1px solid {line};"
            "padding:7px 14px; border-radius:6px; font-size:12px; font-weight:500;"
        )
        self.btn_open_site.clicked.connect(self._on_open_site)
        btn_row.addWidget(self.btn_open_site)

        btn_row.addStretch(1)

        self.btn_open = QPushButton("선택한 법령 열기 (Enter)", self)
        self.btn_open.setEnabled(False)
        self.btn_open.setStyleSheet(
            f"background:{accent}; color:{button_text}; font-weight:600;"
            "padding:7px 18px; border-radius:6px; border:none; font-size:12px;"
        )
        self.btn_open.clicked.connect(self._on_open)
        btn_row.addWidget(self.btn_open)

        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setStyleSheet(
            f"background:{panel}; color:{text}; border:1px solid {line};"
            "padding:7px 14px; border-radius:6px; font-size:12px;"
        )
        self.btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

        if initial_query.strip():
            self._on_search()

    # ------------------------------------------------------------- 동작
    @Slot()
    def _on_search(self) -> None:
        query = self.edit_query.text().strip()
        if not query:
            self.lbl_status.setText("검색어를 입력해 주세요.")
            return

        if self._worker is not None and self._worker.isRunning():
            return

        self.btn_search.setEnabled(False)
        self.btn_open.setEnabled(False)
        self.list_result.clear()
        self._results = []
        self.lbl_status.setText(f"‘{query}’ 최신 법령 검색 중...")

        self._worker = _LawSearchWorker(query, self)
        self._worker.done.connect(self._on_results)
        self._worker.start()

    @Slot(list, str)
    def _on_results(self, results: list, query: str) -> None:
        self.btn_search.setEnabled(True)
        self._results = results
        self._last_query = query
        self._render_results()

    @Slot()
    def _on_toggle_current_only(self) -> None:
        """「현행 법령만 보기」 체크 상태가 바뀌면 목록을 다시 그린다."""
        self._render_results()

    def _render_results(self) -> None:
        """현재 결과와 필터 상태에 맞춰 목록을 깔끔한 카드형으로 채운다."""
        from PySide6.QtWidgets import QFrame  # noqa: PLC0415
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")

        self.list_result.clear()
        self._shown = []

        results = self._results
        if not results:
            self.lbl_status.setText(
                f"‘{self._last_query}’ 검색 결과가 없습니다. "
                "검색 페이지에서 직접 확인해 보세요."
            )
            return

        if self.chk_current_only.isChecked():
            results = [r for r in results if r.is_current]
            if not results:
                self.lbl_status.setText(
                    "현행 법령이 없습니다. '현행 법령만 보기' 체크를 해제하면 연혁/구법령을 확인할 수 있습니다."
                )
                return

        current = sum(1 for r in results if r.is_current)
        if current and current < len(results):
            self.lbl_status.setText(
                f"‘{self._last_query}’ 검색 결과 {len(results)}건 (현행 {current}건) "
                "— 항목을 더블클릭하거나 선택 후 [선택한 법령 열기]를 누르세요."
            )
        elif current:
            self.lbl_status.setText(
                f"‘{self._last_query}’ 현행 법령 {len(results)}건 "
                "— 항목을 더블클릭하거나 선택 후 [선택한 법령 열기]를 누르세요."
            )
        else:
            self.lbl_status.setText(
                f"‘{self._last_query}’ 검색 결과 {len(results)}건 (연혁/구법령)"
            )

        for r in results:
            card = QFrame()
            card.setObjectName("lawCard")
            border_col = "#E2E8F0"
            bg_col = "#FFFFFF"
            card.setStyleSheet(f"""
                QFrame#lawCard {{
                    background-color: {bg_col};
                    border: 1px solid {border_col};
                    border-radius: 8px;
                }}
                QFrame#lawCard:hover {{
                    background-color: #F8FAFC;
                    border: 1px solid #94A3B8;
                }}
            """)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 9, 14, 9)
            cl.setSpacing(5)

            # 상단 행: [현행/연혁 배지] [법령명] [법령구분] ... [시행일자]
            top = QHBoxLayout()
            top.setSpacing(8)

            badge = QLabel("현행" if r.is_current else "연혁")
            if r.is_current:
                badge.setStyleSheet("background-color: #ECFDF5; color: #047857; border: 1px solid #A7F3D0; border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: bold;")
            else:
                badge.setStyleSheet("background-color: #F1F5F9; color: #64748B; border: 1px solid #CBD5E1; border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: 500;")
            top.addWidget(badge)

            title = QLabel(r.name)
            title_col = "#0F172A" if r.is_current else "#475569"
            title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {title_col};")
            top.addWidget(title)

            kind = QLabel(r.law_kind or "법령")
            kind.setStyleSheet("color: #64748B; font-size: 11px;")
            top.addWidget(kind)

            top.addStretch(1)

            enf = QLabel(f"시행 {r.enforce_date}" if r.enforce_date else "")
            enf_col = "#0284C7" if r.is_current else "#94A3B8"
            enf.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {enf_col};")
            top.addWidget(enf)
            cl.addLayout(top)

            # 하단 행: 소관부처 · 공포일자 · 제개정구분
            sub = QHBoxLayout()
            sub.setSpacing(8)
            detail_parts = []
            if r.office:
                detail_parts.append(f"소관: {r.office}")
            if r.promulgate_date:
                detail_parts.append(f"공포 {r.promulgate_date}")
            if r.revision:
                detail_parts.append(r.revision)

            sub_lbl = QLabel("  ·  ".join(detail_parts))
            sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
            sub.addWidget(sub_lbl)
            sub.addStretch(1)
            cl.addLayout(sub)

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setSizeHint(card.sizeHint())
            detail = r.detail_text()
            item.setToolTip(f"{r.display()}\n{detail}\n\n{r.url}" if detail else r.url)

            self.list_result.addItem(item)
            self.list_result.setItemWidget(item, card)
            self._shown.append(r)

        # 항목 선택 시 카드 하이라이트 동기화
        def _update_card_selection():
            cur_item = self.list_result.currentItem()
            for idx in range(self.list_result.count()):
                it = self.list_result.item(idx)
                c = self.list_result.itemWidget(it)
                if not c:
                    continue
                is_cur = (it is cur_item)
                if is_cur:
                    c.setStyleSheet(f"""
                        QFrame#lawCard {{
                            background-color: #F0F9FF;
                            border: 1.5px solid {accent};
                            border-radius: 8px;
                        }}
                    """)
                else:
                    c.setStyleSheet("""
                        QFrame#lawCard {{
                            background-color: #FFFFFF;
                            border: 1px solid #E2E8F0;
                            border-radius: 8px;
                        }}
                        QFrame#lawCard:hover {{
                            background-color: #F8FAFC;
                            border: 1px solid #94A3B8;
                        }}
                    """)

        self.list_result.itemSelectionChanged.connect(_update_card_selection)

        # 첫 번째 항목 자동 선택
        if self.list_result.count() > 0:
            self.list_result.setCurrentRow(0)
            _update_card_selection()

    @Slot()
    def _on_select(self) -> None:
        item = self.list_result.currentItem()
        if item is None:
            self.btn_open.setEnabled(False)
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        self.btn_open.setEnabled(bool(r))

    @Slot()
    def _on_open(self) -> None:
        item = self.list_result.currentItem()
        if item is None:
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        if r is None:
            return
        self._open_url(r.url)
        self.accept()

    @Slot()
    def _on_open_site(self) -> None:
        query = self.edit_query.text().strip()
        self._open_url(build_search_page_url(query) if query else build_home_url())

    def _open_url(self, url: str) -> None:
        if not url:
            return
        self._opened_url = url
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            try:
                webbrowser.open(url)
            except Exception as exc:  # pragma: no cover
                logger.warning("URL 열기 실패: %s", exc)
                QMessageBox.warning(self, "안내", f"브라우저를 열지 못했습니다.\n\n{url}")
                return

    @property
    def opened_url(self) -> str:
        """테스트/로그용: 마지막으로 열었던 URL"""
        return self._opened_url


def open_law_search(parent: QWidget | None = None, palette: dict[str, str] | None = None,
                    initial_query: str = "") -> None:
    """「법령 찾기」 대화상자를 띄운다."""
    dlg = LawSearchDialog(parent, palette, initial_query)
    dlg.exec()