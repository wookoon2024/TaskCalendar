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
    QColor,
    QTextCharFormat,
    QInputMethodEvent,
)

class RichTextEdit(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.is_todo_mode = False
        self._selected_cursor = None
        self._drag_handle = None
        self._initial_mouse_pos = None
        self._initial_img_size = None
        self._orig_aspect_ratio = 1.0
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.textChanged.connect(self._on_text_changed)
        self.cursorPositionChanged.connect(self._on_cursor_changed)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        """Fix Windows Korean IME preedit formatting where composing characters get an inverted black background."""
        if event.preeditString():
            new_attrs = []
            for attr in event.attributes():
                if attr.type == QInputMethodEvent.AttributeType.TextFormat:
                    fmt = QTextCharFormat(attr.value) if isinstance(attr.value, QTextCharFormat) else QTextCharFormat()
                    fmt.clearBackground()
                    fmt.clearForeground()
                    fmt.setFontUnderline(True)
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                    new_attrs.append(QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.TextFormat, attr.start, attr.length, fmt))
                else:
                    new_attrs.append(attr)
            new_event = QInputMethodEvent(event.preeditString(), new_attrs)
            new_event.setCommitString(event.commitString(), event.replacementStart(), event.replacementLength())
            super().inputMethodEvent(new_event)
            event.accept()
            return
        super().inputMethodEvent(event)

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
        
        img_fmt = cursor.charFormat().toImageFormat()
        w = int(img_fmt.width()) if img_fmt.width() > 0 else max(20, r2.left() - r1.left())
        h = int(img_fmt.height()) if img_fmt.height() > 0 else max(20, r1.height())
        return QRect(r1.left(), r1.top(), max(20, w), max(20, h))

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
        
        painter.save()
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
        painter.restore()
        
        # Draw image selection border and handles in viewport coordinates
        if self._selected_cursor:
            if not self._is_valid_image_cursor(self._selected_cursor):
                self._selected_cursor = None
            else:
                rect = self._get_image_rect(self._selected_cursor)
                if rect.isValid() and rect.width() > 0 and rect.height() > 0:
                    painter.setClipRect(self.viewport().rect())
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

        if getattr(self, "is_todo_mode", False):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                cursor = self.textCursor()
                blk = cursor.block()
                text = blk.text()

                # 빈 체크박스만 있는 상태에서 엔터 -> 체크박스 삭제 후 일반 줄로
                if text.strip() in ("☐", "☑"):
                    cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                    cursor.removeSelectedText()
                    event.accept()
                    return

                # 일반 내용이 있는 경우 엔터 -> 새 줄에 ☐ 자동 삽입
                super().keyPressEvent(event)
                new_cursor = self.textCursor()
                fmt = new_cursor.charFormat()
                fmt.setFontStrikeOut(False)
                new_cursor.setCharFormat(fmt)
                new_cursor.insertText("☐ ")
                event.accept()
                return

            elif event.key() == Qt.Key.Key_Backspace:
                cursor = self.textCursor()
                blk = cursor.block()
                text = blk.text()
                # 맨 앞 체크박스 바로 뒤에서 백스페이스 누르면 체크박스 전체 삭제
                if (text.startswith("☐ ") or text.startswith("☑ ")) and cursor.positionInBlock() <= 2:
                    cursor.setPosition(blk.position(), QTextCursor.MoveMode.KeepAnchor)
                    cursor.removeSelectedText()
                    event.accept()
                    return

        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()

        # Check if clicked on a To-Do checkbox (☐ or ☑)
        if event.button() == Qt.MouseButton.LeftButton:
            c = self.cursorForPosition(pos)
            blk = c.block()
            text = blk.text()
            if text.startswith("☐") or text.startswith("☑"):
                pos_in_blk = c.positionInBlock()
                # If clicking on the checkbox area at start of block
                if pos_in_blk <= 2:
                    is_checking = text.startswith("☐")
                    cur = QTextCursor(blk)
                    cur.setPosition(blk.position())
                    cur.setPosition(blk.position() + 1, QTextCursor.MoveMode.KeepAnchor)
                    cur.insertText("☑" if is_checking else "☐")

                    # Apply/remove strikeout for the remainder of the line while preserving color & font
                    cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setFontStrikeOut(is_checking)
                    cur.mergeCharFormat(fmt)
                    self.viewport().update()
                    event.accept()
                    return

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
                
                fmt = c1.charFormat()
                if fmt.isImageFormat():
                    img_fmt = fmt.toImageFormat()
                    w = int(img_fmt.width()) if img_fmt.width() > 0 else max(20, r2.left() - r1.left())
                    h = int(img_fmt.height()) if img_fmt.height() > 0 else max(20, r1.height())
                else:
                    w = max(20, r2.left() - r1.left())
                    h = max(20, r1.height())
                rect = QRect(r1.left(), r1.top(), max(20, w), max(20, h))
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

    def toggle_todo_style(self) -> None:
        self.is_todo_mode = not getattr(self, "is_todo_mode", False)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            doc = self.document()
            blk = doc.begin()
            has_content = False
            while blk.isValid():
                txt = blk.text()
                if txt.strip():
                    has_content = True
                    cur = QTextCursor(blk)
                    if self.is_todo_mode:
                        if not txt.startswith("☐") and not txt.startswith("☑"):
                            cur.setPosition(blk.position())
                            cur.insertText("☐ ")
                    else:
                        if txt.startswith("☐ ") or txt.startswith("☑ "):
                            cur.setPosition(blk.position() + 2, QTextCursor.MoveMode.KeepAnchor)
                            cur.removeSelectedText()
                            cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                            fmt = QTextCharFormat()
                            fmt.setFontStrikeOut(False)
                            cur.mergeCharFormat(fmt)
                        elif txt.startswith("☐") or txt.startswith("☑"):
                            cur.setPosition(blk.position() + 1, QTextCursor.MoveMode.KeepAnchor)
                            cur.removeSelectedText()
                blk = blk.next()

            if self.is_todo_mode and not has_content:
                cur = self.textCursor()
                cur.movePosition(QTextCursor.MoveOperation.Start)
                cur.insertText("☐ ")
                self.setTextCursor(cur)
        finally:
            cursor.endEditBlock()
        self.viewport().update()

    def contextMenuEvent(self, event) -> None:
        parent_dlg = self.window()
        if parent_dlg and str(getattr(parent_dlg, "entry_type", "")) == "EntryType.MEMO":
            menu = self.createStandardContextMenu()
            menu.setStyleSheet("""
                QMenu {
                    background-color: #ffffff;
                    border: 1px solid #d0d5dd;
                    padding: 4px 0px;
                    border-radius: 6px;
                }
                QMenu::item {
                    padding: 6px 24px 6px 20px;
                    font-size: 12px;
                    color: #222222;
                }
                QMenu::item:selected {
                    background-color: #f1f5f9;
                    color: #0f172a;
                }
            """)
            first_action = menu.actions()[0] if menu.actions() else None

            tb = getattr(parent_dlg, "memo_editor_toolbar", None)
            is_tb_vis = tb.isVisible() if tb is not None else False
            ed_act = menu.addAction("에디터 보기/닫기")
            ed_act.setCheckable(True)
            ed_act.setChecked(is_tb_vis)
            if hasattr(parent_dlg, "_toggle_editor_toolbar"):
                ed_act.triggered.connect(lambda: parent_dlg._toggle_editor_toolbar())

            is_todo = getattr(self, "is_todo_mode", False)
            todo_act = menu.addAction("To-Do 스타일로 변경" if not is_todo else "일반 텍스트로 변경")
            todo_act.setCheckable(True)
            todo_act.setChecked(is_todo)
            if hasattr(parent_dlg, "_toggle_todo_mode"):
                todo_act.triggered.connect(parent_dlg._toggle_todo_mode)
            else:
                todo_act.triggered.connect(self.toggle_todo_style)

            att_bar = getattr(parent_dlg, "attachment_bar", None)
            is_att_vis = att_bar.isVisible() if att_bar is not None else True
            att_act = menu.addAction("하단 파일첨부 열고/닫기")
            att_act.setCheckable(True)
            att_act.setChecked(is_att_vis)
            if hasattr(parent_dlg, "_toggle_attachment_bar"):
                att_act.triggered.connect(lambda: parent_dlg._toggle_attachment_bar())

            if first_action:
                menu.removeAction(ed_act)
                menu.removeAction(todo_act)
                menu.removeAction(att_act)
                menu.insertAction(first_action, ed_act)
                menu.insertAction(first_action, todo_act)
                menu.insertAction(first_action, att_act)
                menu.insertSeparator(first_action)

            menu.addSeparator()
            if hasattr(parent_dlg, "_create_new_group"):
                new_grp_act = menu.addAction("새 그룹 추가...")
                new_grp_act.triggered.connect(lambda: parent_dlg._create_new_group(assign_current=False))

            if hasattr(parent_dlg, "_populate_group_menu"):
                grp_sub = menu.addMenu("📁 그룹 지정 / 이동")
                grp_sub.setStyleSheet(menu.styleSheet())
                parent_dlg._populate_group_menu(grp_sub)

            if hasattr(parent_dlg, "_show_memo_context_menu"):
                memo_all_act = menu.addAction("플로팅 메모 전체 설정...")
                memo_all_act.triggered.connect(lambda: parent_dlg._show_memo_context_menu(event.globalPosition().toPoint()))

            menu.exec(event.globalPosition().toPoint())
            return
        super().contextMenuEvent(event)
