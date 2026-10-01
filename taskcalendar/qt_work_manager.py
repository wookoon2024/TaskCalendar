from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QMarginsF,
    QMimeData,
    QPoint,
    QPointF,
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
    QPdfWriter,
    QPen,
    QPolygonF,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
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
    """트리 항목(폴더 및 문서)의 텍스트 렌더링 델리게이트"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        view = opt.widget
        is_sel = bool(view and view.selectionModel() and view.selectionModel().isSelected(index))
        is_parent = not index.parent().isValid()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if is_sel:
            painter.setPen(QColor('#0284C7'))
        elif opt.state & QStyle.State_MouseOver:
            painter.setPen(QColor('#0F172A'))
        else:
            painter.setPen(QColor('#1E293B') if is_parent else QColor('#334155'))

        font = opt.font
        if is_parent or is_sel:
            font.setBold(True)
        painter.setFont(font)

        text_rect = opt.rect.adjusted(2, 0, -2, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, opt.text)
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

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
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

        super().dropEvent(event)
        self.blockSignals(True)
        # 방어적 무결성 검증:
        # 1. 문서(WorkSheetData)가 어떤 아이템을 자식으로 품고 있다면 -> 그 자식을 상위 폴더로 이동
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

        self.blockSignals(False)
        self.orderChanged.emit()


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


class CompactAttachmentTree(QTreeWidget):
    """
    우측 첨부파일 계층형 트리 위젯:
    - 좌측 여백 최소화(화살표 영역 제거로 폴더/파일이 좌측에 바짝 붙음)
    - 폴더(📁/📂) 및 파일 계층 구조 지원 (트리 열고닫기)
    - 탐색기 파일/폴더 드래그앤드롭 수신 (특정 폴더 위에 드롭 시 해당 폴더로 쏙)
    - Shift/Ctrl 다중 선택 및 Delete 키 단축키 지원
    """

    orderChanged = Signal()
    filesDropped = Signal(list, str)  # (paths, target_subfolder)
    deletePressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(14)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.setItemDelegate(CompactAttachmentItemDelegate(self))
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def drawBranches(self, painter, rect, index):
        """화살표 영역 제거 (폴더 아이콘 자체로 상태 표시 및 좌측 여백 극소화)"""
        return

    def drawRow(self, painter, option, index):
        is_sel = bool(self.selectionModel() and self.selectionModel().isSelected(index))
        is_hover = bool(option.state & QStyle.State_MouseOver)
        is_parent = not index.parent().isValid()

        if is_sel or is_hover:
            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                bg_color = QColor("#E0F2FE") if is_sel else QColor("#F1F5F9")
                painter.setBrush(bg_color)
                if is_parent:
                    row_rect = QRect(0, option.rect.y() + 1, option.rect.width(), option.rect.height() - 2)
                else:
                    vr = self.visualRect(index)
                    row_rect = vr.adjusted(0, 1, -2, -1)
                painter.drawRoundedRect(row_rect, 4, 4)
            finally:
                painter.restore()

        item_option = QStyleOptionViewItem(option)
        item_option.rect = self.visualRect(index)
        self.itemDelegate().paint(painter, item_option, index)

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
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        item = self.itemAt(pos)

        # 1. 윈도우 탐색기 등 외부 파일 드롭인 경우
        if event.mimeData().hasUrls() and not event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                target_subfolder = ""
                if item:
                    item_data = item.data(0, Qt.UserRole) or {}
                    if item_data.get("type") == "folder":
                        target_subfolder = item_data.get("folder_path", "")
                    else:
                        parent = item.parent()
                        if parent:
                            p_data = parent.data(0, Qt.UserRole) or {}
                            target_subfolder = p_data.get("folder_path", "")
                self.filesDropped.emit(paths, target_subfolder)
                event.acceptProposedAction()
                return

        # 2. 내부 파일/폴더 드래그 이동인 경우
        if event.mimeData().hasFormat("application/x-taskcalendar-att-internal"):
            super().dropEvent(event)
            self.blockSignals(True)
            # 방어 코드: 일반 파일은 자식 항목을 가질 수 없으므로 루트로 분리
            for i in range(self.topLevelItemCount() - 1, -1, -1):
                top = self.topLevelItem(i)
                data = top.data(0, Qt.UserRole) or {}
                if data.get("type") != "folder" and top.childCount() > 0:
                    while top.childCount() > 0:
                        ch = top.takeChild(0)
                        self.addTopLevelItem(ch)
            self.blockSignals(False)
            self.orderChanged.emit()
            event.acceptProposedAction()
            return

        super().dropEvent(event)


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
                    import xml.etree.ElementTree as ET
                    with zipfile.ZipFile(path, "r") as z:
                        for name in z.namelist():
                            if name.startswith("Contents/section") and name.endswith(".xml"):
                                root = ET.fromstring(z.read(name))
                                text_nodes = [elem.text for elem in root.iter() if elem.text and elem.tag.endswith("t")]
                                if text_nodes:
                                    extracted_text += "\n".join(text_nodes) + "\n"
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

    # 3. DOCX 워드 문서 (표준 zipfile 및 XML 파싱)
    if ext == ".docx":
        try:
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(path, "r") as z:
                xml_content = z.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                paras = []
                for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    p_text = "".join(node.text for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text)
                    if p_text:
                        paras.append(p_text)
                return "\n".join(paras), None
        except Exception as e:
            logger.warning(f"Failed to extract DOCX text from {path}: {e}")
            return "", None

    # 4. 일반 텍스트 및 코드/데이터 파일 (.txt, .md, .csv, .json, .log, .py, .xml, .html 등)
    encodings = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16")
    for enc in encodings:
        try:
            text = path.read_text(encoding=enc)
            return text, None
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f"Failed to read text file {path} with {enc}: {e}")
            break

    return "", None


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
        path_lbl = QLabel(full_path_str)
        path_lbl.setStyleSheet(f"font-size: 11px; color: {text_muted}; border: none; line-height: 1.4;")
        path_lbl.setWordWrap(True)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(path_lbl)

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
        if self.folder_path.exists() and self.folder_path.is_dir():
            for p in self.folder_path.rglob("*"):
                if p.is_dir():
                    self.subfolder_count += 1
                elif p.is_file():
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
        lbl_files = QLabel(f"총 파일: <b>{len(self.all_files)}</b>개")
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

        doc_exts = {".hwp", ".hwpx", ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".json"}
        sorted_exts = sorted(self.ext_counter.items(), key=lambda x: -x[1])
        for ext, count in sorted_exts:
            cb = QCheckBox(f"{ext} ({count}개 파일)")
            is_doc = ext in doc_exts
            cb.setChecked(is_doc or len(self.ext_counter) <= 3)
            self.ext_checkboxes[ext] = cb
            scroll_layout.addWidget(cb)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # 4. 업무분류 명칭 설정
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("등록될 최상위 업무 분류명:"))
        self.cat_input = QLineEdit(self.folder_path.name)
        self.cat_input.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 4px 8px;")
        cat_layout.addWidget(self.cat_input)
        layout.addLayout(cat_layout)

        # 5. 첨부파일 자동 보관 체크박스
        self.cb_attachments = QCheckBox("📂 원본 파일들을 각 업무 문서의 첨부파일로도 자동 보관")
        self.cb_attachments.setChecked(True)
        layout.addWidget(self.cb_attachments)

        # 6. 하단 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        btn_cancel = QPushButton("취소")
        btn_cancel.setFixedSize(74, 30)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_ok = QPushButton("업무 등록 시작")
        btn_ok.setFixedSize(110, 30)
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
        doc_exts = {".hwp", ".hwpx", ".pdf", ".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".json"}
        for ext, cb in self.ext_checkboxes.items():
            cb.setChecked(ext in doc_exts)

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
            from xml.sax.saxutils import escape as xml_escape

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

        # 포맷 그룹
        format_group_box = QFrame()
        format_group_box.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px; padding: 12px;")
        f_layout = QVBoxLayout(format_group_box)
        f_layout.setSpacing(8)
        lbl_f = QLabel("<b>내보내기 문서 형식 선택</b>")
        lbl_f.setStyleSheet(f"color: {accent}; font-size: 12px;")
        f_layout.addWidget(lbl_f)

        self.rb_hwpx = QRadioButton("📄 한글 문서 (.hwpx / .hwp) - 한글에서 바로 편집 가능한 정형 문서")
        self.rb_hwpx.setChecked(True)
        self.rb_pdf = QRadioButton("📑 PDF 문서 (.pdf) - 깔끔한 인쇄 및 배포용 A4 표준 문서")
        self.format_btn_group = QButtonGroup(self)
        self.format_btn_group.addButton(self.rb_hwpx)
        self.format_btn_group.addButton(self.rb_pdf)
        f_layout.addWidget(self.rb_hwpx)
        f_layout.addWidget(self.rb_pdf)
        s2_layout.addWidget(format_group_box)

        # 저장 폴더 그룹
        dir_group_box = QFrame()
        dir_group_box.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 6px; padding: 12px;")
        d_layout = QVBoxLayout(dir_group_box)
        d_layout.setSpacing(8)
        lbl_d = QLabel("<b>저장 대상 폴더 지정</b>")
        lbl_d.setStyleSheet(f"color: {accent}; font-size: 12px;")
        d_layout.addWidget(lbl_d)

        dir_h = QHBoxLayout()
        default_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
        self.dest_dir_input = QLineEdit(str(Path(default_dir) / "업무편람_내보내기"))
        self.dest_dir_input.setStyleSheet(f"background-color: {panel_alt}; border: 1px solid {line}; border-radius: 4px; padding: 4px 8px;")
        dir_h.addWidget(self.dest_dir_input, 1)

        btn_browse = QPushButton("찾아보기...")
        btn_browse.setFixedHeight(28)
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
        # 하단 탐색 버튼
        # -------------------------------------------------------------
        btn_nav_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setFixedSize(74, 30)
        self.btn_cancel.clicked.connect(self.reject)
        btn_nav_layout.addWidget(self.btn_cancel)

        btn_nav_layout.addStretch(1)

        self.btn_prev = QPushButton("◀ 이전")
        self.btn_prev.setFixedSize(80, 30)
        self.btn_prev.clicked.connect(self._go_prev_step)
        self.btn_prev.hide()
        btn_nav_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("다음 ▶")
        self.btn_next.setFixedSize(84, 30)
        self.btn_next.setStyleSheet(f"""
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
        self.btn_next.clicked.connect(self._go_next_step)
        btn_nav_layout.addWidget(self.btn_next)

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
    - 탭 제목이 좁아져서 조기 말줄임(...) 되지 않도록 텍스트 길이에 맞춰 자연스럽게 탭 너비 확장
    - 탭 닫기(X) 버튼이 우측 테두리에 너무 붙지 않도록 편안한 여백 유지
    """

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        text = self.tabText(index)
        fm = self.fontMetrics()
        text_w = fm.horizontalAdvance(text)
        # 좌측 여백(12px) + 텍스트 + 간격(12px) + 닫기버튼(18px) + 우측 여백(12px)
        needed_w = 12 + text_w + 12 + 18 + 12
        return QSize(max(needed_w, 115), 28)

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
            p = THEMES.get("default", {})
        self.palette = p

        self.setWindowTitle("업무 관리 및 인수인계 편람")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.resize(1180, 740)
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

        self._load_data_from_db()
        self._init_ui()
        # 처음에 문서를 아무것도 띄우지 않음 (빈 상태 초기화)
        self._clear_editor_view()
        self._restore_window_state()
        QTimer.singleShot(400, self._check_first_time_context_menu_prompt)

    @property
    def _sheets(self) -> list[WorkSheetData]:
        """외부 및 하위 호환용: 현재 열려있는 시트 목록 반환"""
        return self._open_sheets

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

        top_btn_style = f"""
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
                border-color: #94A3B8;
                color: {text};
            }}
            QPushButton:pressed {{
                background-color: #E2E8F0;
            }}
        """

        # 좌측: 불러오기 및 내보내기 도구 버튼
        btn_import = QPushButton("불러오기")
        btn_import.setFixedHeight(30)
        btn_import.setAutoDefault(False)
        btn_import.setDefault(False)
        btn_import.setToolTip("폴더 또는 파일을 업무로 불러오기")
        btn_import.setStyleSheet(top_btn_style)
        btn_import.clicked.connect(self._on_import_menu)
        top_layout.addWidget(btn_import)

        btn_export = QPushButton("내보내기")
        btn_export.setFixedHeight(30)
        btn_export.setAutoDefault(False)
        btn_export.setDefault(False)
        btn_export.setToolTip("업무 문서 및 첨부파일 내보내기 (HWPX / PDF)")
        btn_export.setStyleSheet(top_btn_style)
        btn_export.clicked.connect(self._on_export_wizard)
        top_layout.addWidget(btn_export)

        top_layout.addStretch(1)

        # 검색 입력창 (가로길이 2배 = 480px, 초록색 테두리 대신 단정한 중립 회색 테두리)
        self.search_input = QLineEdit()
        self.search_input.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_input.setPlaceholderText("업무, 본문, 첨부파일 검색...")
        self.search_input.setFixedHeight(30)
        self.search_input.setFixedWidth(480)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {panel_alt};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                color: {text};
            }}
            QLineEdit:focus {{
                background-color: {panel};
                border: 1.5px solid #64748B;
            }}
        """)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(lambda: self._on_search_text_changed(self.search_input.text()))
        top_layout.addWidget(self.search_input)

        # 검색 버튼
        btn_search = QPushButton("검색")
        btn_search.setFixedHeight(30)
        btn_search.setAutoDefault(False)
        btn_search.setDefault(False)
        btn_search.setStyleSheet(top_btn_style)
        btn_search.clicked.connect(lambda: self._on_search_text_changed(self.search_input.text()))
        top_layout.addWidget(btn_search)

        top_layout.addStretch(1)

        # 새 업무 추가 버튼 (캘린더 버튼 양식 통일, 이모티콘 제거)
        btn_new_work = QPushButton("새 업무")
        btn_new_work.setFixedHeight(30)
        btn_new_work.setAutoDefault(False)
        btn_new_work.setDefault(False)
        btn_new_work.setToolTip("새 업무 생성")
        btn_new_work.setStyleSheet(top_btn_style)
        btn_new_work.clicked.connect(self._on_add_new_sheet)
        top_layout.addWidget(btn_new_work)

        # 저장 버튼 (Ctrl+S) (캘린더 버튼 양식 통일, 이모티콘 제거)
        btn_save_work = QPushButton("저장")
        btn_save_work.setFixedHeight(30)
        btn_save_work.setAutoDefault(False)
        btn_save_work.setDefault(False)
        btn_save_work.setToolTip("현재 업무 문서 및 변경사항 저장 (Ctrl+S)")
        btn_save_work.setShortcut(QKeySequence("Ctrl+S"))
        btn_save_work.setStyleSheet(top_btn_style)
        btn_save_work.clicked.connect(self._on_save_button_clicked)
        top_layout.addWidget(btn_save_work)

        # 업무 설정 버튼 (탐색기 연동 및 편람 기본값)
        btn_work_settings = QPushButton("설정")
        btn_work_settings.setFixedHeight(30)
        btn_work_settings.setAutoDefault(False)
        btn_work_settings.setDefault(False)
        btn_work_settings.setToolTip("업무 관리 및 탐색기 연동 환경설정")
        btn_work_settings.setStyleSheet(top_btn_style)
        btn_work_settings.clicked.connect(self._open_work_settings)
        top_layout.addWidget(btn_work_settings)

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
        self.category_tree.setIndentation(14)
        self.category_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.category_tree.setRootIsDecorated(False)
        self.category_tree.setAnimated(False)
        self.category_tree.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {line};
                border-radius: 4px;
                background-color: {panel_alt};
                color: {text};
                font-size: 11px;
                padding: 2px 2px;
                outline: none;
            }}
            QTreeWidget::item {{
                height: 22px;
                padding: 0px 2px;
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
        """)
        self.category_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.category_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.category_tree.itemCollapsed.connect(self._on_tree_item_collapsed)
        self.category_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.category_tree.orderChanged.connect(self._on_tree_order_changed)
        self.category_tree.filesDropped.connect(self._on_tree_files_dropped)
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
        right_h_layout.setContentsMargins(2, 6, 6, 6)
        right_h_layout.setSpacing(2)

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

        btn_add_folder = QPushButton("+ 폴더")
        btn_add_folder.setFixedHeight(22)
        btn_add_folder.setStyleSheet(self._sub_btn_style())
        btn_add_folder.clicked.connect(self._on_add_attachment_folder)
        right_header.addWidget(btn_add_folder)

        btn_add_file = QPushButton("+ 파일")
        btn_add_file.setFixedHeight(22)
        btn_add_file.setStyleSheet(self._sub_btn_style())
        btn_add_file.clicked.connect(self._on_add_attachment)
        right_header.addWidget(btn_add_file)
        right_layout.addLayout(right_header)

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
                background-color: #F1F5F9;
                color: #0F172A;
            }}
            QTreeWidget::item:selected {{
                background-color: #E0F2FE;
                color: #0284C7;
                font-weight: 600;
                border: none;
                outline: none;
            }}
        """)
        self.file_list.itemDoubleClicked.connect(self._on_attachment_double_clicked)
        self.file_list.itemExpanded.connect(self._on_attachment_item_expanded)
        self.file_list.itemCollapsed.connect(self._on_attachment_item_collapsed)
        self.file_list.filesDropped.connect(self._on_attachments_dropped)
        self.file_list.orderChanged.connect(self._on_attachment_order_changed)
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

        self.sheet_tab_bar = WorkSheetTabBar()
        self.sheet_tab_bar.setDrawBase(False)
        self.sheet_tab_bar.setTabsClosable(True)
        self.sheet_tab_bar.setMovable(True)
        self.sheet_tab_bar.setExpanding(False)
        self.sheet_tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
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
                border-radius: 4px 4px 0px 0px;
                padding: 0px 28px 0px 14px;
                margin-top: 0px;
                margin-right: 3px;
                font-size: 12px;
                min-width: 100px;
                max-width: 320px;
                height: 28px;
            }}
            QTabBar::tab:selected {{
                background: {panel};
                color: {accent};
                font-weight: bold;
                border: 1px solid {line};
                border-bottom: 1px solid {panel};
                margin-top: 0px;
                height: 28px;
                padding: 0px 28px 0px 14px;
            }}
            QTabBar::tab:hover:!selected {{
                background: #FFFFFF;
                color: {text};
                border: 1px solid {line};
                border-bottom: none;
            }}
            QTabBar::close-button {{
                image: url('{str(asset_path("tab_close_red.svg")).replace("\\", "/")}');
                subcontrol-position: right;
                subcontrol-origin: padding;
                width: 18px;
                height: 18px;
                padding: 0px;
                margin-right: 8px;
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
        bottom_layout.addWidget(self.sheet_tab_bar, 0)
        bottom_layout.addStretch(1)

        main_layout.addWidget(bottom_bar)

        self._refresh_category_combos()
        self._refresh_category_tree()
        self._refresh_sheet_tabs()

        # 편집 변경 감지 시그널 연결 (저장되지 않은 변경사항 추적)
        self.editor.contentChanged.connect(self._mark_active_sheet_dirty)

        # 모든 버튼의 autoDefault 및 default 비활성화 (검색창 등에서 엔터 시 의도치 않은 버튼 작동 원천 차단)
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
            btn.setDefault(False)

    def keyPressEvent(self, event):
        """다이얼로그 기본 동작인 Enter 시 accept() 또는 기본 버튼 실행 차단"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if hasattr(self, "search_input") and self.search_input.hasFocus():
                self._on_search_text_changed(self.search_input.text())
                event.accept()
                return
            event.ignore()
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
        target_w = max(self._last_right_width, 220)
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
        if sheet in self._open_sheets and self._open_sheets.index(sheet) == self._active_sheet_index:
            self._save_current_sheet_data()
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

    def _on_sheet_tab_changed(self, index: int) -> None:
        """하단 시트 탭 클릭 시 해당 업무 로드"""
        if index < 0 or index >= len(self._open_sheets):
            return
        self._save_current_sheet_data()
        self._active_sheet_index = index
        self._load_sheet_to_editor(index)

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
                target_w = max(self._last_right_width, 220)
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
        self.sheet_tab_bar.blockSignals(True)
        self.sheet_tab_bar.setCurrentIndex(tab_idx)
        self.sheet_tab_bar.blockSignals(False)
        self._active_sheet_index = tab_idx
        self._load_sheet_to_editor(tab_idx)

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

        self._save_window_state()
        event.accept()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._save_window_state()

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
            self._last_left_width = max(last_left_width, 220)
        if last_right_width is not None and last_right_width > 60:
            self._last_right_width = max(last_right_width, 220)

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

        if splitter_sizes and len(splitter_sizes) == 3:
            if self._left_expanded and self._right_expanded:
                # 좌우 사이드바 가로 길이를 처음 또는 불균형 시 동일하게(최소 220px) 대칭 유지
                if abs(splitter_sizes[0] - splitter_sizes[2]) > 30 or splitter_sizes[2] < 180 or splitter_sizes[0] < 180:
                    sym_w = max(splitter_sizes[0], splitter_sizes[2], 220)
                    total_w = sum(splitter_sizes)
                    center_w = max(300, total_w - 2 * sym_w)
                    splitter_sizes = [sym_w, center_w, sym_w]
            self.splitter.setSizes(splitter_sizes)
        else:
            self.splitter.setSizes([220, 740, 220])

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
                QMessageBox.information(self, "저장 완료", f"'{current.title}' 업무 매뉴얼이 DB에 성공적으로 저장되었습니다.")

        self.editor.export_document_data(_after_export)

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

    def _refresh_category_combos(self) -> None:
        self.meta_cat_combo.blockSignals(True)
        self.meta_cat_combo.clear()
        self.meta_cat_combo.addItems(self._categories)
        self.meta_cat_combo.blockSignals(False)

    def _refresh_category_tree(self) -> None:
        """좌측 업무 분류 트리 재구성 (계층형 폴더 및 전체 문서 목록 표시, 펼침 상태 및 아이콘 보존)"""
        self.category_tree.blockSignals(True)
        self.category_tree.clear()

        cat_items_map: dict[int, QTreeWidgetItem] = {}
        pending_subcats: list[dict] = []

        for cat in self._category_rows:
            cat_id = cat["id"]
            parent_id = cat.get("parent_id")
            raw_name = cat["name"]
            is_expanded = (cat_id in self._expanded_category_ids) if self._has_saved_expanded_ids else True
            icon = "📂" if is_expanded else "📁"
            item = QTreeWidgetItem([f"{icon} {raw_name}"])
            item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsDropEnabled | Qt.ItemIsDragEnabled)
            item.setData(0, Qt.UserRole + 1, cat_id)
            item.setData(0, Qt.UserRole + 2, raw_name)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setForeground(0, QColor("#1E293B"))
            cat_items_map[cat_id] = item

            if not parent_id:
                self.category_tree.addTopLevelItem(item)
            else:
                pending_subcats.append(cat)

        for cat in pending_subcats:
            cat_id = cat["id"]
            parent_id = cat.get("parent_id")
            item = cat_items_map[cat_id]
            parent_item = cat_items_map.get(parent_id)
            if parent_item:
                parent_item.addChild(item)
            else:
                self.category_tree.addTopLevelItem(item)

        for sheet in self._all_sheets:
            parent_item = None
            if sheet.category_id and sheet.category_id in cat_items_map:
                parent_item = cat_items_map[sheet.category_id]
            else:
                for c in self._category_rows:
                    if c["name"] == sheet.category:
                        parent_item = cat_items_map[c["id"]]
                        sheet.category_id = c["id"]
                        break

            if not parent_item:
                if self.category_tree.topLevelItemCount() > 0:
                    parent_item = self.category_tree.topLevelItem(0)
                else:
                    parent_item = QTreeWidgetItem(["📁 기본 분류"])
                    parent_item.setData(0, Qt.UserRole + 2, "기본 분류")
                    self.category_tree.addTopLevelItem(parent_item)

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
        """좌측 트리 문서 클릭 시 하단 탭으로 열기 (폴더 클릭 시 선택 하이라이트 및 펼침/접힘 토글)"""
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
            menu.addSeparator()
            act_delete = menu.addAction("🗑️ 업무 삭제 (DB 영구 삭제)")
            menu.addSeparator()
            act_add_doc = menu.addAction("➕ 새 업무 추가")
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
            menu.addSeparator()
            act_delete = menu.addAction("🗑️ 폴더 삭제")
            menu.addSeparator()
            act_add_cat = menu.addAction("📁 새 루트 분류(폴더) 추가")
            action = menu.exec(self.category_tree.mapToGlobal(pos))
            if action == act_add_doc:
                self._on_add_sheet_in_category(cat_name, cat_id)
            elif action == act_add_sub:
                self._on_add_sub_category(cat_id, cat_name)
            elif action == act_rename:
                self._on_rename_category(cat_name)
            elif action == act_delete:
                self._on_delete_category(cat_name)
            elif action == act_add_cat:
                self._on_add_category()

    def _on_add_sub_category(self, parent_id: int | None, parent_name: str) -> None:
        """하위 폴더(분류) 생성"""
        sub_name, ok = QInputDialog.getText(self, "하위 분류(폴더) 추가", f"[{parent_name}] 하위 분류 명칭을 입력하세요:")
        if ok and sub_name.strip():
            c = sub_name.strip()
            if self.repository:
                self.repository.add_work_category(c, sort_order=100, parent_id=parent_id)
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

        if self.repository:
            self.repository.delete_work_category(cat_id)

        for s in to_del:
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
        """트리 항목 드래그 앤 드롭 정렬 변경 시 DB 및 메모리 동기화 (계층형 폴더 및 문서)"""
        new_all_sheets = []
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

        self._all_sheets = new_all_sheets
        self._load_categories_from_db()
        self._refresh_category_combos()

    def _on_add_category(self) -> None:
        cat_name, ok = QInputDialog.getText(self, "새 업무 분류 추가", "분류 명칭을 입력하세요 (예: 4. 대민 행정 서비스):")
        if ok and cat_name.strip():
            c = cat_name.strip()
            if self.repository:
                self.repository.add_work_category(c, len(self._categories) + 1)
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
            is_expanded = fp in self._expanded_attachment_folders
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

        # 3. 폴더 펼침 상태 적용
        for fp, f_item in folder_items.items():
            if fp in self._expanded_attachment_folders:
                f_item.setExpanded(True)

        self.file_list.blockSignals(False)
        self.right_title.setText(f"📎 첨부파일 ({file_count})")

    def _on_attachment_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            fp = data.get("folder_path", "")
            self._expanded_attachment_folders.add(fp)
            item.setText(0, f"📂 {data.get('name')}")

    def _on_attachment_item_collapsed(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "folder":
            fp = data.get("folder_path", "")
            self._expanded_attachment_folders.discard(fp)
            item.setText(0, f"📁 {data.get('name')}")

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
            item = self.file_list.currentItem()
            if item:
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "folder":
                    parent_folder = data.get("folder_path", "")
                else:
                    parent = item.parent()
                    if parent:
                        p_data = parent.data(0, Qt.UserRole) or {}
                        parent_folder = p_data.get("folder_path", "")
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

    def _add_attachment_path(self, file_path: str | Path, subfolder: str = "") -> None:
        """외부 파일을 프로그램 내 업무 전용 폴더에 복사하고 첨부파일 목록에 등록"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        p = Path(file_path)
        if not p.exists():
            return

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
                self._mark_active_sheet_dirty()
                self._refresh_attachments_list(curr.attachments)
            except Exception as e:
                logger.exception("Failed to copy and attach file: %s", e)
                QMessageBox.warning(self, "오류", f"첨부파일 복사 중 오류가 발생했습니다: {e}")

    def _on_add_attachment(self, target_folder: str | None = None) -> None:
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        if target_folder is None:
            item = self.file_list.currentItem()
            if item:
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "folder":
                    target_folder = data.get("folder_path", "")
                else:
                    parent = item.parent()
                    if parent:
                        p_data = parent.data(0, Qt.UserRole) or {}
                        target_folder = p_data.get("folder_path", "")
                    else:
                        target_folder = ""
            else:
                target_folder = ""

        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "업무 관련 서식 및 첨부파일 선택",
            "",
            "모든 파일 (*.*);;한글 문서 (*.hwp *.hwpx);;엑셀 서식 (*.xlsx *.xls);;PDF (*.pdf);;문서 (*.docx *.txt)",
        )
        if paths:
            for p in paths:
                self._add_attachment_path(p, subfolder=target_folder or "")

    def _on_attachments_dropped(self, paths: list[str], target_subfolder: str = "") -> None:
        """우측 첨부파일 패널로 파일/폴더 드래그 앤 드롭 시 프로그램 내부로 안전 복사 및 등록"""
        if self._active_sheet_index < 0 or self._active_sheet_index >= len(self._open_sheets):
            QMessageBox.information(self, "알림", "첨부파일을 추가할 업무를 먼저 선택하거나 열어주세요.")
            return

        curr = self._open_sheets[self._active_sheet_index]
        for path_str in paths:
            p = Path(path_str)
            if not p.exists():
                continue
            if p.is_dir():
                self._import_folder_into_attachments(curr, p, parent_subfolder=target_subfolder)
            else:
                self._add_attachment_path(p, subfolder=target_subfolder)

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

        for item in items:
            att = item.data(0, Qt.UserRole) or {}
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

        cat_name = target_cat_name or (self._categories[0] if self._categories else "일반 업무")
        dlg = WorkDocumentImportDialog(
            parent=self,
            file_path=p,
            categories=self._categories,
            default_category=cat_name,
            palette=self.palette,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        res = dlg.get_result()
        chosen_cat = res["category"]
        chosen_title = res["title"]
        copy_attachment = res["copy_attachment"]

        chosen_cat_id = None
        for cat in self._category_rows:
            if cat["name"] == chosen_cat:
                chosen_cat_id = cat["id"]
                break
        if not chosen_cat_id and self.repository:
            chosen_cat_id = self.repository.add_work_category(chosen_cat, sort_order=len(self._categories) + 1)
            self._load_categories_from_db()
            self._refresh_category_combos()

        content_text, hwpx_blob = extract_document_content(p)

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

        attachments = []
        if copy_attachment and self.repository and db_id:
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
        self.open_sheet(new_sheet)

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

        all_files = [f for f in root_dir.rglob("*") if f.is_file() and (f.suffix.lower() in selected_exts or (not f.suffix and "(확장자 없음)" in selected_exts))]
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
            root_cat_id = self.repository.add_work_category(root_name, sort_order=len(self._categories) + 1, parent_id=None)

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
                            new_id = self.repository.add_work_category(sub, sort_order=100, parent_id=cur_cat_id)
                        dir_to_cat[curr_p] = (sub, new_id)
                        cur_cat_id = new_id
                        cur_cat_name = sub
                    else:
                        cur_cat_name, cur_cat_id = dir_to_cat[curr_p]

            cat_name, cat_id = dir_to_cat[parent_dir]
            doc_title = file_p.stem

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
        """상단 [불러오기] 버튼 클릭 시 폴더 또는 파일 불러오기 팝업 메뉴 표시"""
        menu = QMenu(self)
        act_folder = menu.addAction("📁 폴더 불러오기 (하위 폴더/파일을 업무 분류 및 문서로 일괄 등록)")
        act_files = menu.addAction("📄 파일 불러오기 (문서들을 업무로 등록)")

        btn = self.sender()
        pos = btn.mapToGlobal(QPoint(0, btn.height())) if btn else QCursor.pos()
        action = menu.exec(pos)
        if action == act_folder:
            selected_dir = QFileDialog.getExistingDirectory(self, "가져올 업무 폴더 선택")
            if selected_dir:
                self.import_work_folder(selected_dir)
        elif action == act_files:
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "업무로 등록할 문서 파일 선택",
                "",
                "모든 지원 문서 (*.hwp *.hwpx *.pdf *.docx *.txt *.xlsx *.csv);;모든 파일 (*.*)",
            )
            if paths:
                for p in paths:
                    self.import_document_file(p)

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

        # 좌측 카테고리 트리 필터링 적용 (다단계 폴더 재귀)
        def matches(sheet: WorkSheetData) -> bool:
            return bool(
                (sheet.db_id and sheet.db_id in rag_matched_ids)
                or query in sheet.title.lower()
                or query in sheet.category.lower()
                or query in sheet.assignee.lower()
                or query in sheet.content_text.lower()
                or query in sheet.content_html.lower()
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
