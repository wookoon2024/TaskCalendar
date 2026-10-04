from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QMarginsF,
    QMimeData,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QDrag,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QPen,
    QPolygonF,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
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
    QProgressDialog,
    QProxyStyle,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleFactory,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QToolTip,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.fonts import font_family_css, make_ui_font_like, scale_px, ui_font_family
from taskcalendar.paths import asset_path
from taskcalendar.qt_styles import _shade, resolve_palette
from taskcalendar.qt_rhwp_editor import RhwpEditorWidget
from taskcalendar.rich_text_edit import RichTextEdit
from taskcalendar.models import CalendarEntry, EntryType, RecurrenceType
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


class FloatingToastOverlay(QFrame):
    """업무창 정중앙에 스킨 색상에 맞춰 떴다가 자동으로 페이드아웃되며 사라지는 플로팅 알림창"""

    def __init__(self, parent: QWidget, message: str, palette: dict[str, str], duration_ms: int = 1200) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        panel = palette.get("panel", "#FFFFFF")
        text = palette.get("text", "#1F2328")
        line = palette.get("line", "#CBD5E0")
        accent = palette.get("accent", "#2563EB")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1.5px solid {accent};
                border-radius: 18px;
            }}
            QLabel {{
                color: {text};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
                border: none;
            }}
            QLabel#iconLbl {{
                color: {accent};
                font-size: 15px;
                font-weight: bold;
                background: transparent;
                border: none;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 9, 20, 9)
        layout.setSpacing(8)

        icon_lbl = QLabel("✓", self)
        icon_lbl.setObjectName("iconLbl")
        layout.addWidget(icon_lbl)

        text_lbl = QLabel(message, self)
        layout.addWidget(text_lbl)

        self.adjustSize()
        self._reposition()

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(1.0)

        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._anim.setDuration(350)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.close)

        QTimer.singleShot(duration_ms, self._start_fade_out)

    def _reposition(self) -> None:
        try:
            p = self.parent()
            if p and p.isWidgetType():
                parent_rect = p.rect()
                x = (parent_rect.width() - self.width()) // 2
                y = (parent_rect.height() - self.height()) // 2
                self.move(x, y)
        except Exception:
            pass

    def _start_fade_out(self) -> None:
        try:
            self._anim.start()
        except Exception:
            pass


class CompactCategoryItemDelegate(QStyledItemDelegate):
    """트리 항목(폴더 및 문서)의 텍스트 렌더링 델리게이트 (상단 '업무 분류' 헤더와 100% 동일한 QLabel 렌더링 적용)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lbl = QLabel()
        self._lbl.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._lbl.setContentsMargins(0, 0, 0, 0)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(size.height(), 24))

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        is_sel = bool(view and view.selectionModel() and view.selectionModel().isSelected(index))
        is_hover = bool(opt.state & QStyle.State_MouseOver)
        is_parent = not index.parent().isValid()
        text = opt.text or ""

        # 상단 '📁 업무 분류' 헤더(left_title)와 100% 동일한 QLabel 스타일 및 렌더링
        weight = "bold" if is_parent else "normal"
        if is_sel:
            color = "#0284C7"
        elif is_hover:
            color = "#0F172A"
        else:
            color = "#1F2328"

        self._lbl.setText(text)
        self._lbl.setStyleSheet(f"font-weight: {weight}; font-size: 12px; color: {color}; border: none; background: transparent;")

        r = opt.rect.adjusted(2, 0, -2, 0)
        self._lbl.resize(r.width(), r.height())

        painter.save()
        painter.translate(r.topLeft())
        self._lbl.render(painter, QPoint(0, 0))
        painter.restore()


class CompactCategoryTree(QTreeWidget):
    """왼쪽 인덴트/가지 영역과 텍스트가 일관된 테마 색상으로 자연스럽게 선택되며 내부 정렬 및 외부 파일 드롭을 지원하는 트리 위젯"""

    orderChanged = Signal()
    filesDropped = Signal(list, str, object)  # (file_paths, category_name, category_id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._drop_target_item = None
        self._drop_pos = None

    def _detach_item(self, item: QTreeWidgetItem) -> QTreeWidgetItem:
        """아이템을 현재 부모 또는 최상위 트리에서 안전하게 분리하여 반환"""
        parent = item.parent()
        if parent:
            parent.takeChild(parent.indexOfChild(item))
        else:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        return item

    def drawBranches(self, painter, rect, index):
        """화살표 영역 제거 (폴더 아이콘 📁/📂 자체로 펼침/접힘 상태 표시)"""
        return

    def drawRow(self, painter, option, index):
        is_sel = bool(self.selectionModel() and self.selectionModel().isSelected(index))
        is_hover = bool(option.state & QStyle.State_MouseOver)
        is_parent = not index.parent().isValid()

        # 폴더 항목인 경우 좌측 가지 영역까지 포함하여 행 전체(x=0~끝)를 동일한 색상으로 칠함
        # 문서(자식) 항목인 경우 들여쓰기를 반영한 영역에 칠함
        if is_sel or is_hover:
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                bg_color = QColor('#E0F2FE') if is_sel else QColor('#F1F5F9')
                painter.setBrush(bg_color)
                if is_parent:
                    row_rect = QRect(0, option.rect.y() + 1, option.rect.width(), option.rect.height() - 2)
                else:
                    vr = self.visualRect(index)
                    row_rect = vr.adjusted(0, 1, -2, -1)
                painter.drawRoundedRect(row_rect, 4, 4)
            finally:
                painter.restore()

        if self.model().hasChildren(index):
            item = self.itemFromIndex(index)
            if item:
                vr = self.visualItemRect(item)
                b_rect = QRect(0, option.rect.y(), vr.left(), option.rect.height())
                self.drawBranches(painter, b_rect, index)

        # 항목별 실제 영역(들여쓰기 반영)으로 텍스트 그리기
        item_option = QStyleOptionViewItem(option)
        item_option.rect = self.visualRect(index)
        self.itemDelegate().paint(painter, item_option, index)

    def paintEvent(self, event):
        super().paintEvent(event)
        if getattr(self, "_drop_target_item", None):
            item = self._drop_target_item
            drop_pos = getattr(self, "_drop_pos", None)
            rect = self.visualItemRect(item)
            if not rect.isValid() or rect.width() <= 0:
                return

            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            try:
                accent_color = QColor("#2563EB")
                accent_fill = QColor(37, 99, 235, 35)

                is_folder = not isinstance(item.data(0, Qt.UserRole), WorkSheetData)
                # 폴더 위로 올렸거나 OnItem인 경우: 대상 폴더 영역 전체에 파란색 포커스 테두리와 은은한 배경 강조
                if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem or (is_folder and drop_pos is None):
                    target_rect = QRect(2, rect.y() + 1, self.viewport().width() - 4, rect.height() - 2)
                    painter.setPen(QPen(accent_color, 2))
                    painter.setBrush(accent_fill)
                    painter.drawRoundedRect(target_rect, 4, 4)
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
                    # 상단 삽입 위치 가이드선 (좌측 앵커 점 + 가로선)
                    y = rect.top()
                    x_start = max(4, rect.left())
                    x_end = self.viewport().width() - 6
                    painter.setPen(QPen(accent_color, 2))
                    painter.drawLine(x_start + 4, y, x_end, y)
                    painter.setBrush(accent_color)
                    painter.drawEllipse(QPoint(x_start + 4, y), 3, 3)
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    # 하단 삽입 위치 가이드선 (좌측 앵커 점 + 가로선)
                    y = rect.bottom()
                    x_start = max(4, rect.left())
                    x_end = self.viewport().width() - 6
                    painter.setPen(QPen(accent_color, 2))
                    painter.drawLine(x_start + 4, y, x_end, y)
                    painter.setBrush(accent_color)
                    painter.drawEllipse(QPoint(x_start + 4, y), 3, 3)
            finally:
                painter.end()

    def startDrag(self, supportedActions):
        """내부 트리 이동 시 식별용 마임 전달 및 순수 MoveAction 적용"""
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData("application/x-taskcalendar-cat-internal", b"1")
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-cat-internal"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-cat-internal"):
            event.acceptProposedAction()
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._drop_target_item = self.itemAt(pos)
            self._drop_pos = self.dropIndicatorPosition()
            self.viewport().update()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._drop_target_item = None
        self._drop_pos = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        # 1. 탐색기 등 외부 파일 드롭
        if event.mimeData().hasUrls() and not event.mimeData().hasFormat("application/x-taskcalendar-cat-internal"):
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                item = self.itemAt(pos)
                target_cat_name = "일반 업무"
                target_cat_id = None
                if item:
                    cat_name = item.data(0, Qt.UserRole + 2)
                    cat_id = item.data(0, Qt.UserRole + 1)
                    if cat_name:
                        target_cat_name = cat_name
                        target_cat_id = cat_id
                    else:
                        parent = item.parent()
                        if parent:
                            target_cat_name = parent.data(0, Qt.UserRole + 2) or parent.text(0)
                            target_cat_id = parent.data(0, Qt.UserRole + 1)
                self.filesDropped.emit(paths, target_cat_name, target_cat_id)
                event.acceptProposedAction()
                return

        # 2. 내부 드래그 이동 (복사가 아닌 순수 이동 수동 처리)
        if not event.mimeData().hasFormat("application/x-taskcalendar-cat-internal"):
            super().dropEvent(event)
            return

        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_item = self.itemAt(pos)
        moving_items = [it for it in self.selectedItems() if it is not target_item]
        if not moving_items:
            event.acceptProposedAction()
            return

        self.blockSignals(True)
        try:
            drop_pos = self.dropIndicatorPosition()

            for it in moving_items:
                is_sheet = isinstance(it.data(0, Qt.UserRole), WorkSheetData)

                if is_sheet:
                    # ── 이동 대상이 [문서]인 경우 ────────────────────────
                    if target_item:
                        target_is_sheet = isinstance(target_item.data(0, Qt.UserRole), WorkSheetData)
                        if not target_is_sheet:
                            # 폴더 위에 드롭 -> 해당 폴더의 자식으로 맨 끝에 추가
                            self._detach_item(it)
                            target_item.addChild(it)
                            target_item.setExpanded(True)
                        else:
                            # 다른 문서 위에/근처에 드롭 -> 해당 문서의 부모 폴더 내에서 순서 삽입
                            t_parent = target_item.parent()
                            if t_parent:
                                idx = t_parent.indexOfChild(target_item)
                                if drop_pos == QTreeWidget.DropIndicatorPosition.BelowItem:
                                    idx += 1
                                self._detach_item(it)
                                t_parent.insertChild(min(idx, t_parent.childCount()), it)
                                t_parent.setExpanded(True)
                            else:
                                # 부모 없는 문서 방어 -> 첫 번째 폴더로 이동
                                self._detach_item(it)
                                if self.topLevelItemCount() > 0:
                                    self.topLevelItem(0).addChild(it)
                    else:
                        # 빈 공간에 드롭 -> 트리의 마지막 폴더에 추가
                        last_folder = None
                        for i in range(self.topLevelItemCount() - 1, -1, -1):
                            top = self.topLevelItem(i)
                            if not isinstance(top.data(0, Qt.UserRole), WorkSheetData):
                                last_folder = top
                                break
                        target = last_folder or (self.topLevelItem(0) if self.topLevelItemCount() > 0 else None)
                        if target:
                            self._detach_item(it)
                            target.addChild(it)
                            target.setExpanded(True)

                else:
                    # ── 이동 대상이 [폴더(분류)]인 경우 ─────────────────
                    def is_ancestor(ancestor, item):
                        p = item.parent()
                        while p:
                            if p == ancestor:
                                return True
                            p = p.parent()
                        return False

                    # 자기 자신이나 자기 자식으로의 이동은 순환 방지를 위해 무시
                    if target_item and (target_item == it or is_ancestor(it, target_item)):
                        continue

                    if target_item:
                        target_is_sheet = isinstance(target_item.data(0, Qt.UserRole), WorkSheetData)
                        effective_target = target_item.parent() if target_is_sheet else target_item

                        if effective_target and effective_target != it:
                            if drop_pos == QTreeWidget.DropIndicatorPosition.OnItem and not target_is_sheet:
                                # 폴더 위에 드롭 -> 해당 폴더의 하위 폴더로 이동
                                self._detach_item(it)
                                effective_target.addChild(it)
                                effective_target.setExpanded(True)
                            else:
                                # 폴더 위/아래 순서 변경
                                p = effective_target.parent()
                                if p:
                                    idx = p.indexOfChild(effective_target)
                                    if drop_pos == QTreeWidget.DropIndicatorPosition.BelowItem:
                                        idx += 1
                                    self._detach_item(it)
                                    p.insertChild(min(idx, p.childCount()), it)
                                else:
                                    idx = self.indexOfTopLevelItem(effective_target)
                                    if drop_pos == QTreeWidget.DropIndicatorPosition.BelowItem:
                                        idx += 1
                                    self._detach_item(it)
                                    self.insertTopLevelItem(min(idx, self.topLevelItemCount()), it)
                    else:
                        # 빈 공간에 드롭 -> 최상위 폴더 맨 뒤로 이동
                        self._detach_item(it)
                        self.addTopLevelItem(it)

            # 방어적 무결성 검증:
            # 1. 문서(WorkSheetData)가 어떤 아이템을 자식으로 품고 있다면 -> 상위로 분리
            def fix_children(parent_item):
                for i in range(parent_item.childCount() - 1, -1, -1):
                    child = parent_item.child(i)
                    sheet = child.data(0, Qt.UserRole)
                    if isinstance(sheet, WorkSheetData):
                        while child.childCount() > 0:
                            sub = child.takeChild(0)
                            parent_item.addChild(sub)
                    else:
                        fix_children(child)

            # 2. 루트 레벨에 문서가 떠돌고 있다면 -> 인접한 첫 번째 폴더로 흡수
            first_folder = None
            for i in range(self.topLevelItemCount()):
                top = self.topLevelItem(i)
                if not isinstance(top.data(0, Qt.UserRole), WorkSheetData):
                    first_folder = top
                    break

            for i in range(self.topLevelItemCount() - 1, -1, -1):
                top = self.topLevelItem(i)
                sheet = top.data(0, Qt.UserRole)
                if isinstance(sheet, WorkSheetData):
                    self.takeTopLevelItem(i)
                    target = first_folder or (self.topLevelItem(0) if self.topLevelItemCount() > 0 else None)
                    if target:
                        target.addChild(top)
                else:
                    fix_children(top)

            event.acceptProposedAction()
        finally:
            self._drop_target_item = None
            self._drop_pos = None
            self.viewport().update()
            self.blockSignals(False)

        self.orderChanged.emit()


class WorkCategoryTreeContainer(QWidget):
    """트리와 하단 탭을 완벽한 엑셀 시트형 일체감으로 결합하는 복합 컨테이너"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workCategoryTreeContainer")
        self.setStyleSheet("#workCategoryTreeContainer { background: transparent; border: none; }")
        self.category_tree: QTreeWidget | None = None
        self.cat_tab_scroll: QWidget | None = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        h = self.height()
        tab_h = 24
        if self.category_tree and self.cat_tab_scroll:
            self.category_tree.setGeometry(0, 0, w, max(10, h - tab_h + 1))
            self.cat_tab_scroll.setGeometry(0, max(0, h - tab_h), w, tab_h)
            self.cat_tab_scroll.raise_()


class CompactAttachmentItemDelegate(QStyledItemDelegate):
    """우측 첨부파일 트리 항목(폴더 및 파일)의 텍스트 렌더링 델리게이트"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        is_sel = bool(view and view.selectionModel() and view.selectionModel().isSelected(index))
        item = view.itemFromIndex(index) if view else None
        item_data = item.data(0, Qt.UserRole) if item else {}
        is_folder = isinstance(item_data, dict) and item_data.get("type") == "folder"

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if is_sel:
            painter.setPen(QColor("#0284C7"))
        elif opt.state & QStyle.State_MouseOver:
            painter.setPen(QColor("#0F172A"))
        else:
            painter.setPen(QColor("#1E293B") if is_folder else QColor("#334155"))

        font = opt.font
        if is_folder or is_sel:
            font.setBold(True)
        painter.setFont(font)

        text_rect = opt.rect.adjusted(2, 0, -2, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, opt.text)
        painter.restore()


def make_work_progress_dialog(parent: QWidget | None, title: str, label: str, max_val: int) -> QProgressDialog:
    """작업 관리자 공통 프로그레스 다이얼로그 생성기"""
    dlg = QProgressDialog(label, None, 0, max(1, max_val), parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(0)
    dlg.setAutoClose(True)
    dlg.setAutoReset(True)
    dlg.setFixedSize(380, 100)
    dlg.setStyleSheet("""
        QProgressDialog {
            background-color: #FFFFFF;
            border: 1px solid #CBD5E1;
            border-radius: 8px;
        }
        QLabel {
            font-size: 12px;
            color: #1E293B;
        }
        QProgressBar {
            border: 1px solid #E2E8F0;
            border-radius: 4px;
            text-align: center;
            background-color: #F1F5F9;
            height: 18px;
            font-size: 11px;
            color: #1E293B;
        }
        QProgressBar::chunk {
            background-color: #0284C7;
            border-radius: 3px;
        }
    """)
    dlg.show()
    QApplication.processEvents()
    return dlg


class CompactAttachmentTree(QTreeWidget):
    """
    우측 첨부파일 계층형 트리 위젯:
    - 폴더(📁/📂) 및 파일 계층 구조 지원 (트리 열고닫기)
    - 탐색기 파일/폴더 드래그앤드롭 수신 (특정 폴더 위에 드롭 시 해당 폴더로 쏙)
    - 내부 항목 드래그 이동 (폴더 위 드롭 시 자식으로 이동, 순서 변경)
    - Shift/Ctrl 다중 선택 및 Delete 키 단축키 지원
    """

    orderChanged = Signal()
    filesDropped = Signal(list, str)  # (paths, target_subfolder)
    deletePressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(16)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.setItemDelegate(CompactAttachmentItemDelegate(self))
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._drop_target_item = None
        self._drop_pos = None

    # ------------------------------------------------------------------
    # 내부 유틸: 아이템을 현재 위치에서 분리
    # ------------------------------------------------------------------
    def _detach_item(self, item: "QTreeWidgetItem") -> "QTreeWidgetItem":
        parent = item.parent()
        if parent:
            parent.takeChild(parent.indexOfChild(item))
        else:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        return item

    def drawBranches(self, painter, rect, index):
        """화살표 영역 제거 (폴더 아이콘 자체로 상태 표시 및 좌측 여백 극소화)"""
        return

    def drawRow(self, painter, option, index):
        is_sel = bool(self.selectionModel() and self.selectionModel().isSelected(index))
        is_hover = bool(option.state & QStyle.State_MouseOver)

        if is_sel or is_hover:
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                bg_color = QColor("#E0F2FE") if is_sel else QColor("#F1F5F9")
                painter.setBrush(bg_color)
                row_rect = QRect(0, option.rect.y() + 1, self.viewport().width(), option.rect.height() - 2)
                painter.drawRoundedRect(row_rect, 4, 4)
            finally:
                painter.restore()

        item_option = QStyleOptionViewItem(option)
        # 중요: visualRect(index)를 사용하여 계층별 들여쓰기 좌표를 정확히 반영하여 텍스트 렌더링!
        vr = self.visualRect(index)
        item_option.rect = vr
        self.itemDelegate().paint(painter, item_option, index)

    def paintEvent(self, event):
        super().paintEvent(event)
        if getattr(self, "_drop_target_item", None):
            item = self._drop_target_item
            drop_pos = getattr(self, "_drop_pos", None)
            rect = self.visualItemRect(item)
            if not rect.isValid() or rect.width() <= 0:
                return

            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            try:
                accent_color = QColor("#2563EB")
                accent_fill = QColor(37, 99, 235, 35)

                it_data = item.data(0, Qt.UserRole) or {}
                is_folder = (it_data.get("type") == "folder")

                if drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem or (is_folder and drop_pos is None):
                    target_rect = QRect(2, rect.y() + 1, self.viewport().width() - 4, rect.height() - 2)
                    painter.setPen(QPen(accent_color, 2))
                    painter.setBrush(accent_fill)
                    painter.drawRoundedRect(target_rect, 4, 4)
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
                    y = rect.top()
                    x_start = max(4, rect.left())
                    x_end = self.viewport().width() - 6
                    painter.setPen(QPen(accent_color, 2))
                    painter.drawLine(x_start + 4, y, x_end, y)
                    painter.setBrush(accent_color)
                    painter.drawEllipse(QPoint(x_start + 4, y), 3, 3)
                elif drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                    y = rect.bottom()
                    x_start = max(4, rect.left())
                    x_end = self.viewport().width() - 6
                    painter.setPen(QPen(accent_color, 2))
                    painter.drawLine(x_start + 4, y, x_end, y)
                    painter.setBrush(accent_color)
                    painter.drawEllipse(QPoint(x_start + 4, y), 3, 3)
            finally:
                painter.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.deletePressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supportedActions):
        """
        드래그 시작:
        1. 이미지 파일 등 외부 한글(HWP)로 드래그 시 CF_HDROP(setUrls) 및 setImageData 전달하여 즉시 삽입
        2. 내부 트리 이동 시 식별용 마임 전달
        """
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        urls = []
        has_image = False
        img_path = None
        for it in items:
            data = it.data(0, Qt.UserRole) or {}
            fpath = data.get("path")
            if fpath and os.path.exists(fpath):
                urls.append(QUrl.fromLocalFile(os.path.abspath(fpath)))
                ext = Path(fpath).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                    has_image = True
                    img_path = fpath

        if urls:
            mime_data.setUrls(urls)
            if has_image and img_path:
                img = QImage(img_path)
                if not img.isNull():
                    mime_data.setImageData(img)

        mime_data.setData("application/x-taskcalendar-att-internal", b"1")
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        super().dragEnterEvent(event)
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        self._drop_target_item = self.itemAt(pos)
        self._drop_pos = self.dropIndicatorPosition()
        self.viewport().update()
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop_target_item = None
        self._drop_pos = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_item = self.itemAt(pos)

        # ── 1. 외부 파일 드롭 (탐색기 등) ──────────────────────────────────
        if event.mimeData().hasUrls() and not event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                target_subfolder = ""
                if target_item:
                    d = target_item.data(0, Qt.UserRole) or {}
                    if d.get("type") == "folder":
                        target_subfolder = d.get("folder_path", "")
                    else:
                        parent = target_item.parent()
                        if parent:
                            target_subfolder = (parent.data(0, Qt.UserRole) or {}).get("folder_path", "")
                self.filesDropped.emit(paths, target_subfolder)
                event.acceptProposedAction()
            return

        # ── 2. 내부 드래그 이동 (수동 처리) ────────────────────────────────
        if not event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            super().dropEvent(event)
            return

        moving_items = [it for it in self.selectedItems() if it is not target_item]
        if not moving_items:
            event.acceptProposedAction()
            return

        self.blockSignals(True)
        try:
            # 타겟 정보 확인
            target_is_folder = False
            if target_item:
                t_data = target_item.data(0, Qt.UserRole) or {}
                target_is_folder = (t_data.get("type") == "folder")

            # ── 케이스 A: 폴더 아이템에 드롭한 경우 -> 무조건 해당 폴더의 자식으로 삽입!
            if target_item and target_is_folder:
                for it in moving_items:
                    it_data = it.data(0, Qt.UserRole) or {}
                    # 파일만 폴더 안으로 이동 (폴더 간 중첩은 방지)
                    if it_data.get("type") != "folder":
                        self._detach_item(it)
                        target_item.addChild(it)
                target_item.setExpanded(True)

            # ── 케이스 B: 폴더 안의 특정 파일 위에/근처에 드롭한 경우 -> 그 부모 폴더의 자식으로 삽입!
            elif target_item and target_item.parent():
                t_parent = target_item.parent()
                idx = t_parent.indexOfChild(target_item)
                drop_pos = self.dropIndicatorPosition()
                if drop_pos == QTreeWidget.DropIndicatorPosition.BelowItem:
                    idx += 1
                for i, it in enumerate(moving_items):
                    it_data = it.data(0, Qt.UserRole) or {}
                    if it_data.get("type") != "folder":
                        self._detach_item(it)
                        t_parent.insertChild(min(idx + i, t_parent.childCount()), it)
                t_parent.setExpanded(True)

            # ── 케이스 C: 최상위 파일 위에 드롭한 경우 -> 최상위에서 순서 변경
            elif target_item and not target_item.parent():
                idx = self.indexOfTopLevelItem(target_item)
                drop_pos = self.dropIndicatorPosition()
                if drop_pos == QTreeWidget.DropIndicatorPosition.BelowItem:
                    idx += 1
                for i, it in enumerate(moving_items):
                    self._detach_item(it)
                    self.insertTopLevelItem(min(idx + i, self.topLevelItemCount()), it)

            # ── 케이스 D: 빈 공간에 드롭 -> 최상위 루트로 분리
            else:
                for it in moving_items:
                    self._detach_item(it)
                    self.addTopLevelItem(it)

            # 모든 폴더 펼침 유지
            self.expandAll()

        finally:
            self._drop_target_item = None
            self._drop_pos = None
            self.blockSignals(False)

        self.viewport().update()
        self.update()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.orderChanged.emit)
        event.acceptProposedAction()




def extract_document_content(file_path: Path | str) -> tuple[str, bytes | None]:
    """
    다양한 문서 포맷(HWP, HWPX, PDF, DOCX, TXT, MD, CSV, JSON 등)에서 본문 텍스트 또는 바이너리 추출
    반환: (text_content, hwpx_or_hwp_binary_bytes)
    """
    path = Path(file_path)
    if not path.exists():
        return "", None

    ext = path.suffix.lower()

    # 1. HWP / HWPX (rhwp-studio WebAssembly 엔진에서 무손실 직접 로드)
    if ext in (".hwpx", ".hwp"):
        try:
            raw_bytes = path.read_bytes()
            extracted_text = ""
            if ext == ".hwpx":
                try:
                    import zipfile
                    import re
                    with zipfile.ZipFile(path, "r") as z:
                        for name in z.namelist():
                            if name.startswith("Contents/section") and name.endswith(".xml"):
                                xml_data = z.read(name).decode("utf-8", errors="ignore")
                                text_nodes = re.findall(r"<[^:]+:t[^>]*>(.*?)</[^:]+:t>", xml_data)
                                if text_nodes:
                                    extracted_text += "\n".join(t for t in text_nodes if t.strip()) + "\n"
                except Exception:
                    pass
            return extracted_text.strip(), raw_bytes
        except Exception as e:
            logger.warning(f"Failed to read HWP/HWPX file {path}: {e}")
            return "", None

    # 2. PDF 문서 (pypdf 활용)
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            pages_text = []
            for p in reader.pages:
                t = p.extract_text()
                if t:
                    pages_text.append(t.strip())
            return "\n\n".join(pages_text), None
        except Exception as e:
            logger.warning(f"Failed to extract PDF text from {path}: {e}")
            return "", None

    # 3. DOCX 워드 문서 (표준 zipfile 및 안전한 텍스트 추출)
    if ext == ".docx":
        try:
            import zipfile
            import re
            with zipfile.ZipFile(path, "r") as z:
                xml_content = z.read("word/document.xml").decode("utf-8", errors="ignore")
                paras = []
                p_matches = re.findall(r"<w:p[ >](.*?)</w:p>", xml_content, flags=re.DOTALL)
                for p_body in p_matches:
                    t_nodes = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p_body)
                    p_text = "".join(t_nodes).strip()
                    if p_text:
                        paras.append(p_text)
                return "\n".join(paras), None
        except Exception as e:
            logger.warning(f"Failed to extract DOCX text from {path}: {e}")
            return "", None

    # 4. 일반 텍스트 및 코드/데이터 파일 (.txt, .md, .csv, .json, .log, .py, .xml, .html 등)
    if ext in (".html", ".htm"):
        # 단일 번들 웹앱(수 MB 이상)인 경우 raw script/css/base64가 텍스트에 포함되지 않도록 정제
        file_size = path.stat().st_size
        if file_size > 500 * 1024: # 500KB 초과 대용량 HTML은 웹 애플리케이션으로 간주
            return (
                f"[웹 애플리케이션 / 대용량 HTML 파일: {path.name} ({file_size // 1024} KB)]\n"
                f"본 파일은 대용량 웹 번들 파일로, 원본은 첨부파일 보관함에 안전하게 유지됩니다.",
                None,
            )
        try:
            from bs4 import BeautifulSoup
            for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
                try:
                    raw_html = path.read_text(encoding=enc)
                    soup = BeautifulSoup(raw_html, "html.parser")
                    for s in soup(["script", "style", "svg", "noscript"]):
                        s.decompose()
                    clean_text = soup.get_text(separator="\n", strip=True)
                    return clean_text[:50000], None
                except UnicodeDecodeError:
                    continue
        except Exception:
            pass

    encodings = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16")
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            # 최대 100KB까지만 텍스트로 로드 (초대용량 덤프/로그 방어)
            if len(text) > 100 * 1024:
                text = text[:100 * 1024] + f"\n... [대용량 텍스트 파일: {len(text) // 1024}KB 중 100KB 미리보기 표시]"
            return text, None
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f"Failed to read text file {path} with {enc}: {e}")
            break

    return "", None


def check_document_drm(file_path: Path | str, raw_bytes: bytes | None = None) -> tuple[bool, str]:
    """
    한글(HWP, HWPX) 및 주요 문서 파일이 DRM(Fasoo, SoftCamp, MarkAny 등)으로 암호화되어 있는지 감지.
    반환: (is_drm, drm_vendor_or_reason)
    """
    path = Path(file_path)
    if not path.exists():
        return False, ""

    if raw_bytes is None:
        try:
            with open(path, "rb") as f:
                raw_bytes = f.read(4096)
        except Exception as e:
            return False, f"파일 읽기 오류: {e}"

    if not raw_bytes or len(raw_bytes) < 8:
        return False, ""

    header_sample = raw_bytes[:1024]
    header_lower = header_sample.lower()

    # 명시적 DRM 시그니처 검사 (국내 주요 공공기관 및 대기업 DRM)
    if b"fs_packet" in header_lower or b"fsdn" in header_lower or b"fasoo" in header_lower:
        return True, "Fasoo DRM (파수 엔터프라이즈 문서보안)"
    if b"scdoc" in header_lower or b"scpkg" in header_lower or b"documentsafer" in header_lower or b"softcamp" in header_lower:
        return True, "SoftCamp Document Safer (소프트캠프 문서보안)"
    if b"markany" in header_lower or b"made" in header_lower or b"drm" in header_lower[:64]:
        return True, "MarkAny DRM (마크애니 문서보안)"

    ext = path.suffix.lower()
    if ext == ".hwpx":
        if not raw_bytes.startswith(b"PK\x03\x04"):
            return True, "사내 문서보안 솔루션 (DRM 암호화 파일)"
    elif ext == ".hwp":
        OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        if not raw_bytes.startswith(OLE_MAGIC) and not raw_bytes.startswith(b"HWP Document File"):
            return True, "사내 문서보안 솔루션 (DRM 암호화 파일)"
    elif ext in (".docx", ".xlsx", ".pptx"):
        if not raw_bytes.startswith(b"PK\x03\x04"):
            return True, "사내 문서보안 솔루션 (DRM 암호화 파일)"
    elif ext == ".pdf":
        if not raw_bytes.startswith(b"%PDF"):
            return True, "사내 문서보안 솔루션 (DRM 암호화 파일)"

    return False, ""


def try_extract_via_hwp_com(file_path: Path | str, timeout_sec: float = 3.5) -> tuple[str, bytes | None]:
    """
    PC에 설치된 한글 프로그램(Hwp.exe)의 OLE Automation(COM)을 통해
    DRM 파일의 텍스트 또는 HWPX 변환 바이트 추출 시도 (최대 timeout_sec 초 대기).
    한글 프로그램은 DRM 화이트리스트에 등록되어 있으므로 정상 복호화 가능.
    보안 차단이나 응답 대기 시 프리징을 방지하기 위해 별도 스레드에서 타임아웃 실행.
    """
    import threading
    result = ["", None]

    def _worker():
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                try:
                    hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
                except Exception:
                    return

                try:
                    # 메시지 박스 무인 모드 (대화상자 팝업 억제)
                    if hasattr(hwp, "SetMessageBoxMode"):
                        hwp.SetMessageBoxMode(0x00010000)
                    # 보안 승인 모듈 등록 시도
                    hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
                except Exception:
                    pass

                try:
                    if hasattr(hwp, "XHwpWindows") and hwp.XHwpWindows.Count > 0:
                        hwp.XHwpWindows.Item(0).Visible = False

                    opened = hwp.Open(str(Path(file_path).resolve()), "HWP", "forceopen:true")
                    if not opened:
                        hwp.Quit()
                        return

                    text = hwp.GetTextFile("TEXT", "")

                    hwpx_bytes = None
                    try:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".hwpx", delete=False) as tmp:
                            tmp_hwpx = tmp.name
                        hwp.SaveAs(tmp_hwpx, "HWPX")
                        tmp_p = Path(tmp_hwpx)
                        if tmp_p.exists() and tmp_p.stat().st_size > 0:
                            is_re_drm, _ = check_document_drm(tmp_p)
                            if not is_re_drm:
                                hwpx_bytes = tmp_p.read_bytes()
                        try:
                            tmp_p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    hwp.Clear(1)
                    hwp.Quit()
                    result[0] = (text or "").strip()
                    result[1] = hwpx_bytes
                except Exception as e:
                    logger.warning(f"Error during Hwp COM extraction: {e}")
                    try:
                        hwp.Quit()
                    except Exception:
                        pass
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logger.warning(f"win32com dispatch failed: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    return result[0], result[1]


class WorkDrmWarningDialog(QDialog):
    """DRM 암호화 문서 감지 시 사용자에게 사유와 해결 방법을 안내하고 선택지를 제공하는 모달 다이얼로그"""

    def __init__(self, parent=None, file_path: Path | str = "", drm_vendor: str = "", palette: dict | None = None):
        super().__init__(parent)
        self.p = Path(file_path)
        self.drm_vendor = drm_vendor or "사내 문서보안(DRM)"
        self.action = "cancel"
        pal = palette or {}

        self.setWindowTitle("보안(DRM) 암호화 문서 안내")
        self.setFixedWidth(530)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)

        # 상단 헤더
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(12)
        icon_lbl = QLabel("🔒")
        icon_lbl.setStyleSheet("font-size: 28px;")
        hdr_row.addWidget(icon_lbl)

        hdr_v = QVBoxLayout()
        hdr_v.setSpacing(3)
        t_lbl = QLabel("사내 보안(DRM) 암호화 문서 감지")
        t_lbl.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {pal.get('text', '#1F2328')};")
        s_lbl = QLabel("선택하신 문서는 보안 솔루션으로 암호화되어 있어 직접 변환할 수 없습니다.")
        s_lbl.setStyleSheet(f"font-size: 12px; color: {pal.get('muted', '#656D76')};")
        hdr_v.addWidget(t_lbl)
        hdr_v.addWidget(s_lbl)
        hdr_row.addLayout(hdr_v)
        layout.addLayout(hdr_row)

        # 파일 정보 카드
        try:
            sz = self.p.stat().st_size
            sz_str = f"{sz / 1024 / 1024:.2f} MB" if sz >= 1024 * 1024 else f"{sz / 1024:.1f} KB"
        except Exception:
            sz_str = "알 수 없음"

        info_box = QFrame()
        info_box.setStyleSheet(f"""
            QFrame {{
                background-color: {pal.get('panel_alt', '#F8FAFC')};
                border: 1px solid {pal.get('line', '#E2E8F0')};
                border-radius: 6px;
            }}
        """)
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(4)
        f_lbl = QLabel(f"• <b>대상 파일:</b> {self.p.name} ({sz_str})")
        d_lbl = QLabel(f"• <b>감지된 보안 솔루션:</b> {self.drm_vendor}")
        for lbl in (f_lbl, d_lbl):
            lbl.setStyleSheet(f"font-size: 11px; color: {pal.get('text', '#1F2328')};")
            lbl.setWordWrap(True)
            info_layout.addWidget(lbl)
        layout.addWidget(info_box)

        # 상세 사유 및 가이드 카드
        guide_box = QFrame()
        guide_box.setStyleSheet("""
            QFrame {{
                background-color: #FFFBEB;
                border: 1px solid #FDE68A;
                border-radius: 6px;
            }}
        """)
        guide_layout = QVBoxLayout(guide_box)
        guide_layout.setContentsMargins(12, 10, 12, 10)
        guide_layout.setSpacing(6)

        g_title = QLabel("💡 한글 프로그램에서는 열리는데 왜 나라수첩에서는 안 열리나요?")
        g_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #92400E;")
        guide_layout.addWidget(g_title)

        g_desc = QLabel(
            "사내 DRM(Fasoo, SoftCamp 등)은 보안 정책상 오직 공인된 <b>한글(Hwp.exe) 정품 프로그램</b>에만 실시간 복호화 권한을 부여합니다.<br>"
            "따라서 외부 프로그램이나 웹 에디터가 파일을 직접 읽으면 암호문으로만 확인됩니다."
        )
        g_desc.setTextFormat(Qt.TextFormat.RichText)
        g_desc.setStyleSheet("font-size: 11px; color: #78350F; line-height: 1.4;")
        g_desc.setWordWrap(True)
        guide_layout.addWidget(g_desc)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #FDE68A; margin: 2px 0;")
        guide_layout.addWidget(sep)

        sol_title = QLabel("📌 권장 등록 방법 (가장 빠르고 간편한 방법):")
        sol_title.setStyleSheet("font-size: 11px; font-weight: bold; color: #92400E;")
        guide_layout.addWidget(sol_title)

        sol_desc = QLabel(
            "1. <b>한글 프로그램</b>에서 해당 문서를 엽니다.<br>"
            "2. 본문 전체 선택(<b>Ctrl + A</b>) ➔ 복사(<b>Ctrl + C</b>)합니다.<br>"
            "3. 나라수첩 업무 편집기에 붙여넣기(<b>Ctrl + V</b>)하시면 표와 서식이 그대로 즉시 등록됩니다!<br>"
            "<span style='color: #B45309;'>(또는 사내 결재 시스템에서 '보안 해제(반출)' 승인 후 등록해 주세요.)</span>"
        )
        sol_desc.setTextFormat(Qt.TextFormat.RichText)
        sol_desc.setStyleSheet("font-size: 11px; color: #78350F; line-height: 1.4;")
        sol_desc.setWordWrap(True)
        guide_layout.addWidget(sol_desc)

        layout.addWidget(guide_box)

        # 하단 액션 버튼
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_attach = QPushButton("📁 원본 첨부파일로 보관하고 업무 등록")
        self.btn_attach.setFixedHeight(34)
        self.btn_attach.setToolTip("본문 변환 대신 원본 암호화 파일을 첨부파일 목록에 보관합니다. (더블클릭 시 한글 프로그램으로 열람 가능)")
        self.btn_attach.clicked.connect(self._on_attach_only)
        btn_row.addWidget(self.btn_attach)

        btn_row.addStretch(1)

        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setFixedHeight(34)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        layout.addLayout(btn_row)

    def _on_attach_only(self):
        self.action = "attach_only"
        self.accept()


class WorkDocumentImportDialog(QDialog):
    """외부 문서 파일 드래그앤드롭 또는 탐색기 우클릭 등록 시 표시되는 확인 및 분류 선택 모달"""

    def __init__(
        self,
        parent=None,
        file_path: str | Path = "",
        categories: list[str] = None,
        default_category: str = "",
        palette: dict | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("업무 등록")
        self.setFixedWidth(500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.path = Path(file_path)
        self.categories = [c for c in (categories or []) if c.strip()]
        if not self.categories:
            self.categories = ["일반 업무"]
        self.default_category = default_category if default_category in self.categories else self.categories[0]

        pal = palette or resolve_palette(False)
        bg = pal.get("bg", "#F8FAFC")
        panel = pal.get("panel", "#FFFFFF")
        text = pal.get("text", "#1E293B")
        text_muted = pal.get("text_muted", "#64748B")
        line = pal.get("line", "#E2E8F0")
        accent = pal.get("accent", "#0284C7")

        self.setStyleSheet(f"background-color: {bg}; color: {text}; font-family: {ui_font_family()};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # 상단 안내 카드 (이모티콘 없이 전체 폭을 활용하여 파일명과 전체 경로를 시원하게 표시)
        header_frame = QFrame()
        header_frame.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px;")
        info_layout = QVBoxLayout(header_frame)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(6)

        name_lbl = QLabel(self.path.name)
        name_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {text}; border: none;")
        name_lbl.setWordWrap(True)
        name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(name_lbl)

        full_path_str = str(self.path.resolve() if self.path.exists() else self.path)
        try:
            sz = self.path.stat().st_size
            sz_str = f"{sz / 1024 / 1024:.2f} MB" if sz >= 1024 * 1024 else f"{sz / 1024:.1f} KB"
            is_large = sz >= 5 * 1024 * 1024
        except Exception:
            sz_str = ""
            is_large = False

        meta_info = f"크기: {sz_str}  |  위치: {full_path_str}" if sz_str else full_path_str
        path_lbl = QLabel(meta_info)
        path_lbl.setStyleSheet(f"font-size: 11px; color: {text_muted}; border: none; line-height: 1.4;")
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(path_lbl)

        if is_large:
            large_hint = QLabel("⚡ 5MB 이상의 대용량 문서입니다. 등록 진행 시 단계별 진행 안내 창이 표시됩니다.")
            large_hint.setStyleSheet("font-size: 11px; color: #D97706; font-weight: 500; border: none;")
            info_layout.addWidget(large_hint)

        layout.addWidget(header_frame)

        form_frame = QFrame()
        form_frame.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px;")
        f_layout = QVBoxLayout(form_frame)
        f_layout.setContentsMargins(14, 14, 14, 14)
        f_layout.setSpacing(10)

        cat_lbl = QLabel("업무 분류:")
        cat_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text}; border: none;")
        f_layout.addWidget(cat_lbl)

        self.cat_combo = QComboBox()
        self.cat_combo.addItems(self.categories)
        self.cat_combo.setCurrentText(self.default_category)
        self.cat_combo.setFixedHeight(30)
        self.cat_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 4px;
                padding: 4px 8px;
                background-color: {panel};
                font-size: 12px;
            }}
        """)
        f_layout.addWidget(self.cat_combo)

        title_lbl = QLabel("업무 제목:")
        title_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text}; border: none;")
        f_layout.addWidget(title_lbl)

        self.title_input = QLineEdit()
        self.title_input.setText(self.path.stem)
        self.title_input.setFixedHeight(30)
        self.title_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {line};
                border-radius: 4px;
                padding: 4px 8px;
                background-color: {panel};
                font-size: 12px;
            }}
            QLineEdit:focus {{
                border: 1.5px solid {accent};
            }}
        """)
        f_layout.addWidget(self.title_input)

        self.check_copy = QCheckBox("원본 파일을 업무 첨부파일로도 함께 보관 (권장)")
        self.check_copy.setChecked(True)
        self.check_copy.setStyleSheet(f"font-size: 12px; color: {text}; border: none; margin-top: 4px;")
        f_layout.addWidget(self.check_copy)

        layout.addWidget(form_frame)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(32)
        btn_cancel.setFixedWidth(80)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #F1F5F9;
            }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("업무로 등록")
        btn_ok.setFixedHeight(32)
        btn_ok.setFixedWidth(100)
        btn_ok.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 4px;
                background-color: {accent};
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
            }}
        """)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def get_result(self) -> dict:
        return {
            "category": self.cat_combo.currentText().strip() or "일반 업무",
            "title": self.title_input.text().strip() or self.path.stem,
            "copy_attachment": self.check_copy.isChecked(),
        }


class WorkAttachmentChooserDialog(QDialog):
    """외부 파일을 기존 업무에 첨부파일로 연결할 때 업무를 선택하는 모달"""

    def __init__(
        self,
        parent=None,
        file_path: str | Path = "",
        sheets: list[WorkSheetData] = None,
        categories: list[str] = None,
        palette: dict | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("업무 첨부파일 등록")
        self.setFixedWidth(500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.path = Path(file_path)
        self.sheets = sheets or []
        self.categories = categories or []
        pal = palette or resolve_palette(False)
        bg = pal.get("bg", "#F8FAFC")
        panel = pal.get("panel", "#FFFFFF")
        text = pal.get("text", "#1E293B")
        text_muted = pal.get("text_muted", "#64748B")
        line = pal.get("line", "#E2E8F0")
        accent = pal.get("accent", "#0284C7")

        self.setStyleSheet(f"background-color: {bg}; color: {text}; font-family: {ui_font_family()};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # 상단 안내 카드 (이모티콘 없이 전체 폭을 활용하여 파일명과 전체 경로를 시원하게 표시)
        header_frame = QFrame()
        header_frame.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px;")
        info_layout = QVBoxLayout(header_frame)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(6)

        name_lbl = QLabel(self.path.name)
        name_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {text}; border: none;")
        name_lbl.setWordWrap(True)
        name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(name_lbl)

        full_path_str = str(self.path.resolve() if self.path.exists() else self.path)
        path_lbl = QLabel(full_path_str)
        path_lbl.setStyleSheet(f"font-size: 11px; color: {text_muted}; border: none; line-height: 1.4;")
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(path_lbl)

        layout.addWidget(header_frame)

        prompt_text = "이 폴더를 첨부할 대상 업무를 선택하세요:" if self.path.is_dir() else "이 파일을 첨부할 대상 업무를 선택하세요:"
        desc_lbl = QLabel(prompt_text)
        desc_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {text}; border: none;")
        layout.addWidget(desc_lbl)

        self.sheet_combo = QComboBox()
        self.sheet_combo.setFixedHeight(32)
        self.sheet_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {line};
                border-radius: 4px;
                padding: 4px 8px;
                background-color: {panel};
                font-size: 12px;
            }}
        """)

        # 분류(카테고리) 순서 및 가나다 순으로 완벽하게 정렬하여 표시
        cat_order = {c: i for i, c in enumerate(self.categories)}
        sorted_sheets = sorted(
            self.sheets,
            key=lambda s: (
                cat_order.get(s.category, 9999),
                str(s.category or ""),
                str(s.title or ""),
            )
        )
        for s in sorted_sheets:
            self.sheet_combo.addItem(f"[{s.category}] {s.title}", userData=s)
        self.sheet_combo.addItem("+ 새 업무 생성하여 첨부...", userData=None)
        layout.addWidget(self.sheet_combo)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_layout.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(32)
        btn_cancel.setFixedWidth(80)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: #F1F5F9;
            }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("첨부파일 등록")
        btn_ok.setFixedHeight(32)
        btn_ok.setFixedWidth(110)
        btn_ok.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 4px;
                background-color: {accent};
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
            }}
        """)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_ok)

        layout.addLayout(btn_layout)

    def get_selected_sheet(self) -> WorkSheetData | None:
        return self.sheet_combo.currentData()


class WorkContextMenuGuideDialog(QDialog):
    """
    업무 관리 처음 진입 시 윈도우 탐색기 우클릭 메뉴 연동 여부를 안내하고 선택받는 모달
    """

    def __init__(self, parent=None, palette: dict | None = None):
        super().__init__(parent)
        self.palette = palette or {}
        self.setWindowTitle("Windows 파일 탐색기 연동 안내")
        self.setFixedSize(490, 310)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._init_ui()

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        line = self.palette.get("line", "#CBD5E1")
        text = self.palette.get("text", "#0F172A")
        muted = self.palette.get("text_muted", "#64748B")
        accent = self.palette.get("accent", "#0284C7")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
            QLabel {{
                color: {text};
            }}
            QCheckBox {{
                color: {text};
                font-size: 11px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title_lbl = QLabel("💡 Windows 파일 탐색기 우클릭 연동")
        title_lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {accent};")
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(
            "윈도우 파일 탐색기에서 문서 파일(*.hwp, *.hwpx, *.pdf 등)이나 폴더를 마우스 우클릭하여 "
            "TaskCalendar 업무 및 첨부파일로 바로 등록할 수 있는 기능을 추가하시겠습니까?"
        )
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"font-size: 12px; line-height: 1.5; color: {text};")
        layout.addWidget(desc_lbl)

        card = QFrame()
        card.setStyleSheet(f"background-color: {panel_alt}; border: 1px solid {line}; border-radius: 6px; padding: 8px 12px;")
        card_l = QVBoxLayout(card)
        card_l.setSpacing(6)
        card_l.setContentsMargins(4, 4, 4, 4)

        m1 = QLabel("• <b>TaskCalendar 업무로 등록</b> : 문서 즉시 등록 또는 폴더 일괄 등록")
        m1.setStyleSheet("font-size: 11px;")
        card_l.addWidget(m1)

        m2 = QLabel("• <b>TaskCalendar 업무 첨부파일로 등록</b> : 원하는 업무에 첨부파일로 안전 보관")
        m2.setStyleSheet("font-size: 11px;")
        card_l.addWidget(m2)
        layout.addWidget(card)

        note_lbl = QLabel("※ 상단 [설정] 버튼 또는 [환경설정 > 업무]에서 언제든지 켜거나 끌 수 있습니다.")
        note_lbl.setStyleSheet(f"font-size: 11px; color: {muted};")
        layout.addWidget(note_lbl)

        layout.addStretch(1)

        bottom_h = QHBoxLayout()
        self.cb_dont_ask = QCheckBox("다시 묻지 않기")
        self.cb_dont_ask.setChecked(True)
        bottom_h.addWidget(self.cb_dont_ask)
        bottom_h.addStretch(1)

        btn_later = QPushButton("나중에 하기")
        btn_later.setFixedSize(86, 32)
        btn_later.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_later.clicked.connect(self.reject)
        bottom_h.addWidget(btn_later)

        btn_add = QPushButton("지금 추가하기")
        btn_add.setFixedSize(100, 32)
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                border: none;
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
            }}
        """)
        btn_add.clicked.connect(self.accept)
        bottom_h.addWidget(btn_add)

        layout.addLayout(bottom_h)

    def is_dont_ask_checked(self) -> bool:
        return self.cb_dont_ask.isChecked()


# ---------------------------------------------------------------------------
def is_inside_attachment_folder(p: Path | str) -> bool:
    """경로의 상위 폴더 중 하나가 '_첨부파일', '_files', '_attachments'로 끝나는지 확인"""
    path_obj = Path(p)
    for parent in path_obj.parents:
        name_lower = parent.name.lower()
        if (
            name_lower.endswith("_첨부파일")
            or name_lower.endswith("_files")
            or name_lower.endswith("_attachments")
        ):
            return True
    return False


def find_associated_attachment_dir(file_p: Path | str) -> Path | None:
    """업무 문서 파일에 대응하는 첨부파일 폴더가 인접하여 존재하는지 탐색"""
    p = Path(file_p)
    parent = p.parent
    candidates = [
        parent / f"{p.stem}_첨부파일",
        parent / f"{p.name}_첨부파일",
        parent / f"{p.stem}_files",
        parent / f"{p.stem}_attachments",
    ]
    for cand in candidates:
        if cand.exists() and cand.is_dir():
            return cand
    return None


# ---------------------------------------------------------------------------
# 불러오기 마법사 – 2단계: 폴더 또는 파일 불러오기
# ---------------------------------------------------------------------------
# 실제로 내용이 표시되는 확장자 목록 (extract_document_content 지원)
_IMPORT_VIEWABLE_EXTS = {
    ".hwp", ".hwpx",                      # 한글 – 원본 바이너리 그대로 표시
    ".txt", ".md", ".csv", ".json",       # 텍스트 계열 – 플레인 텍스트 표시
    ".xlsx", ".xls",                      # 엑셀 – 텍스트 추출
}
# 텍스트만 추출(미리보기 한계) 형식 – 등록은 되지만 경고 표시
_IMPORT_TEXT_ONLY_EXTS = {".pdf", ".docx", ".doc"}


class WorkImportWizardDialog(QDialog):
    """불러오기 2단계 마법사.
    Step 1: 방식 선택 (폴더 / 파일 선택)
    Step 2: 경로 선택 + 업무분류명 + 첨부파일 옵션
    """

    MODE_FOLDER = "folder"
    MODE_FILES = "files"

    def __init__(self, parent=None, categories: list[str] = None, palette: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("업무 불러오기 마법사")
        self.setFixedSize(540, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self.categories = categories or []
        pal = palette or resolve_palette(False)
        bg       = pal.get("bg",         "#F8FAFC")
        panel    = pal.get("panel",      "#FFFFFF")
        panel_alt = pal.get("panel_alt", "#F1F5F9")
        text     = pal.get("text",       "#1E293B")
        text_muted = pal.get("text_muted", "#64748B")
        line     = pal.get("line",       "#CBD5E1")
        accent   = pal.get("accent",     "#0284C7")
        self._pal = dict(bg=bg, panel=panel, panel_alt=panel_alt,
                         text=text, text_muted=text_muted, line=line, accent=accent)

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: {ui_font_family()};
            }}
            QLabel {{
                color: {text};
            }}
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
            QCheckBox {{
                color: {text};
                font-size: 12px;
                spacing: 6px;
            }}
        """)

        self._mode = self.MODE_FOLDER
        self._selected_folder: str = ""
        self._selected_files: list[str] = []

        self._step = 0
        self._init_ui()

    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        pal = self._pal
        bg, panel, panel_alt = pal["bg"], pal["panel"], pal["panel_alt"]
        text, text_muted, line, accent = pal["text"], pal["text_muted"], pal["line"], pal["accent"]

        _BTN_W, _BTN_H = 90, 32
        _btn_nav_style = f"""
            QPushButton {{
                background-color: #FFFFFF;
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                font-size: 12px;
                font-weight: normal;
                padding: 0px 8px;
                min-height: 32px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """
        _accent_ss = f"""
            QPushButton {{
                background-color: {accent};
                border: 1px solid {accent};
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
                padding: 0px 8px;
                min-height: 32px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
                border-color: #0274AD;
            }}
        """

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 20, 24, 20)
        main_layout.setSpacing(16)

        # 단계 표시 헤더
        self.lbl_step = QLabel()
        self.lbl_step.setStyleSheet(f"color: {accent}; font-size: 13px; font-weight: bold;")
        main_layout.addWidget(self.lbl_step)

        # 스택
        self.stacked = QStackedWidget()
        main_layout.addWidget(self.stacked, 1)

        # ---------------------------------------------------------------
        # STEP 1 – 방식 선택 (동그라미 없는 모던 카드형 선택)
        # ---------------------------------------------------------------
        step1 = QWidget()
        s1 = QVBoxLayout(step1)
        s1.setContentsMargins(0, 0, 0, 0)
        s1.setSpacing(10)

        lbl_hint = QLabel("가져올 방식을 선택하세요.")
        lbl_hint.setStyleSheet(f"color: {text_muted}; font-size: 12px;")
        s1.addWidget(lbl_hint)

        _rb_sel = f"""
            QRadioButton {{
                padding: 10px 16px;
                border: 2px solid {accent};
                border-radius: 6px;
                background-color: {panel_alt};
                color: {text};
                font-size: 12px;
                font-weight: bold;
            }}
            QRadioButton::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}
        """
        _rb_unsel = f"""
            QRadioButton {{
                padding: 10px 16px;
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
                font-weight: normal;
            }}
            QRadioButton:hover {{
                border-color: {accent};
                background-color: {panel_alt};
            }}
            QRadioButton::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}
        """

        self.rb_folder = QRadioButton(
            "폴더 불러오기  –  하위 파일을 업무 분류 및 문서로 일괄 등록"
        )
        self.rb_folder.setChecked(True)
        self.rb_files = QRadioButton(
            "파일 불러오기  –  문서 파일 1개 또는 여러 개를 업무로 등록"
        )
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.addButton(self.rb_folder)
        self._mode_btn_group.addButton(self.rb_files)

        def _upd_rb():
            self.rb_folder.setStyleSheet(_rb_sel if self.rb_folder.isChecked() else _rb_unsel)
            self.rb_files.setStyleSheet(_rb_sel if self.rb_files.isChecked() else _rb_unsel)
        self.rb_folder.toggled.connect(lambda _: _upd_rb())
        self.rb_files.toggled.connect(lambda _: _upd_rb())
        _upd_rb()

        s1.addWidget(self.rb_folder)
        s1.addWidget(self.rb_files)

        # 지원 형식 안내
        note_box = QFrame()
        note_box.setStyleSheet(f"background-color: {panel}; border-radius: 6px; padding: 10px;")
        note_lay = QVBoxLayout(note_box)
        note_lay.setContentsMargins(10, 8, 10, 8)
        note_lay.setSpacing(4)
        note_lbl = QLabel("<b>등록 가능한 문서 형식</b>")
        note_lbl.setStyleSheet(f"color: {accent}; font-size: 11px; border: none;")
        note_lay.addWidget(note_lbl)
        sup_exts = sorted(_IMPORT_VIEWABLE_EXTS | _IMPORT_TEXT_ONLY_EXTS)
        note_detail = QLabel(
            "내용 완전 표시:  " + "  ".join(sorted(_IMPORT_VIEWABLE_EXTS)) + "\n"
            "텍스트만 추출:  " + "  ".join(sorted(_IMPORT_TEXT_ONLY_EXTS)) + "  (내용은 등록되나 서식 손실)"
        )
        note_detail.setStyleSheet(f"color: {text_muted}; font-size: 11px; border: none;")
        note_lay.addWidget(note_detail)
        s1.addWidget(note_box)
        s1.addStretch(1)
        self.stacked.addWidget(step1)

        # ---------------------------------------------------------------
        # STEP 2 – 경로 선택 + 옵션
        # ---------------------------------------------------------------
        step2 = QWidget()
        s2 = QVBoxLayout(step2)
        s2.setContentsMargins(0, 0, 0, 0)
        s2.setSpacing(10)

        # 경로 행
        path_row = QHBoxLayout()
        self.lbl_path_caption = QLabel("폴더 경로:")
        self.lbl_path_caption.setStyleSheet(f"color: {text_muted}; font-size: 12px;")
        self.edit_path = QLineEdit()
        self.edit_path.setPlaceholderText("선택한 경로가 여기에 표시됩니다")
        self.edit_path.setReadOnly(True)
        self.edit_path.setFixedHeight(_BTN_H)
        self.edit_path.setStyleSheet(
            f"background-color: {panel_alt}; border: 1px solid {line}; "
            f"border-radius: 4px; padding: 4px 8px; color: {text};"
        )
        self.btn_pick_path = QPushButton("선택...")
        self.btn_pick_path.setFixedSize(_BTN_W, _BTN_H)
        self.btn_pick_path.setStyleSheet(
            f"QPushButton {{ border: 1px solid {line}; border-radius: 4px; "
            f"background-color: {panel}; color: {accent}; font-size: 12px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: {panel_alt}; }}"
        )
        self.btn_pick_path.clicked.connect(self._on_pick_path)
        path_row.addWidget(self.lbl_path_caption)
        path_row.addWidget(self.edit_path, 1)
        path_row.addWidget(self.btn_pick_path)
        s2.addLayout(path_row)

        # 파일 목록 (파일 모드 전용)
        self.file_list_frame = QFrame()
        self.file_list_frame.setStyleSheet(
            f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px;"
        )
        fl = QVBoxLayout(self.file_list_frame)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(4)
        fl_hdr = QHBoxLayout()
        self.lbl_file_count = QLabel("선택된 파일 없음")
        self.lbl_file_count.setStyleSheet(f"color: {text_muted}; font-size: 11px; border: none;")
        fl_hdr.addWidget(self.lbl_file_count)
        fl_hdr.addStretch(1)
        btn_clear = QPushButton("목록 지우기")
        btn_clear.setFixedHeight(22)
        btn_clear.setStyleSheet(f"border: none; color: {text_muted}; font-size: 11px; background: transparent;")
        btn_clear.clicked.connect(self._clear_files)
        fl_hdr.addWidget(btn_clear)
        fl.addLayout(fl_hdr)
        self.file_list_widget = QListWidget()
        self.file_list_widget.setFixedHeight(100)
        self.file_list_widget.setStyleSheet(
            f"border: none; background: transparent; color: {text}; font-size: 11px;"
        )
        fl.addWidget(self.file_list_widget)
        s2.addWidget(self.file_list_frame)

        # 업무분류명 (폴더 모드 전용)
        self.cat_frame = QFrame()
        cat_lay = QHBoxLayout(self.cat_frame)
        cat_lay.setContentsMargins(0, 0, 0, 0)
        cat_lay.addWidget(QLabel("등록될 최상위 업무 분류명:"))
        self.cat_input = QLineEdit()
        self.cat_input.setFixedHeight(_BTN_H)
        self.cat_input.setStyleSheet(
            f"background-color: {panel_alt}; border: 1px solid {line}; border-radius: 4px; padding: 4px 8px;"
        )
        cat_lay.addWidget(self.cat_input, 1)
        s2.addWidget(self.cat_frame)

        # 대상 업무분류 콤보 (파일 모드 전용)
        self.target_cat_frame = QFrame()
        tc_lay = QHBoxLayout(self.target_cat_frame)
        tc_lay.setContentsMargins(0, 0, 0, 0)
        tc_lay.addWidget(QLabel("등록 대상 업무분류:"))
        self.target_cat_combo = QComboBox()
        self.target_cat_combo.setFixedHeight(_BTN_H)
        for c in self.categories:
            self.target_cat_combo.addItem(c)
        if not self.categories:
            self.target_cat_combo.addItem("일반 업무")
        self.target_cat_combo.setStyleSheet(
            f"background-color: {panel_alt}; border: 1px solid {line}; border-radius: 4px; padding: 2px 8px;"
        )
        tc_lay.addWidget(self.target_cat_combo, 1)
        s2.addWidget(self.target_cat_frame)

        # 첨부파일 복사 옵션
        self.cb_copy_att = QCheckBox("원본 파일을 각 업무 문서의 첨부파일로도 자동 보관")
        self.cb_copy_att.setChecked(True)
        s2.addWidget(self.cb_copy_att)

        s2.addStretch(1)
        self.stacked.addWidget(step2)

        # ---------------------------------------------------------------
        # 하단 버튼 – 모두 세로 높이 32px 통일
        # ---------------------------------------------------------------
        nav = QHBoxLayout()
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setFixedSize(_BTN_W, _BTN_H)
        self.btn_cancel.setStyleSheet(_btn_nav_style)
        self.btn_cancel.clicked.connect(self.reject)
        nav.addWidget(self.btn_cancel)

        nav.addStretch(1)

        self.btn_prev = QPushButton("◀ 이전")
        self.btn_prev.setFixedSize(_BTN_W, _BTN_H)
        self.btn_prev.setStyleSheet(_btn_nav_style)
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_prev.hide()
        nav.addWidget(self.btn_prev)

        self.btn_next = QPushButton("다음 ▶")
        self.btn_next.setFixedSize(110, _BTN_H)
        self.btn_next.setStyleSheet(_accent_ss)
        self.btn_next.clicked.connect(self._go_next)
        nav.addWidget(self.btn_next)

        main_layout.addLayout(nav)

        self._go_to_step(0)

    # ------------------------------------------------------------------
    def _go_to_step(self, step: int) -> None:
        self._step = step
        self.stacked.setCurrentIndex(step)
        self.btn_prev.setVisible(step > 0)
        if step == 0:
            self.lbl_step.setText("1단계 / 2단계:  불러오기 방식 선택")
            self.btn_next.setText("다음 ▶")
        else:
            mode = self.MODE_FOLDER if self.rb_folder.isChecked() else self.MODE_FILES
            self._mode = mode
            self.lbl_step.setText("2단계 / 2단계:  파일 또는 폴더 선택 및 옵션")
            self.btn_next.setText("불러오기 실행")
            # 모드에 따라 위젯 표시 조정
            is_folder = (mode == self.MODE_FOLDER)
            self.lbl_path_caption.setText("폴더 경로:" if is_folder else "파일 경로:")
            self.file_list_frame.setVisible(not is_folder)
            self.cat_frame.setVisible(is_folder)
            self.target_cat_frame.setVisible(not is_folder)
            # 폴더 모드면 기본 분류명 초기화
            if is_folder and not self.cat_input.text():
                self.cat_input.setPlaceholderText("폴더명이 자동 입력됩니다")

    def _go_prev(self) -> None:
        self._go_to_step(self._step - 1)

    def _go_next(self) -> None:
        if self._step == 0:
            self._go_to_step(1)
        else:
            self._try_accept()

    # ------------------------------------------------------------------
    def _on_pick_path(self) -> None:
        if self._mode == self.MODE_FOLDER:
            d = QFileDialog.getExistingDirectory(self, "가져올 업무 폴더 선택", self._selected_folder or "")
            if d:
                self._selected_folder = d
                self.edit_path.setText(d)
                if not self.cat_input.text():
                    self.cat_input.setText(Path(d).name)
        else:
            all_exts = sorted(_IMPORT_VIEWABLE_EXTS | _IMPORT_TEXT_ONLY_EXTS)
            ext_filter = "지원 문서 (" + " ".join(f"*{e}" for e in all_exts) + ");;모든 파일 (*.*)"
            paths, _ = QFileDialog.getOpenFileNames(self, "업무로 등록할 문서 파일 선택", "", ext_filter)
            if paths:
                supported = _IMPORT_VIEWABLE_EXTS | _IMPORT_TEXT_ONLY_EXTS
                ok, bad = [], []
                for p in paths:
                    (ok if Path(p).suffix.lower() in supported else bad).append(p)
                if bad:
                    QMessageBox.warning(
                        self,
                        "지원되지 않는 파일",
                        "아래 파일은 내용을 표시할 수 없어 제외됩니다:\n"
                        + "\n".join(Path(b).name for b in bad),
                    )
                if ok:
                    self._selected_files.extend(ok)
                    self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_list_widget.clear()
        for p in self._selected_files:
            self.file_list_widget.addItem(Path(p).name)
        n = len(self._selected_files)
        self.lbl_file_count.setText(f"선택된 파일 {n}개" if n else "선택된 파일 없음")
        self.edit_path.setText(", ".join(Path(p).name for p in self._selected_files) if n else "")

    def _clear_files(self) -> None:
        self._selected_files.clear()
        self._refresh_file_list()

    # ------------------------------------------------------------------
    def _try_accept(self) -> None:
        if self._mode == self.MODE_FOLDER:
            if not self._selected_folder:
                QMessageBox.warning(self, "폴더 미선택", "가져올 폴더를 선택해주세요.")
                return
        else:
            if not self._selected_files:
                QMessageBox.warning(self, "파일 미선택", "가져올 파일을 1개 이상 선택해주세요.")
                return
        self.accept()

    # ------------------------------------------------------------------
    # 결과 조회
    # ------------------------------------------------------------------
    def get_mode(self) -> str:
        return self._mode

    def get_folder(self) -> str:
        return self._selected_folder

    def get_files(self) -> list[str]:
        return list(self._selected_files)

    def get_category_name(self) -> str:
        return self.cat_input.text().strip() or (Path(self._selected_folder).name if self._selected_folder else "")

    def get_target_category(self) -> str:
        return self.target_cat_combo.currentText()

    def get_copy_attachment(self) -> bool:
        return self.cb_copy_att.isChecked()


class FolderWorkImportDialog(QDialog):

    """
    폴더를 업무로 등록하기 전 사전 분석 모달:
    - 포함된 하위 폴더 수 및 파일 수 표시
    - 발견된 확장자별 파일 수 및 체크박스 필터 제공
    - 업무분류 명칭 및 첨부파일 보관 여부 설정
    """

    def __init__(self, parent=None, folder_path: str | Path = "", palette: dict | None = None):
        super().__init__(parent)
        self.folder_path = Path(folder_path)
        self.palette = palette or {}
        self.selected_extensions: set[str] = set()
        self.ext_checkboxes: dict[str, QCheckBox] = {}

        self.setWindowTitle("업무 폴더 분석 및 가져오기 설정")
        self.setMinimumWidth(500)
        self.setMinimumHeight(440)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._scan_folder()
        self._init_ui()

    def _scan_folder(self) -> None:
        self.subfolder_count = 0
        self.all_files: list[Path] = []
        self.att_files_count = 0
        if self.folder_path.exists() and self.folder_path.is_dir():
            for p in self.folder_path.rglob("*"):
                if p.is_dir():
                    if not is_inside_attachment_folder(p / "dummy"):
                        self.subfolder_count += 1
                elif p.is_file():
                    if is_inside_attachment_folder(p):
                        self.att_files_count += 1
                    else:
                        self.all_files.append(p)
        self.ext_counter = Counter(f.suffix.lower() or "(확장자 없음)" for f in self.all_files)

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        line = self.palette.get("line", "#CBD5E1")
        text = self.palette.get("text", "#0F172A")
        muted = self.palette.get("text_muted", "#64748B")
        accent = self.palette.get("accent", "#0284C7")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
            QLabel {{
                color: {text};
            }}
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
            QCheckBox {{
                color: {text};
                font-size: 12px;
                spacing: 6px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        # 1. 안내 헤더
        header_lbl = QLabel(f"📁 폴더 분석: <b>{html.escape(self.folder_path.name)}</b>")
        header_lbl.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {accent};")
        layout.addWidget(header_lbl)

        path_lbl = QLabel(str(self.folder_path))
        path_lbl.setStyleSheet(f"font-size: 11px; color: {muted}; margin-bottom: 4px;")
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        # 2. 통계 카드
        stats_frame = QFrame()
        stats_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 8px 12px;
            }}
        """)
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(8, 6, 8, 6)

        lbl_folders = QLabel(f"하위 폴더: <b>{self.subfolder_count}</b>개")
        file_msg = f"총 문서: <b>{len(self.all_files)}</b>개"
        if self.att_files_count > 0:
            file_msg += f" (연계 첨부파일 {self.att_files_count}개)"
        lbl_files = QLabel(file_msg)
        lbl_types = QLabel(f"파일 형식: <b>{len(self.ext_counter)}</b>종류")
        stats_layout.addWidget(lbl_folders)
        stats_layout.addWidget(lbl_files)
        stats_layout.addWidget(lbl_types)
        layout.addWidget(stats_frame)

        # 3. 확장자 선택 영역
        ext_header = QHBoxLayout()
        ext_header.addWidget(QLabel("등록할 파일 형식 선택:"))
        ext_header.addStretch(1)

        btn_select_all = QPushButton("전체 선택")
        btn_select_all.setFixedHeight(22)
        btn_select_all.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_select_all.clicked.connect(self._select_all_exts)
        ext_header.addWidget(btn_select_all)

        btn_select_docs = QPushButton("문서만 선택")
        btn_select_docs.setFixedHeight(22)
        btn_select_docs.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_select_docs.clicked.connect(self._select_doc_exts)
        ext_header.addWidget(btn_select_docs)

        btn_clear_all = QPushButton("전체 해제")
        btn_clear_all.setFixedHeight(22)
        btn_clear_all.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_clear_all.clicked.connect(self._clear_all_exts)
        ext_header.addWidget(btn_clear_all)
        layout.addLayout(ext_header)

        # 스크롤 영역에 확장자 체크박스들 배치
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px;")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(10, 8, 10, 8)
        scroll_layout.setSpacing(6)

        # extract_document_content()가 실제로 내용을 추출할 수 있는 확장자
        SUPPORTED_EXTS = {
            ".hwp", ".hwpx", ".pdf", ".docx", ".doc",
            ".txt", ".md", ".csv", ".json", ".xlsx", ".xls",
        }

        doc_exts = {".hwp", ".hwpx", ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".json"}
        sorted_exts = sorted(self.ext_counter.items(), key=lambda x: -x[1])
        for ext, count in sorted_exts:
            supported = ext in SUPPORTED_EXTS
            label = f"{ext} ({count}개 파일)" if supported else f"{ext} ({count}개 파일)  [지원 안 됨]"
            cb = QCheckBox(label)
            if supported:
                is_doc = ext in doc_exts
                cb.setChecked(is_doc or len(self.ext_counter) <= 3)
                cb.setEnabled(True)
            else:
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setStyleSheet("color: gray;")
            self.ext_checkboxes[ext] = cb
            scroll_layout.addWidget(cb)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 4. 업무분류 명칭 설정
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("등록될 최상위 업무 분류명:"))
        self.cat_input = QLineEdit(self.folder_path.name)
        self.cat_input.setFixedHeight(32)
        self.cat_input.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 4px 8px;")
        cat_layout.addWidget(self.cat_input)
        layout.addLayout(cat_layout)

        # 5. 첨부파일 자동 보관 체크박스
        self.cb_attachments = QCheckBox("📂 원본 파일들을 각 업무 문서의 첨부파일로도 자동 보관")
        self.cb_attachments.setChecked(True)
        layout.addWidget(self.cb_attachments)

        # 6. 하단 버튼 – 높이 32px 통일
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedSize(90, 32)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("업무 등록 시작")
        btn_ok.setFixedSize(110, 32)
        btn_ok.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 4px;
                background-color: {accent};
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
            }}
        """)
        btn_ok.clicked.connect(self._on_accept)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def _select_all_exts(self) -> None:
        for cb in self.ext_checkboxes.values():
            cb.setChecked(True)

    def _clear_all_exts(self) -> None:
        for cb in self.ext_checkboxes.values():
            cb.setChecked(False)

    def _select_doc_exts(self) -> None:
        SUPPORTED_EXTS = {
            ".hwp", ".hwpx", ".pdf", ".docx", ".doc",
            ".txt", ".md", ".csv", ".json", ".xlsx", ".xls",
        }
        for ext, cb in self.ext_checkboxes.items():
            if cb.isEnabled():
                cb.setChecked(ext in SUPPORTED_EXTS)

    def _on_accept(self) -> None:
        selected = {ext for ext, cb in self.ext_checkboxes.items() if cb.isChecked()}
        if not selected:
            QMessageBox.warning(self, "선택 오류", "최소 1개 이상의 파일 형식을 선택해주세요.")
            return
        self.selected_extensions = selected
        self.accept()

    def get_selected_extensions(self) -> set[str]:
        return self.selected_extensions

    def get_category_name(self) -> str:
        return self.cat_input.text().strip() or self.folder_path.name

    def should_copy_attachments(self) -> bool:
        return self.cb_attachments.isChecked()


def export_sheet_to_pdf(sheet: WorkSheetData, dest_path: Path | str) -> bool:
    """단위 업무 시트를 A4 정형 보고서 형태의 벡터 PDF로 내보내기"""
    try:
        dest_p = Path(dest_path)
        dest_p.parent.mkdir(parents=True, exist_ok=True)

        writer = QPdfWriter(str(dest_p))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setResolution(300)
        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)

        title = html.escape(sheet.title or "무제 업무")
        category = html.escape(sheet.category or "일반")
        assignee = html.escape(sheet.assignee or "-")
        cycle = html.escape(sheet.cycle or "-")
        deadline = html.escape(sheet.deadline or "-")

        body_content = ""
        if sheet.content_html and len(sheet.content_html.strip()) > 20:
            body_content = sheet.content_html
        else:
            txt = sheet.content_text or ""
            paragraphs = []
            for line in txt.splitlines():
                if line.strip():
                    paragraphs.append(f"<p style='margin: 4px 0; line-height: 1.6;'>{html.escape(line)}</p>")
                else:
                    paragraphs.append("<p style='margin: 4px 0;'>&nbsp;</p>")
            body_content = "".join(paragraphs) if paragraphs else "<p style='color: #94A3B8;'>(내용 없음)</p>"

        html_doc = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Malgun Gothic', sans-serif; font-size: 10.5pt; color: #1E293B; }}
                h1 {{ font-size: 18pt; color: #0F172A; border-bottom: 2px solid #0284C7; padding-bottom: 8px; margin-bottom: 14px; }}
                table.meta {{ width: 100%; border-collapse: collapse; margin-bottom: 18px; font-size: 9.5pt; }}
                table.meta th {{ background-color: #F1F5F9; border: 1px solid #CBD5E1; padding: 5px 8px; text-align: left; width: 15%; color: #475569; }}
                table.meta td {{ border: 1px solid #CBD5E1; padding: 5px 8px; width: 35%; color: #1E293B; }}
                div.content {{ font-size: 10.5pt; line-height: 1.6; margin-top: 10px; }}
            </style>
        </head>
        <body>
            <h1>{title}</h1>
            <table class="meta">
                <tr>
                    <th>업무 분류</th><td>{category}</td>
                    <th>담 당 자</th><td>{assignee}</td>
                </tr>
                <tr>
                    <th>업무 주기</th><td>{cycle}</td>
                    <th>마감 기한</th><td>{deadline}</td>
                </tr>
            </table>
            <div class="content">
                {body_content}
            </div>
        </body>
        </html>
        """
        doc = QTextDocument()
        doc.setHtml(html_doc)
        doc.print_(writer)
        return True
    except Exception as e:
        logger.exception("Failed to export sheet to PDF: %s", e)
        return False


def export_sheet_to_hwpx(sheet: WorkSheetData, dest_path: Path | str) -> bool:
    """단위 업무 시트를 한컴오피스 호환 HWPX 문서로 내보내기"""
    try:
        dest_p = Path(dest_path)
        dest_p.parent.mkdir(parents=True, exist_ok=True)

        # 1. 시트에 이미 HWPX 바이너리 blob이 존재하는 경우 그대로 저장
        if sheet.hwpx_blob:
            dest_p.write_bytes(sheet.hwpx_blob)
            return True

        # 2. hwpx 스킬 베이스 템플릿을 활용하여 완전한 HWPX 패키지 조립
        base_tmpl = Path(os.path.expanduser("~")) / ".gemini" / "config" / "skills" / "hwpx-skill" / "templates" / "base"
        if base_tmpl.exists() and (base_tmpl / "Contents" / "section0.xml").exists():
            import html

            def xml_escape(val: str) -> str:
                return html.escape(str(val or ""), quote=True).replace("'", "&apos;")

            text = sheet.content_text or sheet.title or ""
            p_xmls = []
            title_esc = xml_escape(sheet.title or "무제 업무")
            p_xmls.append(f'<hp:p id="1" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>[{title_esc}]</hp:t></hp:run></hp:p>')
            p_xmls.append(f'<hp:p id="2" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>분류: {xml_escape(sheet.category or "-")} | 담당: {xml_escape(sheet.assignee or "-")} | 주기: {xml_escape(sheet.cycle or "-")}</hp:t></hp:run></hp:p>')
            p_xmls.append('<hp:p id="3" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t></hp:t></hp:run></hp:p>')

            for idx, line in enumerate(text.splitlines(), start=10):
                line_esc = xml_escape(line)
                p_xmls.append(f'<hp:p id="{idx}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>{line_esc}</hp:t></hp:run></hp:p>')

            body_xml = "\n".join(p_xmls)

            sec0_path = base_tmpl / "Contents" / "section0.xml"
            sec0_text = sec0_path.read_text(encoding="utf-8")
            # <hs:sec ...> 태그 내부의 문단 교체
            sec_open = sec0_text.split("<hp:p")[0]
            # 기본 첫 번째 문단 안에 secPr(용지 설정) 보존
            if "</hp:secPr>" in sec0_text:
                sec_pr_part = sec0_text.split("</hp:secPr>")[0] + "</hp:secPr></hp:run></hp:p>"
                new_sec0 = f"{sec_pr_part}\n{body_xml}\n</hs:sec>"
            else:
                new_sec0 = f"{sec_open}\n{body_xml}\n</hs:sec>"

            with zipfile.ZipFile(dest_p, "w") as zf:
                # OCF 규격: mimetype 파일은 무압축(ZIP_STORED)
                mimetype_p = base_tmpl / "mimetype"
                if mimetype_p.exists():
                    zf.write(mimetype_p, "mimetype", compress_type=zipfile.ZIP_STORED)

                for item in base_tmpl.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(base_tmpl).as_posix()
                        if rel == "mimetype":
                            continue
                        if rel == "Contents/section0.xml":
                            zf.writestr(rel, new_sec0.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
                        else:
                            zf.write(item, rel, compress_type=zipfile.ZIP_DEFLATED)
            return True

        # 3. 폴백: 텍스트 파일 저장
        txt_path = dest_p.with_suffix(".txt")
        txt_content = f"[{sheet.title}]\n분류: {sheet.category} | 담당: {sheet.assignee} | 주기: {sheet.cycle}\n\n{sheet.content_text}"
        txt_path.write_text(txt_content, encoding="utf-8")
        return True
    except Exception as e:
        logger.exception("Failed to export sheet to HWPX: %s", e)
        return False


class WorkExportWizardDialog(QDialog):
    """
    2단계 내보내기 마법사 모달 다이얼로그:
    - 1단계: 업무 분류 및 문서 선택 (체크리스트 트리) + 첨부파일 포함 여부
    - 2단계: 내보내기 형식(HWPX / PDF), 대상 저장 폴더, 분류별 하위 폴더 생성 여부
    - 실행: QProgressDialog 연동 및 저장 폴더 열기 안내
    """

    def __init__(self, parent=None, sheets: list[WorkSheetData] | None = None, categories: list[dict] | None = None, palette: dict | None = None):
        super().__init__(parent)
        self.sheets = sheets or []
        self.categories = categories or []
        self.palette = palette or {}

        self.setWindowTitle("업무 및 편람 내보내기 마법사")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._init_ui()

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        line = self.palette.get("line", "#CBD5E1")
        text = self.palette.get("text", "#0F172A")
        muted = self.palette.get("text_muted", "#64748B")
        accent = self.palette.get("accent", "#0284C7")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
            }}
            QLabel {{
                color: {text};
            }}
            QPushButton {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                font-size: 12px;
                padding: 0px 8px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
            QTreeWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                color: {text};
                padding: 4px;
            }}
            QRadioButton, QCheckBox {{
                color: {text};
                font-size: 12px;
                spacing: 6px;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 상단 스텝 안내 바
        self.step_label = QLabel("<b>1단계 / 2단계:</b> 내보낼 업무 및 첨부파일 선택")
        self.step_label.setStyleSheet(f"font-size: 13px; color: {accent};")
        main_layout.addWidget(self.step_label)

        self.stacked = QStackedWidget()
        main_layout.addWidget(self.stacked, 1)

        # -------------------------------------------------------------
        # STEP 1: 업무 분류 및 문서 선택
        # -------------------------------------------------------------
        step1_widget = QWidget()
        s1_layout = QVBoxLayout(step1_widget)
        s1_layout.setContentsMargins(0, 0, 0, 0)
        s1_layout.setSpacing(10)

        s1_top = QHBoxLayout()
        s1_top.addWidget(QLabel("내보낼 업무 문서를 선택하세요:"))
        s1_top.addStretch(1)

        btn_check_all = QPushButton("전체 선택")
        btn_check_all.setFixedHeight(22)
        btn_check_all.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_check_all.clicked.connect(self._check_all_items)
        s1_top.addWidget(btn_check_all)

        btn_uncheck_all = QPushButton("전체 해제")
        btn_uncheck_all.setFixedHeight(22)
        btn_uncheck_all.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_uncheck_all.clicked.connect(self._uncheck_all_items)
        s1_top.addWidget(btn_uncheck_all)
        s1_layout.addLayout(s1_top)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self._populate_tree()
        s1_layout.addWidget(self.tree, 1)

        self.cb_include_attachments = QCheckBox("📎 첨부파일도 함께 내보내기 (문서별 첨부파일 폴더로 자동 구성)")
        self.cb_include_attachments.setChecked(True)
        s1_layout.addWidget(self.cb_include_attachments)
        self.stacked.addWidget(step1_widget)

        # -------------------------------------------------------------
        # STEP 2: 포맷 및 저장 경로 선택
        # -------------------------------------------------------------
        step2_widget = QWidget()
        s2_layout = QVBoxLayout(step2_widget)
        s2_layout.setContentsMargins(0, 0, 0, 0)
        s2_layout.setSpacing(14)

        # 포맷 그룹 (테두리 없음)
        format_group_box = QFrame()
        format_group_box.setStyleSheet(f"background-color: {panel}; border: none; border-radius: 6px; padding: 12px;")
        f_layout = QVBoxLayout(format_group_box)
        f_layout.setSpacing(6)
        lbl_f = QLabel("<b>내보내기 문서 형식 선택</b>")
        lbl_f.setStyleSheet(f"color: {accent}; font-size: 12px; border: none;")
        f_layout.addWidget(lbl_f)

        # 라디오버튼 – 선택 시 자신의 스타일로 직접 강조 (동그라미 없는 모던 카드형 선택)
        _rb_sel = f"""
            QRadioButton {{
                padding: 10px 16px;
                border: 2px solid {accent};
                border-radius: 6px;
                background-color: {panel_alt};
                color: {text};
                font-size: 12px;
                font-weight: bold;
            }}
            QRadioButton::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}
        """
        _rb_unsel = f"""
            QRadioButton {{
                padding: 10px 16px;
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel};
                color: {text};
                font-size: 12px;
                font-weight: normal;
            }}
            QRadioButton:hover {{
                border-color: {accent};
                background-color: {panel_alt};
            }}
            QRadioButton::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}
        """

        self.rb_hwpx = QRadioButton("한글 문서 (.hwpx / .hwp)  –  한글에서 바로 편집 가능한 정형 문서")
        self.rb_hwpx.setChecked(True)
        self.rb_pdf = QRadioButton("PDF 문서 (.pdf)  –  깔끔한 인쇄 및 배포용 A4 표준 문서")
        self.format_btn_group = QButtonGroup(self)
        self.format_btn_group.addButton(self.rb_hwpx)
        self.format_btn_group.addButton(self.rb_pdf)

        def _update_rb_style():
            self.rb_hwpx.setStyleSheet(_rb_sel if self.rb_hwpx.isChecked() else _rb_unsel)
            self.rb_pdf.setStyleSheet(_rb_sel if self.rb_pdf.isChecked() else _rb_unsel)

        self.rb_hwpx.toggled.connect(lambda _: _update_rb_style())
        self.rb_pdf.toggled.connect(lambda _: _update_rb_style())
        _update_rb_style()

        f_layout.addWidget(self.rb_hwpx)
        f_layout.addWidget(self.rb_pdf)
        s2_layout.addWidget(format_group_box)

        # 저장 폴더 그룹 (테두리 없음)
        dir_group_box = QFrame()
        dir_group_box.setStyleSheet(f"background-color: {panel}; border: none; border-radius: 6px; padding: 12px;")
        d_layout = QVBoxLayout(dir_group_box)
        d_layout.setSpacing(8)
        lbl_d = QLabel("<b>저장 대상 폴더 지정</b>")
        lbl_d.setStyleSheet(f"color: {accent}; font-size: 12px; border: none;")
        d_layout.addWidget(lbl_d)

        dir_h = QHBoxLayout()
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        self.dest_dir_input = QLineEdit(str(Path(default_dir) / "업무편람_내보내기"))
        self.dest_dir_input.setFixedHeight(32)
        self.dest_dir_input.setStyleSheet(f"background-color: {panel_alt}; border: 1px solid {line}; border-radius: 4px; padding: 4px 8px;")
        dir_h.addWidget(self.dest_dir_input, 1)

        btn_browse = QPushButton("찾아보기...")
        btn_browse.setFixedSize(90, 32)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel};
                color: {accent};
                font-size: 12px;
                padding: 0 8px;
                min-height: 32px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """)
        btn_browse.clicked.connect(self._on_browse_dest_dir)
        dir_h.addWidget(btn_browse)
        d_layout.addLayout(dir_h)

        self.cb_subfolders = QCheckBox("📁 업무 분류(카테고리)별로 하위 폴더를 생성하여 정리")
        self.cb_subfolders.setChecked(True)
        d_layout.addWidget(self.cb_subfolders)
        s2_layout.addWidget(dir_group_box)

        s2_layout.addStretch(1)
        self.stacked.addWidget(step2_widget)

        # -------------------------------------------------------------
        # 하단 탐색 버튼 – 모두 32px 높이 통일 및 일관된 스타일
        # -------------------------------------------------------------
        _BTN_W, _BTN_H = 90, 32
        _btn_nav_style = f"""
            QPushButton {{
                background-color: #FFFFFF;
                border: 1px solid {line};
                border-radius: 4px;
                color: {text};
                font-size: 12px;
                font-weight: normal;
                padding: 0px 8px;
                min-height: 32px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
            }}
        """
        _btn_accent_ss = f"""
            QPushButton {{
                background-color: {accent};
                border: 1px solid {accent};
                border-radius: 4px;
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
                padding: 0px 8px;
                min-height: 32px;
                max-height: 32px;
            }}
            QPushButton:hover {{
                background-color: #0274AD;
                border-color: #0274AD;
            }}
        """

        btn_nav_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setFixedSize(_BTN_W, _BTN_H)
        self.btn_cancel.setStyleSheet(_btn_nav_style)
        self.btn_cancel.clicked.connect(self.reject)
        btn_nav_layout.addWidget(self.btn_cancel)

        btn_nav_layout.addStretch(1)

        self.btn_prev = QPushButton("◀ 이전")
        self.btn_prev.setFixedSize(_BTN_W, _BTN_H)
        self.btn_prev.setStyleSheet(_btn_nav_style)
        self.btn_prev.clicked.connect(self._go_prev_step)
        self.btn_prev.hide()
        btn_nav_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("다음 ▶")
        self.btn_next.setFixedSize(110, _BTN_H)
        self.btn_next.setStyleSheet(_btn_accent_ss)
        self.btn_next.clicked.connect(self._go_next_step)
        nav = btn_nav_layout
        nav.addWidget(self.btn_next)

        main_layout.addLayout(btn_nav_layout)


    def _populate_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()

        cat_map: dict[str, QTreeWidgetItem] = {}
        for c in self.categories:
            c_name = c["name"]
            c_item = QTreeWidgetItem([f"📁 {c_name}"])
            c_item.setFlags(c_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            c_item.setCheckState(0, Qt.CheckState.Checked)
            font = c_item.font(0)
            font.setBold(True)
            c_item.setFont(0, font)
            cat_map[c_name] = c_item
            self.tree.addTopLevelItem(c_item)

        for s in self.sheets:
            c_name = s.category or "일반"
            if c_name not in cat_map:
                c_item = QTreeWidgetItem([f"📁 {c_name}"])
                c_item.setFlags(c_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                c_item.setCheckState(0, Qt.CheckState.Checked)
                font = c_item.font(0)
                font.setBold(True)
                c_item.setFont(0, font)
                cat_map[c_name] = c_item
                self.tree.addTopLevelItem(c_item)

            parent_item = cat_map[c_name]
            doc_item = QTreeWidgetItem([f"📄 {s.title}"])
            doc_item.setFlags(doc_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            doc_item.setCheckState(0, Qt.CheckState.Checked)
            doc_item.setData(0, Qt.ItemDataRole.UserRole, s)
            parent_item.addChild(doc_item)

        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        state = item.checkState(0)
        # 카테고리 체크 시 모든 자식 문서 일괄 반영
        if item.childCount() > 0:
            self.tree.blockSignals(True)
            for i in range(item.childCount()):
                item.child(i).setCheckState(0, state)
            self.tree.blockSignals(False)

    def _check_all_items(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Checked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False)

    def _uncheck_all_items(self) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Unchecked)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def _get_selected_sheets(self) -> list[WorkSheetData]:
        selected: list[WorkSheetData] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                ch = top.child(j)
                if ch.checkState(0) == Qt.CheckState.Checked:
                    s = ch.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(s, WorkSheetData):
                        selected.append(s)
        return selected

    def _go_next_step(self) -> None:
        if self.stacked.currentIndex() == 0:
            sheets = self._get_selected_sheets()
            if not sheets:
                QMessageBox.warning(self, "선택 오류", "내보낼 업무 문서를 1개 이상 선택해주세요.")
                return
            self.stacked.setCurrentIndex(1)
            self.step_label.setText("<b>2단계 / 2단계:</b> 내보내기 형식 및 저장 폴더 지정")
            self.btn_prev.show()
            self.btn_next.setText("내보내기 실행")
        else:
            self._execute_export()

    def _go_prev_step(self) -> None:
        if self.stacked.currentIndex() == 1:
            self.stacked.setCurrentIndex(0)
            self.step_label.setText("<b>1단계 / 2단계:</b> 내보낼 업무 및 첨부파일 선택")
            self.btn_prev.hide()
            self.btn_next.setText("다음 ▶")

    def _on_browse_dest_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.dest_dir_input.text())
        if d:
            self.dest_dir_input.setText(d)

    def _execute_export(self) -> None:
        dest_dir_str = self.dest_dir_input.text().strip()
        if not dest_dir_str:
            QMessageBox.warning(self, "입력 오류", "저장 대상 폴더를 지정해주세요.")
            return

        dest_root = Path(dest_dir_str)
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "폴더 오류", f"저장 폴더를 생성할 수 없습니다:\n{e}")
            return

        sheets = self._get_selected_sheets()
        total = len(sheets)
        is_hwpx = self.rb_hwpx.isChecked()
        include_attachments = self.cb_include_attachments.isChecked()
        use_subfolders = self.cb_subfolders.isChecked()

        progress = QProgressDialog("업무 문서를 내보내는 중입니다...", "취소", 0, total, self)
        progress.setWindowTitle("업무 내보내기 진행")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.resize(460, 130)
        progress.show()
        QApplication.processEvents()

        success_count = 0
        for i, s in enumerate(sheets):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"[{i + 1}/{total}] '{s.title}' 내보내는 중...")
            progress.setValue(i)
            QApplication.processEvents()

            # 대상 서브폴더 결정
            if use_subfolders:
                clean_cat = "".join(c for c in (s.category or "일반") if c not in r'\/:*?"<>|').strip() or "일반"
                target_dir = dest_root / clean_cat
            else:
                target_dir = dest_root
            target_dir.mkdir(parents=True, exist_ok=True)

            clean_title = "".join(c for c in (s.title or "무제") if c not in r'\/:*?"<>|').strip() or "무제"
            if is_hwpx:
                ext = ".hwp" if (s.title.lower().endswith(".hwp") and not s.title.lower().endswith(".hwpx")) else ".hwpx"
                target_file = target_dir / f"{clean_title}{ext}"
                ok = export_sheet_to_hwpx(s, target_file)
            else:
                target_file = target_dir / f"{clean_title}.pdf"
                ok = export_sheet_to_pdf(s, target_file)

            if ok:
                success_count += 1

            # 첨부파일 내보내기
            if include_attachments and s.attachments:
                att_dir = target_dir / f"{clean_title}_첨부파일"
                for att in s.attachments:
                    if att.get("type") == "folder" or att.get("file_type") == "folder":
                        continue
                    src_path = att.get("path")
                    if src_path and Path(src_path).exists():
                        subfolder = (att.get("folder_path") or "").strip().replace("\\", "/").strip("/")
                        att_target_dir = att_dir / subfolder if subfolder else att_dir
                        att_target_dir.mkdir(parents=True, exist_ok=True)
                        dest_att_file = att_target_dir / Path(src_path).name
                        try:
                            shutil.copy2(src_path, dest_att_file)
                        except Exception as e:
                            logger.warning(f"Failed to copy attachment during export: {e}")

        progress.setValue(total)
        self.accept()

        res = QMessageBox.information(
            self.parent(),
            "내보내기 완료",
            f"총 {success_count}건의 업무 문서가 성공적으로 내보내졌습니다.\n\n저장 경로:\n{dest_root}",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Ok,
        )
        if res == QMessageBox.StandardButton.Open:
            try:
                os.startfile(str(dest_root))
            except Exception:
                pass


class WorkDocSelectDialog(QDialog):
    """현재 업무에 연결할 문서(Task)를 검색하고 다중 선택하는 대화상자"""

    def __init__(
        self,
        parent: QWidget | None,
        repository: EncryptedRepository,
        initial_entry_ids: list[int] | None = None,
        palette: dict[str, str] | None = None,
        title: str = "연결할 관련 문서 선택",
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.palette = palette or {}
        self.selected_entry_ids: list[int] = list(initial_entry_ids or [])
        self.setWindowTitle(title)
        self.resize(520, 560)
        self.setMinimumSize(420, 420)
        bg = self.palette.get("bg", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        self.setStyleSheet(f"QDialog {{ background-color: {bg}; color: {text}; font-family: {font_family_css()}; }}")

        self._init_ui()
        self._populate_task_list()

    def _init_ui(self) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 상단 검색창
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  문서 제목, 분류, 기안자, 내용 검색...")
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
            QLineEdit:focus {{
                border-color: {accent};
            }}
        """)
        self.search_input.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_input)

        # 문서 목록 리스트 위젯 (체크박스)
        self.doc_list = QListWidget()
        self.doc_list.setStyleSheet(f"""
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
                height: 30px;
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
        layout.addWidget(self.doc_list, 1)

        # 빠른 선택 및 하단 액션 버튼
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        btn_all = QPushButton("전체 선택")
        btn_all.setFixedHeight(28)
        btn_all.setStyleSheet(f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 0 10px; font-size: 11px;")
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        bottom_row.addWidget(btn_all)

        btn_none = QPushButton("선택 해제")
        btn_none.setFixedHeight(28)
        btn_none.setStyleSheet(f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 0 10px; font-size: 11px;")
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        bottom_row.addWidget(btn_none)

        bottom_row.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedHeight(28)
        btn_cancel.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 0 14px; font-size: 12px;")
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)

        btn_ok = QPushButton("연결 확인")
        btn_ok.setFixedHeight(28)
        btn_ok.setStyleSheet(f"background-color: {accent}; color: #FFFFFF; border: 1px solid {accent}; border-radius: 6px; padding: 0 16px; font-size: 12px; font-weight: bold;")
        btn_ok.clicked.connect(self._on_accept)
        bottom_row.addWidget(btn_ok)

        layout.addLayout(bottom_row)

    def _populate_task_list(self) -> None:
        self.doc_list.clear()
        if not self.repository:
            return
        from taskcalendar.models import EntryType
        all_entries = self.repository.list_all_entries()
        tasks = [e for e in all_entries if e.entry_type == EntryType.TASK]
        # 최신 생성순 또는 날짜순 정렬
        tasks.sort(key=lambda t: (t.day or t.start_date or (t.created_at.date() if t.created_at else date.min)), reverse=True)

        for task in tasks:
            cat = task.memo_group or "일반"
            author = f" [{task.assignee}]" if task.assignee else ""
            d_str = ""
            if task.day or task.start_date:
                tgt_d = task.day or task.start_date
                d_str = f" ({tgt_d.strftime('%m.%d')})"

            display_txt = f"[{cat}] {task.title}{author}{d_str}"
            item = QListWidgetItem(f"📄 {display_txt}")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = task.entry_id in self.selected_entry_ids
            item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, task.entry_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, task)
            tip = f"제목: {task.title}\n분류: {cat}\n기안자: {task.assignee or '-'}\n일자: {task.day or task.start_date or '-'}\n내용: {task.description or '(없음)'}"
            item.setToolTip(tip)
            self.doc_list.addItem(item)

    def _filter_list(self, text: str) -> None:
        q = text.strip().lower()
        for i in range(self.doc_list.count()):
            item = self.doc_list.item(i)
            task = item.data(Qt.ItemDataRole.UserRole + 1)
            if not task:
                continue
            matched = (
                not q
                or (q in (task.title or "").lower())
                or (q in (task.description or "").lower())
                or (q in (task.assignee or "").lower())
                or (q in (task.memo_group or "").lower())
            )
            item.setHidden(not matched)

    def _set_all_checks(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.doc_list.count()):
            item = self.doc_list.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def _on_accept(self) -> None:
        self.selected_entry_ids = []
        for i in range(self.doc_list.count()):
            item = self.doc_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                eid = item.data(Qt.ItemDataRole.UserRole)
                if eid is not None:
                    self.selected_entry_ids.append(int(eid))
        self.accept()


class _CleanTreeProxyStyle(QProxyStyle):
    """트리 뷰의 OS 기본 포커스 사각 테두리 및 인디케이터 잔상을 깔끔하게 제거하고 드롭 인디케이터를 세련되게 렌더링하는 프록시 스타일"""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PrimitiveElement.PE_FrameFocusRect, QStyle.PrimitiveElement.PE_PanelItemViewRow):
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            if option.state & QStyle.State_Children:
                super().drawPrimitive(element, option, painter, widget)
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorItemViewItemDrop:
            # 드래그 앤 드롭 시 위치 표시 테두리/라인 커스텀
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = option.rect
            if rect.height() <= 4:
                # 항목 사이 삽입 라인: 깔끔한 블루 수평선
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#2563EB"))
                painter.drawRoundedRect(rect.adjusted(2, 0, -2, 0), 1, 1)
            else:
                # 폴더 위로 드롭 시: 부드러운 라운드 블루 테두리 및 반투명 배경
                painter.setPen(QPen(QColor("#3B82F6"), 1.5))
                painter.setBrush(QColor(59, 130, 246, 25))
                painter.drawRoundedRect(rect.adjusted(1, 1, -2, -1), 4, 4)
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)


class WorkSheetData:
    """단위 업무 시트 메모리 데이터 구조"""

    def __init__(
        self,
        sheet_id: str,
        title: str,
        category: str = "일반 업무",
        category_id: int | None = None,
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
        self.category_id = category_id
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
        self.is_dirty: bool = False


class WorkSheetTabBar(QTabBar):
    """
    엑셀/한글 스타일 하단 시트 탭 바
    - 탭 제목이 잘리지 않고 끝에 '...'으로 자연스럽게 표시되도록 ElideRight 지원
    - 닫기(X) 버튼과 텍스트가 겹치지 않도록 충분한 우측 여백 확보
    """

    def tabSizeHint(self, index: int) -> QSize:
        text = self.tabText(index)
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        # 좌측 여백(10px) + 텍스트 + 간격(4px) + 닫기버튼(18px) + 우측 여백(6px)
        needed_w = 10 + text_w + 4 + 18 + 6
        return QSize(max(needed_w, 100), 28)

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        self.alignCloseButtons()

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        self.alignCloseButtons()

    def alignCloseButtons(self) -> None:
        for i in range(self.count()):
            btn = self.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn:
                tr = self.tabRect(i)
                btn_w = 18
                btn_h = 18
                target_x = tr.right() - 8 - btn_w
                target_y = tr.y() + (tr.height() - btn_h) // 2
                btn.setGeometry(target_x, target_y, btn_w, btn_h)
                btn.setStyleSheet("background: transparent; border: none; outline: none;")


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
            theme_name = "light"
            if self.repository:
                theme_name = self.repository.get_setting("theme", "light")
            p = THEMES.get(theme_name, THEMES.get("light", {}))
        self.palette = p

        self.setWindowTitle("업무 관리 및 인수인계 편람")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1260, 780)
        self.setMinimumSize(880, 540)
        self.setAttribute(Qt.WA_StyledBackground, True)

        # 시트 및 카테고리 데이터
        self._categories: list[str] = []
        self._category_rows: list[dict] = []
        self._all_sheets: list[WorkSheetData] = []  # DB에 존재하는 전체 업무 목록 (좌측 트리에 표시)
        self._open_sheets: list[WorkSheetData] = []  # 현재 하단 탭에 열려있는 업무 목록
        self._active_sheet_index: int = -1
        self._is_loading_sheet: bool = False
        self._expanded_category_ids: set[int] = set()
        self._has_saved_expanded_ids: bool = False
        self._expanded_attachment_folders: set[str] = set()
        self._collapsed_attachment_folders: set[str] = set()

        # 업무 분류 하단 탭 관련
        self._category_tabs: list[dict] = []
        self._active_category_tab_id: int = 1
        self.cat_tab_scroll = None
        self.cat_tab_container = None
        self.cat_tab_layout = None

        self._load_data_from_db()
        self._init_ui()
        # 처음에 문서를 아무것도 띄우지 않음 (빈 상태 초기화)
        self._clear_editor_view()
        self._restore_window_state()
        self._first_show_prompt_done = False

    @property
    def _sheets(self) -> list[WorkSheetData]:
        """외부 및 하위 호환용: 현재 열려있는 시트 목록 반환"""
        return self._open_sheets

    def _load_category_tabs(self) -> None:
        """업무 분류 하단 탭 목록 및 활성 탭 로드"""
        self._category_tabs = []
        if self.repository:
            import json
            raw_tabs = self.repository.get_setting("work_category_tabs", "")
            if raw_tabs:
                try:
                    tabs_data = json.loads(raw_tabs)
                    if isinstance(tabs_data, list) and tabs_data:
                        self._category_tabs = [
                            {"id": int(t.get("id", 1)), "name": str(t.get("name", "1"))}
                            for t in tabs_data if isinstance(t, dict) and "id" in t
                        ]
                except Exception as e:
                    logger.warning(f"Failed to parse work_category_tabs: {e}")

            raw_active = self.repository.get_setting("work_active_category_tab_id", "1")
            try:
                self._active_category_tab_id = int(raw_active)
            except Exception:
                self._active_category_tab_id = 1

        if not self._category_tabs:
            self._category_tabs = [{"id": 1, "name": "1"}]
            self._active_category_tab_id = 1

        tab_ids = [t["id"] for t in self._category_tabs]
        if self._active_category_tab_id not in tab_ids:
            self._active_category_tab_id = tab_ids[0]

    def _save_category_tabs(self) -> None:
        """업무 분류 하단 탭 목록 및 활성 탭 저장"""
        if self.repository:
            import json
            try:
                self.repository.set_setting("work_category_tabs", json.dumps(self._category_tabs, ensure_ascii=False))
                self.repository.set_setting("work_active_category_tab_id", str(self._active_category_tab_id))
            except Exception as e:
                logger.warning(f"Failed to save work_category_tabs: {e}")

    def _load_categories_from_db(self) -> None:
        """카테고리 목록(계층 정보 포함) 동기화"""
        if self.repository:
            self._category_rows = self.repository.list_work_categories()
            self._categories = [c["name"] for c in self._category_rows]
        else:
            self._category_rows = []
            self._categories = []

    def _load_data_from_db(self) -> None:
        """SQLite DB에서 업무 분류 및 시트 데이터 로드"""
        self._load_category_tabs()
        if not self.repository:
            self._load_categories_from_db()
            self._all_sheets = []
            return

        self._load_categories_from_db()

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
                    "folder_path": a.get("folder_path", ""),
                    "file_type": a.get("file_type", ""),
                    "type": "folder" if a.get("file_type") == "folder" else "file",
                })
            sheet = WorkSheetData(
                db_id=r["id"],
                sheet_id=f"sheet_{r['id']}",
                title=r["title"],
                category=r.get("category_name") or (self._categories[0] if self._categories else "기본 분류"),
                category_id=r.get("category_id"),
                cycle=r.get("cycle", "수시"),
                assignee=r.get("assignee", ""),
                deadline=r.get("deadline", ""),
                content_html=r.get("content_html", ""),
                content_text=r.get("content_text", ""),
                hwpx_blob=r.get("hwpx_blob"),
                attachments=norm_atts,
            )
            self._all_sheets.append(sheet)

    def _tooltip_css(self) -> str:
        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        tip_bg = "#1E293B" if is_dark else "#FFFFFF"
        tip_fg = "#F8FAFC" if is_dark else "#0F172A"
        tip_border = "#475569" if is_dark else "#CBD5E1"
        return f"""
            QToolTip {{
                background-color: {tip_bg};
                color: {tip_fg};
                border: 1px solid {tip_border};
                border-radius: 6px;
                padding: 5px 9px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: 500;
            }}
        """

    def _apply_tooltip_palette(self) -> None:
        palette = QToolTip.palette()
        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        bg_col = QColor("#1E293B" if is_dark else "#FFFFFF")
        text_col = QColor("#F8FAFC" if is_dark else "#0F172A")
        for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
            palette.setColor(group, QPalette.ToolTipBase, bg_col)
            palette.setColor(group, QPalette.ToolTipText, text_col)
            palette.setColor(group, QPalette.Window, bg_col)
            palette.setColor(group, QPalette.WindowText, text_col)
        QToolTip.setPalette(palette)

    def _init_sample_data(self) -> None:
        """기본 샘플 업무 시트 구성 (빈 상태 유지)"""
        self._all_sheets = []

    def _init_ui(self) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")
        btn_text = self.palette.get("button_text", "#FFFFFF")
        muted = self.palette.get("text_muted", "#64748B")

        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        tip_bg = "#1E293B" if is_dark else "#FFFFFF"
        tip_fg = "#F8FAFC" if is_dark else "#0F172A"
        tip_border = "#475569" if is_dark else "#CBD5E1"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.palette.get("bg", "#F1F5F9")};
                color: {text};
                font-family: {font_family_css()};
            }}
            QSplitter::handle {{
                background-color: {line};
                width: 1px;
            }}
            QSplitter::handle:hover {{
                background-color: {accent};
                width: 3px;
            }}
            QToolTip {{
                background-color: {tip_bg};
                color: {tip_fg};
                border: 1px solid {tip_border};
                border-radius: 6px;
                padding: 6px 10px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: 500;
            }}
        """)
        self._apply_tooltip_palette()

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 10, 12, 8)
        self.main_layout.setSpacing(0)
        main_layout = self.main_layout

        # =========================================================================
        # 2. 탭 바: 검색창 바로 아래, 본문(스플리터) 상단에 딱 붙여 배치
        # =========================================================================
        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("tabBarContainer")
        self.bottom_bar.setFixedHeight(32)
        self.bottom_bar.setStyleSheet("""
            QFrame#tabBarContainer {
                background-color: transparent;
                border: none;
                padding: 0px 4px;
            }
        """)
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(4, 0, 4, 0)
        bottom_layout.setSpacing(6)

        self.sheet_tab_bar = WorkSheetTabBar()
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
                border-bottom: 1px solid {line};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                padding: 0px 4px 0px 10px;
                margin-top: 4px;
                margin-right: 3px;
                font-size: 12px;
                min-width: 90px;
                max-width: 360px;
                height: 27px;
            }}
            QTabBar::tab:selected {{
                background: {panel};
                color: {accent};
                font-weight: bold;
                border: 1px solid {line};
                border-bottom: 1px solid {panel};
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                margin-top: 2px;
                margin-bottom: -1px;
                height: 29px;
                padding: 0px 4px 0px 10px;
            }}
            QTabBar::tab:hover:!selected {{
                background: #FFFFFF;
                color: {text};
                border: 1px solid {line};
                border-bottom: 1px solid {line};
            }}
            QTabBar::close-button {{
                image: url('{str(asset_path("tab_close_red.svg")).replace("\\", "/")}');
                subcontrol-position: right;
                subcontrol-origin: padding;
                width: 16px;
                height: 16px;
                padding: 0px;
                margin-right: 6px;
                background: transparent;
                border: none;
                outline: none;
            }}
            QTabBar::close-button:hover {{
                background: transparent;
                border: none;
                outline: none;
            }}
        """)
        self.sheet_tab_bar.currentChanged.connect(self._on_sheet_tab_changed)
        self.sheet_tab_bar.tabCloseRequested.connect(self._on_sheet_tab_close)
        self.sheet_tab_bar.tabBarDoubleClicked.connect(self._on_sheet_tab_double_clicked)
        self.sheet_tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sheet_tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        bottom_layout.addWidget(self.sheet_tab_bar, 0, alignment=Qt.AlignmentFlag.AlignBottom)

        # 탭 영역과 우측 도구 영역 사이에 여백을 두고, [전체화면][템플릿] 묶음을
        # 좌측으로 당긴 뒤 [저장(S)] 을 창 우측 끝에 붙인다.
        bottom_layout.addStretch(1)

        # [전체화면][템플릿] 만 묶어서 왼쪽으로 당겨, 좌측 시작점을 우측 첨부파일
        # 카드 내부 시작점(테두리 1px + 여백 8px = x=9)에 맞춘다.
        # [저장(S)] 은 원래 우측 끝 위치를 유지한다.
        self._right_tools_group = QWidget()
        self._right_tools_group.setObjectName("wmRightToolsGroup")
        self._right_tools_group.setFixedHeight(32)
        self._right_tools_group.setStyleSheet(
            "#wmRightToolsGroup { background: transparent; border: none; }")
        _rtg = QHBoxLayout(self._right_tools_group)
        _rtg.setContentsMargins(0, 0, 0, 0)
        _rtg.setSpacing(6)

        # 탭 우측 끝 도구: [전체화면] [이미지 템플릿] [업무 템플릿] [저장(S)] (세로 크기 및 글꼴 크기 통일)
        self.btn_fullscreen = QPushButton("전체화면")
        self.btn_fullscreen.setFixedHeight(30)
        self.btn_fullscreen.setAutoDefault(False)
        self.btn_fullscreen.setDefault(False)
        self.btn_fullscreen.setToolTip("에디터 전체화면 토글 (단축키: Ctrl+Enter, F12)")
        self.btn_fullscreen.setStyleSheet(self._toolbar_sub_btn_style())
        self.btn_fullscreen.clicked.connect(self._toggle_editor_fullscreen)
        _rtg.addWidget(self.btn_fullscreen, 0, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.btn_template = QPushButton("템플릿")
        self.btn_template.setFixedHeight(30)
        self.btn_template.setAutoDefault(False)
        self.btn_template.setDefault(False)
        self.btn_template.setToolTip("템플릿 (이미지 템플릿, 업무 템플릿)")
        self.btn_template.setStyleSheet(self._toolbar_sub_btn_style())
        self.btn_template.clicked.connect(self._show_template_dropdown_menu)
        _rtg.addWidget(self.btn_template, 0, alignment=Qt.AlignmentFlag.AlignVCenter)

        # [전체화면][템플릿] 묶음만 왼쪽으로 당겨 좌측 시작점을
        # 우측 첨부파일 카드 내부 시작점(테두리 1px + 여백 8px = x=9)에 맞춘다.
        # [저장(S)] 은 뒤쪽에서 원래 우측 끝 위치를 그대로 유지한다.
        bottom_layout.addWidget(self._right_tools_group, 0,
                                alignment=Qt.AlignmentFlag.AlignVCenter)

        # [전체화면][템플릿] 묶음과 [저장(S)] 사이의 고정 여백.
        # 이 여백 덕분에 [저장(S)] 은 우측 끝에 붙어 원래 자리를 유지하고,
        # 앞쪽 [전체화면][템플릿] 만 77px 왼쪽으로 당겨진다.
        self._right_tools_mid_spacer = QWidget()
        self._right_tools_mid_spacer.setFixedWidth(77)
        self._right_tools_mid_spacer.setFixedHeight(32)
        self._right_tools_mid_spacer.setStyleSheet("background: transparent; border: none;")
        bottom_layout.addWidget(self._right_tools_mid_spacer, 0,
                                alignment=Qt.AlignmentFlag.AlignVCenter)

        self.btn_save_work = QPushButton("저장(S)")
        self.btn_save_work.setFixedHeight(30)
        self.btn_save_work.setAutoDefault(False)
        self.btn_save_work.setDefault(False)
        self.btn_save_work.setToolTip("현재 업무 문서 및 변경사항 저장 (Ctrl+S)")
        self.btn_save_work.setShortcut(QKeySequence("Ctrl+S"))
        self.btn_save_work.setStyleSheet(self._primary_btn_style())
        self.btn_save_work.clicked.connect(self._on_save_button_clicked)
        self._shortcut_save_alt = QShortcut(QKeySequence("Alt+S"), self)
        self._shortcut_save_alt.activated.connect(self._on_save_button_clicked)
        bottom_layout.addWidget(self.btn_save_work, 0, alignment=Qt.AlignmentFlag.AlignVCenter)

        main_layout.addWidget(self.bottom_bar)

        # 검색창 열기 단축키 (Ctrl+F)
        self._shortcut_search = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_search.activated.connect(self._open_search_bar)

        # 에디터 전체화면 단축키 (Ctrl+Enter, F12)
        self._shortcut_fullscreen_return = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._shortcut_fullscreen_return.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_fullscreen_return.activated.connect(self._toggle_editor_fullscreen)

        self._shortcut_fullscreen_enter = QShortcut(QKeySequence("Ctrl+Enter"), self)
        self._shortcut_fullscreen_enter.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_fullscreen_enter.activated.connect(self._toggle_editor_fullscreen)

        self._shortcut_fullscreen_f12 = QShortcut(QKeySequence("F12"), self)
        self._shortcut_fullscreen_f12.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_fullscreen_f12.activated.connect(self._toggle_editor_fullscreen)

        # =========================================================================
        # 3. 중간: 좌 / 중 / 우 3분할 스플리터 (초슬림 밀착형)
        # =========================================================================
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(2)

        # -------------------------------------------------------------------------
        # [중간 - 좌측 컨테이너]: 업무 분류 트리 (열림) + 슬림 바 (닫힘)
        # -------------------------------------------------------------------------
        self._left_expanded = True
        self._last_left_width = 320

        self.left_container = QWidget()
        self.left_container.setMinimumWidth(130)
        left_container_layout = QHBoxLayout(self.left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(0)

        # 1) 좌측 패널 (펼침 상태) - 카드 박스와 슬림 접기 버튼 밀착 배치
        self.left_panel = QWidget()
        self.left_panel.setObjectName("wmLeftPanel")
        self.left_panel.setStyleSheet("#wmLeftPanel { background: transparent; border: none; }")
        left_h_layout = QHBoxLayout(self.left_panel)
        left_h_layout.setContentsMargins(0, 0, 0, 0)
        left_h_layout.setSpacing(1)

        # 좌측 패널 본체 카드 박스
        self.left_card = QFrame()
        self.left_card.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        left_layout = QVBoxLayout(self.left_card)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.setSpacing(4)

        left_header = QHBoxLayout()
        left_header.setContentsMargins(2, 0, 2, 0)
        left_title = QLabel("업무 분류")
        left_title.setStyleSheet(f"font-weight: bold; font-size: 12px; color: {text}; border: none;")
        left_header.addWidget(left_title)
        left_header.addStretch(1)

        # 분류 버튼 왼쪽으로 새 업무 버튼 배치
        self.btn_new_work = QPushButton("+ 업무")
        self.btn_new_work.setFixedHeight(22)
        self.btn_new_work.setToolTip("새 업무(문서) 생성")
        self.btn_new_work.setStyleSheet(self._sub_btn_style())
        self.btn_new_work.clicked.connect(self._on_add_new_sheet)
        left_header.addWidget(self.btn_new_work)

        self.btn_add_cat = QPushButton("+ 분류")
        self.btn_add_cat.setFixedHeight(22)
        self.btn_add_cat.setToolTip("새 업무 분류(폴더) 추가")
        self.btn_add_cat.setStyleSheet(self._sub_btn_style())
        self.btn_add_cat.clicked.connect(self._on_add_category)
        left_header.addWidget(self.btn_add_cat)

        self.btn_reg_cal = QPushButton("일정등록")
        self.btn_reg_cal.setFixedHeight(22)
        self.btn_reg_cal.setStyleSheet(self._sub_btn_style())
        self.btn_reg_cal.setToolTip("선택한 업무 또는 폴더를 캘린더에 일정으로 등록합니다.")
        self.btn_reg_cal.clicked.connect(self._register_selected_to_calendar)
        left_header.addWidget(self.btn_reg_cal)

        left_layout.addLayout(left_header)

        # -------------------------------------------------------------------------
        # 검색 영역: 업무분류 바로 아랫줄 (검색창 + 우측 검색 버튼)
        # -------------------------------------------------------------------------
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(2, 2, 2, 2)
        search_layout.setSpacing(4)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("업무, 본문, 첨부 검색... (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(28)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 12px;
                color: {text};
            }}
            QLineEdit:focus {{
                background-color: {panel};
                border: 1.5px solid {accent};
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(lambda: self._on_search_text_changed(self.search_input.text()))
        search_layout.addWidget(self.search_input, 1)

        self.btn_search = QPushButton("검색")
        self.btn_search.setFixedHeight(28)
        self.btn_search.setFixedWidth(48)
        self.btn_search.setAutoDefault(False)
        self.btn_search.setDefault(False)
        self.btn_search.setStyleSheet(self._sub_btn_style())
        self.btn_search.clicked.connect(lambda: self._on_search_text_changed(self.search_input.text()))
        search_layout.addWidget(self.btn_search)

        left_layout.addLayout(search_layout)

        # 트리와 하단 탭을 간격 없이 하나의 복합 유닛으로 묶는 컨테이너 (엑셀 시트 탭 일체형 디자인)
        tree_tab_container = WorkCategoryTreeContainer()

        self.category_tree = CompactCategoryTree(tree_tab_container)
        # 델리게이트가 픽셀사이즈/굵기를 직접 지정하므로, 여기서는
        # 전역 렌더링 설정(안티에일리어싱·힌팅)만 물려받은 폰트를 쓴다.
        self.category_tree.setFont(make_ui_font_like(9))
        self.category_tree.setItemDelegate(CompactCategoryItemDelegate(self.category_tree))
        self.category_tree.setHeaderHidden(True)
        self.category_tree.setIndentation(14)
        self.category_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_tree.setRootIsDecorated(False)
        self.category_tree.setAnimated(False)
        self.category_tree.setStyleSheet(f"""
            QTreeWidget {{
                font-family: {font_family_css()};
                font-size: 12px;
                border: 1px solid {line};
                border-radius: 6px;
                background-color: {panel_alt};
                color: {text};
                padding: 2px 2px;
                outline: none;
            }}
            QTreeWidget::item {{
                font-family: {font_family_css()};
                font-size: 12px;
                height: 24px;
                padding: 0px 4px;
                margin: 1px 1px;
                border: none;
                background: transparent;
            }}
            QTreeWidget::branch {{
                background: transparent;
            }}
            QTreeWidget::drop-indicator {{
                background-color: #2563EB;
                height: 2px;
                border: none;
            }}
            {self._tooltip_css()}
        """)
        self.category_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.category_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self.category_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.category_tree.itemCollapsed.connect(self._on_tree_item_collapsed)
        self.category_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.category_tree.orderChanged.connect(self._on_tree_order_changed)
        self.category_tree.filesDropped.connect(self._on_tree_files_dropped)

        # 업무 분류 하단 탭 바 (1 | 2 | +) - 트리 하단선에 직결되는 엑셀 시트 탭 바
        self.cat_tab_scroll = QWidget(tree_tab_container)
        self.cat_tab_scroll.setObjectName("wmCatTabScroll")
        self.cat_tab_scroll.setFixedHeight(24)
        self.cat_tab_scroll.setStyleSheet("#wmCatTabScroll { background: transparent; border: none; }")

        self.cat_tab_layout = QHBoxLayout(self.cat_tab_scroll)
        self.cat_tab_layout.setContentsMargins(10, 0, 10, 0)
        self.cat_tab_layout.setSpacing(3)
        self.cat_tab_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        tree_tab_container.category_tree = self.category_tree
        tree_tab_container.cat_tab_scroll = self.cat_tab_scroll

        left_layout.addWidget(tree_tab_container, 1)

        self._render_category_tab_bar()

        # 업무 분류 하단: [불러오기] [내보내기] 버튼 2개 배치
        left_btn_row = QHBoxLayout()
        left_btn_row.setContentsMargins(0, 4, 0, 0)
        left_btn_row.setSpacing(6)

        self.btn_import = QPushButton("불러오기")
        self.btn_import.setFixedHeight(24)
        self.btn_import.setAutoDefault(False)
        self.btn_import.setDefault(False)
        self.btn_import.setToolTip("폴더 또는 파일을 업무로 불러오기")
        self.btn_import.setStyleSheet(self._sub_btn_style())
        self.btn_import.clicked.connect(self._on_import_menu)
        left_btn_row.addWidget(self.btn_import, 1)

        self.btn_export = QPushButton("내보내기")
        self.btn_export.setFixedHeight(24)
        self.btn_export.setAutoDefault(False)
        self.btn_export.setDefault(False)
        self.btn_export.setToolTip("업무 문서 및 첨부파일 내보내기 (HWPX / PDF)")
        self.btn_export.setStyleSheet(self._sub_btn_style())
        self.btn_export.clicked.connect(self._on_export_wizard)
        left_btn_row.addWidget(self.btn_export, 1)

        left_layout.addLayout(left_btn_row)
        left_h_layout.addWidget(self.left_card, 1)

        # 좌측 패널 우측 경계면 - 위아래 중간에 위치한 [◀] 버튼 거터
        left_gutter = QWidget()
        left_gutter_layout = QVBoxLayout(left_gutter)
        left_gutter_layout.setContentsMargins(0, 0, 0, 0)
        left_gutter_layout.setSpacing(0)
        left_gutter_layout.addStretch(1)

        self.btn_collapse_left = QPushButton("◀")
        self.btn_collapse_left.setToolTip("업무 분류 패널 접기 (◀)")
        self.btn_collapse_left.setFixedSize(11, 36)
        self.btn_collapse_left.setStyleSheet(self._gutter_arrow_style())
        self.btn_collapse_left.clicked.connect(self._collapse_left_panel)
        left_gutter_layout.addWidget(self.btn_collapse_left)

        left_gutter_layout.addStretch(1)
        left_h_layout.addWidget(left_gutter, 0)

        left_container_layout.addWidget(self.left_panel)

        # 2) 좌측 슬림 바 (접힘 상태) - 위아래 중간에 위치한 [▶] 버튼
        self.left_collapsed_bar = QFrame()
        self.left_collapsed_bar.setFixedWidth(14)
        self.left_collapsed_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.left_collapsed_bar.setToolTip("클릭하여 업무 분류 패널 펼치기 (▶)")
        self.left_collapsed_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
            }}
            QFrame:hover {{
                border-color: {accent};
                background-color: {accent_soft};
            }}
        """)
        left_col_layout = QVBoxLayout(self.left_collapsed_bar)
        left_col_layout.setContentsMargins(1, 4, 1, 4)
        left_col_layout.setSpacing(0)
        left_col_layout.addStretch(1)

        btn_expand_left = QPushButton("▶")
        btn_expand_left.setToolTip("업무 분류 패널 펼치기 (▶)")
        btn_expand_left.setFixedSize(11, 36)
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

        # 중앙 스택 위젯: 탭이 열려있을 때는 에디터, 모든 탭이 닫혔을 때는 빈 페이지 표시
        self.center_stack = QStackedWidget(self.center_panel)

        # 1) 본문 웹 에디터 페이지
        self.page_editor = QWidget()
        page_editor_layout = QVBoxLayout(self.page_editor)
        page_editor_layout.setContentsMargins(0, 0, 0, 0)
        page_editor_layout.setSpacing(0)
        self.editor = RhwpEditorWidget(self, palette=self.palette)
        self.editor.contentChanged.connect(self._on_editor_text_changed)
        page_editor_layout.addWidget(self.editor, 1)
        self.center_stack.addWidget(self.page_editor)

        # 2) 모든 탭이 닫혔을 때 표시할 빈 페이지
        self.page_empty = QWidget()
        page_empty_layout = QVBoxLayout(self.page_empty)
        page_empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_empty_layout.setContentsMargins(20, 40, 20, 40)
        page_empty_layout.setSpacing(12)

        lbl_empty_icon = QLabel("📄")
        lbl_empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_icon.setStyleSheet("font-size: 40px; border: none; background: transparent;")

        lbl_empty_title = QLabel("열려있는 업무 문서가 없습니다")
        lbl_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {text}; border: none; background: transparent;")

        lbl_empty_sub = QLabel("좌측 업무 분류에서 문서를 선택하거나 상단의 [새 업무] 버튼을 클릭하세요.")
        lbl_empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_empty_sub.setStyleSheet(f"font-size: 12px; color: {muted}; border: none; background: transparent;")

        page_empty_layout.addStretch(1)
        page_empty_layout.addWidget(lbl_empty_icon)
        page_empty_layout.addWidget(lbl_empty_title)
        page_empty_layout.addWidget(lbl_empty_sub)
        page_empty_layout.addStretch(1)
        self.center_stack.addWidget(self.page_empty)

        center_layout.addWidget(self.center_stack, 1)

        self.splitter.addWidget(self.center_panel)

        # -------------------------------------------------------------------------
        # [중간 - 우측 컨테이너]: 첨부파일 관리 (열림) + 슬림 바 (닫힘)
        # -------------------------------------------------------------------------
        self._right_expanded = True
        self._last_right_width = 320

        self.right_container = QWidget()
        self.right_container.setMinimumWidth(130)
        right_container_layout = QHBoxLayout(self.right_container)
        right_container_layout.setContentsMargins(0, 0, 0, 0)
        right_container_layout.setSpacing(0)

        # 1) 우측 슬림 바 (접힘 상태) - 위아래 중간에 위치한 [◀] 버튼
        self.right_collapsed_bar = QFrame()
        self.right_collapsed_bar.setFixedWidth(14)
        self.right_collapsed_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.right_collapsed_bar.setToolTip("클릭하여 첨부파일 패널 펼치기 (◀)")
        self.right_collapsed_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 4px;
            }}
            QFrame:hover {{
                border-color: {accent};
                background-color: {accent_soft};
            }}
        """)
        right_col_layout = QVBoxLayout(self.right_collapsed_bar)
        right_col_layout.setContentsMargins(1, 4, 1, 4)
        right_col_layout.setSpacing(0)
        right_col_layout.addStretch(1)

        btn_expand_right = QPushButton("◀")
        btn_expand_right.setToolTip("첨부파일 패널 펼치기 (◀)")
        btn_expand_right.setFixedSize(11, 36)
        btn_expand_right.setStyleSheet(self._gutter_arrow_style())
        btn_expand_right.clicked.connect(self._expand_right_panel)
        right_col_layout.addWidget(btn_expand_right, alignment=Qt.AlignmentFlag.AlignCenter)

        right_col_layout.addStretch(1)

        self.right_collapsed_bar.mousePressEvent = lambda e: self._expand_right_panel()
        self.right_collapsed_bar.hide()
        right_container_layout.addWidget(self.right_collapsed_bar)

        # 2) 우측 패널 (펼침 상태) - 카드 박스와 슬림 접기 버튼 밀착 배치
        self.right_panel = QWidget()
        self.right_panel.setObjectName("wmRightPanel")
        self.right_panel.setStyleSheet("#wmRightPanel { background: transparent; border: none; }")
        right_h_layout = QHBoxLayout(self.right_panel)
        right_h_layout.setContentsMargins(0, 0, 0, 0)
        right_h_layout.setSpacing(1)

        # 우측 패널 좌측 경계면 - 위아래 중간에 위치한 [▶] 버튼 거터
        right_gutter = QWidget()
        right_gutter_layout = QVBoxLayout(right_gutter)
        right_gutter_layout.setContentsMargins(0, 0, 0, 0)
        right_gutter_layout.setSpacing(0)
        right_gutter_layout.addStretch(1)

        self.btn_collapse_right = QPushButton("▶")
        self.btn_collapse_right.setToolTip("첨부파일 패널 접기 (▶)")
        self.btn_collapse_right.setFixedSize(11, 36)
        self.btn_collapse_right.setStyleSheet(self._gutter_arrow_style())
        self.btn_collapse_right.clicked.connect(self._collapse_right_panel)
        right_gutter_layout.addWidget(self.btn_collapse_right)

        right_gutter_layout.addStretch(1)
        right_h_layout.addWidget(right_gutter, 0)

        # 우측 패널 본체
        right_main_widget = QWidget()
        right_layout = QVBoxLayout(right_main_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.right_v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_v_splitter.setChildrenCollapsible(False)
        self.right_v_splitter.setHandleWidth(8)
        self.right_v_splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background-color: transparent;
                height: 8px;
            }
        """)

        # -------------------------------------------------------------
        # 1) 상단: 첨부파일 독립 카드 박스
        # -------------------------------------------------------------
        self.attach_card = QFrame()
        self.attach_card.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        attach_layout = QVBoxLayout(self.attach_card)
        attach_layout.setContentsMargins(8, 8, 8, 8)
        attach_layout.setSpacing(6)

        right_header = QHBoxLayout()
        self.right_title = QLabel("📎 첨부파일 (0)")
        self.right_title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {text}; border: none;")
        right_header.addWidget(self.right_title)
        right_header.addStretch(1)

        self.btn_add_folder = QPushButton("+ 폴더")
        self.btn_add_folder.setFixedHeight(22)
        self.btn_add_folder.setStyleSheet(self._sub_btn_style())
        self.btn_add_folder.clicked.connect(self._on_add_attachment_folder)
        right_header.addWidget(self.btn_add_folder)

        self.btn_add_file = QPushButton("+ 파일")
        self.btn_add_file.setFixedHeight(22)
        self.btn_add_file.setStyleSheet(self._sub_btn_style())
        self.btn_add_file.clicked.connect(self._on_add_attachment)
        right_header.addWidget(self.btn_add_file)
        attach_layout.addLayout(right_header)

        # 계층형 첨부파일 트리 위젯
        self.file_list = CompactAttachmentTree()
        self.file_list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._on_attachment_context_menu)
        self.file_list.deletePressed.connect(self._delete_selected_attachment)
        self.file_list.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                padding: 2px;
                outline: none;
            }}
            QTreeWidget::item {{
                height: 22px;
                padding: 0px 2px;
                margin: 1px 0px;
                border: none;
                border-radius: 3px;
            }}
            QTreeWidget::item:hover:!selected {{
                background-color: {accent_soft};
                color: {accent};
            }}
            QTreeWidget::item:selected {{
                background-color: {accent};
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                outline: none;
            }}
            {self._tooltip_css()}
        """)
        self.file_list.itemClicked.connect(self._on_attachment_item_clicked)
        self.file_list.itemDoubleClicked.connect(self._on_attachment_double_clicked)
        self.file_list.itemExpanded.connect(self._on_attachment_item_expanded)
        self.file_list.itemCollapsed.connect(self._on_attachment_item_collapsed)
        self.file_list.filesDropped.connect(self._on_attachments_dropped)
        self.file_list.orderChanged.connect(self._on_attachment_order_changed)
        attach_layout.addWidget(self.file_list, 1)

        # 하단 액션 버튼 (열기, 삭제)
        file_btn_row = QHBoxLayout()
        file_btn_row.setSpacing(6)

        self.btn_open_file = QPushButton("열기")
        self.btn_open_file.setFixedHeight(24)
        self.btn_open_file.setStyleSheet(self._sub_btn_style())
        self.btn_open_file.clicked.connect(self._open_selected_attachment)
        file_btn_row.addWidget(self.btn_open_file)

        self.btn_delete_file = QPushButton("삭제")
        self.btn_delete_file.setFixedHeight(24)
        self.btn_delete_file.setStyleSheet(self._danger_btn_style())
        self.btn_delete_file.clicked.connect(self._delete_selected_attachment)
        file_btn_row.addWidget(self.btn_delete_file)

        attach_layout.addLayout(file_btn_row)
        self.right_v_splitter.addWidget(self.attach_card)

        # -------------------------------------------------------------
        # 2) 하단: 관련 항목(일정 / 문서) 탭 카드 박스
        # -------------------------------------------------------------
        self.schedule_card = QFrame()
        self.schedule_card.setStyleSheet(f"""
            QFrame {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
            }}
        """)
        schedule_layout = QVBoxLayout(self.schedule_card)
        schedule_layout.setContentsMargins(8, 8, 8, 8)
        schedule_layout.setSpacing(6)

        # 상단 헤더: [📅 관련 일정 (0)] [📄 관련 문서 (0)] 탭 토글 + 우측 [+ 추가] 버튼
        sched_header = QHBoxLayout()
        sched_header.setSpacing(4)

        self._active_related_tab = 0  # 0: 일정, 1: 문서

        self.btn_tab_related_sched = QPushButton("일정 (0)")
        self.btn_tab_related_sched.setFixedHeight(22)
        self.btn_tab_related_sched.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_related_sched.clicked.connect(lambda: self._set_related_tab(0))
        sched_header.addWidget(self.btn_tab_related_sched)

        self.btn_tab_related_doc = QPushButton("문서 (0)")
        self.btn_tab_related_doc.setFixedHeight(22)
        self.btn_tab_related_doc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_tab_related_doc.clicked.connect(lambda: self._set_related_tab(1))
        sched_header.addWidget(self.btn_tab_related_doc)

        sched_header.addStretch(1)

        # 동적 추가 버튼 (+ 일정 <-> + 문서)
        self.btn_add_related = QPushButton("+ 일정")
        self.btn_add_related.setFixedHeight(22)
        self.btn_add_related.setStyleSheet(self._sub_btn_style())
        self.btn_add_related.clicked.connect(self._on_add_related_clicked)
        sched_header.addWidget(self.btn_add_related)
        schedule_layout.addLayout(sched_header)

        # QStackedWidget을 통해 관련 일정 리스트와 관련 문서 리스트 적재
        self.related_stack = QStackedWidget()

        # [페이지 0] 관련 일정 목록
        self.work_schedule_list = QListWidget()
        self.work_schedule_list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.work_schedule_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.work_schedule_list.customContextMenuRequested.connect(self._on_schedule_context_menu)
        self.work_schedule_list.itemDoubleClicked.connect(self._on_schedule_item_double_clicked)
        list_style = f"""
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
                height: 24px;
                padding: 2px 4px;
                margin: 1px 0px;
                border: none;
                border-radius: 3px;
            }}
            QListWidget::item:hover:!selected {{
                background-color: {accent_soft};
                color: {accent};
            }}
            QListWidget::item:selected {{
                background-color: {accent};
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                outline: none;
            }}
            {self._tooltip_css()}
        """
        self.work_schedule_list.setStyleSheet(list_style)
        self.related_stack.addWidget(self.work_schedule_list)

        # [페이지 1] 관련 문서 목록
        self.work_doc_list = QListWidget()
        self.work_doc_list.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.work_doc_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.work_doc_list.customContextMenuRequested.connect(self._on_doc_context_menu)
        self.work_doc_list.itemDoubleClicked.connect(self._on_doc_item_double_clicked)
        self.work_doc_list.setStyleSheet(list_style)
        self.related_stack.addWidget(self.work_doc_list)

        schedule_layout.addWidget(self.related_stack, 1)

        # 하단 액션 버튼 (이동, 수정, 삭제 / 연결 해제)
        sched_btn_row = QHBoxLayout()
        sched_btn_row.setSpacing(6)

        self.btn_goto_related = QPushButton("이동")
        self.btn_goto_related.setFixedHeight(24)
        self.btn_goto_related.setStyleSheet(self._sub_btn_style())
        self.btn_goto_related.clicked.connect(self._on_goto_related_clicked)
        sched_btn_row.addWidget(self.btn_goto_related)

        self.btn_edit_related = QPushButton("수정")
        self.btn_edit_related.setFixedHeight(24)
        self.btn_edit_related.setStyleSheet(self._sub_btn_style())
        self.btn_edit_related.clicked.connect(self._on_edit_related_clicked)
        sched_btn_row.addWidget(self.btn_edit_related)

        self.btn_delete_related = QPushButton("삭제")
        self.btn_delete_related.setFixedHeight(24)
        self.btn_delete_related.setStyleSheet(self._danger_btn_style())
        self.btn_delete_related.clicked.connect(self._on_delete_related_clicked)
        sched_btn_row.addWidget(self.btn_delete_related)

        schedule_layout.addLayout(sched_btn_row)
        self._update_related_tab_style()
        self.right_v_splitter.addWidget(self.schedule_card)

        self.right_v_splitter.setStretchFactor(0, 1)
        self.right_v_splitter.setStretchFactor(1, 1)
        self.right_v_splitter.setSizes([320, 260])

        right_layout.addWidget(self.right_v_splitter)
        right_h_layout.addWidget(right_main_widget, 1)

        right_container_layout.addWidget(self.right_panel)

        self.splitter.addWidget(self.right_container)

        # 스플리터 초기 비율 설정 (좌 320px : 중 잔여 : 우 320px 대칭)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([320, 640, 320])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        main_layout.addWidget(self.splitter, 1)



        self._refresh_category_combos()
        self._refresh_category_tree()
        self._refresh_sheet_tabs()

        # 편집 변경 감지 시그널 연결 (저장되지 않은 변경사항 추적)
        self.editor.contentChanged.connect(self._mark_active_sheet_dirty)
        if hasattr(self.editor, "fullscreenToggleRequested"):
            self.editor.fullscreenToggleRequested.connect(self._toggle_editor_fullscreen)

        # 모든 버튼의 autoDefault 및 default 비활성화 (검색창 등에서 엔터 시 의도치 않은 버튼 작동 원천 차단)
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
            btn.setDefault(False)

    def keyPressEvent(self, event):
        """다이얼로그 기본 동작인 Enter 시 accept() 차단 및 에디터 전체화면 단축키(Ctrl+Enter, F12) 지원"""
        # 1. F12 키: 에디터 전체화면 토글
        if event.key() == Qt.Key.Key_F12:
            self._toggle_editor_fullscreen()
            event.accept()
            return

        # 2. Ctrl + Enter / Return: 에디터 전체화면 토글
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._toggle_editor_fullscreen()
                event.accept()
                return
            if hasattr(self, "search_input") and self.search_input.hasFocus():
                self._on_search_text_changed(self.search_input.text())
                event.accept()
                return
            event.ignore()
            return

        # 3. Escape: 전체화면 중이면 전체화면 해제, 검색창 포커스 시 검색어 지우기
        if event.key() == Qt.Key.Key_Escape:
            if getattr(self, "_is_editor_fullscreen", False):
                self._set_editor_fullscreen(False)
                event.accept()
                return
            if hasattr(self, "search_input") and self.search_input.hasFocus():
                self.search_input.clear()
                self.search_input.clearFocus()
                event.accept()
                return
        super().keyPressEvent(event)

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
                background-color: transparent;
                color: {text};
                border: 1px solid transparent;
                border-radius: 2px;
                font-weight: bold;
                font-size: 8px;
                padding: 0;
            }}
            QPushButton:hover {{
                border-color: {line};
                color: {accent};
                background-color: {panel_alt};
            }}
            {self._tooltip_css()}
        """

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """사용자가 스플리터 구분선을 직접 드래그하여 패널 너비를 조절할 때 실시간 크기 기억"""
        sizes = self.splitter.sizes()
        if len(sizes) == 3:
            if self._left_expanded and sizes[0] > 60:
                self._last_left_width = sizes[0]
            if self._right_expanded and sizes[2] > 60:
                self._last_right_width = sizes[2]

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

    def _expand_left_panel(self) -> None:
        """좌측 업무 분류 패널 펼치기 (▶)"""
        if self._left_expanded:
            return
        self._left_expanded = True
        self.left_container.setMinimumWidth(130)
        self.left_container.setMaximumWidth(16777215)
        self.left_collapsed_bar.hide()
        self.left_panel.show()
        target_w = max(self._last_left_width, 320)
        current_sizes = self.splitter.sizes()
        diff = target_w - current_sizes[0]
        new_center = max(200, current_sizes[1] - diff)
        self.splitter.setSizes([target_w, new_center, current_sizes[2]])

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

    def _expand_right_panel(self) -> None:
        """우측 첨부파일 패널 펼치기 (◀)"""
        if self._right_expanded:
            return
        self._right_expanded = True
        self.right_container.setMinimumWidth(130)
        self.right_container.setMaximumWidth(16777215)
        self.right_collapsed_bar.hide()
        self.right_panel.show()
        target_w = max(self._last_right_width, 320)
        current_sizes = self.splitter.sizes()
        diff = target_w - current_sizes[2]
        new_center = max(200, current_sizes[1] - diff)
        self.splitter.setSizes([current_sizes[0], new_center, target_w])

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

    def _toggle_editor_fullscreen(self) -> None:
        """웹에디터 창 전체화면 토글 (좌우 패널 숨김/복원)"""
        self._set_editor_fullscreen(not getattr(self, "_is_editor_fullscreen", False))

    def _set_editor_fullscreen(self, fullscreen: bool) -> None:
        """웹에디터 창 전체화면 설정 / 해제 (여백 및 상단바 완전 제거)"""
        if getattr(self, "_is_editor_fullscreen", False) == fullscreen:
            return
        self._is_editor_fullscreen = fullscreen
        panel = self.palette.get("panel", "#FFFFFF")
        line = self.palette.get("line", "#CBD5E0")

        if fullscreen:
            # 이전 상태 저장
            self._pre_full_left_expanded = getattr(self, "_left_expanded", True)
            self._pre_full_right_expanded = getattr(self, "_right_expanded", True)
            sizes = self.splitter.sizes()
            if sizes:
                self._pre_full_splitter_sizes = sizes

            # 1. 상단 탭 및 도구바(전체화면, 템플릿, 저장 버튼) 완전 숨김
            if hasattr(self, "bottom_bar") and self.bottom_bar:
                self.bottom_bar.hide()

            # 2. 좌우 컨테이너 완전 숨김 (접힘 거터 바 포함)
            self.left_container.hide()
            self.right_container.hide()

            # 3. 창 전체 여백 및 테두리 완전히 제거 (에디터만 창에 100% 꽉 차게)
            if hasattr(self, "main_layout") and self.main_layout:
                self.main_layout.setContentsMargins(0, 0, 0, 0)
            if hasattr(self, "center_panel") and self.center_panel:
                self.center_panel.setStyleSheet("QFrame { background-color: #FFFFFF; border: none; border-radius: 0px; }")
            if hasattr(self, "splitter") and self.splitter:
                self.splitter.setHandleWidth(0)

            # 4. 안내 토스트 표시
            self.show_floating_toast("전체화면 모드 (단축키: F12, Ctrl+Enter 로 해제)")

            # 버튼 상태 업데이트
            if hasattr(self, "btn_fullscreen"):
                self.btn_fullscreen.setText("전체화면 해제")
                self.btn_fullscreen.setToolTip("에디터 전체화면 해제 (단축키: Ctrl+Enter, F12)")
                self.btn_fullscreen.setStyleSheet(self._primary_btn_style())
        else:
            # 1. 창 여백 및 중앙 패널 테두리 복원
            if hasattr(self, "main_layout") and self.main_layout:
                self.main_layout.setContentsMargins(12, 10, 12, 8)
            if hasattr(self, "center_panel") and self.center_panel:
                self.center_panel.setStyleSheet(f"""
                    QFrame {{
                        background-color: {panel};
                        border: 1px solid {line};
                        border-radius: 8px;
                    }}
                """)
            if hasattr(self, "splitter") and self.splitter:
                self.splitter.setHandleWidth(2)

            # 2. 상단 탭 및 도구바 복원
            if hasattr(self, "bottom_bar") and self.bottom_bar:
                self.bottom_bar.show()

            # 3. 좌우 컨테이너 표시
            self.left_container.show()
            self.right_container.show()

            # 4. 이전 펼침/접힘 상태로 복원
            if getattr(self, "_pre_full_left_expanded", True):
                self._left_expanded = False
                self._expand_left_panel()
            else:
                self._left_expanded = True
                self._collapse_left_panel()

            if getattr(self, "_pre_full_right_expanded", True):
                self._right_expanded = False
                self._expand_right_panel()
            else:
                self._right_expanded = True
                self._collapse_right_panel()

            if hasattr(self, "_pre_full_splitter_sizes") and self._pre_full_splitter_sizes:
                self.splitter.setSizes(self._pre_full_splitter_sizes)

            # 버튼 상태 복원
            if hasattr(self, "btn_fullscreen"):
                self.btn_fullscreen.setText("전체화면")
                self.btn_fullscreen.setToolTip("에디터 전체화면 토글 (단축키: Ctrl+Enter, F12)")
                self.btn_fullscreen.setStyleSheet(self._toolbar_sub_btn_style())

    def _get_tab_text(self, sheet: WorkSheetData) -> str:
        title = sheet.title or "새 업무"
        if len(title) > 30:
            title = title[:29] + "…"
        if sheet.is_dirty:
            return f"* {title}"
        return title

    def _update_tab_title(self, sheet: WorkSheetData) -> None:
        if sheet in self._open_sheets:
            idx = self._open_sheets.index(sheet)
            self.sheet_tab_bar.setTabText(idx, self._get_tab_text(sheet))
            self.sheet_tab_bar.setTabToolTip(idx, sheet.title)

    def _mark_active_sheet_dirty(self) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        sheet = self._open_sheets[self._active_sheet_index]
        if not sheet.is_dirty:
            sheet.is_dirty = True
            self._update_tab_title(sheet)

    def _save_sheet_sync(self, sheet: WorkSheetData) -> None:
        """시트 내용 동기 저장 (탭 닫기 또는 창 닫기 시)"""
        try:
            if sheet in self._open_sheets and self._open_sheets.index(sheet) == self._active_sheet_index:
                self._save_current_sheet_data()
                # 현재 에디터 내용 추출 후 저장
                loop = QEventLoop()
                def _on_exported(text: str, hwpx_bytes: bytes | None):
                    if text:
                        sheet.content_text = text
                    if hwpx_bytes:
                        sheet.hwpx_blob = hwpx_bytes
                    if loop.isRunning():
                        loop.quit()

                self.editor.export_document_data(_on_exported)
                QTimer.singleShot(800, lambda: loop.quit() if loop.isRunning() else None)
                loop.exec()

            if self.repository:
                sheet.db_id = self.repository.upsert_work_item(
                    work_id=sheet.db_id,
                    title=sheet.title,
                    category_name=sheet.category,
                    category_id=sheet.category_id,
                    cycle=sheet.cycle,
                    assignee=sheet.assignee,
                    deadline=sheet.deadline,
                    content_text=sheet.content_text,
                    content_html=sheet.content_html,
                    hwpx_blob=sheet.hwpx_blob,
                    sort_order=self._all_sheets.index(sheet) if sheet in self._all_sheets else 0,
                )
        except Exception as e:
            logger.exception("Failed to save sheet sync: %s", e)
        finally:
            sheet.is_dirty = False
            self._update_tab_title(sheet)

    def _reload_sheet_from_db(self, sheet: WorkSheetData) -> None:
        """시트의 미저장 변경 내용을 버리고 DB의 원래 저장된 데이터로 롤백"""
        if not self.repository or not sheet.db_id:
            # DB에 한 번도 저장되지 않은 신규 문서라면 목록에서 완전 제거
            if sheet in self._open_sheets:
                self._open_sheets.remove(sheet)
            if sheet in self._all_sheets:
                self._all_sheets.remove(sheet)
            return

        try:
            db_item = self.repository.get_work_item(sheet.db_id)
            if db_item:
                sheet.title = db_item.get("title", "") or "새 업무"
                sheet.category = db_item.get("category_name", "") or "일반 업무"
                sheet.category_id = db_item.get("category_id")
                sheet.cycle = db_item.get("cycle", "")
                sheet.assignee = db_item.get("assignee", "")
                sheet.deadline = db_item.get("deadline", "")
                sheet.content_text = db_item.get("content_text", "")
                sheet.content_html = db_item.get("content_html", "")
                sheet.hwpx_blob = db_item.get("hwpx_blob")
                sheet.attachments = db_item.get("attachments", [])
        except Exception as e:
            logger.warning("Failed to reload sheet %s from db: %s", sheet.db_id, e)
        sheet.is_dirty = False
        self._update_tab_title(sheet)

    def _refresh_sheet_tabs(self) -> None:
        """하단 엑셀 스타일 시트 탭 바 갱신 (열려있는 문서 탭만 표시)"""
        self.sheet_tab_bar.blockSignals(True)
        while self.sheet_tab_bar.count() > 0:
            self.sheet_tab_bar.removeTab(0)

        for sheet in self._open_sheets:
            idx = self.sheet_tab_bar.addTab(self._get_tab_text(sheet))
            self.sheet_tab_bar.setTabToolTip(idx, sheet.title)

        if 0 <= self._active_sheet_index < self.sheet_tab_bar.count():
            self.sheet_tab_bar.setCurrentIndex(self._active_sheet_index)

        self.sheet_tab_bar.blockSignals(False)

    def _switch_sheet_to_editor(self, target_index: int) -> None:
        """현재 편집 중인 시트의 최신 본문 데이터를 메모리에 동기화한 뒤 목표 시트로 전환"""
        if target_index < 0 or target_index >= len(self._open_sheets):
            return
        if self._active_sheet_index == target_index:
            return

        old_index = self._active_sheet_index
        if 0 <= old_index < len(self._open_sheets):
            old_sheet = self._open_sheets[old_index]
            self._save_current_sheet_data()

            def _after_export(text: str, hwpx_bytes: bytes | None):
                if text:
                    old_sheet.content_text = text
                if hwpx_bytes:
                    old_sheet.hwpx_blob = hwpx_bytes
                self._active_sheet_index = target_index
                self._load_sheet_to_editor(target_index)

            # 비동기로 본문 텍스트 및 HWPX 바이너리 보존 후 전환
            self.editor.export_document_data(_after_export)
        else:
            self._active_sheet_index = target_index
            self._load_sheet_to_editor(target_index)

    def _on_sheet_tab_changed(self, index: int) -> None:
        """하단 시트 탭 클릭 시 해당 업무 로드"""
        if index < 0 or index >= len(self._open_sheets):
            return
        if self._active_sheet_index == index:
            return
        self._switch_sheet_to_editor(index)

    def open_sheet(self, sheet: WorkSheetData) -> None:
        """문서를 하단 탭에 열고 중앙 에디터에 로드 (이미 열려있으면 해당 탭으로 전환)"""
        # 모든 탭이 닫혀있던 상태에서 새 문서가 열릴 때 에디터 페이지 복원 및 우측 패널 표시
        self.center_stack.setCurrentWidget(self.page_editor)
        self.right_container.show()
        if self._right_expanded:
            self.right_collapsed_bar.hide()
            self.right_panel.show()
            self.right_container.setMinimumWidth(130)
            self.right_container.setMaximumWidth(16777215)
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] < 150:
                target_w = max(self._last_right_width, 310)
                diff = target_w - sizes[2]
                new_center = max(200, sizes[1] - diff)
                self.splitter.setSizes([sizes[0], new_center, target_w])
        else:
            self.right_panel.hide()
            self.right_collapsed_bar.show()
            self.right_container.setFixedWidth(24)

        if sheet not in self._open_sheets:
            self._open_sheets.append(sheet)
            self.sheet_tab_bar.blockSignals(True)
            idx = self.sheet_tab_bar.addTab(self._get_tab_text(sheet))
            self.sheet_tab_bar.setTabToolTip(idx, sheet.title)
            self.sheet_tab_bar.blockSignals(False)

        tab_idx = self._open_sheets.index(sheet)
        if self._active_sheet_index == tab_idx:
            # 이미 현재 열려있는 탭이면 다시 로드하지 않음 (깜빡임 및 편집 내용 덮어쓰기 완전 방지)
            return

        self.sheet_tab_bar.blockSignals(True)
        self.sheet_tab_bar.setCurrentIndex(tab_idx)
        self.sheet_tab_bar.blockSignals(False)
        self._switch_sheet_to_editor(tab_idx)

    def _on_sheet_tab_close(self, index: int) -> None:
        """하단 시트 탭의 X 버튼 클릭 시 탭 닫기 (미저장 시 저장 여부 확인)"""
        if index < 0 or index >= len(self._open_sheets):
            return
        sheet = self._open_sheets[index]
        if sheet.is_dirty:
            # 먼저 해당 시트로 전환하여 확인
            if self._active_sheet_index != index:
                self.sheet_tab_bar.setCurrentIndex(index)
                self._active_sheet_index = index
                self._load_sheet_to_editor(index)

            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("저장되지 않은 변경사항")
            msg_box.setText(f"'{sheet.title}' 문서에 저장되지 않은 변경사항이 있습니다.\n\n닫기 전에 저장하시겠습니까?")
            btn_save = msg_box.addButton("저장(&S)", QMessageBox.ButtonRole.AcceptRole)
            btn_discard = msg_box.addButton("저장 안 함(&D)", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = msg_box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_save)
            msg_box.exec()

            clicked = msg_box.clickedButton()
            if clicked == btn_cancel:
                return  # 닫기 취소
            elif clicked == btn_save:
                self._save_sheet_sync(sheet)
            elif clicked == btn_discard:
                self._reload_sheet_from_db(sheet)

        self._open_sheets.pop(index)
        self.sheet_tab_bar.blockSignals(True)
        self.sheet_tab_bar.removeTab(index)
        self.sheet_tab_bar.blockSignals(False)

        if self._open_sheets:
            new_idx = max(0, min(self._active_sheet_index, len(self._open_sheets) - 1))
            self.sheet_tab_bar.blockSignals(True)
            self.sheet_tab_bar.setCurrentIndex(new_idx)
            self.sheet_tab_bar.blockSignals(False)
            self._active_sheet_index = new_idx
            self._load_sheet_to_editor(new_idx)
        else:
            self._active_sheet_index = -1
            self._clear_editor_view()

    def closeEvent(self, event):
        """다이얼로그 닫힐 때 미저장 시트 확인 및 창 상태 저장"""
        for sheet in list(self._open_sheets):
            if sheet.is_dirty:
                self.open_sheet(sheet)
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("저장되지 않은 변경사항")
                msg_box.setText(f"'{sheet.title}' 문서에 저장되지 않은 변경사항이 있습니다.\n\n창을 닫기 전에 저장하시겠습니까?")
                btn_save = msg_box.addButton("저장(&S)", QMessageBox.ButtonRole.AcceptRole)
                btn_discard = msg_box.addButton("저장 안 함(&D)", QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = msg_box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
                msg_box.setDefaultButton(btn_save)
                msg_box.exec()

                clicked = msg_box.clickedButton()
                if clicked == btn_cancel:
                    event.ignore()
                    return
                elif clicked == btn_save:
                    self._save_sheet_sync(sheet)
                elif clicked == btn_discard:
                    self._reload_sheet_from_db(sheet)

        self._save_window_state()
        event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous() and self.repository:
            self._load_categories_from_db()
            self._refresh_category_combos()
            self._refresh_category_tree()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._save_window_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        toast = getattr(self, "_active_toast", None)
        if toast is not None:
            try:
                import shiboken6
                if shiboken6.isValid(toast) and toast.isVisible():
                    toast._reposition()
                elif not shiboken6.isValid(toast):
                    self._active_toast = None
            except Exception:
                self._active_toast = None

    def show_floating_toast(self, message: str) -> None:
        """업무 관리창 정중앙에 스킨 색상에 맞춰 떴다가 자동으로 사라지는 플로팅 알림 표시"""
        old_toast = getattr(self, "_active_toast", None)
        if old_toast is not None:
            try:
                import shiboken6
                if shiboken6.isValid(old_toast):
                    old_toast.close()
            except Exception:
                pass
            self._active_toast = None
        new_toast = FloatingToastOverlay(self, message, self.palette, duration_ms=1200)
        self._active_toast = new_toast
        try:
            new_toast.destroyed.connect(lambda *_: setattr(self, "_active_toast", None))
        except Exception:
            pass
        new_toast.show()
        new_toast.raise_()

    def _restore_window_state(self) -> None:
        """이전 종료 시점의 창 위치, 크기, 좌우 패널 상태 및 폴더 펼침 상태 복원"""
        left_expanded = None
        right_expanded = None
        last_left_width = None
        last_right_width = None
        splitter_sizes = None
        expanded_cat_ids = None

        try:
            settings = QSettings("TaskCalendar", "WorkManager")
            geo = settings.value("geometry")
            if geo:
                self.restoreGeometry(geo)
            if settings.contains("left_expanded"):
                left_expanded = settings.value("left_expanded", type=bool)
            if settings.contains("right_expanded"):
                right_expanded = settings.value("right_expanded", type=bool)
            if settings.contains("last_left_width"):
                last_left_width = settings.value("last_left_width", type=int)
            if settings.contains("last_right_width"):
                last_right_width = settings.value("last_right_width", type=int)
            s_sizes = settings.value("splitter_sizes")
            if s_sizes and isinstance(s_sizes, (list, tuple)) and len(s_sizes) == 3:
                splitter_sizes = [int(s) for s in s_sizes]
            exp_ids = settings.value("expanded_category_ids")
            if exp_ids is not None and isinstance(exp_ids, (list, tuple)):
                expanded_cat_ids = [int(i) for i in exp_ids if str(i).lstrip("-").isdigit()]
        except Exception:
            pass

        if self.repository:
            try:
                import json
                raw = self.repository.get_setting("work_manager_layout_state", "")
                if not raw:
                    raw = self.repository.get_setting("work_manager_geometry", "")
                if raw:
                    data = json.loads(raw)
                    if not self.geometry().isValid() or self.width() <= 100:
                        w = data.get("w")
                        h = data.get("h")
                        x = data.get("x")
                        y = data.get("y")
                        if w and h:
                            self.resize(w, h)
                        if x is not None and y is not None:
                            self.move(x, y)
                        if data.get("maximized"):
                            self.showMaximized()
                    if left_expanded is None and "left_expanded" in data:
                        left_expanded = bool(data["left_expanded"])
                    if right_expanded is None and "right_expanded" in data:
                        right_expanded = bool(data["right_expanded"])
                    if last_left_width is None and "last_left_width" in data:
                        last_left_width = int(data["last_left_width"])
                    if last_right_width is None and "last_right_width" in data:
                        last_right_width = int(data["last_right_width"])
                    if splitter_sizes is None and "splitter_sizes" in data:
                        splitter_sizes = [int(s) for s in data["splitter_sizes"]]
                    if expanded_cat_ids is None and "expanded_category_ids" in data:
                        expanded_cat_ids = [int(i) for i in data["expanded_category_ids"]]
            except Exception:
                pass

        if last_left_width is not None and last_left_width > 60:
            self._last_left_width = last_left_width
        else:
            self._last_left_width = 320

        if last_right_width is not None and last_right_width > 60:
            self._last_right_width = last_right_width
        else:
            self._last_right_width = 320

        if expanded_cat_ids is not None:
            self._expanded_category_ids = set(expanded_cat_ids)
            self._has_saved_expanded_ids = True
            self._apply_folder_states_to_tree()

        # 좌우 패널 접힘/펼침 상태 복원
        if left_expanded is False:
            self._collapse_left_panel()
        elif left_expanded is True:
            self._expand_left_panel()

        if right_expanded is False:
            self._collapse_right_panel()
        elif right_expanded is True:
            self._expand_right_panel()

        if splitter_sizes and len(splitter_sizes) == 3 and sum(splitter_sizes) > 300:
            # 사용자가 변경했던 사이즈를 그대로 정확히 복원
            s0, s1, s2 = splitter_sizes
            if self._left_expanded and s0 < 60:
                s0 = self._last_left_width
            if self._right_expanded and s2 < 60:
                s2 = self._last_right_width
            self.splitter.setSizes([s0, s1, s2])
        else:
            # 처음 띄울 때 (저장된 설정이 없음):
            # 모든 버튼들이 다 보이는 초기 권장 크기 (좌 320px, 중앙 잔여, 우 320px)
            total_w = self.width() if self.width() > 800 else 1260
            center_w = max(400, total_w - 640)
            self.splitter.setSizes([320, center_w, 320])

    def _save_window_state(self) -> None:
        """창 위치, 크기, 좌우 패널 상태 및 폴더 펼침 상태 저장"""
        try:
            settings = QSettings("TaskCalendar", "WorkManager")
            settings.setValue("geometry", self.saveGeometry())
            settings.setValue("left_expanded", self._left_expanded)
            settings.setValue("right_expanded", self._right_expanded)
            settings.setValue("last_left_width", self._last_left_width)
            settings.setValue("last_right_width", self._last_right_width)
            settings.setValue("splitter_sizes", self.splitter.sizes())
            settings.setValue("expanded_category_ids", list(self._expanded_category_ids))
        except Exception:
            pass

        if self.repository:
            try:
                import json
                geo = self.normalGeometry() if self.isMaximized() else self.geometry()
                data = {
                    "x": int(geo.x()),
                    "y": int(geo.y()),
                    "w": int(geo.width()),
                    "h": int(geo.height()),
                    "maximized": self.isMaximized(),
                    "left_expanded": self._left_expanded,
                    "right_expanded": self._right_expanded,
                    "last_left_width": self._last_left_width,
                    "last_right_width": self._last_right_width,
                    "splitter_sizes": self.splitter.sizes(),
                    "expanded_category_ids": list(self._expanded_category_ids),
                }
                self.repository.set_setting("work_manager_layout_state", json.dumps(data))
                self.repository.save()
            except Exception:
                pass

    def _clear_editor_view(self) -> None:
        """열려있는 탭이 없을 때 에디터 입력 초기화 및 빈 페이지 표시"""
        self.work_title_input.setText("")
        self.meta_assignee_input.setText("")
        self.meta_deadline_input.setText("")
        self._refresh_attachments_list([])
        self._refresh_work_schedules_list(None)
        self.category_tree.blockSignals(True)
        self.category_tree.clearSelection()
        self.category_tree.blockSignals(False)
        self.center_stack.setCurrentWidget(self.page_empty)

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        """폴더 펼침 시 📂 아이콘 전환 및 상태 기록"""
        raw_name = item.data(0, Qt.UserRole + 2)
        if raw_name:
            item.setText(0, f"📂 {raw_name}")
            cat_id = item.data(0, Qt.UserRole + 1)
            if cat_id is not None:
                self._expanded_category_ids.add(int(cat_id))
                self._has_saved_expanded_ids = True

    def _on_tree_item_collapsed(self, item: QTreeWidgetItem) -> None:
        """폴더 접힘 시 📁 아이콘 전환 및 상태 기록"""
        raw_name = item.data(0, Qt.UserRole + 2)
        if raw_name:
            item.setText(0, f"📁 {raw_name}")
            cat_id = item.data(0, Qt.UserRole + 1)
            if cat_id is not None:
                self._expanded_category_ids.discard(int(cat_id))
                self._has_saved_expanded_ids = True

    def _apply_folder_states_to_tree(self) -> None:
        """기록된 _expanded_category_ids에 따라 트리 폴더 펼침/접힘 및 아이콘 일괄 적용"""
        self.category_tree.blockSignals(True)

        def apply_item(item: QTreeWidgetItem):
            cat_id = item.data(0, Qt.UserRole + 1)
            raw_name = item.data(0, Qt.UserRole + 2)
            if cat_id is not None and raw_name:
                expanded = int(cat_id) in self._expanded_category_ids
                item.setExpanded(expanded)
                icon = "📂" if expanded else "📁"
                item.setText(0, f"{icon} {raw_name}")
            for k in range(item.childCount()):
                apply_item(item.child(k))

        for i in range(self.category_tree.topLevelItemCount()):
            apply_item(self.category_tree.topLevelItem(i))

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
            self._update_tab_title(sheet)
            self._refresh_category_tree()

    def _on_tab_context_menu(self, pos: QPoint) -> None:
        """시트 탭 우클릭 컨텍스트 메뉴"""
        tab_idx = self.sheet_tab_bar.tabAt(pos)
        if tab_idx < 0 or tab_idx >= len(self._open_sheets):
            return
        target = self._open_sheets[tab_idx]
        menu = QMenu(self)
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 16px 6px 12px;
                border-radius: 4px;
                font-size: 12px;
            }}
            QMenu::item:selected {{
                background-color: #F1F5F9;
                color: #0284C7;
            }}
            QMenu::separator {{
                height: 1px;
                background: {line};
                margin: 4px 6px;
            }}
        """)
        act_save = menu.addAction("💾  저장")
        menu.addSeparator()
        act_close = menu.addAction("❌  탭 닫기")
        act_rename = menu.addAction("✏️  이름 바꾸기")
        act_duplicate = menu.addAction("📋  시트 복제")
        act_reg_cal = menu.addAction("📅  캘린더에 일정 등록")
        menu.addSeparator()
        act_delete = menu.addAction("🗑️  업무 삭제 (DB 영구 삭제)")

        action = menu.exec(self.sheet_tab_bar.mapToGlobal(pos))
        if action == act_save:
            # 해당 탭으로 전환 후 저장 수행
            if self._active_sheet_index != tab_idx:
                self.open_sheet(target)
            self._on_save_button_clicked()
        elif action == act_reg_cal:
            self._register_sheet_to_calendar(target)
        elif action == act_close:
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
            f"'{target.title}' 업무 문서를 영구히 삭제하시겠습니까?\n\n※ 작성된 매뉴얼 내용 및 보관된 모든 첨부파일이 디스크와 DB에서 영구 삭제됩니다.",
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

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "search_input") and self.search_input:
            self.search_input.clearFocus()
        if self._active_sheet_index >= 0:
            QTimer.singleShot(250, self._focus_editor)
        if not getattr(self, "_first_show_prompt_done", False):
            self._first_show_prompt_done = True
            QTimer.singleShot(350, self._check_first_time_context_menu_prompt)

    def _focus_editor(self) -> None:
        """문서 편집기로 포커스를 이동하여 즉시 타이핑할 수 있도록 함"""
        if hasattr(self, "search_input") and self.search_input:
            self.search_input.clearFocus()
        if hasattr(self, "editor") and self.editor:
            self.editor.setFocus()

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
        self.editor.load_document(
            title=sheet.title,
            text=sheet.content_text,
            hwpx_bytes=sheet.hwpx_blob,
        )

        # 우측 첨부파일 갱신
        self._refresh_attachments_list(sheet.attachments)

        # 우측 관련 일정 목록 갱신
        self._refresh_work_schedules_list(sheet)

        # 좌측 카테고리 트리 선택 동기화
        self._sync_tree_selection(index)

        # 검색창 포커스 해제 및 문서 편집기로 포커스 자동 전환
        if hasattr(self, "search_input") and self.search_input:
            self.search_input.clearFocus()
        QTimer.singleShot(250, self._focus_editor)

        # 로딩 플래그 해제 (약간의 딜레이를 주어 초기 텍스트 이벤트로 dirty 설정되는 것 방지)
        QTimer.singleShot(300, lambda: setattr(self, "_is_loading_sheet", False))

    def _sync_tree_selection(self, index: int) -> None:
        """좌측 카테고리 트리의 선택 항목을 현재 열린 시트와 동기화 (다단계 폴더 재귀 탐색)"""
        self.category_tree.blockSignals(True)
        if index < 0 or index >= len(self._open_sheets):
            self.category_tree.clearSelection()
            self.category_tree.blockSignals(False)
            return

        current_sheet = self._open_sheets[index]

        def find(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            item_sheet = item.data(0, Qt.UserRole)
            if isinstance(item_sheet, WorkSheetData) and (
                item_sheet is current_sheet
                or (current_sheet.db_id and getattr(item_sheet, "db_id", None) == current_sheet.db_id)
            ):
                return item
            for k in range(item.childCount()):
                found = find(item.child(k))
                if found is not None:
                    return found
            return None

        for i in range(self.category_tree.topLevelItemCount()):
            found = find(self.category_tree.topLevelItem(i))
            if found is not None:
                self.category_tree.setCurrentItem(found)
                break
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
            self.show_floating_toast("저장할 열린 문서가 없습니다.")
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
                        category_id=current.category_id,
                        cycle=current.cycle,
                        assignee=current.assignee,
                        deadline=current.deadline,
                        content_text=current.content_text,
                        content_html=current.content_html,
                        hwpx_blob=current.hwpx_blob,
                        sort_order=self._all_sheets.index(current) if current in self._all_sheets else 0,
                    )
                current.is_dirty = False
                self._update_tab_title(current)
                self._refresh_category_tree()
                self.show_floating_toast(f"'{current.title}' 저장 완료")

        self.editor.export_document_data(_after_export)

    def _show_template_dropdown_menu(self) -> None:
        """템플릿 통합 버튼 클릭 시 드롭다운 팝업 메뉴 표시 (이미지 템플릿, 업무 템플릿)"""
        from PySide6.QtWidgets import QMenu

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
                padding: 6px 18px 6px 12px;
                border-radius: 4px;
                color: #1E293B;
            }
            QMenu::item:selected {
                background-color: #F1F5F9;
                color: #0284C7;
                font-weight: bold;
            }
        """)

        action_img = menu.addAction("🖼️ 이미지 템플릿")
        action_img.setToolTip("공문서 직인·결재선·고무인 및 AI 서식 이미지 템플릿 불러오기")
        action_img.triggered.connect(self._show_image_template_dialog)

        action_work = menu.addAction("📑 업무 템플릿")
        action_work.setToolTip("공무원 필수 업무 서식 및 템플릿 불러오기")
        action_work.triggered.connect(self._show_template_menu)

        try:
            pos = self.btn_template.mapToGlobal(QPoint(0, self.btn_template.height() + 2))
            menu.exec(pos)
        except Exception:
            logger.exception("Failed to show template dropdown menu")

    def _show_template_menu(self) -> None:
        """공무원 필수 업무 서식 및 템플릿 메뉴 팝업"""
        from PySide6.QtWidgets import QMenu
        from taskcalendar.paths import runtime_root

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                font-size: 12px;
                color: #1E293B;
            }
            QMenu::item:selected {
                background-color: #EFF6FF;
                color: #1D4ED8;
                font-weight: 500;
            }
        """)

        from taskcalendar.paths import runtime_root, package_root
        import shutil

        tpl_dir = runtime_root() / "업무 템플릿"
        if not tpl_dir.exists():
            tpl_dir.mkdir(parents=True, exist_ok=True)

        # 단일 exe만 배포된 경우 내장된 번들 템플릿을 자동으로 추출(시딩)
        try:
            bundled_tpl = package_root().parent / "업무 템플릿"
            if bundled_tpl.exists() and bundled_tpl.is_dir() and not any(tpl_dir.iterdir()):
                for item in bundled_tpl.iterdir():
                    dst = tpl_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dst, dirs_exist_ok=True)
                    elif item.is_file():
                        shutil.copy2(item, dst)
        except Exception:
            pass

        categories = sorted([d for d in tpl_dir.iterdir() if d.is_dir()])
        for cat in categories:
            cat_name = cat.name.replace("_", " ")
            sub_menu = menu.addMenu(f"📁 {cat_name}")
            sub_menu.setStyleSheet(menu.styleSheet())
            seen_stems = set()
            files = []
            for f in sorted(cat.iterdir()):
                if f.is_file() and f.suffix.lower() in (".md", ".txt"):
                    if f.stem not in seen_stems:
                        seen_stems.add(f.stem)
                        files.append(f)
            for f in files:
                title = f.stem.replace("_", " ")
                act = sub_menu.addAction(title)
                act.triggered.connect(lambda _, fp=f: self._apply_template_file(fp))

        menu.addSeparator()
        act_open_folder = menu.addAction("📂 템플릿 폴더 열기...")
        act_open_folder.triggered.connect(lambda: os.startfile(str(tpl_dir)))

        menu.exec(self.btn_template.mapToGlobal(QPoint(0, self.btn_template.height() + 2)))

    def _show_image_template_dialog(self) -> None:
        """공문서 직인·결재선·고무인 및 AI 서식 이미지 템플릿 대화상자 표시"""
        from taskcalendar.image_template_dialog import ImageTemplateDialog

        active_editor = self.editor if hasattr(self, "editor") else self
        dlg = ImageTemplateDialog(parent_editor=active_editor, palette=self.palette)
        dlg.exec()

    def _apply_template_file(self, file_path: Path) -> None:
        """선택된 템플릿을 현재 에디터에 삽입 또는 새 문서로 적용"""
        try:
            content = file_path.read_text(encoding="utf-8")
            stem = file_path.stem.replace("_", " ")

            # 열려있는 시트가 없는 경우 새 업무로 자동 생성
            if self._active_sheet_index < 0 or not self._open_sheets:
                target_cat_name = self._categories[0] if self._categories else "1. 일반 업무"
                db_id = None
                if self.repository:
                    db_id = self.repository.upsert_work_item(
                        work_id=None,
                        title=stem,
                        category_name=target_cat_name,
                        cycle="수시",
                        content_text=content,
                        sort_order=len(self._all_sheets) + 1,
                    )
                new_sheet = WorkSheetData(
                    db_id=db_id,
                    sheet_id=f"sheet_{db_id or (len(self._all_sheets) + 1)}",
                    title=stem,
                    category=target_cat_name,
                    cycle="수시",
                    content_text=content,
                )
                self._all_sheets.append(new_sheet)
                self._open_sheets.append(new_sheet)
                self._active_sheet_index = len(self._open_sheets) - 1
                self._render_tab_strip()
                self._switch_to_sheet(new_sheet)
                self._refresh_category_tree()
                self.show_floating_toast(f"'{stem}' 서식으로 새 업무가 생성되었습니다.")
                return

            if hasattr(self, "editor") and hasattr(self.editor, "insert_text_at_cursor"):
                self.editor.insert_text_at_cursor(content)
                self.show_floating_toast(f"'{stem}' 서식이 적용되었습니다.")
        except Exception as e:
            logger.exception("Failed to apply template: %s", e)

    def _on_add_new_sheet(self) -> None:
        """선택된 폴더(또는 선택된 문서의 부모 폴더)에서 모달창으로 제목을 입력받아 새 업무 생성 및 탭 오픈"""
        self._save_current_sheet_data()
        target_cat_name = None
        target_cat_id = None

        selected = self.category_tree.selectedItems()
        if selected:
            sel_item = selected[0]
            sheet = sel_item.data(0, Qt.UserRole)
            if isinstance(sheet, WorkSheetData):
                target_cat_name = sheet.category
                target_cat_id = sheet.category_id
            else:
                target_cat_name = sel_item.data(0, Qt.UserRole + 2) or sel_item.text(0).replace("📁 ", "").replace("📂 ", "").strip()
                target_cat_id = sel_item.data(0, Qt.UserRole + 1)
        elif 0 <= self._active_sheet_index < len(self._open_sheets):
            curr = self._open_sheets[self._active_sheet_index]
            target_cat_name = curr.category
            target_cat_id = curr.category_id

        if not target_cat_name:
            if self._categories:
                target_cat_name = self._categories[0]
            else:
                target_cat_name = "1. 일반 업무"

        default_title = f"신규 단위업무 {len(self._all_sheets) + 1}"
        title, ok = QInputDialog.getText(
            self,
            "새 업무 생성",
            f"업무 분류: [{target_cat_name}]\n생성할 업무 제목을 입력하세요:",
            QLineEdit.Normal,
            default_title,
        )
        if not ok or not title.strip():
            return

        new_title = title.strip()
        db_id = None
        if self.repository:
            db_id = self.repository.upsert_work_item(
                work_id=None,
                title=new_title,
                category_name=target_cat_name,
                category_id=target_cat_id,
                cycle="수시",
                content_text="",
                sort_order=len(self._all_sheets) + 1,
            )
        new_sheet = WorkSheetData(
            db_id=db_id,
            sheet_id=f"sheet_{db_id or (len(self._all_sheets) + 1)}",
            title=new_title,
            category=target_cat_name,
            category_id=target_cat_id,
            cycle="수시",
            content_text="",
        )
        self._all_sheets.append(new_sheet)
        if self.repository:
            self._load_categories_from_db()
            self._refresh_category_combos()
        self._refresh_category_tree()
        self.open_sheet(new_sheet)

    # =========================================================================
    # 좌측 카테고리 트리 & 우측 첨부파일 관리
    # =========================================================================

    def _render_category_tab_bar(self) -> None:
        """업무 분류 하단 탭 바 (1 | 2 | +) 렌더링"""
        if not hasattr(self, "cat_tab_layout") or not self.cat_tab_layout:
            return

        # 기존 탭 위젯 모두 제거
        while self.cat_tab_layout.count():
            item = self.cat_tab_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        panel_alt_hover = _shade(panel_alt, -0.06)
        line = self.palette.get("line", "#CBD5E1")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#667085")
        accent = self.palette.get("accent", "#1F7A67")

        for tab_info in self._category_tabs:
            tab_id = tab_info["id"]
            tab_name = tab_info["name"]
            is_active = (tab_id == self._active_category_tab_id)

            btn = QPushButton(tab_name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"탭 '{tab_name}' (우클릭: 이름 변경 / 삭제)")
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, tid=tab_id, tname=tab_name, b=btn: self._show_category_tab_context_menu(pos, tid, tname, b)
            )

            # 클릭 여부와 관계없이 동일한 크기 유지 (크기 덜컥거림 완전 제거)
            btn.setFixedHeight(22)

            if is_active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {panel_alt};
                        color: {accent};
                        font-weight: bold;
                        font-size: 11px;
                        border: 1px solid {line};
                        border-top: none;
                        border-bottom-left-radius: 5px;
                        border-bottom-right-radius: 5px;
                        border-top-left-radius: 0px;
                        border-top-right-radius: 0px;
                        padding: 0px 10px;
                        min-width: 24px;
                        margin-top: 0px;
                        outline: none;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {panel_alt_hover};
                        color: {muted};
                        font-size: 11px;
                        font-weight: normal;
                        border: 1px solid {line};
                        border-bottom-left-radius: 5px;
                        border-bottom-right-radius: 5px;
                        border-top-left-radius: 0px;
                        border-top-right-radius: 0px;
                        padding: 0px 10px;
                        min-width: 24px;
                        margin-top: 0px;
                        outline: none;
                    }}
                    QPushButton:hover {{
                        background-color: {panel_alt};
                        color: {text};
                        border-color: {line};
                    }}
                """)

            btn.clicked.connect(lambda checked=False, tid=tab_id: self._set_active_category_tab(tid))
            self.cat_tab_layout.addWidget(btn)

        # '+' 추가 버튼 (엑셀 시트 탭 '+' 모양)
        btn_add = QPushButton("+")
        btn_add.setFixedHeight(20)
        btn_add.setFixedWidth(22)
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.setToolTip("새 분류 탭 추가")
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {muted};
                font-size: 12px;
                font-weight: bold;
                border: 1px dashed {line};
                border-bottom-left-radius: 5px;
                border-bottom-right-radius: 5px;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                padding: 0;
                margin-top: 0px;
                outline: none;
            }}
            QPushButton:hover {{
                background-color: {panel_alt_hover};
                color: {accent};
                border-color: {accent};
            }}
        """)
        btn_add.clicked.connect(self._on_add_category_tab)
        self.cat_tab_layout.addWidget(btn_add)
        self.cat_tab_layout.addStretch(1)

        if hasattr(self, "cat_tab_scroll") and self.cat_tab_scroll:
            self.cat_tab_scroll.raise_()

    def _set_active_category_tab(self, tab_id: int) -> None:
        """업무 분류 탭 전환"""
        if self._active_category_tab_id == tab_id:
            return
        self._active_category_tab_id = tab_id
        self._save_category_tabs()
        self._render_category_tab_bar()
        self._refresh_category_combos()
        self._refresh_category_tree()

    def _on_add_category_tab(self) -> None:
        """새 분류 탭 추가 (+ 누르면 번호가 계속 생성됨)"""
        existing_ids = [t["id"] for t in self._category_tabs]
        new_id = (max(existing_ids) + 1) if existing_ids else 1

        existing_names = {t["name"] for t in self._category_tabs}
        cand_num = 1
        while str(cand_num) in existing_names:
            cand_num += 1

        new_tab_name = str(cand_num)
        self._category_tabs.append({"id": new_id, "name": new_tab_name})
        self._active_category_tab_id = new_id
        self._save_category_tabs()
        self._render_category_tab_bar()
        self._refresh_category_combos()
        self._refresh_category_tree()

    def _show_category_tab_context_menu(self, pos: QPoint, tab_id: int, tab_name: str, btn: QPushButton) -> None:
        """분류 탭 우클릭 컨텍스트 메뉴"""
        menu = QMenu(self)
        act_rename = menu.addAction("✏️ 이름 바꾸기")
        act_delete = menu.addAction("🗑️ 탭 삭제")

        action = menu.exec(btn.mapToGlobal(pos))
        if action == act_rename:
            new_name, ok = QInputDialog.getText(self, "탭 이름 변경", "새 탭 이름:", text=tab_name)
            if ok and new_name.strip() and new_name.strip() != tab_name:
                for t in self._category_tabs:
                    if t["id"] == tab_id:
                        t["name"] = new_name.strip()
                        break
                self._save_category_tabs()
                self._render_category_tab_bar()
        elif action == act_delete:
            self._on_delete_category_tab(tab_id, tab_name)

    def _on_delete_category_tab(self, tab_id: int, tab_name: str) -> None:
        """분류 탭 삭제"""
        if len(self._category_tabs) <= 1:
            QMessageBox.warning(self, "안내", "최소 1개의 탭은 유지되어야 하므로 삭제할 수 없습니다.")
            return

        cats_in_tab = [c for c in self._category_rows if c.get("tab_id", 1) == tab_id and not c.get("parent_id")]
        remaining_tabs = [t for t in self._category_tabs if t["id"] != tab_id]
        target_tab = remaining_tabs[0]

        if cats_in_tab:
            reply = QMessageBox.question(
                self,
                "탭 삭제 확인",
                f"'{tab_name}' 탭에 등록된 업무 분류가 {len(cats_in_tab)}개 있습니다.\n"
                f"탭을 삭제하면 소속 분류는 '{target_tab['name']}' 탭으로 이동됩니다.\n\n"
                f"삭제하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            if self.repository:
                for c in cats_in_tab:
                    self.repository.update_work_category_tab(c["id"], target_tab["id"])
        else:
            reply = QMessageBox.question(
                self,
                "탭 삭제 확인",
                f"'{tab_name}' 탭을 삭제하시겠습니까?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._category_tabs = remaining_tabs
        if self._active_category_tab_id == tab_id:
            self._active_category_tab_id = target_tab["id"]

        self._save_category_tabs()
        self._load_categories_from_db()
        self._render_category_tab_bar()
        self._refresh_category_combos()
        self._refresh_category_tree()

    def _move_category_to_tab(self, cat_id: int, cat_name: str, target_tab_id: int) -> None:
        """카테고리를 다른 분류 탭으로 이동"""
        if self.repository and cat_id:
            self.repository.update_work_category_tab(cat_id, target_tab_id)
        self._load_categories_from_db()
        self._refresh_category_combos()
        self._refresh_category_tree()

    def _move_sheet_to_tab(self, sheet: WorkSheetData, target_tab_id: int) -> None:
        """업무 문서를 다른 분류 탭으로 이동"""
        if not self.repository or not sheet.db_id:
            return

        target_cats = [c for c in self._category_rows if c.get("tab_id", 1) == target_tab_id]
        target_cat = next((c for c in target_cats if c["name"] == sheet.category), None)

        if not target_cat:
            cat_name_to_use = sheet.category if sheet.category else "일반 업무"
            target_cat_id = self.repository.add_work_category(
                name=cat_name_to_use,
                sort_order=len(target_cats) + 1,
                tab_id=target_tab_id,
            )
            target_cat_name = cat_name_to_use
        else:
            target_cat_id = target_cat["id"]
            target_cat_name = target_cat["name"]

        self.repository.update_work_item_category(sheet.db_id, target_cat_id)
        sheet.category_id = target_cat_id
        sheet.category = target_cat_name

        self._load_categories_from_db()
        self._refresh_category_combos()
        self._refresh_category_tree()

        if 0 <= self._active_sheet_index < len(self._open_sheets):
            if self._open_sheets[self._active_sheet_index] == sheet:
                self.meta_cat_combo.setCurrentText(sheet.category)

    def _refresh_category_combos(self) -> None:
        self.meta_cat_combo.blockSignals(True)
        self.meta_cat_combo.clear()

        # 현재 활성 탭에 속한 카테고리
        active_cat_names = [c["name"] for c in self._category_rows if c.get("tab_id", 1) == self._active_category_tab_id]

        current_sheet_cat = ""
        if 0 <= self._active_sheet_index < len(self._open_sheets):
            current_sheet_cat = self._open_sheets[self._active_sheet_index].category or ""

        combo_items = list(active_cat_names)
        if current_sheet_cat and current_sheet_cat not in combo_items:
            combo_items.append(current_sheet_cat)

        self.meta_cat_combo.addItems(combo_items if combo_items else self._categories)
        self.meta_cat_combo.blockSignals(False)

    def _refresh_category_tree(self) -> None:
        """좌측 업무 분류 트리 재구성 (활성 탭에 속한 계층형 폴더 및 문서 목록 표시)"""
        self.category_tree.blockSignals(True)
        self.category_tree.clear()

        cat_items_map: dict[int, QTreeWidgetItem] = {}
        pending_subcats: list[dict] = []

        # 1. 루트 카테고리 중 현재 활성 탭에 속한 것만 추가
        for cat in self._category_rows:
            cat_id = cat["id"]
            parent_id = cat.get("parent_id")
            tab_id = cat.get("tab_id", 1)

            if not parent_id:
                if tab_id != self._active_category_tab_id:
                    continue
                raw_name = cat["name"]
                is_expanded = (cat_id in self._expanded_category_ids) if self._has_saved_expanded_ids else True
                icon = "📂" if is_expanded else "📁"
                item = QTreeWidgetItem([f"{icon} {raw_name}"])
                item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled)
                item.setData(0, Qt.UserRole + 1, cat_id)
                item.setData(0, Qt.UserRole + 2, raw_name)
                cat_items_map[cat_id] = item
                self.category_tree.addTopLevelItem(item)
            else:
                pending_subcats.append(cat)

        # 2. 하위 카테고리는 부모가 cat_items_map에 존재하는 경우에만 추가 (활성 탭에 귀속)
        added = True
        remaining = pending_subcats
        while added and remaining:
            added = False
            next_rem = []
            for cat in remaining:
                cat_id = cat["id"]
                parent_id = cat.get("parent_id")
                parent_item = cat_items_map.get(parent_id)
                if parent_item:
                    raw_name = cat["name"]
                    is_expanded = (cat_id in self._expanded_category_ids) if self._has_saved_expanded_ids else True
                    icon = "📂" if is_expanded else "📁"
                    item = QTreeWidgetItem([f"{icon} {raw_name}"])
                    item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled)
                    item.setData(0, Qt.UserRole + 1, cat_id)
                    item.setData(0, Qt.UserRole + 2, raw_name)
                    cat_items_map[cat_id] = item
                    parent_item.addChild(item)
                    added = True
                else:
                    next_rem.append(cat)
            remaining = next_rem

        # 3. 문서 배치: 현재 활성 탭의 카테고리에 속한 문서만 트리에 추가
        seen_tree_sheets = set()
        for sheet in self._all_sheets:
            key = sheet.db_id if sheet.db_id else id(sheet)
            if key in seen_tree_sheets:
                continue
            seen_tree_sheets.add(key)

            parent_item = None
            if sheet.category_id and sheet.category_id in cat_items_map:
                parent_item = cat_items_map[sheet.category_id]
            else:
                for c in self._category_rows:
                    if c["name"] == sheet.category and c["id"] in cat_items_map:
                        parent_item = cat_items_map[c["id"]]
                        sheet.category_id = c["id"]
                        break

            # 현재 탭에 속하지 않은 문서는 트리에 표시하지 않음
            if not parent_item:
                continue

            child = QTreeWidgetItem([f"📄 {sheet.title}"])
            # 중요: 문서 아이템은 자식을 받지 않도록 ItemIsDropEnabled 플래그를 제거!
            child.setFlags((child.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            child.setData(0, Qt.UserRole, sheet)
            child.setToolTip(0, sheet.title)
            parent_item.addChild(child)

        self.category_tree.blockSignals(False)

        if not self._has_saved_expanded_ids:
            self.category_tree.expandAll()
            for cat_id in cat_items_map:
                self._expanded_category_ids.add(cat_id)
        else:
            self._apply_folder_states_to_tree()

        self._sync_tree_selection(self._active_sheet_index)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """좌측 트리 항목 클릭 (폴더 클릭 시 펼침/접힘 토글, 문서는 단순 선택)"""
        sheet = item.data(0, Qt.UserRole)
        if not isinstance(sheet, WorkSheetData):
            item.setExpanded(not item.isExpanded())

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """좌측 트리 문서 더블클릭 시 에디터로 열기 (폴더 더블클릭 시 펼침/접힘 토글)"""
        sheet = item.data(0, Qt.UserRole)
        if isinstance(sheet, WorkSheetData):
            self.open_sheet(sheet)
        else:
            item.setExpanded(not item.isExpanded())

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        """좌측 트리 항목 우클릭 컨텍스트 메뉴 (폴더추가, 문서추가, 이름변경, 삭제)"""
        item = self.category_tree.itemAt(pos)
        menu = QMenu(self)

        if not item:
            act_add_cat = menu.addAction("📁 새 분류(폴더) 추가")
            act_add_doc = menu.addAction("📄 새 업무 추가")
            action = menu.exec(self.category_tree.mapToGlobal(pos))
            if action == act_add_cat:
                self._on_add_category()
            elif action == act_add_doc:
                self._on_add_new_sheet()
            return

        sheet = item.data(0, Qt.UserRole)
        if isinstance(sheet, WorkSheetData):
            act_open = menu.addAction("📄 열기")
            act_rename = menu.addAction("✏️ 이름 바꾸기")
            act_dup = menu.addAction("📋 복제")

            # 다른 탭으로 이동 서브메뉴 (현재 활성 탭 외의 탭들이 있을 때)
            other_tabs = [t for t in self._category_tabs if t["id"] != self._active_category_tab_id]
            doc_tab_actions = {}
            if other_tabs and sheet.db_id:
                menu_move_tab = menu.addMenu("📑 다른 탭으로 이동")
                for ot in other_tabs:
                    act_m = menu_move_tab.addAction(f"'{ot['name']}' 탭으로 이동")
                    doc_tab_actions[act_m] = ot["id"]

            menu.addSeparator()
            act_reg_cal = menu.addAction("📅 캘린더에 일정 등록")
            menu.addSeparator()
            act_delete = menu.addAction("🗑️ 업무 삭제 (DB 영구 삭제)")
            menu.addSeparator()
            act_add_doc = menu.addAction("➕ 새 업무 추가")
            action = menu.exec(self.category_tree.mapToGlobal(pos))
            if action in doc_tab_actions:
                self._move_sheet_to_tab(sheet, doc_tab_actions[action])
            elif action == act_open:
                self.open_sheet(sheet)
            elif action == act_reg_cal:
                self._register_sheet_to_calendar(sheet)
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
                        self._update_tab_title(sheet)
                        if o_idx == self._active_sheet_index:
                            self.work_title_input.setText(sheet.title)
                    self._refresh_category_tree()
            elif action == act_dup:
                self._duplicate_sheet(sheet)
            elif action == act_delete:
                self._delete_sheet_permanently(sheet)
            elif action == act_add_doc:
                self._on_add_sheet_in_category(sheet.category, sheet.category_id)
        else:
            cat_name = item.data(0, Qt.UserRole + 2) or item.text(0).replace("📁 ", "").replace("📂 ", "").strip()
            cat_id = item.data(0, Qt.UserRole + 1)
            act_add_doc = menu.addAction(f"➕ '{cat_name}'에 새 문서 추가")
            act_add_sub = menu.addAction("📁 하위 폴더(분류) 추가")
            act_rename = menu.addAction("✏️ 폴더 이름 변경")

            # 다른 탭으로 이동 서브메뉴 (현재 활성 탭 외의 탭들이 있을 때)
            other_tabs = [t for t in self._category_tabs if t["id"] != self._active_category_tab_id]
            tab_actions = {}
            if other_tabs and cat_id:
                menu_move_tab = menu.addMenu("📑 다른 탭으로 이동")
                for ot in other_tabs:
                    act_m = menu_move_tab.addAction(f"'{ot['name']}' 탭으로 이동")
                    tab_actions[act_m] = ot["id"]

            menu.addSeparator()
            act_reg_cal = menu.addAction("📅 캘린더에 일정 등록")
            menu.addSeparator()
            act_delete = menu.addAction("🗑️ 폴더 삭제")
            menu.addSeparator()
            act_add_cat = menu.addAction("📁 새 루트 분류(폴더) 추가")
            action = menu.exec(self.category_tree.mapToGlobal(pos))
            if action == act_add_doc:
                self._on_add_sheet_in_category(cat_name, cat_id)
            elif action == act_reg_cal:
                self._register_category_to_calendar(cat_name, cat_id)
            elif action == act_add_sub:
                self._on_add_sub_category(cat_id, cat_name)
            elif action == act_rename:
                self._on_rename_category(cat_name)
            elif action in tab_actions:
                self._move_category_to_tab(cat_id, cat_name, tab_actions[action])
            elif action == act_delete:
                self._on_delete_category(cat_name)
            elif action == act_add_cat:
                self._on_add_category()

    def _register_selected_to_calendar(self) -> None:
        """선택된 업무 또는 폴더를 캘린더에 일정으로 등록"""
        item = self.category_tree.currentItem()
        if not item:
            if 0 <= self._active_sheet_index < len(self._open_sheets):
                self._register_sheet_to_calendar(self._open_sheets[self._active_sheet_index])
                return
            QMessageBox.information(self, "안내", "캘린더에 등록할 업무 또는 분류(폴더)를 먼저 선택하세요.")
            return
        sheet = item.data(0, Qt.UserRole)
        if isinstance(sheet, WorkSheetData):
            self._register_sheet_to_calendar(sheet)
        else:
            cat_name = item.data(0, Qt.UserRole + 2) or item.text(0).replace("📁 ", "").replace("📂 ", "").strip()
            cat_id = item.data(0, Qt.UserRole + 1)
            self._register_category_to_calendar(cat_name, cat_id)

    def _register_sheet_to_calendar(self, sheet: WorkSheetData) -> None:
        """업무 항목을 캘린더 일정으로 등록"""
        if not self.main_window:
            QMessageBox.warning(self, "오류", "메인 캘린더 창을 찾을 수 없습니다.")
            return
        if not sheet.db_id and self.repository:
            self._save_current_sheet_data()
            if not sheet.db_id:
                sheet.db_id = self.repository.upsert_work_item(
                    work_id=None,
                    title=sheet.title or "새 업무",
                    category_name=sheet.category,
                    category_id=sheet.category_id,
                    cycle=sheet.cycle,
                    assignee=sheet.assignee,
                    deadline=sheet.deadline,
                    content_text=sheet.content_text or "",
                    content_html=sheet.content_html or "",
                    hwpx_blob=sheet.hwpx_blob,
                )
                self.repository.save()
        sheet_title = (sheet.title or "새 업무").strip()
        reg_title = sheet_title if (sheet_title.startswith("[문서]") or sheet_title.startswith("[업무]")) else f"[문서] {sheet_title}"
        saved = self.main_window.register_schedule_from_work(
            title=reg_title,
            description=reg_title,
            work_id=sheet.db_id,
            work_type="work",
        )
        if saved:
            self.show_floating_toast(f"'{sheet.title}' 일정이 캘린더에 등록되었습니다.")
            self._refresh_work_schedules_list(sheet)

    def _register_category_to_calendar(self, cat_name: str, cat_id: int | None) -> None:
        """업무 분류(폴더)를 캘린더 일정으로 등록"""
        if not self.main_window:
            QMessageBox.warning(self, "오류", "메인 캘린더 창을 찾을 수 없습니다.")
            return
        cat_clean = cat_name.strip()
        reg_title = cat_clean if (cat_clean.startswith("[문서]") or cat_clean.startswith("[업무]")) else f"[문서] {cat_clean}"
        saved = self.main_window.register_schedule_from_work(
            title=reg_title,
            description=reg_title,
            work_id=cat_id,
            work_type="folder",
        )
        if saved:
            self.show_floating_toast(f"'{cat_name}' 분류 일정이 캘린더에 등록되었습니다.")
            curr = self._get_current_sheet()
            if curr:
                self._refresh_work_schedules_list(curr)

    def select_work_item_or_category(self, item_id: int | None, item_type: str = "work", title: str = "") -> None:
        """캘린더에서 링크 클릭 시 해당 업무 또는 폴더를 열고 트리를 선택"""
        if item_type in ("work", "item"):
            target_sheet = None
            if item_id:
                for s in self._all_sheets:
                    if s.db_id == item_id:
                        target_sheet = s
                        break
            if not target_sheet and title:
                clean_title = title.replace("[문서]", "").replace("[업무]", "").strip()
                for s in self._all_sheets:
                    if s.title.strip() == clean_title or clean_title in s.title:
                        target_sheet = s
                        break
            if target_sheet:
                self.open_sheet(target_sheet)
                self._select_sheet_in_tree(target_sheet)
        elif item_type in ("folder", "category"):
            if item_id:
                self._select_category_in_tree(item_id)

    def _select_sheet_in_tree(self, sheet: WorkSheetData) -> None:
        """트리에서 sheet 항목을 찾아 부모 펼치고 선택"""
        def find_item(parent_item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
            count = parent_item.childCount() if parent_item else self.category_tree.topLevelItemCount()
            for i in range(count):
                child = parent_item.child(i) if parent_item else self.category_tree.topLevelItem(i)
                data = child.data(0, Qt.UserRole)
                if data == sheet or (isinstance(data, WorkSheetData) and data.db_id == sheet.db_id):
                    return child
                res = find_item(child)
                if res:
                    return res
            return None

        found = find_item(None)
        if found:
            p = found.parent()
            while p:
                p.setExpanded(True)
                p = p.parent()
            self.category_tree.setCurrentItem(found)
            self.category_tree.scrollToItem(found)

    def _select_category_in_tree(self, cat_id: int) -> None:
        """트리에서 cat_id 폴더 항목을 찾아 부모 펼치고 선택"""
        def find_item(parent_item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
            count = parent_item.childCount() if parent_item else self.category_tree.topLevelItemCount()
            for i in range(count):
                child = parent_item.child(i) if parent_item else self.category_tree.topLevelItem(i)
                if child.data(0, Qt.UserRole + 1) == cat_id:
                    return child
                res = find_item(child)
                if res:
                    return res
            return None

        found = find_item(None)
        if found:
            p = found.parent()
            while p:
                p.setExpanded(True)
                p = p.parent()
            found.setExpanded(True)
            self.category_tree.setCurrentItem(found)
            self.category_tree.scrollToItem(found)

    def _on_add_sub_category(self, parent_id: int | None, parent_name: str) -> None:
        """하위 폴더(분류) 생성"""
        sub_name, ok = QInputDialog.getText(self, "하위 분류(폴더) 추가", f"[{parent_name}] 하위 분류 명칭을 입력하세요:")
        if ok and sub_name.strip():
            c = sub_name.strip()
            if self.repository:
                self.repository.add_work_category(c, sort_order=100, parent_id=parent_id, tab_id=self._active_category_tab_id)
            self._load_categories_from_db()
            self._refresh_category_combos()
            self._refresh_category_tree()

    def _on_add_sheet_in_category(self, cat_name: str, cat_id: int | None = None) -> None:
        """지정된 분류(폴더) 내에 모달창으로 제목을 입력받아 새 업무 생성 및 탭 오픈"""
        self._save_current_sheet_data()
        default_title = f"신규 단위업무 {len(self._all_sheets) + 1}"
        title, ok = QInputDialog.getText(
            self,
            "새 업무 생성",
            f"업무 분류: [{cat_name}]\n생성할 업무 제목을 입력하세요:",
            QLineEdit.Normal,
            default_title,
        )
        if not ok or not title.strip():
            return

        new_title = title.strip()
        db_id = None
        if self.repository:
            db_id = self.repository.upsert_work_item(
                work_id=None,
                title=new_title,
                category_name=cat_name,
                category_id=cat_id,
                cycle="수시",
                content_text="",
                sort_order=len(self._all_sheets) + 1,
            )
        new_sheet = WorkSheetData(
            db_id=db_id,
            sheet_id=f"sheet_{db_id or (len(self._all_sheets) + 1)}",
            title=new_title,
            category=cat_name,
            category_id=cat_id,
            cycle="수시",
            content_text="",
        )
        self._all_sheets.append(new_sheet)
        self._refresh_category_tree()
        self.open_sheet(new_sheet)

    def _on_rename_category(self, old_cat: str) -> None:
        """폴더(분류) 이름 변경"""
        new_cat, ok = QInputDialog.getText(self, "폴더(분류) 이름 변경", "새 분류 명칭:", text=old_cat)
        if ok and new_cat.strip() and new_cat.strip() != old_cat:
            c = new_cat.strip()
            # DB 상의 카테고리 이름 갱신
            if self.repository:
                for row in self._category_rows:
                    if row["name"] == old_cat:
                        self.repository.update_work_category(row["id"], c)
                        break

            for s in self._all_sheets:
                if s.category == old_cat:
                    s.category = c
                    if self.repository and s.db_id:
                        self.repository.upsert_work_item(
                            work_id=s.db_id,
                            title=s.title,
                            category_name=c,
                            category_id=s.category_id,
                            cycle=s.cycle,
                            assignee=s.assignee,
                            deadline=s.deadline,
                            content_text=s.content_text,
                            content_html=s.content_html,
                            hwpx_blob=s.hwpx_blob,
                        )
            self._load_categories_from_db()
            self._refresh_category_combos()
            self._refresh_category_tree()

    def _on_delete_category(self, cat_name: str) -> None:
        """폴더(분류) 및 하위 폴더/문서 전체 삭제"""
        cat_id = None
        for row in self._category_rows:
            if row["name"] == cat_name:
                cat_id = row["id"]
                break
        if cat_id is None:
            return

        # 삭제 대상 폴더와 모든 하위 폴더 id 수집
        target_ids = {cat_id}
        changed = True
        while changed:
            changed = False
            for row in self._category_rows:
                if row.get("parent_id") in target_ids and row["id"] not in target_ids:
                    target_ids.add(row["id"])
                    changed = True

        to_del = [
            s
            for s in self._all_sheets
            if s.category_id in target_ids or (s.category_id is None and s.category == cat_name)
        ]
        sub_count = len(target_ids) - 1
        msg = f"'{cat_name}' 분류를 삭제하시겠습니까?"
        if sub_count > 0:
            msg += f"\n포함된 하위 폴더 {sub_count}개도 함께 삭제됩니다."
        if to_del:
            msg += f"\n포함된 {len(to_del)}개의 업무 문서도 함께 DB에서 삭제됩니다."
        res = QMessageBox.question(self, "분류 삭제 확인", msg, QMessageBox.Yes | QMessageBox.No)
        if res != QMessageBox.Yes:
            return

        total_steps = len(to_del) + 1
        progress = None
        if len(to_del) > 0 or sub_count > 0:
            progress = make_work_progress_dialog(
                self,
                "업무 분류 삭제",
                f"분류 '{cat_name}' 삭제 준비 중...",
                total_steps,
            )

        try:
            if self.repository:
                self.repository.delete_work_category(cat_id)
            if progress:
                progress.setValue(1)
                progress.setLabelText(f"하위 문서 및 첨부파일 정리 중... (0/{len(to_del)})")
                QApplication.processEvents()

            for idx, s in enumerate(to_del):
                if progress:
                    progress.setValue(idx + 1)
                    progress.setLabelText(f"하위 문서 삭제 중... ({idx+1}/{len(to_del)})\n{s.title}")
                    QApplication.processEvents()

                if self.repository and s.db_id:
                    self.repository.delete_work_item(s.db_id)
                if s in self._all_sheets:
                    self._all_sheets.remove(s)
                if s in self._open_sheets:
                    o_idx = self._open_sheets.index(s)
                    self._open_sheets.pop(o_idx)
                    self.sheet_tab_bar.blockSignals(True)
                    self.sheet_tab_bar.removeTab(o_idx)
                    self.sheet_tab_bar.blockSignals(False)
        finally:
            if progress:
                progress.setValue(total_steps)
                progress.close()
        if self._open_sheets:
            new_idx = max(0, min(self._active_sheet_index, len(self._open_sheets) - 1))
            self.sheet_tab_bar.blockSignals(True)
            self.sheet_tab_bar.setCurrentIndex(new_idx)
            self.sheet_tab_bar.blockSignals(False)
            self._active_sheet_index = new_idx
            self._load_sheet_to_editor(new_idx)
        else:
            self._active_sheet_index = -1
            self._clear_editor_view()
        self._load_categories_from_db()
        self._refresh_category_combos()
        self._refresh_category_tree()

    def _on_tree_order_changed(self) -> None:
        """트리 항목 드래그 앤 드롭 정렬 변경 시 DB 및 메모리 동기화 (계층형 폴더 및 문서 이동/순서변경)"""
        new_all_sheets = []
        seen_sheets = set()
        seen_ids = set()
        duplicates_to_remove = []
        doc_sort_order = 0

        def traverse_folder(folder_item, parent_id: int | None, cat_sort: int):
            nonlocal doc_sort_order
            cat_name = folder_item.data(0, Qt.UserRole + 2) or folder_item.text(0).replace("📁 ", "").replace("📂 ", "").strip()
            cat_id = folder_item.data(0, Qt.UserRole + 1)

            if self.repository:
                if cat_id:
                    self.repository.update_work_category_parent(cat_id, parent_id, cat_sort)
                else:
                    cat_id = self.repository.add_work_category(cat_name, cat_sort, parent_id)
                    folder_item.setData(0, Qt.UserRole + 1, cat_id)

            sub_order = 0
            for k in range(folder_item.childCount()):
                child = folder_item.child(k)
                sheet = child.data(0, Qt.UserRole)
                if isinstance(sheet, WorkSheetData):
                    key = id(sheet)
                    db_key = sheet.db_id if sheet.db_id else sheet.sheet_id
                    if key in seen_sheets or (db_key and db_key in seen_ids):
                        duplicates_to_remove.append((folder_item, child))
                        continue
                    seen_sheets.add(key)
                    if db_key:
                        seen_ids.add(db_key)

                    sheet.category = cat_name
                    sheet.category_id = cat_id
                    new_all_sheets.append(sheet)
                    doc_sort_order += 1
                    if self.repository and sheet.db_id:
                        self.repository.upsert_work_item(
                            work_id=sheet.db_id,
                            title=sheet.title,
                            category_name=cat_name,
                            category_id=cat_id,
                            cycle=sheet.cycle,
                            assignee=sheet.assignee,
                            deadline=sheet.deadline,
                            content_text=sheet.content_text,
                            content_html=sheet.content_html,
                            hwpx_blob=sheet.hwpx_blob,
                            sort_order=doc_sort_order,
                        )
                else:
                    sub_order += 1
                    traverse_folder(child, cat_id, sub_order)

        for i in range(self.category_tree.topLevelItemCount()):
            top = self.category_tree.topLevelItem(i)
            sheet = top.data(0, Qt.UserRole)
            if not isinstance(sheet, WorkSheetData):
                traverse_folder(top, None, i + 1)

        # 중복 아이템이 감지되었다면 트리에서 정리
        for p_item, c_item in duplicates_to_remove:
            p_item.removeChild(c_item)

        self._all_sheets = new_all_sheets
        self._load_categories_from_db()
        self._refresh_category_combos()

        # 현재 활성 시트의 카테고리가 이동되었다면 상단 카테고리 콤보박스 동기화
        if 0 <= self._active_sheet_index < len(self._open_sheets):
            active_sheet = self._open_sheets[self._active_sheet_index]
            idx = self.meta_cat_combo.findText(active_sheet.category)
            if idx >= 0:
                self.meta_cat_combo.blockSignals(True)
                self.meta_cat_combo.setCurrentIndex(idx)
                self.meta_cat_combo.blockSignals(False)

    def _on_add_category(self) -> None:
        cat_name, ok = QInputDialog.getText(self, "새 업무 분류 추가", "분류 명칭을 입력하세요 (예: 4. 대민 행정 서비스):")
        if ok and cat_name.strip():
            c = cat_name.strip()
            if self.repository:
                self.repository.add_work_category(c, len(self._categories) + 1, tab_id=self._active_category_tab_id)
            self._load_categories_from_db()
            self._refresh_category_combos()
            self._refresh_category_tree()

    def _refresh_attachments_list(self, files: list[dict] | None = None) -> None:
        """우측 첨부파일 트리 재구성 (폴더 트리 계층 구조 및 파일 목록)"""
        if files is None:
            if 0 <= self._active_sheet_index < len(self._open_sheets):
                files = self._open_sheets[self._active_sheet_index].attachments
            else:
                files = []

        self.file_list.blockSignals(True)
        self.file_list.clear()

        # 폴더 맵 (folder_path -> QTreeWidgetItem)
        folder_items: dict[str, QTreeWidgetItem] = {}

        # 1. 모든 항목의 folder_path 분석하여 계층형 폴더 노드 미리 생성
        all_folder_paths: set[str] = set()
        for f in files:
            fp = (f.get("folder_path") or "").strip().replace("\\", "/").strip("/")
            if fp:
                parts = fp.split("/")
                cur = ""
                for p in parts:
                    cur = f"{cur}/{p}" if cur else p
                    all_folder_paths.add(cur)
            if f.get("file_type") == "folder" or f.get("type") == "folder":
                fname = f.get("name", "").strip()
                if fname:
                    combined = f"{fp}/{fname}".strip("/") if fp else fname
                    all_folder_paths.add(combined)

        sorted_folder_paths = sorted(all_folder_paths, key=lambda x: (x.count("/"), x))
        for fp in sorted_folder_paths:
            parts = fp.split("/")
            name = parts[-1]
            parent_fp = "/".join(parts[:-1]) if len(parts) > 1 else ""
            # 기본적으로 모든 폴더는 열린 상태(📂), 사용자가 명시적으로 접은 폴더만 닫힘(📁)
            is_expanded = fp not in self._collapsed_attachment_folders
            icon = "📂" if is_expanded else "📁"
            f_item = QTreeWidgetItem([f"{icon} {name}"])
            f_item.setData(0, Qt.UserRole, {"type": "folder", "name": name, "folder_path": fp})
            f_item.setToolTip(0, fp)
            font = f_item.font(0)
            font.setBold(True)
            f_item.setFont(0, font)
            folder_items[fp] = f_item

            if parent_fp and parent_fp in folder_items:
                folder_items[parent_fp].addChild(f_item)
            else:
                self.file_list.addTopLevelItem(f_item)

        # 2. 파일 항목 배치
        file_count = 0
        for f in files:
            if f.get("file_type") == "folder" or f.get("type") == "folder":
                continue
            file_count += 1
            name = f.get("name", "첨부파일")
            size = f.get("size", "")
            icon = get_file_extension_icon(name)
            item_text = f"{icon} {name}  ({size})" if size else f"{icon} {name}"
            file_item = QTreeWidgetItem([item_text])
            f_dict = dict(f)
            f_dict["type"] = "file"
            file_item.setData(0, Qt.UserRole, f_dict)
            file_item.setToolTip(0, name)

            fp = (f.get("folder_path") or "").strip().replace("\\", "/").strip("/")
            if fp and fp in folder_items:
                folder_items[fp].addChild(file_item)
            else:
                self.file_list.addTopLevelItem(file_item)

        # 3. 폴더 기본 펼침 상태 적용 (명시적으로 접은 폴더만 닫힘)
        for fp, f_item in folder_items.items():
            if fp in self._collapsed_attachment_folders:
                f_item.setExpanded(False)
            else:
                f_item.setExpanded(True)

        self.file_list.blockSignals(False)
        self.right_title.setText(f"📎 첨부파일 ({file_count})")

    def _get_current_sheet(self) -> WorkSheetData | None:
        """현재 열려있는 활성 시트 반환"""
        if hasattr(self, "_open_sheets") and hasattr(self, "_active_sheet_index"):
            if 0 <= self._active_sheet_index < len(self._open_sheets):
                return self._open_sheets[self._active_sheet_index]
        return None

    def _update_related_tab_style(self) -> None:
        """관련 일정/관련 문서 탭 버튼의 활성/비활성 스타일 및 텍스트 갱신"""
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")
        text = self.palette.get("text", "#1F2328")
        muted = self.palette.get("muted", "#64748B")

        active_style = f"""
            QPushButton {{
                background-color: {accent_soft};
                color: {accent};
                font-weight: bold;
                font-size: 11px;
                border: 1px solid {accent};
                border-radius: 4px;
                padding: 1px 8px;
            }}
        """
        inactive_style = f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {muted};
                font-weight: normal;
                font-size: 11px;
                border: 1px solid {line};
                border-radius: 4px;
                padding: 1px 8px;
            }}
            QPushButton:hover {{
                background-color: {panel};
                color: {text};
                border-color: {line};
            }}
        """

        if self._active_related_tab == 0:
            self.btn_tab_related_sched.setStyleSheet(active_style)
            self.btn_tab_related_doc.setStyleSheet(inactive_style)
            self.btn_add_related.setText("+ 일정")
            self.btn_add_related.setToolTip("현재 업무와 연결된 캘린더 일정 등록")
            self.btn_goto_related.setText("이동")
            self.btn_goto_related.setToolTip("선택한 일정 날짜의 캘린더로 이동")
            self.btn_edit_related.setText("수정")
            self.btn_edit_related.setToolTip("선택한 일정 수정")
            self.btn_delete_related.setText("삭제")
            self.btn_delete_related.setToolTip("선택한 일정 삭제")
            self.btn_delete_related.setStyleSheet(self._danger_btn_style())
        else:
            self.btn_tab_related_sched.setStyleSheet(inactive_style)
            self.btn_tab_related_doc.setStyleSheet(active_style)
            self.btn_add_related.setText("+ 문서")
            self.btn_add_related.setToolTip("현재 업무와 연결할 관련 문서(업무) 추가")
            self.btn_goto_related.setText("이동")
            self.btn_goto_related.setToolTip("선택한 문서를 문서 관리 대시보드에서 열기")
            self.btn_edit_related.setText("수정")
            self.btn_edit_related.setToolTip("선택한 문서 내용/정보 수정")
            self.btn_delete_related.setText("연결 해제")
            self.btn_delete_related.setToolTip("현재 업무와 해당 문서의 연결 해제")
            self.btn_delete_related.setStyleSheet(self._sub_btn_style())

    def _set_related_tab(self, tab_index: int) -> None:
        """우측 하단 관련 항목 탭 전환 (0: 일정, 1: 문서)"""
        self._active_related_tab = tab_index
        self.related_stack.setCurrentIndex(tab_index)
        self._update_related_tab_style()

    def _refresh_work_schedules_list(self, sheet: WorkSheetData | None = None) -> None:
        """현재 시트와 연결된 캘린더 일정 및 문서 목록을 갱신하여 1줄씩 표시"""
        if not hasattr(self, "work_schedule_list") or not self.work_schedule_list:
            return
        self.work_schedule_list.clear()
        if sheet is None:
            sheet = self._get_current_sheet()

        # 관련 문서 목록도 함께 갱신
        self._refresh_work_documents_list(sheet)

        if not sheet or not self.repository:
            if hasattr(self, "btn_tab_related_sched"):
                self.btn_tab_related_sched.setText("일정 (0)")
            return

        entries = self.repository.list_entries_for_work(work_id=sheet.db_id, work_title=sheet.title)
        if hasattr(self, "btn_tab_related_sched"):
            self.btn_tab_related_sched.setText(f"일정 ({len(entries)})")

        if not entries:
            empty_item = QListWidgetItem("(등록된 일정 없음)")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QColor(self.palette.get("muted", "#94A3B8")))
            self.work_schedule_list.addItem(empty_item)
            return

        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        # 기간 일정을 일단위(1일 1줄)로 전개 (반복 일정은 _occurs_on으로 실제 발생일만 전개)
        schedule_rows: list[tuple[date, CalendarEntry]] = []
        for entry in entries:
            s_d = entry.start_date or entry.day or date.today()
            e_d = entry.end_date or entry.day or s_d
            if s_d > e_d:
                s_d, e_d = e_d, s_d

            # 1) 반복 일정: 반복 규칙에 부합하는 실제 발생 일자만 추가
            if entry.recurrence_enabled and entry.recurrence_type != RecurrenceType.NONE:
                max_end = s_d + timedelta(days=366)
                actual_end = min(e_d, max_end) if entry.end_date else max_end
                cur = s_d
                while cur <= actual_end:
                    if hasattr(self.repository, "_occurs_on") and self.repository._occurs_on(entry, cur):
                        schedule_rows.append((cur, entry))
                    cur += timedelta(days=1)
            # 2) 일반 일정(단일일 또는 연속 기간 일정):
            else:
                cur = s_d
                while cur <= e_d:
                    schedule_rows.append((cur, entry))
                    cur += timedelta(days=1)

        # 날짜순, 시작시간순, ID순 정렬
        schedule_rows.sort(key=lambda x: (x[0], x[1].start_time or "00:00", x[1].entry_id or 0))

        if hasattr(self, "btn_tab_related_sched"):
            self.btn_tab_related_sched.setText(f"일정 ({len(schedule_rows)})")

        if not schedule_rows:
            empty_item = QListWidgetItem("(등록된 일정 없음)")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QColor(self.palette.get("muted", "#94A3B8")))
            self.work_schedule_list.addItem(empty_item)
            return

        for cur_date, entry in schedule_rows:
            w = weekdays[cur_date.weekday()]
            date_txt = f"{cur_date.strftime('%m.%d')}({w})"
            time_txt = f" {entry.start_time}" if (not entry.all_day and entry.start_time) else ""
            display_title = entry.title or sheet.title or "일정"
            line_txt = f"{date_txt}{time_txt}  {display_title}"

            item = QListWidgetItem(line_txt)
            item.setData(Qt.UserRole, entry.entry_id)
            item.setData(Qt.UserRole + 1, entry)
            item.setData(Qt.UserRole + 2, cur_date)
            item.setToolTip(f"제목: {entry.title}\n일시: {date_txt}{time_txt}\n설명: {entry.description or '(없음)'}\n(더블클릭 시 해당 일자 캘린더로 이동 / 우클릭 메뉴)")
            self.work_schedule_list.addItem(item)

        # 오늘 날짜와 가장 가까운 일정으로 자동 스크롤
        today = date.today()
        target_item = None
        # 1) 오늘 이후(오늘 포함) 중 가장 빠른 일정 우선 선택
        for i in range(self.work_schedule_list.count()):
            it = self.work_schedule_list.item(i)
            c_date = it.data(Qt.UserRole + 2)
            if c_date and isinstance(c_date, date) and c_date >= today:
                target_item = it
                break

        # 2) 만약 모든 일정이 오늘보다 과거라면, 가장 최근 과거 일정(마지막 항목) 선택
        if not target_item and self.work_schedule_list.count() > 0:
            target_item = self.work_schedule_list.item(self.work_schedule_list.count() - 1)

        if target_item:
            self.work_schedule_list.scrollToItem(target_item, QAbstractItemView.ScrollHint.PositionAtTop)
            self.work_schedule_list.setCurrentItem(target_item)

    def _refresh_work_documents_list(self, sheet: WorkSheetData | None = None) -> None:
        """현재 시트와 연결된 문서(task) 목록 갱신"""
        if not hasattr(self, "work_doc_list") or not self.work_doc_list:
            return
        self.work_doc_list.clear()
        if sheet is None:
            sheet = self._get_current_sheet()
        if not sheet or not self.repository or not sheet.db_id:
            if hasattr(self, "btn_tab_related_doc"):
                self.btn_tab_related_doc.setText("문서 (0)")
            return

        tasks = self.repository.list_linked_entries_for_work(work_id=sheet.db_id, entry_type="task")
        if hasattr(self, "btn_tab_related_doc"):
            self.btn_tab_related_doc.setText(f"문서 ({len(tasks)})")

        if not tasks:
            empty_item = QListWidgetItem("(연결된 문서 없음)")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(QColor(self.palette.get("muted", "#94A3B8")))
            self.work_doc_list.addItem(empty_item)
            return

        for t in tasks:
            cat = t.memo_group or "일반"
            author = f" [{t.assignee}]" if t.assignee else ""
            d_str = ""
            if t.day or t.start_date:
                tgt_d = t.day or t.start_date
                d_str = f" ({tgt_d.strftime('%m.%d')})"

            display_txt = f"[{cat}] {t.title}{author}{d_str}"
            item = QListWidgetItem(display_txt)
            item.setData(Qt.ItemDataRole.UserRole, t.entry_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, t)
            item.setData(Qt.ItemDataRole.UserRole + 2, t.day or t.start_date)
            tip = f"제목: {t.title}\n분류: {cat}\n기안자: {t.assignee or '-'}\n일자: {t.day or t.start_date or '-'}\n설명: {t.description or '(없음)'}\n(더블클릭 시 문서 관리에서 보기 / 우클릭 메뉴)"
            item.setToolTip(tip)
            self.work_doc_list.addItem(item)

        # 오늘 날짜와 가장 가까운 문서로 자동 스크롤 및 포커스
        today = date.today()
        target_doc_item = None
        min_diff = None

        for i in range(self.work_doc_list.count()):
            it = self.work_doc_list.item(i)
            doc_date = it.data(Qt.ItemDataRole.UserRole + 2)
            if doc_date and isinstance(doc_date, date):
                diff = abs((doc_date - today).days)
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    target_doc_item = it

        # 날짜 정보가 있는 항목이 없으면 맨 위 첫 번째 항목 선택
        if not target_doc_item and self.work_doc_list.count() > 0:
            target_doc_item = self.work_doc_list.item(0)

        if target_doc_item:
            self.work_doc_list.scrollToItem(target_doc_item, QAbstractItemView.ScrollHint.PositionAtTop)
            self.work_doc_list.setCurrentItem(target_doc_item)

    def _on_add_related_clicked(self) -> None:
        """상단 '+ 추가' 버튼 클릭 시 현재 활성 탭에 맞춰 일정 또는 문서 추가"""
        if self._active_related_tab == 0:
            self._on_add_work_schedule_clicked()
        else:
            self._on_add_work_document_clicked()

    def _on_goto_related_clicked(self) -> None:
        """하단 '이동' 버튼 클릭 시 현재 활성 탭에 맞춰 캘린더 이동 또는 문서 관리 이동"""
        if self._active_related_tab == 0:
            self._on_goto_calendar_clicked()
        else:
            self._on_goto_document_clicked()

    def _on_edit_related_clicked(self) -> None:
        """하단 '수정' 버튼 클릭 시 현재 활성 탭에 맞춰 일정 또는 문서 수정"""
        if self._active_related_tab == 0:
            self._on_edit_work_schedule_clicked()
        else:
            self._on_edit_work_document_clicked()

    def _on_delete_related_clicked(self) -> None:
        """하단 삭제/연결해제 버튼 클릭 시 현재 활성 탭에 맞춰 일정 삭제 또는 문서 연결 해제"""
        if self._active_related_tab == 0:
            self._on_delete_work_schedule_clicked()
        else:
            self._on_unlink_work_document_clicked()

    def _on_add_work_document_clicked(self) -> None:
        """현재 업무에 연결할 문서 선택 창 띄우기"""
        sheet = self._get_current_sheet()
        if not sheet or not sheet.db_id:
            self.show_floating_toast("선택된 업무가 없습니다.")
            return

        linked_tasks = self.repository.list_linked_entries_for_work(work_id=sheet.db_id, entry_type="task")
        initial_eids = [t.entry_id for t in linked_tasks if t.entry_id]

        dlg = WorkDocSelectDialog(
            self,
            self.repository,
            initial_entry_ids=initial_eids,
            palette=self.palette,
            title=f"'{sheet.title}' 관련 문서 연결",
        )
        if dlg.exec() == QDialog.Accepted:
            new_eids = dlg.selected_entry_ids
            # 기존 연결 해제된 항목 및 신규 연결 항목 반영
            to_remove = set(initial_eids) - set(new_eids)
            for eid in to_remove:
                self.repository.unlink_entry_from_work(sheet.db_id, eid)
            if new_eids:
                self.repository.link_entries_to_works([sheet.db_id], new_eids)
            self.repository.save()
            self._refresh_work_documents_list(sheet)
            self.show_floating_toast(f"관련 문서 {len(new_eids)}개가 연결되었습니다.")

    def _on_goto_document_clicked(self) -> None:
        """선택한 문서를 TaskManagerDialog에서 열고 포커스"""
        item = self.work_doc_list.currentItem()
        if not item:
            self.show_floating_toast("이동할 문서를 선택하세요.")
            return
        entry = item.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return

        try:
            from taskcalendar.qt_task_manager import TaskManagerDialog
            tm = getattr(self.main_window, "_task_manager_dialog", None) if self.main_window else None
            # 캘린더 창이 함께 뜨지 않도록 parent=None 독립 윈도우로 생성/보장
            if tm is None or not tm.isVisible() or tm.parent() is not None:
                if tm is not None and tm.parent() is not None:
                    try:
                        tm.close()
                    except Exception:
                        pass
                tm = TaskManagerDialog(None, self.repository, self.main_window)
                if self.main_window:
                    self.main_window._task_manager_dialog = tm
            tm.apply_palette(self.palette)
            tm.reload_tasks()
            tm.show()
            tm.raise_()
            tm.activateWindow()
            if entry.entry_id:
                tm._select_and_highlight_task(entry.entry_id)
        except Exception as e:
            logger.exception("Failed to jump to task manager: %s", e)

    def _on_doc_item_double_clicked(self, item: QListWidgetItem) -> None:
        """문서 더블클릭 시 문서 관리 창으로 이동"""
        self._on_goto_document_clicked()

    def _on_edit_work_document_clicked(self) -> None:
        """선택한 문서 수정 다이얼로그 호출"""
        item = self.work_doc_list.currentItem()
        if not item:
            self.show_floating_toast("수정할 문서를 선택하세요.")
            return
        entry = item.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return

        try:
            from taskcalendar.qt_task_manager import TaskEditDialog
            dlg = TaskEditDialog(self, self.repository, task=entry, palette=self.palette)
            if dlg.exec() == QDialog.Accepted:
                saved_task = dlg.get_task()
                self.repository.upsert_entry(saved_task)
                self.repository.save()
                if self.main_window and hasattr(self.main_window, "refresh"):
                    self.main_window.refresh()
                self._refresh_work_documents_list()
                self.show_floating_toast("문서 정보가 수정되었습니다.")
        except Exception as e:
            logger.exception("Failed to edit task: %s", e)

    def _on_unlink_work_document_clicked(self) -> None:
        """선택한 문서와 현재 업무의 연결 해제"""
        sheet = self._get_current_sheet()
        if not sheet or not sheet.db_id:
            return
        item = self.work_doc_list.currentItem()
        if not item:
            self.show_floating_toast("연결 해제할 문서를 선택하세요.")
            return
        entry = item.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(entry, CalendarEntry) or not entry.entry_id:
            return

        reply = QMessageBox.question(
            self,
            "연결 해제",
            f"'{entry.title}' 문서와의 연결을 해제하시겠습니까?\n\n(문서 자체가 삭제되지는 않고 이 업무와의 연결만 해제됩니다.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.repository.unlink_entry_from_work(sheet.db_id, entry.entry_id)
            self.repository.save()
            self._refresh_work_documents_list(sheet)
            self.show_floating_toast("문서 연결이 해제되었습니다.")

    def _on_doc_context_menu(self, pos: QPoint) -> None:
        item = self.work_doc_list.itemAt(pos)
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {self.palette.get('panel', '#FFFFFF')};
                color: {self.palette.get('text', '#1F2328')};
                border: 1px solid {self.palette.get('line', '#CBD5E0')};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 11px;
            }}
            QMenu::item:selected {{
                background-color: #F1F5F9;
                color: #0284C7;
            }}
        """)
        act_jump = menu.addAction("문서 관리에서 보기")
        act_jump.triggered.connect(self._on_goto_document_clicked)
        act_edit = menu.addAction("문서 수정")
        act_edit.triggered.connect(self._on_edit_work_document_clicked)
        menu.addSeparator()
        act_del = menu.addAction("연결 해제")
        act_del.triggered.connect(self._on_unlink_work_document_clicked)

        menu.exec(self.work_doc_list.mapToGlobal(pos))

    def _on_add_work_schedule_clicked(self) -> None:
        """우측 패널 '+ 일정' 버튼 클릭 시 현재 업무의 캘린더 일정 등록 다이얼로그 호출"""
        sheet = self._get_current_sheet()
        if not sheet:
            self.show_floating_toast("선택된 업무가 없습니다.")
            return
        self._register_sheet_to_calendar(sheet)

    def _on_goto_calendar_clicked(self) -> None:
        """선택한 일정을 메인 캘린더에서 보기 위해 캘린더로 이동"""
        item = self.work_schedule_list.currentItem()
        if not item:
            self.show_floating_toast("이동할 일정을 선택하세요.")
            return
        entry = item.data(Qt.UserRole + 1)
        target_day = item.data(Qt.UserRole + 2)
        if not isinstance(entry, CalendarEntry):
            return
        self._jump_to_calendar_entry(entry, target_day=target_day)

    def _jump_to_calendar_entry(self, entry: CalendarEntry, target_day: date | None = None) -> None:
        if not self.main_window:
            return
        if target_day is None:
            target_day = entry.start_date or entry.day or date.today()
        self.main_window.current_date = target_day
        self.main_window.selected_day = target_day
        self.main_window.refresh()
        self.main_window.show()
        self.main_window.raise_()
        self.main_window.activateWindow()

    def _on_schedule_item_double_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole + 1)
        target_day = item.data(Qt.UserRole + 2)
        if isinstance(entry, CalendarEntry):
            self._jump_to_calendar_entry(entry, target_day=target_day)

    def _on_edit_work_schedule_clicked(self) -> None:
        item = self.work_schedule_list.currentItem()
        if not item:
            self.show_floating_toast("수정할 일정을 선택하세요.")
            return
        entry = item.data(Qt.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return
        if not self.main_window:
            return
        from taskcalendar.qt_dialogs import EntryDialog
        dlg = EntryDialog(self, entry.entry_type, entry.day or date.today(), entry)
        if dlg.exec():
            res = dlg.result
            if res:
                res.entry_id = entry.entry_id
                res.linked_work_id = entry.linked_work_id
                res.linked_work_type = entry.linked_work_type
                self.repository.upsert_entry(res)
                self.repository.save()
                if self.main_window:
                    self.main_window.refresh()
                self._refresh_work_schedules_list()
                self.show_floating_toast("일정이 수정되었습니다.")

    def _on_delete_work_schedule_clicked(self) -> None:
        item = self.work_schedule_list.currentItem()
        if not item:
            self.show_floating_toast("삭제할 일정을 선택하세요.")
            return
        entry = item.data(Qt.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return
        reply = QMessageBox.question(
            self,
            "일정 삭제",
            f"'{entry.title}' 일정을 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            target_id = getattr(entry, "source_entry_id", None) or entry.entry_id
            if target_id is not None:
                self.repository.delete_entry(target_id)
                self.repository.save()
            if self.main_window:
                self.main_window.refresh()
            self._refresh_work_schedules_list()
            self.show_floating_toast("일정이 삭제되었습니다.")

    def _on_schedule_context_menu(self, pos: QPoint) -> None:
        item = self.work_schedule_list.itemAt(pos)
        if not item:
            return
        entry = item.data(Qt.UserRole + 1)
        if not isinstance(entry, CalendarEntry):
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {self.palette.get('panel', '#FFFFFF')};
                color: {self.palette.get('text', '#1F2328')};
                border: 1px solid {self.palette.get('line', '#CBD5E0')};
                border-radius: 6px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 4px;
                font-size: 11px;
            }}
            QMenu::item:selected {{
                background-color: #F1F5F9;
                color: #0284C7;
            }}
        """)
        act_jump = menu.addAction("📅 캘린더에서 보기")
        act_jump.triggered.connect(lambda: self._jump_to_calendar_entry(entry))
        act_edit = menu.addAction("✏️ 일정 수정")
        act_edit.triggered.connect(self._on_edit_work_schedule_clicked)
        menu.addSeparator()
        act_del = menu.addAction("🗑️ 일정 삭제")
        act_del.triggered.connect(self._on_delete_work_schedule_clicked)

        menu.exec(self.work_schedule_list.mapToGlobal(pos))

    def _on_attachment_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            fp = data.get("folder_path", "")
            self._collapsed_attachment_folders.discard(fp)
            self._expanded_attachment_folders.add(fp)
            item.setText(0, f"📂 {data.get('name')}")

    def _on_attachment_item_collapsed(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            fp = data.get("folder_path", "")
            self._collapsed_attachment_folders.add(fp)
            self._expanded_attachment_folders.discard(fp)
            item.setText(0, f"📁 {data.get('name')}")

    def _on_attachment_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """첨부파일 항목 클릭 시 폴더면 열기/닫기 토글 및 선택 상태 유지"""
        self.file_list.setCurrentItem(item)
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            item.setExpanded(not item.isExpanded())

    def _on_attachment_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            item.setExpanded(not item.isExpanded())
        else:
            self._open_selected_attachment()

    def _on_add_attachment_folder(self, parent_folder: str | None = None) -> None:
        """우측 첨부파일 패널 내 새 폴더 생성"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "폴더를 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        curr = self._open_sheets[self._active_sheet_index]
        if not curr.db_id and self.repository:
            self._save_sheet_sync(curr)

        if parent_folder is None:
            selected = self.file_list.selectedItems()
            item = selected[0] if selected else self.file_list.currentItem()
            if item:
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "folder":
                    parent_folder = data.get("folder_path") or data.get("name") or ""
                else:
                    parent = item.parent()
                    if parent:
                        p_data = parent.data(0, Qt.UserRole) or {}
                        parent_folder = p_data.get("folder_path") or p_data.get("name") or ""
                    else:
                        parent_folder = ""
            else:
                parent_folder = ""

        prompt = f"[{parent_folder}] 하위 폴더 이름:" if parent_folder else "새 폴더 이름:"
        folder_name, ok = QInputDialog.getText(self, "새 첨부파일 폴더 생성", prompt)
        if not ok or not folder_name.strip():
            return

        clean_name = folder_name.strip().replace("/", "_").replace("\\", "_")
        full_path = f"{parent_folder}/{clean_name}".strip("/") if parent_folder else clean_name

        if self.repository and curr.db_id:
            dir_path = self.repository.get_work_attachments_dir(curr.db_id, full_path)
            dir_path.mkdir(parents=True, exist_ok=True)
            att_id = self.repository.add_work_attachment(
                work_id=curr.db_id,
                file_name=clean_name,
                file_path="",
                file_size="",
                file_type="folder",
                folder_path=parent_folder,
            )
            curr.attachments.append({
                "id": att_id,
                "name": clean_name,
                "path": "",
                "size": "",
                "folder_path": parent_folder,
                "file_type": "folder",
                "type": "folder",
            })
            self._expanded_attachment_folders.add(full_path)
            self._mark_active_sheet_dirty()
            self._refresh_attachments_list(curr.attachments)

    def _on_rename_attachment_folder(self, folder_data: dict) -> None:
        """첨부파일 폴더 이름 변경"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        curr = self._open_sheets[self._active_sheet_index]
        old_name = folder_data.get("name", "")
        old_fp = folder_data.get("folder_path", "")
        new_name, ok = QInputDialog.getText(self, "폴더 이름 변경", "새 폴더 이름:", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return

        clean_name = new_name.strip().replace("/", "_").replace("\\", "_")
        parts = old_fp.split("/")
        parts[-1] = clean_name
        new_fp = "/".join(parts)

        if self.repository and curr.db_id:
            try:
                self.repository.rename_work_attachment_folder(curr.db_id, old_fp, new_fp)
            except Exception as e:
                logger.warning(f"Failed to rename attachment folder {old_fp} -> {new_fp}: {e}")

        for a in curr.attachments:
            afp = a.get("folder_path", "")
            if afp == old_fp:
                a["folder_path"] = new_fp
            elif afp.startswith(old_fp + "/"):
                a["folder_path"] = new_fp + afp[len(old_fp):]
            if a.get("file_type") == "folder" and a.get("name") == old_name:
                a["name"] = clean_name

        if old_fp in self._expanded_attachment_folders:
            self._expanded_attachment_folders.discard(old_fp)
            self._expanded_attachment_folders.add(new_fp)

        self._mark_active_sheet_dirty()
        self._refresh_attachments_list(curr.attachments)

    def _on_attachment_order_changed(self) -> None:
        """첨부파일 트리에서 항목 드래그 이동/순서 변경 시 계층 구조 및 물리 파일 위치 갱신"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        curr = self._open_sheets[self._active_sheet_index]
        if not curr.db_id or not self.repository:
            return

        updated_attachments: list[dict] = []

        def traverse(item: QTreeWidgetItem, parent_fp: str):
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            item_type = data.get("type")
            att_id = data.get("id")

            if item_type == "folder":
                folder_name = data.get("name", "")
                cur_fp = f"{parent_fp}/{folder_name}".strip("/") if parent_fp else folder_name
                data["folder_path"] = parent_fp
                item.setData(0, Qt.ItemDataRole.UserRole, data)
                if att_id:
                    self.repository.connection.execute(
                        "UPDATE work_attachments SET folder_path = ? WHERE id = ?",
                        (parent_fp, att_id),
                    )
                updated_attachments.append({
                    "id": att_id,
                    "name": folder_name,
                    "path": "",
                    "size": "",
                    "folder_path": parent_fp,
                    "file_type": "folder",
                    "type": "folder",
                })
                for c_idx in range(item.childCount()):
                    traverse(item.child(c_idx), cur_fp)
            else:
                file_name = data.get("name", "")
                old_path = data.get("path", "")
                target_dir = self.repository.get_work_attachments_dir(curr.db_id, parent_fp)
                target_dir.mkdir(parents=True, exist_ok=True)
                new_path = str(target_dir / Path(old_path).name) if old_path else ""

                if old_path and os.path.exists(old_path) and os.path.abspath(old_path) != os.path.abspath(new_path):
                    try:
                        shutil.move(old_path, new_path)
                        data["path"] = new_path
                    except Exception as e:
                        logger.warning(f"Failed to move attachment file on tree move: {e}")

                data["folder_path"] = parent_fp
                item.setData(0, Qt.ItemDataRole.UserRole, data)
                if att_id:
                    self.repository.connection.execute(
                        "UPDATE work_attachments SET folder_path = ?, file_path = ? WHERE id = ?",
                        (parent_fp, data.get("path", ""), att_id),
                    )
                updated_attachments.append({
                    "id": att_id,
                    "name": file_name,
                    "path": data.get("path", ""),
                    "size": data.get("size", ""),
                    "folder_path": parent_fp,
                    "file_type": data.get("file_type", ""),
                    "type": "file",
                })

        for i in range(self.file_list.topLevelItemCount()):
            traverse(self.file_list.topLevelItem(i), "")

        self.repository.save()
        curr.attachments = updated_attachments
        self._mark_active_sheet_dirty()
        self._refresh_attachments_list(curr.attachments)

    def _add_attachment_path(self, file_path: str | Path, subfolder: str = "", refresh: bool = True) -> bool:
        """외부 파일을 프로그램 내 업무 전용 폴더에 복사하고 첨부파일 목록에 등록"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return False

        p = Path(file_path)
        if not p.exists():
            return False

        curr = self._open_sheets[self._active_sheet_index]
        if not curr.db_id and self.repository:
            self._save_sheet_sync(curr)

        if self.repository and curr.db_id:
            try:
                dest_path, size_str, dest_name = self.repository.copy_work_attachment_file(curr.db_id, p, subfolder=subfolder)
                att_id = self.repository.add_work_attachment(
                    work_id=curr.db_id,
                    file_name=dest_name,
                    file_path=dest_path,
                    file_size=size_str,
                    file_type=p.suffix.lower(),
                    folder_path=subfolder,
                )
                new_att = {
                    "id": att_id,
                    "name": dest_name,
                    "path": dest_path,
                    "size": size_str,
                    "folder_path": subfolder,
                    "file_type": p.suffix.lower(),
                    "type": "file",
                }
                curr.attachments.append(new_att)
                if subfolder:
                    self._expanded_attachment_folders.add(subfolder)
                self._mark_active_sheet_dirty()
                if refresh:
                    self._refresh_attachments_list(curr.attachments)
                return True
            except Exception as e:
                logger.exception("Failed to copy and attach file: %s", e)
                QMessageBox.warning(self, "오류", f"첨부파일 복사 중 오류가 발생했습니다: {e}")
                return False
        return False

    def _on_add_attachment(self, target_folder: str | None = None) -> None:
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        if target_folder is None:
            selected = self.file_list.selectedItems()
            item = selected[0] if selected else self.file_list.currentItem()
            if item:
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "folder":
                    target_folder = data.get("folder_path") or data.get("name") or ""
                else:
                    parent = item.parent()
                    if parent:
                        p_data = parent.data(0, Qt.UserRole) or {}
                        target_folder = p_data.get("folder_path") or p_data.get("name") or ""
                    else:
                        target_folder = data.get("folder_path", "")
            else:
                target_folder = ""

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "업무 관련 서식 및 첨부파일 선택",
            "",
            "모든 파일 (*.*);;한글 문서 (*.hwp *.hwpx);;엑셀 서식 (*.xlsx *.xls);;PDF (*.pdf);;문서 (*.docx *.txt)",
        )
        if not paths:
            return

        curr = self._open_sheets[self._active_sheet_index]
        count = len(paths)
        sub = target_folder or ""

        # 여러 파일 추가 시 진행바 표시하여 버벅임 방지
        progress = None
        if count > 1:
            progress = make_work_progress_dialog(
                self,
                "첨부파일 추가",
                f"첨부파일 복사 및 등록 중... (0/{count})",
                count,
            )

        try:
            for idx, p in enumerate(paths):
                if progress:
                    progress.setValue(idx)
                    progress.setLabelText(f"첨부파일 복사 및 등록 중... ({idx+1}/{count})\n{Path(p).name}")
                    QApplication.processEvents()
                self._add_attachment_path(p, subfolder=sub, refresh=False)
        finally:
            if progress:
                progress.setValue(count)
                progress.close()

        if sub:
            self._expanded_attachment_folders.add(sub)
        self._refresh_attachments_list(curr.attachments)

    def _on_attachments_dropped(self, paths: list[str], target_subfolder: str = "") -> None:
        """우측 첨부파일 패널로 파일/폴더 드래그 앤 드롭 시 프로그램 내부로 안전 복사 및 등록"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        curr = self._open_sheets[self._active_sheet_index]
        valid_paths = [Path(p) for p in paths if Path(p).exists()]
        if not valid_paths:
            return

        count = len(valid_paths)
        progress = None
        if count > 1:
            progress = make_work_progress_dialog(
                self,
                "첨부파일 등록",
                f"첨부파일 복사 및 등록 중... (0/{count})",
                count,
            )

        try:
            for idx, p in enumerate(valid_paths):
                if progress:
                    progress.setValue(idx)
                    progress.setLabelText(f"첨부파일 복사 및 등록 중... ({idx+1}/{count})\n{p.name}")
                    QApplication.processEvents()

                if p.is_dir():
                    self._import_folder_into_attachments(curr, p, parent_subfolder=target_subfolder)
                else:
                    self._add_attachment_path(p, subfolder=target_subfolder, refresh=False)
        finally:
            if progress:
                progress.setValue(count)
                progress.close()

        if target_subfolder:
            self._expanded_attachment_folders.add(target_subfolder)
        self._refresh_attachments_list(curr.attachments)

    def _import_folder_into_attachments(self, sheet: WorkSheetData, folder_dir: Path, parent_subfolder: str = "") -> None:
        """폴더를 재귀적으로 탐색하여 첨부파일 계층 구조로 일괄 복사 및 등록"""
        if not self.repository or not sheet.db_id:
            self._save_sheet_sync(sheet)

        all_files = [f for f in folder_dir.rglob("*") if f.is_file()]
        base_sub = f"{parent_subfolder}/{folder_dir.name}".strip("/") if parent_subfolder else folder_dir.name

        if not all_files:
            dir_path = self.repository.get_work_attachments_dir(sheet.db_id, base_sub)
            dir_path.mkdir(parents=True, exist_ok=True)
            att_id = self.repository.add_work_attachment(
                work_id=sheet.db_id,
                file_name=folder_dir.name,
                file_path="",
                file_size="",
                file_type="folder",
                folder_path=parent_subfolder,
            )
            sheet.attachments.append({
                "id": att_id,
                "name": folder_dir.name,
                "path": "",
                "size": "",
                "folder_path": parent_subfolder,
                "file_type": "folder",
                "type": "folder",
            })
            self._expanded_attachment_folders.add(base_sub)
            self._mark_active_sheet_dirty()
            self._refresh_attachments_list(sheet.attachments)
            return

        progress = QProgressDialog(f"폴더 '{folder_dir.name}' 복사 중...", "취소", 0, len(all_files), self)
        progress.setWindowTitle("첨부파일 폴더 가져오기")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.resize(440, 120)
        progress.show()
        QApplication.processEvents()

        for i, f in enumerate(all_files):
            if progress.wasCanceled():
                break
            progress.setLabelText(f"[{i + 1}/{len(all_files)}] '{f.name}' 복사 중...")
            progress.setValue(i)
            QApplication.processEvents()

            rel = f.relative_to(folder_dir).parent
            rel_str = str(rel) if str(rel) != "." else ""
            sub = f"{base_sub}/{rel_str}".strip("/").replace("\\", "/")
            try:
                dest_path, size_str, dest_name = self.repository.copy_work_attachment_file(sheet.db_id, f, subfolder=sub)
                att_id = self.repository.add_work_attachment(
                    work_id=sheet.db_id,
                    file_name=dest_name,
                    file_path=dest_path,
                    file_size=size_str,
                    file_type=f.suffix.lower(),
                    folder_path=sub,
                )
                sheet.attachments.append({
                    "id": att_id,
                    "name": dest_name,
                    "path": dest_path,
                    "size": size_str,
                    "folder_path": sub,
                    "file_type": f.suffix.lower(),
                    "type": "file",
                })
            except Exception as e:
                logger.warning(f"Failed to copy file {f}: {e}")

        progress.setValue(len(all_files))
        self._expanded_attachment_folders.add(base_sub)
        self._mark_active_sheet_dirty()
        self._refresh_attachments_list(sheet.attachments)

    def _open_selected_attachment(self) -> None:
        """선택된 첨부파일 열기 (복수 선택 시 모두 열기)"""
        items = self.file_list.selectedItems()
        if not items and self.file_list.currentItem():
            items = [self.file_list.currentItem()]
        if not items:
            return
        for item in items:
            data = item.data(0, Qt.UserRole) or {}
            if data.get("type") == "folder":
                self._open_selected_folder()
                continue
            path = data.get("path")
            if path and os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QMessageBox.warning(self, "파일 열기 실패", f"해당 첨부파일의 로컬 경로를 찾을 수 없습니다:\n{path}")

    def _open_selected_folder(self) -> None:
        """선택된 첨부파일 또는 폴더의 실제 디렉토리 열기 (탐색기)"""
        items = self.file_list.selectedItems()
        if not items and self.file_list.currentItem():
            items = [self.file_list.currentItem()]

        curr = self._open_sheets[self._active_sheet_index] if 0 <= self._active_sheet_index < len(self._open_sheets) else None

        if items:
            item = items[0]
            data = item.data(0, Qt.UserRole) or {}
            if data.get("type") == "folder":
                fp = data.get("folder_path", "")
                if curr and curr.db_id and self.repository:
                    folder_dir = self.repository.get_work_attachments_dir(curr.db_id, fp)
                    if folder_dir.exists():
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_dir)))
                        return
            else:
                path = data.get("path")
                if path and os.path.exists(path):
                    abs_path = os.path.abspath(path)
                    try:
                        subprocess.Popen(f'explorer /select,"{abs_path}"')
                        return
                    except Exception:
                        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
                        return

        if curr and self.repository and curr.db_id:
            folder = self.repository.get_work_attachments_dir(curr.db_id)
            if folder.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
                return
        QMessageBox.information(self, "알림", "열람할 첨부파일 또는 폴더를 찾을 수 없습니다.")

    def _delete_selected_attachment(self) -> None:
        """선택된 첨부파일 또는 폴더(단일 또는 Shift/Ctrl 다중 선택) 일괄 삭제"""
        items = self.file_list.selectedItems()
        if not items and self.file_list.currentItem():
            items = [self.file_list.currentItem()]
        if not items or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return

        curr = self._open_sheets[self._active_sheet_index]
        count = len(items)

        if count == 1:
            att = items[0].data(0, Qt.UserRole) or {}
            name = att.get("name", "항목")
            if att.get("type") == "folder":
                msg = f"폴더 '{name}' 및 포함된 모든 하위 파일/폴더를 삭제하시겠습니까?\n\n※ 디스크의 실제 파일들도 함께 영구 삭제됩니다."
            else:
                msg = f"'{name}' 첨부파일을 삭제하시겠습니까?\n\n※ 프로그램 내 보관된 실제 파일도 디스크에서 함께 영구 삭제됩니다."
        else:
            msg = f"선택한 {count}개 항목을 모두 삭제하시겠습니까?\n\n※ 디스크에 보관된 실제 파일/폴더들도 함께 영구 삭제됩니다."

        res = QMessageBox.question(self, "삭제 확인", msg, QMessageBox.Yes | QMessageBox.No)
        if res != QMessageBox.Yes:
            return

        progress = None
        if count > 1 or (count == 1 and (items[0].data(0, Qt.UserRole) or {}).get("type") == "folder"):
            progress = make_work_progress_dialog(
                self,
                "첨부파일 삭제",
                f"첨부파일 삭제 중... (0/{count})",
                count,
            )

        try:
            for idx, item in enumerate(items):
                att = item.data(0, Qt.UserRole) or {}
                name = att.get("name", "항목")
                if progress:
                    progress.setValue(idx)
                    progress.setLabelText(f"첨부파일 삭제 중... ({idx+1}/{count})\n{name}")
                    QApplication.processEvents()

                if att.get("type") == "folder":
                    fp = att.get("folder_path", "")
                    if self.repository and curr.db_id and fp:
                        try:
                            self.repository.delete_work_attachment_folder(curr.db_id, fp)
                        except Exception as e:
                            logger.warning(f"Failed to delete attachment folder {fp}: {e}")
                    curr.attachments = [
                        a for a in curr.attachments
                        if not (
                            (a.get("folder_path") == fp)
                            or (a.get("folder_path") or "").startswith(f"{fp}/")
                            or (a.get("file_type") == "folder" and a.get("name") == att.get("name"))
                        )
                    ]
                else:
                    att_id = att.get("id")
                    if self.repository and att_id:
                        try:
                            self.repository.delete_work_attachment(att_id, delete_file=True)
                        except Exception as e:
                            logger.warning(f"Failed to delete attachment {att}: {e}")
                    if att in curr.attachments:
                        curr.attachments.remove(att)
        finally:
            if progress:
                progress.setValue(count)
                progress.close()

        self._mark_active_sheet_dirty()
        self._refresh_attachments_list(curr.attachments)

    def _on_attachment_context_menu(self, pos: QPoint) -> None:
        """우측 첨부파일 트리 우클릭 컨텍스트 메뉴"""
        items = self.file_list.selectedItems()
        menu = QMenu(self)

        if items:
            count = len(items)
            if count == 1:
                item = items[0]
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "folder":
                    fp = data.get("folder_path", "")
                    act_open_folder = menu.addAction("📂 폴더 열기 (탐색기에서 보기)")
                    menu.addSeparator()
                    act_add_file = menu.addAction("➕ 이 폴더에 파일 추가...")
                    act_add_sub = menu.addAction("📁 하위 폴더 추가...")
                    act_rename = menu.addAction("✏️ 폴더 이름 변경...")
                    menu.addSeparator()
                    act_delete = menu.addAction("🗑️ 폴더 삭제")

                    action = menu.exec(self.file_list.mapToGlobal(pos))
                    if action == act_open_folder:
                        self._open_selected_folder()
                    elif action == act_add_file:
                        self._on_add_attachment(target_folder=fp)
                    elif action == act_add_sub:
                        self._on_add_attachment_folder(parent_folder=fp)
                    elif action == act_rename:
                        self._on_rename_attachment_folder(data)
                    elif action == act_delete:
                        self._delete_selected_attachment()
                    return
                else:
                    act_open_file = menu.addAction("📄 파일 열기")
                    act_open_folder = menu.addAction("📁 폴더 열기 (탐색기에서 보기)")
                    menu.addSeparator()
                    act_delete = menu.addAction("🗑️ 파일 삭제")
                    menu.addSeparator()
                    act_add_file = menu.addAction("➕ 파일 추가...")

                    action = menu.exec(self.file_list.mapToGlobal(pos))
                    if action == act_open_file:
                        self._open_selected_attachment()
                    elif action == act_open_folder:
                        self._open_selected_folder()
                    elif action == act_delete:
                        self._delete_selected_attachment()
                    elif action == act_add_file:
                        self._on_add_attachment()
                    return
            else:
                act_open_file = menu.addAction(f"📄 선택한 {count}개 항목 열기")
                act_open_folder = menu.addAction("📁 폴더 열기 (탐색기에서 보기)")
                menu.addSeparator()
                act_delete = menu.addAction(f"🗑️ 선택한 {count}개 항목 삭제")

                action = menu.exec(self.file_list.mapToGlobal(pos))
                if action == act_open_file:
                    self._open_selected_attachment()
                elif action == act_open_folder:
                    self._open_selected_folder()
                elif action == act_delete:
                    self._delete_selected_attachment()
                return
        else:
            act_add_folder = menu.addAction("📁 새 폴더 추가...")
            act_add_file = menu.addAction("➕ 파일 추가...")
            act_open_folder = None
            if 0 <= self._active_sheet_index < len(self._open_sheets):
                act_open_folder = menu.addAction("📂 첨부파일 보관 폴더 열기")

            action = menu.exec(self.file_list.mapToGlobal(pos))
            if action == act_add_folder:
                self._on_add_attachment_folder()
            elif action == act_add_file:
                self._on_add_attachment()
            elif action == act_open_folder:
                self._open_selected_folder()

    def _on_tree_files_dropped(self, paths: list[str], target_cat_name: str, target_cat_id: int | None) -> None:
        """좌측 업무 분류 트리로 문서 파일 드래그 앤 드롭 시 업무 등록 마법사 실행"""
        for p in paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                self.import_work_folder(path_obj)
            else:
                self.import_document_file(p, target_cat_name=target_cat_name, target_cat_id=target_cat_id)

    def import_document_file(self, file_path: str | Path, target_cat_name: str = "", target_cat_id: int | None = None) -> None:
        """외부 문서 파일을 파싱하여 새 업무 문서로 등록하고 편집기에 즉시 로드"""
        p = Path(file_path)
        if not p.exists():
            QMessageBox.warning(self, "파일 오류", f"선택한 파일을 찾을 수 없습니다:\n{file_path}")
            return

        active_cat_names = [c["name"] for c in self._category_rows if c.get("tab_id", 1) == self._active_category_tab_id]
        cat_name = target_cat_name or (active_cat_names[0] if active_cat_names else "일반 업무")
        dlg = WorkDocumentImportDialog(
            parent=self,
            file_path=p,
            categories=active_cat_names if active_cat_names else ["일반 업무"],
            default_category=cat_name,
            palette=self.palette,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        res = dlg.get_result()
        chosen_cat = res["category"]
        chosen_title = res["title"]
        copy_attachment = res["copy_attachment"]

        # 1. 파일 크기 계산 및 진행 상태 다이얼로그 초기화
        try:
            file_size = p.stat().st_size
            size_mb_str = f"{file_size / 1024 / 1024:.1f}MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.0f}KB"
        except Exception:
            file_size = 0
            size_mb_str = ""

        progress = QProgressDialog(self)
        progress.setWindowTitle("업무 등록 진행")
        progress.setLabelText(f"문서 파일을 검사하고 있습니다...\n({p.name} {size_mb_str})")
        progress.setRange(0, 100)
        progress.setValue(10)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.resize(380, 120)
        progress.show()
        QApplication.processEvents()

        # 2. DRM 상태 확인
        progress.setLabelText(f"보안(DRM) 암호화 여부 검사 중...\n({p.name})")
        progress.setValue(20)
        QApplication.processEvents()

        is_drm, drm_vendor = check_document_drm(p)
        content_text = ""
        hwpx_blob = None

        if is_drm:
            # 한글(HWP) 정품 프로그램 연동 백그라운드 복호화 추출 시도
            progress.setLabelText(f"사내 보안(DRM) 감지됨\n한글(HWP) 정품 프로그램을 통한 본문 복호화 시도 중...")
            progress.setValue(35)
            QApplication.processEvents()

            com_text, com_hwpx = try_extract_via_hwp_com(p)
            if com_text or com_hwpx:
                content_text, hwpx_blob = com_text, com_hwpx
            else:
                # COM 복호화 실패 시 안내 팝업 모달 표시
                progress.close()
                drm_dlg = WorkDrmWarningDialog(self, p, drm_vendor, palette=self.palette)
                if drm_dlg.exec() == QDialog.DialogCode.Accepted and drm_dlg.action == "attach_only":
                    content_text = (
                        f"[사내 보안(DRM) 암호화 문서]\n\n"
                        f"• 파일명: {p.name} ({size_mb_str})\n"
                        f"• 감지된 보안 솔루션: {drm_vendor}\n\n"
                        "【내용 등록 방법】\n"
                        "한글(HWP) 정품 프로그램에서 해당 문서를 연 뒤, "
                        "전체 선택(Ctrl+A) ➔ 복사(Ctrl+C)하여 여기에 붙여넣기(Ctrl+V)하세요.\n"
                        "원본 암호화 파일은 우측 첨부파일에 안전하게 보관되었습니다."
                    )
                    hwpx_blob = None
                    copy_attachment = True

                    progress = QProgressDialog(self)
                    progress.setWindowTitle("업무 등록 진행")
                    progress.setLabelText("원본 파일을 첨부파일로 보관하고 업무를 생성 중입니다...")
                    progress.setRange(0, 100)
                    progress.setValue(50)
                    progress.setCancelButton(None)
                    progress.setWindowModality(Qt.WindowModality.WindowModal)
                    progress.setMinimumDuration(0)
                    progress.resize(380, 120)
                    progress.show()
                    QApplication.processEvents()
                else:
                    return

        if not is_drm or (content_text and not hwpx_blob and not is_drm):
            progress.setLabelText(f"문서 본문 및 서식 데이터 추출 중...\n({size_mb_str} 대용량 문서 파싱)")
            progress.setValue(45)
            QApplication.processEvents()
            content_text, hwpx_blob = extract_document_content(p)

        # 3. 카테고리 준비
        chosen_cat_id = None
        for cat in self._category_rows:
            if cat["name"] == chosen_cat and cat.get("tab_id", 1) == self._active_category_tab_id:
                chosen_cat_id = cat["id"]
                break
        if not chosen_cat_id and self.repository:
            chosen_cat_id = self.repository.add_work_category(chosen_cat, sort_order=len(self._categories) + 1, tab_id=self._active_category_tab_id)
            self._load_categories_from_db()
            self._refresh_category_combos()

        # 4. DB 저장
        progress.setLabelText("업무 데이터베이스 저장 중...")
        progress.setValue(65)
        QApplication.processEvents()

        db_id = None
        if self.repository:
            db_id = self.repository.upsert_work_item(
                work_id=None,
                title=chosen_title,
                category_name=chosen_cat,
                category_id=chosen_cat_id,
                cycle="수시",
                content_text=content_text,
                hwpx_blob=hwpx_blob,
                sort_order=len(self._all_sheets) + 1,
            )

        # 5. 첨부파일 저장
        attachments = []
        if copy_attachment and self.repository and db_id:
            progress.setLabelText(f"첨부파일 보관소 복사 중... ({size_mb_str})")
            progress.setValue(80)
            QApplication.processEvents()
            try:
                dest_path, size_str, dest_name = self.repository.copy_work_attachment_file(db_id, p)
                att_id = self.repository.add_work_attachment(
                    work_id=db_id,
                    file_name=dest_name,
                    file_path=dest_path,
                    file_size=size_str,
                    file_type=p.suffix.lower(),
                )
                attachments.append({
                    "id": att_id,
                    "name": dest_name,
                    "path": dest_path,
                    "size": size_str,
                    "folder_path": "",
                    "file_type": p.suffix.lower(),
                    "type": "file",
                })
            except Exception as e:
                logger.exception("Failed to copy imported document as attachment: %s", e)

        # 6. 연계 첨부파일 폴더 처리
        att_dir = find_associated_attachment_dir(p)
        if att_dir and self.repository and db_id:
            progress.setLabelText("연계 첨부파일 폴더 동기화 중...")
            progress.setValue(88)
            QApplication.processEvents()
            try:
                for att_item in sorted(att_dir.rglob("*")):
                    if att_item.is_dir():
                        continue
                    rel_sub = str(att_item.relative_to(att_dir).parent).replace("\\", "/")
                    if rel_sub == ".":
                        rel_sub = ""
                    dest_p, s_str, d_name = self.repository.copy_work_attachment_file(db_id, att_item, subfolder=rel_sub)
                    a_id = self.repository.add_work_attachment(
                        work_id=db_id,
                        file_name=d_name,
                        file_path=dest_p,
                        file_size=s_str,
                        file_type=att_item.suffix.lower(),
                        folder_path=rel_sub,
                    )
                    attachments.append({
                        "id": a_id,
                        "name": d_name,
                        "path": dest_p,
                        "size": s_str,
                        "folder_path": rel_sub,
                        "file_type": att_item.suffix.lower(),
                        "type": "file",
                    })
            except Exception as e:
                logger.warning(f"Failed to import associated attachments from {att_dir}: {e}")

        new_sheet = WorkSheetData(
            db_id=db_id,
            sheet_id=f"sheet_{db_id or (len(self._all_sheets) + 1)}",
            title=chosen_title,
            category=chosen_cat,
            category_id=chosen_cat_id,
            cycle="수시",
            content_text=content_text,
            hwpx_blob=hwpx_blob,
            attachments=attachments,
        )
        self._all_sheets.append(new_sheet)
        self._refresh_category_tree()

        # 7. 에디터 로드
        progress.setLabelText("웹에디터로 문서를 로드하고 있습니다...")
        progress.setValue(95)
        QApplication.processEvents()

        self.open_sheet(new_sheet)

        progress.setValue(100)
        QApplication.processEvents()
        progress.close()

    def import_attachment_file(self, file_path: str | Path) -> None:
        """외부 파일 우클릭 시 특정 업무의 첨부파일로 바로 등록"""
        p = Path(file_path)
        if not p.exists():
            QMessageBox.warning(self, "파일 오류", f"선택한 파일을 찾을 수 없습니다:\n{file_path}")
            return

        if not self._all_sheets:
            res = QMessageBox.question(
                self,
                "업무 첨부파일 등록",
                f"등록된 업무 문서가 없습니다.\n'{p.name}' 첨부파일을 위한 새 업무 문서를 생성하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if res != QMessageBox.Yes:
                return
            self.import_document_file(p)
            return

        dlg = WorkAttachmentChooserDialog(
            parent=self,
            file_path=p,
            sheets=self._all_sheets,
            categories=self._categories,
            palette=self.palette,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        target_sheet = dlg.get_selected_sheet()
        if target_sheet is None:
            self.import_document_file(p)
            return

        self.open_sheet(target_sheet)
        self._add_attachment_path(p)
        QMessageBox.information(
            self,
            "첨부파일 등록 완료",
            f"'{p.name}' 파일이\n[{target_sheet.category}] '{target_sheet.title}' 업무의 첨부파일로 안전하게 보관되었습니다.",
        )

    def import_work_folder(self, folder_path: str | Path) -> None:
        """
        탐색기에서 폴더 우클릭 '업무로 등록' 또는 [불러오기]->폴더 선택 시 호출:
        - 사전 분석 모달(FolderWorkImportDialog)을 통해 하위 폴더 수, 파일 수, 파일 확장자 선택 필터 제공
        - 폴더 및 하위 모든 폴더를 업무분류 트리 구조로 생성
        - 사용자가 선택한 확장자의 파일들을 업무 문서로 일괄 등록
        - 진행률(QProgressDialog)을 표시하여 장시간 작업 상태 안내
        """
        root_dir = Path(folder_path)
        if not root_dir.exists() or not root_dir.is_dir():
            QMessageBox.warning(self, "폴더 오류", f"유효한 폴더를 찾을 수 없습니다:\n{folder_path}")
            return

        dlg = FolderWorkImportDialog(parent=self, folder_path=root_dir, palette=self.palette)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_exts = dlg.get_selected_extensions()
        root_name = dlg.get_category_name()
        copy_as_att = dlg.should_copy_attachments()

        all_files = [
            f for f in root_dir.rglob("*")
            if f.is_file()
            and not is_inside_attachment_folder(f)
            and (f.suffix.lower() in selected_exts or (not f.suffix and "(확장자 없음)" in selected_exts))
        ]
        if not all_files:
            QMessageBox.information(self, "알림", f"'{root_dir.name}' 폴더 내에 선택한 형식({', '.join(selected_exts)})의 파일이 없습니다.")
            return

        total_files = len(all_files)
        progress = QProgressDialog("업무 폴더를 가져오는 중입니다...", "취소", 0, total_files, self)
        progress.setWindowTitle("업무 폴더 일괄 등록")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.resize(460, 130)
        progress.show()
        QApplication.processEvents()

        root_cat_id = None
        if self.repository:
            root_cat_id = self.repository.add_work_category(root_name, sort_order=len(self._categories) + 1, parent_id=None, tab_id=self._active_category_tab_id)

        dir_to_cat: dict[Path, tuple[str, int | None]] = {root_dir: (root_name, root_cat_id)}
        imported_sheets: list[WorkSheetData] = []

        for i, file_p in enumerate(all_files):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"[{i + 1}/{total_files}] '{file_p.name}' 등록 중...")
            progress.setValue(i)
            QApplication.processEvents()

            parent_dir = file_p.parent
            if parent_dir not in dir_to_cat:
                rel_parts = parent_dir.relative_to(root_dir).parts
                curr_p = root_dir
                cur_cat_id = root_cat_id
                cur_cat_name = root_name
                for sub in rel_parts:
                    curr_p = curr_p / sub
                    if curr_p not in dir_to_cat:
                        new_id = None
                        if self.repository:
                            new_id = self.repository.add_work_category(sub, sort_order=100, parent_id=cur_cat_id, tab_id=self._active_category_tab_id)
                        dir_to_cat[curr_p] = (sub, new_id)
                        cur_cat_id = new_id
                        cur_cat_name = sub
                    else:
                        cur_cat_name, cur_cat_id = dir_to_cat[curr_p]

            cat_name, cat_id = dir_to_cat[parent_dir]
            doc_title = file_p.stem

            is_drm, drm_vendor = check_document_drm(file_p)
            content_text = ""
            hwpx_blob = None
            if is_drm:
                com_text, com_hwpx = try_extract_via_hwp_com(file_p)
                if com_text or com_hwpx:
                    content_text, hwpx_blob = com_text, com_hwpx
                else:
                    content_text = (
                        f"[사내 보안(DRM) 암호화 문서]\n\n"
                        f"• 파일명: {file_p.name}\n"
                        f"• 감지된 보안 솔루션: {drm_vendor}\n\n"
                        "한글(HWP) 정품 프로그램에서 문서를 열고 내용 복사(Ctrl+A, Ctrl+C) 후 여기에 붙여넣기(Ctrl+V)하세요.\n"
                        "원본 암호화 파일은 우측 첨부파일에 안전하게 보관되었습니다."
                    )
                    hwpx_blob = None
                    copy_as_att = True
            else:
                content_text, hwpx_blob = extract_document_content(file_p)

            db_id = None
            if self.repository:
                db_id = self.repository.upsert_work_item(
                    work_id=None,
                    title=doc_title,
                    category_name=cat_name,
                    category_id=cat_id,
                    cycle="수시",
                    content_text=content_text,
                    hwpx_blob=hwpx_blob,
                    sort_order=len(self._all_sheets) + len(imported_sheets) + 1,
                )

            attachments = []
            if copy_as_att and self.repository and db_id:
                try:
                    dest_path, size_str, dest_name = self.repository.copy_work_attachment_file(db_id, file_p)
                    att_id = self.repository.add_work_attachment(
                        work_id=db_id,
                        file_name=dest_name,
                        file_path=dest_path,
                        file_size=size_str,
                        file_type=file_p.suffix.lower(),
                    )
                    attachments.append({
                        "id": att_id,
                        "name": dest_name,
                        "path": dest_path,
                        "size": size_str,
                        "folder_path": "",
                        "file_type": file_p.suffix.lower(),
                        "type": "file",
                    })
                except Exception as e:
                    logger.warning(f"Failed to copy attachment for imported doc {file_p}: {e}")

            # 연계된 첨부파일 폴더({문서명}_첨부파일 등)가 있는 경우 하위 파일 자동 복사 및 복원
            att_dir = find_associated_attachment_dir(file_p)
            if att_dir and self.repository and db_id:
                try:
                    for att_item in sorted(att_dir.rglob("*")):
                        if att_item.is_dir():
                            continue
                        rel_sub = str(att_item.relative_to(att_dir).parent).replace("\\", "/")
                        if rel_sub == ".":
                            rel_sub = ""
                        dest_p, s_str, d_name = self.repository.copy_work_attachment_file(db_id, att_item, subfolder=rel_sub)
                        a_id = self.repository.add_work_attachment(
                            work_id=db_id,
                            file_name=d_name,
                            file_path=dest_p,
                            file_size=s_str,
                            file_type=att_item.suffix.lower(),
                            folder_path=rel_sub,
                        )
                        attachments.append({
                            "id": a_id,
                            "name": d_name,
                            "path": dest_p,
                            "size": s_str,
                            "folder_path": rel_sub,
                            "file_type": att_item.suffix.lower(),
                            "type": "file",
                        })
                except Exception as e:
                    logger.warning(f"Failed to import associated attachments from {att_dir}: {e}")

            sheet = WorkSheetData(
                db_id=db_id,
                sheet_id=f"sheet_{db_id or (len(self._all_sheets) + len(imported_sheets) + 1)}",
                title=doc_title,
                category=cat_name,
                category_id=cat_id,
                cycle="수시",
                content_text=content_text,
                hwpx_blob=hwpx_blob,
                attachments=attachments,
            )
            imported_sheets.append(sheet)

        progress.setValue(total_files)

        self._all_sheets.extend(imported_sheets)
        self._load_categories_from_db()
        self._refresh_category_combos()
        self._refresh_category_tree()

        if imported_sheets:
            self.open_sheet(imported_sheets[0])
            QMessageBox.information(
                self,
                "업무 등록 완료",
                f"폴더 '{root_name}' 내 총 {len(imported_sheets)}개의 파일이\n계층형 업무 분류 및 문서로 성공적으로 등록되었습니다.",
            )

    def _on_import_menu(self) -> None:
        """상단 [불러오기] 버튼 클릭 시 불러오기 마법사 실행"""
        dlg = WorkImportWizardDialog(
            parent=self,
            categories=self._categories,
            palette=self.palette,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        mode = dlg.get_mode()
        copy_att = dlg.get_copy_attachment()

        if mode == WorkImportWizardDialog.MODE_FOLDER:
            folder = dlg.get_folder()
            if folder:
                # FolderWorkImportDialog(사전 분석 모달)로 이어서 처리
                self.import_work_folder(folder)
        else:
            target_cat = dlg.get_target_category()
            target_cat_id = None
            for cat in self._category_rows:
                if cat["name"] == target_cat:
                    target_cat_id = cat["id"]
                    break
            for p in dlg.get_files():
                self.import_document_file(
                    p,
                    target_cat_name=target_cat,
                    target_cat_id=target_cat_id,
                )

    def _on_export_wizard(self) -> None:
        """상단 [내보내기] 버튼 클릭 시 내보내기 마법사 다이얼로그 실행"""
        self._save_current_sheet_data()
        dlg = WorkExportWizardDialog(
            parent=self,
            sheets=self._all_sheets,
            categories=self._category_rows,
            palette=self.palette,
        )
        dlg.exec()

    def _check_first_time_context_menu_prompt(self) -> None:
        """업무 관리 첫 진입 시 윈도우 탐색기 연동 여부 안내 모달 표시 (1회 안내)"""
        if not self.repository:
            return
        if self.repository.get_setting("work_context_menu_prompted", "0") != "0":
            return

        dlg = WorkContextMenuGuideDialog(parent=self, palette=self.palette)
        accepted = (dlg.exec() == QDialog.DialogCode.Accepted)
        if accepted:
            from taskcalendar.desktop_services import register_explorer_context_menus
            register_explorer_context_menus()
            self.repository.set_setting("work_enable_context_menu", "1")
            self.repository.set_setting("work_context_menu_prompted", "1")
            self.repository.save()
            QMessageBox.information(
                self,
                "연동 완료",
                "파일 탐색기 우클릭 메뉴가 등록되었습니다.\n문서 또는 폴더를 우클릭하여 업무로 바로 등록할 수 있습니다.\n\n(상단 [설정] 버튼에서 언제든 변경할 수 있습니다)",
            )
        else:
            if dlg.is_dont_ask_checked():
                self.repository.set_setting("work_context_menu_prompted", "1")
            self.repository.set_setting("work_enable_context_menu", "0")
            self.repository.save()

    def _open_work_settings(self) -> None:
        """업무 관리 및 탐색기 연동 환경설정 창 열기"""
        if self.main_window and hasattr(self.main_window, "_open_settings"):
            self.main_window._open_settings(initial_tab="work")
        elif self.repository:
            from taskcalendar.qt_dialogs import SettingsDialog
            from taskcalendar.desktop_services import default_shortcut, default_memo_shortcut
            dlg = SettingsDialog(
                self,
                current_theme="default",
                current_shortcut=self.repository.get_setting("toggle_shortcut", default_shortcut()),
                auto_start_enabled=False,
                sticker_animation_enabled=True,
                hide_completed_on_calendar=True,
                auto_backup_enabled=True,
                auto_backup_interval_days=1,
                auto_backup_keep_count=5,
                db_path=self.repository.db_path,
                current_memo_shortcut=self.repository.get_setting("memo_toggle_shortcut", default_memo_shortcut()),
                initial_tab="work",
                work_enable_context_menu=self.repository.get_setting("work_enable_context_menu", "0") == "1",
                work_copy_attachments_default=self.repository.get_setting("work_copy_attachments_default", "1") != "0",
                work_delete_attachments_default=self.repository.get_setting("work_delete_attachments_default", "1") != "0",
                work_default_cycle=self.repository.get_setting("work_default_cycle", "수시"),
            )
            if dlg.exec() and dlg.result:
                if "work_enable_context_menu" in dlg.result:
                    new_ctx = bool(dlg.result["work_enable_context_menu"])
                    self.repository.set_setting("work_enable_context_menu", "1" if new_ctx else "0")
                    from taskcalendar.desktop_services import register_explorer_context_menus, unregister_explorer_context_menus
                    if new_ctx:
                        register_explorer_context_menus()
                    else:
                        unregister_explorer_context_menus()
                if "work_copy_attachments_default" in dlg.result:
                    self.repository.set_setting("work_copy_attachments_default", "1" if dlg.result["work_copy_attachments_default"] else "0")
                if "work_delete_attachments_default" in dlg.result:
                    self.repository.set_setting("work_delete_attachments_default", "1" if dlg.result["work_delete_attachments_default"] else "0")
                if "work_default_cycle" in dlg.result:
                    self.repository.set_setting("work_default_cycle", str(dlg.result["work_default_cycle"]))
                self.repository.save()

    def import_attachment_folder(self, folder_path: str | Path) -> None:
        """
        탐색기에서 폴더 우클릭 '업무 첨부파일로 등록' 시 호출:
        - 등록할 대상 업무 시트를 선택하고
        - 폴더 전체(하위 폴더 포함)를 해당 업무의 첨부파일 계층 구조로 복사
        - 진행률(QProgressDialog) 표시
        """
        root_dir = Path(folder_path)
        if not root_dir.exists() or not root_dir.is_dir():
            QMessageBox.warning(self, "폴더 오류", f"유효한 폴더를 찾을 수 없습니다:\n{folder_path}")
            return

        if not self._all_sheets:
            res = QMessageBox.question(
                self,
                "업무 첨부파일 등록",
                f"등록된 업무 문서가 없습니다.\n'{root_dir.name}' 폴더를 업무(문서)로 등록하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if res == QMessageBox.Yes:
                self.import_work_folder(root_dir)
            return

        dlg = WorkAttachmentChooserDialog(
            parent=self,
            file_path=root_dir,
            sheets=self._all_sheets,
            categories=self._categories,
            palette=self.palette,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        target_sheet = dlg.get_selected_sheet()
        if target_sheet is None:
            return

        self.open_sheet(target_sheet)
        self._import_folder_into_attachments(target_sheet, root_dir, parent_subfolder="")
        QMessageBox.information(
            self,
            "첨부파일 등록 완료",
            f"폴더 '{root_dir.name}'의 파일들이\n[{target_sheet.category}] '{target_sheet.title}' 업무의 첨부파일로 성공적으로 등록되었습니다.",
        )

    # =========================================================================
    # 에디터 서식 및 공공 템플릿
    # =========================================================================

    def _on_title_text_changed(self, text: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        curr = self._open_sheets[self._active_sheet_index]
        curr.title = text.strip() or "새 업무"
        self._mark_active_sheet_dirty()
        self._refresh_category_tree()

    def _on_meta_cat_changed(self, cat: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].category = cat
        self._mark_active_sheet_dirty()
        self._refresh_category_tree()

    def _on_meta_cycle_changed(self, cycle: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].cycle = cycle
        self._mark_active_sheet_dirty()

    def _on_meta_assignee_changed(self, val: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].assignee = val
        self._mark_active_sheet_dirty()

    def _on_meta_deadline_changed(self, val: str) -> None:
        if self._is_loading_sheet or self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            return
        self._open_sheets[self._active_sheet_index].deadline = val
        self._mark_active_sheet_dirty()

    def _on_editor_text_changed(self) -> None:
        self._mark_active_sheet_dirty()

    def _open_search_bar(self) -> None:
        """단축키(Ctrl+F)로 검색창 포커스"""
        if hasattr(self, "_left_expanded") and not self._left_expanded:
            self._expand_left_panel()
        if hasattr(self, "search_input") and self.search_input:
            self.search_input.setFocus()
            self.search_input.selectAll()

    def _toggle_search_bar(self) -> None:
        self._open_search_bar()

    def _on_search_text_changed(self, query: str) -> None:
        """좌측 업무 분류 트리 실시간 검색 필터링 (열린 탭과 에디터는 변경하지 않음)"""
        query = query.strip().lower()
        if not query:
            def show_all(item: QTreeWidgetItem) -> None:
                item.setHidden(False)
                for k in range(item.childCount()):
                    show_all(item.child(k))

            for i in range(self.category_tree.topLevelItemCount()):
                show_all(self.category_tree.topLevelItem(i))
            return

        rag_matched_ids = set()
        if self.repository and hasattr(self.repository, "search_work_rag"):
            try:
                results = self.repository.search_work_rag(query)
                for r in results:
                    rag_matched_ids.add(r["id"])
            except Exception:
                pass

        # 좌측 카테고리 트리 필터링만 수행
        def matches(sheet: WorkSheetData) -> bool:
            # 대용량 본문 검색 시 메모리 할당/프리징 방지를 위해 상한선 설정
            content_sample = sheet.content_text[:50000].lower() if sheet.content_text else ""
            return bool(
                (sheet.db_id and sheet.db_id in rag_matched_ids)
                or query in sheet.title.lower()
                or query in sheet.category.lower()
                or query in sheet.assignee.lower()
                or query in content_sample
                or query in sheet.content_html[:50000].lower()
                or any(query in att.get("name", "").lower() for att in sheet.attachments)
            )

        def apply_filter(item: QTreeWidgetItem) -> bool:
            sheet = item.data(0, Qt.UserRole)
            if isinstance(sheet, WorkSheetData):
                matched = matches(sheet)
                item.setHidden(not matched)
                return matched
            visible = query in item.text(0).lower()
            for k in range(item.childCount()):
                if apply_filter(item.child(k)):
                    visible = True
            item.setHidden(not visible)
            if visible:
                item.setExpanded(True)
            return visible

        for i in range(self.category_tree.topLevelItemCount()):
            apply_filter(self.category_tree.topLevelItem(i))

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

    def _top_btn_style(self) -> str:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")

        return f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {panel_alt};
                border-color: {accent};
                color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {accent_soft};
            }}
            {self._tooltip_css()}
        """

    def _primary_btn_style(self) -> str:
        accent = self.palette.get("accent", "#2563EB")
        btn_text = self.palette.get("button_text", "#FFFFFF")
        hover_bg = _shade(accent, -0.1)
        pressed_bg = _shade(accent, -0.2)

        return f"""
            QPushButton {{
                background-color: {accent};
                color: {btn_text};
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {hover_bg};
                color: {btn_text};
            }}
            QPushButton:pressed {{
                background-color: {pressed_bg};
            }}
            {self._tooltip_css()}
        """

    def _sub_btn_style(self) -> str:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")

        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {accent_soft};
                border-color: {accent};
                color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {panel};
            }}
            {self._tooltip_css()}
        """

    def _toolbar_sub_btn_style(self) -> str:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")

        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {accent_soft};
                border-color: {accent};
                color: {accent};
            }}
            QPushButton:pressed {{
                background-color: {panel};
            }}
            {self._tooltip_css()}
        """

    def _danger_btn_style(self) -> str:
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        danger = self.palette.get("danger", "#EF4444")

        return f"""
            QPushButton {{
                background-color: {panel_alt};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: #FEF2F2;
                border-color: {danger};
                color: {danger};
            }}
            QPushButton:pressed {{
                background-color: #FEE2E2;
            }}
            {self._tooltip_css()}
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
            {self._tooltip_css()}
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

    def _apply_theme_styles(self) -> None:
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        text = self.palette.get("text", "#1F2328")
        line = self.palette.get("line", "#CBD5E0")
        accent = self.palette.get("accent", "#2563EB")
        accent_soft = self.palette.get("accent_soft", "#EFF6FF")

        is_dark = self.palette.get("bg", "").lower() in ("#0a0c10", "#171b22") or self.palette.get("text", "").lower() == "#f3f6fb"
        tip_bg = "#1E293B" if is_dark else "#FFFFFF"
        tip_fg = "#F8FAFC" if is_dark else "#0F172A"
        tip_border = "#475569" if is_dark else "#CBD5E1"

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
            QToolTip {{
                background-color: {tip_bg};
                color: {tip_fg};
                border: 1px solid {tip_border};
                border-radius: 6px;
                padding: 6px 10px;
                font-family: {font_family_css()};
                font-size: 12px;
                font-weight: 500;
            }}
        """)

        for btn in (
            getattr(self, "btn_import", None),
            getattr(self, "btn_export", None),
            getattr(self, "btn_new_work", None),
        ):
            if btn:
                btn.setStyleSheet(self._sub_btn_style())

        for btn in (
            getattr(self, "btn_template", None),
            getattr(self, "btn_img_template", None),
            getattr(self, "btn_fullscreen", None),
        ):
            if btn:
                btn.setStyleSheet(self._toolbar_sub_btn_style())

        if hasattr(self, "btn_save_work") and self.btn_save_work:
            self.btn_save_work.setStyleSheet(self._primary_btn_style())

        for btn in (
            getattr(self, "btn_search", None),
            getattr(self, "btn_add_cat", None),
            getattr(self, "btn_reg_cal", None),
            getattr(self, "btn_add_folder", None),
            getattr(self, "btn_add_file", None),
            getattr(self, "btn_open_file", None),
            getattr(self, "btn_add_sched", None),
            getattr(self, "btn_goto_sched", None),
            getattr(self, "btn_edit_sched", None),
        ):
            if btn:
                btn.setStyleSheet(self._sub_btn_style())

        for btn in (
            getattr(self, "btn_delete_file", None),
            getattr(self, "btn_delete_sched", None),
        ):
            if btn:
                btn.setStyleSheet(self._danger_btn_style())

        for btn in (
            getattr(self, "btn_collapse_left", None),
            getattr(self, "btn_collapse_right", None),
            getattr(self, "btn_expand_left", None),
            getattr(self, "btn_expand_right", None),
        ):
            if btn:
                btn.setStyleSheet(self._gutter_arrow_style())

        if hasattr(self, "search_input") and self.search_input:
            self.search_input.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {panel_alt};
                    border: 1px solid {line};
                    border-radius: 4px;
                    padding: 2px 8px;
                    font-size: 12px;
                    color: {text};
                }}
                QLineEdit:focus {{
                    background-color: {panel};
                    border: 1.5px solid {accent};
                }}
            """)

        if hasattr(self, "left_panel") and self.left_panel:
            self.left_panel.setObjectName("wmLeftPanel")
            self.left_panel.setStyleSheet("#wmLeftPanel { background: transparent; border: none; }")

        if hasattr(self, "left_card") and self.left_card:
            self.left_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {panel};
                    border: 1px solid {line};
                    border-radius: 8px;
                }}
            """)

        if hasattr(self, "category_tree") and self.category_tree:
            self.category_tree.setFont(make_ui_font_like(9))
            self.category_tree.setStyleSheet(f"""
                QTreeWidget {{
                    font-family: {font_family_css()};
                    font-size: 12px;
                    border: 1px solid {line};
                    border-radius: 6px 6px 0px 0px;
                    background-color: {panel_alt};
                    color: {text};
                    padding: 2px 2px;
                    outline: none;
                }}
                QTreeWidget::item {{
                    font-family: {font_family_css()};
                    height: 24px;
                    padding: 0px 4px;
                    margin: 1px 1px;
                    border: none;
                }}
                QTreeWidget::item:hover {{
                    background-color: {accent_soft};
                    color: {accent};
                    border-radius: 3px;
                }}
                QTreeWidget::item:selected {{
                    background-color: {accent};
                    color: #FFFFFF;
                    font-weight: bold;
                    border-radius: 3px;
                }}
                {self._tooltip_css()}
            """)

        if hasattr(self, "center_panel") and self.center_panel:
            self.center_panel.setStyleSheet(f"""
                QFrame {{
                    background-color: {panel};
                    border: 1px solid {line};
                    border-radius: 8px;
                }}
            """)

        if hasattr(self, "right_panel") and self.right_panel:
            self.right_panel.setObjectName("wmRightPanel")
            self.right_panel.setStyleSheet("#wmRightPanel { background: transparent; border: none; }")

        if hasattr(self, "attach_card") and self.attach_card:
            self.attach_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {panel};
                    border: 1px solid {line};
                    border-radius: 8px;
                }}
            """)

        if hasattr(self, "schedule_card") and self.schedule_card:
            self.schedule_card.setStyleSheet(f"""
                QFrame {{
                    background-color: {panel};
                    border: 1px solid {line};
                    border-radius: 8px;
                }}
            """)

        if hasattr(self, "file_list") and self.file_list:
            self.file_list.setStyleSheet(f"""
                QTreeWidget {{
                    border: 1px solid {line};
                    border-radius: 4px;
                    background-color: {panel_alt};
                    color: {text};
                    font-size: 11px;
                    padding: 2px;
                    outline: none;
                }}
                QTreeWidget::item {{
                    height: 22px;
                    padding: 0px 2px;
                    margin: 1px 0px;
                    border: none;
                    border-radius: 3px;
                }}
                QTreeWidget::item:hover:!selected {{
                    background-color: {accent_soft};
                    color: {accent};
                }}
                QTreeWidget::item:selected {{
                    background-color: {accent};
                    color: #FFFFFF;
                    font-weight: 600;
                }}
                {self._tooltip_css()}
            """)

        if hasattr(self, "work_schedule_list") and self.work_schedule_list:
            self.work_schedule_list.setStyleSheet(f"""
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
                    height: 24px;
                    padding: 2px 4px;
                    margin: 1px 0px;
                    border: none;
                    border-radius: 3px;
                }}
                QListWidget::item:hover:!selected {{
                    background-color: {accent_soft};
                    color: {accent};
                }}
                QListWidget::item:selected {{
                    background-color: {accent};
                    color: #FFFFFF;
                    font-weight: 600;
                }}
                {self._tooltip_css()}
            """)

        if hasattr(self, "work_doc_list") and self.work_doc_list and hasattr(self, "work_schedule_list") and self.work_schedule_list:
            self.work_doc_list.setStyleSheet(self.work_schedule_list.styleSheet())

        self._apply_tooltip_palette()

        if hasattr(self, "bottom_bar") and self.bottom_bar:
            self.bottom_bar.setStyleSheet("""
                QFrame {
                    background-color: transparent;
                    border: none;
                    padding: 0px 4px;
                }
            """)

        if hasattr(self, "sheet_tab_bar") and self.sheet_tab_bar:
            self.sheet_tab_bar.setStyleSheet(f"""
                QTabBar {{
                    background: transparent;
                    border: none;
                }}
                QTabBar::tab {{
                    background: #E2E8F0;
                    color: #64748B;
                    border: 1px solid {line};
                    border-bottom: 1px solid {line};
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                    padding: 0px 4px 0px 10px;
                    margin-top: 4px;
                    margin-right: 3px;
                    font-size: 12px;
                    min-width: 90px;
                    max-width: 360px;
                    height: 27px;
                }}
                QTabBar::tab:selected {{
                    background: {panel};
                    color: {accent};
                    font-weight: bold;
                    border: 1px solid {line};
                    border-bottom: 1px solid {panel};
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                    margin-top: 2px;
                    margin-bottom: -1px;
                    height: 29px;
                    padding: 0px 4px 0px 10px;
                }}
                QTabBar::tab:hover:!selected {{
                    background: #FFFFFF;
                    color: {text};
                    border: 1px solid {line};
                    border-bottom: 1px solid {line};
                }}
                QTabBar::close-button {{
                    image: url('{str(asset_path("tab_close_red.svg")).replace("\\", "/")}');
                    subcontrol-position: right;
                    subcontrol-origin: padding;
                    width: 16px;
                    height: 16px;
                    padding: 0px;
                    margin-right: 6px;
                    background: transparent;
                    border: none;
                    outline: none;
                }}
                QTabBar::close-button:hover {{
                    background: transparent;
                    border: none;
                    outline: none;
                }}
            """)

        if hasattr(self, "editor") and hasattr(self.editor, "apply_palette"):
            self.editor.apply_palette(self.palette)

    def apply_palette(self, palette: dict[str, str]) -> None:
        self.palette = palette
        self._apply_theme_styles()
        self._refresh_category_tree()
        self._render_category_tab_bar()
        self._refresh_sheet_tabs()
        self.update()
