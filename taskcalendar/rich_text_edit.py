import os
from PySide6.QtWidgets import QTextEdit, QMenu, QInputDialog
from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice, QUrl, QRect, QRectF, QPoint
from PySide6.QtGui import (
    QImage,
    QTextCursor,
    QTextImageFormat,
    QTextDocument,
    QPainter,
    QPen,
    QBrush,
    QAbstractTextDocumentLayout,
)

class RichTextEdit(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(True)
        self._selected_cursor = None
        self._drag_handle = None
        self._initial_mouse_pos = None
        self._initial_img_size = None
        self._orig_aspect_ratio = 1.0
        self.viewport().setMouseTracking(True)
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_cursor_changed)

    def _is_valid_image_cursor(self, cursor: QTextCursor | None) -> bool:
        if not cursor:
            return False
        doc = self.document()
        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        if start >= end or start < 0 or end > doc.characterCount():
            return False
        if doc.characterAt(start) != '\ufffc':
            return False
        fmt = cursor.charFormat()
        if not fmt.isImageFormat():
            return False
        return True

    def _on_text_changed(self) -> None:
        if self._selected_cursor and not self._is_valid_image_cursor(self._selected_cursor):
            self._selected_cursor = None
            self.viewport().update()

    def _on_cursor_changed(self) -> None:
        if self._selected_cursor:
            if not self._is_valid_image_cursor(self._selected_cursor):
                self._selected_cursor = None
                self.viewport().update()

    def _get_image_rect(self, cursor: QTextCursor | None) -> QRect:
        if not self._is_valid_image_cursor(cursor):
            return QRect()
        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        
        doc = self.document()
        c1 = QTextCursor(doc)
        c1.setPosition(start)
        r1 = self.cursorRect(c1)
        
        c2 = QTextCursor(doc)
        c2.setPosition(end)
        r2 = self.cursorRect(c2)
        
        w = r2.left() - r1.left()
        if w <= 0:
            img_fmt = cursor.charFormat().toImageFormat()
            w = int(img_fmt.width()) if img_fmt.width() > 0 else 60
        h = max(20, r1.height())
        return QRect(r1.left(), r1.top(), max(20, w), h)

    def _get_handles(self, rect: QRect) -> dict[str, QRect]:
        hs = 8  # handle size
        return {
            "tl": QRect(rect.left() - hs//2, rect.top() - hs//2, hs, hs),
            "tr": QRect(rect.right() - hs//2, rect.top() - hs//2, hs, hs),
            "bl": QRect(rect.left() - hs//2, rect.bottom() - hs//2, hs, hs),
            "br": QRect(rect.right() - hs//2, rect.bottom() - hs//2, hs, hs),
            "t": QRect(rect.center().x() - hs//2, rect.top() - hs//2, hs, hs),
            "b": QRect(rect.center().x() - hs//2, rect.bottom() - hs//2, hs, hs),
            "l": QRect(rect.left() - hs//2, rect.center().y() - hs//2, hs, hs),
            "r": QRect(rect.right() - hs//2, rect.center().y() - hs//2, hs, hs),
        }

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette = self.palette()
        ctx.cursorPosition = self.textCursor().position() if self.hasFocus() else -1
        
        offset_y = self.verticalScrollBar().value()
        offset_x = self.horizontalScrollBar().value()
        painter.translate(-offset_x, -offset_y)
        
        ctx.clip = QRectF(event.rect()).translated(offset_x, offset_y)
        
        cursor = self.textCursor()
        if cursor.hasSelection():
            sel = QAbstractTextDocumentLayout.Selection()
            sel.cursor = cursor
            sel.format.setBackground(self.palette().highlight())
            sel.format.setForeground(self.palette().highlightedText())
            ctx.selections = [sel]
            
        self.document().documentLayout().draw(painter, ctx)
        
        # Draw image selection border and handles
        if self._selected_cursor:
            if not self._is_valid_image_cursor(self._selected_cursor):
                self._selected_cursor = None
            else:
                rect = self._get_image_rect(self._selected_cursor)
                if rect.isValid() and rect.width() > 0 and rect.height() > 0:
                    pen = QPen(Qt.GlobalColor.blue, 1, Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(rect)
                    
                    painter.setPen(QPen(Qt.GlobalColor.blue, 1))
                    painter.setBrush(QBrush(Qt.GlobalColor.white))
                    handles = self._get_handles(rect)
                    for h in handles.values():
                        painter.drawRect(h)
                        
        painter.end()

    def keyPressEvent(self, event) -> None:
        if self._selected_cursor and self._is_valid_image_cursor(self._selected_cursor):
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._selected_cursor.removeSelectedText()
                self._selected_cursor = None
                self.viewport().update()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        
        # 1. If an image is selected, check if we clicked a resize handle
        if self._selected_cursor and self._is_valid_image_cursor(self._selected_cursor):
            rect = self._get_image_rect(self._selected_cursor)
            handles = self._get_handles(rect)
            for key, handle_rect in handles.items():
                if handle_rect.adjusted(-4, -4, 4, 4).contains(pos):
                    self._drag_handle = key
                    self._initial_mouse_pos = pos
                    self._initial_img_size = (rect.width(), rect.height())
                    
                    # Always use current rectangular proportions as base aspect ratio
                    img_fmt = self._selected_cursor.charFormat().toImageFormat()
                    cur_w = float(img_fmt.width()) if img_fmt.width() > 0 else float(rect.width())
                    cur_h = float(img_fmt.height()) if img_fmt.height() > 0 else float(rect.height())
                    if cur_h > 0 and cur_w > 0:
                        self._orig_aspect_ratio = cur_w / cur_h
                    else:
                        self._orig_aspect_ratio = max(0.05, float(rect.width()) / max(1.0, float(rect.height())))
                        
                    event.accept()
                    return
        
        # 2. Check if we clicked on any image in the document
        doc = self.document()
        found_image_cursor = None
        for p in range(doc.characterCount()):
            if doc.characterAt(p) == '\ufffc':
                c1 = QTextCursor(doc)
                c1.setPosition(p)
                r1 = self.cursorRect(c1)
                
                c2 = QTextCursor(doc)
                c2.setPosition(p + 1)
                r2 = self.cursorRect(c2)
                
                rect = QRect(r1.left(), r1.top(), max(20, r2.left() - r1.left()), max(20, r1.height()))
                if rect.contains(pos):
                    cursor = QTextCursor(doc)
                    cursor.setPosition(p)
                    cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
                    if self._is_valid_image_cursor(cursor):
                        found_image_cursor = cursor
                        break
                        
        if found_image_cursor:
            self._selected_cursor = found_image_cursor
            self.setTextCursor(found_image_cursor)
            self.viewport().update()
            
            if event.button() == Qt.MouseButton.RightButton:
                self._show_image_context_menu(event.globalPosition().toPoint(), found_image_cursor, found_image_cursor.charFormat().toImageFormat())
                event.accept()
                return
            event.accept()
            return
        else:
            if self._selected_cursor:
                self._selected_cursor = None
                self.viewport().update()

            if event.button() == Qt.MouseButton.RightButton:
                parent_dlg = self.parent()
                while parent_dlg and not hasattr(parent_dlg, "_show_memo_context_menu"):
                    parent_dlg = parent_dlg.parent()
                if parent_dlg and hasattr(parent_dlg, "_show_memo_context_menu"):
                    parent_dlg._show_memo_context_menu(event.globalPosition().toPoint())
                    event.accept()
                    return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        
        # Handle dragging
        if self._drag_handle and self._selected_cursor and self._is_valid_image_cursor(self._selected_cursor):
            dx = pos.x() - self._initial_mouse_pos.x()
            dy = pos.y() - self._initial_mouse_pos.y()
            init_w, init_h = self._initial_img_size
            
            # Check keyboard modifiers: Shift or Ctrl pressed preserves aspect ratio
            modifiers = event.modifiers()
            keep_aspect = bool(modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
            ratio = getattr(self, "_orig_aspect_ratio", init_w / max(1, init_h))
            if ratio <= 0.01:
                ratio = 1.0

            new_w = init_w
            new_h = init_h
            
            is_corner = self._drag_handle in ("tl", "tr", "bl", "br")
            
            if self._drag_handle in ("tr", "br", "r"):
                new_w = max(30, init_w + dx)
            elif self._drag_handle in ("tl", "bl", "l"):
                new_w = max(30, init_w - dx)
                
            if self._drag_handle in ("bl", "br", "b"):
                new_h = max(30, init_h + dy)
            elif self._drag_handle in ("tl", "tr", "t"):
                new_h = max(30, init_h - dy)
                
            if keep_aspect or is_corner:
                if self._drag_handle in ("l", "r"):
                    new_h = max(20, int(round(new_w / ratio)))
                elif self._drag_handle in ("t", "b"):
                    new_w = max(20, int(round(new_h * ratio)))
                else:
                    if abs(dx) >= abs(dy):
                        new_h = max(20, int(round(new_w / ratio)))
                    else:
                        new_w = max(20, int(round(new_h * ratio)))
                
            img_fmt = self._selected_cursor.charFormat().toImageFormat()
            new_fmt = QTextImageFormat()
            new_fmt.setName(img_fmt.name())
            new_fmt.setWidth(new_w)
            new_fmt.setHeight(new_h)
            
            self._selected_cursor.setCharFormat(new_fmt)
            self.viewport().update()
            event.accept()
            return

        # Change cursor shape on hover
        if self._selected_cursor and self._is_valid_image_cursor(self._selected_cursor):
            rect = self._get_image_rect(self._selected_cursor)
            handles = self._get_handles(rect)
            for key, handle_rect in handles.items():
                if handle_rect.adjusted(-4, -4, 4, 4).contains(pos):
                    if key in ("tl", "br"):
                        self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
                    elif key in ("tr", "bl"):
                        self.viewport().setCursor(Qt.CursorShape.SizeBDiagCursor)
                    elif key in ("t", "b"):
                        self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
                    elif key in ("l", "r"):
                        self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
                    event.accept()
                    return
            
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_handle = None
        self._initial_mouse_pos = None
        self._initial_img_size = None
        super().mouseReleaseEvent(event)

    def _show_image_context_menu(self, pos, cursor, image_format) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 0px;
                border-radius: 6px;
            }
            QMenu::item {
                padding: 6px 20px;
                font-size: 12px;
                color: #222222;
            }
            QMenu::item:selected {
                background-color: #f1f5f9;
                color: #0f172a;
            }
        """)
        
        delete_action = menu.addAction("이미지 삭제")
        menu.addSeparator()
        
        resize_150 = menu.addAction("크기: 150px (아주 작게)")
        resize_300 = menu.addAction("크기: 300px (작게)")
        resize_450 = menu.addAction("크기: 450px (중간)")
        resize_600 = menu.addAction("크기: 600px (기본/최대)")
        custom_resize = menu.addAction("크기 직접 지정...")
        
        action = menu.exec(pos)
        if action == delete_action:
            if cursor and self._is_valid_image_cursor(cursor):
                cursor.removeSelectedText()
            self._selected_cursor = None
            self.viewport().update()
        elif action == resize_150:
            self._resize_image(cursor, image_format, 150)
        elif action == resize_300:
            self._resize_image(cursor, image_format, 300)
        elif action == resize_450:
            self._resize_image(cursor, image_format, 450)
        elif action == resize_600:
            self._resize_image(cursor, image_format, 600)
        elif action == custom_resize:
            current_w = int(image_format.width() or 400)
            val, ok = QInputDialog.getInt(self, "크기 변경", "이미지 가로 크기(px):", value=current_w, min=50, max=3000, step=50)
            if ok:
                self._resize_image(cursor, image_format, val)

    def _resize_image(self, cursor, image_format, width) -> None:
        new_format = QTextImageFormat()
        new_format.setName(image_format.name())
        new_format.setWidth(width)
        
        resource = self.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(image_format.name()))
        if resource and not resource.isNull():
            orig_w = resource.width()
            orig_h = resource.height()
            if orig_w > 0:
                new_format.setHeight(int(orig_h * (width / orig_w)))
                
        cursor.setCharFormat(new_format)
        self.viewport().update()

    def insertFromMimeData(self, source) -> None:
        if source.hasUrls():
            image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
            image_files = []
            non_image_files = []
            for url in source.urls():
                local_path = url.toLocalFile()
                if local_path and os.path.exists(local_path):
                    ext = os.path.splitext(local_path)[1].lower()
                    if ext in image_exts:
                        image_files.append(local_path)
                    else:
                        non_image_files.append(local_path)
            
            for img_path in image_files:
                self.insert_image_file(img_path)
                
            if non_image_files:
                parent_dlg = self.window()
                if parent_dlg and hasattr(parent_dlg, "add_dropped_attachments"):
                    parent_dlg.add_dropped_attachments(non_image_files)
                    
            if image_files or non_image_files:
                parent_dlg = self.window()
                if parent_dlg and hasattr(parent_dlg, "_auto_save_to_db"):
                    parent_dlg._auto_save_to_db()
                return

        if source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self._insert_qimage(image)
                return
        super().insertFromMimeData(source)

    def insert_image_file(self, filepath: str) -> None:
        image = QImage(filepath)
        if not image.isNull():
            self._insert_qimage(image)

    def _insert_qimage(self, image: QImage) -> None:
        orig_w = image.width()
        orig_h = image.height()
        if orig_w > 2560:
            image = image.scaledToWidth(2560, Qt.TransformationMode.SmoothTransformation)
            orig_w = image.width()
            orig_h = image.height()
            
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        base64_data = byte_array.toBase64().data().decode("ascii")
        
        disp_w = min(340, orig_w)
        disp_h = int(round(disp_w * (orig_h / max(1, orig_w))))
        self.insertHtml(f'<img src="data:image/png;base64,{base64_data}" width="{disp_w}" height="{disp_h}" />')
