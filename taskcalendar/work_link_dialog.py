"""문서 → 업무 연결 다이얼로그.

업무 관리의 WorkDocSelectDialog(업무 → 문서)와 대칭으로,
문서 등록/목록에서 연결할 업무를 골라 주는 창이다.
분류(폴더)는 제목 줄로만 보이고 고를 수 없다. 체크는 업무에만 있다.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css
from taskcalendar.paths import asset_path
from taskcalendar.storage import EncryptedRepository
from taskcalendar.ui_common import style_button

ROLE_KIND = Qt.ItemDataRole.UserRole          # ("cat", id, depth) / ("work", id)
ROLE_TITLE = Qt.ItemDataRole.UserRole + 1     # 업무 제목 (검색·선택 표시용)
ROLE_META = Qt.ItemDataRole.UserRole + 2      # 오른쪽 흐린 글자 (담당자)


class _WorkRowDelegate(QStyledItemDelegate):
    """업무 줄 오른쪽에 담당자를 흐리게, 분류 줄 아래에 얇은 선을 그린다."""

    def __init__(self, palette: dict, parent=None) -> None:
        super().__init__(parent)
        self.palette = palette

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        kind = index.data(ROLE_KIND) or ()
        painter.save()
        if kind and kind[0] == "cat":
            painter.setPen(QColor(self.palette.get("line_soft", self.palette.get("line", "#E2E8F0"))))
            y = option.rect.bottom()
            painter.drawLine(option.rect.left() + 6, y, option.rect.right() - 6, y)
        else:
            meta = index.data(ROLE_META) or ""
            if meta:
                painter.setPen(QColor(self.palette.get("muted", "#64748B")))
                font = QFont(option.font)
                font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
                painter.setFont(font)
                rect = QRect(option.rect)
                rect.setRight(rect.right() - 10)
                painter.drawText(rect, Qt.AlignRight | Qt.AlignVCenter, meta)
        painter.restore()


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
        self.resize(480, 560)
        self.setMinimumSize(400, 420)

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
        accent_soft = self.palette.get("accent_soft", "#DBEAFE")
        check_white_icon = str(asset_path("checkmark_white.svg")).replace("\\", "/")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("업무명, 분류, 담당자 검색")
        self.search_input.setFixedHeight(30)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 0 10px;
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
        """)
        self.search_input.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_input)

        self.work_list = QListWidget()
        self.work_list.setItemDelegate(_WorkRowDelegate(self.palette, self.work_list))
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
                padding: 0px 6px;
                border-radius: 4px;
            }}
            QListWidget::indicator {{
                width: 15px;
                height: 15px;
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
            }}
            QListWidget::indicator:hover {{ border-color: {accent}; }}
            QListWidget::indicator:checked {{
                background-color: {accent};
                border: 1px solid {accent};
                image: url("{check_white_icon}");
            }}
            QListWidget::item:hover {{ background-color: {panel_alt}; }}
            QListWidget::item:selected {{ background-color: {accent_soft}; color: {text}; }}
        """)
        self.work_list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.work_list, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {muted}; font-size: 12px;")
        layout.addWidget(self.status_label)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        btn_all = style_button(QPushButton("보이는 업무 모두 선택"), self.palette)
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        bottom_row.addWidget(btn_all)

        btn_none = style_button(QPushButton("선택 해제"), self.palette)
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        bottom_row.addWidget(btn_none)

        bottom_row.addStretch(1)

        btn_cancel = style_button(QPushButton("취소"), self.palette)
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)

        btn_ok = style_button(QPushButton("연결"), self.palette, "primary")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_accept)
        bottom_row.addWidget(btn_ok)

        layout.addLayout(bottom_row)

    # ------------------------------------------------------------ 데이터
    def _populate_work_list(self) -> None:
        """분류 제목 줄 아래에 업무를 체크 항목으로 채운다 (분류 자체는 고를 수 없음)."""
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
        children: dict[int | None, list[int]] = {}
        for c in categories:
            children.setdefault(c.get("parent_id") or None, []).append(c["id"])

        def has_works(cat_id: int) -> bool:
            return bool(by_cat.get(cat_id)) or any(has_works(ch) for ch in children.get(cat_id, []))

        checked = set(self.selected_work_ids)
        muted = QColor(self.palette.get("muted", "#64748B"))

        def add_cat(cat_id: int, depth: int, path: str) -> None:
            if not has_works(cat_id):
                return
            name = cat_name.get(cat_id, "") or "(이름 없는 분류)"
            full = f"{path} › {name}" if path else name
            item = QListWidgetItem(full)
            item.setData(ROLE_KIND, ("cat", cat_id, depth))
            item.setData(ROLE_TITLE, full)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 고를 수도, 체크할 수도 없는 제목 줄
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setForeground(muted)
            self.work_list.addItem(item)

            for it in by_cat.get(cat_id, []):
                wid = it["id"]
                title = (it.get("title") or "").strip() or "(제목 없음)"
                witem = QListWidgetItem(title)
                witem.setData(ROLE_KIND, ("work", wid))
                witem.setData(ROLE_TITLE, title)
                witem.setData(ROLE_META, str(it.get("assignee") or ""))
                witem.setData(Qt.ItemDataRole.UserRole + 3, f"{full} {it.get('assignee') or ''}".lower())
                witem.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
                )
                witem.setCheckState(Qt.CheckState.Checked if wid in checked else Qt.CheckState.Unchecked)
                self.work_list.addItem(witem)

            for ch in children.get(cat_id, []):
                add_cat(ch, depth + 1, full)

        for root in children.get(None, []):
            add_cat(root, 0, "")

        self.work_list.blockSignals(False)
        if not self._check_signal_connected:
            self.work_list.itemChanged.connect(lambda _item: self._update_status())
            self._check_signal_connected = True
        self._update_status()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """업무 줄은 글자를 눌러도 체크가 바뀌게 (체크박스만 누르지 않아도 됨)."""
        kind = item.data(ROLE_KIND) or ()
        if not kind or kind[0] != "work":
            return
        pos = self.work_list.viewport().mapFromGlobal(self.cursor().pos())
        rect = self.work_list.visualItemRect(item)
        if pos.x() - rect.left() <= 28:  # 체크박스 자리 클릭은 Qt가 이미 처리함
            return
        item.setCheckState(
            Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
        )

    def _filter_list(self, text: str) -> None:
        query = (text or "").strip().lower()
        last_cat = None
        cat_visible: dict[int, bool] = {}
        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            kind = item.data(ROLE_KIND) or ()
            if kind and kind[0] == "cat":
                last_cat = i
                cat_visible[i] = False
                continue
            hay = f"{item.data(ROLE_TITLE) or ''} {item.data(Qt.ItemDataRole.UserRole + 3) or ''}".lower()
            hidden = bool(query) and query not in hay
            item.setHidden(hidden)
            if not hidden and last_cat is not None:
                cat_visible[last_cat] = True
        for row, visible in cat_visible.items():
            self.work_list.item(row).setHidden(not visible)

    def _set_all_checks(self, checked: bool) -> None:
        self.work_list.blockSignals(True)
        try:
            for i in range(self.work_list.count()):
                item = self.work_list.item(i)
                kind = item.data(ROLE_KIND) or ()
                if item.isHidden() or not kind or kind[0] != "work":
                    continue
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.work_list.blockSignals(False)
        self.work_list.viewport().update()
        self._update_status()

    def _update_status(self) -> None:
        self.selected_work_ids = []
        titles: list[str] = []
        for i in range(self.work_list.count()):
            item = self.work_list.item(i)
            kind = item.data(ROLE_KIND) or ()
            if kind and kind[0] == "work" and item.checkState() == Qt.CheckState.Checked:
                self.selected_work_ids.append(int(kind[1]))
                titles.append(str(item.data(ROLE_TITLE) or ""))

        if not titles:
            self.status_label.setText("선택된 업무가 없습니다.")
        elif len(titles) <= 2:
            self.status_label.setText(f"{len(titles)}개 선택: {', '.join(titles)}")
        else:
            self.status_label.setText(f"{len(titles)}개 선택: {titles[0]}, {titles[1]} 외 {len(titles) - 2}개")

    def _on_accept(self) -> None:
        self._update_status()
        self.accept()
