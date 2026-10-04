"""문서 → 업무 연결 다이얼로그.

업무 관리의 WorkDocSelectDialog(업무 → 문서)와 대칭으로,
문서 등록/목록에서 연결할 업무를 골라 주는 창이다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css
from taskcalendar.storage import EncryptedRepository


class WorkLinkSelectDialog(QDialog):
    """현재 문서에 연결할 업무(work)를 검색하고 다중 선택하는 대화상자"""

    def __init__(
        self,
        parent: QWidget | None,
        repository: EncryptedRepository,
        initial_work_ids: list[int] | None = None,
        palette: dict[str, str] | None = None,
        title: str = "문서와 연결할 업무 선택",
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.palette = palette or {}
        self.selected_work_ids: list[int] = list(initial_work_ids or [])
        self._check_signal_connected = False

        self.setWindowTitle(title)
        self.resize(520, 560)
        self.setMinimumSize(420, 420)

        bg = self.palette.get("bg", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        self.setStyleSheet(
            f"QDialog {{ background-color: {bg}; color: {text}; font-family: {font_family_css()}; }}"
        )

        self._init_ui()
        self._populate_work_list()

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  업무명, 분류, 담당자 검색...")
        self.search_input.setFixedHeight(30)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
        """)
        self.search_input.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_input)

        self.work_list = QListWidget()
        self.work_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px;
                font-size: 12px;
                outline: none;
            }}
            QListWidget::item {{
                min-height: 30px;
                padding: 2px 6px;
                border-radius: 4px;
            }}
            QListWidget::item:hover {{
                background-color: {panel_alt};
            }}
            QListWidget::item:selected {{
                background-color: {panel_alt};
                color: {text};
            }}
        """)
        layout.addWidget(self.work_list, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {muted}; font-size: 11px;")
        layout.addWidget(self.status_label)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        btn_all = QPushButton("전체 선택")
        btn_all.setFixedHeight(28)
        btn_all.setStyleSheet(
            f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; "
            "border-radius: 6px; padding: 0 10px; font-size: 11px;"
        )
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        bottom_row.addWidget(btn_all)

        btn_none = QPushButton("선택 해제")
        btn_none.setFixedHeight(28)
        btn_none.setStyleSheet(
            f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; "
            "border-radius: 6px; padding: 0 10px; font-size: 11px;"
        )
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        bottom_row.addWidget(btn_none)

        bottom_row.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(28)
        btn_cancel.setStyleSheet(
            f"background-color: {panel}; color: {text}; border: 1px solid {line}; "
            "border-radius: 6px; padding: 0 14px; font-size: 12px;"
        )
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)

        btn_ok = QPushButton("연결 확인")
        btn_ok.setFixedHeight(28)
        btn_ok.setStyleSheet(
            f"background-color: {accent}; color: #FFFFFF; border: 1px solid {accent}; "
            "border-radius: 6px; padding: 0 16px; font-size: 12px; font-weight: bold;"
        )
        btn_ok.clicked.connect(self._on_accept)
        bottom_row.addWidget(btn_ok)

        layout.addLayout(bottom_row)

    # ------------------------------------------------------------ 데이터
    def _populate_work_list(self) -> None:
        """업무 목록(분류 트리 구조)을 체크박스 항목으로 채운다."""
        self.work_list.blockSignals(True)
        self.work_list.clear()

        try:
            categories = self.repository.list_work_categories()
            items = self.repository.list_work_items()
        except Exception:
            categories = []
            items = []

        cat_name = {c["id"]: c["name"] for c in categories}

        by_cat: dict[int | None, list] = {}
        for it in items:
            by_cat.setdefault(it.get("category_id"), []).append(it)

        checked = set(self.selected_work_ids)

        def add_cat(cat_id: int, depth: int) -> None:
            name = cat_name.get(cat_id, "")
            prefix = "    " * depth + ("📁 " if depth == 0 else "└ ")
            item = QListWidgetItem(prefix + name)
            item.setData(Qt.ItemDataRole.UserRole, ("cat", cat_id))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if cat_id in checked else Qt.CheckState.Unchecked
            )
            font = item.font()
            font.setBold(depth == 0)
            item.setFont(font)
            self.work_list.addItem(item)

            for it in by_cat.get(cat_id, []):
                wid = it["id"]
                label = "    " * (depth + 1) + f"📄 {it['title']}"
                meta = []
                if it.get("cycle"):
                    meta.append(str(it["cycle"]))
                if it.get("assignee"):
                    meta.append(str(it["assignee"]))
                if meta:
                    label += f"  ({' · '.join(meta)})"
                witem = QListWidgetItem(label)
                witem.setData(Qt.ItemDataRole.UserRole, ("work", wid))
                witem.setFlags(witem.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                witem.setCheckState(
                    Qt.CheckState.Checked if wid in checked else Qt.CheckState.Unchecked
                )
                self.work_list.addItem(witem)

            for c in categories:
                if c.get("parent_id") == cat_id:
                    add_cat(c["id"], depth + 1)

        for c in categories:
            if not c.get("parent_id"):
                add_cat(c["id"], 0)

        self.work_list.blockSignals(False)
        # 체크박스를 직접 눌러도 상태 표시가 즉시 갱신되도록 시그널을 연결한다.
        if not self._check_signal_connected:
            self.work_list.itemChanged.connect(lambda _item: self._update_status())
            self._check_signal_connected = True
        self._update_status()

    def _filter_list(self, text: str) -> None:
        query = (text or "").strip().lower()
        cat_rows = []
        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole) or ()
            if data and data[0] == "cat":
                cat_rows.append((i, data[1]))

        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole) or ()
            if data and data[0] == "work":
                item.setHidden(bool(query) and query not in item.text().lower())

        for idx, (row, cat_id) in enumerate(cat_rows):
            end = cat_rows[idx + 1][0] if idx + 1 < len(cat_rows) else self.work_list.count()
            has_child = False
            for j in range(row + 1, end):
                child = self.work_list.item(j)
                if not child.isHidden():
                    has_child = True
                    break
            item = self.work_list.item(row)
            if query and query in item.text().lower():
                item.setHidden(False)
            else:
                item.setHidden(not has_child)

    def _set_all_checks(self, checked: bool) -> None:
        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            if item.isHidden():
                continue
            data = item.data(Qt.ItemDataRole.UserRole) or ()
            if data and data[0] == "work":
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self._update_status()

    def _update_status(self) -> None:
        self.selected_work_ids = []
        titles: list[str] = []
        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole) or ()
            if data and data[0] == "work":
                self.selected_work_ids.append(int(data[1]))
                titles.append(item.text().split("📄")[-1].strip().split("  (")[0])

        if not titles:
            self.status_label.setText("선택된 업무가 없습니다.")
        elif len(titles) <= 2:
            self.status_label.setText(f"{len(titles)}개 선택됨: {', '.join(titles)}")
        else:
            self.status_label.setText(
                f"{len(titles)}개 선택됨: {titles[0]}, {titles[1]} 외 {len(titles) - 2}개"
            )

    def _on_accept(self) -> None:
        self._update_status()
        self.accept()